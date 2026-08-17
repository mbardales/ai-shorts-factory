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
    ApplicationConflictError,
    ApplicationError,
    ApplicationProjectNotFoundError,
    ApplicationRunNotFoundError,
    ApplicationValidationError,
    WorkerUnauthorizedError,
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
from .postgres_repository import (
    DependencyRequiredError,
    PostgreSQLPersistenceRepository,
)
from .repository import LocalPersistenceRepository, PersistenceRepository
from .service import ApplicationService
from .storage import LocalRunStorage, RunStorage, StorageError
from .worker import WorkerService

__all__ = [
    "ApplicationConflictError",
    "ApplicationError",
    "ApplicationProjectNotFoundError",
    "ApplicationRunNotFoundError",
    "ApplicationValidationError",
    "WorkerUnauthorizedError",
    "CreateProjectRequest",
    "CreateRunRequest",
    "CreateRunResponse",
    "DependencyRequiredError",
    "PostgreSQLPersistenceRepository",
    "ProjectRecord",
    "ProjectsOverview",
    "RunStatusResponse",
    "RunSummary",
    "ApplicationService",
    "LocalPersistenceRepository",
    "LocalRunStorage",
    "PersistenceRepository",
    "RunStorage",
    "StorageError",
    "WorkerService",
]
