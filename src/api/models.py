"""Modelos de la capa HTTP de AI Shorts Factory (ME30.2).

Modelos Pydantic de entrada/salida de la API. Son el contrato de transporte:
la capa HTTP los traduce a/desde los dataclasses de ``application.models``
(delegando siempre en :class:`ApplicationService`).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CreateRunRequest(BaseModel):
    """Cuerpo de ``POST /api/v1/runs``.

    Attributes:
        topic: tema del Short (obligatorio, no vacío).
        run_id: identificador opcional del run (``run-YYYYMMDD-HHMMSS``).
        project_id: identificador opcional del contenido.
        offline: si ``True``, usa los proveedores sintéticos (sin API ni red).
    """

    topic: str
    run_id: Optional[str] = None
    project_id: Optional[str] = None
    offline: bool = False


class CreateRunResponse(BaseModel):
    """Respuesta de ``POST /api/v1/runs`` (HTTP 202).

    Attributes:
        run_id: identificador de la ejecución creada.
        status: estado final persistido del run.
        project_id: proyecto al que se asoció el run (o ``null``).
    """

    run_id: str
    status: str
    project_id: Optional[str] = None


class RunStatusResponse(BaseModel):
    """Respuesta de ``GET /api/v1/runs/{run_id}``.

    Attributes:
        run_id: identificador de la ejecución.
        status: estado persistido (``RUNNING``, ``SUCCESS``, ``FAILED``,
            ``QUALITY_FAILED``, ``UNKNOWN``).
        success: ``True``/``False`` si el estado es terminal; ``null`` si no.
        video_path: URL local segura del video (``/api/v1/runs/{run_id}/video``)
            cuando el run tiene video servible; ``null`` en otro caso. Nunca es
            una ruta absoluta del filesystem.
        error: mensaje de error persistido (o ``null``).
        stage: etapa en curso derivada de los artefactos (o ``null`` si no
            está en ejecución).
        project_id: proyecto al que está asociado el run (o ``null``).
    """

    run_id: str
    status: str
    success: Optional[bool] = None
    video_path: Optional[str] = None
    error: Optional[str] = None
    stage: Optional[str] = None
    project_id: Optional[str] = None


class RunSummaryResponse(BaseModel):
    """Resumen de un run para ``GET /api/v1/runs`` (historial).

    No expone rutas absolutas del filesystem: el video se referencia con una
    URL local segura de la propia API (``/api/v1/runs/{run_id}/video``).

    Attributes:
        run_id: identificador de la ejecución.
        status: estado persistido.
        created_at: fecha de creación en ISO 8601 UTC (o ``null``).
        duration_seconds: duración del pipeline (o ``null``).
        video_url: URL local del video (o ``null`` si no hay video).
        error: mensaje de error persistido (o ``null``).
        project_id: proyecto al que está asociado el run (o ``null``).
    """

    run_id: str
    status: str
    created_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    video_url: Optional[str] = None
    error: Optional[str] = None
    project_id: Optional[str] = None


class ArtifactResponse(BaseModel):
    """Metadata de un artifact publicado por un run (``GET /api/v1/runs/{run_id}/artifacts``).

    Nunca expone rutas absolutas del filesystem ni URLs públicas de descarga:
    ``reference`` es una referencia interna controlada por el servidor (object
    key del almacenamiento o ruta relativa al run).

    Attributes:
        artifact_id: identificador único del artifact.
        run_id: identificador de la ejecución.
        kind: tipo lógico del artifact (``video``, ``image``, ...).
        filename: nombre del archivo (sin rutas).
        content_type: tipo MIME del artifact.
        size_bytes: tamaño del artifact en bytes.
        reference: referencia interna del artifact (no una URL pública).
        storage: backend donde vive el artifact (``local``, ``s3-compatible``).
    """

    artifact_id: str
    run_id: str
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    reference: str
    storage: str


class ArtifactListResponse(BaseModel):
    """Respuesta de ``GET /api/v1/runs/{run_id}/artifacts`` (solo lectura).

    Attributes:
        run_id: identificador de la ejecución consultada.
        artifacts: metadata de los artifacts publicados del run.
    """

    run_id: str
    artifacts: list[ArtifactResponse]


class CreateProjectRequest(BaseModel):
    """Cuerpo de ``POST /api/v1/projects``.

    Attributes:
        name: nombre del proyecto (obligatorio, no vacío).
        description: descripción opcional del proyecto.
    """

    name: str
    description: Optional[str] = None


class ProjectResponse(BaseModel):
    """Proyecto registrado (agrupación de runs).

    Attributes:
        project_id: identificador seguro del proyecto (slug).
        name: nombre legible del proyecto.
        description: descripción opcional (o ``null``).
        created_at: instante de creación en ISO 8601 UTC (o ``null``).
    """

    project_id: str
    name: str
    description: Optional[str] = None
    created_at: Optional[str] = None


class ProjectsResponse(BaseModel):
    """Respuesta de ``GET /api/v1/projects``.

    Attributes:
        projects: proyectos registrados.
        active_project_id: id del proyecto activo (o ``null``).
    """

    projects: list[ProjectResponse]
    active_project_id: Optional[str] = None


class SetActiveProjectRequest(BaseModel):
    """Cuerpo de ``POST /api/v1/projects/active``.

    Attributes:
        project_id: id del proyecto a activar, o ``null`` para "sin proyecto".
    """

    project_id: Optional[str] = None


class HealthResponse(BaseModel):
    """Respuesta de ``GET /api/v1/health``."""

    status: str


class WorkerJobResponse(BaseModel):
    """Job entregado por ``GET /api/v1/worker/jobs/next``.

    El claim es **atómico** en el servidor (``QUEUED → RUNNING`` reutilizando
    ``claim_job``), por lo que ``status`` refleja el estado persistido del job
    ya adquirido por este worker.
    """

    run_id: str
    topic: str
    offline: bool
    project_id: Optional[str] = None
    status: str


class WorkerCompleteRequest(BaseModel):
    """Cuerpo de ``POST /api/v1/worker/jobs/{run_id}/complete``.

    Attributes:
        status: estado terminal reportado (``SUCCESS``/``FAILED``/
            ``QUALITY_FAILED``).
        error: mensaje de error (solo en ``FAILED``).
    """

    status: str
    error: Optional[str] = None


class WorkerCompleteResponse(BaseModel):
    """Respuesta de ``complete`` con el estado final del job."""

    run_id: str
    status: str


class WorkerProgressRequest(BaseModel):
    """Cuerpo de ``POST /api/v1/worker/jobs/{run_id}/progress``.

    Attributes:
        stage: etapa en curso (``preparing``, ``content``, ``image``, ``audio``,
            ``manifest``, ``render``, ``quality``). No se persiste: el sistema
            la deriva de los artefactos del run.
    """

    stage: str


class WorkerHeartbeatResponse(BaseModel):
    """Respuesta de ``POST /api/v1/worker/jobs/{run_id}/heartbeat``."""

    run_id: str
    status: str
