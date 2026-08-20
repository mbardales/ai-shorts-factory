"""Aplicación FastAPI de AI Shorts Factory (ME30.2).

Expone endpoints que **delegan exclusivamente** en
:class:`application.ApplicationService` (no se duplica ``PipelineRunner`` ni
``RunContext``):

- ``POST /api/v1/runs`` → crea un run y lo deja ``QUEUED`` (HTTP 202 inmediato;
  el pipeline lo ejecuta un worker local de forma asíncrona).
- ``GET /api/v1/runs`` → lista los runs (historial, solo lectura).
- ``GET /api/v1/runs/{run_id}`` → consulta el estado persistido.
- ``GET /api/v1/runs/{run_id}/video`` → sirve el video renderizado del run
  (a través de :class:`application.storage.RunStorage`, sin exponer rutas del
  filesystem).
- ``GET /api/v1/runs/{run_id}/artifacts`` → lista la metadata de los artifacts
  publicados del run (a través de :class:`application.ArtifactAccess`, solo
  lectura; sin URLs públicas ni rutas absolutas).
- ``GET /api/v1/health`` → solo verifica disponibilidad (no ejecuta pipeline).
- ``GET /api/v1/worker/jobs/next`` → entrega el siguiente job al worker
  (claim atómico ``QUEUED → RUNNING``; requiere ``Authorization: Bearer``).
- ``POST /api/v1/worker/jobs/{run_id}/complete`` → resultado del job.
- ``POST /api/v1/worker/jobs/{run_id}/progress`` → avance de etapa (advisory).
- ``POST /api/v1/worker/jobs/{run_id}/heartbeat`` → vida del worker.

La aplicación se construye con :func:`create_app`, que admite una raíz de
ejecuciones explícita (para pruebas) o usa el valor por defecto (``output/runs``
o ``PIPELINE_RUNS_ROOT``). ``app`` es la instancia por defecto para Uvicorn.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response

from pipeline.context import RUNS_ROOT

from application import ApplicationService, WorkerService
from application.artifact_access import ArtifactAccess
from application.artifact_store_factory import build_artifact_store
from application.exceptions import ApplicationVideoNotFoundError, WorkerUnauthorizedError
from application.models import CreateProjectRequest as AppCreateProjectRequest
from application.models import CreateRunRequest as AppCreateRunRequest

from . import errors as _errors
from .models import (
    ArtifactListResponse,
    ArtifactResponse,
    CreateProjectRequest,
    CreateRunRequest,
    CreateRunResponse,
    HealthResponse,
    ProjectResponse,
    ProjectsResponse,
    RunStatusResponse,
    RunSummaryResponse,
    SetActiveProjectRequest,
    WorkerCompleteRequest,
    WorkerCompleteResponse,
    WorkerHeartbeatResponse,
    WorkerJobResponse,
    WorkerProgressRequest,
)

logger = logging.getLogger(__name__)

#: Expiración de la URL temporal del video (segundos). Controlada por el
#: servidor: el cliente nunca la elige (ME40.9D.3.4).
VIDEO_URL_EXPIRES_SECONDS = 300


def create_app(
    runs_root: Optional[Path] = None,
    artifact_access: Optional[ArtifactAccess] = None,
) -> FastAPI:
    """Construye la aplicación FastAPI con su servicio de aplicación.

    Args:
        runs_root: raíz de ejecuciones para el servicio (por defecto la del
            módulo ``application``).
        artifact_access: acceso a artifacts publicado por la API (por defecto
            se construye uno sobre ``runs_root`` vía ``build_artifact_store``).

    Returns:
        Aplicación FastAPI configurada.
    """
    if artifact_access is None:
        artifact_access = _build_artifact_access(runs_root)
    service = ApplicationService(
        runs_root=runs_root,
        artifact_access=artifact_access,
    )
    worker_service = WorkerService(runs_root=runs_root)
    app = FastAPI(title="AI Shorts Factory API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _errors.register_error_handlers(app)

    @app.post(
        "/api/v1/runs",
        status_code=202,
        response_model=CreateRunResponse,
        summary="Crea un run y lo encola (HTTP 202)",
    )
    def create_run(payload: CreateRunRequest) -> CreateRunResponse:
        result = service.create_run(
            AppCreateRunRequest(
                topic=payload.topic,
                run_id=payload.run_id,
                project_id=payload.project_id,
                offline=payload.offline,
            )
        )
        logger.info("HTTP POST /api/v1/runs -> run_id=%s status=%s",
                    result.run_id, result.status)
        return CreateRunResponse(
            run_id=result.run_id, status=result.status, project_id=result.project_id
        )

    @app.get(
        "/api/v1/runs",
        response_model=List[RunSummaryResponse],
        summary="Lista los runs generados (historial, solo lectura)",
    )
    def list_runs(project_id: Optional[str] = None) -> List[RunSummaryResponse]:
        summaries = service.list_runs(project_id=project_id)
        return [
            RunSummaryResponse(
                run_id=summary.run_id,
                status=summary.status,
                created_at=summary.created_at,
                duration_seconds=summary.duration_seconds,
                video_url=summary.video_url,
                error=summary.error,
                project_id=summary.project_id,
            )
            for summary in summaries
        ]

    @app.get(
        "/api/v1/runs/{run_id}",
        response_model=RunStatusResponse,
        summary="Consulta el estado de un run",
    )
    def get_run(run_id: str) -> RunStatusResponse:
        result = service.get_run(run_id)
        return RunStatusResponse(
            run_id=result.run_id,
            status=result.status,
            success=result.success,
            video_path=result.video_path,
            error=result.error,
            stage=result.stage,
            project_id=result.project_id,
        )

    @app.get(
        "/api/v1/runs/{run_id}/video",
        response_class=RedirectResponse,
        summary="Redirige (307) a la URL temporal firmada del video del run",
    )
    def get_run_video(run_id: str) -> RedirectResponse:
        url = service.get_video_temporary_url(run_id, VIDEO_URL_EXPIRES_SECONDS)
        if url is None:
            raise ApplicationVideoNotFoundError(
                f"No hay video publicada para el run: {run_id}"
            )
        logger.info("HTTP GET /api/v1/runs/%s/video -> 307", run_id)
        return RedirectResponse(url, status_code=307)

    @app.get(
        "/api/v1/runs/{run_id}/artifacts",
        response_model=ArtifactListResponse,
        summary="Lista los artifacts publicados de un run (solo metadata)",
    )
    def list_run_artifacts(run_id: str) -> ArtifactListResponse:
        result = service.list_artifacts(run_id)
        logger.info(
            "HTTP GET /api/v1/runs/%s/artifacts -> %d artifacts",
            run_id, len(result.artifacts),
        )
        return ArtifactListResponse(
            run_id=result.run_id,
            artifacts=[
                ArtifactResponse(
                    artifact_id=item.artifact_id,
                    run_id=item.run_id,
                    kind=item.kind,
                    filename=item.filename,
                    content_type=item.content_type,
                    size_bytes=item.size_bytes,
                    reference=item.reference,
                    storage=item.storage,
                )
                for item in result.artifacts
            ],
        )

    @app.post(
        "/api/v1/projects",
        status_code=201,
        response_model=ProjectResponse,
        summary="Crea un proyecto y lo deja como activo",
    )
    def create_project(payload: CreateProjectRequest) -> ProjectResponse:
        project = service.create_project(
            AppCreateProjectRequest(
                name=payload.name,
                description=payload.description,
            )
        )
        logger.info("HTTP POST /api/v1/projects -> project_id=%s",
                    project.project_id)
        return ProjectResponse(
            project_id=project.project_id,
            name=project.name,
            description=project.description,
            created_at=project.created_at,
        )

    @app.get(
        "/api/v1/projects",
        response_model=ProjectsResponse,
        summary="Lista los proyectos y el proyecto activo",
    )
    def list_projects() -> ProjectsResponse:
        overview = service.list_projects()
        return ProjectsResponse(
            projects=[
                ProjectResponse(
                    project_id=project.project_id,
                    name=project.name,
                    description=project.description,
                    created_at=project.created_at,
                )
                for project in overview.projects
            ],
            active_project_id=overview.active_project_id,
        )

    @app.post(
        "/api/v1/projects/active",
        response_model=ProjectResponse,
        summary="Establece (o limpia) el proyecto activo",
    )
    def set_active_project(payload: SetActiveProjectRequest) -> ProjectResponse:
        project = service.set_active_project(payload.project_id)
        logger.info("HTTP POST /api/v1/projects/active -> project_id=%s",
                    payload.project_id)
        return ProjectResponse(
            project_id=project.project_id,
            name=project.name,
            description=project.description,
            created_at=project.created_at,
        )

    @app.get(
        "/api/v1/health",
        response_model=HealthResponse,
        summary="Health check (no ejecuta el pipeline)",
    )
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    def _require_worker_auth(authorization: Optional[str]) -> None:
        """Rechaza (401) peticiones worker sin token Bearer válido."""
        token = None
        if authorization and authorization.startswith("Bearer "):
            token = authorization[len("Bearer "):].strip()
        if token is None or not worker_service.check_token(token):
            raise WorkerUnauthorizedError("Worker no autenticado.")

    @app.get(
        "/api/v1/worker/jobs/next",
        response_model=Optional[WorkerJobResponse],
        summary="Entrega el siguiente job al worker (claim atómico)",
        responses={204: {"description": "No hay jobs en cola"}},
    )
    def worker_jobs_next(
        worker_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> object:
        _require_worker_auth(authorization)
        job = worker_service.next_job(worker_id)
        if job is None:
            return Response(status_code=204)
        logger.info("HTTP GET /api/v1/worker/jobs/next -> job=%s", job.get("run_id"))
        return WorkerJobResponse(**job)

    @app.post(
        "/api/v1/worker/jobs/{run_id}/complete",
        response_model=WorkerCompleteResponse,
        summary="Informa el resultado del job al API",
    )
    def worker_complete(
        run_id: str,
        payload: WorkerCompleteRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> WorkerCompleteResponse:
        _require_worker_auth(authorization)
        result = worker_service.complete_job(
            run_id, payload.status, error=payload.error
        )
        logger.info(
            "HTTP POST complete -> run_id=%s status=%s", run_id, result["status"]
        )
        return WorkerCompleteResponse(
            run_id=result["run_id"], status=result["status"]
        )

    @app.post(
        "/api/v1/worker/jobs/{run_id}/progress",
        summary="Reporta el avance de etapa del job",
    )
    def worker_progress(
        run_id: str,
        payload: WorkerProgressRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> Response:
        _require_worker_auth(authorization)
        worker_service.report_progress(run_id, payload.stage)
        return Response(status_code=200)

    @app.post(
        "/api/v1/worker/jobs/{run_id}/heartbeat",
        response_model=WorkerHeartbeatResponse,
        summary="Confirma que el worker sigue vivo",
    )
    def worker_heartbeat(
        run_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> WorkerHeartbeatResponse:
        _require_worker_auth(authorization)
        status = worker_service.heartbeat(run_id)
        return WorkerHeartbeatResponse(run_id=run_id, status=status)

    return app


def _build_artifact_access(runs_root: Optional[Path]) -> ArtifactAccess:
    """Construye el :class:`ArtifactAccess` de la API (fail-fast).

    Resuelve la raíz de ejecuciones efectiva (explícita o el valor por defecto
    de ``pipeline.context``) y delega en :func:`build_artifact_store` (backend
    ``local`` salvo configuración explícita). Una configuración de backend
    inválida (p. ej. ``s3`` sin boto3, sin credenciales o sin endpoint) hace
    que ``create_app()`` falle al arrancar: los fallos reales de producción
    no se ocultan silenciosamente.
    """
    artifact_root = runs_root if runs_root is not None else RUNS_ROOT
    return ArtifactAccess(build_artifact_store(artifact_root))


#: Instancia por defecto para ``uvicorn api.app:app``.
app = create_app()