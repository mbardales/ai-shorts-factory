"""Serialización JSON del modelo "Project Manifest".

Funciones puras (sin dependencias externas, solo el módulo ``json`` de la
biblioteca estándar) que convierten un :class:`ProjectManifest` hacia y desde
diccionarios planos y texto JSON. Esto permite persistir el manifest en
``output/project.json`` o intercambiarlo con otros módulos (por ejemplo, el
futuro renderizador de video).

El formato de dict es estable y coincide con la estructura del agregado; los
enums (``AssetKind``) se serializan por su valor de cadena.

La reconstrucción desde dict delega en :func:`project.validator.coerce_project_manifest`,
que rellena campos ausentes con valores por defecto y solo lanza error si el
JSON es irrecuperable.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .models import ProjectManifest
from .validator import coerce_project_manifest


# ---------------------------------------------------------------------------
# Hacia dict / JSON
# ---------------------------------------------------------------------------


def _identity_to_dict(identity: Any) -> dict[str, Any]:
    """Convierte una identidad de proyecto a dict."""
    return {
        "id": identity.id,
        "title": identity.title,
        "language": identity.language,
        "created_at": identity.created_at,
    }


def _asset_to_dict(asset: Any) -> dict[str, Any]:
    """Convierte un :class:`ProjectAsset` a dict (enum por su valor)."""
    return {
        "kind": asset.kind.value,
        "name": asset.name,
        "path": asset.path,
        "scene_index": asset.scene_index,
        "extension": asset.extension,
        "provider": asset.provider,
        "model": asset.model,
    }


def _metadata_to_dict(metadata: Any) -> dict[str, Any]:
    """Convierte un :class:`ProjectMetadata` a dict.

    ``run_id`` solo se incluye cuando no es ``None`` para mantener la salida
    retrocompatible con manifest legacy (que no declaraban ejecución).
    """
    result: dict[str, Any] = {
        "schema_version": metadata.schema_version,
        "created_at": metadata.created_at,
        "updated_at": metadata.updated_at,
        "content_file": metadata.content_file,
    }
    if metadata.run_id is not None:
        result["run_id"] = metadata.run_id
    return result


def project_manifest_to_dict(manifest: ProjectManifest) -> dict[str, Any]:
    """Convierte un :class:`ProjectManifest` a un dict serializable a JSON.

    Args:
        manifest: agregado a serializar.

    Returns:
        Representación plana del manifest.
    """
    return {
        "identity": _identity_to_dict(manifest.identity),
        "content": dict(manifest.content),
        "assets": [_asset_to_dict(asset) for asset in manifest.assets],
        "estimated_duration_seconds": manifest.estimated_duration_seconds,
        "output_file": manifest.output_file,
        "metadata": _metadata_to_dict(manifest.metadata),
    }


def project_manifest_to_json(manifest: ProjectManifest, *, indent: int = 2) -> str:
    """Serializa un :class:`ProjectManifest` a una cadena JSON.

    Args:
        manifest: agregado a serializar.
        indent: sangría del JSON (por defecto 2).

    Returns:
        Cadena JSON con caracteres Unicode no escapados.
    """
    return json.dumps(
        project_manifest_to_dict(manifest),
        ensure_ascii=False,
        indent=indent,
    )


# ---------------------------------------------------------------------------
# Desde dict / JSON
# ---------------------------------------------------------------------------


def project_manifest_from_dict(data: Mapping[str, Any]) -> ProjectManifest:
    """Reconstruye un :class:`ProjectManifest` desde un dict de forma robusta.

    Delega en :func:`project.validator.coerce_project_manifest`, que valida los
    campos del agregado y completa con valores por defecto los ausentes. Solo
    lanza error si el dict es estructuralmente irrecuperable.

    Args:
        data: dict producido por :func:`project_manifest_to_dict` (o
            equivalente).

    Returns:
        Agregado completo con todos los campos poblados.

    Raises:
        ProjectValidationError: si ``data`` no es un dict.
    """
    return coerce_project_manifest(data)


def project_manifest_from_json(text: str) -> ProjectManifest:
    """Reconstruye un :class:`ProjectManifest` desde una cadena JSON.

    Args:
        text: JSON producido por :func:`project_manifest_to_json`.

    Returns:
        Manifest reconstruido y **completado** con valores por defecto.

    Raises:
        ValueError: si el JSON es inválido.
        ProjectValidationError: si el JSON no es un objeto (dict).
    """
    return project_manifest_from_dict(json.loads(text))
