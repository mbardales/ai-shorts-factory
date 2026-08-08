"""Modelos de dominio del Audio Core.

Representan la **fuente** de las pistas de audio (la narración de un Content
Package) y la solicitud de síntesis derivada de cada porción, sin acoplar al
proveedor de TTS ni a Media Core. La conversión desde el Content Package usa
*duck typing* (lectura por atributos) para no crear una dependencia dura con el
paquete ``content``.

- :class:`NarrationSource`: porción de narración a sintetizar.
- :class:`AudioNarration`: conjunto de porciones de narración de un contenido.
- :class:`SpeechPrompt`: solicitud de síntesis lista para enviar a un proveedor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .base import AudioFormat, AudioRequest, VoiceSettings


@dataclass(frozen=True)
class NarrationSource:
    """Porción de narración a convertir en voz.

    Attributes:
        index: índice de la porción en la secuencia (0-based).
        text: texto de la porción.
        voice: voz sugerida por el contenido (opcional).
        timing_seconds: duración estimada de la porción (opcional).
    """

    index: int
    text: str
    voice: Optional[str] = None
    timing_seconds: Optional[float] = None

    @classmethod
    def from_content_narration(
        cls, narration: Any, *, index: int
    ) -> "NarrationSource":
        """Construye una porción desde la narración de un ContentPackage.

        La narración se lee por atributos (``text``, ``voice``) para no acoplar
        este módulo al paquete ``content``.
        """
        return cls(
            index=index,
            text=str(getattr(narration, "text", "") or ""),
            voice=getattr(narration, "voice", None),
        )


@dataclass(frozen=True)
class AudioNarration:
    """Narración de un contenido, lista para sintetizar.

    Attributes:
        content_id: identificador del contenido de origen.
        sources: porciones de narración con su texto.
    """

    content_id: str
    sources: tuple[NarrationSource, ...] = ()

    @classmethod
    def from_content_package(cls, package: Any) -> "AudioNarration":
        """Construye las porciones desde un ContentPackage (duck typing).

        Por ahora la narración es un texto único; se genera una sola porción si
        el contenido tiene narración no vacía.
        """
        identity = getattr(package, "identity", None)
        content_id = str(getattr(identity, "id", "") or "")
        narration = getattr(package, "narration", None)
        text = str(getattr(narration, "text", "") or "")
        if not text.strip():
            return cls(content_id=content_id)
        source = NarrationSource.from_content_narration(narration, index=0)
        return cls(content_id=content_id, sources=(source,))


@dataclass(frozen=True)
class SpeechPrompt:
    """Solicitud de síntesis de una porción de narración.

    Attributes:
        content_id: identificador del contenido de origen.
        source_index: índice de la porción (0-based).
        text: texto a sintetizar.
        voice: voz sugerida por el contenido (opcional).
        timing_seconds: duración estimada de la porción (opcional).
    """

    content_id: str
    source_index: int
    text: str
    voice: Optional[str] = None
    timing_seconds: Optional[float] = None

    def to_request(
        self,
        *,
        format: AudioFormat = AudioFormat.MP3,
        **kwargs: Any,
    ) -> AudioRequest:
        """Convierte este prompt en una :class:`AudioRequest`.

        Args:
            format: formato de codificación del audio resultante.
            **kwargs: campos adicionales de la solicitud (``voice``, etc.).

        Returns:
            :class:`AudioRequest` lista para enviar a un proveedor.
        """
        voice = VoiceSettings(name=self.voice) if self.voice else None
        return AudioRequest(text=self.text, format=format, voice=voice, **kwargs)
