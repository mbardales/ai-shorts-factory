"""Proveedor de síntesis de voz de Google (Gemini TTS).

Implementa :class:`audio.base.AudioProvider` sobre el SDK oficial
``google-genai`` (API ``generate_content`` con los modelos TTS de Gemini, p. ej.
``gemini-3.1-flash-tts-preview``). La clave se lee de la variable de entorno
``GEMINI_API_KEY`` (o se pasa explícitamente al constructor) y los errores de la
API se traducen a la jerarquía de ``audio.exceptions``.

Reutiliza Media Core para los metadatos del resultado
(:class:`media.AudioMetadata`).

El import de ``google.genai`` es diferido para que este módulo pueda
importarse sin que el SDK esté instalado (la dependencia solo se requiere al
instanciar el proveedor).
"""

from __future__ import annotations

import enum
import logging
import os
from typing import Any, Optional, Type

from ..base import AudioFormat, AudioProvider, AudioRequest, AudioResult, VoiceSettings
from ..exceptions import AudioGenerationError, AudioProviderError
from media import AudioMetadata
from media.paths import sanitize_component

logger = logging.getLogger(__name__)

#: Nombre de la variable de entorno que debe contener la API key.
API_KEY_ENV_VAR = "GEMINI_API_KEY"

#: Marcadores de valor sin completar (no usar la clave en estos casos).
_PLACEHOLDER_MARKERS = ("REEMPLAZAR", "TU_API_KEY")

#: Modelo TTS de Gemini por defecto.
DEFAULT_TTS_MODEL = "gemini-3.1-flash-tts-preview"

#: Modalidad de respuesta que solicita audio en ``generate_content``.
_RESPONSE_MODALITY = "AUDIO"

#: Tipo MIME por defecto cuando la API no lo reporta.
_DEFAULT_MIME_TYPE = "audio/wav"


