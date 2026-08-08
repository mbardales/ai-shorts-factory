"""Jerarquía de excepciones del Renderer de AI Shorts Factory.

Todas las excepciones del paquete ``renderer`` derivan de :class:`RendererError`
para que los consumidores puedan capturar errores de renderizado de forma
homogénea y, a la vez, con distinción por causa.

Jerarquía:

- ``RendererError``: base genérica.
  - ``RendererValidationError``: la solicitud no supera las validaciones.
  - ``RendererExecutionError``: error al ejecutar el renderizador o al
    comunicarse con él.
"""

from __future__ import annotations


class RendererError(Exception):
    """Error base de todas las excepciones del módulo ``renderer``."""


class RendererValidationError(RendererError):
    """La solicitud de renderizado no supera las validaciones del dominio."""


class RendererExecutionError(RendererError):
    """Error ocurrido al ejecutar el renderizador o al comunicarse con él."""
