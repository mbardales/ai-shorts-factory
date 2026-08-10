"""Validaciones y coerción del modelo "Project Manifest".

Contiene la lógica de reglas del dominio, independiente de cualquier
infraestructura:

- ``find_errors`` / ``validate_project_manifest``: reglas de integridad del
  manifest (identidad, referencia al ContentPackage, activos, duración y
  archivo de salida).
- ``coerce_project_manifest``: reconstrucción robusta desde un dict, rellenando
  campos ausentes con valores por defecto y lanzando solo si la entrada es
  irrecuperable.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .models import (
    AssetKind,
    ProjectAsset,
    ProjectIdentity,
    ProjectManifest,
    ProjectMetadata,
    _utc_now_iso,
)
from .exceptions import ProjectValidationError


def find_errors(manifest: ProjectManifest) -> list[str]:
    """Devuelve la lista de errores de validación de un manifest.

    Inspecciona la identidad, la referencia al ContentPackage, los activos
    generados, la duración estimada, el archivo de salida y los metadatos.

    Args:
        manifest: manifest a inspeccionar.

    Returns:
        Lista de mensajes; vacía si el manifest es válido.
    """
    errors: list[str] = []

    identity = manifest.identity
    if not identity.id or not identity.id.strip():
        errors.append("El campo 'identity.id' no puede estar vacío.")
    if not identity.title or not identity.title.strip():
        errors.append("El campo 'identity.title' no puede estar vacío.")
    if not identity.language or not identity.language.strip():
        errors.append("El campo 'identity.language' no puede estar vacío.")
    if not identity.created_at:
        errors.append("El campo 'identity.created_at' no puede estar vacío.")

    if "identity" not in manifest.content:
        errors.append(
            "'content' debe incluir 'identity' para referenciar el ContentPackage."
        )

    if manifest.estimated_duration_seconds < 0:
        errors.append("'estimated_duration_seconds' no puede ser negativo.")

    if not manifest.output_file or not manifest.output_file.strip():
        errors.append("El campo 'output_file' no puede estar vacío.")

    if manifest.metadata.schema_version < 1:
        errors.append("'metadata.schema_version' debe ser mayor o igual a 1.")
    if not manifest.metadata.created_at:
        errors.append("El campo 'metadata.created_at' no puede estar vacío.")
    if not manifest.metadata.updated_at:
        errors.append("El campo 'metadata.updated_at' no puede estar vacío.")

    for index, asset in enumerate(manifest.assets):
        if not asset.name or not asset.name.strip():
            errors.append(f"El activo en la posición {index} no tiene 'name'.")
        if not asset.path or not asset.path.strip():
            errors.append(f"El activo en la posición {index} no tiene 'path'.")
        if asset.scene_index is not None and asset.scene_index < 0:
            errors.append(
                f"El activo en la posición {index} tiene un 'scene_index' negativo."
            )
        if asset.kind is AssetKind.AUDIO and asset.scene_index is not None:
            errors.append(
                f"El activo de audio en la posición {index} no debe tener "
                f"'scene_index'."
            )
        if asset.extension is not None and not asset.extension.strip():
            errors.append(f"El activo en la posición {index} tiene 'extension' vacía.")

    return errors


def validate_project_manifest(manifest: ProjectManifest) -> None:
    """Valida un :class:`ProjectManifest` y lanza errores si es inválido.

    Args:
        manifest: manifest a validar.

    Raises:
        ProjectValidationError: si se detecta al menos un error.
    """
    errors = find_errors(manifest)
    if errors:
        raise ProjectValidationError(errors)


# ---------------------------------------------------------------------------
# Coerción robusta desde dict (valida y completa con valores por defecto)
# ---------------------------------------------------------------------------


def _as_str(value: Any, *, default: str = "") -> str:
    """Coacciona un valor a cadena (None o tipos no textuales con ``default``)."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _as_optional_str(value: Any) -> Optional[str]:
    """Coacciona un valor a cadena opcional (None se conserva)."""
    if value is None:
        return None
    return _as_str(value)


def _as_optional_int(value: Any) -> Optional[int]:
    """Coacciona un valor a entero opcional (None si no es convertible)."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_optional_float(value: Any) -> Optional[float]:
    """Coacciona un valor a flotante opcional (None si no es convertible)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_asset_kind(value: Any) -> AssetKind:
    """Coacciona un valor a :class:`AssetKind` (por defecto ``IMAGE``)."""
    if isinstance(value, AssetKind):
        return value
    try:
        return AssetKind(_as_str(value))
    except ValueError:
        return AssetKind.IMAGE


def _coerce_identity(data: Any) -> ProjectIdentity:
    """Construye un :class:`ProjectIdentity` completo (vacío si no hay datos)."""
    if not isinstance(data, Mapping):
        return ProjectIdentity()
    now = _utc_now_iso()
    return ProjectIdentity(
        id=_as_str(data.get("id")),
        title=_as_str(data.get("title")),
        language=_as_str(data.get("language"), default="es"),
        created_at=_as_str(data.get("created_at")) or now,
    )


def _coerce_asset(data: Any) -> ProjectAsset:
    """Construye un :class:`ProjectAsset` completo desde un dict."""
    if not isinstance(data, Mapping):
        return ProjectAsset(kind=AssetKind.IMAGE, name="", path="")
    return ProjectAsset(
        kind=_coerce_asset_kind(data.get("kind")),
        name=_as_str(data.get("name")),
        path=_as_str(data.get("path")),
        scene_index=_as_optional_int(data.get("scene_index")),
        extension=_as_optional_str(data.get("extension")),
        provider=_as_optional_str(data.get("provider")),
        model=_as_optional_str(data.get("model")),
    )


def _coerce_metadata(data: Any) -> ProjectMetadata:
    """Construye un :class:`ProjectMetadata` completo (vacío si no hay datos)."""
    if not isinstance(data, Mapping):
        return ProjectMetadata()
    now = _utc_now_iso()
    return ProjectMetadata(
        schema_version=_as_optional_int(data.get("schema_version")) or 1,
        created_at=_as_str(data.get("created_at")) or now,
        updated_at=_as_str(data.get("updated_at")) or now,
        content_file=_as_optional_str(data.get("content_file")),
    )


def coerce_project_manifest(data: Mapping[str, Any]) -> ProjectManifest:
    """Reconstruye un :class:`ProjectManifest` desde un dict de forma robusta.

    Rellena con valores por defecto cualquier campo ausente o no recuperable.
    Solo lanza error si la entrada es estructuralmente irrecuperable (no es un
    dict).

    Args:
        data: dict con la estructura de un ProjectManifest (por ejemplo, el
            contenido de ``output/project.json``).

    Returns:
        Manifest completo con todos los campos poblados.

    Raises:
        ProjectValidationError: si ``data`` no es un objeto JSON (dict).
    """
    if not isinstance(data, Mapping):
        raise ProjectValidationError(
            ["El JSON de entrada no es un objeto (dict); no se puede recuperar."]
        )
    raw_assets = data.get("assets")
    assets = ()
    if isinstance(raw_assets, (list, tuple)):
        assets = tuple(_coerce_asset(item) for item in raw_assets)
    return ProjectManifest(
        identity=_coerce_identity(data.get("identity")),
        content=dict(data.get("content")) if isinstance(data.get("content"), Mapping) else {},
        assets=assets,
        estimated_duration_seconds=_as_optional_float(
            data.get("estimated_duration_seconds")
        )
        or 0.0,
        output_file=_as_str(data.get("output_file")),
        metadata=_coerce_metadata(data.get("metadata")),
    )
