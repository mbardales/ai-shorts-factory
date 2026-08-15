"""Alineación local palabra-a-palabra de subtítulos (sin dependencias cloud).

Kokoro-82M (``KPipeline``) no expone marcas de tiempo por palabra: sus
segmentos son ``(graphemes, phonemes, audio)`` sin timestamps. Para alinear los
subtítulos al audio real se implementa un fallback determinista y puramente
local sobre el propio WAV sintetizado:

1. Se decodifican las muestras PCM del WAV (stdlib ``wave`` + ``array``).
2. Se detectan los intervalos de silencio por energía de tramas (RMS).
3. Cada palabra recibe un intervalo proporcional a su número de caracteres
   alfanuméricos repartido sobre la duración hablada real, y esos tiempos se
   proyectan a la línea de tiempo saltando los silencios.

El resultado son marcas de tiempo válidas (ordenadas y dentro de la duración
del audio) que respetan las pausas reales de la voz, sin red, sin APIs y sin
dependencias de terceros. Es un módulo puro: no accede al filesystem y no
ejecuta procesos; recibe los bytes del WAV en memoria.
"""

from __future__ import annotations

import array
import io
import math
import sys
import wave
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

#: Tamaño de trama para el análisis de energía (segundos).
FRAME_SECONDS = 0.025
#: Piso absoluto de energía (RMS en [-1, 1]) para considerar "habla".
SPEECH_FLOOR = 0.004
#: Fracción del pico de energía para el umbral relativo de "habla".
SPEECH_PEAK_RATIO = 0.12
#: Silencios más cortos que este valor (segundos) se ignoran (micropausas).
MIN_GAP_SECONDS = 0.06


@dataclass(frozen=True)
class WordTiming:
    """Marca de tiempo de una palabra de la narración.

    Attributes:
        start: instante de inicio en segundos (>= 0).
        end: instante de fin en segundos (<= duración del audio).
        text: palabra mostrada (puede incluir puntuación adjunta).
    """

    start: float
    end: float
    text: str


