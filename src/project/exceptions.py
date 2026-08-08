"""Jerarquía de excepciones del Project Manifest de AI Shorts Factory.

Todas las excepciones del paquete ``project`` derivan de :class:`ProjectError`
para que los consumidores puedan capturar errores del manifest de forma
homogénea y, a la vez, con distinción por causa.

Jerarquía:

- ``ProjectError``: base genérica.
  - ``ProjectValidationError``: el manifest no cumple las reglas del dominio.
  - ``ProjectNotFoundError``: falta un insumo obligatorio del proyecto
    (por ejemplo, el ``output/content.json`` de origen).
"""

from __future__ import annotations

from typing import Iterable


class ProjectError(Exception):
    """Error base de todas las excepciones del módulo ``project``."""


class ProjectValidationError(ProjectError):
    """Indica que un :class:`ProjectManifest` no cumple las reglas del dominio.

    Attributes:
        errors: lista de mensajes de error detectados.
    """

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors: list[str] = list(errors)
        super().__init__("; ".join(self.errors) or "Manifest de proyecto inválido.")


class ProjectNotFoundError(ProjectError):
    """Falta un archivo o directorio obligatorio del proyecto."""