class GoogleTTSProvider(AudioProvider):
    """Proveedor de síntesis de voz de Google (Gemini TTS).

    Args:
        model: modelo TTS de Gemini a utilizar (por defecto
            ``gemini-3.1-flash-tts-preview``).
        api_key: clave de API. Si es ``None`` se lee de la variable de
            entorno ``GEMINI_API_KEY``.
        voice: configuración de voz opcional (``name``, ``language``).

    Raises:
        AudioProviderError: si no hay clave configurada, es un placeholder o
            el SDK ``google-genai`` no está instalado.
    """

    name: str = "google-tts"

    def __init__(
        self,
        model: str = DEFAULT_TTS_MODEL,
        *,
        api_key: Optional[str] = None,
        voice: Optional[VoiceSettings] = None,
    ) -> None:
        super().__init__(model, voice=voice)
        self._logger = logging.getLogger(f"{__name__}.GoogleTTSProvider")

        api_key = api_key or os.environ.get(API_KEY_ENV_VAR)
        if not api_key or any(marker in api_key for marker in _PLACEHOLDER_MARKERS):
            raise AudioProviderError(
                f"'{API_KEY_ENV_VAR}' no está configurada o es un placeholder. "
                "Defínela en el entorno (o en un archivo .env) con una clave real."
            )

        try:
            from google import genai  # import diferido para no acoplar el import
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise AudioProviderError(
                "No se encontró el SDK 'google-genai'. Instálalo con: "
                "pip install google-genai"
            ) from exc

        self._client = genai.Client(api_key=api_key)
        self._logger.debug(
            "Proveedor Gemini TTS inicializado (modelo='%s').", self.model
        )

    def generate(self, request: AudioRequest, **kwargs: Any) -> AudioResult:
        """Sintetiza audio a partir de una :class:`AudioRequest`.

        Args:
            request: solicitud del audio a generar.
            **kwargs: sobrescribe configuración de voz (``name``, ``language``,
                ``pitch``, ``rate``, ``volume_gain_db``). ``pitch``, ``rate`` y
                ``volume_gain_db`` no aplican a los modelos TTS de Gemini vía
                ``generate_content`` y se ignoran.

        Returns:
            :class:`AudioResult` con los bytes del audio y sus metadatos.

        Raises:
            ValueError: si el texto está vacío o en blanco.
            audio.exceptions.AudioProviderError: error de la API o no
                controlado.
            audio.exceptions.AudioGenerationError: el modelo no devolvió una
                pista válida (respuesta vacía o bloqueada).
        """
        if not request.text or not request.text.strip():
            raise ValueError("El texto de la narración no puede estar vacío.")

        voice = self._merge_voice(request.voice or self.voice, **kwargs)
        config = self._build_speech_config(request, voice)
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=request.text,
                config=config,
            )
        except self._client_error_type() as exc:
            raise self._map_client_error(exc) from exc
        except Exception as exc:  # noqa: BLE001 - envolver errores inesperados
            self._logger.exception("Error inesperado al llamar a Gemini TTS.")
            raise AudioProviderError(
                f"Error inesperado al llamar a Gemini TTS: {exc}"
            ) from exc

        content, mime_type = self._extract_audio(response)
        metadata = self._build_metadata(request, content, mime_type)
        return AudioResult(
            text=request.text,
            content=content,
            metadata=metadata,
            model=self.model,
        )

    # ------------------------------------------------------------------
    # Ayudantes privados
    # ------------------------------------------------------------------

    def _merge_voice(self, base: VoiceSettings, **kwargs: Any) -> VoiceSettings:
        """Combina una voz base con los sobrescritos de ``kwargs``.

        La voz efectiva prioriza ``request.voice`` (si existe), luego la voz
        base del proveedor, y por último los sobrescritos por ``kwargs``.
        """
        return VoiceSettings(
            name=kwargs.get("name", base.name),
            language=kwargs.get("language", base.language),
            pitch=kwargs.get("pitch", base.pitch),
            rate=kwargs.get("rate", base.rate),
            volume_gain_db=kwargs.get("volume_gain_db", base.volume_gain_db),
        )

    def _build_speech_config(
        self, request: AudioRequest, voice: VoiceSettings
    ) -> Any:
        """Construye la configuración de síntesis de voz de Gemini TTS."""
        from google.genai import types

        params: dict[str, Any] = {"response_modalities": [_RESPONSE_MODALITY]}
        speech_params: dict[str, Any] = {}
        if voice.name:
            speech_params["voice_config"] = types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice.name
                )
            )
        if voice.language:
            speech_params["language_code"] = voice.language
        if speech_params:
            params["speech_config"] = types.SpeechConfig(**speech_params)
        if any(
            value is not None
            for value in (voice.pitch, voice.rate, voice.volume_gain_db)
        ):
            self._logger.debug(
                "pitch/rate/volume_gain_db no aplican a Gemini TTS vía "
                "generate_content y se ignoran."
            )
        return types.GenerateContentConfig(**params)

    def _client_error_type(self) -> Type[Exception]:
        """Devuelve la clase de error HTTP del SDK (import diferido)."""
        from google.genai import errors

        return errors.ClientError

    def _map_client_error(self, exc: Exception) -> AudioProviderError:
        """Traduce un ``ClientError`` del SDK a :class:`AudioProviderError`."""
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or str(exc)
        status = getattr(exc, "status", None)
        self._logger.warning(
            "Gemini TTS devolvió un error: code=%s status=%s message=%s",
            code,
            status,
            message,
        )
        return AudioProviderError(
            f"Error del Gemini TTS API (HTTP {code}): {message}"
        )

    def _extract_audio(self, response: Any) -> tuple[bytes, str]:
        """Extrae los bytes y el tipo MIME de la pista de audio generada.

        Raises:
            AudioGenerationError: si no hay audio en la respuesta o está
                bloqueada por políticas de seguridad.
        """
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            for part in (getattr(content, "parts", None) or []):
                blob = getattr(part, "inline_data", None)
                data = getattr(blob, "data", None)
                if data:
                    mime_type = getattr(blob, "mime_type", None) or _DEFAULT_MIME_TYPE
                    return bytes(data), mime_type
        finish_reason = self._extract_finish_reason(response)
        if finish_reason:
            raise AudioGenerationError(
                f"Gemini TTS no devolvió audio (finish_reason={finish_reason}). "
                "El contenido pudo ser bloqueado por políticas de seguridad."
            )
        raise AudioGenerationError("Gemini TTS no devolvió audio en la respuesta.")

    def _extract_finish_reason(self, response: Any) -> Optional[str]:
        """Extrae el motivo de finalización del primer candidato, si existe."""
        candidates = getattr(response, "candidates", None)
        if not candidates:
            return None
        finish_reason = getattr(candidates[0], "finish_reason", None)
        if isinstance(finish_reason, enum.Enum):
            return finish_reason.value
        return finish_reason if finish_reason is not None else None

    def _build_metadata(
        self, request: AudioRequest, content: bytes, mime_type: str
    ) -> AudioMetadata:
        """Construye los metadatos de la pista generada (Media Core)."""
        return AudioMetadata(
            name=sanitize_component(request.text[:60], fallback="narracion"),
            mime_type=mime_type,
            size_bytes=len(content),
        )
