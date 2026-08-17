"""Adaptador PostgreSQL de :class:`PersistenceRepository` (ME40.6).

Segunda implementación del contrato de persistencia de metadata (runs y
proyectos), lista para sustituir a :class:`LocalPersistenceRepository` **sin
modificar** ``ApplicationService``, la API, el worker, la cola ni el pipeline:

    service = ApplicationService(
        repository=PostgreSQLPersistenceRepository(),  # DATABASE_URL (env)
    )

Alcance de ME40.6 ("solo prepara el adapter"):

- El **contrato** :class:`PersistenceRepository` NO cambia: este adapter
  implementa exactamente las mismas operaciones que la versión local.
- La **metadata** (runs, proyectos, proyecto activo y asociación run→proyecto)
  vive en PostgreSQL; los **assets** (imágenes/audio/video) siguen en
  ``RunStorage`` (filesystem). La cola (``job.json``) y el worker siguen
  escribiendo el estado a través de ``pipeline.lifecycle``; migrarlos a la base
  queda reportado como trabajo futuro (las columnas ``topic``/``offline`` de
  ``runs`` están reservadas para ese momento).
- El driver PostgreSQL **no se instala automáticamente**. La importación del
  módulo es segura sin driver; solo al **usarlo** sin driver se eleva
  :class:`DependencyRequiredError` con el mensaje ``DEPENDENCY REQUIRED``.
  El driver se importa de forma perezosa (convención del proyecto).
- Para pruebas se puede inyectar una ``connection`` (duck-typed) y evitar así
  el driver y la red; es la vía que usan ``tests/test_postgres_repository_me406.py``.

Seguridad: los ``run_id`` y ``project_id`` se validan con el mismo formato
estricto que la API antes de formar cualquier consulta; nunca se construyen
rutas del filesystem a partir de ellos (salvo el directorio de trabajo local
del run, que se valida con ``checked_run_dir`` igual que la versión local).
El ``DATABASE_URL`` **nunca se imprime ni se guarda en logs**.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pipeline.context import validate_run_id
from pipeline.lifecycle import RunRecord, RunStatus, checked_run_dir

from .exceptions import ApplicationProjectNotFoundError, ApplicationValidationError
from .models import ProjectRecord
from .projects import project_id_from_name, validate_project_id
from .repository import PersistenceRepository

logger = logging.getLogger(__name__)

#: Dependencia exacta que se reporta cuando falta el driver (nunca se instala).
REQUIRED_DEPENDENCY = "psycopg[binary] (o psycopg2-binary)"


class DependencyRequiredError(RuntimeError):
    """El driver PostgreSQL requerido no está instalado."""


# ---------------------------------------------------------------------------
# SQL (DML del adapter; el DDL vive en ``sql/0001_initial_schema.sql``)
# ---------------------------------------------------------------------------

RUN_UPSERT = """
INSERT INTO runs (
    run_id, status, project_id,
    created_at, queued_at, started_at, finished_at, error, quality_passed
)
VALUES (
    %s, %s, (SELECT project_id FROM run_projects WHERE run_id = %s),
    %s, %s, %s, %s, %s, %s
)
ON CONFLICT (run_id) DO UPDATE SET
    status = EXCLUDED.status,
    project_id = COALESCE(EXCLUDED.project_id, runs.project_id),
    created_at = COALESCE(EXCLUDED.created_at, runs.created_at),
    queued_at = EXCLUDED.queued_at,
    started_at = EXCLUDED.started_at,
    finished_at = EXCLUDED.finished_at,
    error = EXCLUDED.error,
    quality_passed = EXCLUDED.quality_passed
"""

RUN_SELECT = """
SELECT run_id, status, created_at, queued_at, started_at, finished_at, error, quality_passed
FROM runs WHERE run_id = %s
"""

RUN_EXISTS = "SELECT COUNT(*) AS count FROM runs WHERE run_id = %s"

RUN_LIST = """
SELECT run_id, status, created_at, queued_at, started_at, finished_at, error, quality_passed
FROM runs ORDER BY created_at, run_id
"""

PROJECT_IDS = "SELECT id FROM projects"

PROJECT_INSERT = (
    "INSERT INTO projects (id, name, description, created_at) "
    "VALUES (%s, %s, %s, %s)"
)

PROJECT_SELECT = (
    "SELECT id, name, description, created_at FROM projects WHERE id = %s"
)

PROJECT_LIST = (
    "SELECT id, name, description, created_at FROM projects ORDER BY created_at, id"
)

PROJECT_CLEAR_ACTIVE = "UPDATE projects SET active = FALSE WHERE active = TRUE"

PROJECT_SET_ACTIVE = "UPDATE projects SET active = TRUE WHERE id = %s"

PROJECT_ACTIVE = "SELECT id FROM projects WHERE active = TRUE"

RUN_PROJECT_UPSERT = (
    "INSERT INTO run_projects (run_id, project_id) VALUES (%s, %s) "
    "ON CONFLICT (run_id) DO UPDATE SET project_id = EXCLUDED.project_id"
)

RUN_PROJECT_SELECT = "SELECT project_id FROM run_projects WHERE run_id = %s"


# ---------------------------------------------------------------------------
# Lectura del schema versionado (para el test del schema y el aprovisionado)
# ---------------------------------------------------------------------------


def load_schema_sql() -> str:
    """Devuelve el DDL versionado (``sql/0001_initial_schema.sql``)."""
    path = Path(__file__).resolve().parent / "sql" / "0001_initial_schema.sql"
    return path.read_text(encoding="utf-8")


SCHEMA_SQL = load_schema_sql()


# ---------------------------------------------------------------------------
# Conversión de filas a modelos de dominio
# ---------------------------------------------------------------------------


def _as_dt(value: object) -> Optional[datetime]:
    """Convierte un valor a datetime aware UTC (``None`` si es inválido)."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _iso(value: object) -> Optional[str]:
    """Convierte un valor a ISO 8601 UTC (o ``None``)."""
    parsed = _as_dt(value)
    return parsed.isoformat() if parsed is not None else None


