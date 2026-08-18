"""Worker de AI Shorts Factory (ME40.2 + ME40.4).

Procesa de forma asíncrona los runs ``QUEUED``. Soporta dos modos:

**Local (ME40.2, por defecto):** el worker y la API comparten el filesystem de
``runs_root`` y el worker hace polling de la cola local directamente
(:func:`pipeline.queue.process_one`), adquiriendo jobs de forma atómica.

**Outbound (ME40.4):** si se define ``WORKER_API_URL``, el worker se convierte
en un **cliente** del protocolo outbound: **el worker siempre inicia la
conexión**, nunca se abre ningún puerto, y no hay túneles/port-forwarding.

1. Se autentica con ``Authorization: Bearer <WORKER_TOKEN>``.
2. Pide job a ``GET /api/v1/worker/jobs/next`` (el API reclama el job de forma
   atómica ``QUEUED → RUNNING``).
3. Si 204, espera y vuelve a intentar.
4. Si recibe job, ejecuta ``PipelineRunner`` localmente (synthetic, SD15,
   Kokoro, FFmpeg y Quality Gate) sobre el mismo ``runs_root``.
5. Informa el resultado a ``POST /api/v1/worker/jobs/{run_id}/complete``
   (idempotente; con reintentos si la red falla, sin destruir el job).

El token nunca se imprime ni se loguea; viene de ``WORKER_TOKEN``. Un fallo de
red no marca ``SUCCESS``, no borra ni duplica el job ni corrompe ``run.json``.

Uso:

    python scripts/run_worker.py                       # local, modo continuo
    python scripts/run_worker.py --once                # un solo job y termina
    python scripts/run_worker.py --poll-interval 2     # poll cada 2 segundos

Variables de entorno (outbound): ``WORKER_API_URL``, ``WORKER_TOKEN``,
``WORKER_ID`` (opcional; por defecto ``local-worker``). ``PIPELINE_RUNS_ROOT``
resuelve la raíz de ejecuciones compartida.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

# --- Ajuste del path para poder importar los paquetes de src/ ----------------
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import load_project_env  # noqa: E402
from pipeline import RUNS_ROOT, RunContext, find_orphan_jobs, load_run_record, process_one  # noqa: E402
from pipeline.queue import materialize_local_job  # noqa: E402
from application.artifacts import (  # noqa: E402
    DEFAULT_KIND,
    ArtifactNotFoundError,
    ArtifactRecord,
    ArtifactStore,
    LocalArtifactStore,
)

logger = logging.getLogger("run_worker")


class WorkerApiError(Exception):
    """Error de comunicación con la API del protocolo outbound."""


class WorkerApiClient:
    """Cliente HTTP mínimo del protocolo outbound (solo stdlib)."""

    def __init__(self, api_url: str, token: str, worker_id: str) -> None:
        self.base = api_url.rstrip("/")
        self.token = token
        self.worker_id = worker_id

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        timeout: float = 30.0,
    ) -> tuple[int, Optional[dict]]:
        req = urllib.request.Request(self.base + path, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        data = None
        if body is not None:
            req.add_header("Content-Type", "application/json")
            data = json.dumps(body).encode("utf-8")
        try:
            with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, (json.loads(raw) if raw else None)
        except Exception as exc:  # noqa: BLE001 - red caída/timeout/desconexión
            raise WorkerApiError(
                f"No se pudo contactar con la API ({method} {path}): {exc}"
            ) from exc

    def next_job(self) -> Optional[dict]:
        st, data = self._request(
            "GET", f"/api/v1/worker/jobs/next?worker_id={quote(self.worker_id)}"
        )
        if st == 204:
            return None
        if st == 200 and isinstance(data, dict):
            return data
        raise WorkerApiError(f"jobs/next devolvió el estado {st}.")

    def heartbeat(self, run_id: str) -> int:
        return self._request(
            "POST", f"/api/v1/worker/jobs/{run_id}/heartbeat", {}
        )[0]

    def progress(self, run_id: str, stage: str) -> int:
        return self._request(
            "POST", f"/api/v1/worker/jobs/{run_id}/progress", {"stage": stage}
        )[0]

    def complete(self, run_id: str, status: str, error: Optional[str] = None) -> int:
        return self._request(
            "POST",
            f"/api/v1/worker/jobs/{run_id}/complete",
            {"status": status, "error": error},
        )[0]


def run_pipeline_executor(context: RunContext) -> object:
    """Ejecuta el pipeline real sobre un contexto adquirido (vía job.json)."""
    from pipeline.queue import execute_job

    return execute_job(context)


def _find_video(run_dir: Path) -> Optional[Path]:
    """Localiza el primer ``.mp4`` renderizado dentro del run.

    El renderer escribe el video en ``run_dir/output/video/``. Devuelve el
    primero en orden alfabético, o ``None`` si no hay ninguno. Nunca sale de
    ``run_dir`` (el glob es relativo y acotado al subdirectorio de video).
    """
    video_dir = Path(run_dir) / "output" / "video"
    if not video_dir.is_dir():
        return None
    for candidate in sorted(video_dir.glob("*.mp4")):
        return candidate
    return None


def publish_video_artifact(
    runs_root: Path,
    run_id: str,
    video_path: Path,
    *,
    store: Optional[ArtifactStore] = None,
) -> ArtifactRecord:
    """Registra la metadata del video generado en ``artifacts.json`` (kind video).

    ME40.9B: tras un pipeline en SUCCESS el worker publica el artefacto de
    forma LOCAL (nunca se copia ni se mueve el MP4; la referencia queda
    relativa al run). Idempotente: si el run ya tiene un artefacto de video
    registrado, devuelve ese registro sin volver a publicar (no se crean
    duplicados en el mismo ciclo).

    Raises:
        ArtifactValidationError: si el run_id es inválido o la ruta escapa del run.
        ArtifactNotFoundError: si el archivo físico no existe.
    """
    store = store or LocalArtifactStore(runs_root)
    for existing in store.list(run_id):
        if existing.kind == DEFAULT_KIND:
            logger.info(
                "Artefacto de video ya publicado para %s; omitiendo.", run_id
            )
            return existing
    return store.publish(run_id, video_path, kind=DEFAULT_KIND)


def attempt_complete(
    client: WorkerApiClient,
    run_id: str,
    status: str,
    error: Optional[str],
    retries: int = 3,
) -> Optional[int]:
    """Informa el resultado al API con reintentos (complete es idempotente).

    Nunca borra ni duplica el job; si no se puede informar, el run conserva el
    estado terminal persistido por el pipeline y el bloqueo se limpia en la
    siguiente pasada del servidor.
    """
    for attempt in range(retries):
        try:
            st = client.complete(run_id, status, error)
            if st < 500:
                return st
        except WorkerApiError:
            pass
        time.sleep(min(2.0 * (attempt + 1), 8.0))
    logger.error(
        "No se pudo informar el resultado de %s tras %d intentos.", run_id, retries
    )
    return None


def run_remote_cycle(
    client: WorkerApiClient,
    runs_root: Path,
    *,
    executor: Optional[Callable[[RunContext], object]] = None,
    artifact_store: Optional[ArtifactStore] = None,
) -> bool:
    """Pide un job, lo ejecuta, publica el artefacto y lo completa.

    Devuelve ``True`` si procesó un job (el llamador debe repetir el ciclo) o
    ``False`` si no había jobs en cola.
    """
    job = client.next_job()
    if job is None:
        return False
    run_id = job.get("run_id")
    if not run_id:
        raise WorkerApiError("jobs/next devolvió un job sin run_id.")
    logger.info("Job %s obtenido; ejecutando pipeline local.", run_id)

    try:
        client.heartbeat(run_id)
    except Exception:  # noqa: BLE001 - aviso no crítico
        logger.debug("Heartbeat no enviado para %s.", run_id)

    # ME40.8: el worker y el control plane no comparten filesystem. A partir del
    # payload HTTP (run_id/topic/offline/project_id) se materializa LOCALMENTE
    # el contexto y el job.json que execute_job necesita; no se copia nada desde
    # la API. Un payload malformado (topic vacío, run_id inválido) eleva
    # PipelineValidationError y se reporta FAILED de forma controlada.
    status = "FAILED"
    error: Optional[str] = None
    try:
        topic = job.get("topic")
        project_id = (
            job.get("project_id")
            if isinstance(job.get("project_id"), str)
            else None
        )
        context = materialize_local_job(
            runs_root,
            run_id,
            topic=topic if isinstance(topic, str) else "",
            offline=bool(job.get("offline")),
            project_id=project_id,
        )
        (executor or run_pipeline_executor)(context)
        record = load_run_record(context.run_dir)
        if record is not None and record.status.value in (
            "SUCCESS",
            "QUALITY_FAILED",
            "FAILED",
        ):
            status = record.status.value
            error = record.error
            # ME40.9B: el video solo se publica si el pipeline terminó en
            # SUCCESS. La metadata queda en artifacts.json dentro del run local
            # (no se copia ni mueve el MP4). SUCCESS solo se informa después de
            # que la publicación tuvo éxito: si no hay video publicable o la
            # publicación falla, el run se reporta FAILED de forma controlada.
            if status == "SUCCESS":
                try:
                    video_path = _find_video(context.run_dir)
                    if video_path is None:
                        raise ArtifactNotFoundError(
                            "No se encontró ningún .mp4 en "
                            f"{context.run_dir / 'output' / 'video'}."
                        )
                    publish_video_artifact(
                        runs_root,
                        run_id,
                        video_path,
                        store=artifact_store,
                    )
                except Exception as exc:  # noqa: BLE001 - FAILED controlado
                    logger.exception(
                        "No se pudo publicar el artefacto de %s.", run_id
                    )
                    status = "FAILED"
                    error = (
                        "Pipeline SUCCESS pero falló la publicación del "
                        f"artefacto: {exc}"
                    )
        else:
            # Nunca se inventa SUCCESS: sin estado terminal, se reporta FAILED.
            error = "El pipeline no persistió un estado terminal."
    except Exception as exc:  # noqa: BLE001 - usar la transición FAILED existente
        logger.exception("El pipeline falló para %s.", run_id)
        status = "FAILED"
        error = str(exc)

    attempt_complete(client, run_id, status, error)
    return True


def run_local(runs_root: Path, args: argparse.Namespace) -> int:
    """Modo local (ME40.2): polling directo de la cola en el filesystem."""
    if args.once:
        run_cycle(runs_root)
        return 0
    while True:
        try:
            run_cycle(runs_root)
        except Exception:  # noqa: BLE001 - el ciclo nunca debe morir
            logger.exception("Error no controlado en el ciclo del worker.")
        time.sleep(max(0.0, args.interval))


def run_remote(api_url: str, runs_root: Path, args: argparse.Namespace) -> int:
    """Modo outbound (ME40.4): el worker es cliente del protocolo HTTP."""
    token = os.environ.get("WORKER_TOKEN", "")
    if not token:
        logger.error(
            "WORKER_TOKEN no está definido; el worker no puede autenticarse."
        )
        return 1
    worker_id = os.environ.get("WORKER_ID", "").strip() or "local-worker"
    client = WorkerApiClient(api_url, token, worker_id)
    logger.info(
        "Worker outbound iniciado (api=%s worker_id=%s runs_root=%s).",
        api_url,
        worker_id,
        runs_root,
    )
    if args.once:
        try:
            run_remote_cycle(client, runs_root)
        except Exception:  # noqa: BLE001 - la API caída no destruye el job
            logger.exception("Error no controlado en el ciclo outbound.")
        return 0
    while True:
        try:
            run_remote_cycle(client, runs_root)
        except Exception:  # noqa: BLE001 - el ciclo nunca debe morir
            logger.exception("Error no controlado en el ciclo outbound.")
        time.sleep(max(0.0, args.interval))


def run_cycle(runs_root: Path) -> None:
    """Procesa un job de la cola local (si hay) y registra huérfanos."""
    orphans = find_orphan_jobs(runs_root)
    if orphans:
        logger.warning(
            "Se detectaron %d run(s) potencialmente huérfano(s): %s",
            len(orphans),
            ", ".join(o.run_id for o in orphans),
        )
    if process_one(runs_root):
        logger.info("Job procesado.")
    else:
        logger.debug("No hay jobs QUEUED.")


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description=(
            "Worker local que procesa los runs QUEUED de la API de forma "
            "asíncrona (pipeline completo: contenido, imagen, audio, manifest, "
            "render y quality)."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Procesa un solo job (si hay alguno) y termina.",
    )
    parser.add_argument(
        "--interval",
        "--poll-interval",
        type=float,
        default=2.0,
        help="Segundos de espera entre comprobaciones de la cola (por defecto 2.0).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Nivel de log (por defecto INFO).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del script. Devuelve 0 en éxito, 1 en error."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_project_env()

    runs_root = RUNS_ROOT
    logger.info("Worker iniciado (runs_root=%s).", runs_root)

    api_url = os.environ.get("WORKER_API_URL", "").strip()
    if api_url:
        return run_remote(api_url, runs_root, args)
    return run_local(runs_root, args)


if __name__ == "__main__":
    sys.exit(main())