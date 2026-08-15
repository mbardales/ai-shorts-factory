"""Capa Application de AI Shorts Factory (ME30.1).

API interna de aplicación sobre :class:`PipelineRunner` (sin HTTP todavía):
crear runs y consultar su estado persistido.

Uso:

    from application import ApplicationService, CreateRunRequest

    service = ApplicationService()
    response = service.create_run(CreateRunRequest(topic="Un eclipse solar", offline=True))
    status = service.get_run(response.run_id)
"""

from __future__ import annotations

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
from .service import ApplicationService

__all__ = [
    "ApplicationError",
    "ApplicationProjectNotFoundError",
    "ApplicationRunNotFoundError",
    "ApplicationValidationError",
    "CreateProjectRequest",
    "CreateRunRequest",
    "CreateRunResponse",
    "ProjectRecord",
    "ProjectsOverview",
    "RunStatusResponse",
    "RunSummary",
    "ApplicationService",
]
