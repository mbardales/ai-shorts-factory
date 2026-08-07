"""Validaciones y transiciones del modelo "Content Package".

Contiene la lógica de reglas del dominio, independiente de cualquier
infraestructura:

- ``find_errors`` / ``validate_content_package``: reglas básicas de integridad.
- ``coerce_content_package``: reconstrucción robusta desde un dict, rellenando
  campos ausentes con valores por defecto y lanzando solo si el JSON es
  imposible de recuperar.
- ``transition``: transiciones permitidas del ciclo de vida de un Short.

Reglas de límites basadas en las restricciones conocidas de YouTube Shorts:
título máx. 100 caracteres, descripción máx. 5000 y un máximo de 30 etiquetas.

Las etapas avanzadas del flujo (ver :class:`ContentStage`) exigen progresivamente
contenido: guion desde ``SCRIPT``, escenas desde ``SCENES`` y narración desde
``NARRATION``. Un Short ``PUBLISHED`` debe incluir los datos de publicación.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any, Iterable, Mapping, Optional

from .models import (
    Analytics,
    Assets,
    ContentPackage,
    ContentStage,
    Identity,
    Narration,
    Publication,
    Research,
    SEO,
    Scene,
    Script,
    Status,
    Visuals,
    _utc_now_iso,
)

#: Límite de caracteres del título de YouTube.
MAX_TITLE_LENGTH = 100
#: Límite de caracteres de la descripción de YouTube.
MAX_DESCRIPTION_LENGTH = 5000
#: Número máximo de etiquetas de YouTube.
MAX_TAGS = 30


class ContentValidationError(ValueError):
    """Indica que un :class:`ContentPackage` no cumple las reglas del dominio.

    Attributes:
        errors: lista de mensajes de error detectados.
    """

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors: list[str] = list(errors)
        super().__init__("; ".join(self.errors) or "Contenido inválido.")


def _coerce_stage(stage: ContentStage | str) -> ContentStage:
    """Normaliza una etapa desde enum o cadena a :class:`ContentStage`.

    Raises:
        ValueError: si la cadena no corresponde a una etapa conocida.
    """
    if isinstance(stage, ContentStage):
        return stage
    return ContentStage(stage)


def _stage_index(stage: ContentStage) -> int:
    """Devuelve la posición de una etapa en el orden del ciclo de vida."""
    return list(ContentStage).index(stage)


#: Transiciones permitidas entre etapas del ciclo de vida: avance lineal y
#: retroceso a la etapa anterior. ``PUBLISHED`` es un estado terminal.
_TRANSITIONS: dict[ContentStage, frozenset[ContentStage]] = {
    ContentStage.GENERATED: frozenset({ContentStage.RESEARCH}),
    ContentStage.RESEARCH: frozenset({ContentStage.GENERATED, ContentStage.SEO}),
    ContentStage.SEO: frozenset({ContentStage.RESEARCH, ContentStage.SCRIPT}),
    ContentStage.SCRIPT: frozenset({ContentStage.SEO, ContentStage.SCENES}),
    ContentStage.SCENES: frozenset({ContentStage.SCRIPT, ContentStage.NARRATION}),
    ContentStage.NARRATION: frozenset({ContentStage.SCENES, ContentStage.ASSETS}),
    ContentStage.ASSETS: frozenset({ContentStage.NARRATION, ContentStage.VIDEO}),
    ContentStage.VIDEO: frozenset({ContentStage.ASSETS, ContentStage.PUBLISHED}),
    ContentStage.PUBLISHED: frozenset(),
}


def find_errors(package: ContentPackage) -> list[str]:
    """Devuelve la lista de errores de validación de un paquete.

    Inspecciona los diez objetos de valor del agregado: ``identity``,
    ``research``, ``seo``, ``script``, ``visuals``, ``narration``, ``assets``,
    ``publication``, ``analytics`` y ``status``.

    Args:
        package: agregado a inspeccionar.

    Returns:
        Lista de mensajes; vacía si el paquete es válido.
    """
    errors: list[str] = []
    stage_index = _stage_index(package.status.stage)

    identity = package.identity
    if not identity.id or not identity.id.strip():
        errors.append("El campo 'identity.id' no puede estar vacío.")
    if not identity.title or not identity.title.strip():
        errors.append("El campo 'identity.title' no puede estar vacío.")
    elif len(identity.title) > MAX_TITLE_LENGTH:
        errors.append(
            f"El campo 'identity.title' supera el límite de "
            f"{MAX_TITLE_LENGTH} caracteres."
        )
    if not identity.language or not identity.language.strip():
        errors.append("El campo 'identity.language' no puede estar vacío.")

    for field_name, items in (
        ("research.keywords", package.research.keywords),
        ("research.sources", package.research.sources),
    ):
        for index, item in enumerate(items):
            if not item or not item.strip():
                errors.append(f"'{field_name}' en la posición {index} está vacío.")

    if len(package.seo.description) > MAX_DESCRIPTION_LENGTH:
        errors.append(
            f"El campo 'seo.description' supera el límite de "
            f"{MAX_DESCRIPTION_LENGTH} caracteres."
        )

    if len(package.seo.tags) > MAX_TAGS:
        errors.append(f"'seo.tags' no puede tener más de {MAX_TAGS} elementos.")
    for index, tag in enumerate(package.seo.tags):
        if not tag or not tag.strip():
            errors.append(f"La etiqueta en la posición {index} está vacía.")

    if stage_index >= _stage_index(ContentStage.SCRIPT):
        if not package.script.hook or not package.script.hook.strip():
            errors.append("'script.hook' no puede estar vacío en esta etapa.")
        if not package.script.development or not package.script.development.strip():
            errors.append("'script.development' no puede estar vacío en esta etapa.")

    if stage_index >= _stage_index(ContentStage.SCENES):
        if not package.visuals.scenes:
            errors.append(
                "'visuals.scenes' debe contener al menos una escena en esta etapa."
            )
        for index, scene in enumerate(package.visuals.scenes):
            if not scene.description or not scene.description.strip():
                errors.append(
                    f"La escena en la posición {index} tiene una descripción vacía."
                )
            if scene.timing_seconds is not None and scene.timing_seconds < 0:
                errors.append(
                    f"La escena en la posición {index} tiene un "
                    f"'timing_seconds' negativo."
                )

    if stage_index >= _stage_index(ContentStage.NARRATION):
        if not package.narration.text or not package.narration.text.strip():
            errors.append("'narration.text' no puede estar vacío en esta etapa.")

    for field_name in ("audio", "music", "branding", "font", "video", "thumbnail"):
        value = getattr(package.assets, field_name)
        if value is not None and not isinstance(value, str):
            errors.append(f"'assets.{field_name}' debe ser una cadena o None.")

    if not package.publication.platform or not package.publication.platform.strip():
        errors.append("'publication.platform' no puede estar vacío.")

    if package.status.stage is ContentStage.PUBLISHED:
        if not package.publication.video_id:
            errors.append(
                "Un Short 'PUBLISHED' debe incluir 'publication.video_id'."
            )
        if not package.publication.published_at:
            errors.append("Un Short 'PUBLISHED' debe incluir 'published_at'.")
        if not package.publication.url:
            errors.append("Un Short 'PUBLISHED' debe incluir 'publication.url'.")

    for metric_name in ("views", "likes", "comments", "shares"):
        value = getattr(package.analytics, metric_name)
        if value is not None and value < 0:
            errors.append(f"'analytics.{metric_name}' no puede ser negativo.")

    if not package.status.updated_at:
        errors.append("'status.updated_at' no puede estar vacío.")

    return errors


def validate_content_package(package: ContentPackage) -> None:
    """Valida un :class:`ContentPackage` y lanza errores si es inválido.

    Args:
        package: agregado a validar.

    Raises:
        ContentValidationError: si se detecta al menos un error.
    """
    errors = find_errors(package)
    if errors:
        raise ContentValidationError(errors)


# ---------------------------------------------------------------------------
# Coerción robusta desde dict (valida y completa con valores por defecto)
# ---------------------------------------------------------------------------


def _as_str(value: Any, *, default: str = "") -> str:
    """Coacciona un valor a cadena (None o tipos no textuales con ``default``)."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _as_optional_str(value: Any) -> Optional[str]:
    """Coacciona un valor a cadena opcional (None se conserva)."""
    if value is None:
        return None
    return _as_str(value)


