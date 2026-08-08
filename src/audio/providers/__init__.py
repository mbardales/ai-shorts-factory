"""Implementaciones de proveedores de audio.

- ``google``: :class:`GoogleTTSProvider` (Google Gemini TTS).
"""

from .google import GoogleTTSProvider

__all__ = ["GoogleTTSProvider"]
