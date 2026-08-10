"""Adaptador puro entre Project Manifest / RenderRequest y FFmpegCommand.

Transforma los activos de un :class:`project.ProjectManifest` (imágenes y
audio) en entradas FFmpeg estructuradas y delega la construcción del comando en
:func:`renderer.commands.build_ffmpeg_command`.

Este módulo **no ejecuta FFmpeg**: no importa ``subprocess``, no detecta el
binario, no accede al filesystem y no verifica que los archivos existan. Solo
transforma objetos de dominio en otros objetos.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Optional

from project import AssetKind, ProjectAsset, ProjectManifest

from .base import RenderRequest, RendererFormat
from .commands import (
    FFmpegCommand,
    FFmpegInput,
    FFmpegOutput,
    build_ffmpeg_command,
)
from .exceptions import RendererValidationError


def _is_valid_asset_path(path: Any) -> bool:
    """True si el valor es una ruta de activo estructuralmente válida.

    Las rutas de los activos del manifest son cadenas relativas a ``output/``;
    la validación es estructural y no comprueba la existencia física.
    """
    if not isinstance(path, str):
        return False
    value = Path(path)
    return bool(str(value).strip()) and bool(value.name)


def _scene_duration(manifest: ProjectManifest, scene_index: Optional[int]) -> Optional[float]:
    """Devuelve la duración en segundos de la escena ``scene_index``.

    La temporización se obtiene del ContentPackage embebido en el manifest
    (``visuals.scenes[i].timing_seconds``), que es la fuente de verdad de las
    duraciones de cada escena.

    Args:
        manifest: manifest del proyecto.
        scene_index: índice (0-based) de la escena; ``None`` si no aplica.

    Returns:
        Duración en segundos o ``None`` si no se puede determinar.
    """
    if scene_index is None:
        return None
    content = getattr(manifest, "content", None)
    if not isinstance(content, dict):
        return None
    visuals = content.get("visuals")
    scenes = visuals.get("scenes") if isinstance(visuals, dict) else None
    if not isinstance(scenes, (list, tuple)):
        return None
    if not (0 <= scene_index < len(scenes)):
        return None
    scene = scenes[scene_index]
    timing = scene.get("timing_seconds") if isinstance(scene, dict) else None
    if not isinstance(timing, (int, float)) or isinstance(timing, bool) or timing <= 0:
        return None
    return float(timing)


def build_project_ffmpeg_command(
    request: RenderRequest,
    *,
    executable: str = "ffmpeg",
    options: Sequence[str] = (),
) -> FFmpegCommand:
    """Construye un :class:`FFmpegCommand` a partir de un :class:`RenderRequest`.

    Las imágenes y las pistas de audio del manifest se convierten en
    :class:`FFmpegInput` conservando su orden; la salida se deriva de
    ``request.output_path`` y ``request.format.value``.

    Args:
        request: solicitud de renderizado con el manifest de origen.
        executable: binario de FFmpeg (por defecto ``"ffmpeg"``).
        options: opciones adicionales enviadas sin modificar al constructor de
            comandos.

    Returns:
        :class:`FFmpegCommand` con las entradas y la salida resueltas.

    Raises:
        RendererValidationError: si la solicitud o los activos no superan las
            validaciones de dominio.
    """
    if request is None or not isinstance(request, RenderRequest):
        raise RendererValidationError("'request' debe ser un RenderRequest.")

    errors: list[str] = []

    manifest = request.project_manifest
    if not isinstance(manifest, ProjectManifest):
        errors.append("'request.project_manifest' debe ser un ProjectManifest.")

    if not isinstance(request.output_path, Path):
        errors.append("'request.output_path' debe ser un Path.")
        output_path: Optional[Path] = None
    else:
        output_path = request.output_path

    if not isinstance(request.format, RendererFormat):
        errors.append("'request.format' debe ser un RendererFormat.")
        resolved_format: Optional[str] = None
    else:
        resolved_format = request.format.value

    ffmpeg_inputs: list[FFmpegInput] = []
    durations: list[Optional[float]] = []
    assets = manifest.assets if isinstance(manifest, ProjectManifest) else None
    if assets is None or isinstance(assets, (str, bytes)) or not isinstance(assets, Sequence):
        errors.append("'manifest.assets' debe ser una colección de ProjectAsset.")
    else:
        for index, asset in enumerate(assets):
            if not isinstance(asset, ProjectAsset):
                errors.append(f"El asset en la posición {index} no es un ProjectAsset.")
                continue
            if asset.kind not in (AssetKind.IMAGE, AssetKind.AUDIO):
                continue
            if not _is_valid_asset_path(asset.path):
                errors.append(f"El asset en la posición {index} no tiene una ruta válida.")
                continue
            ffmpeg_inputs.append(FFmpegInput(path=Path(asset.path)))
            if asset.kind == AssetKind.IMAGE:
                durations.append(_scene_duration(manifest, asset.scene_index))
            else:
                durations.append(None)

    if errors:
        raise RendererValidationError("; ".join(errors))

    options_obj = request.options
    fps = getattr(options_obj, "fps", None)

    return build_ffmpeg_command(
        inputs=ffmpeg_inputs,
        output=FFmpegOutput(path=output_path, format=resolved_format),
        options=options,
        executable=executable,
        durations=durations or None,
        fps=fps,
    )
