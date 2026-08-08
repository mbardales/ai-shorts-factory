"""Contratos base compartidos por todos los tipos de medio.

Define la taxonomía de tipos de medio (imagen, audio, video) y las extensiones
de archivo asociadas a cada uno. ``image``, ``audio`` y ``video`` (módulos
futuros) se apoyarán en esta clasificación para el resto de la infraestructura
sin duplicarla.
"""

from __future__ import annotations

import enum
from typing import Optional


class MediaKind(str, enum.Enum):
    """Tipos de medio soportados por la infraestructura.

    Miembros:

    - ``IMAGE``: imágenes y gráficos estáticos.
    - ``AUDIO``: pistas y locuciones de audio.
    - ``VIDEO``: clips y vídeos.
    """

    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"

    @property
    def extensions(self) -> tuple[str, ...]:
        """Extensiones de archivo asociadas a este tipo de medio."""
        return MEDIA_EXTENSIONS[self]

    @classmethod
    def from_extension(cls, extension: Optional[str]) -> Optional["MediaKind"]:
        """Devuelve el tipo de medio asociado a una extensión (o ``None``).

        La extensión se normaliza (sin punto, en minúsculas). Si ninguna
        extensión conocida coincide, devuelve ``None``.
        """
        if not extension:
            return None
        ext = str(extension).strip().lower().lstrip(".")
        for kind, extensions in MEDIA_EXTENSIONS.items():
            if ext in extensions:
                return kind
        return None


#: Extensiones de archivo reconocidas por tipo de medio. Solo agrupan formatos
#: habituales de YouTube Shorts; la validación fina de cada formato pertenece a
#: los módulos de ``image``, ``audio`` y ``video``.
MEDIA_EXTENSIONS: dict[MediaKind, tuple[str, ...]] = {
    MediaKind.IMAGE: ("jpg", "jpeg", "png", "webp", "gif"),
    MediaKind.AUDIO: ("mp3", "wav", "ogg", "m4a"),
    MediaKind.VIDEO: ("mp4", "webm", "mov", "mkv"),
}
