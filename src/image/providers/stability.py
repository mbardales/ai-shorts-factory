"""Proveedor oficial de Stability AI (Stable Image Core).

Implementa :class:`image.base.ImageProvider` sobre la API REST v2beta de
Stability AI (endpoint ``/v2beta/stable-image/generate/core``). La clave se
lee de la variable de entorno ``STABILITY_API_KEY`` (o se pasa explícitamente
al constructor) y los errores de la API se traducen a la jerarquía de
``image.exceptions``.

La petición se envía como ``multipart/form-data`` usando únicamente la
biblioteca estándar (``urllib.request``) para no añadir dependencias HTTP al
proyecto. El proveedor devuelve un :class:`image.base.ImageResult` con los
bytes de la imagen y sus metadatos; no escribe archivos ni conoce el
directorio de salida del pipeline.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from ..base import ImageOptions, ImageProvider, ImageRequest, ImageResult
from ..exceptions import ImageErrorCode, ImageGenerationError, ImageProviderError
from media import ImageMetadata
from media.paths import sanitize_component

logger = logging.getLogger(__name__)

#: Nombre de la variable de entorno que debe contener la API key.
API_KEY_ENV_VAR = "STABILITY_API_KEY"

#: Marcadores de valor sin completar (no usar la clave en estos casos).
_PLACEHOLDER_MARKERS = ("REEMPLAZAR", "TU_API_KEY", "YOUR_")

#: Servicio/modelo de Stability (el endpoint del servicio es fijo).
DEFAULT_MODEL = "stable-image-core"

#: Endpoint oficial de Stable Image Core (REST v2beta).
DEFAULT_ENDPOINT = "https://api.stability.ai/v2beta/stable-image/generate/core"

#: Tiempo máximo por petición HTTP en segundos.
DEFAULT_TIMEOUT_SECONDS = 60

#: Formato de salida por defecto (PNG, detectable por generate_manifest.py).
DEFAULT_OUTPUT_FORMAT = "png"

#: Proporción de aspecto por defecto (YouTube Shorts verticales, 9:16).
DEFAULT_ASPECT_RATIO = "9:16"


class StabilityImageProvider(ImageProvider):
    """Proveedor de imágenes de Stability AI (Stable Image Core).

    Args:
        model: identificador del servicio/modelo. Por defecto
            ``"stable-image-core"`` (el endpoint del servicio es fijo).
        api_key: clave de API. Si es ``None`` se lee de la variable de
            entorno ``STABILITY_API_KEY``.
        options: parámetros de generación opcionales (``seed``).
        timeout: tiempo máximo de espera por petición HTTP (segundos).

    Raises:
        ImageProviderError: si no hay clave configurada, es un placeholder o
            el timeout no es un número positivo.
    """

    name: str = "stability-image"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: Optional[str] = None,
        options: Optional[ImageOptions] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(model, options=options)
        self._logger = logging.getLogger(f"{__name__}.StabilityImageProvider")
        self.endpoint = DEFAULT_ENDPOINT

        try:
            self.timeout = float(timeout)
        except (TypeError, ValueError):
            raise ImageProviderError(
                f"El timeout debe ser un número en segundos; se recibió {timeout!r}."
            ) from None
        if self.timeout <= 0:
            raise ImageProviderError(
                f"El timeout debe ser positivo; se recibió {timeout!r}."
            )

        api_key = api_key or os.environ.get(API_KEY_ENV_VAR)
        if not api_key or any(marker in api_key for marker in _PLACEHOLDER_MARKERS):
            raise ImageProviderError(
                f"'{API_KEY_ENV_VAR}' no está configurada o es un placeholder. "
                "Defínela en el entorno (o en un archivo .env) con una clave real."
            )
        self._api_key = api_key
        self._logger.debug(
            "Proveedor Stability Images inicializado (modelo='%s').", self.model
        )

    def generate(self, request: ImageRequest, **kwargs: Any) -> ImageResult:
        """Genera una imagen a partir de una :class:`ImageRequest`.

        Args:
            request: solicitud de la imagen a generar.
            **kwargs: sobrescribe parámetros de generación (``seed``).

        Returns:
            :class:`ImageResult` con los bytes de la imagen y sus metadatos.

        Raises:
            ValueError: si el prompt está vacío o en blanco.
            image.exceptions.ImageProviderError: error de la API, de red o de
                configuración.
            image.exceptions.ImageGenerationError: el servicio no devolvió una
                imagen válida (respuesta vacía).
        """
        if not request.prompt or not request.prompt.strip():
            raise ValueError("El prompt de la imagen no puede estar vacío.")

        options = self.resolve_options(**kwargs)
        fields = self._build_fields(request, options)
        body, boundary = self._encode_multipart(fields)
        headers = self._build_headers(boundary)

        req = urllib_request.Request(
            self.endpoint, data=body, headers=headers, method="POST"
        )
        try:
            with urllib_request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                content = resp.read()
                content_type = (
                    resp.headers.get("Content-Type", "") if resp.headers else ""
                )
        except urllib_error.HTTPError as exc:
            raise self._map_http_error(exc) from exc
        except TimeoutError as exc:
            self._logger.warning(
                "Timeout (%.1fs) al llamar a Stability AI.", self.timeout
            )
            raise ImageProviderError(
                f"Tiempo de espera agotado al llamar a Stability AI "
                f"(HTTP timeout {self.timeout:.0f}s).",
                error_code=ImageErrorCode.TIMEOUT,
            ) from exc
        except urllib_error.URLError as exc:
            self._logger.warning("Error de red al llamar a Stability AI: %s", exc.reason)
            raise ImageProviderError(
                f"Error de red al llamar a Stability AI: {exc.reason}",
                error_code=ImageErrorCode.NETWORK,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - envolver errores inesperados
            self._logger.exception("Error inesperado al llamar a Stability AI.")
            raise ImageProviderError(
                f"Error inesperado al llamar a Stability AI: {exc}",
                error_code=ImageErrorCode.UNEXPECTED,
            ) from exc

        if not content:
            raise ImageGenerationError(
                "Stability AI no devolvió bytes de imagen (HTTP 200 vacío).",
                error_code=ImageErrorCode.EMPTY_RESPONSE,
            )
        mime_type = self._normalize_mime_type(content_type)
        metadata = self._build_metadata(request, content, mime_type)
        return ImageResult(
            prompt=request.prompt,
            content=content,
            metadata=metadata,
            model=self.model,
            provider=self.name,
        )

    # ------------------------------------------------------------------
    # Ayudantes privados
    # ------------------------------------------------------------------

    def _build_fields(
        self, request: ImageRequest, options: ImageOptions
    ) -> dict[str, Any]:
        """Construye los campos ``multipart/form-data`` de Stable Image Core.

        Solo se incluyen parámetros realmente soportados por el contrato y la
        API: ``prompt``, ``aspect_ratio``, ``output_format``, y opcionalmente
        ``negative_prompt`` y ``seed``.
        """
        fields: dict[str, Any] = {
            "prompt": request.prompt,
            "aspect_ratio": DEFAULT_ASPECT_RATIO,
            "output_format": DEFAULT_OUTPUT_FORMAT,
        }
        aspect = getattr(request, "aspect_ratio", None)
        value = getattr(aspect, "value", None)
        if value:
            fields["aspect_ratio"] = value
        if getattr(request, "negative_prompt", None):
            fields["negative_prompt"] = request.negative_prompt
        if options.seed is not None:
            fields["seed"] = str(options.seed)
        return fields

    @staticmethod
    def _encode_multipart(fields: dict[str, Any]) -> tuple[bytes, str]:
        """Codifica un dict de campos como cuerpo ``multipart/form-data``."""
        boundary = "----ai-shorts-factory-" + uuid.uuid4().hex
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.append(f"--{boundary}\r\n".encode("ascii"))
            chunks.append(
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii")
            )
            chunks.append(str(value).encode("utf-8"))
            chunks.append(b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode("ascii"))
        return b"".join(chunks), boundary

    def _build_headers(self, boundary: str) -> dict[str, str]:
        """Cabeceras HTTP de la petición (sin registrar nunca la API key)."""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "image/*",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }

    def _map_http_error(self, exc: urllib_error.HTTPError) -> ImageProviderError:
        """Traduce un ``HTTPError`` del SDK a :class:`ImageProviderError`."""
        code = getattr(exc, "code", None)
        status = getattr(exc, "status", None)
        message = self._safe_error_message(exc)
        self._logger.warning(
            "Stability AI devolvió un error: code=%s status=%s message=%s",
            code,
            status,
            message,
        )
        if code == 401:
            causa = "de autenticación (HTTP 401)"
            error_code = ImageErrorCode.AUTH
        elif code == 403:
            causa = "de permiso (HTTP 403)"
            error_code = ImageErrorCode.PERMISSION
        elif code == 404:
            causa = f"del API (HTTP {code})"
            error_code = ImageErrorCode.MODEL_NOT_FOUND
        elif code in (400, 422):
            causa = f"de solicitud (HTTP {code})"
            error_code = ImageErrorCode.BAD_REQUEST
        elif code == 429:
            causa = "de cuota o límite de solicitudes (HTTP 429)"
            error_code = ImageErrorCode.RATE_LIMIT
        elif code is not None and 500 <= code < 600:
            causa = f"del servicio (HTTP {code})"
            error_code = ImageErrorCode.SERVER_ERROR
        else:
            causa = f"del API (HTTP {code})"
            error_code = ImageErrorCode.UNKNOWN
        return ImageProviderError(
            f"Error {causa} de Stability AI: {message}",
            error_code=error_code,
        )

    @staticmethod
    def _safe_error_message(exc: urllib_error.HTTPError, *, limit: int = 500) -> str:
        """Extrae un mensaje de error del cuerpo HTTP (nunca contiene secretos)."""
        try:
            raw = exc.read(limit) if hasattr(exc, "read") else b""
        except Exception:  # noqa: BLE001 - lectura del cuerpo fallible
            raw = b""
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            text = str(getattr(exc, "reason", "") or exc)
        return text[:limit]

    @staticmethod
    def _normalize_mime_type(content_type: str) -> str:
        """Normaliza el ``Content-Type`` de la respuesta (sin parámetros)."""
        ctype = (content_type or "").split(";")[0].strip().lower()
        return ctype or "image/png"

    def _build_metadata(
        self, request: ImageRequest, content: bytes, mime_type: str
    ) -> ImageMetadata:
        """Construye los metadatos de la imagen generada (Media Core).

        Las dimensiones reales las reporta Stability en el contenido binario;
        el contrato permite metadatos parciales, así que no se inventan aquí.
        """
        return ImageMetadata(
            name=sanitize_component(request.prompt[:60], fallback="imagen"),
            mime_type=mime_type,
            size_bytes=len(content),
            width=None,
            height=None,
            format=DEFAULT_OUTPUT_FORMAT,
        )
