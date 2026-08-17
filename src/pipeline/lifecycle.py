"""Ciclo de vida de los runs de AI Shorts Factory (ME29.1).

Define el estado **persistido** de cada ejecución en un ``run.json`` atómico
dentro de ``runs_root/<run_id>/``:

- :class:`RunStatus`: estados posibles de un run.
- :class:`RunRecord`: metadatos persistidos (timestamps, error, veredicto del
  Quality Gate).
- :func:`write_run_record`: escritura atómica (``.tmp`` + ``os.replace``).
- :func:`load_run_record`: lectura defensiva (``None`` si falta/corrupto).
- :func:`list_runs`: listado seguro de runs bajo una raíz.
- :func:`find_cleanup_candidates`: detección de candidatos a retención/limpieza
  (**no borra nada**; el borrado real queda fuera de ME29.1).

Seguridad: toda operación valida el ``run_id``, rechaza symlinks/junctions y
garantiza que el directorio del run resuelva dentro de su ``runs_root``; nunca
se escribe ni se lee fuera de ``runs_root/<run_id>/``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

from .exceptions import PipelineValidationError
from .context import validate_run_id
from .models import _utc_now

logger = logging.getLogger(__name__)

#: Nombre del archivo de estado del run (dentro de ``runs_root/<run_id>/``).
RUN_FILE = "run.json"

#: Prefijo del archivo temporal usado para la escritura atómica.
_TMP_PREFIX = f".{RUN_FILE}."


class RunStatus(str, Enum):
    """Estados de ciclo de vida de un run."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    QUALITY_FAILED = "QUALITY_FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RunRecord:
    """Metadatos persistidos de un run.

    Attributes:
        run_id: identificador de la ejecución (``run-YYYYMMDD-HHMMSS``).
        status: estado del ciclo de vida.
        created_at: instante UTC de creación del run.
        queued_at: instante UTC en que el run quedó encolado (``QUEUED``).
        started_at: instante UTC en que el pipeline comenzó a ejecutarse.
        finished_at: instante UTC de finalización (o ``None`` si aún corre).
        error: mensaje de error de la ejecución (si falló).
        quality_passed: veredicto del Quality Gate (``True``/``False``) si la
            etapa ``quality`` se ejecutó; ``None`` en caso contrario.
    """

    run_id: str
    status: RunStatus
    created_at: Optional[datetime] = None
    queued_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    quality_passed: Optional[bool] = None

    def to_dict(self) -> dict:
        """Serializa el registro a un dict JSON-safe."""
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "created_at": _to_iso(self.created_at),
            "queued_at": _to_iso(self.queued_at),
            "started_at": _to_iso(self.started_at),
            "finished_at": _to_iso(self.finished_at),
            "error": self.error,
            "quality_passed": self.quality_passed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunRecord":
        """Reconstruye un registro desde un dict (lectura defensiva).

        Valores ausentes o corruptos se degradan sin lanzar: un ``status``
        desconocido se mapea a :attr:`RunStatus.UNKNOWN`.
        """
        try:
            status = RunStatus(str(data.get("status") or ""))
        except ValueError:
            status = RunStatus.UNKNOWN
        quality = data.get("quality_passed")
        if not isinstance(quality, bool):
            quality = None
        return cls(
            run_id=str(data.get("run_id") or ""),
            status=status,
            created_at=_from_iso(data.get("created_at")),
            queued_at=_from_iso(data.get("queued_at")),
            started_at=_from_iso(data.get("started_at")),
            finished_at=_from_iso(data.get("finished_at")),
            error=data.get("error") if isinstance(data.get("error"), str) else None,
            quality_passed=quality,
        )


# ---------------------------------------------------------------------------
# Seguridad de rutas
# ---------------------------------------------------------------------------


def checked_run_dir(run_dir: Path, *, runs_root: Optional[Path] = None) -> Path:
    """Valida y devuelve un directorio de run seguro.

    Rechaza ``run_id`` inválidos, symlinks/junctions y directorios que
    resuelvan fuera de ``runs_root`` (o del padre del directorio si
    ``runs_root`` no se indica).

    Args:
        run_dir: directorio del run (``runs_root/<run_id>``).
        runs_root: raíz global de ejecuciones esperada (opcional).

    Returns:
        El ``run_dir`` normalizado, listo para usar.

    Raises:
        PipelineValidationError: si el directorio no es un run seguro.
    """
    candidate = Path(run_dir)
    validate_run_id(candidate.name)
    root = Path(runs_root).resolve() if runs_root is not None else candidate.parent.resolve()
    if candidate.is_symlink():
        raise PipelineValidationError(
            [f"El directorio de run es un symlink/junction: {candidate}"]
        )
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise PipelineValidationError(
            [f"No se puede resolver el directorio de run: {candidate} ({exc})"]
        ) from exc
    if not resolved.is_relative_to(root):
        raise PipelineValidationError(
            [f"El directorio de run queda fuera de runs_root: {candidate}"]
        )
    return candidate


