"""Genera el Project Manifest de un Short a partir de los activos del pipeline.

Flujo:

1. Lee ``output/content.json`` y reconstruye el :class:`content.ContentPackage`.
2. Detecta automáticamente las imágenes en ``output/images/``.
3. Detecta automáticamente el audio en ``output/audio/``.
4. Construye un :class:`project.ProjectManifest` con el dominio.
5. Lo valida con :func:`project.validate_project_manifest`.
6. Serializa el manifest en ``output/project.json`` (UTF-8).

El manifest es la única fuente de verdad para el renderizador de video: incluye
el ContentPackage, los activos generados, la duración estimada y el archivo de
salida esperado, de modo que el renderizado pueda consumir exclusivamente
``output/project.json`` sin inspeccionar carpetas.

Uso:

    python scripts/generate_manifest.py
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Mapping

# --- Ajuste del path para poder importar los paquetes de src/ ----------------
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from content import (  # noqa: E402
    ContentValidationError,
    content_package_from_json,
    content_package_to_dict,
    content_package_to_json,
    coerce_content_package,
)
from project import (  # noqa: E402
    ProjectValidationError,
    build_project_manifest,
    project_manifest_to_json,
    rescale_scene_timings,
    validate_project_manifest,
)

logger = logging.getLogger("generate_manifest")

#: Directorio de salida del pipeline.
OUTPUT_DIR = ROOT / "output"
#: Ruta del ContentPackage de origen.
INPUT_PATH = OUTPUT_DIR / "content.json"
#: Directorio donde se guardan las imágenes generadas.
IMAGES_DIR = OUTPUT_DIR / "images"
#: Directorio donde se guardan las pistas de audio generadas.
AUDIO_DIR = OUTPUT_DIR / "audio"
#: Ruta del archivo de salida del manifest.
OUTPUT_PATH = OUTPUT_DIR / "project.json"

#: Extensiones de imagen reconocidas (el pipeline genera ``.png``).
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
#: Extensiones de audio reconocidas (el pipeline genera ``.mp3``).
AUDIO_EXTENSIONS = {"mp3", "wav", "ogg", "m4a"}


def load_image_providers(path: Path) -> dict[str, dict[str, str]]:
    """Carga la metadata opcional provider/model por imagen.

    El sidecar es **opcional**: si no existe o no es interpretable, se
    devuelve un mapa vacío y los activos se construyen con ``provider=None``
    y ``model=None`` (compatible con ejecuciones anteriores).

    Args:
        path: ruta al sidecar ``providers.json`` (o equivalente).

    Returns:
        Dict ``filename -> {"provider": ..., "model": ...}`` con solo valores
        de tipo cadena; nunca secretos.
    """
    if not path.is_file():
        logger.info("No se encontró metadata de providers: %s", path)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        logger.warning("No se pudo leer la metadata de providers (%s): %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("La metadata de providers no es un objeto: %s", path)
        return {}
    result: dict[str, dict[str, str]] = {}
    for filename, entry in data.items():
        if not isinstance(filename, str) or not isinstance(entry, Mapping):
            continue
        clean: dict[str, str] = {}
        provider = entry.get("provider")
        model = entry.get("model")
        if isinstance(provider, str):
            clean["provider"] = provider
        if isinstance(model, str):
            clean["model"] = model
        result[filename] = clean
    return result


def detect_assets(
    directory: Path, extensions: set[str], *, base_dir: Path = OUTPUT_DIR
) -> list[str]:
    """Devuelve rutas relativas a ``base_dir`` de los activos detectados.

    Los activos se ordenan por nombre para garantizar un orden estable; las
    imágenes se asignan a las escenas en ese mismo orden.

    Args:
        directory: directorio a inspeccionar.
        extensions: extensiones (sin punto) reconocidas como activos.
        base_dir: directorio base respecto al cual se expresan las rutas
            (por defecto ``output/``).

    Returns:
        Lista ordenada de rutas relativas a ``base_dir``.
    """
    if not directory.is_dir():
        logger.warning("No se encontró el directorio de activos: %s", directory)
        return []
    found = [
        path.relative_to(base_dir).as_posix()
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower().lstrip(".") in extensions
    ]
    return sorted(found)


def sum_scene_timing(package: Mapping[str, Any]) -> float:
    """Suma los ``timing_seconds`` positivos de las escenas del ContentPackage."""
    visuals = package.get("visuals")
    scenes = visuals.get("scenes") if isinstance(visuals, Mapping) else None
    if not isinstance(scenes, (list, tuple)):
        return 0.0
    total = 0.0
    for scene in scenes:
        if not isinstance(scene, Mapping):
            continue
        timing = scene.get("timing_seconds")
        if isinstance(timing, (int, float)) and not isinstance(timing, bool):
            if timing > 0:
                total += float(timing)
    return total


def audio_real_duration(path: Path) -> float | None:
    """Duración real de una pista de audio (WAV vía ``wave``; resto por ffprobe).

    Devuelve ``None`` si no se puede medir (archivo ausente, tool ausente,
    timeout o formato ilegible). Nunca lanza.
    """
    if not path.is_file():
        return None
    extension = path.suffix.lower().lstrip(".")
    if extension == "wav":
        try:
            with wave.open(str(path), "rb") as wav:
                rate = wav.getframerate()
                frames = wav.getnframes()
        except (wave.Error, OSError, ValueError) as exc:
            logger.warning("No se pudo medir la duración WAV de %s: %s", path, exc)
            return None
        return frames / rate if rate else None
    executable = shutil.which("ffprobe") or "ffprobe"
    try:
        completed = subprocess.run(
            [executable, "-v", "error", "-show_format", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("No se pudo medir la duración de %s: %s", path, exc)
        return None
    if completed.returncode != 0:
        logger.debug("ffprobe devolvió %d para %s.", completed.returncode, path)
        return None
    try:
        data = json.loads(completed.stdout or "{}")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        logger.debug("Sin duración medible para %s: %s", path, exc)
        return None


def generate_manifest_to_path(
    output_dir: Path,
    *,
    run_id: str | None = None,
) -> int:
    """Construye y persiste el Project Manifest en ``output_dir/project.json``.

    Es la función interna reutilizable del script: recibe el directorio de
    salida de forma explícita (permite al PipelineRunner escribir en un run
    aislado). Devuelve 0 en éxito y 1 en error.

    Args:
        output_dir: directorio absoluto de salida del run (contiene
            ``content.json``, ``images/`` y ``audio/``).
        run_id: identificador de la ejecución del pipeline que se registra en
            ``metadata.run_id`` del manifest (opcional).
    """
    content_path = output_dir / "content.json"
    images_dir = output_dir / "images"
    audio_dir = output_dir / "audio"
    project_path = output_dir / "project.json"

    if not content_path.is_file():
        logger.error("No se encontró el ContentPackage: %s", content_path)
        return 1

    try:
        package = content_package_from_json(content_path.read_text(encoding="utf-8"))
    except (ValueError, ContentValidationError) as exc:
        logger.error("No se pudo leer el ContentPackage: %s", exc)
        return 1

    image_paths = detect_assets(images_dir, IMAGE_EXTENSIONS, base_dir=output_dir)
    audio_paths = detect_assets(audio_dir, AUDIO_EXTENSIONS, base_dir=output_dir)
    image_providers = load_image_providers(images_dir / "providers.json")

    package_dict = content_package_to_dict(package)

    # Sincroniza el timeline con la narración real: si el audio dura más que la
    # suma de timing_seconds, extiende las escenas proporcionalmente para que el
    # render (y el Quality Gate) vean una duración coherente con el audio.
    #
    # El manifest embebe la temporización en punto flotante (fuente del
    # renderer); content.json conserva su contrato de enteros (fuente del
    # Quality Gate), por lo que ambos se actualizan con la misma corrección.
    if len(audio_paths) == 1:
        real_duration = audio_real_duration(output_dir / audio_paths[0])
        if real_duration is not None and real_duration > 0:
            sum_timing = sum_scene_timing(package_dict)
            if real_duration > sum_timing + 1e-6:
                content_float = rescale_scene_timings(package_dict, real_duration)
                content_int = rescale_scene_timings(
                    package_dict, real_duration, integer=True
                )
                package_dict = content_float
                content_path.write_text(
                    content_package_to_json(coerce_content_package(content_int)),
                    encoding="utf-8",
                )
                logger.info(
                    "Timeline ajustado al audio real (%.2fs): suma timing %.2fs; "
                    "las escenas se extendieron proporcionalmente (factor %.3f). "
                    "content.json y manifest re-temporizados.",
                    real_duration,
                    sum_timing,
                    real_duration / sum_timing,
                )
            else:
                logger.debug(
                    "El audio real (%.2fs) no supera la suma timing (%.2fs): "
                    "sin ajuste.",
                    real_duration,
                    sum_timing,
                )

    manifest = build_project_manifest(
        package_dict,
        image_paths,
        audio_paths,
        content_file="content.json",
        image_providers=image_providers,
        run_id=run_id,
    )

    try:
        validate_project_manifest(manifest)
    except ProjectValidationError as exc:
        logger.error("El manifest no supera la validación: %s", exc)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    project_path.write_text(project_manifest_to_json(manifest), encoding="utf-8")
    logger.info(
        "Manifest guardado en: %s (%d imágenes, %d pistas de audio, "
        "duración estimada %.1fs).",
        project_path,
        len(image_paths),
        len(audio_paths),
        manifest.estimated_duration_seconds,
    )
    return 0


def main() -> int:
    """Punto de entrada del script. Devuelve 0 en éxito, 1 en error."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return generate_manifest_to_path(OUTPUT_DIR)


if __name__ == "__main__":
    sys.exit(main())
