"""Jerarquía de excepciones del Video Core de AI Shorts Factory.

Todas las excepciones del paquete ``video`` derivan de :class:`VideoError` para
que los consumidores puedan capturar errores de renderizado de forma homogénea
y, a la vez, con distinción por causa.

Jerarquía:

- ``VideoError``: base genérica.
  - ``VideoRendererError``: error de un renderizador de video (ejecución,
    binario, entrada inválida, tiempo de espera).
  - ``VideoRenderError``: el renderizador no produjo un video válido.
"""

from __future__ import annotations


class VideoError(Exception):
    """Error base de todas las excepciones del módulo ``video``."""


class VideoRendererError(VideoError):
    """Error ocurrido en un renderizador de video o al comunicarse con él."""


class VideoRenderError(VideoError):
    """El renderizador no produjo un video válido para la solicitud."""
