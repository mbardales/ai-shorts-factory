"""Protocolo outbound del worker de GPU (ME40.4, pre-Northflank).

Implementa la interfaz lógica **el worker siempre inicia la conexión**:

    worker → poll → claim → execute → heartbeat/progress → complete

:class:`WorkerService` encapsula el protocolo del lado del servidor (API):

- **Autenticación**: el worker presenta ``Authorization: Bearer <WORKER_TOKEN>``
  (token simple desde ``os.environ``; nunca se imprime, no se loguea, no se
  almacena en código ni en ``run.json``). Token ausente/incorrecto → 401.
- **Polling + claim atómico**: ``GET /worker/jobs/next`` adquiere el primer
  job ``QUEUED`` reutilizando :func:`pipeline.queue.claim_job` (``O_EXCL``);
  dos workers simultáneos nunca obtienen el mismo job.
- **Complete**: ``POST /worker/jobs/{run_id}/complete`` valida la transición
  desde ``RUNNING`` (o acepta idempotentemente el mismo estado terminal).
- **Progress/heartbeat**: avisos de vida sin persistir progreso redundante
  (el stage ya se deriva de los artefactos del run).

La cola sigue siendo la local de ME40.2 (filesystem en ``runs_root``) y el
almacenamiento de runs usa :class:`application.storage.RunStorage` (ME40.3).
ME40.4 es estrictamente local: no abre puertos, no crea túneles, no hay
servicios externos.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from pathlib import Path
from typing import Optional

from pipeline import PipelineValidationError, load_run_record
from pipeline.lifecycle import RunRecord, RunStatus, write_run_record
from pipeline.models import _utc_now
from pipeline.queue import claim_job, find_queued_jobs, load_job_payload, release_job

from .exceptions import (
    ApplicationConflictError,
    ApplicationRunNotFoundError,
    ApplicationValidationError,
    WorkerUnauthorizedError,
)
from .storage import LocalRunStorage, RunStorage, StorageError

logger = logging.getLogger(__name__)

#: Identidad lógica del worker: ``^[A-Za-z0-9][A-Za-z0-9_-]*$`` (sin IP/hostname).
WORKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

#: Variable de entorno con el secreto del worker.
WORKER_TOKEN_ENV = "WORKER_TOKEN"

#: Etapas reportables por el worker (sin porcentajes inventados).
WORKER_STAGES = {
    "preparing",
    "content",
    "image",
    "audio",
    "manifest",
    "render",
    "quality",
}

#: Estados terminales aceptados por ``complete``.
COMPLETE_TARGETS = (
    RunStatus.SUCCESS,
    RunStatus.FAILED,
    RunStatus.QUALITY_FAILED,
)


class WorkerService:
    """Protocolo outbound del worker de GPU (lado servidor).

    Args:
        runs_root: raíz de ejecuciones; por defecto la del módulo ``context``
            (``output/runs`` o ``PIPELINE_RUNS_ROOT``).
        storage: implementación de :class:`RunStorage`; por defecto
            :class:`LocalRunStorage` sobre ``runs_root``.
    """

    def __init__(
        self,
        runs_root: Optional[Path] = None,
        storage: Optional[RunStorage] = None,
    ) -> None:
        self._runs_root = Path(runs_root) if runs_root is not None else None
        self._storage = (
            storage if storage is not None else LocalRunStorage(runs_root=runs_root)
        )
        logger.debug(
            "WorkerService creado (runs_root=%s, storage=%s).",
            self._runs_root or "default",
            type(self._storage).__name__,
        )

    # ------------------------------------------------------------------
    # Autenticación e identidad
    # ------------------------------------------------------------------

    def check_token(self, token: Optional[str]) -> bool:
        """Compara el token presentado con ``WORKER_TOKEN`` (tiempo constante).

        El token se lee de ``os.environ`` en cada comprobación; nunca se
        loguea ni se devuelve en respuestas.
        """
        expected = os.environ.get(WORKER_TOKEN_ENV, "")
        if not expected or not isinstance(token, str) or not token:
            return False
        return hmac.compare_digest(token, expected)

    @staticmethod
    def validate_worker_id(worker_id: object) -> str:
        """Valida la identidad lógica del worker.

        Raises:
            ApplicationValidationError: si no cumple ``WORKER_ID_PATTERN``.
        """
        if not isinstance(worker_id, str) or not WORKER_ID_PATTERN.match(worker_id):
            raise ApplicationValidationError(
                f"worker_id inválido: {worker_id!r}. Formato: "
                "letras/dígitos, guiones y guiones bajos."
            )
        return worker_id

    # ------------------------------------------------------------------
    # Polling + claim atómico
    # ------------------------------------------------------------------

    def next_job(self, worker_id: str) -> Optional[dict]:
        """Adquiere el siguiente job ``QUEUED`` y devuelve sus parámetros.

        Reutiliza :func:`pipeline.queue.claim_job` (bloqueo ``O_EXCL``) para
        el cambio **atómico** ``QUEUED → RUNNING``: dos workers no pueden
        obtener el mismo job. Si no hay jobs disponibles devuelve ``None``
        (la API responde 204). **No ejecuta el pipeline.**

        Args:
            worker_id: identidad lógica del worker.

        Returns:
            Payload del job adquirido (``run_id``, ``topic``, ``offline``,
            ``project_id`` y el ``status`` persistido tras el claim
            ``RUNNING``), o ``None`` si no había jobs en cola.

        Raises:
            ApplicationValidationError: si el ``worker_id`` es inválido.
        """
        self.validate_worker_id(worker_id)
        root = self._resolve_runs_root()
        for record in find_queued_jobs(root):
            context = claim_job(root, record.run_id)
            if context is None:
                continue  # otro worker lo adquirió
            payload = load_job_payload(context.run_dir)
            return {
                "run_id": context.run_id,
                "topic": payload.topic if payload else "",
                "offline": bool(payload.offline) if payload else False,
                "project_id": payload.project_id if payload else None,
                "status": RunStatus.RUNNING.value,
            }
        return None

    # ------------------------------------------------------------------
    # Complete (resultado del pipeline)
    # ------------------------------------------------------------------

    def complete_job(
        self,
        run_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> dict:
        """Persiste el resultado del job con validación de transición.

        Reglas:

        - ``RUNNING`` → ``SUCCESS``/``FAILED``/``QUALITY_FAILED``: transición
          válida, se persiste el estado terminal.
        - estado terminal igual al reportado: respuesta **idempotente** (no
          altera nada, no re-ejecuta el pipeline).
        - cualquier otra combinación (p. ej. ``SUCCESS`` → ``FAILED``, sobre un
          job sin reclamar o ``RUNNING`` → ``RUNNING``): conflicto/validación.

        Args:
            run_id: identificador del job reclamado.
            status: estado terminal reportado por el worker.
            error: mensaje de error (solo en ``FAILED``).

        Returns:
            Estado final persistido.

        Raises:
            ApplicationValidationError: si el ``run_id`` es inválido/inseguro
                o el ``status`` no es un terminal válido.
            ApplicationRunNotFoundError: si el run no existe.
            ApplicationConflictError: si la transición no es válida.
        """
        run_dir = self._checked_run_dir(run_id)
        record = load_run_record(run_dir)
        if record is None:
            raise ApplicationRunNotFoundError(
                f"No existe el run con estado legible: {run_id}"
            )
        target = self._parse_target(status)
        current = record.status

        if current is RunStatus.RUNNING:
            self._persist_terminal(run_dir, record, target, error)
            release_job(self._resolve_runs_root(), run_id)
            return {"run_id": run_id, "status": target.value}

        if current is target:
            # Idempotente: el worker reintentó complete tras perder la respuesta.
            release_job(self._resolve_runs_root(), run_id)
            return {"run_id": run_id, "status": target.value}

        raise ApplicationConflictError(
            f"Transición no válida para el run {run_id}: "
            f"{current.value} -> {target.value}"
        )

    # ------------------------------------------------------------------
    # Progress y heartbeat
    # ------------------------------------------------------------------

    def report_progress(self, run_id: str, stage: str) -> None:
        """Registra un avance de etapa del worker (advisory, sin persistir).

        El stage ya se deriva de los artefactos en ``GET /runs/{id}``
        (ME40.4, sección 7: no se guarda progreso redundante). Solo valida que
        el run esté en ejecución y la etapa sea conocida.
        """
        run_dir = self._checked_run_dir(run_id)
        if stage not in WORKER_STAGES:
            raise ApplicationValidationError(f"stage inválido: {stage!r}")
        self._require_running(run_dir, run_id)

    def heartbeat(self, run_id: str) -> str:
        """Confirma que el worker sigue vivo (no cambia el resultado del job).

        Sin recuperación automática de ``RUNNING`` en esta ME.
        """
        run_dir = self._checked_run_dir(run_id)
        self._require_running(run_dir, run_id)
        return RunStatus.RUNNING.value

    # ------------------------------------------------------------------
    # Ayudantes
    # ------------------------------------------------------------------

    def _checked_run_dir(self, run_id: str) -> Path:
        """Directorio del run validado (o excepciones de capa)."""
        try:
            run_dir = self._storage.run_dir(run_id)
        except StorageError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        if run_dir is None:
            raise ApplicationRunNotFoundError(f"No existe el run: {run_id}")
        return run_dir

    @staticmethod
    def _parse_target(status: object) -> RunStatus:
        try:
            target = RunStatus(str(status or ""))
        except ValueError:
            raise ApplicationValidationError(
                f"Estado de finalización inválido: {status!r}"
            ) from None
        if target not in COMPLETE_TARGETS:
            raise ApplicationValidationError(
                f"Estado de finalización inválido: {status!r}"
            )
        return target

    @staticmethod
    def _require_running(run_dir: Path, run_id: str) -> RunRecord:
        record = load_run_record(run_dir)
        if record is None or record.status is not RunStatus.RUNNING:
            raise ApplicationConflictError(f"El run {run_id} no está en ejecución.")
        return record

    def _persist_terminal(
        self,
        run_dir: Path,
        previous: RunRecord,
        target: RunStatus,
        error: Optional[str],
    ) -> None:
        """Persiste el estado terminal validado (transición RUNNING → target)."""
        quality_passed: Optional[bool] = None
        if target is RunStatus.SUCCESS:
            quality_passed = True
        elif target is RunStatus.QUALITY_FAILED:
            quality_passed = False
        try:
            write_run_record(
                run_dir,
                RunRecord(
                    run_id=previous.run_id or run_dir.name,
                    status=target,
                    created_at=previous.created_at,
                    queued_at=previous.queued_at,
                    started_at=previous.started_at,
                    finished_at=_utc_now(),
                    error=error,
                    quality_passed=quality_passed,
                ),
            )
        except (OSError, PipelineValidationError) as exc:
            raise ApplicationConflictError(
                f"No se pudo persistir el estado {target.value} del run "
                f"{run_dir.name}: {exc}"
            ) from exc

    def _resolve_runs_root(self) -> Path:
        """Raíz real de ejecuciones del servicio (explicita o por defecto)."""
        if self._runs_root is not None:
            return Path(self._runs_root)
        from pipeline.context import RUNS_ROOT

        return RUNS_ROOT