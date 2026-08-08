"""Adaptador de audio desacoplado del proveedor concreto.

``AudioAdapter`` envuelve un proveedor que implementa :class:`audio.base.AudioProvider`
y expone operaciones orientadas al dominio (``generate``). El adaptador no
conoce el SDK ni la API del proveedor: depende únicamente del contrato base y
traduce los errores no controlados a la jerarquía de ``audio.exceptions``.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import AudioProvider, AudioRequest, AudioResult
from .exceptions import AudioError, AudioProviderError

logger = logging.getLogger(__name__)


class AudioAdapter:
    """Envoltorio orientado al dominio que delega en un :class:`AudioProvider`.

    Args:
        provider: implementación concreta de un proveedor de audio.

    Raises:
        TypeError: si ``provider`` no implementa ``AudioProvider``.
    """

    def __init__(self, provider: AudioProvider) -> None:
        if not isinstance(provider, AudioProvider):
            raise TypeError(
                f"Se esperaba una instancia de AudioProvider, se recibió "
                f"{type(provider).__name__}."
            )
        self._provider = provider
        self._logger = logging.getLogger(f"{__name__}.AudioAdapter")

    @property
    def provider(self) -> AudioProvider:
        """Proveedor de audio subyacente."""
        return self._provider

    def generate(self, request: AudioRequest, **kwargs: Any) -> AudioResult:
        """Sintetiza audio a partir de una :class:`AudioRequest`.

        Args:
            request: solicitud del audio a generar.
            **kwargs: parámetros de generación adicionales.

        Returns:
            :class:`AudioResult` con la pista de audio generada.

        Raises:
            AudioError: cualquier error del proveedor.
            AudioProviderError: error no controlado del proveedor.
        """
        self._logger.info(
            "Sintetizando audio con proveedor '%s' (modelo '%s').",
            self._provider.name,
            self._provider.model,
        )
        try:
            result = self._provider.generate(request, **kwargs)
        except AudioError:
            raise
        except Exception as exc:  # noqa: BLE001 - envolver errores inesperados
            self._logger.exception("Error no controlado en el proveedor.")
            raise AudioProviderError(
                f"Error no controlado en el proveedor: {exc}"
            ) from exc

        self._logger.debug(
            "Pista generada: %d bytes (modelo '%s').",
            len(result.content),
            result.model,
        )
        return result
