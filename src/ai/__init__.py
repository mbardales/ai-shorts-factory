"""Núcleo de IA (AI Core) de AI Shorts Factory.

Paquete que abstrae el acceso a proveedores de modelos de lenguaje mediante
una interfaz base, una jerarquía de excepciones propia y un adaptador
desacoplado del proveedor concreto.

Estructura:

- ``ai.base``: contratos base (``BaseAIProvider``) y tipos de datos
  (``GenerationResult``, ``GenerationOptions``).
- ``ai.exceptions``: jerarquía de excepciones personalizadas.
- ``ai.adapter``: ``AIAdapter``, orquestador desacoplado del proveedor.
- ``ai.providers``: implementaciones de proveedores (``gemini``).

Uso típico:

    from ai import AIAdapter
    from ai.providers import GeminiProvider

    adapter = AIAdapter(GeminiProvider(model="gemini-3.6-flash"))
    texto = adapter.generate_text("Hola")
"""

from .base import BaseAIProvider, GenerationOptions, GenerationResult
from .exceptions import (
    AIError,
    APIError,
    AuthenticationError,
    ConfigurationError,
    ContentBlockedError,
    ProviderError,
    RateLimitError,
)
from .adapter import AIAdapter, ProviderSettings
from .providers import GeminiProvider

__all__ = [
    "AIAdapter",
    "ProviderSettings",
    "BaseAIProvider",
    "GenerationOptions",
    "GenerationResult",
    "GeminiProvider",
    "AIError",
    "APIError",
    "AuthenticationError",
    "ConfigurationError",
    "ContentBlockedError",
    "ProviderError",
    "RateLimitError",
]
