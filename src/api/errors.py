"""Mapeo de errores HTTP de la capa HTTP de AI Shorts Factory (ME30.2).

Registra handlers que traducen las excepciones de :class:`ApplicationService`
a códigos HTTP, sin exponer stack traces ni secretos:

- ``ApplicationValidationError`` → 400 (petición/run_id inválidos).
- ``ApplicationRunNotFoundError`` → 404.
- ``ApplicationConflictError`` → 409 (transición de job no válida).
- ``WorkerUnauthorizedError`` → 401 (worker no autenticado).
- ``ApplicationError`` (y cualquier excepción inesperada) → 500 genérico.
- ``RequestValidationError`` (Pydantic/FastAPI) → 400.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from application.exceptions import (
    ApplicationConflictError,
    ApplicationError,
    ApplicationProjectNotFoundError,
    ApplicationRunNotFoundError,
    ApplicationValidationError,
    WorkerUnauthorizedError,
)

logger = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    """Conecta los handlers de error a la aplicación FastAPI."""

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_request: Request, exc: RequestValidationError):
        logger.warning("Petición inválida: %s", exc.errors())
        return JSONResponse(status_code=400, content={"detail": "Petición inválida."})

    @app.exception_handler(ApplicationValidationError)
    async def _application_validation(_request: Request, exc: ApplicationValidationError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ApplicationRunNotFoundError)
    async def _run_not_found(_request: Request, exc: ApplicationRunNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ApplicationProjectNotFoundError)
    async def _project_not_found(
        _request: Request, exc: ApplicationProjectNotFoundError
    ):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(WorkerUnauthorizedError)
    async def _worker_unauthorized(_request: Request, exc: WorkerUnauthorizedError):
        return JSONResponse(status_code=401, content={"detail": str(exc)})

    @app.exception_handler(ApplicationConflictError)
    async def _application_conflict(
        _request: Request, exc: ApplicationConflictError
    ):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ApplicationError)
    async def _application_error(_request: Request, exc: ApplicationError):
        logger.error("Error de aplicación: %s", exc)
        return JSONResponse(
            status_code=500, content={"detail": "Error interno de la aplicación."}
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(_request: Request, exc: Exception):
        logger.exception("Error no controlado en la API.")
        return JSONResponse(
            status_code=500, content={"detail": "Error interno del servidor."}
        )
