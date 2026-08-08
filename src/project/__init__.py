"""Project Manifest de AI Shorts Factory.

Representa un proyecto completo generado por el pipeline: identidad, referencia
al ContentPackage, activos generados (imágenes y audio), duración estimada,
archivo de salida esperado y metadatos básicos. Es la **única fuente de verdad**
para el renderizador de video, que consumirá exclusivamente ``output/project.json``
sin inspeccionar carpetas.

Estructura:

- ``models``: tipos de dominio (``ProjectManifest``, ``ProjectIdentity``,
  ``ProjectAsset``, ``AssetKind``, ``ProjectMetadata``) y ``build_project_manifest``.
- ``serializer``: serialización a JSON y desde JSON (función pura, sin
  dependencias).
- ``validator``: validaciones y coerción robusta del manifest.
- ``exceptions``: jerarquía de excepciones propia.

El módulo es **puro**: no depende de ``content``, ``media`` ni de ningún
proveedor; el ContentPackage se embebe como dict serializable (referencia
autocontenida).

Uso típico (en ``scripts/generate_manifest.py``):

    from project import build_project_manifest, project_manifest_to_json

    manifest = build_project_manifest(package_dict, image_paths, audio_paths)
    json_text = project_manifest_to_json(manifest)
"""

from __future__ import annotations

from .exceptions import (
    ProjectError,
    ProjectNotFoundError,
    ProjectValidationError,
)
from .models import (
    AssetKind,
    ProjectAsset,
    ProjectIdentity,
    ProjectManifest,
    ProjectMetadata,
    build_project_manifest,
)
from .validator import (
    coerce_project_manifest,
    find_errors,
    validate_project_manifest,
)
from .serializer import (
    project_manifest_from_dict,
    project_manifest_from_json,
    project_manifest_to_dict,
    project_manifest_to_json,
)

__all__ = [
    "AssetKind",
    "ProjectAsset",
    "ProjectIdentity",
    "ProjectManifest",
    "ProjectMetadata",
    "build_project_manifest",
    "ProjectError",
    "ProjectNotFoundError",
    "ProjectValidationError",
    "coerce_project_manifest",
    "find_errors",
    "validate_project_manifest",
    "project_manifest_from_dict",
    "project_manifest_from_json",
    "project_manifest_to_dict",
    "project_manifest_to_json",
]