def _record_from_row(row: dict) -> RunRecord:
    """Reconstruye un :class:`RunRecord` desde una fila (dict-row)."""
    try:
        status = RunStatus(str(row.get("status") or ""))
    except ValueError:
        status = RunStatus.UNKNOWN
    quality = row.get("quality_passed")
    if not isinstance(quality, bool):
        quality = None
    return RunRecord(
        run_id=str(row.get("run_id") or ""),
        status=status,
        created_at=_as_dt(row.get("created_at")),
        queued_at=_as_dt(row.get("queued_at")),
        started_at=_as_dt(row.get("started_at")),
        finished_at=_as_dt(row.get("finished_at")),
        error=row.get("error") if isinstance(row.get("error"), str) else None,
        quality_passed=quality,
    )


def _project_from_row(row: dict) -> ProjectRecord:
    """Reconstruye un :class:`ProjectRecord` desde una fila (dict-row)."""
    description = row.get("description")
    return ProjectRecord(
        project_id=str(row.get("id") or ""),
        name=str(row.get("name") or ""),
        description=description if isinstance(description, str) else None,
        created_at=_iso(row.get("created_at")),
    )


# ---------------------------------------------------------------------------
# Conexión (lazy, sin dependencia instalada hasta que se usa de verdad)
# ---------------------------------------------------------------------------


def _import_driver():
    """Importa perezosamente el driver PostgreSQL instalado.

    Raises:
        DependencyRequiredError: si no hay ningún driver PostgreSQL instalado.
    """
    for name in ("psycopg", "psycopg2"):
        try:
            return __import__(name)
        except ImportError:
            continue
    raise DependencyRequiredError(
        "DEPENDENCY REQUIRED: PostgreSQLPersistenceRepository necesita un driver "
        f"PostgreSQL instalado ({REQUIRED_DEPENDENCY}). No se instala "
        "automáticamente; añádelo a scripts/requirements.txt y al entorno (.venv) "
        "antes de usarlo."
    )


class _Psycopg2Connection:
    """Adapta una conexión psycopg2 a la API mínima ``execute`` del adapter."""

    def __init__(self, connection) -> None:
        self._connection = connection

    def execute(self, sql: str, params=None):
        import psycopg2.extras

        cursor = self._connection.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )
        cursor.execute(sql, params)
        return cursor

    def close(self) -> None:
        self._connection.close()


