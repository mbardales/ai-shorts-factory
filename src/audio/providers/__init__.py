"""Implementaciones de proveedores de audio.

- ``google``: :class:`GoogleTTSProvider` (Google Gemini TTS).
- ``synthetic``: :class:`SyntheticAudioProvider` (WAV sintético offline).
- ``kokoro``: :class:`KokoroAudioProvider` (TTS local Kokoro-82M).
"""

from .google import GoogleTTSProvider
from .synthetic import SyntheticAudioProvider
from .kokoro import KokoroAudioProvider

__all__ = [
    "GoogleTTSProvider",
    "SyntheticAudioProvider",
    "KokoroAudioProvider",
]