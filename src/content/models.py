"""Modelo de dominio "Content Package" de AI Shorts Factory.

Representa el **ciclo de vida completo** de un YouTube Short como un agregado
raíz compuesto por diez objetos de valor:

``Identity``, ``Research``, ``SEO``, ``Script``, ``Visuals``, ``Narration``,
``Assets``, ``Publication``, ``Analytics`` y ``Status``.

Etapas del ciclo de vida (ver ``ContentStage``): Generado → Investigación → SEO →
Guion → Escenas → Narración → Activos → Video → Publicación.

Propiedades del dominio:

- Es un modelo **puro**: usa solo ``dataclasses`` y la biblioteca estándar.
- **No** contiene lógica de IA ni referencias a proveedores (p. ej. Gemini).
- **No** depende de infraestructura (p. ej. n8n) ni de una plataforma concreta:
  ``Publication`` está preparada para futuras plataformas.
- Es serializable a JSON (ver ``content.schema``).

Estructura del paquete:

- ``models``: tipos de dominio.
- ``schema``: serialización a JSON y desde JSON.
- ``validator``: reglas de integridad y transiciones de etapa.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _utc_now_iso() -> str:
    """Devuelve la hora actual en UTC como cadena ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


class ContentStage(str, enum.Enum):
    """Etapas del ciclo de vida de un YouTube Short.

    Flujo previsto (lineal, con retroceso permitido al verificar):

    ``GENERATED`` → ``RESEARCH`` → ``SEO`` → ``SCRIPT`` → ``SCENES`` →
    ``NARRATION`` → ``ASSETS`` → ``VIDEO`` → ``PUBLISHED``.

    ``PUBLISHED`` es un estado terminal. Las transiciones se validan en
    ``content.validator``.
    """

    GENERATED = "generated"
    RESEARCH = "research"
    SEO = "seo"
    SCRIPT = "script"
    SCENES = "scenes"
    NARRATION = "narration"
    ASSETS = "assets"
    VIDEO = "video"
    PUBLISHED = "published"


@dataclass(frozen=True)
class Identity:
    """Identidad del contenido: quién es este Short.

    Attributes:
        id: identificador único del contenido.
        title: título del Short (límite de 100 caracteres de YouTube).
        language: idioma del contenido.
        created_at: fecha de creación en formato ISO-8601.
    """

    id: str
    title: str
    language: str = "es"
    created_at: str = field(default_factory=_utc_now_iso)


@dataclass(frozen=True)
class Research:
    """Resultado de la etapa de investigación del tema.

    Attributes:
        topic: tema principal investigado.
        keywords: palabras clave relacionadas con el tema.
        sources: referencias o fuentes consultadas.
        notes: observaciones de la investigación.
    """

    topic: str = ""
    keywords: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    notes: Optional[str] = None


@dataclass(frozen=True)
class SEO:
    """Campos de optimización para motores de búsqueda y descubrimiento.

    Attributes:
        description: descripción del video (máx. 5000 caracteres).
        tags: etiquetas del video (máx. 30).
        keywords: palabras clave de posicionamiento.
    """

    description: str = ""
    tags: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class Script:
    """Guion del contenido, dividido en partes.

    Attributes:
        hook: frases iniciales para captar atención.
        development: cuerpo del contenido.
        call_to_action: llamada a la acción final (opcional).
    """

    hook: str = ""
    development: str = ""
    call_to_action: Optional[str] = None


@dataclass(frozen=True)
class Scene:
    """Una escena individual del Short.

    Attributes:
        description: descripción de lo que ocurre en la escena.
        timing_seconds: duración estimada de la escena (opcional).
    """

    description: str
    timing_seconds: Optional[int] = None


@dataclass(frozen=True)
class Visuals:
    """Plan de escenas y notas visuales.

    Attributes:
        scenes: escenas que componen el video.
        style_notes: notas de estilo visual (opcional).
    """

    scenes: tuple[Scene, ...] = ()
    style_notes: Optional[str] = None


@dataclass(frozen=True)
class Narration:
    """Locución del contenido.

    Attributes:
        text: texto de la narración.
        voice: voz o configuración de locución (opcional).
    """

    text: str = ""
    voice: Optional[str] = None


@dataclass(frozen=True)
class Assets:
    """Referencias a los activos materiales del Short.

    Cada campo es una referencia (ruta, URI o identificador). Todos son
    opcionales: no todo Short requiere todos los activos.

    Attributes:
        audio: pista de audio de la voz.
        music: música de fondo.
        branding: elementos de identidad visual.
        font: tipografía.
        video: video final renderizado.
        thumbnail: miniatura del video.
    """

    audio: Optional[str] = None
    music: Optional[str] = None
    branding: Optional[str] = None
    font: Optional[str] = None
    video: Optional[str] = None
    thumbnail: Optional[str] = None


@dataclass(frozen=True)
class Publication:
    """Datos de publicación del contenido en una plataforma.

    Diseñado para ser agnóstico de plataforma (preparado para futuras
    plataformas además de YouTube).

    Attributes:
        platform: plataforma de publicación (por defecto ``"youtube"``).
        video_id: identificador del video publicado.
        published_at: fecha de publicación en formato ISO-8601.
        privacy_status: estado de privacidad en la plataforma.
        url: URL pública del contenido publicado.
    """

    platform: str = "youtube"
    video_id: Optional[str] = None
    published_at: Optional[str] = None
    privacy_status: Optional[str] = None
    url: Optional[str] = None


@dataclass(frozen=True)
class Analytics:
    """Métricas de rendimiento del contenido tras su publicación.

    Attributes:
        views: número de visualizaciones.
        likes: número de "me gusta".
        comments: número de comentarios.
        shares: número de veces compartido.
    """

    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None


@dataclass(frozen=True)
class Status:
    """Estado actual del ciclo de vida del contenido.

    Attributes:
        stage: etapa actual del flujo.
        updated_at: fecha de la última transición en formato ISO-8601.
    """

    stage: ContentStage = ContentStage.GENERATED
    updated_at: str = field(default_factory=_utc_now_iso)


@dataclass(frozen=True)
class ContentPackage:
    """Agregado raíz que representa un YouTube Short en todo su ciclo de vida.

    Compuesto por los objetos de valor del dominio. Todos salvo ``identity``
    tienen valores por defecto, de modo que un paquete puede crearse solo con
    la idea y completarse etapa a etapa.
    """

    identity: Identity
    research: Research = field(default_factory=Research)
    seo: SEO = field(default_factory=SEO)
    script: Script = field(default_factory=Script)
    visuals: Visuals = field(default_factory=Visuals)
    narration: Narration = field(default_factory=Narration)
    assets: Assets = field(default_factory=Assets)
    publication: Publication = field(default_factory=Publication)
    analytics: Analytics = field(default_factory=Analytics)
    status: Status = field(default_factory=Status)
