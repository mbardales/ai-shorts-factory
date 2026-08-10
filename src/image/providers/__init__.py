"""Implementaciones de proveedores de imágenes.

- `gemini`: :class:`GeminiImageProvider` (Google Gemini / Imagen).
- `stability`: :class:`StabilityImageProvider` (Stability AI, Stable Image Core).
- `synthetic`: :class:`SyntheticImageProvider` (proveedor sintético local).
"""

from .gemini import GeminiImageProvider
from .stability import StabilityImageProvider
from .synthetic import SyntheticImageProvider

__all__ = [
    "GeminiImageProvider",
    "StabilityImageProvider",
    "SyntheticImageProvider",
]
