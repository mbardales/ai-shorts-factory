"""Implementaciones concretas de proveedores de IA de AI Shorts Factory.

- ``gemini``: :class:`GeminiProvider` (Google Gemini).
- ``synthetic``: :class:`SyntheticContentProvider` (contenido sintético local).
"""

from .gemini import GeminiProvider
from .synthetic import SyntheticContentProvider

__all__ = ["GeminiProvider", "SyntheticContentProvider"]
