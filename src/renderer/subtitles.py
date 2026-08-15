"""Generador de subtítulos ASS puro (sin dependencias cloud).

Construye el contenido de un archivo Advanced SubStation Alpha (ASS) a partir
del texto de la narración y de la duración real del audio, listo para renderizar
con el filtro ``ass`` de FFmpeg (libass).

Si se dispone de marcas de tiempo palabra-a-palabra derivadas del audio real
(ver :mod:`renderer.alignment`), los avisos se temporizan con esas marcas
(estilo palabra a palabra, máximo tres palabras y dos líneas por aviso). Sin
marcas, el texto se segmenta en avisos de como máximo dos líneas con
temporización proporcional a la longitud de cada aviso sobre la duración total
del audio. El estilo está pensado para video vertical 720x1280: texto legible
con contorno, centrado en la zona segura inferior.

Este módulo es puro: no accede al filesystem, no ejecuta procesos y no depende
de FFmpeg ni de servicios externos.
"""

from __future__ import annotations

import re
from typing import Optional

from .alignment import WordTiming

#: Ancho de canvas de subtítulos (coincide con el video vertical 720x1280).
PLAY_RES_X = 720

#: Alto de canvas de subtítulos (coincide con el video vertical 720x1280).
PLAY_RES_Y = 1280

#: Caracteres máximos por línea de subtítulo (para 720 px de ancho).
MAX_LINE_CHARS = 26

#: Líneas máximas por aviso de subtítulo.
MAX_CUE_LINES = 2

#: Palabras máximas por aviso cuando hay alineación palabra-a-palabra.
MAX_CUE_WORDS = 3

#: Nombre de la fuente (Arial está disponible en Windows).
FONT_NAME = "Arial"

#: Tamaño de fuente en unidades ASS (relativo a PlayResX/PlayResY).
FONT_SIZE = 40

#: Margen inferior en unidades ASS (zona segura inferior).
MARGIN_V = 70

#: Separador de oraciones: puntuación seguida de espacio.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def _split_sentences(text: str) -> list[str]:
    """Divide el texto en oraciones conservando los signos de puntuación."""
    normalized = text.replace("\r", " ").replace("\n", " ").strip()
    if not normalized:
        return []
    return [
        part.strip()
        for part in _SENTENCE_SPLIT_RE.split(normalized)
        if part.strip()
    ]


def _split_into_lines(text: str, max_chars: int) -> list[str]:
    """Divide el texto en líneas de como máximo ``max_chars`` caracteres.

    El corte respeta los límites de palabra siempre que sea posible; las
    palabras excepcionalmente largas se parten al tamaño máximo.
    """
    lines: list[str] = []
    for sentence in _split_sentences(text):
        words = sentence.split()
        line = ""
        for word in words:
            while len(word) > max_chars:
                if line:
                    lines.append(line)
                    line = ""
                lines.append(word[:max_chars])
                word = word[max_chars:]
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= max_chars:
                line = f"{line} {word}"
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
    return lines


def _group_cues(lines: list[str], max_lines: int) -> list[list[str]]:
    """Agrupa líneas consecutivas en avisos de como máximo ``max_lines`` líneas."""
    return [lines[i : i + max_lines] for i in range(0, len(lines), max_lines)]


def _format_timestamp(seconds: float) -> str:
    """Formatea segundos como marca de tiempo ASS (H:MM:SS.CC)."""
    total_cs = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(total_cs, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _ass_header() -> str:
    """Cabecera + estilos ASS para subtítulos legibles en zona segura inferior."""
    return "\n".join(
        (
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {PLAY_RES_X}",
            f"PlayResY: {PLAY_RES_Y}",
            "WrapStyle: 0",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Default,{FONT_NAME},{FONT_SIZE},&H00FFFFFF,&H000000FF,"
            f"&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,60,60,"
            f"{MARGIN_V},1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text",
        )
    ) + "\n"


def _build_word_events(
    word_timings: list[WordTiming], duration: float
) -> list[str]:
    """Construye avisos ASS palabra-a-palabra desde marcas de tiempo reales.

    Agrupa las palabras en avisos de como máximo ``MAX_CUE_WORDS`` palabras y
    ``MAX_CUE_LINES`` líneas, respetando los caracteres por línea. Cada aviso
    empieza cuando se habla su primera palabra y termina al acabar su última,
    por lo que los subtítulos avanzan con el audio real. Las marcas se recortan
    al rango ``[0, duration]`` para evitar subtítulos fuera de pantalla.
    """
    cues: list[list[WordTiming]] = []
    for timing in word_timings:
        if timing.end <= timing.start:
            continue
        if cues and len(cues[-1]) < MAX_CUE_WORDS:
            trial = cues[-1] + [timing]
            joined = " ".join(word.text for word in trial)
            if len(_split_into_lines(joined, MAX_LINE_CHARS)) <= MAX_CUE_LINES:
                cues[-1] = trial
                continue
        cues.append([timing])

    events: list[str] = []
    for cue in cues:
        start = max(0.0, cue[0].start)
        end = min(duration, cue[-1].end)
        if end <= start:
            end = min(duration, start + 0.1)
        joined = " ".join(word.text for word in cue)
        display = "\\N".join(_split_into_lines(joined, MAX_LINE_CHARS))
        events.append(
            f"Dialogue: 0,{_format_timestamp(start)},{_format_timestamp(end)},"
            f"Default,,0,0,0,,{display}"
        )
    return events


def build_ass_subtitles(
    text: str,
    duration_seconds: float,
    word_timings: Optional[list[WordTiming]] = None,
) -> str:
    """Construye el contenido ASS de subtítulos desde la narración.

    Si se proporcionan marcas de tiempo palabra-a-palabra (de
    :func:`renderer.alignment.align_word_timings`), los avisos se temporizan con
    esas marcas reales del audio (hasta ``MAX_CUE_WORDS`` palabras y dos
    líneas); en caso contrario, el texto se segmenta en avisos de como máximo
    dos líneas y cada aviso se temporiza de forma proporcional a su longitud
    sobre la duración total; el último aviso siempre termina exactamente en
    ``duration_seconds``.

    Args:
        text: texto hablado (narración) a mostrar como subtítulos.
        duration_seconds: duración real del audio en segundos (referencia de
            temporización de los subtítulos).
        word_timings: marcas de tiempo por palabra opcionales; si son válidas,
            se usan en lugar de la temporización proporcional.

    Returns:
        Contenido del archivo ASS listo para escribir en disco. Si ``text``
        está vacío o la duración no es positiva, devuelve solo la cabecera
        (sin avisos).
    """
    duration = float(duration_seconds) if duration_seconds is not None else 0.0
    if not text or not text.strip() or duration <= 0:
        return _ass_header()

    if word_timings:
        word_events = _build_word_events(list(word_timings), duration)
        if word_events:
            return _ass_header() + "\n".join(word_events) + "\n"

    lines = _split_into_lines(text, MAX_LINE_CHARS)
    cues = _group_cues(lines, MAX_CUE_LINES)
    if not cues:
        return _ass_header()

    total_chars = max(1, sum(len(" ".join(cue)) for cue in cues))
    events: list[str] = []
    start = 0.0
    for index, cue in enumerate(cues):
        end = duration if index == len(cues) - 1 else start + (
            duration * sum(len(part) for part in cue) / total_chars
        )
        display = "\\N".join(part for part in cue if part)
        events.append(
            f"Dialogue: 0,{_format_timestamp(start)},{_format_timestamp(end)},"
            f"Default,,0,0,0,,{display}"
        )
        start = end

    return _ass_header() + "\n".join(events) + "\n"
