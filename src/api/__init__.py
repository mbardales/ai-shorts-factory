"""Capa HTTP de AI Shorts Factory (ME30.2).

API HTTP mínima sobre :class:`application.ApplicationService`: crear y
consultar runs, más un endpoint de salud. Sin background tasks, autenticación,
base de datos ni orquestación asíncrona en esta versión.

Transporte: FastAPI + Uvicorn.
"""

from __future__ import annotations

from .app import create_app
from .models import (
    CreateRunRequest,
    CreateRunResponse,
    HealthResponse,
    RunStatusResponse,
)

__all__ = [
    "create_app",
    "CreateRunRequest",
    "CreateRunResponse",
    "HealthResponse",
    "RunStatusResponse",
]
