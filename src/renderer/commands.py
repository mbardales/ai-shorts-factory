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
from typing import Any

from .exceptions import RendererValidationError


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


def build_ffmpeg_command(
    *,
    inputs: Sequence[FFmpegInput],
    output: FFmpegOutput,
    options: Sequence[str] = (),
    executable: str = "ffmpeg",
) -> FFmpegCommand:
    """Construye un :class:`FFmpegCommand` de forma determinista.

    El resultado conserva el orden de las ``options`` y de los ``inputs``,
    coloca la salida al final y expresa su formato con ``-f <format>``
    inmediatamente antes de la ruta. No añade argumentos que el consumidor no
    haya solicitado.

    Args:
        inputs: entradas del comando, en orden.
        output: salida del comando.
        options: opciones adicionales (tras el ejecutable, antes de las
            entradas).
        executable: binario de FFmpeg (por defecto ``"ffmpeg"``).

    Returns:
        :class:`FFmpegCommand` con los argumentos construidos.

    Raises:
        RendererValidationError: si algún argumento no es estructuralmente
            válido.
    """
    errors = _validate_command_args(inputs, output, options, executable)
    if errors:
        raise RendererValidationError("; ".join(errors))

    arguments: list[str] = []
    arguments.extend(options)
    for item in inputs:
        arguments.extend(("-i", str(item.path)))
    arguments.extend(("-f", output.format, str(output.path)))
    return FFmpegCommand(executable=executable, arguments=tuple(arguments))
