"""Jerarquía de excepciones del módulo ``pipeline`` de AI Shorts Factory.

Todas las excepciones del paquete ``pipeline`` derivan de :class:`PipelineError`
para que los consumidores puedan capturar errores de orquestación de forma
homogénea y, a la vez, con distinción por causa.

Jerarquía:

- ``PipelineError``: base genérica.
  - ``PipelineValidationError``: dato inválido (identificador de ejecución,
    ruta de resolución, estructura del directorio de ejecución).
  - ``PipelineNotFoundError``: no existe un directorio de ejecución esperado.
"""

from __future__ import annotations

from typing import Iterable


class PipelineError(Exception):
    """Error base de todas las excepciones del módulo ``pipeline``."""


class PipelineValidationError(PipelineError):
    """Indica un valor inválido en el contexto de ejecución.

    Attributes:
        errors: lista de mensajes de error detectados.
    """

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors: list[str] = list(errors)
        super().__init__("; ".join(self.errors) or "Contexto de ejecución inválido.")


class PipelineNotFoundError(PipelineError):
    """No existe un directorio de ejecución esperado (ej. el directorio ``run``)."""