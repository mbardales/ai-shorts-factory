"""Proveedor oficial de imágenes de Google Gemini.

Implementa :class:`image.base.ImageProvider` sobre el SDK oficial
``google-genai`` (API ``models.generate_images``, modelos Imagen). La clave se
lee de la variable de entorno ``GEMINI_API_KEY`` (o se pasa explícitamente al
constructor) y los errores de la API se traducen a la jerarquía de
``image.exceptions``.

Reutiliza Media Core para los metadatos del resultado
(:class:`media.ImageMetadata`) y, opcionalmente, para persistir la imagen
generada en un :class:`media.LocalStorage`.

El import de ``google.genai`` es diferido para que este módulo pueda
importarse sin que el SDK esté instalado (la dependencia solo se requiere al
instanciar el proveedor).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional, Type

from ..base import ImageOptions, ImageProvider, ImageRequest, ImageResult
from ..exceptions import ImageGenerationError, ImageProviderError
from media import ImageMetadata, MediaKind
from media.paths import build_asset_rel_path, sanitize_component
from media.storage import LocalStorage

logger = logging.getLogger(__name__)

#: Nombre de la variable de entorno que debe contener la API key.
API_KEY_ENV_VAR = "GEMINI_API_KEY"

#: Marcadores de valor sin completar (no usar la clave en estos casos).
_PLACEHOLDER_MARKERS = ("REEMPLAZAR", "TU_API_KEY")

#: Modelo por defecto de generación de imágenes (familia Imagen).
DEFAULT_IMAGE_MODEL = "imagen-3.0-generate-002"

#: Cantidad de imágenes por solicitud.
_NUMBER_OF_IMAGES = 1


class GeminiImageProvider(ImageProvider):
    """Proveedor de imágenes de Google Gemini (Google AI Studio).

    Args:
        model: modelo de Imagen a utilizar (por defecto
            ``imagen-3.0-generate-002``).
        api_key: clave de API. Si es ``None`` se lee de la variable de
            entorno ``GEMINI_API_KEY``.
        options: parámetros de generación opcionales.
        storage: almacén de Media Core (opcional). Si se indica, la imagen
            generada puede persistirse con :meth:`store`.

    Raises:
        ImageProviderError: si no hay clave configurada, es un placeholder o
            el SDK ``google-genai`` no está instalado.
    """

    name: str = "gemini-image"

    def __init__(
        self,
        model: str = DEFAULT_IMAGE_MODEL,
        *,
        api_key: Optional[str] = None,
        options: Optional[ImageOptions] = None,
        storage: Optional[LocalStorage] = None,
    ) -> None:
        super().__init__(model, options=options)
        self._logger = logging.getLogger(f"{__name__}.GeminiImageProvider")
        self._storage = storage

        api_key = api_key or os.environ.get(API_KEY_ENV_VAR)
        if not api_key or any(marker in api_key for marker in _PLACEHOLDER_MARKERS):
            raise ImageProviderError(
                f"'{API_KEY_ENV_VAR}' no está configurada o es un placeholder. "
                "Defínela en el entorno (o en un archivo .env) con una clave real."
            )

        try:
            from google import genai  # import diferido para no acoplar el import
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise ImageProviderError(
                "No se encontró el SDK 'google-genai'. Instálalo con: "
                "pip install google-genai"
            ) from exc

        self._client = genai.Client(api_key=api_key)
        self._logger.debug(
            "Proveedor Gemini Images inicializado (modelo='%s').", self.model
        )

    def generate(self, request: ImageRequest, **kwargs: Any) -> ImageResult:
        """Genera una imagen a partir de una :class:`ImageRequest`.

        Args:
            request: solicitud de la imagen a generar.
            **kwargs: sobrescribe parámetros de generación (``seed``,
                ``steps``, ``guidance_scale``). ``steps`` no aplica a los
                modelos Imagen vía esta API y se ignora.

        Returns:
            :class:`ImageResult` con los bytes de la imagen y sus metadatos.

        Raises:
            ValueError: si el prompt está vacío o en blanco.
            image.exceptions.ImageProviderError: error de la API o no
                controlado.
            image.exceptions.ImageGenerationError: el modelo no devolvió una
                imagen válida (respuesta vacía o filtrada).
        """
        if not request.prompt or not request.prompt.strip():
            raise ValueError("El prompt de la imagen no puede estar vacío.")

        options = self.resolve_options(**kwargs)
        config = self._build_image_config(request, options)
        try:
            response = self._client.models.generate_images(
                model=self.model,
                prompt=request.prompt,
                config=config,
            )
        except self._client_error_type() as exc:
            raise self._map_client_error(exc) from exc
        except Exception as exc:  # noqa: BLE001 - envolver errores inesperados
            self._logger.exception("Error inesperado al llamar a Gemini Images.")
            raise ImageProviderError(
                f"Error inesperado al llamar a Gemini Images: {exc}"
            ) from exc

        content, mime_type = self._extract_image(response)
        metadata = self._build_metadata(request, content, mime_type)
        return ImageResult(
            prompt=request.prompt,
            content=content,
            metadata=metadata,
            model=self.model,
        )

    def store(
        self,
        result: ImageResult,
        *,
        content_id: str,
        name: Optional[str] = None,
        overwrite: bool = True,
    ) -> Path:
        """Persiste una imagen generada en el almacén de Media Core.

        Usa la nomenclatura del proyecto: ``assets/<kind>/<content_id>/<archivo>``.

        Args:
            result: resultado de :meth:`generate`.
            content_id: identificador del contenido de origen.
            name: descriptor del activo (por defecto, el nombre del metadato).
            overwrite: si ``False`` y el activo ya existe, no sobrescribe.

        Returns:
            Ubicación física del archivo escrito.

        Raises:
            ImageProviderError: si no se configuró un ``storage`` en el
                constructor.
        """
        storage = self._storage
        if storage is None:
            raise ImageProviderError(
                "No se configuró un almacén (storage) para persistir la imagen."
            )
        extension = result.metadata.format or "png"
        rel_path = build_asset_rel_path(
            content_id=content_id,
            name=name or result.metadata.name,
            ext=extension,
            kind=MediaKind.IMAGE,
        )
        path = storage.write_bytes(rel_path, result.content, overwrite=overwrite)
        self._logger.info("Imagen persistida en: %s", path)
        return path

    # ------------------------------------------------------------------
    # Ayudantes privados
    # ------------------------------------------------------------------

    def _build_image_config(self, request: ImageRequest, options: ImageOptions) -> Any:
        """Construye la configuración de generación de Imagen."""
        from google.genai import types

        params: dict[str, Any] = {"number_of_images": _NUMBER_OF_IMAGES}
        if request.width is not None and request.height is not None:
            params["image_size"] = f"{request.width}x{request.height}"
        else:
            params["aspect_ratio"] = request.aspect_ratio.value
        if request.negative_prompt:
            params["negative_prompt"] = request.negative_prompt
        if options.seed is not None:
            params["seed"] = options.seed
        if options.guidance_scale is not None:
            params["guidance_scale"] = options.guidance_scale
        return types.GenerateImagesConfig(**params)

    def _client_error_type(self) -> Type[Exception]:
        """Devuelve la clase de error HTTP del SDK (import diferido)."""
        from google.genai import errors

        return errors.ClientError

    def _map_client_error(self, exc: Exception) -> ImageProviderError:
        """Traduce un ``ClientError`` del SDK a :class:`ImageProviderError`."""
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or str(exc)
        status = getattr(exc, "status", None)
        self._logger.warning(
            "Gemini Images devolvió un error: code=%s status=%s message=%s",
            code,
            status,
            message,
        )
        if code in (401, 403):
            causa = "autenticación"
        elif code == 429:
            causa = "cuota o límite de solicitudes"
        else:
            causa = "del API"
        return ImageProviderError(
            f"Error {causa} de Gemini Images (HTTP {code}): {message}"
        )

    def _extract_image(self, response: Any) -> tuple[bytes, str]:
        """Extrae los bytes y el tipo MIME de la primera imagen generada.

        Raises:
            ImageGenerationError: si no hay imágenes, la imagen está filtrada
                o no contiene bytes.
        """
        generated_images = getattr(response, "generated_images", None) or []
        if not generated_images:
            raise ImageGenerationError(
                "Gemini Images no devolvió imágenes en la respuesta."
            )
        generated = generated_images[0]
        filtered_reason = getattr(generated, "rai_filtered_reason", None)
        if filtered_reason:
            raise ImageGenerationError(
                f"La imagen fue filtrada por políticas de seguridad: "
                f"{filtered_reason}"
            )
        image = getattr(generated, "image", None)
        if image is None:
            raise ImageGenerationError("La respuesta no contiene una imagen.")
        content = getattr(image, "image_bytes", None)
        if not content:
            raise ImageGenerationError("La imagen generada no contiene bytes.")
        mime_type = getattr(image, "mime_type", None) or "image/png"
        return content, mime_type

    def _build_metadata(
        self, request: ImageRequest, content: bytes, mime_type: str
    ) -> ImageMetadata:
        """Construye los metadatos de la imagen generada (Media Core)."""
        extension = mime_type.split("/")[-1] if "/" in mime_type else mime_type
        return ImageMetadata(
            name=sanitize_component(request.prompt[:60], fallback="imagen"),
            mime_type=mime_type,
            size_bytes=len(content),
            width=request.width,
            height=request.height,
            format=extension,
        )
