"""Metadatos de activos multimedia.

Contiene las ``dataclasses`` que describen un activo de medio y sus atributos
técnicos. :class:`MediaMetadata` es la base común (todo activo tiene nombre,
tipo, MIME, tamaño y fechas); las clases específicas añaden los campos propios
de cada tipo:

- :class:`ImageMetadata`: dimensiones, formato y modo de píxel.
- :class:`AudioMetadata`: duración, frecuencia, canales, bitrate y códec.
- :class:`VideoMetadata`: dimensiones, duración, fotogramas y códec.

Todos los metadatos son inmutables y serializables: ``to_dict()`` produce un
dict JSON-compatible y ``from_dict()`` / :func:`metadata_from_dict` reconstruyen
el objeto de forma robusta (coaccionando valores por tipo).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Type, Union, get_args, get_origin, get_type_hints

from .base import MediaKind
from .exceptions import MediaUnsupportedError, MediaValidationError

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """Devuelve la hora actual en UTC como cadena ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


def _as_str(value: Any, *, default: str = "") -> str:
    """Coacciona un valor a cadena (None o tipos no textuales con ``default``)."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


@dataclass(frozen=True)
class MediaMetadata:
    """Metadatos comunes a todo activo multimedia.

    Attributes:
        name: nombre lógico del activo (un slug descriptivo, sin extensión).
        kind: tipo de medio (:class:`MediaKind`).
        mime_type: tipo MIME del archivo (opcional).
        size_bytes: tamaño del archivo en bytes (opcional).
        created_at: fecha de creación en formato ISO-8601.
        updated_at: fecha de última actualización en formato ISO-8601.
    """

    name: str
    kind: MediaKind
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serializa los metadatos a un dict JSON-compatible."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MediaMetadata":
        """Reconstruye los metadatos desde un dict, coaccionando por tipo.

        Los campos ausentes usan su valor por defecto. ``name`` y ``kind`` son
        obligatorios (salvo que la clase los fije por defecto).

        Raises:
            MediaValidationError: si falta ``name`` o ``kind``.
        """
        hints = get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        for field_name in cls.__dataclass_fields__:
            if field_name not in data:
                continue
            kwargs[field_name] = _coerce_typed(data[field_name], hints[field_name])

        missing = [req for req in ("name", "kind") if req not in kwargs]
        if missing:
            raise MediaValidationError(
                f"Metadatos incompletos; faltan los campos: {', '.join(missing)}."
            )
        return cls(**kwargs)


def _coerce_typed(value: Any, hint: Any) -> Any:
    """Coacciona un valor al tipo indicado por una anotación (best effort)."""
    if value is None:
        return None
    origin = get_origin(hint)
    if origin is Union:
        return _coerce_typed(value, get_args(hint)[0])
    if hint is bool:
        return bool(value)
    if hint is int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if hint is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if hint is str:
        return str(value)
    if hint is MediaKind:
        return MediaKind(value)
    return value


@dataclass(frozen=True)
class ImageMetadata(MediaMetadata):
    """Metadatos de una imagen.

    Attributes:
        width: anchura en píxeles (opcional).
        height: altura en píxeles (opcional).
        format: formato de codificación, ej. ``"jpeg"``, ``"png"`` (opcional).
        mode: modo de píxel, ej. ``"RGB"``, ``"RGBA"`` (opcional).
    """

    kind: MediaKind = MediaKind.IMAGE
    width: Optional[int] = None
    height: Optional[int] = None
    format: Optional[str] = None
    mode: Optional[str] = None


@dataclass(frozen=True)
class AudioMetadata(MediaMetadata):
    """Metadatos de una pista de audio.

    Attributes:
        duration_seconds: duración total en segundos (opcional).
        sample_rate: frecuencia de muestreo en Hz (opcional).
        channels: número de canales, ej. 2 para estéreo (opcional).
        bit_rate_bps: bitrate en bits por segundo (opcional).
        codec: códec de audio, ej. ``"mp3"``, ``"aac"`` (opcional).
    """

    kind: MediaKind = MediaKind.AUDIO
    duration_seconds: Optional[float] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    bit_rate_bps: Optional[int] = None
    codec: Optional[str] = None


@dataclass(frozen=True)
class VideoMetadata(MediaMetadata):
    """Metadatos de un vídeo.

    Attributes:
        width: anchura del vídeo en píxeles (opcional).
        height: altura del vídeo en píxeles (opcional).
        duration_seconds: duración total en segundos (opcional).
        frame_rate: fotogramas por segundo (opcional).
        codec: códec de vídeo, ej. ``"h264"``, ``"vp9"`` (opcional).
        has_audio: si el vídeo incluye pista de audio (opcional).
    """

    kind: MediaKind = MediaKind.VIDEO
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    frame_rate: Optional[float] = None
    codec: Optional[str] = None
    has_audio: Optional[bool] = None


#: Registro de clases de metadatos por tipo de medio.
_METADATA_BY_KIND: dict[MediaKind, Type[MediaMetadata]] = {
    MediaKind.IMAGE: ImageMetadata,
    MediaKind.AUDIO: AudioMetadata,
    MediaKind.VIDEO: VideoMetadata,
}


def metadata_from_dict(data: Mapping[str, Any]) -> MediaMetadata:
    """Reconstruye metadatos del tipo correcto según el campo ``kind``.

    Args:
        data: dict producido por :meth:`MediaMetadata.to_dict` (o equivalente).

    Returns:
        :class:`ImageMetadata`, :class:`AudioMetadata` o :class:`VideoMetadata`
        según el valor de ``data["kind"]``.

    Raises:
        MediaValidationError: si ``data`` no es un dict.
        MediaUnsupportedError: si ``kind`` falta o no corresponde a un tipo
            de medio soportado.
    """
    if not isinstance(data, Mapping):
        raise MediaValidationError(
            f"Los metadatos deben ser un dict; se recibió {type(data).__name__}."
        )

    kind_value = data.get("kind")
    if isinstance(kind_value, MediaKind):
        kind: Optional[MediaKind] = kind_value
    elif kind_value is None:
        kind = None
    else:
        try:
            kind = MediaKind(_as_str(kind_value))
        except ValueError:
            kind = None

    if kind is None:
        raise MediaUnsupportedError(
            f"'kind' no soportado: {kind_value!r}. "
            f"Soportados: {[member.value for member in MediaKind]}."
        )

    metadata_cls = _METADATA_BY_KIND.get(kind)
    if metadata_cls is None:  # pragma: no cover - defensivo
        raise MediaUnsupportedError(f"No hay metadatos definidos para '{kind.value}'.")
    return metadata_cls.from_dict(data)
