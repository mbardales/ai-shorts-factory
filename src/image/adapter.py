"""Adaptador de imágenes desacoplado del proveedor concreto.

``ImageAdapter`` envuelve un proveedor que implementa :class:`image.base.ImageProvider`
y expone operaciones orientadas al dominio (``generate``). El adaptador no
conoce el SDK ni la API del proveedor: depende únicamente del contrato base y
traduce los errores no controlados a la jerarquía de ``image.exceptions``.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import ImageProvider, ImageRequest, ImageResult
from .exceptions import ImageError, ImageProviderError

logger = logging.getLogger(__name__)


class ImageAdapter:
    """Envoltorio orientado al dominio que delega en un :class:`ImageProvider`.

    Args:
        provider: implementación concreta de un proveedor de imágenes.

    Raises:
        TypeError: si ``provider`` no implementa ``ImageProvider``.
    """

    def __init__(self, provider: ImageProvider) -> None:
        if not isinstance(provider, ImageProvider):
            raise TypeError(
                f"Se esperaba una instancia de ImageProvider, se recibió "
                f"{type(provider).__name__}."
            )
        self._provider = provider
        self._logger = logging.getLogger(f"{__name__}.ImageAdapter")

    @property
    def provider(self) -> ImageProvider:
        """Proveedor de imágenes subyacente."""
        return self._provider

    def generate(self, request: ImageRequest, **kwargs: Any) -> ImageResult:
        """Genera una imagen a partir de una solicitud.

        Args:
            request: solicitud de la imagen a generar.
            **kwargs: parámetros de generación adicionales.

        Returns:
            :class:`ImageResult` con la imagen generada.

        Raises:
            ImageError: cualquier error del proveedor.
            ImageProviderError: error no controlado del proveedor.
        """
        self._logger.info(
            "Generando imagen con proveedor '%s' (modelo '%s').",
            self._provider.name,
            self._provider.model,
        )
        try:
            result = self._provider.generate(request, **kwargs)
        except ImageError:
            raise
        except Exception as exc:  # noqa: BLE001 - envolver errores inesperados
            self._logger.exception("Error no controlado en el proveedor.")
            raise ImageProviderError(
                f"Error no controlado en el proveedor: {exc}"
            ) from exc

        self._logger.debug(
            "Imagen generada: %d bytes (modelo '%s').",
            len(result.content),
            result.model,
        )
        return result
