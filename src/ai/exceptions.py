"""Jerarquía de excepciones del AI Core de AI Shorts Factory.

Todas las excepciones del paquete ``ai`` derivan de ``AIError`` para que los
consumidores puedan capturar errores de IA de forma homogénea y, a la vez,
con distinción por causa.

Jerarquía:

- ``AIError``: base genérica.
  - ``ConfigurationError``: configuración inválida o incompleta.
  - ``ProviderError``: error de un proveedor de IA.
    - ``APIError``: error devuelto por la API del proveedor.
    - ``AuthenticationError``: clave inválida o sin permisos.
    - ``RateLimitError``: se superó la cuota o el límite de solicitudes.
    - ``ContentBlockedError``: el modelo bloqueó el contenido solicitado.
"""

from __future__ import annotations

from typing import Optional


class AIError(Exception):
    """Error base de todas las excepciones del módulo ``ai``."""


class ConfigurationError(AIError):
    """Configuración inválida o incompleta (claves, modelos, dependencias)."""


class ProviderError(AIError):
    """Error general ocurrido en un proveedor de IA."""


class _ProviderHTTPError(ProviderError):
    """Base para errores del proveedor que incluyen contexto HTTP.

    Args:
        message: descripción legible del error.
        code: código HTTP devuelto por la API (si está disponible).
        status: estado textual de la API (si está disponible).
    """

    def __init__(
        self,
        message: str,
        *,
        code: Optional[int] = None,
        status: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


class APIError(_ProviderHTTPError):
    """Error devuelto por la API del proveedor de IA."""


class AuthenticationError(_ProviderHTTPError):
    """Error de autenticación: clave inválida, revocada o sin permisos."""


class RateLimitError(_ProviderHTTPError):
    """Se superó la cuota o el límite de solicitudes del proveedor."""


class ContentBlockedError(ProviderError):
    """El modelo bloqueó el contenido (políticas de seguridad)."""
