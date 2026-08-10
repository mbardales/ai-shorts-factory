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

import enum


class ImageErrorCode(str, enum.Enum):
    """Clasificación mínima y estable de la causa de un error de imagen.

    No sustituye al mensaje (que se conserva íntegro para debugging): aporta
    un código estructurado para que la orquestación pueda decidir si un error
    es recuperable mediante fallback. ``UNKNOWN`` es el valor por defecto y
    significa "no clasificado".
    """

    UNKNOWN = "UNKNOWN"
    BAD_REQUEST = "BAD_REQUEST"
    AUTH = "AUTH"
    PERMISSION = "PERMISSION"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    SERVER_ERROR = "SERVER_ERROR"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    FILTERED = "FILTERED"
    UNEXPECTED = "UNEXPECTED"


class ImageError(Exception):
    """Error base de todas las excepciones del módulo ``image``.

    Args:
        message: descripción del error.
        error_code: clasificación estructurada de la causa
            (:class:`ImageErrorCode`). Por defecto ``UNKNOWN``.
    """

    def __init__(
        self,
        message: str = "",
        *,
        error_code: ImageErrorCode = ImageErrorCode.UNKNOWN,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code


class ImageProviderError(ImageError):
    """Error ocurrido en un proveedor de imágenes o al comunicarse con él."""


class ImageGenerationError(ImageError):
    """El proveedor no produjo una imagen válida para la solicitud."""
