"""Proveedor de TTS local Kokoro (kokoro-82m).

Implementa :class:`audio.base.AudioProvider` delegando la síntesis en un worker
aislado de Python 3.12 (``scripts/kokoro_worker.py``). Kokoro y su dependencia
PyTorch requieren Python <3.13, por lo que el proveedor ejecuta el worker con
un intérprete dedicado en lugar de importar Kokoro en el runtime principal
(Python 3.14).

Características:

- TTS local/offline: una vez descargado el modelo (cache de Hugging Face), no
  requiere red ni API key.
- Voz configurable por constructor/``kwargs`` (por defecto ``ef_dora``).
- Genera WAV PCM mono 24000 Hz / 16-bit.
- Opera en un directorio temporal que siempre se limpia (incluso en error).
- Respeta el contrato de :class:`audio.base.AudioProvider` sin añadir campos.

El intérprete del worker se configura con ``KOKORO_PYTHON`` (por defecto
``.venv-kokoro/Scripts/python.exe`` en la raíz del proyecto); si no existe o no
tiene Kokoro instalado, el proveedor lanza :class:`audio.exceptions.AudioProviderError`.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Optional

from ..base import AudioFormat, AudioProvider, AudioRequest, AudioResult
from ..exceptions import AudioGenerationError, AudioProviderError
from media import AudioMetadata
from media.paths import sanitize_component


logger = logging.getLogger(__name__)


#: Variable de entorno que configura el intérprete Python 3.12 de Kokoro.
KOKORO_PYTHON_ENV = "KOKORO_PYTHON"
#: Ruta del intérprete por defecto, relativa a la raíz del proyecto.
DEFAULT_KOKORO_PYTHON_REL = Path(".venv-kokoro") / "Scripts" / "python.exe"
#: Modelo por defecto del proveedor.
DEFAULT_MODEL = "kokoro-82m"
#: Voz por defecto (español, femenina).
DEFAULT_VOICE = "ef_dora"
#: Frecuencia de muestreo del WAV generado por Kokoro (Hz).
SAMPLE_RATE = 24000
#: Canales del WAV generado (mono).
CHANNELS = 1
#: Ancho de muestra en bytes (PCM 16-bit).
SAMPLE_WIDTH = 2
#: Códec PCM del WAV generado.
CODEC = "pcm_s16le"
#: Tipo MIME del WAV generado.
MIME_TYPE = "audio/wav"
#: Tiempo límite de ejecución del worker (segundos).
DEFAULT_TIMEOUT_SECONDS = 180.0


class KokoroAudioProvider(AudioProvider):
    """Proveedor de TTS local que ejecuta Kokoro-82M en un worker Python 3.12.

    Args:
        model: identificador del modelo Kokoro.
        voice: configuración de voz; solo se usa ``name`` (ej. ``"af_heart"``).
        timeout_seconds: tiempo límite de ejecución del worker.
    """

    name: str = "kokoro-audio"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        voice: Optional[Any] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(model, voice=voice)
        self._timeout_seconds = timeout_seconds
        self._root = Path(__file__).resolve().parents[3]
        self._worker_path = self._root / "scripts" / "kokoro_worker.py"
        self._logger = logging.getLogger(f"{__name__}.KokoroAudioProvider")

    def generate(
        self,
        request: AudioRequest,
        **kwargs: Any,
    ) -> AudioResult:
        """Sintetiza la narración con Kokoro y devuelve los bytes WAV.

        Args:
            request: solicitud del audio a sintetizar.
            **kwargs: ``name`` (voz de Kokoro) sobrescribe la voz configurada.

        Returns:
            :class:`AudioResult` con los bytes WAV y sus metadatos.

        Raises:
            TypeError: si ``request`` no es un :class:`AudioRequest`.
            ValueError: si el texto está vacío o en blanco.
            AudioGenerationError: si el formato no es WAV o el worker falla.
            AudioProviderError: si falta el intérprete de Kokoro o el worker
                no se puede ejecutar.
        """
        if not isinstance(request, AudioRequest):
            raise TypeError("request debe ser un AudioRequest.")
        if request.format is not AudioFormat.WAV:
            raise AudioGenerationError(
                f"KokoroAudioProvider solo genera audio WAV; se recibió el "
                f"formato '{request.format.value}'."
            )
        if not request.text or not request.text.strip():
            raise ValueError("El texto de la narración no puede estar vacío.")

        voice = self._resolve_voice_name(**kwargs)
        interpreter = self._resolve_kokoro_python()
        self._validate_interpreter(interpreter)

        temporary_dir = Path(tempfile.mkdtemp(prefix="kokoro-audio-"))
        try:
            output_path = temporary_dir / "narration.wav"
            payload = {
                "text": request.text,
                "voice": voice,
                "output": str(output_path),
            }
            argv = [str(interpreter), str(self._worker_path)]
            self._logger.debug("Ejecutando worker de Kokoro: %s", " ".join(argv))
            try:
                completed = subprocess.run(
                    argv,
                    input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    capture_output=True,
                    shell=False,
                    check=False,
                    timeout=self._timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise AudioProviderError(
                    f"El worker de Kokoro superó el tiempo límite "
                    f"({self._timeout_seconds:.0f} s)."
                ) from exc
            except OSError as exc:
                raise AudioProviderError(
                    f"No se pudo ejecutar el worker de Kokoro: {exc}"
                ) from exc

            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            if completed.returncode != 0:
                raise AudioGenerationError(
                    f"El worker de Kokoro falló (exit {completed.returncode}): "
                    f"{stderr or 'sin detalle en stderr.'}"
                )

            try:
                response = json.loads(
                    completed.stdout.decode("utf-8", errors="replace") or "{}"
                )
            except json.JSONDecodeError as exc:
                raise AudioGenerationError(
                    "El worker de Kokoro devolvió una respuesta JSON inválida: "
                    f"{exc}"
                ) from exc

            if not response.get("ok"):
                raise AudioGenerationError(
                    "Kokoro no generó el audio: "
                    f"{response.get('error') or 'error desconocido.'}"
                )

            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise AudioGenerationError(
                    "Kokoro terminó sin generar un WAV válido."
                )

            content = output_path.read_bytes()
            duration = self._wav_duration_seconds(content)
            metadata = AudioMetadata(
                name=sanitize_component(request.text[:60], fallback="narracion"),
                mime_type=MIME_TYPE,
                size_bytes=len(content),
                duration_seconds=duration,
                sample_rate=SAMPLE_RATE,
                channels=CHANNELS,
                bit_rate_bps=SAMPLE_RATE * CHANNELS * 8 * SAMPLE_WIDTH,
                codec=CODEC,
            )
            self._logger.debug(
                "Audio Kokoro generado: %.2f s, %d bytes.", duration, len(content)
            )
            return AudioResult(
                text=request.text,
                content=content,
                metadata=metadata,
                model=self.model,
            )
        finally:
            shutil.rmtree(temporary_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Ayudantes privados
    # ------------------------------------------------------------------

    def _resolve_voice_name(self, **kwargs: Any) -> str:
        """Devuelve el nombre de voz de Kokoro a utilizar.

        Prioridad: ``kwargs["name"]``, la voz configurada en el constructor y,
        si ninguno está presente, la voz por defecto del proveedor. El nombre de
        voz del ``AudioRequest`` no se interpreta como voz de Kokoro.
        """
        name = kwargs.get("name") or (self.voice.name if self.voice else None)
        return name or DEFAULT_VOICE

    def _resolve_kokoro_python(self) -> Path:
        """Resuelve el intérprete Python 3.12 de Kokoro (env o por defecto).

        ``KOKORO_PYTHON`` puede ser una ruta absoluta o relativa a la raíz del
        proyecto; si no está definido, se usa ``.venv-kokoro/Scripts/python.exe``.
        """
        raw = os.environ.get(KOKORO_PYTHON_ENV, "").strip()
        if not raw:
            return self._root / DEFAULT_KOKORO_PYTHON_REL
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate
        return self._root / candidate

    def _validate_interpreter(self, interpreter: Path) -> None:
        """Valida que el intérprete de Kokoro exista antes de ejecutarlo."""
        if not interpreter.is_file():
            raise AudioProviderError(
                f"No se encontró el intérprete de Kokoro: {interpreter}. "
                f"Instala Kokoro en un Python 3.12 (p. ej. .venv-kokoro) o "
                f"define {KOKORO_PYTHON_ENV} con la ruta al python.exe."
            )

    @staticmethod
    def _wav_duration_seconds(content: bytes) -> float:
        """Lee la duración real del WAV desde sus bytes (stdlib)."""
        with wave.open(io.BytesIO(content), "rb") as wav:
            return wav.getnframes() / max(wav.getframerate(), 1)
