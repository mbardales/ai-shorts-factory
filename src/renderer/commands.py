"""Capa pura de construcción de comandos FFmpeg.

Representa y construye comandos FFmpeg como listas de argumentos **sin
ejecutarlos**. Este módulo no accede al filesystem, no lanza procesos ni importa
``subprocess``: solo produce la especificación tipada de un comando
(:class:`FFmpegCommand`) y su forma ``argv``.

- :class:`FFmpegInput`: entrada (archivo de origen).
- :class:`FFmpegOutput`: salida (archivo de destino y formato).
- :class:`FFmpegCommand`: comando completo (ejecutable + argumentos).
- :func:`build_ffmpeg_command`: constructor determinista de comandos.

Las validaciones son estructurales (no comprueban que los archivos existan ni
que FFmpeg esté instalado) y lanzan :class:`~renderer.exceptions.RendererValidationError`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .exceptions import RendererValidationError

#: Fotogramas por segundo por defecto para la composición de imágenes.
DEFAULT_FPS = 25

#: Códec de video por defecto para la salida compuesta.
DEFAULT_VIDEO_CODEC = "libx264"

#: Formato de píxeles por defecto de la salida (compatible con H.264/MP4).
DEFAULT_PIX_FMT = "yuv420p"

#: Códec de audio por defecto cuando la composición incluye pistas de audio.
DEFAULT_AUDIO_CODEC = "aac"


@dataclass(frozen=True)
class FFmpegInput:
    """Entrada de un comando FFmpeg.

    Attributes:
        path: ruta del archivo de entrada.
    """

    path: Path


@dataclass(frozen=True)
class FFmpegOutput:
    """Salida de un comando FFmpeg.

    Attributes:
        path: ruta del archivo de salida.
        format: formato de salida (extensión, ej. ``"mp4"``).
    """

    path: Path
    format: str


@dataclass(frozen=True)
class FFmpegCommand:
    """Comando FFmpeg completo, representado de forma inmutable.

    Attributes:
        executable: binario de FFmpeg a invocar.
        arguments: argumentos del comando, en orden.
    """

    executable: str
    arguments: tuple[str, ...] = ()

    def to_argv(self) -> list[str]:
        """Devuelve el comando como lista de argumentos ``argv``.

        Returns:
            ``[executable, *arguments]``.
        """
        return [self.executable, *self.arguments]


def _is_valid_path(value: Any) -> bool:
    """True si el valor es un :class:`Path` con ruta y nombre no vacíos.

    Validación estructural: no comprueba la existencia física del archivo.
    """
    return isinstance(value, Path) and bool(str(value).strip()) and bool(value.name)


def _validate_command_args(
    inputs: Sequence[FFmpegInput],
    output: FFmpegOutput,
    options: Sequence[str],
    executable: str,
) -> list[str]:
    """Valida estructuralmente los argumentos del comando.

    Returns:
        Lista de errores; vacía si los argumentos son válidos.
    """
    errors: list[str] = []

    if not executable or not executable.strip():
        errors.append("El ejecutable no puede estar vacío.")

    if inputs is None or not isinstance(inputs, Sequence) or isinstance(inputs, (str, bytes)):
        errors.append("'inputs' debe ser una secuencia de FFmpegInput.")
    else:
        for index, item in enumerate(inputs):
            if not isinstance(item, FFmpegInput):
                errors.append(f"El input en la posición {index} no es un FFmpegInput.")
                continue
            if not _is_valid_path(item.path):
                errors.append(f"El input en la posición {index} no tiene un Path válido.")

    if not isinstance(output, FFmpegOutput):
        errors.append("'output' debe ser un FFmpegOutput.")
    else:
        if not _is_valid_path(output.path):
            errors.append("'output.path' no es un Path válido.")
        if not output.format or not output.format.strip():
            errors.append("'output.format' no puede estar vacío.")

    if options is None or not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        errors.append("'options' debe ser una secuencia de cadenas.")
    else:
        for index, option in enumerate(options):
            if not isinstance(option, str):
                errors.append(f"La opción en la posición {index} no es una cadena.")

    return errors


def _format_duration(value: float) -> str:
    """Formatea una duración en segundos como cadena FFmpeg."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def _build_composed_arguments(
    inputs: Sequence[FFmpegInput],
    *,
    durations: Sequence[Optional[float]],
    fps: int,
    audio_codec: Optional[str] = None,
) -> list[str]:
    """Construye los argumentos de un comando FFmpeg con composición de imágenes.

    Cada entrada con duración se convierte en un segmento de video con
    ``-loop 1 -t <dur>`` y todos los segmentos se concatenan en orden mediante
    ``filter_complex`` + ``concat``. Las entradas sin duración (p. ej. audio)
    se conservan como inputs simples.

    Si existen entradas sin duración (pistas de audio), se mapean como salida
    adicional usando su índice real de input, se codifican con
    ``audio_codec`` (o ``DEFAULT_AUDIO_CODEC``) y se recorta la salida con
    ``-shortest`` para sincronizarla con el video.

    Returns:
        Lista de argumentos previa a la salida (sin incluir ``-f``/ruta).
    """
    arguments: list[str] = []
    input_index = 0
    filter_labels: list[str] = []
    audio_indices: list[int] = []
    for item, duration in zip(inputs, durations):
        if duration is None:
            arguments.extend(("-i", str(item.path)))
            audio_indices.append(input_index)
            input_index += 1
            continue
        arguments.extend(
            (
                "-loop",
                "1",
                "-framerate",
                str(fps),
                "-t",
                _format_duration(float(duration)),
                "-i",
                str(item.path),
            )
        )
        filter_labels.append(f"[{input_index}:v]")
        input_index += 1

    concat_inputs = "".join(filter_labels)
    arguments.extend(
        (
            "-filter_complex",
            f"{concat_inputs}concat=n={len(filter_labels)}:v=1:a=0[outv]",
            "-map",
            "[outv]",
            "-c:v",
            DEFAULT_VIDEO_CODEC,
            "-pix_fmt",
            DEFAULT_PIX_FMT,
            "-r",
            str(fps),
        )
    )
    if audio_indices:
        for audio_index in audio_indices:
            arguments.extend(("-map", f"{audio_index}:a"))
        arguments.extend(("-c:a", audio_codec or DEFAULT_AUDIO_CODEC))
        arguments.extend(("-shortest",))
    return arguments


