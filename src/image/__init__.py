"""Image Core de AI Shorts Factory.

Arquitectura base para la generación de imágenes, reutilizando Media Core y
siguiendo el mismo patrón que ``ai`` y ``media``:

- ``base``: contrato ``ImageProvider`` y tipos ``ImageRequest``, ``ImageResult``,
  ``ImageOptions`` y ``AspectRatio``.
- ``models``: escenas visuales y prompts derivados del ContentPackage.
- ``prompts``: construcción de prompts visuales por escena.
- ``adapter``: ``ImageAdapter``, orquestador desacoplado del proveedor.
- ``exceptions``: jerarquía de excepciones propia.

No se implementa ningún proveedor concreto todavía (``image/providers/`` no
existe) ni se generan ni llaman APIs.

Uso típico (una vez exista un proveedor):

    from image import ImageAdapter, ImageScenes, build_scene_prompts

    image_scenes = ImageScenes.from_content_package(package)
    prompts = build_scene_prompts(image_scenes)
    adapter = ImageAdapter(SomeImageProvider(model="..."))
    resultado = adapter.generate(prompts[0].to_request())
"""

from .base import AspectRatio, ImageOptions, ImageProvider, ImageRequest, ImageResult
from .exceptions import (
    ImageError,
    ImageGenerationError,
    ImageProviderError,
)
from .models import ImageScenes, SceneVisual, VisualPrompt
from .prompts import build_scene_prompt, build_scene_prompts
from .adapter import ImageAdapter

__all__ = [
    "AspectRatio",
    "ImageOptions",
    "ImageProvider",
    "ImageRequest",
    "ImageResult",
    "ImageError",
    "ImageGenerationError",
    "ImageProviderError",
    "ImageScenes",
    "SceneVisual",
    "VisualPrompt",
    "build_scene_prompt",
    "build_scene_prompts",
    "ImageAdapter",
]
