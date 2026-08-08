"""Modelos de composición del Video Core (línea de tiempo).

Representan la estructura de un video como una **línea de tiempo** compuesta por
pistas tipadas (video, audio, subtítulos y branding), sin acoplar al
renderizador ni a Media Core. Son objetos de valor inmutables que un renderizador
futuro consumirá para producir el archivo final.

- :class:`Track`: base común de toda pista (intervalo y orden).
- :class:`SceneClip`: clip de video de una escena (imagen de fondo).
- :class:`AudioTrack`: pista de audio (narración o música).
- :class:`SubtitleTrack`: subtítulo incrustado (texto).
- :class:`BrandingTrack`: superposición de marca (logo/watermark).
- :class:`Timeline`: composición completa de un video.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Track:
    """Pista base de la línea de tiempo.

    Attributes:
        start_seconds: instante de inicio dentro del video (segundos).
        end_seconds: instante de fin dentro del video (segundos).
        order: orden de apilado en su pista (0 = capa inferior/fondo).
        name: nombre descriptivo de la pista (opcional).
    """

    start_seconds: float = 0.0
    end_seconds: float = 0.0
    order: int = 0
    name: Optional[str] = None

    def __post_init__(self) -> None:
        if self.start_seconds < 0:
            raise ValueError("El inicio de la pista no puede ser negativo.")
        if self.end_seconds < self.start_seconds:
            raise ValueError(
                f"El fin ({self.end_seconds}s) no puede ser anterior al inicio "
                f"({self.start_seconds}s)."
            )
        if self.order < 0:
            raise ValueError("El orden de la pista no puede ser negativo.")

    @property
    def duration_seconds(self) -> float:
        """Duración de la pista en segundos."""
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True)
class SceneClip(Track):
    """Clip de video de una escena (imagen de fondo con su duración).

    Attributes:
        scene_index: índice de la escena de origen (0-based, opcional).
        image_path: ruta a la imagen de fondo del clip (opcional).
    """

    scene_index: Optional[int] = None
    image_path: Optional[Path] = None


@dataclass(frozen=True)
class AudioTrack(Track):
    """Pista de audio (narración o música).

    Attributes:
        audio_path: ruta al archivo de audio (opcional).
        volume: volumen de reproducción (1.0 = nivel original).
    """

    audio_path: Optional[Path] = None
    volume: float = 1.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.volume < 0:
            raise ValueError("El volumen de la pista no puede ser negativo.")


@dataclass(frozen=True)
class SubtitleTrack(Track):
    """Subtítulo incrustado en el video.

    Attributes:
        text: texto del subtítulo.
        style: identificador de estilo o posición (opcional).
    """

    text: str = ""
    style: Optional[str] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.text or not self.text.strip():
            raise ValueError("El texto del subtítulo no puede estar vacío.")


@dataclass(frozen=True)
class BrandingTrack(Track):
    """Superposición de marca (logo/watermark).

    Attributes:
        image_path: ruta al logo o imagen de marca (opcional).
        position: posición en pantalla, ej. ``"bottom-right"`` (opcional).
    """

    image_path: Optional[Path] = None
    position: Optional[str] = None


@dataclass(frozen=True)
class Timeline:
    """Composición completa de un video.

    Attributes:
        content_id: identificador del contenido de origen.
        width: anchura del lienzo en píxeles.
        height: altura del lienzo en píxeles.
        fps: fotogramas por segundo del video.
        video: clips de video (escenas) en orden de aparición.
        audio: pistas de audio (narración, música).
        subtitles: subtítulos incrustados.
        branding: superposiciones de marca.
    """

    content_id: str = ""
    width: int = 1080
    height: int = 1920
    fps: int = 30
    video: tuple[SceneClip, ...] = ()
    audio: tuple[AudioTrack, ...] = ()
    subtitles: tuple[SubtitleTrack, ...] = ()
    branding: tuple[BrandingTrack, ...] = ()

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Las dimensiones del video deben ser positivas.")
        if self.fps <= 0:
            raise ValueError("Los fotogramas por segundo deben ser positivos.")
        if not self.video:
            raise ValueError("La línea de tiempo necesita al menos un clip de video.")

    @property
    def duration_seconds(self) -> float:
        """Duración total del video según el fin más tardío de sus pistas."""
        tracks = (
            *self.video,
            *self.audio,
            *self.subtitles,
            *self.branding,
        )
        return max((track.end_seconds for track in tracks), default=0.0)