def build_ffmpeg_command(
    *,
    inputs: Sequence[FFmpegInput],
    output: FFmpegOutput,
    options: Sequence[str] = (),
    executable: str = "ffmpeg",
    durations: Optional[Sequence[Optional[float]]] = None,
    fps: Optional[int] = None,
    audio_codec: Optional[str] = None,
) -> FFmpegCommand:
    """Construye un :class:`FFmpegCommand` de forma determinista.

    El resultado conserva el orden de las ``options`` y de los ``inputs``,
    coloca la salida al final y expresa su formato con ``-f <format>``
    inmediatamente antes de la ruta. No añade argumentos que el consumidor no
    haya solicitado.

    Composición de imágenes (opcional):

    Si ``durations`` es una secuencia de la misma longitud que ``inputs``, cada
    imagen se repite durante su duración (``-loop 1 -t <dur>``) y los segmentos
    resultantes se concatenan en el orden de ``inputs`` mediante
    ``filter_complex`` + ``concat``. Los valores ``None`` en ``durations`` se
    interpretan como entradas sin composición (se pasan como inputs simples,
    útiles para pistas de audio). Si existen esas pistas, se mapean al output
    (``-map <idx>:a`` usando su índice real de input), se codifican con
    ``audio_codec`` (o ``DEFAULT_AUDIO_CODEC``) y la salida se sincroniza con
    ``-shortest``.

    Args:
        inputs: entradas del comando, en orden.
        output: salida del comando.
        options: opciones adicionales (tras el ejecutable, antes de las
            entradas).
        executable: binario de FFmpeg (por defecto ``"ffmpeg"``).
        durations: duración en segundos por entrada (opcional). Si se
            proporciona, activa la composición de imágenes con ``concat``.
        fps: fotogramas por segundo de la composición (por defecto 25).
        audio_codec: códec de audio de salida (por defecto
            ``DEFAULT_AUDIO_CODEC``) cuando hay pistas de audio.

    Returns:
        :class:`FFmpegCommand` con los argumentos construidos.

    Raises:
        RendererValidationError: si algún argumento no es estructuralmente
            válido.
    """
    errors = _validate_command_args(inputs, output, options, executable)
    if durations is not None:
        if not isinstance(durations, Sequence) or isinstance(durations, (str, bytes)):
            errors.append("'durations' debe ser una secuencia.")
        elif len(durations) != len(inputs):
            errors.append(
                "'durations' debe tener la misma longitud que 'inputs' "
                f"({len(durations)} != {len(inputs)})."
            )
        else:
            for index, duration in enumerate(durations):
                if duration is None:
                    continue
                if not isinstance(duration, (int, float)) or isinstance(duration, bool):
                    errors.append(
                        f"La duración en la posición {index} no es un número."
                    )
                elif duration <= 0:
                    errors.append(
                        f"La duración en la posición {index} debe ser positiva."
                    )
    if errors:
        raise RendererValidationError("; ".join(errors))

    compose = durations is not None and any(d is not None for d in durations)
    resolved_fps = fps if fps is not None else DEFAULT_FPS

    arguments: list[str] = []
    if compose:
        arguments.extend(options)
        arguments.extend(
            _build_composed_arguments(
                inputs,
                durations=durations,  # type: ignore[arg-type]
                fps=resolved_fps,
                audio_codec=audio_codec,
            )
        )
    else:
        arguments.extend(options)
        for item in inputs:
            arguments.extend(("-i", str(item.path)))
    arguments.extend(("-f", output.format, str(output.path)))
    return FFmpegCommand(executable=executable, arguments=tuple(arguments))
