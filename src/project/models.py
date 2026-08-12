"""Modelos de dominio del Project Manifest de AI Shorts Factory.

El **Project Manifest** representa un proyecto completo generado por el
pipeline: identidad, referencia al ContentPackage, activos generados (imágenes
y audio), duración estimada, archivo de salida esperado y metadatos básicos.
Es la **única fuente de verdad** para el renderizador de video, que deberá poder
consumir exclusivamente ``output/project.json`` sin inspeccionar carpetas.

El módulo es **puro**: usa solo ``dataclasses`` y la biblioteca estándar. No
depende de ``content``, ``media`` ni de ningún proveedor; el ContentPackage se
embebe como dict serializable (referencia autocontenida) para mantener los
módulos desacoplados.

Convención de rutas: las rutas de los activos (``path``) y del archivo de salida
(``output_file``) son **relativas al directorio ``output/``** del proyecto
(ej. ``images/scene_001.png``, ``video/<id>.mp4``).

- :class:`ProjectIdentity`: identidad del proyecto.
- :class:`AssetKind`: tipo de activo generado (imagen o audio).
- :class:`ProjectAsset`: un activo generado (imagen o pista de audio).
- :class:`ProjectMetadata`: metadatos básicos del manifest.
- :class:`ProjectManifest`: agregado raíz del proyecto.
- :func:`build_project_manifest`: constructor orientado a pipeline.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


def _utc_now_iso() -> str:
    """Devuelve la hora actual en UTC como cadena ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


class AssetKind(str, enum.Enum):
    """Tipos de activos generados por el pipeline.

    Miembros:

    - ``IMAGE``: imagen de una escena.
    - ``AUDIO``: pista de audio (narración, música).
    """

    IMAGE = "image"
    AUDIO = "audio"


@dataclass(frozen=True)
class ProjectIdentity:
    """Identidad del proyecto.

    Attributes:
        id: identificador único del proyecto (heredado del contenido).
        title: título del Short.
        language: idioma del contenido.
        created_at: fecha de creación en formato ISO-8601.
    """

    id: str = ""
    title: str = ""
    language: str = "es"
    created_at: str = ""


@dataclass(frozen=True)
class ProjectAsset:
    """Activo generado por el pipeline (imagen o pista de audio).

    Attributes:
        kind: tipo de activo (:class:`AssetKind`).
        name: nombre del archivo (sin directorio).
        path: ruta relativa a ``output/`` del activo.
        scene_index: índice (0-based) de la escena a la que pertenece un
            activo de imagen; ``None`` para audio.
        extension: extensión del archivo sin punto (opcional).
        provider: nombre del proveedor que generó la imagen
            (ej. ``"gemini-image"``); ``None`` si no se conoce o es audio.
        model: modelo que generó la imagen (opcional).
    """

    kind: AssetKind
    name: str
    path: str
    scene_index: Optional[int] = None
    extension: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None


@dataclass(frozen=True)
class ProjectMetadata:
    """Metadatos básicos del manifest.

    Attributes:
        schema_version: versión del esquema del manifest.
        created_at: fecha de creación en formato ISO-8601.
        updated_at: fecha de última actualización en formato ISO-8601.
        content_file: ruta relativa al ContentPackage de origen (opcional).
        run_id: identificador de la ejecución del pipeline que generó el
            manifest (``run-YYYYMMDD-HHMMSS``); ``None`` si la ejecución no
            usó un :class:`pipeline.RunContext` (legacy, retrocompatible).
    """

    schema_version: int = 1
    created_at: str = ""
    updated_at: str = ""
    content_file: Optional[str] = None
    run_id: Optional[str] = None


@dataclass(frozen=True)
class ProjectManifest:
    """Agregado raíz que representa un proyecto generado por el pipeline.

    Attributes:
        identity: identidad del proyecto.
        content: referencia al ContentPackage (dict serializable autocontenido).
        assets: activos generados (imágenes y pistas de audio).
        estimated_duration_seconds: duración estimada del video (segundos).
        output_file: ruta relativa a ``output/`` del archivo de video esperado.
        metadata: metadatos básicos del manifest.
    """

    identity: ProjectIdentity
    content: Mapping[str, Any]
    assets: tuple[ProjectAsset, ...] = ()
    estimated_duration_seconds: float = 0.0
    output_file: str = ""
    metadata: ProjectMetadata = ProjectMetadata()


