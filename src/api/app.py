"""Aplicación FastAPI de AI Shorts Factory (ME30.2).

Expone endpoints que **delegan exclusivamente** en
:class:`application.ApplicationService` (no se duplica ``PipelineRunner`` ni
``RunContext``):

- ``POST /api/v1/runs`` → crea y ejecuta un run (síncrono) → 202.
- ``GET /api/v1/runs`` → lista los runs (historial, solo lectura).
- ``GET /api/v1/runs/{run_id}`` → consulta el estado persistido.
- ``GET /api/v1/runs/{run_id}/video`` → sirve el video renderizado del run.
- ``GET /api/v1/health`` → solo verifica disponibilidad (no ejecuta pipeline).

La aplicación se construye con :func:`create_app`, que admite una raíz de
ejecuciones explícita (para pruebas) o usa el valor por defecto (``output/runs``
o ``PIPELINE_RUNS_ROOT``). ``app`` es la instancia por defecto para Uvicorn.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from application import ApplicationService
from application.models import CreateProjectRequest as AppCreateProjectRequest
from application.models import CreateRunRequest as AppCreateRunRequest

from . import errors as _errors
from .models import (
    CreateProjectRequest,
    CreateRunRequest,
    CreateRunResponse,
    HealthResponse,
    ProjectResponse,
    ProjectsResponse,
    RunStatusResponse,
    RunSummaryResponse,
    SetActiveProjectRequest,
)

logger = logging.getLogger(__name__)


def create_app(runs_root: Optional[Path] = None) -> FastAPI:
    """Construye la aplicación FastAPI con su servicio de aplicación.

    Args:
        runs_root: raíz de ejecuciones para el servicio (por defecto la del
            módulo ``application``).

    Returns:
        Aplicación FastAPI configurada.
    """
    service = ApplicationService(runs_root=runs_root)
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
        summary="Crea y ejecuta un run del pipeline",
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
        response_class=FileResponse,
        summary="Sirve el video renderizado de un run",
    )
    def get_run_video(run_id: str) -> FileResponse:
        video_path = service.get_run_video(run_id)
        return FileResponse(
            video_path,
            media_type="video/mp4",
            filename=video_path.name,
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

    return app


#: Instancia por defecto para ``uvicorn api.app:app``.
app = create_app()