"""Modelo de dominio "Content Package" de AI Shorts Factory.

Representa el ciclo de vida completo de un YouTube Short, desde la idea hasta
su publicación. Es un modelo **puro** de dominio:

- Usa exclusivamente ``dataclasses`` y tipos de la biblioteca estándar.
- **No** contiene lógica de IA ni depende de ningún proveedor (p. ej. Gemini).
- **No** depende de infraestructura (p. ej. n8n) ni de una plataforma concreta.

Estructura:

- ``models``: tipos de dominio (``ContentPackage`` y sus diez objetos de valor).
- ``schema``: serialización a JSON y desde JSON (función pura, sin dependencias).
- ``validator``: validaciones y transiciones de etapa del ciclo de vida.
"""

from .models import (
    Analytics,
    Assets,
    ContentPackage,
    ContentStage,
    Identity,
    Narration,
    Publication,
    Research,
    SEO,
    Scene,
    Script,
    Status,
    Visuals,
)
from .validator import (
    ContentValidationError,
    coerce_content_package,
    find_errors,
    transition,
    validate_content_package,
)
from .schema import (
    CONTENT_PACKAGE_SCHEMA,
    content_package_from_dict,
    content_package_from_json,
    content_package_to_dict,
    content_package_to_json,
)

__all__ = [
    "ContentPackage",
    "ContentStage",
    "Identity",
    "Research",
    "SEO",
    "Script",
    "Scene",
    "Visuals",
    "Narration",
    "Assets",
    "Publication",
    "Analytics",
    "Status",
    "ContentValidationError",
    "coerce_content_package",
    "find_errors",
    "validate_content_package",
    "transition",
    "content_package_from_dict",
    "content_package_to_dict",
    "content_package_from_json",
    "content_package_to_json",
    "CONTENT_PACKAGE_SCHEMA",
]
