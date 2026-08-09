"""Genera una imagen por escena de un ContentPackage usando IA.

Flujo:

1. Lee ``output/content.json`` y reconstruye el :class:`content.ContentPackage`
   con el dominio existente (``content_package_from_json``).
2. Obtiene todas las escenas del contenido y construye un prompt visual por
   escena con :func:`image.build_scene_prompts`.
3. Genera una imagen por escena mediante el :class:`image.ImageAdapter`
   envuelto sobre :class:`image.GeminiImageProvider`.
4. Persiste cada imagen en ``output/images/`` con nombres ``scene_001.png``,
   ``scene_002.png``, ... usando :class:`media.LocalStorage`.

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

import logging
import os
import sys
from pathlib import Path

# --- Ajuste del path para poder importar los paquetes de src/ ----------------
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from content import content_package_from_json  # noqa: E402
from config import load_project_env  # noqa: E402
from image import (  # noqa: E402
    ImageAdapter,
    ImageScenes,
    build_scene_prompts,
)
from image.exceptions import ImageError  # noqa: E402
from image.providers import GeminiImageProvider  # noqa: E402
from media import LocalStorage, StorageError  # noqa: E402

logger = logging.getLogger("generate_image")

#: Ruta del ContentPackage de entrada.
INPUT_PATH = ROOT / "output" / "content.json"
#: Directorio donde se guardan las imágenes generadas.
OUTPUT_IMAGES_DIR = ROOT / "output" / "images"
#: Modelo de Imagen por defecto si no hay variable de entorno.
DEFAULT_IMAGE_MODEL = "imagen-3.0-generate-002"
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

    try:
        adapter = ImageAdapter(GeminiImageProvider(model=resolve_image_model()))
    except ImageError as exc:
        logger.error("Error al configurar el proveedor de imágenes: %s", exc)
        return 1

    storage = LocalStorage(OUTPUT_IMAGES_DIR, auto_create=True)
    for number, visual in enumerate(visual_prompts, start=1):
        filename = f"scene_{number:03d}.{IMAGE_EXTENSION}"
        logger.info("Generando escena %d/%d...", number, total)
        try:
            result = adapter.generate(visual.to_request())
            path = storage.write_bytes(filename, result.content)
        except ImageError as exc:
            logger.error("Error al generar la imagen de la escena %d: %s", number, exc)
            return 1
        except StorageError as exc:
            logger.error("Error al guardar la imagen de la escena %d: %s", number, exc)
            return 1
        logger.info("Imagen guardada: %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
