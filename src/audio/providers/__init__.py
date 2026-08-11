"""Implementaciones de proveedores de audio.

- ``google``: :class:`GoogleTTSProvider` (Google Gemini TTS).
- ``synthetic``: :class:`SyntheticAudioProvider` (WAV sintético offline).
"""

from .google import GoogleTTSProvider
from .synthetic import SyntheticAudioProvider

__all__ = ["GoogleTTSProvider", "SyntheticAudioProvider"]
