"""Audio Core de AI Shorts Factory.

Arquitectura base para la generación de audio (TTS), reutilizando Media Core y
siguiendo el mismo patrón que ``ai``, ``media`` e ``image``:

- ``base``: contrato ``AudioProvider`` y tipos ``AudioRequest``, ``AudioResult``,
  ``VoiceSettings`` y ``AudioFormat``.
- ``models``: narración y solicitudes de síntesis derivadas del ContentPackage.
- ``adapter``: ``AudioAdapter``, orquestador desacoplado del proveedor.
- ``exceptions``: jerarquía de excepciones propia.
- ``providers``: implementaciones concretas (``google``, ``synthetic``,
  ``kokoro``).

Uso típico:

    from audio import AudioAdapter, AudioRequest
    from audio.providers import SyntheticAudioProvider

    adapter = AudioAdapter(SyntheticAudioProvider(model="synthetic-v1"))
    resultado = adapter.generate(AudioRequest(text="Hola"))
"""

from __future__ import annotations

from .base import AudioFormat, AudioProvider, AudioRequest, AudioResult, VoiceSettings
from .exceptions import (
    AudioError,
    AudioGenerationError,
    AudioProviderError,
)
from .models import AudioNarration, NarrationSource, SpeechPrompt
from .adapter import AudioAdapter
from .providers import GoogleTTSProvider, KokoroAudioProvider, SyntheticAudioProvider

__all__ = [
    "AudioFormat",
    "AudioProvider",
    "AudioRequest",
    "AudioResult",
    "VoiceSettings",
    "AudioError",
    "AudioGenerationError",
    "AudioProviderError",
    "AudioNarration",
    "NarrationSource",
    "SpeechPrompt",
    "AudioAdapter",
    "GoogleTTSProvider",
    "KokoroAudioProvider",
    "SyntheticAudioProvider",
]