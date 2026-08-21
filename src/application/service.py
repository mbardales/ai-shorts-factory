"""Servicio de aplicación de AI Shorts Factory (ME30.1).

:class:`ApplicationService` es la **API interna de aplicación** que encapsula
el :class:`PipelineRunner` para crear runs y consultar su estado persistido,
sin HTTP y sin duplicar la lógica del pipeline:

- :meth:`create_run` valida la petición, crea/usar el :class:`RunContext` y
  **encola** el run (``QUEUED`` + ``job.json``) sin ejecutar el pipeline; la
  ejecución la realiza un worker local de forma asíncrona (respeta ``offline``
  y ``project_id``; no usa ``subprocess`` adicional).
- :meth:`get_run` valida el ``run_id`` y lee el estado persistido
  (``run.json``) mediante las APIs existentes; **no** vuelve a ejecutar el
  pipeline.
- :meth:`list_runs` devuelve resúmenes de historial (solo lectura) con una URL
  local segura del video, reutilizando :func:`pipeline.lifecycle.list_runs`.
- :meth:`get_run_video` delega en :class:`application.storage.RunStorage` para
  localizar y validar el video de un run para servirlo por la API, sin aceptar
  rutas arbitrarias del cliente.

El almacenamiento de runs está abstraído en :class:`RunStorage`
(:class:`LocalRunStorage` por defecto): ``ApplicationService`` y la API no
saben si el storage es local, S3, R2, etc. (ME40.3).

La **persistencia de metadata** (estado de runs, proyectos, proyecto activo y
asociación run→proyecto) está abstraída en :class:`PersistenceRepository`
(:class:`LocalPersistenceRepository` por defecto): el servicio ya no conoce el
mecanismo JSON subyacente, de modo que una futura implementación sobre
PostgreSQL pueda sustituirlo sin cambiar ni la API ni el frontend (ME40.5).

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
    PipelineValidationError,
    RunContext,
)
from pipeline.lifecycle import RunRecord, RunStatus
from pipeline.queue import JobPayload, write_job_payload

from .artifact_access import ArtifactAccess
from .artifact_store_factory import ArtifactStoreConfigError, build_artifact_store
from .artifacts import ArtifactValidationError
from .exceptions import (
    ApplicationError,
    ApplicationProjectNotFoundError,
    ApplicationRunNotFoundError,
    ApplicationValidationError,
)
from .repository import LocalPersistenceRepository, PersistenceRepository
from .projects import validate_project_id
from .storage import LocalRunStorage, RunStorage, StorageError
from .models import (
    ArtifactListResponse,
    ArtifactResponse,
    CreateProjectRequest,
    CreateRunRequest,
    CreateRunResponse,
    ProjectRecord,
    ProjectsOverview,
    RunStatusResponse,
    RunSummary,
)

logger = logging.getLogger(__name__)


class ApplicationService:
    """API interna de aplicación sobre el pipeline de generación.

    Args:
        runs_root: raíz de ejecuciones; por defecto la del módulo ``context``
            (``output/runs`` o ``PIPELINE_RUNS_ROOT``).
        storage: implementación de :class:`RunStorage`; por defecto
            :class:`LocalRunStorage` sobre ``runs_root``.
        repository: implementación de :class:`PersistenceRepository` para la
            metadata (runs/proyectos); por defecto
            :class:`LocalPersistenceRepository` sobre ``runs_root``.
        artifact_access: acceso a los artifacts publicados del run (ME40.9D);
            opcional. Si no se indica, se construye una instancia por defecto
            mediante :func:`application.artifact_store_factory.build_artifact_store`
            sobre ``runs_root`` (backend ``local`` por defecto).
    """

    def __init__(
        self,
        runs_root: Optional[Path] = None,
        storage: Optional[RunStorage] = None,
        repository: Optional[PersistenceRepository] = None,
        artifact_access: Optional[ArtifactAccess] = None,
    ) -> None:
        self._runs_root = Path(runs_root) if runs_root is not None else None
        self._storage = (
            storage if storage is not None else LocalRunStorage(runs_root=runs_root)
        )
        self._repository = (
            repository
            if repository is not None
            else LocalPersistenceRepository(runs_root=runs_root)
        )
        self._artifact_access = artifact_access
        logger.debug(
            "ApplicationService creado (runs_root=%s, storage=%s, repository=%s).",
            self._runs_root or "default",
            type(self._storage).__name__,
            type(self._repository).__name__,
        )

    def create_run(self, request: CreateRunRequest) -> CreateRunResponse:
        """Valida la petición y **encola** un run sin ejecutar el pipeline.

        El pipeline ya no corre dentro de la petición HTTP (ME40.2): la
        creación persiste el run como ``QUEUED`` y guarda los parámetros de
        ejecución en ``job.json``; un worker local (``scripts/run_worker.py``)
        lo adquiere, lo pasa a ``RUNNING`` y ejecuta el pipeline de forma
        asíncrona.

        Args:
            request: petición de creación del run.

        Returns:
            Respuesta inmediata con el ``run_id`` y el estado inicial
            ``QUEUED``.

        Raises:
            ApplicationValidationError: si el tema está vacío o el ``run_id``
                es inválido.
            ApplicationError: si falla la persistencia del run encolado.
        """
        topic = (request.topic or "").strip()
        if not topic:
            raise ApplicationValidationError("El tema del Short no puede estar vacío.")

        project_id = None
        if request.project_id:
            project_id = validate_project_id(request.project_id)

        context = self._build_context(request.run_id)
        self._associate_run(context.run_id, project_id)
        self._enqueue(context, topic=topic, offline=request.offline, project_id=project_id)
        logger.info("Run %s encolado (QUEUED).", context.run_id)
        return CreateRunResponse(
            run_id=context.run_id,
            status=RunStatus.QUEUED.value,
            project_id=project_id,
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
        self._build_context(run_id)
        try:
            exists = self._repository.run_exists(run_id)
        except PipelineValidationError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        if not exists:
            raise ApplicationRunNotFoundError(f"No existe el run: {run_id}")

        record = self._repository.load_run(run_id)
        if record is None:
            record = RunRecord(run_id=run_id, status=RunStatus.UNKNOWN)
        return RunStatusResponse(
            run_id=record.run_id or run_id,
            status=record.status.value,
            success=_success_for_status(record.status),
            video_path=self._video_url(record.run_id or run_id, record),
            error=record.error,
            stage=self._derive_stage(self._run_dir_path(run_id), record),
            project_id=self._repository.project_id_for_run(run_id),
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
        wanted = self._resolve_project_filter(project_id)
        summaries: list[RunSummary] = []
        for record in self._repository.list_runs():
            run_project = self._repository.project_id_for_run(record.run_id)
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
                    video_url=self._video_url(record.run_id, record),
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
        return self._repository.create_project(name, request.description)

    def list_projects(self) -> ProjectsOverview:
        """Lista los proyectos registrados y el proyecto activo.

        Returns:
            Resumen de proyectos (solo lectura).
        """
        projects, active = self._repository.list_projects()
        return ProjectsOverview(projects=tuple(projects), active_project_id=active)

    def get_project(self, project_id: str) -> ProjectRecord:
        """Devuelve un proyecto registrado.

        Raises:
            ApplicationProjectNotFoundError: si no existe el proyecto.
        """
        return self._repository.get_project(project_id)

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
        self._repository.set_active_project(project_id)
        if project_id is None:
            return ProjectRecord(project_id="", name="")
        return self._repository.get_project(project_id)

    def _associate_run(self, run_id: str, project_id: Optional[str]) -> None:
        """Registra la asociación run-proyecto antes de ejecutar el pipeline."""
        try:
            self._repository.associate_run(run_id, project_id)
        except OSError as exc:
            raise ApplicationError(
                f"No se pudo guardar la asociación de proyecto: {exc}"
            ) from exc

    def _enqueue(
        self,
        context: RunContext,
        *,
        topic: str,
        offline: bool,
        project_id: Optional[str],
    ) -> None:
        """Persiste el run como ``QUEUED`` y guarda los parámetros en ``job.json``.

        No ejecuta el pipeline: el worker local adquirirá el job más adelante.
        El estado del run se persiste a través del repositorio (metadata);
        ``job.json`` son los parámetros de la cola (ME40.2).
        """
        now = datetime.now(timezone.utc)
        try:
            self._repository.save_run(
                context.run_id,
                RunRecord(
                    run_id=context.run_id,
                    status=RunStatus.QUEUED,
                    created_at=now,
                    queued_at=now,
                ),
            )
            write_job_payload(
                context.run_dir,
                JobPayload(
                    run_id=context.run_id,
                    topic=topic,
                    offline=offline,
                    project_id=project_id,
                ),
            )
        except (OSError, PipelineValidationError) as exc:
            raise ApplicationError(f"No se pudo encolar el run: {exc}") from exc

    @staticmethod
    def _resolve_project_filter(project_id: Optional[str]) -> Optional[str]:
        """Valida el filtro de proyecto y devuelve el valor normalizado."""
        if project_id is None:
            return None
        if project_id == "none":
            return "none"
        return validate_project_id(project_id)

    def get_run_video(self, run_id: str) -> Path:
        """Devuelve la ruta local del video servible de un run (delegando en storage).

        Delega en :class:`RunStorage` (ME40.3): la resolución valida el
        ``run_id``, mantiene el path contenido dentro del run y solo sirve
        runs ``SUCCESS``/``QUALITY_FAILED`` con video renderizado. La ruta
        resultante es **interna** (la consume ``FileResponse``); nunca se
        expone al cliente.

        Args:
            run_id: identificador de la ejecución.

        Returns:
            Ruta absoluta del video dentro de ``runs_root/<run_id>/output/video``.

        Raises:
            ApplicationValidationError: si el ``run_id`` es inválido o queda
                fuera de ``runs_root`` (traversal/ruta absoluta).
            ApplicationRunNotFoundError: si el run no existe o no tiene video
                disponible.
        """
        try:
            video = self._storage.resolve_video(run_id)
        except StorageError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        if video is None:
            raise ApplicationRunNotFoundError(
                f"El run {run_id} no tiene un video disponible."
            )
        return Path(video)

    def list_artifacts(self, run_id: str) -> ArtifactListResponse:
        """Lista la metadata de los artifacts publicados de un run.

        Valida el ``run_id`` y comprueba la existencia del run mediante el
        repositorio (mismo mecanismo que :meth:`get_run`) y delega
        **exclusivamente** en :class:`ArtifactAccess` (que a su vez usa
        :class:`ArtifactStore`); el servicio nunca accede al filesystem ni a S3
        directamente. Devuelve únicamente metadata de los ``ArtifactRecord``,
        sin URLs públicas ni rutas absolutas.

        Args:
            run_id: identificador de la ejecución.

        Returns:
            Listado de artifacts del run (puede estar vacío).

        Raises:
            ApplicationValidationError: si el ``run_id`` es inválido.
            ApplicationRunNotFoundError: si no existe el run.
            ApplicationError: si no se puede consultar el ArtifactAccess.
        """
        self._build_context(run_id)
        try:
            exists = self._repository.run_exists(run_id)
        except PipelineValidationError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        if not exists:
            raise ApplicationRunNotFoundError(f"No existe el run: {run_id}")

        access = self._resolve_artifact_access()
        try:
            records = access.list_artifacts(run_id)
        except ArtifactValidationError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - error del store -> aplicación
            raise ApplicationError(
                f"No se pudieron consultar los artifacts del run {run_id}: {exc}"
            ) from exc
        return ArtifactListResponse(
            run_id=run_id,
            artifacts=tuple(
                ArtifactResponse(
                    artifact_id=record.artifact_id,
                    run_id=record.run_id,
                    kind=record.kind,
                    filename=record.filename,
                    content_type=record.content_type,
                    size_bytes=record.size_bytes,
                    reference=record.reference,
                    storage=record.storage,
                )
                for record in records
            ),
        )

    def get_video_temporary_url(self, run_id: str, expires_in: int) -> Optional[str]:
        """URL temporal firmada del video de un run, si existe.

        Delega **exclusivamente** en :class:`ArtifactAccess` (que a su vez
        delega en :class:`ArtifactStore`): el servicio no conoce S3, boto3,
        buckets ni object keys, no accede al filesystem y nunca genera URLs.
        Si el run no tiene video publicada devuelve ``None``.

        Args:
            run_id: identificador de la ejecución.
            expires_in: validez de la URL en segundos (entero positivo).

        Returns:
            URL temporal firmada del video, o ``None`` si el run no tiene video.

        Raises:
            ApplicationValidationError: si el ``run_id`` o ``expires_in`` son
                inválidos.
            ApplicationRunNotFoundError: si no existe el run.
            ApplicationError: si el backend no puede emitir la URL temporal o
                falla la consulta.
        """
        self._build_context(run_id)
        try:
            exists = self._repository.run_exists(run_id)
        except PipelineValidationError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        if not exists:
            raise ApplicationRunNotFoundError(f"No existe el run: {run_id}")

        access = self._resolve_artifact_access()
        try:
            return access.get_video_temporary_url(run_id, expires_in)
        except ArtifactValidationError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - error del store -> aplicación
            raise ApplicationError(
                f"No se pudo obtener la URL temporal del video del run "
                f"{run_id}: {exc}"
            ) from exc

    def supports_temporary_urls(self) -> bool:
        """Indica si el backend de artifacts emite URLs temporales (ME40.9E).

        Delega en :class:`ArtifactAccess`: los backends remotos S3/R2 firman
        presigned URLs (``True``) y el backend local custodia el archivo y lo
        sirve en línea a través del control plane (``False``). La API usa esta
        señal para elegir entre redirección 307 y entrega inline **sin**
        conocer el proveedor.
        """
        return self._resolve_artifact_access().supports_temporary_url()

    def get_video_content(self, run_id: str) -> Optional[bytes]:
        """Contenido binario del video de un run, si existe (ME40.9E).

        Modo de entrega del backend local: valida el ``run_id``, comprueba la
        existencia del run (mismo mecanismo que :meth:`get_run`) y delega
        **exclusivamente** en :class:`ArtifactAccess` →
        :meth:`ArtifactStore.read_content`. El servicio nunca accede al
        filesystem ni genera contenido; solo transporta los bytes que el store
        custodia. Devuelve ``None`` si el run no tiene video publicada.

        Args:
            run_id: identificador de la ejecución.

        Returns:
            Contenido binario del video, o ``None`` si no hay video.

        Raises:
            ApplicationValidationError: si el ``run_id`` es inválido.
            ApplicationRunNotFoundError: si no existe el run.
            ApplicationError: si el backend falla al leer el contenido o no
                puede servirlo en línea.
        """
        self._build_context(run_id)
        try:
            exists = self._repository.run_exists(run_id)
        except PipelineValidationError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        if not exists:
            raise ApplicationRunNotFoundError(f"No existe el run: {run_id}")

        access = self._resolve_artifact_access()
        try:
            return access.get_video_content(run_id)
        except ArtifactValidationError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - error del store -> aplicación
            raise ApplicationError(
                f"No se pudo leer el video del run {run_id}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Ayudantes
    # ------------------------------------------------------------------

    def _run_dir_path(self, run_id: str) -> Path:
        """Directorio del run (para derivar la etapa desde sus artefactos).

        Se resuelve a través de :class:`RunStorage` (ME40.3); el run existe
        (ya validado por el repositorio), por lo que nunca es ``None`` para
        ids válidos. El directorio se usa solo para inspeccionar artefactos;
        la metadata vive en el repositorio.
        """
        try:
            path = self._storage.run_dir(run_id)
        except StorageError:
            path = None
        if path is None:
            from pipeline.context import RUNS_ROOT

            path = RUNS_ROOT / run_id
        return path

    def _resolve_artifact_access(self) -> ArtifactAccess:
        """Resuelve el :class:`ArtifactAccess` (inyectado o por defecto).

        Cuando no se inyectó ninguno, construye un acceso por defecto sobre
        ``runs_root`` mediante :func:`build_artifact_store` (backend ``local``
        salvo configuración explícita). Los errores de configuración se
        traducen a :class:`ApplicationError`.
        """
        if self._artifact_access is not None:
            return self._artifact_access
        if self._runs_root is not None:
            runs_root = self._runs_root
        else:
            from pipeline.context import RUNS_ROOT

            runs_root = RUNS_ROOT
        try:
            self._artifact_access = ArtifactAccess(build_artifact_store(runs_root))
        except ArtifactStoreConfigError as exc:
            raise ApplicationError(f"ArtifactAccess no configurado: {exc}") from exc
        return self._artifact_access

    def _video_url(self, run_id: str, record: RunRecord) -> Optional[str]:
        """URL local segura del video del run (o ``None``).

        Solo los runs ``SUCCESS``/``QUALITY_FAILED`` con video renderizado
        obtienen URL; la URL apunta al endpoint servidor de la API, basado
        exclusivamente en el ``run_id`` validado. **Nunca expone rutas
        absolutas del filesystem**.
        """
        if record.status not in (RunStatus.SUCCESS, RunStatus.QUALITY_FAILED):
            return None
        try:
            if not self._storage.has_video(run_id):
                return None
        except StorageError:
            return None
        return f"/api/v1/runs/{run_id}/video"

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
