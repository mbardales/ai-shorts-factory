"""Quality Gate de AI Shorts Factory.

Comprobaciones read-only que determinan si un run del pipeline produjo un
Short técnicamente válido. Este paquete **no modifica nada**: no escribe
archivos, no hace HTTP y no ejecuta comandos con ``shell``.

Estructura:

- ``models``: tipos de dominio (``QualityCheck``, ``QualityGateResult``).
- ``exceptions``: jerarquía de excepciones propia.
- ``gate``: implementación del Quality Gate y sus comprobaciones.

El núcleo reutiliza los validadores de dominio existentes
(``content.find_errors``, ``project`` serializer/validator) y añade
inspecciones nuevas (decode ligero de PNG con ``struct``/``zlib``, decode de
WAV con ``wave`` e inspección de MP4/MP3 con ``ffprobe`` usando el patrón
seguro de subprocess del repo: ``argv`` + ``shell=False``).
"""

from __future__ import annotations

from .exceptions import QualityError, QualityGateError, QualityProbeError
from .gate import QualityGate, run_quality_gate
from .models import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    QualityCheck,
    QualityGateResult,
)

__all__ = [
    "QualityCheck",
    "QualityGateResult",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "SEVERITY_INFO",
    "QualityError",
    "QualityGateError",
    "QualityProbeError",
    "QualityGate",
    "run_quality_gate",
]