# ---------------------------------------------------------------------------
# Lectura / escritura
# ---------------------------------------------------------------------------


def write_run_record(run_dir: Path, record: RunRecord) -> Path:
    """Escribe el ``run.json`` del run de forma atómica.

    Escribe en ``run_dir/.run.json.tmp`` y lo renombra con ``os.replace``;
    ante un error elimina el temporal para no dejar residuos.

    Args:
        run_dir: directorio del run (validado).
        record: registro a persistir.

    Returns:
        Ruta del ``run.json`` escrito.

    Raises:
        PipelineValidationError: si el directorio no es seguro.
        OSError: si falla la escritura.
    """
    safe_dir = checked_run_dir(run_dir)
    target = safe_dir / RUN_FILE
    tmp = safe_dir / f"{_TMP_PREFIX}{RUN_FILE}"
    payload = json.dumps(record.to_dict(), ensure_ascii=False, indent=2)
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    logger.debug("run.json escrito en: %s (status=%s)", target, record.status.value)
    return target


def load_run_record(run_dir: Path) -> Optional[RunRecord]:
    """Lee el ``run.json`` de un run de forma defensiva.

    Devuelve ``None`` si el directorio no es seguro, si ``run.json`` no existe
    o si su contenido no es un JSON válido/legible (el llamador decide cómo
    degradar, p. ej. a :func:`unknown_record`).

    Args:
        run_dir: directorio del run.

    Returns:
        El registro persistido, o ``None`` si no hay estado legible.
    """
    try:
        safe_dir = checked_run_dir(run_dir)
    except PipelineValidationError:
        return None
    path = safe_dir / RUN_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return RunRecord.from_dict(data)
    except (OSError, ValueError, TypeError):
        logger.warning("run.json ilegible o corrupto en: %s", safe_dir)
        return None


def unknown_record(run_dir: Path) -> RunRecord:
    """Registro UNKNOWN para un run sin estado legible."""
    return RunRecord(run_id=Path(run_dir).name, status=RunStatus.UNKNOWN)


# ---------------------------------------------------------------------------
# Listado y candidatos (sin borrado)
# ---------------------------------------------------------------------------


def list_runs(runs_root: Optional[Path] = None) -> list[RunRecord]:
    """Lista los runs bajo ``runs_root`` de forma segura.

    Recorre un solo nivel (hijos directos de ``runs_root``), ignora entradas
    que no sean runs válidos (nombres fuera del patrón, symlinks, ficheros) y
    degrada a UNKNOWN los ``run.json`` ausentes/corruptos.

    Args:
        runs_root: raíz de ejecuciones; por defecto la del módulo ``context``.

    Returns:
        Registros de los runs, ordenados por nombre (cronológico).
    """
    if runs_root is None:
        from .context import RUNS_ROOT

        runs_root = RUNS_ROOT
    root = Path(runs_root)
    if not root.is_dir():
        return []
    records: list[RunRecord] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            checked_run_dir(entry, runs_root=root)
        except PipelineValidationError:
            continue
        record = load_run_record(entry) or unknown_record(entry)
        records.append(record)
    return records


def find_cleanup_candidates(
    runs_root: Optional[Path] = None,
    *,
    older_than: Optional[timedelta] = None,
    statuses: Optional[Iterable[RunStatus]] = None,
) -> list[RunRecord]:
    """Detecta candidatos a retención/limpieza.

    **No borra nada**: solo lista los runs que cumplen los filtros de estado y
    antigüedad. Base para la futura política de retención de ME29.2+.

    Args:
        runs_root: raíz de ejecuciones (ver :func:`list_runs`).
        older_than: conservar solo runs cuyo último instante de actividad sea
            anterior a ``now - older_than``.
        statuses: estados a considerar; por defecto todos.

    Returns:
        Registros candidatos en orden cronológico.
    """
    wanted = set(statuses) if statuses is not None else None
    cutoff = _utc_now() - older_than if older_than is not None else None
    candidates: list[RunRecord] = []
    for record in list_runs(runs_root):
        if wanted is not None and record.status not in wanted:
            continue
        if cutoff is not None:
            reference = record.finished_at or record.started_at or record.created_at
            if reference is None or reference > cutoff:
                continue
        candidates.append(record)
    return candidates


# ---------------------------------------------------------------------------
# Serialización de timestamps
# ---------------------------------------------------------------------------


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    """Convierte un datetime a ISO 8601 (o ``None``)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _from_iso(value: object) -> Optional[datetime]:
    """Convierte ISO 8601 a datetime aware (o ``None`` si es inválido)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
