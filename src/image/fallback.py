"""Orquestación MÍNIMA de fallback entre providers de imagen (uso interno).

Este módulo NO forma parte del contrato público de ``image`` y no debe usarse
fuera del pipeline: implementa exactamente UN intento de fallback POR EJECUCIÓN
entre un provider primario y uno secundario, sin retry, backoff ni circuit
breaker.

Comportamiento:

- El provider primario genera todas las escenas.
- Si una escena falla con un error clasificado como recuperable
  (:func:`is_recoverable_error`) y existe un provider de fallback, se activa el
  fallback para las escenas restantes, incluida la escena que falló.
- Las escenas ya generadas por el primario NO se regeneran (límite documentado
  de esta versión).
- Máximo UN fallback por ejecución: si el provider secundario también falla, el
  error se propaga y la ejecución termina en error.
- SyntheticImageProvider nunca entra en la cadena de forma automática: solo se
  usa si la configuración lo selecciona explícitamente como primario o fallback.

Los operadores pueden ser :class:`image.base.ImageProvider` o
:class:`image.adapter.ImageAdapter` (cualquier objeto con ``generate(request)``
que devuelva un :class:`image.base.ImageResult`); el nombre/modelo se deriva de
``provider`` o ``provider.provider`` según lo que exista.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .base import ImageProvider, ImageRequest, ImageResult
from .exceptions import ImageError, ImageErrorCode

logger = logging.getLogger(__name__)

#: Códigos de error considerados recuperables (candidatos a fallback).
#:
#: - ``RATE_LIMIT``: cuota temporal del proveedor primario.
#: - ``TIMEOUT`` / ``NETWORK``: fallos de transporte, transitorios.
#: - ``SERVER_ERROR``: error 5xx del proveedor, transitorio.
#: - ``EMPTY_RESPONSE`` / ``INVALID_RESPONSE``: respuesta vacía o inválida
#:   identificable como fallo transitorio del proveedor.
#:
#: NO se incluyen ``BAD_REQUEST``, ``AUTH``, ``PERMISSION``,
#: ``MODEL_NOT_FOUND``, ``FILTERED``, ``UNEXPECTED`` ni ``UNKNOWN``: indican
#: errores de solicitud, configuración, permisos o programación que el fallback
#: no debe enmascarar.
RECOVERABLE_ERROR_CODES: frozenset[ImageErrorCode] = frozenset(
    {
        ImageErrorCode.RATE_LIMIT,
        ImageErrorCode.TIMEOUT,
        ImageErrorCode.NETWORK,
        ImageErrorCode.SERVER_ERROR,
        ImageErrorCode.EMPTY_RESPONSE,
        ImageErrorCode.INVALID_RESPONSE,
    }
)


def is_recoverable_error(exc: ImageError) -> bool:
    """Indica si un :class:`ImageError` clasificado es candidato a fallback.

    Args:
        exc: excepción de imagen a evaluar.

    Returns:
        ``True`` solo si el ``error_code`` de la excepción está en
        :data:`RECOVERABLE_ERROR_CODES`.
    """
    return exc.error_code in RECOVERABLE_ERROR_CODES


def _label(operator: Any) -> tuple[str, str]:
    """Deriva (nombre, modelo) de un provider o de un ImageAdapter."""
    provider = getattr(operator, "provider", None) or operator
    name = getattr(provider, "name", None) or type(operator).__name__
    model = getattr(provider, "model", None) or "?"
    return name, model


def make_generate_with_fallback(
    primary: ImageProvider,
    fallback: ImageProvider | None,
    *,
    log: logging.Logger = logger,
) -> Callable[[ImageRequest], ImageResult]:
    """Devuelve ``generate(request) -> ImageResult`` con UN fallback por ejecución.

    Args:
        primary: operador primario (provider o ImageAdapter).
        fallback: operador secundario (provider o ImageAdapter), o ``None`` para
            deshabilitar el fallback.
        log: logger donde registrar la trazabilidad del fallback.

    Returns:
        Función que genera una imagen por solicitud; en caso de error recuperable
        del primario y con fallback configurado, cambia al fallback para el resto
        de la ejecución (máximo una vez) y reintenta la escena fallida.

    Raises:
        ImageError: si el primario falla sin fallback disponible, o si el
            fallback también falla. Los errores no recuperables del primario se
            propagan sin intentar el fallback.
    """
    active: ImageProvider = primary
    switched: bool = False

    def generate(request: ImageRequest) -> ImageResult:
        nonlocal active, switched
        try:
            return active.generate(request)
        except ImageError as exc:
            if not switched and fallback is not None and is_recoverable_error(exc):
                switched = True
                name, model = _label(primary)
                fallback_name, fallback_model = _label(fallback)
                log.warning(
                    "Provider primario '%s' (modelo '%s') falló con error "
                    "recuperable (%s): %s",
                    name,
                    model,
                    exc.error_code.value,
                    exc,
                )
                log.info("Activando fallback: '%s' (modelo '%s').", fallback_name, fallback_model)
                active = fallback
                log.info("Provider activo: '%s' (modelo '%s').", fallback_name, fallback_model)
                return active.generate(request)
            raise

    return generate
