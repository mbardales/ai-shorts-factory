"""Puente entre Project Core y Renderer Core.

Convierte un :class:`project.ProjectManifest` en un
:class:`renderer.base.RenderRequest`: resuelve la ruta de salida (explícita o
derivada de ``output_file``), infiere el :class:`renderer.base.RendererFormat`
desde la extensión cuando no se especifica, y conserva o crea las
:class:`renderer.base.RenderOptions`.

Es una función pura: no accede al filesystem, no ejecuta procesos, no consulta
el entorno ni modifica el manifest ni las opciones recibidas. Las rutas se
conservan tal cual las define el manifest (relativas a ``output/``); no se
convierten a absolutas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from project import ProjectManifest

from .base import RenderOptions, RenderRequest, RendererFormat
from .exceptions import RendererValidationError

_FORMAT_BY_EXTENSION: dict[str, RendererFormat] = {
    "mp4": RendererFormat.MP4,
    "mov": RendererFormat.MOV,
    "webm": RendererFormat.WEBM,
}


def build_render_request(
    project_manifest: ProjectManifest,
    *,
    output_path: Optional[Path] = None,
    format: Optional[RendererFormat] = None,
    options: Optional[RenderOptions] = None,
) -> RenderRequest:
    """Construye una :class:`RenderRequest` a partir de un manifest.

    Args:
        project_manifest: manifest del proyecto a renderizar (obligatorio).
        output_path: ruta de salida explícita; si se omite, se deriva de
            ``project_manifest.output_file`` sin convertirla a absoluta.
        format: formato explícito; si se omite, se infiere de la extensión de la
            ruta de salida (comparación sin distinguir mayúsculas).
        options: opciones de codificación; si se omiten, se crean
            ``RenderOptions()``.

    Returns:
        :class:`RenderRequest` con las referencias resueltas.

    Raises:
        RendererValidationError: si los argumentos no superan las validaciones
            de dominio.
    """
    errors: list[str] = []

    if project_manifest is None or not isinstance(project_manifest, ProjectManifest):
        errors.append("'project_manifest' debe ser un ProjectManifest.")

    if output_path is not None:
        if not isinstance(output_path, Path):
            errors.append("'output_path' debe ser un Path.")
            resolved_output_path: Optional[Path] = None
        else:
            resolved_output_path = output_path
    else:
        output_file = (
            project_manifest.output_file
            if isinstance(project_manifest, ProjectManifest)
            else None
        )
        if output_file is None or not str(output_file).strip():
            errors.append(
                "'output_file' no puede estar vacío cuando no se proporciona "
                "'output_path'."
            )
            resolved_output_path = None
        else:
            resolved_output_path = Path(str(output_file))

    if format is not None:
        if not isinstance(format, RendererFormat):
            errors.append("'format' debe ser un RendererFormat.")
            resolved_format: Optional[RendererFormat] = None
        else:
            resolved_format = format
    elif resolved_output_path is not None:
        extension = resolved_output_path.suffix.lstrip(".").lower()
        if not extension:
            errors.append(
                "No se puede inferir el formato: 'output_path' no tiene extensión."
            )
            resolved_format = None
        elif extension not in _FORMAT_BY_EXTENSION:
            errors.append(f"'{extension}' no es una extensión soportada.")
            resolved_format = None
        else:
            resolved_format = _FORMAT_BY_EXTENSION[extension]
    else:
        resolved_format = None

    if options is not None and not isinstance(options, RenderOptions):
        errors.append("'options' debe ser un RenderOptions.")
        resolved_options: Optional[RenderOptions] = None
    elif options is not None:
        resolved_options = options
    else:
        resolved_options = RenderOptions()

    if errors:
        raise RendererValidationError("; ".join(errors))

    return RenderRequest(
        project_manifest=project_manifest,
        output_path=resolved_output_path,
        format=resolved_format,
        options=resolved_options,
    )
