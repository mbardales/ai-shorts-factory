"""Genera la narración de un ContentPackage usando síntesis de voz.

Flujo:

1. Lee ``output/content.json`` y reconstruye el :class:`content.ContentPackage`
   con el dominio existente (``content_package_from_json``).
2. Obtiene la narración del contenido y construye el
   :class:`audio.SpeechPrompt` correspondiente.
3. Genera la pista mediante el :class:`audio.AudioAdapter` envuelto sobre
   :class:`audio.GoogleTTSProvider`.
4. Persiste la pista en ``output/audio/narration.mp3`` usando
   :class:`media.LocalStorage`.

Uso:

    python scripts/generate_audio.py
"""

from __future__ import annotations

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
from audio import (  # noqa: E402
    AudioAdapter,
    AudioFormat,
    AudioNarration,
    NarrationSource,
    SpeechPrompt,
)
from audio.exceptions import AudioError  # noqa: E402
from audio.providers import GoogleTTSProvider  # noqa: E402
from media import LocalStorage, StorageError  # noqa: E402

logger = logging.getLogger("generate_audio")

#: Ruta del ContentPackage de entrada.
INPUT_PATH = ROOT / "output" / "content.json"
#: Directorio donde se guarda la pista de narración.
OUTPUT_AUDIO_DIR = ROOT / "output" / "audio"
#: Nombre del archivo de salida de la narración.
OUTPUT_FILENAME = "narration.mp3"
#: Modelo TTS por defecto si no hay variable de entorno.
DEFAULT_TTS_MODEL = "gemini-3.1-flash-tts-preview"
#: Voz TTS por defecto si no hay variable de entorno.
DEFAULT_TTS_VOICE = "Kore"
#: Formato de codificación de la narración de salida.
AUDIO_FORMAT = AudioFormat.MP3


def load_env_file(path: Path) -> None:
    """Carga variables ``KEY=VALUE`` de un archivo ``.env`` al entorno.

    No sobrescribe variables ya definidas en el entorno del proceso.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_tts_model() -> str:
    """Devuelve el modelo TTS a usar (env o el predeterminado).

    Normaliza el prefijo ``models/`` si la variable lo incluye.
    """
    model = os.environ.get("GEMINI_TTS_MODEL", "").strip()
    if model.startswith("models/"):
        model = model[len("models/") :]
    return model or DEFAULT_TTS_MODEL


def resolve_voice_name() -> str:
    """Devuelve la voz TTS a usar (env ``GEMINI_TTS_VOICE`` o la predeterminada).

    La voz proviene únicamente de la configuración de runtime; ``narration.voice``
    del contenido no se interpreta como nombre de voz del proveedor.
    """
    return os.environ.get("GEMINI_TTS_VOICE", "").strip() or DEFAULT_TTS_VOICE


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


def build_speech_prompt(
    source: NarrationSource,
    content_id: str,
    *,
    voice: Optional[str] = None,
) -> SpeechPrompt:
    """Construye el :class:`SpeechPrompt` de una porción de narración.

    ``voice`` es el nombre de voz del proveedor a utilizar; si no se indica, se
    usa la voz predeterminada del script. ``source.voice`` (descripción libre
    del contenido) no se usa como nombre de voz.
    """
    return SpeechPrompt(
        content_id=content_id,
        source_index=source.index,
        text=source.text,
        voice=voice or DEFAULT_TTS_VOICE,
        timing_seconds=source.timing_seconds,
    )


def main() -> int:
    """Punto de entrada del script. Devuelve 0 en éxito, 1 en error."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_env_file(ROOT / ".env")

    try:
        package = load_content_package()
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return 1

    narration = AudioNarration.from_content_package(package)
    if not narration.sources:
        logger.warning("El contenido no tiene narración para sintetizar.")
        return 0
    source = narration.sources[0]
    prompt = build_speech_prompt(source, narration.content_id, voice=resolve_voice_name())
    logger.info(
        "Contenido '%s': se generará la narración (%d caracteres).",
        narration.content_id or "(sin id)",
        len(prompt.text),
    )

    try:
        adapter = AudioAdapter(GoogleTTSProvider(model=resolve_tts_model()))
    except AudioError as exc:
        logger.error("Error al configurar el proveedor de TTS: %s", exc)
        return 1

    storage = LocalStorage(OUTPUT_AUDIO_DIR, auto_create=True)
    logger.info("Generando narración...")
    try:
        result = adapter.generate(prompt.to_request(format=AUDIO_FORMAT))
        path = storage.write_bytes(OUTPUT_FILENAME, result.content)
    except AudioError as exc:
        logger.error("Error al generar la narración: %s", exc)
        return 1
    except StorageError as exc:
        logger.error("Error al guardar la narración: %s", exc)
        return 1
    logger.info("Narración guardada: %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
