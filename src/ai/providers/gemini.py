"""Proveedor oficial de Google Gemini.

Implementa :class:`ai.base.BaseAIProvider` sobre el SDK oficial
``google-genai``. La clave se lee de la variable de entorno
``GEMINI_API_KEY`` (o se pasa explícitamente al constructor) y los errores de
la API se traducen a la jerarquía de excepciones de ``ai.exceptions``.

El import de ``google.genai`` es diferido para que este módulo pueda
importarse sin que el SDK esté instalado (la dependencia solo se requiere al
instanciar el proveedor).
"""

from __future__ import annotations

import enum
import logging
import os
from typing import Any, Optional, Type

from ..base import BaseAIProvider, GenerationOptions, GenerationResult
from ..exceptions import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    ContentBlockedError,
    ProviderError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

#: Nombre de la variable de entorno que debe contener la API key.
API_KEY_ENV_VAR = "GEMINI_API_KEY"

#: Marcadores de valor sin completar (no usar la clave en estos casos).
_PLACEHOLDER_MARKERS = ("REEMPLAZAR", "TU_API_KEY")


class GeminiProvider(BaseAIProvider):
    """Proveedor de Google Gemini (Google AI Studio).

    Args:
        model: modelo de Gemini a utilizar (por defecto ``gemini-3.6-flash``).
        api_key: clave de API. Si es ``None`` se lee de la variable de
            entorno ``GEMINI_API_KEY``.
        options: parámetros de generación opcionales.

    Raises:
        ConfigurationError: si no hay clave configurada, es un placeholder o
            el SDK ``google-genai`` no está instalado.
    """

    name: str = "gemini"

    def __init__(
        self,
        model: str = "gemini-3.6-flash",
        *,
        api_key: Optional[str] = None,
        options: Optional[GenerationOptions] = None,
    ) -> None:
        super().__init__(model, options=options)
        self._logger = logging.getLogger(f"{__name__}.GeminiProvider")

        api_key = api_key or os.environ.get(API_KEY_ENV_VAR)
        if not api_key or any(marker in api_key for marker in _PLACEHOLDER_MARKERS):
            raise ConfigurationError(
                f"'{API_KEY_ENV_VAR}' no está configurada o es un placeholder. "
                "Defínela en el entorno (o en un archivo .env) con una clave real."
            )

        try:
            from google import genai  # import diferido para no acoplar el import
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise ConfigurationError(
                "No se encontró el SDK 'google-genai'. Instálalo con: "
                "pip install google-genai"
            ) from exc

        self._client = genai.Client(api_key=api_key)
        self._logger.debug("Proveedor Gemini inicializado (modelo='%s').", self.model)

    def generate(self, prompt: str, **kwargs: Any) -> GenerationResult:
        """Genera texto con Gemini a partir de un prompt.

        Args:
            prompt: texto de entrada. No debe estar vacío.
            **kwargs: sobrescribe parámetros de generación (``temperature``,
                ``max_output_tokens``, ``top_p``, ``top_k``).

        Returns:
            :class:`GenerationResult` con el texto generado.

        Raises:
            ValueError: si el prompt está vacío o en blanco.
            ai.exceptions.AuthenticationError: clave inválida o sin permisos.
            ai.exceptions.RateLimitError: se superó la cuota.
            ai.exceptions.APIError: otro error de la API.
            ai.exceptions.ContentBlockedError: el modelo no devolvió texto.
        """
        if not prompt or not prompt.strip():
            raise ValueError("El prompt no puede estar vacío.")

        config = self._build_generation_config(kwargs)
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
        except self._client_error_type() as exc:
            raise self._map_client_error(exc) from exc
        except Exception as exc:  # noqa: BLE001 - envolver errores inesperados
            self._logger.exception("Error inesperado al llamar a Gemini.")
            raise APIError(f"Error inesperado al llamar a Gemini: {exc}") from exc

        text = getattr(response, "text", None)
        if not text or not text.strip():
            raise ContentBlockedError(
                "Gemini no devolvió texto. El contenido pudo ser bloqueado por "
                "políticas de seguridad o la respuesta está vacía."
            )

        return GenerationResult(
            text=text,
            model=self.model,
            finish_reason=self._extract_finish_reason(response),
            usage=self._extract_usage(response),
        )

    # ------------------------------------------------------------------
    # Ayudantes privados
    # ------------------------------------------------------------------

    def _build_generation_config(self, overrides: dict[str, Any]) -> Any:
        """Construye la configuración de generación combinando opciones base
        con los sobrescritos por ``kwargs``."""
        from google.genai import types

        params: dict[str, Any] = {}
        options = self.options
        merged = {
            "temperature": overrides.get("temperature", options.temperature),
            "top_p": overrides.get("top_p", options.top_p),
            "top_k": overrides.get("top_k", options.top_k),
            "max_output_tokens": overrides.get(
                "max_output_tokens", options.max_output_tokens
            ),
        }
        for key, value in merged.items():
            if value is not None:
                params[key] = value
        return types.GenerateContentConfig(**params) if params else None

    def _client_error_type(self) -> Type[Exception]:
        """Devuelve la clase de error HTTP del SDK (import diferido)."""
        from google.genai import errors

        return errors.ClientError

    def _map_client_error(self, exc: Exception) -> ProviderError:
        """Traduce un ``ClientError`` del SDK a la jerarquía de ``ai``."""
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or str(exc)
        status = getattr(exc, "status", None)
        self._logger.warning(
            "Gemini devolvió un error: code=%s status=%s message=%s",
            code,
            status,
            message,
        )
        if code in (401, 403):
            return AuthenticationError(
                f"Error de autenticación con Gemini (HTTP {code}): {message}",
                code=code,
                status=status,
            )
        if code == 429:
            return RateLimitError(
                f"Cuota o límite de solicitudes de Gemini superado (HTTP 429): {message}",
                code=code,
                status=status,
            )
        return APIError(
            f"Error del Gemini API (HTTP {code}): {message}",
            code=code,
            status=status,
        )

    def _extract_finish_reason(self, response: Any) -> Optional[str]:
        """Extrae el motivo de finalización del primer candidato, si existe."""
        candidates = getattr(response, "candidates", None)
        if not candidates:
            return None
        finish_reason = getattr(candidates[0], "finish_reason", None)
        if isinstance(finish_reason, enum.Enum):
            return finish_reason.value
        return finish_reason if finish_reason is not None else None

    def _extract_usage(self, response: Any) -> dict[str, int]:
        """Extrae las métricas de uso de tokens, si la API las reporta."""
        usage_metadata = getattr(response, "usage_metadata", None)
        if usage_metadata is None:
            return {}
        usage: dict[str, int] = {}
        for attr in (
            "prompt_token_count",
            "candidates_token_count",
            "total_token_count",
        ):
            value = getattr(usage_metadata, attr, None)
            if value is not None:
                usage[attr] = int(value)
        return usage
