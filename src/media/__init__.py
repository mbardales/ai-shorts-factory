"""Infraestructura compartida de activos multimedia.

Provee las piezas reutilizables sobre las que se construirán los módulos
``image``, ``audio`` y ``video`` (aún no creados) sin duplicar código:

- ``base``: taxonomía de tipos de medio (:class:`MediaKind`) y extensiones.
- ``metadata``: ``dataclasses`` de metadatos por tipo de medio.
- ``paths``: utilidades de rutas y nomenclatura consistente de archivos.
- ``storage``: abstracción de almacenamiento (:class:`Storage`,
  :class:`LocalStorage`).
- ``exceptions``: jerarquía de excepciones propia.

Uso típico:

    from media import (
        MediaKind,
        LocalStorage,
        build_asset_rel_path,
        AudioMetadata,
    )

    storage = LocalStorage("assets")
    rel = build_asset_rel_path(
        content_id="short-abc", name="narracion", ext="mp3", kind=MediaKind.AUDIO
    )
    storage.write_bytes(rel, b"...")
    metadatos = AudioMetadata(name="narracion", duration_seconds=30.5)
"""

from .base import MEDIA_EXTENSIONS, MediaKind
from .exceptions import (
    MediaConfigurationError,
    MediaError,
    MediaNotFoundError,
    MediaUnsupportedError,
    MediaValidationError,
    StorageError,
    StorageExistsError,
    StorageNotFoundError,
    StorageReadError,
    StorageWriteError,
)
from .metadata import (
    AudioMetadata,
    ImageMetadata,
    MediaMetadata,
    VideoMetadata,
    metadata_from_dict,
)
from .paths import (
    ASSETS_ROOT,
    ASSET_SUBDIRS,
    PROJECT_ROOT,
    build_asset_filename,
    build_asset_rel_path,
    ensure_safe_relative,
    ensure_supported_extension,
    infer_media_kind,
    is_supported_extension,
    normalize_extension,
    resolve_asset_path,
    sanitize_component,
    split_filename,
)
from .storage import LocalStorage, Storage

__all__ = [
    "MediaKind",
    "MEDIA_EXTENSIONS",
    "MediaError",
    "MediaConfigurationError",
    "MediaNotFoundError",
    "MediaUnsupportedError",
    "MediaValidationError",
    "StorageError",
    "StorageReadError",
    "StorageWriteError",
    "StorageExistsError",
    "StorageNotFoundError",
    "MediaMetadata",
    "ImageMetadata",
    "AudioMetadata",
    "VideoMetadata",
    "metadata_from_dict",
    "PROJECT_ROOT",
    "ASSETS_ROOT",
    "ASSET_SUBDIRS",
    "sanitize_component",
    "normalize_extension",
    "split_filename",
    "build_asset_filename",
    "build_asset_rel_path",
    "resolve_asset_path",
    "infer_media_kind",
    "is_supported_extension",
    "ensure_supported_extension",
    "ensure_safe_relative",
    "Storage",
    "LocalStorage",
]
