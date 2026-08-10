"""Proveedor sintético de imágenes para desarrollo y pruebas.

Implementa ImageProvider sin depender de APIs externas ni de proveedores
comerciales. Genera PNG reales mediante la biblioteca estándar de Python.

Su objetivo es permitir validar el pipeline completo de AI Shorts Factory
cuando los proveedores de generación de imágenes externos no están
disponibles por cuota, credenciales o permisos.

El proveedor:
- no realiza llamadas HTTP;
- no requiere API key;
- no escribe archivos;
- devuelve ImageResult con bytes PNG reales;
- respeta el contrato de ImageProvider;
- genera imágenes verticales 720x1280 por defecto;
- permite resultados reproducibles mediante seed.
"""

from __future__ import annotations

import logging
import struct
import zlib
from typing import Any

from ..base import ImageOptions, ImageProvider, ImageRequest, ImageResult
from ..exceptions import ImageGenerationError
from media import ImageMetadata


logger = logging.getLogger(__name__)


DEFAULT_WIDTH = 720
DEFAULT_HEIGHT = 1280
DEFAULT_MODEL = "synthetic-v1"


class SyntheticImageProvider(ImageProvider):
    """Proveedor local que genera imágenes PNG sintéticas.

    Está destinado exclusivamente a desarrollo, pruebas e integración del
    pipeline. No representa generación mediante IA.

    Args:
        model: identificador lógico del proveedor sintético.
        options: opciones compartidas del Image Core.
    """

    name: str = "synthetic-image"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        options: ImageOptions | None = None,
    ) -> None:
        super().__init__(model, options=options)

    def generate(
        self,
        request: ImageRequest,
        **kwargs: Any,
    ) -> ImageResult:
        """Genera una imagen PNG sintética y devuelve sus bytes."""

        if not isinstance(request, ImageRequest):
            raise TypeError("request debe ser un ImageRequest.")

        if not request.prompt or not request.prompt.strip():
            raise ValueError("El prompt de la imagen no puede estar vacío.")

        options = self.resolve_options(**kwargs)

        width = request.width or DEFAULT_WIDTH
        height = request.height or DEFAULT_HEIGHT

        if width <= 0 or height <= 0:
            raise ImageGenerationError(
                "Las dimensiones de la imagen deben ser positivas."
            )

        if request.aspect_ratio.value != "9:16":
            width, height = self._dimensions_for_aspect_ratio(
                request.aspect_ratio.value,
                width,
                height,
            )

        seed = options.seed
        if seed is None:
            seed = self._stable_seed(request.prompt)

        content = self._build_png(
            width=width,
            height=height,
            seed=seed,
        )

        metadata = ImageMetadata(
            name=self._build_name(request.prompt),
            mime_type="image/png",
            size_bytes=len(content),
            width=width,
            height=height,
            format="png",
            mode="RGB",
        )

        logger.debug(
            "Imagen sintética generada: %dx%d, %d bytes.",
            width,
            height,
            len(content),
        )

        return ImageResult(
            prompt=request.prompt,
            content=content,
            metadata=metadata,
            model=self.model,
            provider=self.name,
        )

    @staticmethod
    def _dimensions_for_aspect_ratio(
        aspect_ratio: str,
        width: int,
        height: int,
    ) -> tuple[int, int]:
        """Calcula dimensiones conservando aproximadamente el tamaño base."""

        if aspect_ratio == "1:1":
            size = min(width, height)
            return size, size

        if aspect_ratio == "16:9":
            return 1280, 720

        if aspect_ratio == "9:16":
            return 720, 1280

        raise ImageGenerationError(
            f"Aspect ratio no soportado por SyntheticImageProvider: "
            f"{aspect_ratio!r}."
        )

    @staticmethod
    def _stable_seed(prompt: str) -> int:
        """Genera una semilla estable a partir del prompt."""

        value = 0

        for index, char in enumerate(prompt):
            value = (
                value
                + (index + 1) * ord(char)
            ) & 0xFFFFFFFF

        return value

    @staticmethod
    def _build_name(prompt: str) -> str:
        """Construye un nombre lógico corto para los metadatos."""

        normalized = " ".join(prompt.split())

        if not normalized:
            return "synthetic-image"

        name = normalized[:50].strip()
        return name or "synthetic-image"

    @staticmethod
    def _build_png(
        *,
        width: int,
        height: int,
        seed: int,
    ) -> bytes:
        """Construye un PNG RGB válido utilizando únicamente stdlib."""

        red = 40 + (seed & 0x7F)
        green = 40 + ((seed >> 7) & 0x7F)
        blue = 40 + ((seed >> 14) & 0x7F)

        red = min(red, 255)
        green = min(green, 255)
        blue = min(blue, 255)

        rows = bytearray()

        for y in range(height):
            rows.append(0)

            progress = y / max(height - 1, 1)

            r = int(red * (1.0 - progress) + 255 * progress)
            g = int(green * (1.0 - progress) + 255 * progress)
            b = int(blue * (1.0 - progress) + 255 * progress)

            pixel = bytes((r, g, b))

            rows.extend(pixel * width)

        compressed = zlib.compress(bytes(rows), level=6)

        signature = b"\x89PNG\r\n\x1a\n"

        ihdr = struct.pack(
            ">IIBBBBB",
            width,
            height,
            8,
            2,
            0,
            0,
            0,
        )

        return (
            signature
            + SyntheticImageProvider._png_chunk(b"IHDR", ihdr)
            + SyntheticImageProvider._png_chunk(b"IDAT", compressed)
            + SyntheticImageProvider._png_chunk(b"IEND", b"")
        )

    @staticmethod
    def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
        """Construye un chunk PNG válido."""

        length = struct.pack(">I", len(data))
        crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF

        return (
            length
            + chunk_type
            + data
            + struct.pack(">I", crc)
        )