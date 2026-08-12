"""Renderiza el video de un proyecto a partir de su manifest.

Flujo:

1. Carga y valida el manifest con el Project Core
   (:func:`project.project_manifest_from_json` +
   :func:`project.validate_project_manifest`).
2. Comprueba la suficiencia física de los activos declarados.
3. Construye la :class:`renderer.RenderRequest` y el :class:`renderer.FFmpegCommand`
   con :func:`renderer.build_render_request` y
   :func:`renderer.build_project_ffmpeg_command`.
4. Ejecuta FFmpeg real con :class:`renderer.FFmpegExecutor` (timeout 60 s)
   usando como directorio de trabajo el directorio del manifest (las rutas de
   los activos y de salida son relativas a ``output/``).
5. Verifica el archivo generado y lo describe con ``ffprobe`` (subprocess solo
   para inspección; el render nunca se invoca por ``subprocess`` directo).

Códigos de salida:

- 0: éxito (el video se generó y validó).
- 1: error general o de configuración.
- 2: el manifest no existe, no es un JSON válido o no cumple el dominio.
- 3: activos insuficientes (no se ejecuta FFmpeg).
- 4: error al renderizar con FFmpeg.

Uso:

    python scripts/render_video.py [ruta_al_manifest]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

# --- Ajuste del path para poder importar los paquetes de src/ ----------------
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from project import (  # noqa: E402
    ProjectError,
    ProjectNotFoundError,
    ProjectValidationError,
    project_manifest_from_json,
    validate_project_manifest,
)
from renderer import (  # noqa: E402
    FFmpegExecutor,
    RendererError,
    RendererValidationError,
    build_project_ffmpeg_command,
    build_render_request,
)

logger = logging.getLogger("render_video")

#: Códigos de salida documentados en el docstring del módulo.
EXIT_OK = 0
EXIT_GENERAL = 1
EXIT_MANIFEST_INVALID = 2
EXIT_ASSETS_INSUFFICIENT = 3
EXIT_RENDER_ERROR = 4

#: Ruta por defecto del manifest.
DEFAULT_MANIFEST_PATH = ROOT / "output" / "project.json"

#: Opciones adicionales para el comando FFmpeg (no interactivo).
FFMPEG_OPTIONS = ("-y", "-nostdin", "-hide_banner")

#: Tiempo límite del proceso FFmpeg en segundos.
FFMPEG_TIMEOUT_SECONDS = 60

#: Timeout de la inspección con ffprobe.
PROBE_TIMEOUT_SECONDS = 30


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Renderiza el video de un proyecto desde su manifest.",
    )
    parser.add_argument(
        "manifest_path",
        nargs="?",
        default=str(DEFAULT_MANIFEST_PATH),
        help="Ruta del project.json (por defecto: %(default)s).",
    )
    return parser


def load_manifest(path: Path) -> object:
    """Carga y valida el manifest desde un archivo JSON.

    Raises:
        ProjectNotFoundError: si el archivo no existe.
        ValueError: si el JSON no es válido.
        ProjectValidationError: si el manifest no cumple las reglas del dominio.
    """
    if not path.is_file():
        raise ProjectNotFoundError(f"No se encontró el manifest: {path}")
    manifest = project_manifest_from_json(path.read_text(encoding="utf-8"))
    validate_project_manifest(manifest)
    return manifest


def verify_assets(manifest: object, base_dir: Path) -> bool:
    """Comprueba que los activos declarados existan físicamente.

    Devuelve ``False`` si el manifest no declara activos o si alguno de los
    archivos declarados no existe en ``base_dir``; en ese caso no se debe
    ejecutar FFmpeg.
    """
    assets = getattr(manifest, "assets", ())
    if not assets:
        logger.error("El manifest no declara activos; no se puede renderizar.")
        return False
    missing = [
        asset.path
        for asset in assets
        if not (base_dir / asset.path).is_file()
    ]
    if missing:
        logger.error(
            "Activos insuficientes: faltan en disco: %s",
            ", ".join(missing),
        )
        return False
    return True


def describe_output(path: Path) -> None:
    """Describe el archivo generado con ``ffprobe`` (solo información)."""
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("No se pudo inspeccionar el video con ffprobe: %s", exc)
        return
    if completed.returncode != 0:
        logger.warning(
            "ffprobe no pudo inspeccionar el archivo: %s",
            (completed.stderr or "").strip(),
        )
        return
    try:
        data = json.loads(completed.stdout or "{}")
    except ValueError:
        logger.warning("La salida de ffprobe no es interpretable.")
        return
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    width = video.get("width")
    height = video.get("height")
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    resolution = f"{width}x{height}" if width and height else "?"
    logger.info(
        "Video: codec=%s resolución=%s",
        video.get("codec_name", "?"),
        resolution,
    )
    logger.info("Audio: codec=%s", audio.get("codec_name", "sin audio"))
    logger.info("Duración real: %.2f s", duration)


def render_video_to_path(manifest_path: Path) -> int:
    """Renderiza el video de un proyecto desde la ruta de su manifest.

    Es la función interna reutilizable del script: recibe la ruta del manifest
    de forma explícita (permite al PipelineRunner renderizar un run aislado).
    Las rutas de los activos y de salida se interpretan relativas al directorio
    del manifest. Devuelve un código de salida documentado.

    Args:
        manifest_path: ruta absoluta del ``project.json`` a renderizar.

    Returns:
        Código de salida (ver :data:`render_video.EXIT_OK` y siguientes).
    """
    base_dir = manifest_path.resolve().parent

    logger.info("Manifest: %s", manifest_path)
    logger.info("Directorio base de activos: %s", base_dir)

    try:
        manifest = load_manifest(manifest_path)
    except ProjectNotFoundError as exc:
        logger.error("%s", exc)
        return EXIT_MANIFEST_INVALID
    except ProjectValidationError as exc:
        logger.error("Manifest inválido: %s", exc)
        return EXIT_MANIFEST_INVALID
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("El manifest no es un JSON válido: %s", exc)
        return EXIT_MANIFEST_INVALID

    logger.info("Proyecto: %s — %s", manifest.identity.id, manifest.identity.title)
    logger.info("Activos declarados: %d", len(manifest.assets))
    logger.info("Duración estimada: %.1f s", manifest.estimated_duration_seconds)
    logger.info("Salida esperada: %s", manifest.output_file)

    if not verify_assets(manifest, base_dir):
        return EXIT_ASSETS_INSUFFICIENT

    try:
        request = build_render_request(manifest)
        command = build_project_ffmpeg_command(request, options=FFMPEG_OPTIONS)
    except RendererValidationError as exc:
        logger.error("No se pudo construir el comando de render: %s", exc)
        return EXIT_GENERAL

    output_path = request.output_path
    output_abs = base_dir / output_path
    try:
        output_abs.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("No se pudo crear el directorio de salida: %s", exc)
        return EXIT_RENDER_ERROR

    saved_cwd = os.getcwd()
    try:
        os.chdir(base_dir)
    except OSError as exc:
        logger.error(
            "No se pudo cambiar al directorio de trabajo %s: %s",
            base_dir,
            exc,
        )
        return EXIT_GENERAL

    try:
        logger.info(
            "Iniciando render con FFmpeg (timeout %d s)...",
            FFMPEG_TIMEOUT_SECONDS,
        )
        executor = FFmpegExecutor(timeout_seconds=FFMPEG_TIMEOUT_SECONDS)
        result = executor.execute(
            command,
            output_path=output_path,
            duration_seconds=manifest.estimated_duration_seconds,
        )
    except RendererError as exc:
        logger.error("Error al renderizar con FFmpeg: %s", exc)
        return EXIT_RENDER_ERROR
    finally:
        os.chdir(saved_cwd)

    if not output_abs.is_file():
        logger.error("FFmpeg terminó sin generar el archivo %s", output_abs)
        return EXIT_RENDER_ERROR
    size = output_abs.stat().st_size
    if size <= 0:
        logger.error("El archivo generado está vacío: %s", output_abs)
        return EXIT_RENDER_ERROR

    logger.info("Render completado en: %s", output_abs)
    logger.info("Tamaño: %d bytes", size)
    logger.info("Duración reportada: %.1f s", result.duration_seconds)
    describe_output(output_abs)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del script. Devuelve un código de salida documentado."""
    args = build_parser().parse_args(argv)
    return render_video_to_path(Path(args.manifest_path))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    sys.exit(main())
