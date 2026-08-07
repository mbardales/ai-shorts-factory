"""Modelos de dominio del Image Core.

Representan la **fuente** de las imágenes (las escenas de un Content Package) y
el prompt visual derivado de cada una, sin acoplar al proveedor de imágenes ni
a Media Core. La conversión desde el Content Package usa *duck typing* (lectura
por atributos) para no crear una dependencia dura con el paquete ``content``.

- :class:`SceneVisual`: vista visual de una escena (índice, descripción, tiempo).
- :class:`ImageScenes`: conjunto de escenas de un contenido.
- :class:`VisualPrompt`: prompt visual listo para enviar a un proveedor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .base import AspectRatio, ImageRequest


@dataclass(frozen=True)
class SceneVisual:
    """Descripción visual de una escena del ContentPackage.

    Attributes:
        scene_index: índice de la escena en la secuencia (0-based).
        description: descripción visual de la escena.
        timing_seconds: duración estimada de la escena (opcional).
    """

    scene_index: int
    description: str
    timing_seconds: Optional[int] = None

    @classmethod
    def from_content_scene(cls, scene: Any, *, index: int) -> "SceneVisual":
        """Construye una vista visual desde una escena del ContentPackage.

        La escena se lee por atributos (``description``, ``timing_seconds``)
        para no acoplar este módulo al paquete ``content``.
        """
        return cls(
            scene_index=index,
            description=str(getattr(scene, "description", "") or ""),
            timing_seconds=getattr(scene, "timing_seconds", None),
        )


@dataclass(frozen=True)
class ImageScenes:
    """Conjunto de escenas de un contenido que requieren imágenes.

    Attributes:
        content_id: identificador del contenido de origen.
        scenes: escenas con su descripción visual.
    """

    content_id: str
    scenes: tuple[SceneVisual, ...] = ()

    @classmethod
    def from_content_package(cls, package: Any) -> "ImageScenes":
        """Construye las escenas desde un ContentPackage (lectura por atributos)."""
        identity = getattr(package, "identity", None)
        content_id = str(getattr(identity, "id", "") or "")
        visuals = getattr(package, "visuals", None)
        raw_scenes = getattr(visuals, "scenes", ()) or ()
        scenes = tuple(
            SceneVisual.from_content_scene(scene, index=index)
            for index, scene in enumerate(raw_scenes)
        )
        return cls(content_id=content_id, scenes=scenes)


@dataclass(frozen=True)
class VisualPrompt:
    """Prompt visual de una escena, listo para enviar a un proveedor.

    Attributes:
        content_id: identificador del contenido de origen.
        scene_index: índice de la escena (0-based).
        prompt: texto del prompt visual.
        timing_seconds: duración estimada de la escena (opcional).
    """

    content_id: str
    scene_index: int
    prompt: str
    timing_seconds: Optional[int] = None

    def to_request(
        self,
        *,
        aspect_ratio: AspectRatio = AspectRatio.VERTICAL,
        **kwargs: Any,
    ) -> ImageRequest:
        """Convierte este prompt visual en una :class:`ImageRequest`.

        Args:
            aspect_ratio: proporción de aspecto por defecto (vertical, 9:16).
            **kwargs: campos adicionales de la solicitud (``negative_prompt``,
                ``width``, ``height``).
        """
        return ImageRequest(
            prompt=self.prompt,
            aspect_ratio=aspect_ratio,
            **kwargs,
        )
