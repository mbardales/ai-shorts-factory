"""Ejecutor de comandos FFmpeg.

Único módulo del Renderer que interactúa con el sistema operativo a través de
``subprocess``. Recibe un :class:`renderer.commands.FFmpegCommand` ya construido,
lo ejecuta como lista ``argv`` (sin shell) y traduce el resultado del proceso a
un :class:`renderer.base.RenderResult` o a una
:class:`renderer.exceptions.RendererExecutionError`.

Este módulo no accede al filesystem para medir el archivo generado: el tamaño se
reporta como ``0`` (desconocido en esta capa) y la duración del video se recibe
de forma explícita por el consumidor.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from .base import RenderResult
from .commands import FFmpegCommand
from .exceptions import RendererExecutionError, RendererValidationError

#: Límite de caracteres de stderr incluidos en el mensaje de error.
_STDERR_CHAR_LIMIT = 2048


def _stderr_summary(data: Optional[bytes]) -> str:
    """Devuelve un resumen truncado de stderr para el mensaje de error."""
    if not data:
        return ""
    text = data.decode("utf-8", errors="replace").strip()
    if len(text) > _STDERR_CHAR_LIMIT:
        text = text[:_STDERR_CHAR_LIMIT] + "... (salida truncada)"
    return text


class FFmpegExecutor:
    """Ejecuta :class:`FFmpegCommand` mediante ``subprocess.run``.

    Attributes:
        executable: binario de FFmpeg declarado como configuración/documentación
            del ejecutor. La fuente de verdad del binario a invocar es
            ``command.executable`` del propio :class:`FFmpegCommand`; este valor
            no lo sobrescribe.
        timeout_seconds: tiempo límite del proceso en segundos; ``None`` indica
            sin límite.
    """

    def __init__(
        self,
        executable: str = "ffmpeg",
        *,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def execute(
        self,
        command: FFmpegCommand,
        *,
        output_path: Optional[Path] = None,
        duration_seconds: Optional[float] = None,
    ) -> RenderResult:
        """Ejecuta el comando y devuelve un :class:`RenderResult`.

        El comando se ejecuta siempre en modo no interactivo: si no incluye ya
        la opción global ``-y``, se inserta tras el ejecutable para que FFmpeg
        sobrescriba la salida sin pedir confirmación (un render repetido no
        debe bloquearse). ``shell=False`` se mantiene en todo caso.

        Args:
            command: comando FFmpeg a ejecutar (su ``executable`` es la fuente
                de verdad del binario a invocar).
            output_path: ruta del archivo de salida (obligatoria).
            duration_seconds: duración estimada del video; si se omite, se
                reporta ``0.0``.

        Returns:
            :class:`RenderResult` con ``size_bytes=0`` (esta capa no consulta
            el filesystem).

        Raises:
            RendererValidationError: si ``command`` no es un
                :class:`FFmpegCommand`.
            RendererExecutionError: si ``output_path`` no es válido o si el
                proceso no se puede ejecutar, supera el tiempo límite o devuelve
                un código de salida distinto de 0.
        """
        if command is None or not isinstance(command, FFmpegCommand):
            raise RendererValidationError("'command' debe ser un FFmpegCommand.")

        if output_path is None or not isinstance(output_path, Path):
            raise RendererExecutionError(
                "'output_path' es obligatorio y debe ser un Path para devolver "
                "un RenderResult."
            )

        resolved_duration = duration_seconds if duration_seconds is not None else 0.0

        argv = command.to_argv()
        if "-y" not in argv:
            argv = [argv[0], "-y", *argv[1:]]
        timeout = self.timeout_seconds

        try:
            if timeout is None:
                completed = subprocess.run(
                    argv,
                    shell=False,
                    capture_output=True,
                    text=False,
                    check=False,
                )
            else:
                completed = subprocess.run(
                    argv,
                    shell=False,
                    capture_output=True,
                    text=False,
                    check=False,
                    timeout=timeout,
                )
        except FileNotFoundError as exc:
            raise RendererExecutionError(
                f"No se encontró el binario de FFmpeg: {argv[0]!r}."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RendererExecutionError(
                "El comando FFmpeg superó el tiempo límite "
                f"({timeout} s)."
            ) from exc
        except OSError as exc:
            raise RendererExecutionError(f"Error al ejecutar FFmpeg: {exc}") from exc

        if completed.returncode != 0:
            stderr_text = _stderr_summary(completed.stderr)
            detail = f" (stderr: {stderr_text})" if stderr_text else ""
            raise RendererExecutionError(
                f"FFmpeg terminó con código de salida {completed.returncode}.{detail}"
            )

        return RenderResult(
            output_path=output_path,
            duration_seconds=resolved_duration,
            size_bytes=0,
        )
