"""Servicio de aplicación de AI Shorts Factory (ME30.1).

:class:`ApplicationService` es la **API interna de aplicación** que encapsula
el :class:`PipelineRunner` para crear runs y consultar su estado persistido,
sin HTTP y sin duplicar la lógica del pipeline:

- :meth:`create_run` valida la petición, crea/usar el :class:`RunContext` y
  ejecuta el pipeline con :class:`PipelineRunner` (respeta ``offline`` y
  ``project_id``; no usa ``subprocess`` adicional).
- :meth:`get_run` valida el ``run_id`` y lee el estado persistido
  (``run.json``) mediante las APIs existentes; **no** vuelve a ejecutar el
  pipeline.
- :meth:`list_runs` devuelve resúmenes de historial (solo lectura) con una URL
  local segura del video, reutilizando :func:`pipeline.lifecycle.list_runs`.
- :meth:`get_run_video` localiza y valida el video de un run para servirlo por
  la API, sin aceptar rutas arbitrarias del cliente.

La raíz de ejecuciones se resuelve desde ``pipeline.context`` (variable de
entorno ``PIPELINE_RUNS_ROOT``) salvo que se indique explícitamente en el
constructor.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pipeline import (
    PipelineRunner,
    PipelineValidationError,
    RunContext,
    list_runs,
    load_run_record,
    unknown_record,
)
from pipeline.exceptions import PipelineError
from pipeline.lifecycle import RunRecord, RunStatus, checked_run_dir

from .exceptions import (
    ApplicationError,
    ApplicationProjectNotFoundError,
    ApplicationRunNotFoundError,
    ApplicationValidationError,
)
from .models import (
    CreateProjectRequest,
    CreateRunRequest,
    CreateRunResponse,
    ProjectRecord,
    ProjectsOverview,
    RunStatusResponse,
    RunSummary,
)
from .projects import (
    associate_run,
    create_project as _create_project,
    get_project as _get_project,
    list_projects as _list_projects,
    project_id_for_run,
    set_active_project as _set_active_project,
    validate_project_id,
)

logger = logging.getLogger(__name__)


class ApplicationService:
    """API interna de aplicación sobre el pipeline de generación.

    Args:
        runs_root: raíz de ejecuciones; por defecto la del módulo ``context``
            (``output/runs`` o ``PIPELINE_RUNS_ROOT``).
    """

    def __init__(self, runs_root: Optional[Path] = None) -> None:
        self._runs_root = Path(runs_root) if runs_root is not None else None
        logger.debug(
            "ApplicationService creado (runs_root=%s).",
            self._runs_root or "default",
        )

    def create_run(self, request: CreateRunRequest) -> CreateRunResponse:
        """Valida la petición y ejecuta el pipeline en un run aislado.

        Args:
            request: petición de creación del run.

        Returns:
            Respuesta con el ``run_id`` y el estado final persistido.

        Raises:
            ApplicationValidationError: si el tema está vacío o el ``run_id``
                es inválido.
            ApplicationError: si la ejecución del pipeline falla de forma no
                controlada.
        """
        topic = (request.topic or "").strip()
        if not topic:
            raise ApplicationValidationError("El tema del Short no puede estar vacío.")

        project_id = None
        if request.project_id:
            project_id = validate_project_id(request.project_id)

        context = self._build_context(request.run_id)
        self._associate_run(context.run_id, project_id)
        runner = PipelineRunner(
            context,
            topic=topic,
            offline=request.offline,
            project_id=project_id,
        )
        try:
            result = runner.run()
        except PipelineValidationError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        except PipelineError as exc:
            raise ApplicationError(f"El pipeline falló: {exc}") from exc

        status = self._final_status(context.run_dir, success=result.success)
        logger.info("Run %s terminó con estado %s.", result.run_id, status)
        return CreateRunResponse(
            run_id=result.run_id, status=status, project_id=project_id
        )

    def get_run(self, run_id: str) -> RunStatusResponse:
        """Consulta el estado persistido de un run existente.

        Lee ``run.json`` mediante las APIs existentes; no ejecuta el
        pipeline. Un ``run.json`` ausente o ilegible se degrada a ``UNKNOWN``.

        Args:
            run_id: identificador de la ejecución.

        Returns:
            Estado del run consultado.

        Raises:
            ApplicationValidationError: si el ``run_id`` es inválido.
            ApplicationRunNotFoundError: si no existe el run.
        """
        context = self._build_context(run_id)
        try:
            checked_run_dir(context.run_dir, runs_root=self._runs_root)
        except PipelineValidationError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        if not context.run_dir.is_dir():
            raise ApplicationRunNotFoundError(f"No existe el run: {run_id}")

        record = load_run_record(context.run_dir) or unknown_record(context.run_dir)
        return RunStatusResponse(
            run_id=record.run_id or run_id,
            status=record.status.value,
            success=_success_for_status(record.status),
            video_path=self._find_video(context.run_dir),
            error=record.error,
            stage=self._derive_stage(context.run_dir, record),
            project_id=project_id_for_run(self._resolve_runs_root(), context.run_id),
        )

    def list_runs(self, project_id: Optional[str] = None) -> list[RunSummary]:
        """Lista los runs existentes como resúmenes de historial (solo lectura).

        Reutiliza :func:`pipeline.lifecycle.list_runs` (no ejecuta pipelines ni
        crea contexts) y enriquece cada registro con la duración, el
        ``project_id`` y una URL local segura del video. **No expone rutas
        absolutas del filesystem**: el video se sirve por la API mediante
        ``GET /api/v1/runs/{run_id}/video``.

        Args:
            project_id: si se indica, filtra los runs asociados a ese
                proyecto (``None`` = todos). El valor especial ``"none"``
                filtra los runs sin proyecto.

        Returns:
            Resúmenes de los runs ordenados por fecha descendente (los
            más recientes primero).

        Raises:
            ApplicationValidationError: si el ``project_id`` es inválido.
        """
        root = self._resolve_runs_root()
        wanted = self._resolve_project_filter(project_id)
        summaries: list[RunSummary] = []
        for record in list_runs(root):
            run_dir = root / record.run_id
            run_project = project_id_for_run(root, record.run_id)
            if wanted == "none" and run_project is not None:
                continue
            if wanted and wanted != "none" and run_project != wanted:
                continue
            summaries.append(
                RunSummary(
                    run_id=record.run_id,
                    status=record.status.value,
                    created_at=_to_iso(record.created_at or record.started_at),
                    duration_seconds=_duration_seconds(record),
                    video_url=self._video_url_for(run_dir, record),
                    error=record.error,
                    project_id=run_project,
                )
            )
        summaries.sort(
            key=lambda summary: (summary.created_at or "", summary.run_id),
            reverse=True,
        )
        return summaries

    def create_project(self, request: CreateProjectRequest) -> ProjectRecord:
        """Crea un proyecto y lo deja como activo.

        Args:
            request: petición de creación del proyecto.

        Returns:
            El :class:`ProjectRecord` creado.

        Raises:
            ApplicationValidationError: si el nombre está vacío.
        """
        name = (request.name or "").strip()
        if not name:
            raise ApplicationValidationError("El nombre del proyecto no puede estar vacío.")
        return _create_project(
            self._resolve_runs_root(),
            name,
            request.description,
        )

    def list_projects(self) -> ProjectsOverview:
        """Lista los proyectos registrados y el proyecto activo.

        Returns:
            Resumen de proyectos (solo lectura).
        """
        projects, active = _list_projects(self._resolve_runs_root())
        return ProjectsOverview(projects=tuple(projects), active_project_id=active)

    def get_project(self, project_id: str) -> ProjectRecord:
        """Devuelve un proyecto registrado.

        Raises:
            ApplicationProjectNotFoundError: si no existe el proyecto.
        """
        return _get_project(self._resolve_runs_root(), project_id)

    def set_active_project(self, project_id: Optional[str]) -> ProjectRecord:
        """Establece (o limpia) el proyecto activo.

        Args:
            project_id: id del proyecto a activar, o ``None`` para "sin
                proyecto".

        Returns:
            El proyecto activado (o un registro vacío si se limpió).

        Raises:
            ApplicationProjectNotFoundError: si el proyecto no existe.
        """
        root = self._resolve_runs_root()
        _set_active_project(root, project_id)
        if project_id is None:
            return ProjectRecord(project_id="", name="")
        return _get_project(root, project_id)

    def _associate_run(self, run_id: str, project_id: Optional[str]) -> None:
        """Registra la asociación run-proyecto antes de ejecutar el pipeline."""
        try:
            associate_run(self._resolve_runs_root(), run_id, project_id)
        except OSError as exc:
            raise ApplicationError(
                f"No se pudo guardar la asociación de proyecto: {exc}"
            ) from exc

    @staticmethod
    def _resolve_project_filter(project_id: Optional[str]) -> Optional[str]:
        """Valida el filtro de proyecto y devuelve el valor normalizado."""
        if project_id is None:
            return None
        if project_id == "none":
            return "none"
        return validate_project_id(project_id)

    def get_run_video(self, run_id: str) -> Path:
        """Devuelve la ruta del video renderizado de un run, validada y segura.

        La ruta se deriva **exclusivamente** del ``run_id`` validado y del
        escaneo de ``run_dir/output/video``: nunca se acepta una ruta arbitraria
        del cliente y el resultado siempre queda contenido dentro del run. Solo
        los runs ``SUCCESS`` y ``QUALITY_FAILED`` con video tienen archivo
        servible; el resto se trata como "no encontrado".

        Args:
            run_id: identificador de la ejecución.

        Returns:
            Ruta absoluta del video dentro de ``runs_root/<run_id>/output/video``.

        Raises:
            ApplicationValidationError: si el ``run_id`` es inválido o queda
                fuera de ``runs_root``.
            ApplicationRunNotFoundError: si el run no existe o no tiene video
                disponible.
        """
        context = self._build_context(run_id)
        try:
            checked_run_dir(context.run_dir, runs_root=self._runs_root)
        except PipelineValidationError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        if not context.run_dir.is_dir():
            raise ApplicationRunNotFoundError(f"No existe el run: {run_id}")

        record = load_run_record(context.run_dir) or unknown_record(context.run_dir)
        if record.status not in (RunStatus.SUCCESS, RunStatus.QUALITY_FAILED):
            raise ApplicationRunNotFoundError(
                f"El run {run_id} no tiene un video disponible."
            )
        video = self._find_video(context.run_dir)
        if video is None:
            raise ApplicationRunNotFoundError(
                f"El run {run_id} no tiene video renderizado."
            )
        return Path(video)

    # ------------------------------------------------------------------
    # Ayudantes
    # ------------------------------------------------------------------

    def _resolve_runs_root(self) -> Path:
        """Raíz real de ejecuciones del servicio (explicita o por defecto)."""
        if self._runs_root is not None:
            return Path(self._runs_root)
        from pipeline.context import RUNS_ROOT

        return RUNS_ROOT

    def _video_url_for(self, run_dir: Path, record: RunRecord) -> Optional[str]:
        """URL local segura del video del run (o ``None``).

        Solo los runs ``SUCCESS``/``QUALITY_FAILED`` con video renderizado
        obtienen URL; la URL apunta al endpoint servidor de la API, basado
        exclusivamente en el ``run_id`` validado.
        """
        if record.status not in (RunStatus.SUCCESS, RunStatus.QUALITY_FAILED):
            return None
        if self._find_video(run_dir) is None:
            return None
        return f"/api/v1/runs/{run_dir.name}/video"

    def _build_context(self, run_id: Optional[str]) -> RunContext:
        """Crea el :class:`RunContext` validando el ``run_id`` cuando exista."""
        from pipeline import generate_run_id

        try:
            if run_id:
                if self._runs_root is not None:
                    return RunContext(run_id=run_id, runs_root=self._runs_root)
                return RunContext(run_id=run_id)
            if self._runs_root is not None:
                return RunContext(run_id=generate_run_id(), runs_root=self._runs_root)
            return RunContext.create()
        except PipelineValidationError as exc:
            raise ApplicationValidationError(str(exc)) from exc

    def _final_status(self, run_dir: Path, *, success: bool) -> str:
        """Estado final persistido del run (o derivado si falta ``run.json``)."""
        record = load_run_record(run_dir)
        if record is not None and record.status != RunStatus.RUNNING:
            return record.status.value
        return RunStatus.SUCCESS.value if success else RunStatus.FAILED.value

    @staticmethod
    def _find_video(run_dir: Path) -> Optional[str]:
        """Ruta del primer video renderizado del run (o ``None``)."""
        video_dir = run_dir / "output" / "video"
        if not video_dir.is_dir():
            return None
        for entry in sorted(video_dir.iterdir()):
            if entry.is_file() and entry.suffix.lower() in {".mp4", ".mov", ".webm"}:
                return str(entry)
        return None

    @staticmethod
    def _derive_stage(run_dir: Path, record: RunRecord) -> Optional[str]:
        """Etapa en curso derivada de los artefactos presentes (o ``None``).

        Solo tiene sentido mientras el run está en ejecución: en cuanto un
        artefacto de la etapa existe, esa etapa se considera completada. La
        primera etapa sin artefacto es la etapa activa. Devuelve ``None`` si el
        run no está en ``RUNNING`` o no hay artefactos todavía.
        """
        if record.status is not RunStatus.RUNNING:
            return None
        output = run_dir / "output"
        if not (output / "content.json").is_file():
            return "content"
        images = output / "images"
        if not images.is_dir() or not any(images.glob("scene_*.png")):
            return "image"
        audio = output / "audio"
        if not audio.is_dir() or not any(
            entry.is_file() for entry in audio.iterdir()
        ):
            return "audio"
        if not (output / "project.json").is_file():
            return "manifest"
        video = output / "video"
        if not video.is_dir() or not any(
            entry.is_file() for entry in video.iterdir()
        ):
            return "render"
        return "quality"


def _success_for_status(status: RunStatus) -> Optional[bool]:
    """Mapea un estado persistido a un booleano de éxito (o ``None``)."""
    if status in (RunStatus.SUCCESS,):
        return True
    if status in (RunStatus.FAILED, RunStatus.QUALITY_FAILED):
        return False
    return None


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    """Convierte un datetime a ISO 8601 UTC (o ``None``)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _duration_seconds(record: RunRecord) -> Optional[float]:
    """Duración del pipeline en segundos (o ``None`` si no es determinable)."""
    if record.finished_at is None:
        return None
    start = record.started_at or record.created_at
    if start is None:
        return None
    try:
        return round((record.finished_at - start).total_seconds(), 2)
    except TypeError:
        return None