class PostgreSQLPersistenceRepository(PersistenceRepository):
    """Implementación de :class:`PersistenceRepository` sobre PostgreSQL.

    Args:
        dsn: URL de conexión libpq; por defecto ``os.environ["DATABASE_URL"]``.
        connection: conexión inyectada (duck-typed, API ``execute(sql, params)``
            devolviendo un cursor con ``fetchall``/``fetchone``) para pruebas y
            reutilización; evita el driver y la red. Si se inyecta, no se exige
            driver instalado.
        runs_root: raíz de ejecuciones para el directorio de trabajo local del
            run (``job.json`` y artefactos de la cola); por defecto la de
            ``pipeline.context``. La metadata vive en PostgreSQL.
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        *,
        connection=None,
        runs_root: Optional[Path] = None,
    ) -> None:
        self._dsn = dsn if dsn is not None else _env_dsn()
        self._runs_root = Path(runs_root) if runs_root is not None else None
        self._connection = connection
        self._driver = None if connection is not None else _import_driver()
        logger.debug(
            "PostgreSQLPersistenceRepository creado (dsn_configured=%s, "
            "connection_injected=%s, runs_root=%s).",
            bool(self._dsn),
            connection is not None,
            self._runs_root or "default",
        )

    # ------------------------------------------------------------------
    # Helpers de conexión y directorio de trabajo
    # ------------------------------------------------------------------

    def _root(self) -> Path:
        """Raíz real de ejecuciones (explícita o por defecto)."""
        if self._runs_root is not None:
            return self._runs_root
        from pipeline.context import RUNS_ROOT

        return RUNS_ROOT

    def _connect(self):
        """Abre una conexión real (solo cuando no hay conexión inyectada)."""
        if not self._dsn:
            raise DependencyRequiredError(
                "DATABASE_URL no configurada: define DATABASE_URL (o pasa 'dsn') "
                "para PostgreSQLPersistenceRepository."
            )
        driver = self._driver
        if hasattr(driver, "rows"):  # psycopg (v3)
            return driver.connect(self._dsn, row_factory=driver.rows.dict_row)
        return _Psycopg2Connection(driver.connect(self._dsn))  # psycopg2

    def _execute(self, sql: str, params=None):
        """Ejecuta una consulta y devuelve un cursor con ``fetchall``/``fetchone``."""
        if self._connection is not None:
            return self._connection.execute(sql, params)
        connection = self._connect()
        try:
            return connection.execute(sql, params)
        finally:
            connection.close()

    def _ensure_work_dir(self, run_id: str) -> None:
        """Crea el directorio local de trabajo del run (cola: ``job.json``).

        Igual que :class:`LocalPersistenceRepository`, crea
        ``runs_root/<run_id>/`` para que ``ApplicationService`` pueda escribir
        los parámetros de la cola y el pipeline sus artefactos; la **metadata**
        del run se guarda en PostgreSQL.
        """
        root = self._root()
        checked_run_dir(root / run_id, runs_root=root).mkdir(
            parents=True, exist_ok=True
        )

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def save_run(self, run_id: str, record: RunRecord) -> None:
        self._ensure_work_dir(run_id)
        self._execute(
            RUN_UPSERT,
            (
                record.run_id or run_id,
                record.status.value,
                run_id,
                record.created_at,
                record.queued_at,
                record.started_at,
                record.finished_at,
                record.error,
                record.quality_passed,
            ),
        )
        logger.debug("Run %s persistido en PostgreSQL (status=%s).", run_id, record.status.value)

    def load_run(self, run_id: str) -> Optional[RunRecord]:
        validate_run_id(run_id)
        cursor = self._execute(RUN_SELECT, (run_id,))
        rows = cursor.fetchall()
        if not rows:
            return None
        return _record_from_row(rows[0])

    def run_exists(self, run_id: str) -> bool:
        validate_run_id(run_id)
        cursor = self._execute(RUN_EXISTS, (run_id,))
        row = cursor.fetchone()
        return bool(row and int(row["count"]) > 0)

    def list_runs(self) -> list[RunRecord]:
        cursor = self._execute(RUN_LIST)
        return [_record_from_row(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Proyectos
    # ------------------------------------------------------------------

    def create_project(
        self, name: str, description: Optional[str] = None
    ) -> ProjectRecord:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ApplicationValidationError("El nombre del proyecto no puede estar vacío.")
        clean_description = (description or "").strip() or None
        cursor = self._execute(PROJECT_IDS)
        existing = {str(row["id"]) for row in cursor.fetchall()}
        record = ProjectRecord(
            project_id=project_id_from_name(clean_name, existing),
            name=clean_name,
            description=clean_description,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._execute(
            PROJECT_INSERT,
            (record.project_id, record.name, record.description, record.created_at),
        )
        self.set_active_project(record.project_id)
        logger.info("Proyecto creado: %s (%s).", record.name, record.project_id)
        return record

    def list_projects(self) -> tuple[list[ProjectRecord], Optional[str]]:
        cursor = self._execute(PROJECT_LIST)
        projects = [_project_from_row(row) for row in cursor.fetchall()]
        active_cursor = self._execute(PROJECT_ACTIVE)
        active_row = active_cursor.fetchone()
        active = str(active_row["id"]) if active_row else None
        return projects, active

    def get_project(self, project_id: str) -> ProjectRecord:
        validate_project_id(project_id)
        cursor = self._execute(PROJECT_SELECT, (project_id,))
        rows = cursor.fetchall()
        if not rows:
            raise ApplicationProjectNotFoundError(f"No existe el proyecto: {project_id}")
        return _project_from_row(rows[0])

    def set_active_project(self, project_id: Optional[str]) -> None:
        if project_id is not None:
            validate_project_id(project_id)
            self.get_project(project_id)  # eleva si no existe
        self._execute(PROJECT_CLEAR_ACTIVE)
        if project_id is not None:
            self._execute(PROJECT_SET_ACTIVE, (project_id,))
        logger.info("Proyecto activo: %s.", project_id)

    # ------------------------------------------------------------------
    # Asociación run -> proyecto
    # ------------------------------------------------------------------

    def associate_run(self, run_id: str, project_id: Optional[str]) -> None:
        validate_run_id(run_id)
        self._execute(RUN_PROJECT_UPSERT, (run_id, project_id))

    def project_id_for_run(self, run_id: str) -> Optional[str]:
        validate_run_id(run_id)
        cursor = self._execute(RUN_PROJECT_SELECT, (run_id,))
        row = cursor.fetchone()
        return str(row["project_id"]) if row and row["project_id"] else None


def _env_dsn() -> str:
    """Lee ``DATABASE_URL`` del entorno (o devuelve vacío, sin imprimirlo)."""
    import os

    return os.environ.get("DATABASE_URL", "") or ""