def _read_wav_samples(wav_bytes: bytes) -> Tuple[int, list[float]]:
    """Decodifica un WAV PCM en muestras mono en el rango [-1, 1].

    Admite WAV PCM 8-bit (sin signo) y 16-bit (signado, orden nativo o LE) con
    cualquier número de canales (se usa el primero). Devuelve ``(rate, muestras)``.

    Raises:
        ValueError: si el WAV no es PCM 8/16 bits o no se puede leer.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
    except (EOFError, wave.Error) as exc:
        raise ValueError(f"WAV no interpretable: {exc}") from exc
    if rate <= 0:
        raise ValueError("WAV sin frecuencia de muestreo válida.")
    if sample_width == 2:
        values = array.array("h")
        values.frombytes(raw)
        if sys.byteorder != "little":
            values.byteswap()
        mono = [value / 32768.0 for value in values[:: max(channels, 1)]]
    elif sample_width == 1:
        values = array.array("B")
        values.frombytes(raw)
        mono = [value / 128.0 - 1.0 for value in values[:: max(channels, 1)]]
    else:
        raise ValueError(
            f"WAV con ancho de muestra no soportado: {sample_width * 8} bits."
        )
    return rate, mono


def _silence_intervals(samples: Sequence[float], rate: int) -> list[Tuple[float, float]]:
    """Detecta intervalos de silencio por energía de tramas (RMS).

    Un trama es "habla" si su RMS supera el máximo entre un piso absoluto y una
    fracción del pico de energía del archivo. Los silencios se devuelven como
    pares ``(inicio, fin)`` en segundos, ignorando las micropausas menores que
    ``MIN_GAP_SECONDS``.
    """
    frame = max(1, int(rate * FRAME_SECONDS))
    count = max(1, len(samples) // frame)
    rms_values: list[float] = []
    for index in range(count):
        block = samples[index * frame : (index + 1) * frame]
        if not block:
            continue
        mean_sq = sum(value * value for value in block) / len(block)
        rms_values.append(math.sqrt(mean_sq))
    if not rms_values:
        return []
    peak = max(rms_values)
    threshold = max(peak * SPEECH_PEAK_RATIO, SPEECH_FLOOR)

    gaps: list[Tuple[float, float]] = []
    gap_start: Optional[float] = None
    for index, rms in enumerate(rms_values):
        frame_start = index * frame / rate
        frame_end = (index + 1) * frame / rate
        if rms < threshold:
            if gap_start is None:
                gap_start = frame_start
        elif gap_start is not None:
            gaps.append((gap_start, frame_start))
            gap_start = None
    if gap_start is not None:
        gaps.append((gap_start, count * frame / rate))

    duration = len(samples) / rate
    return [
        (start, min(end, duration))
        for start, end in gaps
        if end - start >= MIN_GAP_SECONDS
    ]


def _speech_intervals(
    duration: float, silences: Sequence[Tuple[float, float]]
) -> list[Tuple[float, float]]:
    """Devuelve los intervalos de habla (complemento de los silencios)."""
    speech: list[Tuple[float, float]] = []
    cursor = 0.0
    for start, end in sorted(silences):
        if start > cursor:
            speech.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        speech.append((cursor, duration))
    return speech


def _count_alphanumeric(token: str) -> int:
    """Cuenta caracteres alfanuméricos (peso de la palabra en la voz)."""
    return sum(1 for char in token if char.isalnum())


def align_word_timings(text: str, wav_bytes: bytes) -> Optional[list[WordTiming]]:
    """Alinea las palabras de ``text`` a la duración real del WAV.

    Cada palabra recibe un intervalo proporcional a sus caracteres
    alfanuméricos, repartido sobre la duración hablada real (duración total
    menos silencios). Los tiempos se proyectan a la línea de tiempo saltando
    los silencios detectados, por lo que las pausas reales de la voz quedan
    reflejadas en los límites entre palabras.

    Args:
        text: texto hablado (narración) tal y como se envía al TTS.
        wav_bytes: contenido del WAV PCM real sintetizado.

    Returns:
        Lista de :class:`WordTiming` ordenada y dentro de la duración del
        audio, o ``None`` si no se puede derivar una alineación válida (WAV
        ilegible, sin habla detectable o sin palabras con contenido).
    """
    if not text or not text.strip():
        return None
    try:
        rate, samples = _read_wav_samples(wav_bytes)
    except (ValueError, EOFError, wave.Error):
        return None
    if not samples:
        return None

    duration = len(samples) / rate
    silences = _silence_intervals(samples, rate)
    speech = _speech_intervals(duration, silences)
    speech_budget = sum(end - start for start, end in speech)
    if speech_budget <= 0:
        return None

    def to_clock(target: float) -> float:
        """Proyecta una cantidad de tiempo hablado a la línea de tiempo real."""
        remaining = target
        for start, end in speech:
            length = end - start
            if remaining <= length:
                return start + remaining
            remaining -= length
        return duration

    tokens = text.split()
    weights = [_count_alphanumeric(token) for token in tokens]
    total_weight = sum(weights)
    if total_weight <= 0:
        return None

    content_times: list[Tuple[float, float]] = []
    cumulative = 0.0
    for weight in weights:
        start = cumulative
        end = cumulative + weight * speech_budget / total_weight
        cumulative = end
        content_times.append((start, end))

    timings: list[WordTiming] = []
    content_index = 0
    punctuation_prefix = ""
    for token, weight in zip(tokens, weights):
        if weight > 0:
            start, end = content_times[content_index]
            content_index += 1
            clock_start = to_clock(start)
            clock_end = to_clock(end)
            clock_start = max(0.0, clock_start)
            clock_end = min(duration, max(clock_start, clock_end))
            display = punctuation_prefix + token
            punctuation_prefix = ""
            timings.append(WordTiming(clock_start, clock_end, display))
        elif timings:
            previous = timings[-1]
            timings[-1] = WordTiming(
                previous.start,
                previous.end,
                previous.text + token,
            )
        else:
            punctuation_prefix += token

    if not timings:
        return None
    if any(timing.end <= timing.start for timing in timings):
        return None
    return timings
