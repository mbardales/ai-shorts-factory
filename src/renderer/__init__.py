"""Renderer de AI Shorts Factory.

Primera parte del Renderer: los contratos base que definirán todos los
renderizadores de video, siguiendo el mismo patrón que ``ai``, ``audio``,
``image``, ``video`` y ``project``.

- ``base``: contrato ``Renderer`` y tipos ``RendererFormat``, ``RenderOptions``,
  ``RenderRequest`` y ``RenderResult``.
- ``exceptions``: jerarquía de excepciones propia.

No se implementa ningún renderizador concreto todavía ni se ejecuta FFmpeg.

Uso típico (una vez exista una implementación):

    from renderer import RenderRequest, RendererFormat

    request = RenderRequest(
        project_manifest=manifest,
        output_path=Path("video/short.mp4"),
        format=RendererFormat.MP4,
    )
    resultado = SomeRenderer().render(request)
"""

from __future__ import annotations

from .base import (
    Renderer,
    RendererFormat,
    RenderOptions,
    RenderRequest,
    RenderResult,
)
from .commands import (
    FFmpegCommand,
    FFmpegInput,
    FFmpegOutput,
    build_ffmpeg_command,
)
from .builder import build_render_request
from .ffmpeg import build_project_ffmpeg_command
from .executor import FFmpegExecutor
from .subtitles import build_ass_subtitles
from .exceptions import (
    RendererError,
    RendererExecutionError,
    RendererValidationError,
)

__all__ = [
    "Renderer",
    "RendererFormat",
    "RenderOptions",
    "RenderRequest",
    "RenderResult",
    "FFmpegCommand",
    "FFmpegInput",
    "FFmpegOutput",
    "build_ffmpeg_command",
    "build_render_request",
    "build_project_ffmpeg_command",
    "build_ass_subtitles",
    "FFmpegExecutor",
    "RendererError",
    "RendererExecutionError",
    "RendererValidationError",
]
