"""Jerarquía de excepciones del Image Core de AI Shorts Factory.

Todas las excepciones del paquete ``image`` derivan de :class:`ImageError` para
que los consumidores puedan capturar errores de generación de imágenes de forma
homogénea y, a la vez, con distinción por causa.

Jerarquía:

- ``ImageError``: base genérica.
  - ``ImageProviderError``: error del proveedor de imágenes (red, API,
    autenticación, tiempo de espera).
  - ``ImageGenerationError``: el modelo no pudo generar la imagen solicitada
    (contenido bloqueado, respuesta vacía o inválida).
"""

from __future__ import annotations


class ImageError(Exception):
    """Error base de todas las excepciones del módulo ``image``."""


class ImageProviderError(ImageError):
    """Error ocurrido en un proveedor de imágenes o al comunicarse con él."""


class ImageGenerationError(ImageError):
    """El proveedor no produjo una imagen válida para la solicitud."""
