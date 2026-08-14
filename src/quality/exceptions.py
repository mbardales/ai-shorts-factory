"""Jerarquía de excepciones del Quality Gate de AI Shorts Factory.

Todas las excepciones del paquete ``quality`` derivan de :class:`QualityError`
para que los consumidores puedan capturar errores del gate de forma homogénea
y, a la vez, con distinción por causa.

Jerarquía:

- ``QualityError``: base genérica.
  - ``QualityGateError``: error de configuración o ejecución del gate.
  - ``QualityProbeError``: fallo al inspeccionar un artefacto con ffprobe.
"""

from __future__ import annotations


class QualityError(Exception):
    """Error base de todas las excepciones del módulo ``quality``."""


class QualityGateError(QualityError):
    """Indica un error de configuración o ejecución del Quality Gate.

    Se usa para errores de programación (argumentos inválidos, directorio de
    salida inexistente) que impiden ejecutar el gate, no para marcar un run
    como inválido (eso se expresa mediante :class:`models.QualityCheck` con
    ``passed=False``).
    """


class QualityProbeError(QualityError):
    """No se pudo inspeccionar un artefacto con ffprobe.

    Indica que la herramienta no está disponible, el proceso falló por timeout
    o la salida no es interpretable. Es un fallo de la *inspección*, no del
    artefacto en sí: el gate convierte esta situación en un check fallido.
    """