"""Implementaciones de proveedores de imágenes.

- `gemini`: :class:`GeminiImageProvider` (Google Gemini / Imagen).
- `stability`: :class:`StabilityImageProvider` (Stability AI, Stable Image Core).
- `synthetic`: :class:`SyntheticImageProvider` (proveedor sintético local).
- `local-sd15`: :class:`LocalSD15ImageProvider` (Stable Diffusion 1.5 local).
"""

from .gemini import GeminiImageProvider
from .stability import StabilityImageProvider
from .synthetic import SyntheticImageProvider
from .local_sd15 import LocalSD15ImageProvider

__all__ = [
    "GeminiImageProvider",
    "StabilityImageProvider",
    "SyntheticImageProvider",
    "LocalSD15ImageProvider",
]