"""Worker aislado de Kokoro TTS para AI Shorts Factory.

Ejecuta la síntesis de voz con Kokoro-82M fuera del intérprete principal del
proyecto. Kokoro (y su dependencia PyTorch) requiere Python <3.13, mientras que
el runtime de AI Shorts Factory usa Python 3.14; este worker se ejecuta con un
intérprete Python 3.12 dedicado (ver ``KOKORO_PYTHON`` en ``.env.example``).

Protocolo:
    El worker lee un único objeto JSON desde ``stdin`` con los campos:

    - ``text`` (str, obligatorio): texto a sintetizar en español.
    - ``voice`` (str, opcional): voz de Kokoro (por defecto ``ef_dora``).
    - ``output`` (str, obligatorio): ruta absoluta del WAV a escribir.

    Y escribe un único objeto JSON en ``stdout`` con el resultado:

    - Éxito: ``{"ok": true, "output": ..., "model": "kokoro-82m",
      "sample_rate": 24000, "channels": 1, "duration_seconds": ...,
      "size_bytes": ...}``.
    - Error: ``{"ok": false, "error": "..."}``.

    Los logs, avisos y trazas van EXCLUSIVAMENTE a ``stderr`` para no corromper
    el canal de datos de ``stdout``.

Códigos de salida:
    ``0`` éxito; cualquier otro valor indica error.

Restricciones:
    - No realiza llamadas HTTP ni requiere API key (el modelo se descarga y
      cachea por Hugging Face en el entorno del worker).
    - ``output`` debe ser una ruta absoluta terminada en ``.wav``, sin ``..``.
    - Genera WAV PCM mono 24000 Hz / 16-bit (formato nativo de Kokoro).
"""

from __future__ import annotations

import io
import json
import sys
import traceback
import wave
from pathlib import Path

#: Frecuencia de muestreo nativa de Kokoro (Hz).
SAMPLE_RATE = 24000
#: Modelo usado por el worker.
MODEL = "kokoro-82m"
#: Voz por defecto (español, femenina).
DEFAULT_VOICE = "ef_dora"

#: Stream real de ``stdout`` (canal de datos). Las librerías de Kokoro/HF
#: escriben avisos en ``sys.stdout``; se redirigen a un buffer descartable para
#: que el canal de datos contenga únicamente el JSON final.
_DATA_STDOUT: "io.TextIOBase" = sys.stdout


def _emit(stream: "io.TextIOBase", payload: dict) -> None:
    """Escribe el JSON de resultado en ``stream`` (un único objeto por línea)."""
    stream.write(json.dumps(payload, ensure_ascii=False))
    stream.write("\n")
    stream.flush()


def _fail(code: int, message: str, *, trace: str | None = None) -> int:
    """Escribe el JSON de error en ``stdout`` y devuelve el código de salida."""
    if trace:
        print(trace, file=sys.stderr)
    _emit(_DATA_STDOUT, {"ok": False, "error": message})
    return code


def _validate_output_path(raw: str) -> Path:
    """Valida la ruta de salida proporcionada por el proveedor.

    Solo se aceptan rutas absolutas terminadas en ``.wav`` y sin componentes
    ``..``, para evitar escrituras fuera del directorio temporal del proveedor.
    """
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError(f"'output' debe ser una ruta absoluta: {raw!r}.")
    if path.suffix.lower() != ".wav":
        raise ValueError(f"'output' debe terminar en '.wav': {raw!r}.")
    if ".." in path.parts:
        raise ValueError(f"'output' no puede contener '..': {raw!r}.")
    return path


def _generate_wav(text: str, voice: str, output_path: Path) -> dict:
    """Sintetiza el texto con Kokoro y escribe el WAV en ``output_path``."""
    from kokoro import KPipeline
    import numpy as np
    import torch

    pipeline = KPipeline(lang_code="e")
    segments = list(pipeline(text, voice=voice, speed=1.0))
    if not segments:
        raise RuntimeError("Kokoro no devolvió ningún segmento de audio.")

    audio = torch.cat([segment[2] for segment in segments], dim=0)
    samples = np.clip(audio.detach().cpu().numpy(), -1.0, 1.0)
    pcm = (samples * 32767.0).astype(np.int16)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())

    duration_seconds = len(pcm) / SAMPLE_RATE
    return {
        "ok": True,
        "output": str(output_path),
        "model": MODEL,
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "duration_seconds": round(float(duration_seconds), 4),
        "size_bytes": output_path.stat().st_size,
    }


def main() -> int:
    """Punto de entrada del worker. Devuelve el código de salida."""
    try:
        sys.stdin.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    # Redirigir stdout a un buffer descartable: cualquier aviso que Kokoro/HF
    # escriba en stdout no debe corromper el JSON de resultado.
    sys.stdout = io.StringIO()
    try:
        return _run()
    finally:
        sys.stdout = _DATA_STDOUT


def _run() -> int:
    """Lee el payload, sintetiza el WAV y emite el JSON de resultado."""
    raw = sys.stdin.read()
    if not raw.strip():
        return _fail(2, "No se recibió JSON por stdin.")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _fail(3, f"El JSON de entrada es inválido: {exc}.")

    text = str(payload.get("text") or "")
    voice = str(payload.get("voice") or DEFAULT_VOICE)
    output_raw = str(payload.get("output") or "")

    if not text.strip():
        return _fail(4, "El campo 'text' no puede estar vacío.")
    try:
        output_path = _validate_output_path(output_raw)
    except ValueError as exc:
        return _fail(5, str(exc))

    try:
        result = _generate_wav(text, voice, output_path)
    except Exception as exc:  # noqa: BLE001 - el worker aísla todos los errores
        return _fail(
            6,
            f"Error al sintetizar audio con Kokoro: {exc}",
            trace=traceback.format_exc(),
        )

    _emit(_DATA_STDOUT, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())