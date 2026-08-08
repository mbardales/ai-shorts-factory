"""Contratos base del Video Core de AI Shorts Factory.

Define el contrato que deben implementar todos los renderizadores de video
(``VideoRenderer``), las ``dataclasses`` tipadas que intercambian
(``VideoRequest``, ``VideoResult``, ``RenderOptions``) y el enum
:class:`VideoFormat`. Los consumidores deben depender solo de estos tipos y
nunca de un renderizador concreto.

Reutiliza Media Core: el resultado transporta un :class:`media.VideoMetadata`
con los metadatos técnicos del video renderizado, y la composición se describe
con una :class:`~video.timeline.Timeline`.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from media import VideoMetadata

from .timeline import Timeline


class VideoFormat(str, enum.Enum):
    """Formatos de codificación de video soportados por el Video Core.

    Los valores coinciden con las extensiones de ``media.MediaKind.VIDEO``.

    Miembros:

    - ``MP4``: contenedor MPEG-4 (extensión ``.mp4``), habitual en YouTube.
    - ``WEBM``: contenedor WebM/VP9 (extensión ``.webm``).
    - ``MOV``: contenedor QuickTime (extensión ``.mov``).
    - ``MKV``: contenedor Matroska (extensión ``.mkv``).
    """

    MP4 = "mp4"
    WEBM = "webm"
    MOV = "mov"
    MKV = "mkv"


@dataclass(frozen=True)
class RenderOptions:
    """Parámetros de renderizado opcionales compartidos entre renderizadores.

    Los valores ``None`` indican que el renderizador debe usar su valor por
    defecto. No todos los renderizadores soportan todos los parámetros.
    """

    fps: Optional[int] = None
    codec: Optional[str] = None
    bitrate: Optional[str] = None


@dataclass(frozen=True)
class VideoRequest:
    """Solicitud tipada de renderizado de un video.

    Attributes:
        timeline: composición completa del video a renderizar.
        format: formato de codificación del video resultante.
        options: parámetros de renderizado opcionales.
    """

    timeline: Timeline
    format: VideoFormat = VideoFormat.MP4
    options: Optional[RenderOptions] = None

    def __post_init__(self) -> None:
        if not self.timeline or not self.timeline.video:
            raise ValueError("La solicitud requiere una línea de tiempo con video.")


@dataclass(frozen=True)
class VideoResult:
    """Resultado tipado del renderizado de un video.

    Attributes:
        timeline: composición que produjo el video.
        content: bytes del video renderizado.
        metadata: metadatos técnicos del video (de Media Core).
        renderer: renderizador que produjo el video.
    """

    timeline: Timeline
    content: bytes
    metadata: VideoMetadata
    renderer: str


class VideoRenderer(ABC):
    """Contrato que deben implementar todos los renderizadores de video.

    Args:
        model: identificador del motor o perfil de renderizado (ej. ``"ffmpeg"``).
        options: parámetros de renderizado opcionales.
    """

    #: Nombre corto y estable del renderizador (ej. ``"ffmpeg"``).
    name: str = "base"

    def __init__(
        self,
        model: str,
        *,
        options: Optional[RenderOptions] = None,
    ) -> None:
        self.model = model
        self.options = options or RenderOptions()

    @abstractmethod
    def render(self, request: VideoRequest, **kwargs: Any) -> VideoResult:
        """Renderiza un video a partir de una :class:`VideoRequest`.

        Args:
            request: solicitud del video a renderizar.
            **kwargs: parámetros de renderizado que sobrescriben los de
                ``self.options`` (fps, codec, bitrate).

        Returns:
            :class:`VideoResult` con los bytes y metadatos del video.

        Raises:
            video.exceptions.VideoError: cualquier error del renderizador.
        """

    def resolve_options(self, **kwargs: Any) -> RenderOptions:
        """Combina las opciones base con los sobrescritos de ``kwargs``.

        Devuelve una :class:`RenderOptions` con los parámetros con valor
        presente; los ``None`` se omiten para que el renderizador aplique su
        valor por defecto.
        """
        return RenderOptions(
            fps=kwargs.get("fps", self.options.fps),
            codec=kwargs.get("codec", self.options.codec),
            bitrate=kwargs.get("bitrate", self.options.bitrate),
        )
