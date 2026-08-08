"""Contratos base del Image Core de AI Shorts Factory.

Define el contrato que deben implementar todos los proveedores de imágenes
(``ImageProvider``), las ``dataclasses`` tipadas que intercambian
(``ImageRequest``, ``ImageResult``, ``ImageOptions``) y el enum
:class:`AspectRatio`. Los consumidores deben depender solo de estos tipos y
nunca de un proveedor concreto.

Reutiliza Media Core: el resultado transporta un :class:`media.ImageMetadata`
con los metadatos técnicos de la imagen generada.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from media import ImageMetadata


class AspectRatio(str, enum.Enum):
    """Proporciones de aspecto soportadas por el Image Core.

    Valores expresados como cadena ``ancho:alto``.

    Miembros:

    - ``VERTICAL``: 9:16, formato habitual de YouTube Shorts.
    - ``SQUARE``: 1:1.
    - ``LANDSCAPE``: 16:9.
    """

    VERTICAL = "9:16"
    SQUARE = "1:1"
    LANDSCAPE = "16:9"


@dataclass(frozen=True)
class ImageRequest:
    """Solicitud tipada de generación de una imagen.

    Attributes:
        prompt: descripción textual de la imagen a generar.
        aspect_ratio: proporción de aspecto de la imagen.
        negative_prompt: contenido a evitar (opcional).
        width: anchura explícita en píxeles (opcional; si se omite, la decide
            el proveedor según el aspecto).
        height: altura explícita en píxeles (opcional).
    """

    prompt: str
    aspect_ratio: AspectRatio = AspectRatio.VERTICAL
    negative_prompt: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.prompt or not self.prompt.strip():
            raise ValueError("El prompt de la imagen no puede estar vacío.")


@dataclass(frozen=True)
class ImageResult:
    """Resultado tipado de la generación de una imagen.

    Attributes:
        prompt: prompt que produjo la imagen.
        content: bytes de la imagen generada.
        metadata: metadatos técnicos de la imagen (de Media Core).
        model: modelo que produjo la imagen.
    """

    prompt: str
    content: bytes
    metadata: ImageMetadata
    model: str


@dataclass(frozen=True)
class ImageOptions:
    """Parámetros de generación opcionales compartidos entre proveedores.

    Los valores ``None`` indican que el proveedor debe usar su valor por
    defecto. No todos los proveedores soportan todos los parámetros.
    """

    seed: Optional[int] = None
    steps: Optional[int] = None
    guidance_scale: Optional[float] = None


class ImageProvider(ABC):
    """Contrato que deben implementar todos los proveedores de imágenes.

    Args:
        model: identificador del modelo a utilizar.
        options: parámetros de generación opcionales.
    """

    #: Nombre corto y estable del proveedor (ej. ``"gemini-image"``).
    name: str = "base"

    def __init__(
        self,
        model: str,
        *,
        options: Optional[ImageOptions] = None,
    ) -> None:
        self.model = model
        self.options = options or ImageOptions()

    @abstractmethod
    def generate(self, request: ImageRequest, **kwargs: Any) -> ImageResult:
        """Genera una imagen a partir de una :class:`ImageRequest`.

        Args:
            request: solicitud de la imagen a generar.
            **kwargs: parámetros de generación que sobrescriben los de
                ``self.options`` (seed, steps, guidance_scale).

        Returns:
            :class:`ImageResult` con los bytes y metadatos de la imagen.

        Raises:
            image.exceptions.ImageError: cualquier error del proveedor.
        """

    def resolve_options(self, **kwargs: Any) -> ImageOptions:
        """Combina las opciones base con los sobrescritos de ``kwargs``.

        Devuelve una :class:`ImageOptions` con los parámetros con valor
        presente; los ``None`` se omiten para que el proveedor aplique su
        valor por defecto.
        """
        return ImageOptions(
            seed=kwargs.get("seed", self.options.seed),
            steps=kwargs.get("steps", self.options.steps),
            guidance_scale=kwargs.get("guidance_scale", self.options.guidance_scale),
        )
