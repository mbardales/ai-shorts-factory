"""Proveedor sintético de audio para desarrollo y pruebas.

Implementa :class:`audio.base.AudioProvider` sin depender de APIs externas ni de
proveedores comerciales. Genera un WAV PCM válido mediante la biblioteca
estándar de Python (``wave``, ``array``, ``math``, ``hashlib``).

Su objetivo es permitir validar el pipeline completo de AI Shorts Factory
(incluido el render final con audio) cuando el proveedor de TTS externo no está
disponible por cuota, credenciales o permisos.

El proveedor:
- no realiza llamadas HTTP;
- no requiere API key;
- no escribe archivos;
- devuelve un :class:`audio.base.AudioResult` con bytes WAV PCM reales;
- respeta el contrato de :class:`audio.base.AudioProvider`;
- genera audio mono 44100 Hz / 16-bit PCM por defecto;
- es determinista: la misma entrada produce exactamente los mismos bytes.

No pretende producir una voz humana real: genera un tono sintético con
frecuencia portadora por palabra (derivada del texto), suficiente para validar
el pipeline técnicamente.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import wave
from array import array
from typing import Any, Optional

from ..base import AudioFormat, AudioProvider, AudioRequest, AudioResult
from ..exceptions import AudioGenerationError
from media import AudioMetadata
from media.paths import sanitize_component


logger = logging.getLogger(__name__)


#: Frecuencia de muestreo del audio sintético (Hz).
SAMPLE_RATE = 44100
#: Canales del audio sintético (mono).
CHANNELS = 1
#: Ancho de muestra en bytes (PCM 16-bit).
SAMPLE_WIDTH = 2
#: Códec PCM del audio sintético.
CODEC = "pcm_s16le"
#: Tipo MIME del audio sintético.
MIME_TYPE = "audio/wav"
#: Modelo lógico por defecto del proveedor sintético.
DEFAULT_MODEL = "synthetic-v1"

#: Duración base y por carácter para derivar la duración del audio.
BASE_DURATION_SECONDS = 1.5
SECONDS_PER_CHAR = 0.033
MIN_DURATION_SECONDS = 1.0
MAX_DURATION_SECONDS = 60.0

#: Rango de frecuencias portadoras por palabra (Hz).
MIN_FREQ_HZ = 180
MAX_FREQ_HZ = 340

#: Amplitud pico de la señal (fracción del rango 16-bit).
PEAK_AMPLITUDE = 0.25

#: Duración del fundido de entrada/salida del audio (segundos).
FADE_SECONDS = 0.05
#: Fracción de ataque/decaimiento dentro de cada palabra.
WORD_ENVELOPE_FRACTION = 0.15


class SyntheticAudioProvider(AudioProvider):
    """Proveedor local que genera audio WAV sintético.

    Está destinado exclusivamente a desarrollo, pruebas e integración del
    pipeline. No representa síntesis de voz mediante IA.

    Args:
        model: identificador lógico del proveedor sintético.
        voice: configuración de voz (ignorada; la señal es fija para el texto).
    """

    name: str = "synthetic-audio"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        voice: Optional[Any] = None,
    ) -> None:
        super().__init__(model, voice=voice)
        self._logger = logging.getLogger(f"{__name__}.SyntheticAudioProvider")

    def generate(
        self,
        request: AudioRequest,
        **kwargs: Any,
    ) -> AudioResult:
        """Genera un WAV PCM sintético y devuelve sus bytes.

        Solo soporta el formato :class:`audio.base.AudioFormat.WAV`; cualquier
        otro formato se rechaza porque el proveedor no puede codificarlo.

        Args:
            request: solicitud del audio a sintetizar.
            **kwargs: sin efecto (la señal es fija para el texto).

        Returns:
            :class:`AudioResult` con los bytes WAV y sus metadatos.

        Raises:
            TypeError: si ``request`` no es un :class:`AudioRequest`.
            ValueError: si el texto está vacío o en blanco.
            AudioGenerationError: si el formato solicitado no es WAV.
        """
        if not isinstance(request, AudioRequest):
            raise TypeError("request debe ser un AudioRequest.")

        if request.format is not AudioFormat.WAV:
            raise AudioGenerationError(
                f"SyntheticAudioProvider solo genera audio WAV; se recibió el "
                f"formato '{request.format.value}'."
            )

        if not request.text or not request.text.strip():
            raise ValueError("El texto de la narración no puede estar vacío.")

        text = request.text
        duration = self._duration_seconds(text)
        frequencies = self._word_frequencies(text)
        pcm = self._build_pcm(
            text=text,
            duration_seconds=duration,
            frequencies=frequencies,
        )
        content = self._build_wav(pcm)

        metadata = AudioMetadata(
            name=sanitize_component(text[:60], fallback="narracion"),
            mime_type=MIME_TYPE,
            size_bytes=len(content),
            duration_seconds=duration,
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
            bit_rate_bps=SAMPLE_RATE * CHANNELS * 8 * SAMPLE_WIDTH,
            codec=CODEC,
        )

        self._logger.debug(
            "Audio sintético generado: %.2f s, %d bytes.",
            duration,
            len(content),
        )

        return AudioResult(
            text=text,
            content=content,
            metadata=metadata,
            model=self.model,
        )

    # ------------------------------------------------------------------
    # Ayudantes privados
    # ------------------------------------------------------------------

    @classmethod
    def _duration_seconds(cls, text: str) -> float:
        """Deriva la duración del audio de forma determinista desde el texto.

        Duración base más un factor por carácter, acotada a un rango razonable.
        """
        duration = BASE_DURATION_SECONDS + len(text) * SECONDS_PER_CHAR
        return max(MIN_DURATION_SECONDS, min(duration, MAX_DURATION_SECONDS))

    @staticmethod
    def _word_frequencies(text: str) -> list[float]:
        """Devuelve una frecuencia portadora por palabra (determinista).

        Cada frecuencia se deriva de un hash SHA-256 de la palabra, por lo que
        el mismo texto produce siempre la misma secuencia de frecuencias.
        """
        words = text.split()
        if not words:
            words = [text]
        frequencies: list[float] = []
        for word in words:
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            value = int.from_bytes(digest[:4], "big")
            frequency = MIN_FREQ_HZ + (value % (MAX_FREQ_HZ - MIN_FREQ_HZ))
            frequencies.append(float(frequency))
        return frequencies

    @staticmethod
    def _build_pcm(
        *,
        text: str,
        duration_seconds: float,
        frequencies: list[float],
    ) -> bytes:
        """Construye los frames PCM 16-bit del audio sintético.

        Cada palabra ocupa un segmento de la misma duración y modula una
        portadora sinusoidal propia; la fase es continua entre palabras para
        evitar clics. Se aplica un sobre de ataque/decaimiento por palabra y un
        fundido global de entrada/salida.
        """
        num_frames = max(int(duration_seconds * SAMPLE_RATE), 1)
        num_words = len(frequencies)
        frames_per_word = num_frames / num_words

        samples = array("h")
        phase = 0.0
        for frame in range(num_frames):
            time_seconds = frame / SAMPLE_RATE
            word_index = min(int(frame / frames_per_word), num_words - 1)
            frequency = frequencies[word_index]
            phase += 2.0 * math.pi * frequency / SAMPLE_RATE

            local = (frame % frames_per_word) / frames_per_word
            word_env = min(
                local / WORD_ENVELOPE_FRACTION,
                (1.0 - local) / WORD_ENVELOPE_FRACTION,
                1.0,
            )
            word_env = max(word_env, 0.0)

            global_env = min(
                time_seconds / FADE_SECONDS,
                (duration_seconds - time_seconds) / FADE_SECONDS,
                1.0,
            )
            global_env = max(global_env, 0.0)

            value = int(
                32767 * PEAK_AMPLITUDE * word_env * global_env * math.sin(phase)
            )
            samples.append(value)

        return samples.tobytes()

    @staticmethod
    def _build_wav(pcm: bytes) -> bytes:
        """Envuelve los frames PCM en un contenedor WAV válido (stdlib)."""
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(SAMPLE_WIDTH)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(pcm)
        return buffer.getvalue()
