"""Orquestación del pipeline de AI Shorts Factory.

Paquete **pipeline**: define el contexto de ejecución (``RunContext``),
los directorios de ejecución (``RunDirectory``), la generación de
identificadores de ejecución (``run_id``) y el orquestador end-to-end
(``PipelineRunner``). Es el núcleo sobre el que se construye el flujo
contenido → imagen → audio → manifest → render y no depende de proveedores
de IA en su núcleo.
"""

from __future__ import annotations

from .context import (
    PROJECT_ROOT,
    RUN_ID_PATTERN,
    RUNS_ROOT,
    RunContext,
    RunDirectory,
    generate_run_id,
    is_valid_run_id,
    validate_run_id,
)
from .exceptions import (
    PipelineError,
    PipelineNotFoundError,
    PipelineValidationError,
)
from .models import PipelineResult, StageResult
from .runner import STAGE_ORDER, PipelineRunner

__all__ = [
    "PROJECT_ROOT",
    "RUN_ID_PATTERN",
    "RUNS_ROOT",
    "RunContext",
    "RunDirectory",
    "generate_run_id",
    "is_valid_run_id",
    "validate_run_id",
    "PipelineError",
    "PipelineNotFoundError",
    "PipelineValidationError",
    "PipelineResult",
    "StageResult",
    "PipelineRunner",
    "STAGE_ORDER",
]
