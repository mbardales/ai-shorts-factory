"""Abstracción de repositorio de persistencia (ME40.5, pre-Northflank).

Desacopla la persistencia de **metadata** (``RunRecord``, proyectos, proyecto
activo, asociación run→proyecto) de ``ApplicationService``, la API y el resto
de la capa Application: el servicio habla con un :class:`PersistenceRepository`
que hoy es :class:`LocalPersistenceRepository` (JSON sobre el filesystem) y que
en el futuro podrá ser ``PostgreSQLPersistenceRepository`` **sin cambiar** ni
``ApplicationService``, ni la API, ni el frontend, ni ``PipelineRunner``.

Alcance de ME40.5 (local, sin dependencias nuevas, sin servicios externos):

- El contrato cubre **únicamente** las operaciones que ``ApplicationService``
  necesita hoy (runs y proyectos). No es un CRUD genérico.
- No se duplican modelos: se reutilizan :class:`pipeline.lifecycle.RunRecord` y
  :class:`application.models.ProjectRecord`.
- :class:`LocalPersistenceRepository` reutiliza el mecanismo JSON existente
  (``run.json`` vía ``pipeline.lifecycle`` y ``projects.json`` vía
  ``application.projects``); **no cambia el formato de los archivos** y sigue
  siendo compatible con runs/proyectos ya generados.
- La cola (ME40.2) y el worker (ME40.4) siguen escribiendo el estado del run a
  través de ``pipeline.lifecycle`` (la misma persistencia JSON que envuelve este
  repositorio); migrarlos a un repositorio remoto queda reportado como trabajo
  futuro fuera del alcance de ME40.5.

Seguridad: todos los métodos de run reciben el ``run_id`` validado (nunca rutas
del cliente); :class:`LocalPersistenceRepository` reutiliza
``checked_run_dir`` para rechazar traversal/rutas absolutas.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from pipeline import PipelineValidationError, list_runs, load_run_record
from pipeline.lifecycle import RunRecord, checked_run_dir, write_run_record

from . import projects as _projects
from .models import ProjectRecord

logger = logging.getLogger(__name__)


class PersistenceRepository(ABC):
    """Contrato mínimo de persistencia de metadata (runs y proyectos).

    Todas las operaciones se expresan con identificadores (``run_id`` /
    ``project_id``) y modelos de dominio; **ninguna** expone rutas del
    filesystem. Así, una futura implementación sobre PostgreSQL puede sustituir
    a :class:`LocalPersistenceRepository` sin tocar ``ApplicationService``, la
    API ni el frontend.
    """

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    @abstractmethod
    def save_run(self, run_id: str, record: RunRecord) -> None:
        """Persiste (crea o actualiza) el estado de un run."""

    @abstractmethod
    def load_run(self, run_id: str) -> Optional[RunRecord]:
        """Lee el estado persistido de un run (o ``None`` si no hay estado
        legible)."""

    @abstractmethod
    def run_exists(self, run_id: str) -> bool:
        """Indica si el run existe en el repositorio."""

    @abstractmethod
    def list_runs(self) -> list[RunRecord]:
        """Lista los runs existentes en orden cronológico."""

    # ------------------------------------------------------------------
    # Proyectos
    # ------------------------------------------------------------------

    @abstractmethod
    def create_project(
        self, name: str, description: Optional[str] = None
    ) -> ProjectRecord:
        """Crea un proyecto y lo deja como activo."""

    @abstractmethod
    def list_projects(self) -> tuple[list[ProjectRecord], Optional[str]]:
        """Lista los proyectos y el proyecto activo."""

    @abstractmethod
    def get_project(self, project_id: str) -> ProjectRecord:
        """Devuelve un proyecto por id (o eleva si no existe)."""

    @abstractmethod
    def set_active_project(self, project_id: Optional[str]) -> None:
        """Establece (o limpia) el proyecto activo."""

    # ------------------------------------------------------------------
    # Asociación run -> proyecto
    # ------------------------------------------------------------------

    @abstractmethod
    def associate_run(self, run_id: str, project_id: Optional[str]) -> None:
        """Registra la asociación de un run con un proyecto."""

    @abstractmethod
    def project_id_for_run(self, run_id: str) -> Optional[str]:
        """Devuelve el ``project_id`` asociado a un run (o ``None``)."""


class LocalPersistenceRepository(PersistenceRepository):
    """Implementación local sobre el mecanismo JSON existente.

    Reutiliza ``pipeline.lifecycle`` (``run.json``) y ``application.projects``
    (``projects.json``) para mantener **exactamente** el formato actual de los
    archivos y la compatibilidad con runs/proyectos ya generados. La raíz de
    ejecuciones se resuelve desde ``PIPELINE_RUNS_ROOT`` (o ``output/runs``).
    """

    def __init__(self, runs_root: Optional[Path] = None) -> None:
        self._runs_root = Path(runs_root) if runs_root is not None else None
        logger.debug(
            "LocalPersistenceRepository creado (runs_root=%s).",
            self._runs_root or "default",
        )

    def _root(self) -> Path:
        """Raíz real de ejecuciones (explícita o por defecto)."""
        if self._runs_root is not None:
            return self._runs_root
        from pipeline.context import RUNS_ROOT

        return RUNS_ROOT

    def _run_dir(self, run_id: str) -> Path:
        """Directorio del run validado (``runs_root/<run_id>``).

        Raises:
            PipelineValidationError: si el ``run_id`` es inválido o inseguro.
        """
        root = self._root()
        return checked_run_dir(root / run_id, runs_root=root)

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def save_run(self, run_id: str, record: RunRecord) -> None:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        write_run_record(run_dir, record)

    def load_run(self, run_id: str) -> Optional[RunRecord]:
        return load_run_record(self._run_dir(run_id))

    def run_exists(self, run_id: str) -> bool:
        return self._run_dir(run_id).is_dir()

    def list_runs(self) -> list[RunRecord]:
        return list_runs(self._root())

    # ------------------------------------------------------------------
    # Proyectos
    # ------------------------------------------------------------------

    def create_project(
        self, name: str, description: Optional[str] = None
    ) -> ProjectRecord:
        return _projects.create_project(self._root(), name, description)

    def list_projects(self) -> tuple[list[ProjectRecord], Optional[str]]:
        return _projects.list_projects(self._root())

    def get_project(self, project_id: str) -> ProjectRecord:
        return _projects.get_project(self._root(), project_id)

    def set_active_project(self, project_id: Optional[str]) -> None:
        _projects.set_active_project(self._root(), project_id)

    def associate_run(self, run_id: str, project_id: Optional[str]) -> None:
        _projects.associate_run(self._root(), run_id, project_id)

    def project_id_for_run(self, run_id: str) -> Optional[str]:
        return _projects.project_id_for_run(self._root(), run_id)