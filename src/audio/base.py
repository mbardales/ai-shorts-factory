"""Contratos base del Audio Core de AI Shorts Factory.

Define el contrato que deben implementar todos los proveedores de audio/TTS
(``AudioProvider``), las ``dataclasses`` tipadas que intercambian
(``AudioRequest``, ``AudioResult``, ``VoiceSettings``) y el enum
:class:`AudioFormat`. Los consumidores deben depender solo de estos tipos y
nunca de un proveedor concreto.

Reutiliza Media Core: el resultado transporta un :class:`media.AudioMetadata`
con los metadatos técnicos de la pista de audio generada.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from media import AudioMetadata


class AudioFormat(str, enum.Enum):
    """Formatos de codificación de audio soportados por el Audio Core.

    Los valores coinciden con las extensiones de ``media.MediaKind.AUDIO``.

    Miembros:

    - ``MP3``: formato comprimido (extensión ``.mp3``).
    - ``WAV``: PCM sin pérdida (extensión ``.wav``).
    - ``OGG``: contenedor Ogg Vorbis (extensión ``.ogg``).
    """

    MP3 = "mp3"
    WAV = "wav"
    OGG = "ogg"


@dataclass(frozen=True)
class VoiceSettings:
    """Configuración de voz opcional para la síntesis de audio.

    Attributes:
        name: identificador de la voz (ej. ``"es-ES-Wavenet-D"``).
        language: código de idioma (ej. ``"es-ES"``).
        pitch: tono de la voz (semitones o factor, según el proveedor).
        rate: velocidad de habla (factor; 1.0 = velocidad normal).
        volume_gain_db: ganancia de volumen en decibelios.
    """

    name: Optional[str] = None
    language: Optional[str] = None
    pitch: Optional[float] = None
    rate: Optional[float] = None
    volume_gain_db: Optional[float] = None


@dataclass(frozen=True)
class AudioRequest:
    """Solicitud tipada de síntesis de audio.

    Attributes:
        text: texto a convertir en voz.
        format: formato de codificación del audio resultante.
        voice: configuración de voz opcional.
    """

    text: str
    format: AudioFormat = AudioFormat.MP3
    voice: Optional[VoiceSettings] = None

    def __post_init__(self) -> None:
        if not self.text or not self.text.strip():
            raise ValueError("El texto de la narración no puede estar vacío.")


@dataclass(frozen=True)
class AudioResult:
    """Resultado tipado de la síntesis de audio.

    Attributes:
        text: texto que produjo la pista.
        content: bytes del audio generado.
        metadata: metadatos técnicos de la pista (de Media Core).
        model: modelo o voz que produjo la pista.
    """

    text: str
    content: bytes
    metadata: AudioMetadata
    model: str


class AudioProvider(ABC):
    """Contrato que deben implementar todos los proveedores de audio/TTS.

    Args:
        model: identificador del modelo o voz a utilizar.
        voice: configuración de voz opcional.
    """

    #: Nombre corto y estable del proveedor (ej. ``"google-tts"``).
    name: str = "base"

    def __init__(
        self,
        model: str,
        *,
        voice: Optional[VoiceSettings] = None,
    ) -> None:
        self.model = model
        self.voice = voice or VoiceSettings()

    @abstractmethod
    def generate(self, request: AudioRequest, **kwargs: Any) -> AudioResult:
        """Sintetiza audio a partir de una :class:`AudioRequest`.

        Args:
            request: solicitud del audio a generar.
            **kwargs: parámetros de generación que sobrescriben los de
                ``self.voice`` (name, language, pitch, rate, volume_gain_db).

        Returns:
            :class:`AudioResult` con los bytes y metadatos de la pista.

        Raises:
            audio.exceptions.AudioError: cualquier error del proveedor.
        """

    def resolve_voice(self, **kwargs: Any) -> VoiceSettings:
        """Combina la voz base con los sobrescritos de ``kwargs``.

        Devuelve una :class:`VoiceSettings` con los campos con valor presente;
        los ``None`` se omiten para que el proveedor aplique su valor por
        defecto.
        """
        return VoiceSettings(
            name=kwargs.get("name", self.voice.name),
            language=kwargs.get("language", self.voice.language),
            pitch=kwargs.get("pitch", self.voice.pitch),
            rate=kwargs.get("rate", self.voice.rate),
            volume_gain_db=kwargs.get("volume_gain_db", self.voice.volume_gain_db),
        )
