"""Genera una imagen por escena de un ContentPackage usando IA.

Flujo:

1. Lee ``output/content.json`` y reconstruye el :class:`content.ContentPackage`
   con el dominio existente (``content_package_from_json``).
2. Obtiene todas las escenas del contenido y construye un prompt visual por
   escena con :func:`image.build_scene_prompts`.
3. Genera una imagen por escena mediante el :class:`image.ImageAdapter`
   envuelto sobre el proveedor seleccionado con la variable de entorno
   ``GEMINI_IMAGE_PROVIDER`` (``gemini`` por defecto, ``stability`` o
   ``synthetic``). Opcionalmente, si ``GEMINI_IMAGE_FALLBACK_PROVIDER`` define
   un proveedor distinto, se habilita UN fallback POR EJECUCIÓN: si el primario
   falla con un error recuperable (429, timeout, red, 5xx, respuesta vacía), el
   resto de escenas (incluida la fallida) se generan con el fallback. El
   fallback nunca incluye a ``synthetic`` de forma automática.
4. Persiste cada imagen en ``output/images/`` con nombres ``scene_001.png``,
   ``scene_002.png``, ... usando :class:`media.LocalStorage`.
5. Antes de generar, elimina los artifacts de imágenes de ejecuciones
   anteriores (``scene_*.png`` y ``providers.json``) para que una ejecución
   parcial o con menos escenas nunca deje PNGs o metadata stale.
6. Al terminar, escribe ``output/images/providers.json`` (provider/model por
   PNG) de forma atómica (archivo temporal + ``os.replace``).

Restricción temporal (agosto 2026):

La cuenta/API Key actual de Gemini está en el plan gratuito con **cuota 0 para
la generación de imágenes** (todos los modelos de imagen devuelven
``RESOURCE_EXHAUSTED`` con ``limit: 0``). Además, ``GeminiImageProvider`` usa
``models.generate_images`` (endpoint ``predict`` de Imagen, ya deprecado), y los
modelos Imagen no están disponibles para usuarios nuevos de esta cuenta. No se
modifica la arquitectura: el proveedor y el contrato de ``image`` se conservan
tal cual. En cuanto la clave tenga cuota de imágenes, el script debe funcionar
sin cambios (seleccionando un modelo disponible vía ``GEMINI_IMAGE_MODEL``).

Uso:

    python scripts/generate_image.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# --- Ajuste del path para poder importar los paquetes de src/ ----------------
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from content import content_package_from_json  # noqa: E402
from config import load_project_env  # noqa: E402
from image import (  # noqa: E402
    ImageAdapter,
    ImageProvider,
    ImageScenes,
    build_scene_prompts,
)
from image.exceptions import ImageError, ImageProviderError  # noqa: E402
from image.fallback import make_generate_with_fallback  # noqa: E402
from image.providers import (  # noqa: E402
    GeminiImageProvider,
    StabilityImageProvider,
    SyntheticImageProvider,
)
from media import LocalStorage, StorageError  # noqa: E402

logger = logging.getLogger("generate_image")

#: Ruta del ContentPackage de entrada.
INPUT_PATH = ROOT / "output" / "content.json"
#: Directorio donde se guardan las imágenes generadas.
OUTPUT_IMAGES_DIR = ROOT / "output" / "images"
#: Modelo de Imagen por defecto si no hay variable de entorno.
DEFAULT_IMAGE_MODEL = "imagen-3.0-generate-002"
#: Proveedor de imágenes por defecto si no hay variable de entorno.
DEFAULT_IMAGE_PROVIDER = "gemini"
#: Valores admitidos para ``GEMINI_IMAGE_PROVIDER``.
SUPPORTED_IMAGE_PROVIDERS = ("gemini", "stability", "synthetic")
#: Extensión de las imágenes de salida (formato Imagen, PNG).
IMAGE_EXTENSION = "png"


def resolve_image_model() -> str:
    """Devuelve el modelo de imágenes a usar (env o el predeterminado).

    Normaliza el prefijo ``models/`` si la variable lo incluye.
    """
    model = os.environ.get("GEMINI_IMAGE_MODEL", "").strip()
    if model.startswith("models/"):
        model = model[len("models/") :]
    return model or DEFAULT_IMAGE_MODEL


def resolve_image_provider() -> str:
    """Devuelve el proveedor de imágenes a usar (env o el predeterminado).

    Lee ``GEMINI_IMAGE_PROVIDER``, lo normaliza a minúsculas y usa
    ``DEFAULT_IMAGE_PROVIDER`` si no está configurada.
    """
    provider = os.environ.get("GEMINI_IMAGE_PROVIDER", "").strip().lower()
    return provider or DEFAULT_IMAGE_PROVIDER


def resolve_image_fallback_provider() -> str:
    """Devuelve el proveedor de fallback configurado (env) o cadena vacía.

    Lee ``GEMINI_IMAGE_FALLBACK_PROVIDER`` y lo normaliza a minúsculas. Una
    cadena vacía significa "fallback deshabilitado". El valor, si existe, debe
    ser uno de :data:`SUPPORTED_IMAGE_PROVIDERS` (se valida al construirlo).
    """
    return os.environ.get("GEMINI_IMAGE_FALLBACK_PROVIDER", "").strip().lower()


def build_image_provider(provider: str | None = None) -> ImageProvider:
    """Construye el proveedor de imágenes según el nombre indicado.

    Args:
        provider: nombre del proveedor (``gemini``, ``stability`` o
            ``synthetic``). Si es ``None`` se usa el resuelto por
            :func:`resolve_image_provider`.

    Returns:
        Instancia concreta del proveedor seleccionado.

    Raises:
        ImageProviderError: si el nombre no es un valor soportado, o si el
            proveedor requiere configuración inválida (p. ej. API key ausente).
            No se realiza ninguna llamada externa en ese caso.
    """
    provider = (provider or resolve_image_provider())
    if provider == "gemini":
        return GeminiImageProvider(model=resolve_image_model())
    if provider == "stability":
        return StabilityImageProvider(model=resolve_image_model())
    if provider == "synthetic":
        return SyntheticImageProvider()
    raise ImageProviderError(
        f"Proveedor de imágenes no soportado: {provider!r}. "
        f"Valores válidos: {', '.join(SUPPORTED_IMAGE_PROVIDERS)}."
    )


def load_content_package() -> "ContentPackage":
    """Lee y reconstruye el ContentPackage desde ``output/content.json``.

    Raises:
        FileNotFoundError: si el archivo de entrada no existe.
        ValueError: si el JSON no se puede reconstruir como ContentPackage.
    """
    if not INPUT_PATH.is_file():
        raise FileNotFoundError(
            f"No se encontró {INPUT_PATH}. "
            "Ejecuta primero 'python scripts/generate_content.py'."
        )
    return content_package_from_json(INPUT_PATH.read_text(encoding="utf-8"))


def clean_image_outputs(storage: LocalStorage) -> None:
    """Elimina artifacts de imágenes de ejecuciones anteriores.

    Borra únicamente los archivos de primer nivel producidos por este script:
    los PNG de escenas (``scene_*.png``) y el sidecar ``providers.json``. No
    toca otros archivos ni subdirectorios (incluido su contenido), ni el resto
    de ``output/``.

    Args:
        storage: almacén de imágenes (``LocalStorage`` sobre ``output/images``).
    """
    for entry in storage.root.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        is_scene_png = name.startswith("scene_") and name.endswith(
            f".{IMAGE_EXTENSION}"
        )
        if name == "providers.json" or is_scene_png:
            try:
                storage.delete(name)
            except StorageError as exc:
                logger.warning(
                    "No se pudo eliminar el artifact anterior '%s': %s",
                    name,
                    exc,
                )


def write_providers_atomic(data: dict, path: Path) -> None:
    """Escribe la metadata de providers de forma atómica.

    Escribe el contenido completo a un archivo temporal dentro del mismo
    directorio y lo reemplaza con :func:`os.replace`, de modo que nunca quede
    un ``providers.json`` parcial. Si falla, elimina el temporal y propaga el
    error.

    Args:
        data: dict ``filename -> {"provider": ..., "model": ...}``.
        path: ruta final de ``providers.json``.

    Raises:
        OSError: si no se puede escribir o reemplazar el archivo.
    """
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def main() -> int:
    """Punto de entrada del script. Devuelve 0 en éxito, 1 en error."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_project_env()

    try:
        package = load_content_package()
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return 1

    image_scenes = ImageScenes.from_content_package(package)
    visual_prompts = build_scene_prompts(image_scenes)
    total = len(visual_prompts)
    if total == 0:
        logger.warning("El contenido no tiene escenas para generar imágenes.")
        return 0
    logger.info(
        "Contenido '%s': %d escenas, se generará una imagen por escena.",
        image_scenes.content_id or "(sin id)",
        total,
    )

    primary_name = resolve_image_provider()
    try:
        primary = ImageAdapter(build_image_provider(primary_name))
    except ImageError as exc:
        logger.error("Error al configurar el proveedor de imágenes: %s", exc)
        return 1

    fallback = None
    fallback_name = resolve_image_fallback_provider()
    if primary_name == "synthetic":
        if fallback_name:
            logger.info(
                "Provider primario sintético (explícito): no se aplica el "
                "fallback configurado ('%s').",
                fallback_name,
            )
    elif fallback_name:
        if fallback_name == primary_name:
            logger.warning(
                "El provider de fallback ('%s') es igual al primario; "
                "el fallback se ignora.",
                fallback_name,
            )
        else:
            try:
                fallback = ImageAdapter(build_image_provider(fallback_name))
                logger.info("Fallback configurado: '%s'.", fallback.provider.name)
            except ImageError as exc:
                logger.error(
                    "Error al configurar el proveedor de fallback: %s", exc
                )
                return 1

    generate_with_fallback = make_generate_with_fallback(primary, fallback)
    logger.info(
        "Provider primario: '%s' (modelo '%s').",
        primary.provider.name,
        primary.provider.model,
    )

    storage = LocalStorage(OUTPUT_IMAGES_DIR, auto_create=True)
    clean_image_outputs(storage)
    providers: dict[str, dict[str, Optional[str]]] = {}
    for number, visual in enumerate(visual_prompts, start=1):
        filename = f"scene_{number:03d}.{IMAGE_EXTENSION}"
        logger.info("Generando escena %d/%d...", number, total)
        try:
            result = generate_with_fallback(visual.to_request())
            path = storage.write_bytes(filename, result.content)
        except ImageError as exc:
            logger.error("Error al generar la imagen de la escena %d: %s", number, exc)
            return 1
        except StorageError as exc:
            logger.error("Error al guardar la imagen de la escena %d: %s", number, exc)
            return 1
        providers[filename] = {
            "provider": getattr(result, "provider", None),
            "model": result.model,
        }
        logger.info("Imagen guardada: %s", path)

    providers_path = OUTPUT_IMAGES_DIR / "providers.json"
    try:
        write_providers_atomic(providers, providers_path)
    except OSError as exc:
        logger.error("Error al escribir la metadata de providers: %s", exc)
        return 1
    logger.info("Metadata de providers guardada: %s", providers_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
