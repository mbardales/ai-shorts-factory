"""Utilidades para construir prompts visuales a partir de escenas.

Convierte las escenas de un Content Package (vía ``models.ImageScenes``) en
prompts de texto listos para un proveedor de imágenes, con una nomenclatura
consistente y orientada a YouTube Shorts (composición vertical 9:16).
"""

from __future__ import annotations

from .models import ImageScenes, SceneVisual, VisualPrompt


#: Plantilla base de un prompt visual de escena. ``index`` es 1-based (texto
#: legible) y ``description`` es la descripción de la escena.
SCENE_PROMPT_TEMPLATE: str = (
    "Escena {index} del Short: {description}. "
    "Composición vertical 9:16 para YouTube Shorts, encuadre cercano, "
    "iluminación clara, alta calidad y detalle."
)

#: Descripción usada cuando la escena no tiene texto.
_FALLBACK_DESCRIPTION = "escena genérica sin descripción"


def build_scene_prompt(scene: SceneVisual) -> str:
    """Construye el prompt visual de una escena.

    Args:
        scene: descripción visual de la escena.

    Returns:
        Prompt textual listo para un proveedor de imágenes.
    """
    description = (scene.description or "").strip() or _FALLBACK_DESCRIPTION
    return (
        SCENE_PROMPT_TEMPLATE.replace("{index}", str(scene.scene_index + 1))
        .replace("{description}", description)
    )


def build_scene_prompts(image_scenes: ImageScenes) -> tuple[VisualPrompt, ...]:
    """Construye un prompt visual por cada escena de un contenido.

    Args:
        image_scenes: escenas de un contenido (derivadas del ContentPackage).

    Returns:
        Tupla de :class:`VisualPrompt`, uno por escena, en orden.
    """
    return tuple(
        VisualPrompt(
            content_id=image_scenes.content_id,
            scene_index=scene.scene_index,
            prompt=build_scene_prompt(scene),
            timing_seconds=scene.timing_seconds,
        )
        for scene in image_scenes.scenes
    )
