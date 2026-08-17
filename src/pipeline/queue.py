"""Cola local mínima de jobs del pipeline (ME40.2).

Separa la **creación** de un run (que ahora queda ``QUEUED`` en la API) de su
**ejecución** (que realiza un worker local de desarrollo). Persistencia
exclusivamente en el filesystem de ``runs_root``, sin Redis/PostgreSQL/SQLite:

- El estado de cada job es el propio ``run.json`` del run (``QUEUED`` →
  ``RUNNING`` → ``SUCCESS``/``QUALITY_FAILED``/``FAILED``), reutilizando
  :class:`pipeline.lifecycle.RunRecord` (sin duplicar modelo).
- Los **parámetros** necesarios para ejecutar (tema, offline, proyecto) viven en
  un ``job.json`` aparte dentro del directorio del run.
- La **adquisición** de un job es atómica mediante un archivo de bloqueo
  (``.worker.lock`` creado con ``O_CREAT | O_EXCL``): solo un worker adquiere
  cada job, incluso con dos workers locales ejecutándose a la vez.
- :func:`find_orphan_jobs` detecta (solo lectura, sin recuperación) los runs que
  un worker muerto dejó en ``RUNNING`` o los ``QUEUED`` con bloqueo huérfano;
  la recuperación automática queda para una ME futura.

Seguridad: todas las operaciones validan el ``run_id`` y resuelven dentro de
``runs_root`` (reutilizan :func:`pipeline.lifecycle.checked_run_dir`).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .context import RunContext
from .exceptions import PipelineValidationError
from .lifecycle import (
    RunRecord,
    RunStatus,
    checked_run_dir,
    list_runs,
    load_run_record,
    write_run_record,
)
from .models import _utc_now

logger = logging.getLogger(__name__)

#: Archivo de bloqueo de adquisición (uno por job, en ``run_dir``).
LOCK_FILE = ".worker.lock"

#: Archivo con los parámetros de ejecución del job (en ``run_dir``).
JOB_FILE = "job.json"


@dataclass(frozen=True)
class JobPayload:
    """Parámetros necesarios para ejecutar un job (tema, modo y proyecto).

    No duplica el estado del run (que vive en :class:`RunRecord`); solo
    transporta la información de la petición que el worker necesita para
    construir el :class:`pipeline.PipelineRunner`.
    """

    run_id: str
    topic: str
    offline: bool = False
    project_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "topic": self.topic,
            "offline": bool(self.offline),
            "project_id": self.project_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "JobPayload":
        return cls(
            run_id=str(data.get("run_id") or ""),
            topic=str(data.get("topic") or ""),
            offline=bool(data.get("offline")),
            project_id=(
                data.get("project_id")
                if isinstance(data.get("project_id"), str)
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Parámetros del job (job.json)
# ---------------------------------------------------------------------------


def write_job_payload(run_dir: Path, payload: JobPayload) -> Path:
    """Persiste los parámetros del job en ``run_dir/job.json`` (atómico)."""
    safe_dir = checked_run_dir(run_dir)
    target = safe_dir / JOB_FILE
    tmp = safe_dir / f".{JOB_FILE}.tmp"
    try:
        tmp.write_text(
            json.dumps(payload.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    logger.debug("job.json escrito en: %s", target)
    return target


def load_job_payload(run_dir: Path) -> Optional[JobPayload]:
    """Lee los parámetros del job de ``run_dir/job.json`` (defensivo)."""
    try:
        safe_dir = checked_run_dir(run_dir)
    except PipelineValidationError:
        return None
    path = safe_dir / JOB_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return JobPayload.from_dict(data)
    except (OSError, ValueError, TypeError):
        logger.warning("job.json ilegible o corrupto en: %s", safe_dir)
        return None


# ---------------------------------------------------------------------------
# Cola
# ---------------------------------------------------------------------------


def find_queued_jobs(runs_root: Path) -> list[RunRecord]:
    """Jobs ``QUEUED`` bajo ``runs_root``, en orden cronológico.

    Reutiliza :func:`pipeline.lifecycle.list_runs` (solo lectura, degrada a
    ``UNKNOWN`` los runs sin ``run.json`` legible).
    """
    root = Path(runs_root)
    if not root.is_dir():
        return []
    return [r for r in list_runs(root) if r.status is RunStatus.QUEUED]


def claim_job(runs_root: Path, run_id: str) -> Optional[RunContext]:
    """Adquiere un job ``QUEUED`` de forma atómica.

    Crea el bloqueo ``.worker.lock`` con ``O_CREAT | O_EXCL`` (si ya existe,
    otro worker lo está procesando → ``None``) y, solo si el run sigue en
    ``QUEUED``, pasa a ``RUNNING`` (``RunContext.prepare``) y devuelve el
    contexto adquirido.

    Safest against:

    - **dos workers**: el ``O_EXCL`` garantiza que solo uno adquiere el job.
    - **ejecución duplicada**: el bloqueo + la transición a ``RUNNING``
      impiden que otro worker vuelva a tomarlo.
    - **excepción**: si la adquisición falla tras crear el bloqueo, se libera.
    - **worker muerto**: el job queda en ``RUNNING`` (o ``QUEUED`` con bloqueo)
      y es detectable por :func:`find_orphan_jobs`; la recuperación automática
      queda fuera de esta ME.
    """
    root = Path(runs_root)
    run_dir = root / run_id
    try:
        checked_run_dir(run_dir, runs_root=root)
    except PipelineValidationError:
        return None
    lock = run_dir / LOCK_FILE
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    except OSError:
        logger.warning("No se pudo crear el bloqueo de %s.", run_id)
        return None
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
    finally:
        os.close(fd)

    record = load_run_record(run_dir)
    if record is None or record.status is not RunStatus.QUEUED:
        release_job(root, run_id)
        return None

    context = RunContext(run_id=run_id, runs_root=root)
    try:
        context.prepare()
    except Exception:  # noqa: BLE001 - liberar el bloqueo ante cualquier fallo
        release_job(root, run_id)
        raise
    logger.info("Job %s adquirido (RUNNING).", run_id)
    return context


def release_job(runs_root: Path, run_id: str) -> None:
    """Libera el bloqueo de adquisición de un job (idempotente)."""
    try:
        (Path(runs_root) / run_id / LOCK_FILE).unlink(missing_ok=True)
    except OSError:
        logger.warning("No se pudo liberar el bloqueo de %s.", run_id)


def find_orphan_jobs(runs_root: Path) -> list[RunRecord]:
    """Detecta jobs potencialmente huérfanos (solo lectura, sin recuperación).

    Criterios:

    - runs en ``RUNNING``: un worker murió en medio del pipeline.
    - runs en ``QUEUED`` con bloqueo presente: un worker murió entre la
      creación del bloqueo y la transición a ``RUNNING``.

    Estructura preparada para que una ME futura implemente la recuperación;
    aquí no se modifica ningún estado.
    """
    root = Path(runs_root)
    if not root.is_dir():
        return []
    orphans: list[RunRecord] = []
    for record in list_runs(root):
        lock_exists = (root / record.run_id / LOCK_FILE).exists()
        if record.status is RunStatus.RUNNING or (
            record.status is RunStatus.QUEUED and lock_exists
        ):
            orphans.append(record)
    return orphans


# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------

#: Firmatura de un ejecutor de job: recibe el contexto adquirido y devuelve el
#: resultado (o eleva). Por defecto ejecuta el pipeline real.
Executor = Callable[[RunContext], object]


def execute_job(context: RunContext) -> object:
    """Ejecuta el pipeline del job adquirido (estado ya ``RUNNING``).

    Lee los parámetros de ``job.json`` y construye el
    :class:`pipeline.PipelineRunner`. El propio runner persiste el estado final
    (``SUCCESS``/``QUALITY_FAILED``/``FAILED``) en ``run.json``.
    """
    payload = load_job_payload(context.run_dir)
    if payload is None:
        raise PipelineValidationError(
            [f"Falta {JOB_FILE} para el run {context.run_id}; no se puede ejecutar."]
        )
    from .runner import PipelineRunner

    runner = PipelineRunner(
        context,
        topic=payload.topic,
        offline=payload.offline,
        project_id=payload.project_id,
    )
    return runner.run()


def process_queued_job(
    runs_root: Path,
    run_id: str,
    *,
    executor: Optional[Executor] = None,
) -> bool:
    """Adquiere y procesa un job ``QUEUED`` concreto.

    Devuelve ``True`` si el job se procesó (fue adquirido por este llamador) o
    ``False`` si otro worker lo tiene o no estaba en cola. Un error inesperado
    del ejecutor se persiste como ``FAILED`` (el job no queda en ``RUNNING``
    para siempre) sin interrumpir el ciclo del worker.

    Args:
        runs_root: raíz de ejecuciones.
        run_id: identificador del job a procesar.
        executor: función que ejecuta el job (por defecto :func:`execute_job`).
    """
    context = claim_job(runs_root, run_id)
    if context is None:
        return False
    try:
        (executor or execute_job)(context)
    except Exception as exc:  # noqa: BLE001 - persistir FAILED y continuar
        logger.exception("El job %s falló de forma inesperada.", run_id)
        _mark_failed(context, f"Error no controlado del worker: {exc}")
    finally:
        release_job(runs_root, run_id)
    return True


def process_one(
    runs_root: Path,
    *,
    executor: Optional[Executor] = None,
) -> bool:
    """Procesa el primer job ``QUEUED`` disponible.

    Devuelve ``True`` si procesó un job (el llamador debe repetir el ciclo) o
    ``False`` si no había jobs en cola.
    """
    for record in find_queued_jobs(runs_root):
        if process_queued_job(runs_root, record.run_id, executor=executor):
            return True
    return False


def _mark_failed(context: RunContext, error: str) -> None:
    """Persiste ``FAILED`` en un run adquirido cuando el ejecutor eleva."""
    previous = load_run_record(context.run_dir)
    try:
        write_run_record(
            context.run_dir,
            RunRecord(
                run_id=context.run_id,
                status=RunStatus.FAILED,
                created_at=previous.created_at if previous else None,
                queued_at=previous.queued_at if previous else None,
                started_at=previous.started_at if previous else None,
                finished_at=_utc_now(),
                error=error,
            ),
        )
    except (OSError, PipelineValidationError):
        logger.warning("No se pudo persistir el estado FAILED de %s.", context.run_id)