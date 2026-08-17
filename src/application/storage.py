"""Abstracción de almacenamiento de runs (ME40.3, pre-Northflank).

Separa el conocimiento del **filesystem local** de la capa Application/API:
:class:`RunStorage` define el contrato mínimo que ``ApplicationService`` y la
API necesitan, y :class:`LocalRunStorage` lo implementa sobre ``PIPELINE_RUNS_ROOT``
manteniendo **exactamente** el layout actual de los runs.

Objetivo: poder sustituir en el futuro :class:`LocalRunStorage` por una
implementación sobre object storage (S3/R2/...) **sin cambiar** ni la API, ni
``ApplicationService``, ni el frontend. Durante ME40.3 el pipeline sigue
escribiendo en el filesystem local (la abstracción es para la capa
Application/API); migrar el pipeline a object storage queda reportado como
trabajo futuro.

Seguridad: los métodos operan exclusivamente con el ``run_id`` validado y
nunca aceptan rutas arbitrarias del cliente. Un ``run_id`` ausente, fuera del
formato o que resuelva fuera de ``PIPELINE_RUNS_ROOT`` se reporta con
:class:`StorageError` (el llamador decide mapearlo a 4xx); un run o recurso
inexistente devuelve ``None``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Optional

from pipeline import PipelineValidationError
from pipeline.lifecycle import RunStatus, checked_run_dir, load_run_record

logger = logging.getLogger(__name__)

#: Extensiones de video servibles dentro de ``output/video``.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}


class StorageError(Exception):
    """Error de la capa de almacenamiento (``run_id`` inválido o inseguro)."""


class RunStorage(ABC):
    """Contrato mínimo de almacenamiento de runs.

    Todos los métodos reciben solo el ``run_id`` (nunca rutas). Un ``run_id``
    inválido/inseguro lanza :class:`StorageError`; un run/recurso inexistente
    devuelve ``None``/vacío. El contrato cubre únicamente lo que la capa
    Application/API necesita hoy: localizar el directorio del run, comprobar y
    resolver su video, abrirlo para servirlo y listar sus assets.
    """

    @abstractmethod
    def run_dir(self, run_id: str) -> Optional[Path]:
        """Directorio validado del run (``runs_root/<run_id>``) o ``None``.

        Raises:
            StorageError: si el ``run_id`` es inválido o inseguro.
        """

    @abstractmethod
    def has_video(self, run_id: str) -> bool:
        """Indica si el run tiene un video servible (``SUCCESS``/``QUALITY_FAILED``)."""

    @abstractmethod
    def resolve_video(self, run_id: str) -> Optional[Path]:
        """Ruta local del video servible del run, o ``None``.

        La ruta siempre queda dentro de ``runs_root/<run_id>/output/video``.
        """

    @abstractmethod
    def open_video(self, run_id: str) -> Optional[BinaryIO]:
        """Abre el video del run en modo binario de solo lectura, o ``None``.

        El llamador es responsable de cerrar el flujo.
        """

    @abstractmethod
    def list_assets(self, run_id: str) -> tuple[str, ...]:
        """Nombres de los assets del run (relativos a ``output/``), o vacío."""


class LocalRunStorage(RunStorage):
    """Implementación sobre el filesystem local (layout actual de runs).

    Usa ``PIPELINE_RUNS_ROOT`` (por defecto ``output/runs`` del proyecto) y
    mantiene la estructura existente de los runs: ``run.json``, ``output/``,
    ``content.json``, ``project.json``, ``images/``, ``audio/``,
    ``subtitles.ass`` y ``video/``. Compatible con los runs ya generados.
    """

    def __init__(self, runs_root: Optional[Path] = None) -> None:
        self._runs_root = Path(runs_root) if runs_root is not None else None
        logger.debug(
            "LocalRunStorage creado (runs_root=%s).",
            self._runs_root or "default",
        )

    def _root(self) -> Path:
        """Raíz real de ejecuciones (explicita o por defecto)."""
        if self._runs_root is not None:
            return self._runs_root
        from pipeline.context import RUNS_ROOT

        return RUNS_ROOT

    def _checked_run_dir(self, run_id: str) -> Path:
        """Valida el ``run_id`` y devuelve el ``run_dir`` seguro.

        Raises:
            StorageError: si el ``run_id`` es inválido o queda fuera de la raíz.
        """
        if not isinstance(run_id, str) or not run_id:
            raise StorageError("run_id inválido.")
        root = self._root()
        try:
            return checked_run_dir(root / run_id, runs_root=root)
        except PipelineValidationError as exc:
            raise StorageError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Contrato
    # ------------------------------------------------------------------

    def run_dir(self, run_id: str) -> Optional[Path]:
        safe = self._checked_run_dir(run_id)
        return safe if safe.is_dir() else None

    def has_video(self, run_id: str) -> bool:
        return self.resolve_video(run_id) is not None

    def resolve_video(self, run_id: str) -> Optional[Path]:
        run_dir = self.run_dir(run_id)
        if run_dir is None:
            return None
        record = load_run_record(run_dir)
        if record is None or record.status not in (
            RunStatus.SUCCESS,
            RunStatus.QUALITY_FAILED,
        ):
            return None
        return self._find_video(run_dir)

    def open_video(self, run_id: str) -> Optional[BinaryIO]:
        path = self.resolve_video(run_id)
        if path is None:
            return None
        try:
            return path.open("rb")
        except OSError:
            return None

    def list_assets(self, run_id: str) -> tuple[str, ...]:
        run_dir = self.run_dir(run_id)
        if run_dir is None:
            return ()
        output = run_dir / "output"
        if not output.is_dir():
            return ()
        return tuple(
            path.relative_to(output).as_posix()
            for path in sorted(output.rglob("*"))
            if path.is_file()
        )

    # ------------------------------------------------------------------
    # Ayudantes
    # ------------------------------------------------------------------

    @staticmethod
    def _find_video(run_dir: Path) -> Optional[Path]:
        """Primer video renderizado de ``run_dir/output/video`` (o ``None``)."""
        video_dir = run_dir / "output" / "video"
        if not video_dir.is_dir():
            return None
        for entry in sorted(video_dir.iterdir()):
            if entry.is_file() and entry.suffix.lower() in VIDEO_EXTENSIONS:
                return entry
        return None