def _estimate_duration(package: Mapping[str, Any]) -> float:
    """Estima la duración del video a partir de la temporización de las escenas.

    Suma los ``timing_seconds`` de las escenas del ContentPackage. Las escenas
    sin temporización se ignoran; devuelve 0.0 si no hay datos.
    """
    visuals = package.get("visuals")
    scenes = visuals.get("scenes") if isinstance(visuals, Mapping) else None
    if not isinstance(scenes, (list, tuple)):
        return 0.0
    total = 0.0
    for scene in scenes:
        if not isinstance(scene, Mapping):
            continue
        timing = scene.get("timing_seconds")
        if isinstance(timing, (int, float)) and not isinstance(timing, bool):
            total += float(timing)
    return total


def build_project_manifest(
    package: Mapping[str, Any],
    image_paths: Iterable[str] = (),
    audio_paths: Iterable[str] = (),
    *,
    content_file: str = "content.json",
    output_file: Optional[str] = None,
    estimated_duration_seconds: Optional[float] = None,
    image_providers: Optional[Mapping[str, Mapping[str, Any]]] = None,
    run_id: Optional[str] = None,
) -> ProjectManifest:
    """Construye un :class:`ProjectManifest` a partir de los insumos del pipeline.

    Args:
        package: dict del ContentPackage (por ejemplo, el resultado de
            ``content_package_to_dict``).
        image_paths: rutas relativas a ``output/`` de las imágenes detectadas.
            Se asignan a las escenas en orden de ordenación (0-based).
        audio_paths: rutas relativas a ``output/`` de las pistas de audio
            detectadas.
        content_file: ruta relativa al ContentPackage de origen.
        output_file: ruta relativa a ``output/`` del archivo de video esperado;
            si se omite, se deriva como ``video/<content_id>.mp4``.
        estimated_duration_seconds: duración estimada en segundos; si se
            omite, se calcula sumando la temporización de las escenas.
        image_providers: metadata opcional de trazabilidad por imagen, claveada
            por nombre de archivo (ej. ``"scene_001.png"``) y con las claves
            ``provider``/``model``. Si una imagen no está en el mapa, su
            metadata queda en ``None``.
        run_id: identificador opcional de la ejecución del pipeline que generó
            el manifest; ``None`` para ejecuciones legacy.

    Returns:
        Manifest completo con la identidad heredada del contenido.
    """
    identity_data = package.get("identity")
    identity_data = identity_data if isinstance(identity_data, Mapping) else {}
    content_id = str(identity_data.get("id") or "")
    now = _utc_now_iso()

    identity = ProjectIdentity(
        id=content_id,
        title=str(identity_data.get("title") or ""),
        language=str(identity_data.get("language") or "es"),
        created_at=str(identity_data.get("created_at") or now),
    )

    image_meta = image_providers if isinstance(image_providers, Mapping) else {}

    def _image_meta(name: str) -> tuple[Optional[str], Optional[str]]:
        entry = image_meta.get(name)
        if not isinstance(entry, Mapping):
            return None, None
        provider = entry.get("provider")
        model = entry.get("model")
        return (
            str(provider) if provider is not None else None,
            str(model) if model is not None else None,
        )

    ordered_images = sorted({str(path) for path in image_paths})
    assets = tuple(
        ProjectAsset(
            kind=AssetKind.IMAGE,
            name=Path(path).name,
            path=path,
            scene_index=index,
            extension=Path(path).suffix.lstrip(".") or None,
            provider=_image_meta(Path(path).name)[0],
            model=_image_meta(Path(path).name)[1],
        )
        for index, path in enumerate(ordered_images)
    ) + tuple(
        ProjectAsset(
            kind=AssetKind.AUDIO,
            name=Path(path).name,
            path=path,
            extension=Path(path).suffix.lstrip(".") or None,
        )
        for path in sorted({str(path) for path in audio_paths})
    )

    if estimated_duration_seconds is None:
        estimated_duration_seconds = _estimate_duration(package)

    metadata = ProjectMetadata(
        schema_version=1,
        created_at=now,
        updated_at=now,
        content_file=content_file,
        run_id=run_id,
    )

    return ProjectManifest(
        identity=identity,
        content=dict(package),
        assets=assets,
        estimated_duration_seconds=float(estimated_duration_seconds),
        output_file=output_file or f"video/{content_id}.mp4",
        metadata=metadata,
    )
