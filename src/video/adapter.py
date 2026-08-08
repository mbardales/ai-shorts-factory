"""Adaptador de video desacoplado del renderizador concreto.

``VideoAdapter`` envuelve un renderizador que implementa
:class:`video.base.VideoRenderer` y expone operaciones orientadas al dominio
(``render``). El adaptador no conoce el binario ni la API del renderizador:
depende únicamente del contrato base y traduce los errores no controlados a la
jerarquía de ``video.exceptions``.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import VideoRenderer, VideoRequest, VideoResult
from .exceptions import VideoError, VideoRendererError

logger = logging.getLogger(__name__)


class VideoAdapter:
    """Envoltorio orientado al dominio que delega en un :class:`VideoRenderer`.

    Args:
        renderer: implementación concreta de un renderizador de video.

    Raises:
        TypeError: si ``renderer`` no implementa ``VideoRenderer``.
    """

    def __init__(self, renderer: VideoRenderer) -> None:
        if not isinstance(renderer, VideoRenderer):
            raise TypeError(
                f"Se esperaba una instancia de VideoRenderer, se recibió "
                f"{type(renderer).__name__}."
            )
        self._renderer = renderer
        self._logger = logging.getLogger(f"{__name__}.VideoAdapter")

    @property
    def renderer(self) -> VideoRenderer:
        """Renderizador de video subyacente."""
        return self._renderer

    def render(self, request: VideoRequest, **kwargs: Any) -> VideoResult:
        """Renderiza un video a partir de una :class:`VideoRequest`.

        Args:
            request: solicitud del video a renderizar.
            **kwargs: parámetros de renderizado adicionales.

        Returns:
            :class:`VideoResult` con el video renderizado.

        Raises:
            VideoError: cualquier error del renderizador.
            VideoRendererError: error no controlado del renderizador.
        """
        self._logger.info(
            "Renderizando video con renderizador '%s' (modelo '%s').",
            self._renderer.name,
            self._renderer.model,
        )
        try:
            result = self._renderer.render(request, **kwargs)
        except VideoError:
            raise
        except Exception as exc:  # noqa: BLE001 - envolver errores inesperados
            self._logger.exception("Error no controlado en el renderizador.")
            raise VideoRendererError(
                f"Error no controlado en el renderizador: {exc}"
            ) from exc

        self._logger.debug(
            "Video renderizado: %d bytes (modelo '%s').",
            len(result.content),
            result.renderer,
        )
        return result
