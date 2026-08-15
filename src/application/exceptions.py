"""Jerarquía de excepciones de la capa Application de AI Shorts Factory.

Todas las excepciones del paquete ``application`` derivan de
:class:`ApplicationError` para que los consumidores (p. ej. una futura API
HTTP) las capturen de forma homogénea y a la vez distingan por causa.
"""

from __future__ import annotations


class ApplicationError(Exception):
    """Error base de la capa Application."""


class ApplicationValidationError(ApplicationError):
    """Entrada inválida (p. ej. tema vacío o ``run_id`` mal formado)."""


class ApplicationRunNotFoundError(ApplicationError):
    """No existe un run con el ``run_id`` solicitado."""


class ApplicationProjectNotFoundError(ApplicationError):
    """No existe un proyecto con el ``project_id`` solicitado."""