def _as_tuple_of_str(value: Any) -> tuple[str, ...]:
    """Coacciona un valor a una tupla de cadenas (ignora no iterables)."""
    if value is None or isinstance(value, str):
        return ()
    try:
        return tuple(_as_str(item) for item in value if item is not None)
    except TypeError:
        return ()


def _as_optional_int(value: Any) -> Optional[int]:
    """Coacciona un valor a entero opcional (None si no es convertible)."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_stage_value(value: Any) -> ContentStage:
    """Coacciona un valor a :class:`ContentStage` (por defecto ``GENERATED``)."""
    if isinstance(value, ContentStage):
        return value
    try:
        return ContentStage(_as_str(value))
    except ValueError:
        return ContentStage.GENERATED


def _coerce_identity(data: Any) -> Identity:
    """Construye un :class:`Identity` completo, generando id si falta."""
    if not isinstance(data, Mapping):
        data = {}
    return Identity(
        id=_as_str(data.get("id")) or uuid.uuid4().hex[:8],
        title=_as_str(data.get("title")),
        language=_as_str(data.get("language"), default="es"),
        created_at=_as_str(data.get("created_at")) or _utc_now_iso(),
    )


def _coerce_research(data: Any) -> Research:
    """Construye un :class:`Research` completo (vacío si no hay datos)."""
    if not isinstance(data, Mapping):
        return Research()
    return Research(
        topic=_as_str(data.get("topic")),
        keywords=_as_tuple_of_str(data.get("keywords")),
        sources=_as_tuple_of_str(data.get("sources")),
        notes=_as_optional_str(data.get("notes")),
    )


def _coerce_seo(data: Any) -> SEO:
    """Construye un :class:`SEO` completo, limitando etiquetas."""
    if not isinstance(data, Mapping):
        return SEO()
    return SEO(
        description=_as_str(data.get("description")),
        tags=_as_tuple_of_str(data.get("tags"))[:MAX_TAGS],
        keywords=_as_tuple_of_str(data.get("keywords")),
    )


def _coerce_script(data: Any) -> Script:
    """Construye un :class:`Script` completo (vacío si no hay datos)."""
    if not isinstance(data, Mapping):
        return Script()
    return Script(
        hook=_as_str(data.get("hook")),
        development=_as_str(data.get("development")),
        call_to_action=_as_optional_str(data.get("call_to_action")),
    )


def _coerce_scene(data: Any) -> Scene:
    """Construye una :class:`Scene` completa desde un dict."""
    if not isinstance(data, Mapping):
        return Scene(description="")
    return Scene(
        description=_as_str(data.get("description")),
        timing_seconds=_as_optional_int(data.get("timing_seconds")),
    )


def _coerce_visuals(data: Any) -> Visuals:
    """Construye un :class:`Visuals` completo."""
    if not isinstance(data, Mapping):
        return Visuals()
    scenes = data.get("scenes")
    if isinstance(scenes, (list, tuple)):
        return Visuals(
            scenes=tuple(_coerce_scene(item) for item in scenes),
            style_notes=_as_optional_str(data.get("style_notes")),
        )
    return Visuals(style_notes=_as_optional_str(data.get("style_notes")))


def _coerce_narration(data: Any) -> Narration:
    """Construye una :class:`Narration` completa (vacía si no hay datos)."""
    if not isinstance(data, Mapping):
        return Narration()
    return Narration(
        text=_as_str(data.get("text")),
        voice=_as_optional_str(data.get("voice")),
    )


def _coerce_assets(data: Any) -> Assets:
    """Construye un :class:`Assets` completo (todos los campos opcionales)."""
    if not isinstance(data, Mapping):
        return Assets()
    return Assets(
        audio=_as_optional_str(data.get("audio")),
        music=_as_optional_str(data.get("music")),
        branding=_as_optional_str(data.get("branding")),
        font=_as_optional_str(data.get("font")),
        video=_as_optional_str(data.get("video")),
        thumbnail=_as_optional_str(data.get("thumbnail")),
    )


def _coerce_publication(data: Any) -> Publication:
    """Construye una :class:`Publication` completa (vacía si no hay datos)."""
    if not isinstance(data, Mapping):
        return Publication()
    return Publication(
        platform=_as_str(data.get("platform"), default="youtube"),
        video_id=_as_optional_str(data.get("video_id")),
        published_at=_as_optional_str(data.get("published_at")),
        privacy_status=_as_optional_str(data.get("privacy_status")),
        url=_as_optional_str(data.get("url")),
    )


def _coerce_analytics(data: Any) -> Analytics:
    """Construye un :class:`Analytics` completo (métricas opcionales)."""
    if not isinstance(data, Mapping):
        return Analytics()
    return Analytics(
        views=_as_optional_int(data.get("views")),
        likes=_as_optional_int(data.get("likes")),
        comments=_as_optional_int(data.get("comments")),
        shares=_as_optional_int(data.get("shares")),
    )


def _coerce_status(data: Any) -> Status:
    """Construye un :class:`Status` completo (etapa segura por defecto)."""
    if not isinstance(data, Mapping):
        return Status()
    return Status(
        stage=_coerce_stage_value(data.get("stage")),
        updated_at=_as_str(data.get("updated_at")) or _utc_now_iso(),
    )


def coerce_content_package(data: Mapping[str, Any]) -> ContentPackage:
    """Reconstruye un :class:`ContentPackage` desde un dict de forma robusta.

    Valida y completa los diez objetos de valor del agregado, rellenando con
    valores por defecto cualquier campo ausente o no recuperable. Solo lanza
    error si la entrada es estructuralmente irrecuperable (no es un dict).

    Args:
        data: dict con la estructura de un ContentPackage (por ejemplo, la
            respuesta JSON de un modelo de IA).

    Returns:
        Agregado completo con todos los objetos de valor poblados.

    Raises:
        ContentValidationError: si ``data`` no es un objeto JSON (dict).
    """
    if not isinstance(data, Mapping):
        raise ContentValidationError(
            ["El JSON de entrada no es un objeto (dict); no se puede recuperar."]
        )
    return ContentPackage(
        identity=_coerce_identity(data.get("identity")),
        research=_coerce_research(data.get("research")),
        seo=_coerce_seo(data.get("seo")),
        script=_coerce_script(data.get("script")),
        visuals=_coerce_visuals(data.get("visuals")),
        narration=_coerce_narration(data.get("narration")),
        assets=_coerce_assets(data.get("assets")),
        publication=_coerce_publication(data.get("publication")),
        analytics=_coerce_analytics(data.get("analytics")),
        status=_coerce_status(data.get("status")),
    )


def transition(
    package: ContentPackage,
    target: ContentStage | str,
    *,
    video_id: Optional[str] = None,
    url: Optional[str] = None,
) -> ContentPackage:
    """Devuelve una copia del paquete con una nueva etapa del ciclo de vida.

    Valida que la transición sea permitida según el mapa ``_TRANSITIONS``
    (avance lineal o retroceso a la etapa anterior). Al pasar a ``PUBLISHED``
    es obligatorio indicar el ``video_id`` y la ``url`` del video publicado.

    Args:
        package: paquete actual.
        target: etapa de destino (enum o cadena).
        video_id: identificador del video publicado (requerido si el destino
            es ``PUBLISHED``).
        url: URL pública del video publicado (requerida si el destino es
            ``PUBLISHED``).

    Returns:
        Nuevo :class:`ContentPackage` con ``status`` actualizado y, en su
        caso, los datos de :class:`Publication` rellenados.

    Raises:
        ContentValidationError: si la transición no está permitida o el
            resultado no supera las validaciones.
        ValueError: si la etapa de destino no es reconocida.
    """
    current = _coerce_stage(package.status.stage)
    target_stage = _coerce_stage(target)

    allowed = _TRANSITIONS.get(current, frozenset())
    if target_stage not in allowed:
        raise ContentValidationError(
            [
                f"Transición no permitida: '{current.value}' → "
                f"'{target_stage.value}'."
            ]
        )

    publication = package.publication
    if target_stage is ContentStage.PUBLISHED:
        if not video_id:
            raise ContentValidationError(
                ["Al publicar se debe indicar el 'video_id' del video."]
            )
        if not url:
            raise ContentValidationError(
                ["Al publicar se debe indicar la 'url' del video."]
            )
        publication = replace(
            publication,
            video_id=video_id,
            url=url,
            published_at=package.publication.published_at or _utc_now_iso(),
        )

    updated = replace(
        package,
        status=Status(stage=target_stage, updated_at=_utc_now_iso()),
        publication=publication,
    )
    validate_content_package(updated)
    return updated
