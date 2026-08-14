"""Estructuras de resultado del pipeline de AI Shorts Factory.

Define los tipos de valor que representan el resultado de una ejecución
completa del pipeline:

- :class:`StageResult`: resultado de una etapa individual (contenido, imagen,
  audio, manifest, render).
- :class:`PipelineResult`: resultado agregado de una ejecución (run) completa.

Son estructuras mínimas y puras: ``dataclasses`` inmutables sin dependencias
externas. Las métricas avanzadas (checksums, snapshots de configuración,
estadísticas de proveedores, reintentos, telemetría, publicación) se añadirán
en iteraciones posteriores.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from quality.models import QualityGateResult


def _utc_now() -> datetime:
    """Devuelve el instante actual en UTC (aware)."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class StageResult:
    """Resultado de una etapa individual del pipeline.

    Attributes:
        name: nombre corto de la etapa (``content``, ``image``, ``audio``,
            ``manifest``, ``render``).
        success: si la etapa terminó correctamente.
        started_at: instante UTC de inicio de la etapa.
        finished_at: instante UTC de finalización de la etapa.
        error: mensaje de error si la etapa falló; ``None`` si tuvo éxito.
    """

    name: str
    success: bool
    started_at: datetime
    finished_at: datetime
    error: Optional[str] = None


@dataclass(frozen=True)
class PipelineResult:
    """Resultado agregado de una ejecución completa del pipeline.

    Attributes:
        run_id: identificador de la ejecución (``run-YYYYMMDD-HHMMSS``).
        success: si toda la ejecución terminó correctamente (incluye pasar el
            Quality Gate: un run con checks de severidad ``error`` fallidos
            cuenta como fallido aunque el render haya terminado bien).
        stages: resultados de las etapas ejecutadas, en orden. Si el pipeline
            se detuvo por un fallo, solo se incluyen las etapas hasta la
            fallida.
        project_path: ruta del ``project.json`` generado (si existe).
        video_path: ruta del video renderizado (si existe).
        quality_result: veredicto del Quality Gate sobre el run (si la etapa
            ``quality`` se ejecutó); ``None`` si no alcanzó a correr.
        error: mensaje de error de la ejecución (suele coincidir con el de la
            etapa fallida); ``None`` si todo fue bien.
    """

    run_id: str
    success: bool
    stages: tuple[StageResult, ...] = ()
    project_path: Optional[Path] = None
    video_path: Optional[Path] = None
    quality_result: Optional["QualityGateResult"] = None
    error: Optional[str] = None
