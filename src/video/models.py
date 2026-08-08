"""Modelos de dominio del Video Core.

Representan la **fuente** de un renderizado (las escenas y la narración de un
Content Package) y la composición derivada de cada uno, sin acoplar al
renderizador ni a Media Core. La conversión desde el Content Package usa *duck
typing* (lectura por atributos) para no crear una dependencia dura con el
paquete ``content``.

- :class:`SceneRender`: porción de una escena a renderizar (índice y tiempo).
- :class:`VideoSource`: escenas y narración de un contenido, listas para
  renderizar.
- :class:`RenderPrompt`: composición lista para enviar a un renderizador.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .base import VideoFormat, VideoRequest
from .timeline import AudioTrack, SceneClip, Timeline


@dataclass(frozen=True)
class SceneRender:
    """Porción de una escena a incluir en el video.

    Attributes:
        scene_index: índice de la escena en la secuencia (0-based).
        start_seconds: instante de inicio dentro del video (segundos).
        end_seconds: instante de fin dentro del video (segundos).
    """

    scene_index: int
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        if self.scene_index < 0:
            raise ValueError("El índice de la escena no puede ser negativo.")
        if self.end_seconds < self.start_seconds:
            raise ValueError(
                f"El fin ({self.end_seconds}s) no puede ser anterior al inicio "
                f"({self.start_seconds}s)."
            )


@dataclass(frozen=True)
class VideoSource:
    """Escenas y narración de un contenido, listas para renderizar.

    Attributes:
        content_id: identificador del contenido de origen.
        scenes: escenas con su intervalo de tiempo en el video.
        narration_text: texto de la narración (opcional).
        voice: voz sugerida por el contenido (opcional).
    """

    content_id: str
    scenes: tuple[SceneRender, ...] = ()
    narration_text: Optional[str] = None
    voice: Optional[str] = None

    @classmethod
    def from_content_package(cls, package: Any) -> "VideoSource":
        """Construye la fuente de renderizado desde un ContentPackage.

        El contenido se lee por atributos para no acoplar este módulo al
        paquete ``content``. Los intervalos de las escenas se derivan de sus
        ``timing_seconds`` acumulados; si una escena no indica temporización,
        ocupa un hueco de 1 segundo.
        """
        identity = getattr(package, "identity", None)
        content_id = str(getattr(identity, "id", "") or "")
        visuals = getattr(package, "visuals", None)
        raw_scenes = getattr(visuals, "scenes", ()) or ()
        narration = getattr(package, "narration", None)

        scenes: list[SceneRender] = []
        cursor = 0.0
        for index, scene in enumerate(raw_scenes):
            timing = getattr(scene, "timing_seconds", None)
            timing = int(timing) if timing else 1
            scenes.append(
                SceneRender(
                    scene_index=index,
                    start_seconds=cursor,
                    end_seconds=cursor + timing,
                )
            )
            cursor += timing

        return cls(
            content_id=content_id,
            scenes=tuple(scenes),
            narration_text=str(getattr(narration, "text", "") or "") or None,
            voice=getattr(narration, "voice", None),
        )

    def to_timeline(
        self,
        *,
        width: int = 1080,
        height: int = 1920,
        fps: int = 30,
    ) -> Timeline:
        """Materializa una :class:`Timeline` a partir de la fuente.

        Los clips se crean sin rutas de activos (aún no existen); un builder
        posterior o el propio renderizador las completará.
        """
        video = tuple(
            SceneClip(
                scene_index=scene.scene_index,
                start_seconds=scene.start_seconds,
                end_seconds=scene.end_seconds,
                order=index,
            )
            for index, scene in enumerate(self.scenes)
        )
        audio = (
            (
                AudioTrack(
                    start_seconds=0.0,
                    end_seconds=max(
                        (scene.end_seconds for scene in self.scenes), default=0.0
                    ),
                    order=0,
                    name="narracion",
                ),
            )
            if self.narration_text and self.scenes
            else ()
        )
        return Timeline(
            content_id=self.content_id,
            width=width,
            height=height,
            fps=fps,
            video=video,
            audio=audio,
        )


@dataclass(frozen=True)
class RenderPrompt:
    """Composición de un video, lista para enviar a un renderizador.

    Attributes:
        content_id: identificador del contenido de origen.
        timeline: línea de tiempo con las pistas del video.
        voice: voz de la narración (opcional).
    """

    content_id: str
    timeline: Timeline
    voice: Optional[str] = None

    @classmethod
    def from_video_source(cls, source: VideoSource, **kwargs: Any) -> "RenderPrompt":
        """Construye un prompt de renderizado desde una :class:`VideoSource`."""
        return cls(
            content_id=source.content_id,
            timeline=source.to_timeline(**kwargs),
            voice=source.voice,
        )

    def to_request(
        self,
        *,
        format: VideoFormat = VideoFormat.MP4,
        **kwargs: Any,
    ) -> VideoRequest:
        """Convierte esta composición en una :class:`VideoRequest`.

        Args:
            format: formato de codificación del video resultante.
            **kwargs: campos adicionales de la solicitud (``options``).
        """
        return VideoRequest(timeline=self.timeline, format=format, **kwargs)
