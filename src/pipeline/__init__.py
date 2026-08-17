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
from .lifecycle import (
    RUN_FILE,
    RunRecord,
    RunStatus,
    checked_run_dir,
    find_cleanup_candidates,
    list_runs,
    load_run_record,
    unknown_record,
    write_run_record,
)
from .models import PipelineResult, StageResult
from .runner import STAGE_ORDER, PipelineRunner
from .queue import (
    JOB_FILE,
    LOCK_FILE,
    JobPayload,
    claim_job,
    execute_job,
    find_orphan_jobs,
    find_queued_jobs,
    load_job_payload,
    process_one,
    process_queued_job,
    release_job,
    write_job_payload,
)

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
    "RUN_FILE",
    "RunRecord",
    "RunStatus",
    "checked_run_dir",
    "find_cleanup_candidates",
    "list_runs",
    "load_run_record",
    "unknown_record",
    "write_run_record",
    "JOB_FILE",
    "LOCK_FILE",
    "JobPayload",
    "claim_job",
    "execute_job",
    "find_orphan_jobs",
    "find_queued_jobs",
    "load_job_payload",
    "process_one",
    "process_queued_job",
    "release_job",
    "write_job_payload",
]
