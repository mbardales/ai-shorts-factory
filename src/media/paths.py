"""Utilidades de rutas y nomenclatura de archivos multimedia.

Define una convención de nombres y ubicación de los activos dentro del
proyecto, reutilizable por ``image``, ``audio`` y ``video``:

- Nombres de archivo con slugs ASCII: ``<content_id>-<nombre>.<ext>``.
- Ubicación por tipo y contenido: ``assets/<kind>/<content_id>/<archivo>``.
- Validación de extensiones y de rutas relativas seguras (anti path-traversal).

Las raíces por defecto se derivan de la ubicación de este módulo
(``src/media/paths.py`` → raíz del proyecto), igual que en ``ai.adapter``.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Optional, Union

from .base import MediaKind
from .exceptions import MediaUnsupportedError, MediaValidationError

#: Raíz del proyecto (dos niveles por encima de este módulo).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
#: Directorio raíz de los activos multimedia.
ASSETS_ROOT = PROJECT_ROOT / "assets"

#: Subdirectorio por defecto de cada tipo de medio dentro de ``assets/``.
ASSET_SUBDIRS: dict[MediaKind, str] = {
    MediaKind.IMAGE: "images",
    MediaKind.AUDIO: "audio",
    MediaKind.VIDEO: "videos",
}

#: Expresión de componentes válidos en un slug (solo minúsculas y dígitos).
_SLUG_KEEP = re.compile(r"[a-z0-9]+")


def sanitize_component(value: str, *, fallback: str = "asset") -> str:
    """Normaliza un texto a un slug ASCII seguro para nombres de archivo.

    Elimina acentos, convierte a minúsculas y sustituye cualquier secuencia de
    caracteres no alfanuméricos por un guion. Devuelve ``fallback`` si el
    resultado queda vacío.

    Args:
        value: texto a normalizar.
        fallback: valor a usar si el slug normalizado queda vacío.

    Returns:
        Slug seguro (solo ``[a-z0-9-]``).
    """
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    parts = [part for part in _SLUG_KEEP.findall(text)]
    slug = "-".join(parts).strip("-")
    return slug or fallback


def normalize_extension(extension: str) -> str:
    """Normaliza una extensión: sin punto, en minúsculas y sin espacios."""
    return str(extension).strip().lower().lstrip(".")


def split_filename(filename: Union[str, Path]) -> tuple[str, str]:
    """Divide un nombre de archivo en (raíz, extensión) normalizada.

    Args:
        filename: nombre de archivo (con o sin extensión).

    Returns:
        Tupla ``(stem, extension)``; ``extension`` va sin punto y en minúsculas
        (cadena vacía si no hay).
    """
    path = Path(filename)
    return path.stem, path.suffix.lstrip(".").lower()


def build_asset_filename(
    *, content_id: str, name: str, ext: str, kind: Optional[MediaKind] = None
) -> str:
    """Construye el nombre de archivo de un activo con la nomenclatura del proyecto.

    Formato: ``<content_id>-<name>.<ext>`` con componentes saneados y extensión
    normalizada. Ejemplo: ``short-cielo-azul-001-narracion.mp3``.

    Args:
        content_id: identificador del contenido (slug).
        name: descriptor del activo (ej. ``"narracion"``, ``"portada"``).
        ext: extensión del archivo.
        kind: tipo de medio (opcional). Si se indica, valida la extensión.

    Returns:
        Nombre de archivo final.

    Raises:
        MediaUnsupportedError: si ``kind`` se indica y la extensión no es
            válida para ese tipo.
    """
    content_slug = sanitize_component(content_id)
    name_slug = sanitize_component(name)
    extension = (
        ensure_supported_extension(ext, kind)
        if kind is not None
        else normalize_extension(ext)
    )
    return f"{content_slug}-{name_slug}.{extension}"


def build_asset_rel_path(
    *, content_id: str, name: str, ext: str, kind: Optional[MediaKind] = None
) -> Path:
    """Ruta relativa (a ``assets/``) de un activo.

    Formato: ``<kind>/<content_id>/<archivo>``. Si ``kind`` no se indica, se
    infiere de la extensión.

    Raises:
        MediaUnsupportedError: si ``kind`` falta y la extensión no es
            reconocible, o la extensión no es válida para el tipo.
    """
    resolved_kind = kind or _kind_for_extension(ext)
    if resolved_kind is None:
        raise MediaUnsupportedError(
            f"No se pudo inferir el tipo de medio de la extensión '{ext}'. "
            "Indica 'kind' explícitamente."
        )
    extension = ensure_supported_extension(ext, resolved_kind)
    subdir = ASSET_SUBDIRS[resolved_kind]
    filename = build_asset_filename(
        content_id=content_id, name=name, ext=extension, kind=resolved_kind
    )
    return Path(subdir) / sanitize_component(content_id) / filename


def resolve_asset_path(
    *,
    content_id: str,
    name: str,
    ext: str,
    kind: Optional[MediaKind] = None,
    root: Path = ASSETS_ROOT,
) -> Path:
    """Ruta absoluta de un activo dentro del almacén local por defecto."""
    return root / build_asset_rel_path(content_id=content_id, name=name, ext=ext, kind=kind)


def _kind_for_extension(extension: str) -> Optional[MediaKind]:
    """Infiera el tipo de medio desde una extensión (o ``None``)."""
    return MediaKind.from_extension(normalize_extension(extension))


def infer_media_kind(filename: Union[str, Path]) -> Optional[MediaKind]:
    """Devuelve el tipo de medio de un archivo según su extensión (o ``None``)."""
    _, extension = split_filename(filename)
    return _kind_for_extension(extension)


def is_supported_extension(extension: str, kind: MediaKind) -> bool:
    """Indica si una extensión es válida para un tipo de medio."""
    return normalize_extension(extension) in kind.extensions


def ensure_supported_extension(extension: str, kind: MediaKind) -> str:
    """Valida y devuelve la extensión normalizada para un tipo de medio.

    Raises:
        MediaUnsupportedError: si la extensión no está soportada para ``kind``.
    """
    ext = normalize_extension(extension)
    if ext not in kind.extensions:
        raise MediaUnsupportedError(
            f"Extensión '{extension}' no soportada para {kind.value}. "
            f"Soportadas: {', '.join(kind.extensions)}."
        )
    return ext


def ensure_safe_relative(path: Union[str, Path]) -> Path:
    """Valida que una ruta sea relativa y sin componentes peligrosos.

    Rechaza rutas absolutas, con drive/raíz, subidas de nivel (``..``) y
    caracteres nulos para evitar escapes del almacén. Se usa como guarda en las
    operaciones de almacenamiento.

    Args:
        path: ruta relativa a validar.

    Returns:
        La ruta como :class:`Path` normalizado.

    Raises:
        MediaValidationError: si la ruta es absoluta, contiene ``..`` o un
            carácter nulo.
    """
    candidate = Path(path)
    text = str(candidate)
    anchored = candidate.is_absolute() or bool(candidate.drive) or bool(candidate.root)
    if anchored or ".." in candidate.parts or "\x00" in text:
        raise MediaValidationError(f"Ruta relativa no segura: {text!r}")
    return candidate
