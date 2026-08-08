"""Contratos base del Renderer de AI Shorts Factory.

Define el contrato que deben implementar todos los renderizadores de video
(``Renderer``), las ``dataclasses`` tipadas que intercambian (``RenderRequest``,
``RenderResult``, ``RenderOptions``) y el enum :class:`RendererFormat`. Los
consumidores deben depender solo de estos tipos y nunca de un renderizador
concreto.

Sigue el mismo patrón que ``audio``, ``image`` y ``video``: el contrato no
ejecuta FFmpeg ni invoca ningún binario; cada implementación concreta decidirá
cómo renderizar. La entrada de trabajo es un :class:`project.ProjectManifest`,
la fuente de verdad del pipeline.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from project import ProjectManifest


class RendererFormat(str, enum.Enum):
    """Formatos de salida soportados por el Renderer.

    Los valores coinciden con extensiones habituales de video.

    Miembros:

    - ``MP4``: contenedor MPEG-4 (extensión ``.mp4``), habitual en YouTube.
    - ``MOV``: contenedor QuickTime (extensión ``.mov``).
    - ``WEBM``: contenedor WebM/VP9 (extensión ``.webm``).
    """

    MP4 = "mp4"
    MOV = "mov"
    WEBM = "webm"


@dataclass(frozen=True)
class RenderOptions:
    """Parámetros de codificación opcionales compartidos entre renderizadores.

    Los valores ``None`` indican que el renderizador debe usar su valor por
    defecto. No todos los renderizadores soportan todos los parámetros.
    """

    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    crf: Optional[int] = None
    preset: Optional[str] = None
    fps: Optional[int] = None


@dataclass(frozen=True)
class RenderRequest:
    """Solicitud tipada de renderizado de un video.

    Attributes:
        project_manifest: manifest del proyecto a renderizar (fuente de verdad).
        output_path: ruta del archivo de video de salida.
        format: formato de codificación del video resultante.
        options: parámetros de codificación opcionales.
    """

    project_manifest: ProjectManifest
    output_path: Path
    format: RendererFormat = RendererFormat.MP4
    options: RenderOptions = field(default_factory=RenderOptions)


@dataclass(frozen=True)
class RenderResult:
    """Resultado tipado del renderizado de un video.

    Attributes:
        output_path: ruta del archivo de video renderizado.
        duration_seconds: duración total del video en segundos.
        size_bytes: tamaño del archivo renderizado en bytes.
    """

    output_path: Path
    duration_seconds: float
    size_bytes: int


class Renderer(ABC):
    """Contrato que deben implementar todos los renderizadores de video.

    Define únicamente el método :meth:`render`; las implementaciones concretas
    gestionan su propia configuración y ejecución.
    """

    #: Nombre corto y estable del renderizador (ej. ``"ffmpeg"``).
    name: str = "base"

    @abstractmethod
    def render(self, request: RenderRequest) -> RenderResult:
        """Renderiza un video a partir de una :class:`RenderRequest`.

        Args:
            request: solicitud del video a renderizar.

        Returns:
            :class:`RenderResult` con la ruta y métricas del video generado.

        Raises:
            renderer.exceptions.RendererError: cualquier error del renderizador.
        """
