"""Video Core de AI Shorts Factory.

Arquitectura base para el renderizado de video, reutilizando Media Core y
siguiendo el mismo patrón que ``ai``, ``media``, ``image`` y ``audio``:

- ``base``: contrato ``VideoRenderer`` y tipos ``VideoRequest``, ``VideoResult``,
  ``RenderOptions`` y ``VideoFormat``.
- ``timeline``: composición del video (``Timeline`` y sus pistas tipadas).
- ``models``: fuente de renderizado y composiciones derivadas del ContentPackage.
- ``adapter``: ``VideoAdapter``, orquestador desacoplado del renderizador.
- ``exceptions``: jerarquía de excepciones propia.

No se implementa ningún renderizador concreto todavía (no existe
``video/renderer.py`` ni ``video/providers/``) ni se llama FFmpeg.

Uso típico (una vez exista un renderizador):

    from video import VideoAdapter, Timeline, SceneClip, VideoRequest

    timeline = Timeline(content_id="short-abc", video=(SceneClip(image_path=...),))
    adapter = VideoAdapter(SomeVideoRenderer(model="ffmpeg"))
    resultado = adapter.render(VideoRequest(timeline=timeline))
"""

from __future__ import annotations

from .adapter import VideoAdapter
from .base import RenderOptions, VideoFormat, VideoRenderer, VideoRequest, VideoResult
from .exceptions import (
    VideoError,
    VideoRenderError,
    VideoRendererError,
)
from .models import RenderPrompt, SceneRender, VideoSource
from .timeline import (
    AudioTrack,
    BrandingTrack,
    SceneClip,
    SubtitleTrack,
    Timeline,
    Track,
)

__all__ = [
    "RenderOptions",
    "VideoFormat",
    "VideoRenderer",
    "VideoRequest",
    "VideoResult",
    "VideoError",
    "VideoRenderError",
    "VideoRendererError",
    "RenderPrompt",
    "SceneRender",
    "VideoSource",
    "AudioTrack",
    "BrandingTrack",
    "SceneClip",
    "SubtitleTrack",
    "Timeline",
    "Track",
    "VideoAdapter",
]
