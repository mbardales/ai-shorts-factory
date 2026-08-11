"""Genera la narración de un ContentPackage usando síntesis de voz.

Flujo:

1. Lee ``output/content.json`` y reconstruye el :class:`content.ContentPackage`
   con el dominio existente (``content_package_from_json``).
2. Obtiene la narración del contenido y construye el
   :class:`audio.SpeechPrompt` correspondiente.
3. Genera la pista mediante el :class:`audio.AudioAdapter` envuelto sobre el
   proveedor seleccionado con la variable de entorno ``GEMINI_AUDIO_PROVIDER``
   (``gemini`` por defecto o ``synthetic``).
4. Persiste la pista en ``output/audio/`` (``narration.mp3`` para Gemini,
   ``narration.wav`` para synthetic) usando :class:`media.LocalStorage`.

El proveedor ``synthetic`` genera un WAV PCM determinista offline (sin API key
ni red), útil para validar el pipeline completo cuando el TTS externo no está
disponible.

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
from config import load_project_env  # noqa: E402
from audio import (  # noqa: E402
    AudioAdapter,
    AudioFormat,
    AudioNarration,
    AudioProvider,
    NarrationSource,
    SpeechPrompt,
)
from audio.exceptions import AudioError, AudioProviderError  # noqa: E402
from audio.providers import GoogleTTSProvider, SyntheticAudioProvider  # noqa: E402
from media import LocalStorage, StorageError  # noqa: E402

logger = logging.getLogger("generate_audio")

#: Ruta del ContentPackage de entrada.
INPUT_PATH = ROOT / "output" / "content.json"
#: Directorio donde se guarda la pista de narración.
OUTPUT_AUDIO_DIR = ROOT / "output" / "audio"
#: Nombre del archivo de salida de la narración (Gemini).
OUTPUT_FILENAME = "narration.mp3"
#: Nombre del archivo de salida de la narración (synthetic).
SYNTHETIC_OUTPUT_FILENAME = "narration.wav"
#: Proveedor de audio por defecto si no hay variable de entorno.
DEFAULT_AUDIO_PROVIDER = "gemini"
#: Valores admitidos para ``GEMINI_AUDIO_PROVIDER``.
SUPPORTED_AUDIO_PROVIDERS = ("gemini", "synthetic")
#: Modelo TTS por defecto si no hay variable de entorno.
DEFAULT_TTS_MODEL = "gemini-3.1-flash-tts-preview"
#: Voz TTS por defecto si no hay variable de entorno.
DEFAULT_TTS_VOICE = "Kore"
#: Formato de codificación de la narración de salida (Gemini).
AUDIO_FORMAT = AudioFormat.MP3
#: Formato de codificación de la narración de salida (synthetic).
SYNTHETIC_AUDIO_FORMAT = AudioFormat.WAV


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


def resolve_audio_provider() -> str:
    """Devuelve el proveedor de audio a usar (env o el predeterminado).

    Lee ``GEMINI_AUDIO_PROVIDER``, lo normaliza a minúsculas y usa
    ``DEFAULT_AUDIO_PROVIDER`` si no está configurada.
    """
    provider = os.environ.get("GEMINI_AUDIO_PROVIDER", "").strip().lower()
    return provider or DEFAULT_AUDIO_PROVIDER


def build_audio_provider(provider: str | None = None) -> AudioProvider:
    """Construye el proveedor de audio según el nombre indicado.

    Args:
        provider: nombre del proveedor (``gemini`` o ``synthetic``). Si es
            ``None`` se usa el resuelto por :func:`resolve_audio_provider`.

    Returns:
        Instancia concreta del proveedor seleccionado.

    Raises:
        AudioProviderError: si el nombre no es un valor soportado, o si el
            proveedor requiere configuración inválida (p. ej. API key ausente).
            No se realiza ninguna llamada externa en ese caso.
    """
    provider = provider or resolve_audio_provider()
    if provider == "gemini":
        return GoogleTTSProvider(model=resolve_tts_model())
    if provider == "synthetic":
        return SyntheticAudioProvider()
    raise AudioProviderError(
        f"Proveedor de audio no soportado: {provider!r}. "
        f"Valores válidos: {', '.join(SUPPORTED_AUDIO_PROVIDERS)}."
    )


def clean_alternate_narration(storage: LocalStorage, keep: str) -> None:
    """Elimina el audio de narración de la otra vía si quedó stale.

    Evita que un ``narration.mp3`` (Gemini) o ``narration.wav`` (synthetic) de
    una ejecución anterior entre en el manifest como activo duplicado. Solo
    toca los dos nombres de narración conocidos; no toca el resto de
    ``output/audio``.
    """
    for candidate in (OUTPUT_FILENAME, SYNTHETIC_OUTPUT_FILENAME):
        if candidate == keep:
            continue
        if storage.exists(candidate):
            try:
                storage.delete(candidate)
                logger.info(
                    "Eliminado audio stale de ejecución anterior: %s", candidate
                )
            except StorageError as exc:
                logger.warning(
                    "No se pudo eliminar el audio stale '%s': %s", candidate, exc
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
    load_project_env()

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

    provider_name = resolve_audio_provider()
    try:
        adapter = AudioAdapter(build_audio_provider(provider_name))
    except AudioError as exc:
        logger.error("Error al configurar el proveedor de TTS: %s", exc)
        return 1

    is_synthetic = provider_name == "synthetic"
    audio_format = SYNTHETIC_AUDIO_FORMAT if is_synthetic else AUDIO_FORMAT
    filename = SYNTHETIC_OUTPUT_FILENAME if is_synthetic else OUTPUT_FILENAME

    storage = LocalStorage(OUTPUT_AUDIO_DIR, auto_create=True)
    clean_alternate_narration(storage, keep=filename)
    logger.info("Generando narración (proveedor '%s')...", adapter.provider.name)
    try:
        result = adapter.generate(prompt.to_request(format=audio_format))
        path = storage.write_bytes(filename, result.content)
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
