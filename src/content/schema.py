"""Serialización JSON del modelo "Content Package".

Funciones puras (sin dependencias externas, solo el módulo ``json`` de la
biblioteca estándar) que convierten un :class:`ContentPackage` hacia y desde
diccionarios planos y texto JSON. Esto permite persistir el contenido o
intercambiarlo con otros módulos.

El formato de dict es estable y coincide con la estructura del agregado; los
enums (``ContentStage``) se serializan por su valor de cadena.

La reconstrucción desde dict delega en :func:`content.validator.coerce_content_package`,
que rellena campos ausentes con valores por defecto y solo lanza error si el
JSON es irrecuperable.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .models import (
    Analytics,
    Assets,
    ContentPackage,
    ContentStage,
    Identity,
    Narration,
    Publication,
    Research,
    SEO,
    Scene,
    Script,
    Status,
    Visuals,
)
from .validator import coerce_content_package


# ---------------------------------------------------------------------------
# Esquema de salida (Structured Output)
# ---------------------------------------------------------------------------

#: JSON Schema (dict) de un Content Package, usable como ``response_schema``
#: en proveedores con Structured Output. Contiene solo las secciones que el
#: modelo debe producir (identity, research, seo, script, visuals, narration,
#: status); ``assets``, ``publication`` y ``analytics`` se omiten porque en la
#: generación aún no representan información real y el validator las
#: inicializa en null.
CONTENT_PACKAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Content Package de un YouTube Short.",
    "properties": {
        "identity": {
            "type": "object",
            "description": "Identidad del contenido.",
            "properties": {
                "id": {"type": "string", "description": "Identificador único."},
                "title": {
                    "type": "string",
                    "description": "Título del Short (máx 100 caracteres).",
                },
                "language": {"type": "string", "description": "Idioma del contenido."},
                "created_at": {
                    "type": "string",
                    "description": "Fecha de creación en ISO-8601.",
                },
            },
            "required": ["title"],
        },
        "research": {
            "type": "object",
            "description": "Investigación del tema.",
            "properties": {
                "topic": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "sources": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
            },
        },
        "seo": {
            "type": "object",
            "description": "Optimización para descubrimiento.",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Descripción (máx 5000 caracteres).",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Hasta 30 etiquetas.",
                },
                "keywords": {"type": "array", "items": {"type": "string"}},
            },
        },
        "script": {
            "type": "object",
            "description": "Guion del contenido.",
            "properties": {
                "hook": {"type": "string"},
                "development": {"type": "string"},
                "call_to_action": {"type": "string"},
            },
        },
        "visuals": {
            "type": "object",
            "description": "Plan visual del Short.",
            "properties": {
                "scenes": {
                    "type": "array",
                    "description": "Secuencia de escenas.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "timing_seconds": {"type": "integer"},
                        },
                    },
                },
                "style_notes": {"type": "string"},
            },
        },
        "narration": {
            "type": "object",
            "description": "Narración y voz.",
            "properties": {
                "text": {"type": "string"},
                "voice": {"type": "string"},
            },
        },
        "status": {
            "type": "object",
            "description": "Estado del ciclo de vida.",
            "properties": {
                "stage": {
                    "type": "string",
                    "enum": [stage.value for stage in ContentStage],
                },
                "updated_at": {"type": "string"},
            },
        },
    },
    "required": ["identity"],
}


# ---------------------------------------------------------------------------
# Hacia dict / JSON
# ---------------------------------------------------------------------------


def _identity_to_dict(identity: Identity) -> dict[str, Any]:
    """Convierte un :class:`Identity` a dict."""
    return {
        "id": identity.id,
        "title": identity.title,
        "language": identity.language,
        "created_at": identity.created_at,
    }


def _research_to_dict(research: Research) -> dict[str, Any]:
    """Convierte un :class:`Research` a dict."""
    return {
        "topic": research.topic,
        "keywords": list(research.keywords),
        "sources": list(research.sources),
        "notes": research.notes,
    }


def _seo_to_dict(seo: SEO) -> dict[str, Any]:
    """Convierte un :class:`SEO` a dict."""
    return {
        "description": seo.description,
        "tags": list(seo.tags),
        "keywords": list(seo.keywords),
    }


def _script_to_dict(script: Script) -> dict[str, Any]:
    """Convierte un :class:`Script` a dict (incluye los campos opcionales)."""
    return {
        "hook": script.hook,
        "development": script.development,
        "call_to_action": script.call_to_action,
    }


def _scene_to_dict(scene: Scene) -> dict[str, Any]:
    """Convierte una :class:`Scene` a dict."""
    return {
        "description": scene.description,
        "timing_seconds": scene.timing_seconds,
    }


def _visuals_to_dict(visuals: Visuals) -> dict[str, Any]:
    """Convierte un :class:`Visuals` a dict."""
    return {
        "scenes": [_scene_to_dict(scene) for scene in visuals.scenes],
        "style_notes": visuals.style_notes,
    }


def _narration_to_dict(narration: Narration) -> dict[str, Any]:
    """Convierte una :class:`Narration` a dict."""
    return {
        "text": narration.text,
        "voice": narration.voice,
    }


def _assets_to_dict(assets: Assets) -> dict[str, Any]:
    """Convierte un :class:`Assets` a dict (incluye los campos opcionales)."""
    return {
        "audio": assets.audio,
        "music": assets.music,
        "branding": assets.branding,
        "font": assets.font,
        "video": assets.video,
        "thumbnail": assets.thumbnail,
    }


def _publication_to_dict(publication: Publication) -> dict[str, Any]:
    """Convierte una :class:`Publication` a dict."""
    return {
        "platform": publication.platform,
        "video_id": publication.video_id,
        "published_at": publication.published_at,
        "privacy_status": publication.privacy_status,
        "url": publication.url,
    }


def _analytics_to_dict(analytics: Analytics) -> dict[str, Any]:
    """Convierte un :class:`Analytics` a dict."""
    return {
        "views": analytics.views,
        "likes": analytics.likes,
        "comments": analytics.comments,
        "shares": analytics.shares,
    }


def _status_to_dict(status: Status) -> dict[str, Any]:
    """Convierte un :class:`Status` a dict (etapa por su valor de cadena)."""
    return {
        "stage": status.stage.value,
        "updated_at": status.updated_at,
    }


def content_package_to_dict(package: ContentPackage) -> dict[str, Any]:
    """Convierte un :class:`ContentPackage` a un dict serializable a JSON.

    Args:
        package: agregado a serializar.

    Returns:
        Representación plana del paquete.
    """
    return {
        "identity": _identity_to_dict(package.identity),
        "research": _research_to_dict(package.research),
        "seo": _seo_to_dict(package.seo),
        "script": _script_to_dict(package.script),
        "visuals": _visuals_to_dict(package.visuals),
        "narration": _narration_to_dict(package.narration),
        "assets": _assets_to_dict(package.assets),
        "publication": _publication_to_dict(package.publication),
        "analytics": _analytics_to_dict(package.analytics),
        "status": _status_to_dict(package.status),
    }


def content_package_to_json(package: ContentPackage, *, indent: int = 2) -> str:
    """Serializa un :class:`ContentPackage` a una cadena JSON.

    Args:
        package: agregado a serializar.
        indent: sangría del JSON (por defecto 2).

    Returns:
        Cadena JSON con caracteres Unicode no escapados.
    """
    return json.dumps(
        content_package_to_dict(package),
        ensure_ascii=False,
        indent=indent,
    )


# ---------------------------------------------------------------------------
# Desde dict / JSON
# ---------------------------------------------------------------------------


def content_package_from_dict(data: Mapping[str, Any]) -> ContentPackage:
    """Reconstruye un :class:`ContentPackage` desde un dict de forma robusta.

    Delega en :func:`content.validator.coerce_content_package`, que valida los
    diez objetos de valor y completa con valores por defecto los campos
    ausentes. Solo lanza error si el dict es estructuralmente irrecuperable.

    Args:
        data: dict producido por :func:`content_package_to_dict` (o equivalente).

    Returns:
        Agregado completo con todos los objetos de valor poblados.

    Raises:
        ContentValidationError: si ``data`` no es un dict.
    """
    return coerce_content_package(data)


def content_package_from_json(text: str) -> ContentPackage:
    """Reconstruye un :class:`ContentPackage` desde una cadena JSON.

    Args:
        text: JSON producido por :func:`content_package_to_json`.

    Returns:
        Agregado reconstruido y **completado** con valores por defecto.

    Raises:
        ValueError: si el JSON es inválido.
        ContentValidationError: si el JSON no es un objeto (dict).
    """
    return content_package_from_dict(json.loads(text))
