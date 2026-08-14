"""Modelos de dominio del Quality Gate de AI Shorts Factory.

Representan el resultado de inspeccionar, de forma estrictamente read-only,
el directorio de salida de un run: una lista de comprobaciones
(:class:`QualityCheck`) agregadas en un veredicto global
(:class:`QualityGateResult`).

Severidades:

- ``"error"``: el run no es técnicamente válido (marca ``passed=False``).
- ``"warning"``: desviación no crítica que conviene revisar.
- ``"info"``: comprobación satisfecha o información de contexto.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Severidad: el run no pasa el gate.
SEVERITY_ERROR = "error"
#: Severidad: desviación no crítica.
SEVERITY_WARNING = "warning"
#: Severidad: check satisfecho / información de contexto.
SEVERITY_INFO = "info"

#: Valores de severidad admitidos.
_SEVERITIES = frozenset({SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO})


@dataclass(frozen=True)
class QualityCheck:
    """Resultado de una comprobación individual del Quality Gate.

    Attributes:
        name: identificador corto de la comprobación.
        passed: ``True`` si la comprobación se cumplió.
        severity: severidad (``error`` | ``warning`` | ``info``).
        message: mensaje explicativo del resultado.
    """

    name: str
    passed: bool
    severity: str
    message: str

    def __post_init__(self) -> None:
        if self.severity not in _SEVERITIES:
            raise ValueError(
                f"Severidad inválida: {self.severity!r}; se esperaba una de "
                f"{sorted(_SEVERITIES)}."
            )


@dataclass(frozen=True)
class QualityGateResult:
    """Veredicto global del Quality Gate sobre un run.

    Attributes:
        passed: ``True`` si no hay checks con severidad ``error`` fallidos.
        checks: comprobaciones ejecutadas, en orden.
        errors: cantidad de checks fallidos con severidad ``error``.
        warnings: cantidad de checks con severidad ``warning``.
    """

    passed: bool
    checks: tuple[QualityCheck, ...]
    errors: int
    warnings: int

    @property
    def failed_checks(self) -> tuple[QualityCheck, ...]:
        """Checks con severidad ``error`` que no pasaron."""
        return tuple(
            check
            for check in self.checks
            if check.severity == SEVERITY_ERROR and not check.passed
        )

    @property
    def warning_checks(self) -> tuple[QualityCheck, ...]:
        """Checks con severidad ``warning``."""
        return tuple(
            check for check in self.checks if check.severity == SEVERITY_WARNING
        )