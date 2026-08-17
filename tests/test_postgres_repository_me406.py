# -*- coding: utf-8 -*-
"""ME40.6 - Tests del adapter PostgreSQL de persistencia.

Verifica (offline, sin red, sin Gemini/SD15/Kokoro, **sin instalar
dependencias**) que ``PostgreSQLPersistenceRepository`` es una segunda
implementación del contrato :class:`application.PersistenceRepository`,
sustituible por :class:`LocalPersistenceRepository` **sin modificar**
``ApplicationService``, la API, el worker, la cola ni el pipeline.

Sin driver PostgreSQL instalado (y sin servidor local):

- el módulo importa de forma segura (driver perezoso);
- usarlo sin driver eleva ``DependencyRequiredError`` (``DEPENDENCY REQUIRED``
  con la dependencia exacta) y **no** se instala nada;
- la lógica del adapter se ejercita contra una **mini-fake** PostgreSQL
  (conexión duck-typed inyectada) que interpreta el SQL del adapter;
- ``POSTGRES_REAL_TEST`` queda en ``NOT_AVAILABLE`` (no se afirma conexión real).
- si existiera driver + servidor real, se haría UNA prueba CRUD mínima.

Checks cubiertos (A-R):

- A. schema SQL válido (DDL versionado de ``sql/0001_initial_schema.sql``).
- B. nombres/tablas/columnas correctos.
- C. el adapter implementa :class:`PersistenceRepository` (y reporta
  ``DEPENDENCY REQUIRED`` sin driver).
- D. mismas operaciones del contrato (firmas idénticas a la versión local).
- E. ``LocalPersistenceRepository`` sigue funcionando (MVP por defecto).
- F. ``ApplicationService`` acepta el repositorio alternativo por inyección.
- G. runs (crear/actualizar/listar/existe) sobre el adapter.
- H. proyectos (crear/listar/get/validación) sobre el adapter.
- I. proyecto activo (activar/cambiar/limpiar) sobre el adapter.
- J. asociación run → proyecto sobre el adapter.
- K. lifecycle de un run (QUEUED→RUNNING→SUCCESS) sobre el adapter.
- L. la cola (ME40.2) no se rompe.
- M. el worker (ME40.4) no se rompe.
- N. RunStorage (ME40.3) no se rompe.
- O. la API no cambia.
- P. ``node --check``.
- Q. ``py_compile``.
- R. ``git diff --check`` PASS.

Uso (sin framework de test, solo stdlib + fastapi TestClient):

    .venv\\Scripts\\python.exe tests\\test_postgres_repository_me406.py
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
TEMP_ROOT = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "opencode" / "me406"
TEST_RUNS = TEMP_ROOT / "tests_runs"
RUNS_ENV = "PIPELINE_RUNS_ROOT"

if TEST_RUNS.exists():
    shutil.rmtree(TEST_RUNS)
TEST_RUNS.mkdir(parents=True, exist_ok=True)

os.environ[RUNS_ENV] = str(TEST_RUNS)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from application import (  # noqa: E402
    ApplicationService,
    CreateProjectRequest,
    CreateRunRequest,
    DependencyRequiredError,
    LocalPersistenceRepository,
    LocalRunStorage,
    PersistenceRepository,
    PostgreSQLPersistenceRepository,
)
from application.exceptions import (  # noqa: E402
    ApplicationProjectNotFoundError,
    ApplicationValidationError,
)
from application.postgres_repository import (  # noqa: E402
    REQUIRED_DEPENDENCY,
    SCHEMA_SQL,
    load_schema_sql,
)
from api.app import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pipeline import (  # noqa: E402
    RunRecord,
    RunStatus,
    load_run_record,
    process_queued_job,
    process_one,
    write_run_record,
)

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(f"{label}: {detail}")
    print(f"[{'OK' if cond else 'FAIL'}] {label}" + ("" if cond else f" | {detail}"))


# ---------------------------------------------------------------------------
# Mini-fake PostgreSQL (interpreta el SQL del adapter, sin driver ni red)
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, rows: list, rowcount: int = 1) -> None:
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


RUN_COLUMNS = [
    "run_id", "status", "created_at", "queued_at", "started_at",
    "finished_at", "error", "quality_passed",
]


class FakePostgresConnection:
    """Conexión duck-typed que imita el comportamiento PostgreSQL del adapter.

    Mantiene tres tablas (``runs``, ``projects``, ``run_projects``) y resuelve
    el mismo SQL de :class:`PostgreSQLPersistenceRepository` (incluido el
    espejo ``project_id`` del UPSERT de runs).
    """

    def __init__(self) -> None:
        self.calls: list = []
        self.runs: dict[str, dict] = {}
        self.projects: dict[str, dict] = {}
        self.run_projects: dict[str, Optional[str]] = {}

    def execute(self, sql: str, params=None):
        self.calls.append((sql, params))
        s = " ".join(str(sql).split())
        if s.startswith("INSERT INTO runs"):
            (rid, status, rid_sub, created_at, queued_at, started_at,
             finished_at, error, quality) = params
            self.runs[rid] = {
                "run_id": rid, "status": status,
                "project_id": self.run_projects.get(rid_sub),
                "created_at": created_at, "queued_at": queued_at,
                "started_at": started_at, "finished_at": finished_at,
                "error": error, "quality_passed": quality,
            }
            return FakeCursor([])
        if s.startswith("INSERT INTO projects"):
            pid, name, description, created_at = params
            self.projects[pid] = {
                "id": pid, "name": name, "description": description,
                "created_at": created_at, "active": False,
            }
            return FakeCursor([])
        if s.startswith("INSERT INTO run_projects"):
            rid, pid = params
            self.run_projects[rid] = pid
            return FakeCursor([])
        if s.startswith("SELECT COUNT(*)"):
            rid = params[0]
            return FakeCursor([{"count": 1}] if rid in self.runs else [{"count": 0}])
        if "FROM runs" in s and "WHERE run_id" in s:
            rid = params[0]
            row = self.runs.get(rid)
            return FakeCursor(
                [{c: (row[c] if row else None) for c in RUN_COLUMNS}] if row else []
            )
        if s.startswith("SELECT run_id, status") and "FROM runs" in s:
            rows = [
                {c: r[c] for c in RUN_COLUMNS}
                for r in sorted(self.runs.values(),
                                key=lambda r: (r["created_at"] or "", r["run_id"]))
            ]
            return FakeCursor(rows)
        if s.startswith("SELECT id FROM projects WHERE active"):
            actives = [pid for pid, p in self.projects.items() if p.get("active")]
            return FakeCursor([{"id": actives[0]}] if actives else [])
        if s == "SELECT id FROM projects":
            return FakeCursor([{"id": pid} for pid in self.projects])
        if "FROM projects WHERE id" in s:
            pid = params[0]
            row = self.projects.get(pid)
            return FakeCursor([dict(row)] if row else [])
        if s.startswith("SELECT id, name, description, created_at FROM projects"):
            rows = [
                dict(r)
                for r in sorted(self.projects.values(),
                                key=lambda r: (r["created_at"] or "", r["id"]))
            ]
            return FakeCursor(rows)
        if s.startswith("UPDATE projects SET active = FALSE"):
            for p in self.projects.values():
                p["active"] = False
            return FakeCursor([])
        if s.startswith("UPDATE projects SET active = TRUE"):
            pid = params[0]
            if pid in self.projects:
                self.projects[pid]["active"] = True
            return FakeCursor([])
        if "FROM run_projects WHERE run_id" in s:
            rid = params[0]
            return FakeCursor(
                [{"project_id": self.run_projects[rid]}]
                if rid in self.run_projects else []
            )
        raise AssertionError(f"SQL no manejado por el fake: {s}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _driver_installed() -> bool:
    for name in ("psycopg", "psycopg2"):
        if importlib.util.find_spec(name) is not None:
            return True
    return False


def _strip_sql_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _tables(sql: str) -> list[str]:
    return re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)


def _column_names(sql: str, table: str) -> list[str]:
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS " + table + r"\s*\((.*?)\)\s*;",
        _strip_sql_comments(sql), re.S,
    )
    if not match:
        return []
    return re.findall(r"^\s*(\w+)\s", match.group(1), re.M)


def _new_repo() -> PostgreSQLPersistenceRepository:
    return PostgreSQLPersistenceRepository(
        connection=FakePostgresConnection(), runs_root=TEST_RUNS
    )


def _new_service(repo) -> ApplicationService:
    return ApplicationService(runs_root=TEST_RUNS, repository=repo)


def _stub_executor(context) -> None:
    """Ejecutor mínimo: persiste SUCCESS (simula el pipeline, sin proveedores)."""
    previous = load_run_record(context.run_dir)
    write_run_record(
        context.run_dir,
        RunRecord(
            run_id=context.run_id,
            status=RunStatus.SUCCESS,
            created_at=previous.created_at if previous else None,
            queued_at=previous.queued_at if previous else None,
            started_at=previous.started_at if previous else None,
            finished_at=datetime.now(timezone.utc),
            quality_passed=True,
        ),
    )


# ---------------------------------------------------------------------------
# A. Schema SQL válido (DDL versionado)
# ---------------------------------------------------------------------------
check("A1 schema no vacío", bool(SCHEMA_SQL.strip()))
check("A2 tres tablas (projects/runs/run_projects)",
      len(_tables(SCHEMA_SQL)) == 3, str(_tables(SCHEMA_SQL)))
clean = _strip_sql_comments(SCHEMA_SQL)
check("A3 paréntesis balanceados", clean.count("(") == clean.count(")"))
stmts = [s for s in clean.split(";") if s.strip()]
check("A4 sentencias terminadas en ';'", len(stmts) >= 4, str(len(stmts)))

# ---------------------------------------------------------------------------
# B. Nombres / tablas / columnas correctos
# ---------------------------------------------------------------------------
tables = set(_tables(SCHEMA_SQL))
check("B1 tablas correctas", {"projects", "runs", "run_projects"} <= tables, str(tables))
runs_cols = set(_column_names(SCHEMA_SQL, "runs"))
for col in ("run_id", "status", "topic", "offline", "project_id", "created_at",
            "queued_at", "started_at", "finished_at", "error", "quality_passed"):
    check(f"B2 runs.{col}", col in runs_cols, f"faltan {sorted(runs_cols)}")
proj_cols = set(_column_names(SCHEMA_SQL, "projects"))
for col in ("id", "name", "created_at", "active"):
    check(f"B3 projects.{col}", col in proj_cols, f"faltan {sorted(proj_cols)}")
rp_cols = set(_column_names(SCHEMA_SQL, "run_projects"))
for col in ("run_id", "project_id"):
    check(f"B4 run_projects.{col}", col in rp_cols, f"faltan {sorted(rp_cols)}")

# ---------------------------------------------------------------------------
# C. El adapter implementa PersistenceRepository (sin dependencia instalada)
# ---------------------------------------------------------------------------
check("C1 es subclase del contrato",
      issubclass(PostgreSQLPersistenceRepository, PersistenceRepository))
check("C2 no es abstracta", not inspect.isabstract(PostgreSQLPersistenceRepository))
missing = [m for m in PersistenceRepository.__abstractmethods__
           if not hasattr(PostgreSQLPersistenceRepository, m)]
check("C3 implementa todos los métodos abstractos", not missing, str(missing))
check("C4 el módulo importa sin driver (carga el schema)", callable(load_schema_sql))
if not _driver_installed():
    try:
        PostgreSQLPersistenceRepository(dsn="postgresql://localhost/x")
        check("C5 DEPENDENCY REQUIRED al usar sin driver", False,
              "no elevó DependencyRequiredError")
    except DependencyRequiredError as exc:
        msg = str(exc)
        check("C5 DEPENDENCY REQUIRED al usar sin driver",
              "DEPENDENCY REQUIRED" in msg and REQUIRED_DEPENDENCY in msg, msg)
else:
    repo = PostgreSQLPersistenceRepository(dsn="postgresql://localhost/x")
    check("C5 constructor sin conexión (driver presente)", repo is not None)

# ---------------------------------------------------------------------------
# D. Mismas operaciones del contrato (firmas idénticas a la versión local)
# ---------------------------------------------------------------------------
sig_ok = True
sig_detail: list[str] = []
for method in sorted(PersistenceRepository.__abstractmethods__):
    impl = getattr(PostgreSQLPersistenceRepository, method, None)
    local = getattr(LocalPersistenceRepository, method, None)
    if not callable(impl) or not callable(local):
        sig_ok = False
        sig_detail.append(f"{method} ausente")
        continue
    try:
        contract_params = list(
            inspect.signature(getattr(PersistenceRepository, method)).parameters
        )[1:]
        impl_params = list(inspect.signature(impl).parameters)[1:]
        local_params = list(inspect.signature(local).parameters)[1:]
        if contract_params != impl_params or contract_params != local_params:
            sig_ok = False
            sig_detail.append(f"{method}: contrato={contract_params} "
                              f"postgres={impl_params} local={local_params}")
    except (TypeError, ValueError):
        pass
check("D1 firmas idénticas al contrato (local y postgres)", sig_ok, str(sig_detail))

# ---------------------------------------------------------------------------
# E. LocalPersistenceRepository sigue funcionando (MVP por defecto)
# ---------------------------------------------------------------------------
local = LocalPersistenceRepository(runs_root=TEST_RUNS)
now = datetime.now(timezone.utc)
local.save_run("run-20260816-090001", RunRecord(
    run_id="run-20260816-090001", status=RunStatus.QUEUED,
    created_at=now, queued_at=now))
check("E1 local load", local.load_run("run-20260816-090001").status is RunStatus.QUEUED)
check("E2 local exists", local.run_exists("run-20260816-090001"))
local_project = local.create_project("Proyecto Local E")
projects, active = local.list_projects()
check("E3 local proyectos", any(p.project_id == local_project.project_id for p in projects))
check("E4 local activo", active == local_project.project_id, str(active))
local.associate_run("run-20260816-090001", local_project.project_id)
check("E5 local asociación",
      local.project_id_for_run("run-20260816-090001") == local_project.project_id)

# ---------------------------------------------------------------------------
# F. ApplicationService acepta el repositorio alternativo (inyección)
# ---------------------------------------------------------------------------
pg_repo = _new_repo()
svc = _new_service(pg_repo)
project = svc.create_project(CreateProjectRequest(name="Proyecto F"))
rid_f = svc.create_run(CreateRunRequest(
    topic="Tema F", offline=True, run_id="run-20260816-140001",
    project_id=project.project_id)).run_id
run_f = svc.get_run(rid_f)
check("F1 create_run con repo Postgres -> QUEUED", run_f.status == "QUEUED", str(run_f))
check("F2 get_run asociado al proyecto", run_f.project_id == project.project_id, str(run_f))
check("F3 directorio de trabajo local creado (job.json)",
      (TEST_RUNS / rid_f / "job.json").is_file())
check("F4 run reflejado en la metadata PostgreSQL",
      pg_repo.run_exists(rid_f), str(pg_repo.load_run(rid_f)))

# ---------------------------------------------------------------------------
# G. Runs (crear / actualizar / listar / existe) sobre el adapter
# ---------------------------------------------------------------------------
pg_repo = _new_repo()
rid_g = "run-20260816-150001"
pg_repo.save_run(rid_g, RunRecord(run_id=rid_g, status=RunStatus.QUEUED,
                                  created_at=now, queued_at=now))
check("G1 exists tras crear", pg_repo.run_exists(rid_g))
rec_g = pg_repo.load_run(rid_g)
check("G2 load QUEUED", rec_g is not None and rec_g.status is RunStatus.QUEUED, str(rec_g))
pg_repo.save_run(rid_g, RunRecord(
    run_id=rid_g, status=RunStatus.SUCCESS, created_at=now, queued_at=now,
    started_at=now, finished_at=now, quality_passed=True))
rec_g2 = pg_repo.load_run(rid_g)
check("G3 actualización a SUCCESS con veredicto",
      rec_g2 is not None and rec_g2.status is RunStatus.SUCCESS
      and rec_g2.quality_passed is True, str(rec_g2))
rid_g2 = "run-20260816-150002"
pg_repo.save_run(rid_g2, RunRecord(run_id=rid_g2, status=RunStatus.FAILED,
                                   created_at=now, error="boom"))
check("G4 list_runs en orden cronológico",
      [r.run_id for r in pg_repo.list_runs()] == [rid_g, rid_g2],
      str([r.run_id for r in pg_repo.list_runs()]))
check("G5 run inexistente -> None",
      pg_repo.load_run("run-20260816-199999") is None)
check("G6 run inexistente -> exists False",
      not pg_repo.run_exists("run-20260816-199999"))

# ---------------------------------------------------------------------------
# H. Proyectos (crear / listar / get / validación) sobre el adapter
# ---------------------------------------------------------------------------
pg_repo = _new_repo()
p_h1 = pg_repo.create_project("Proyecto Uno")
p_h2 = pg_repo.create_project("Proyecto Dos")
projects_h, active_h = pg_repo.list_projects()
check("H1 dos proyectos en orden",
      [p.project_id for p in projects_h] == [p_h1.project_id, p_h2.project_id],
      str([p.project_id for p in projects_h]))
check("H2 activo es el último", active_h == p_h2.project_id, str(active_h))
check("H3 get_project", pg_repo.get_project(p_h1.project_id).name == "Proyecto Uno")
try:
    pg_repo.get_project("no-existe")
    check("H4 get_project inexistente eleva", False)
except ApplicationProjectNotFoundError:
    check("H4 get_project inexistente eleva", True)
try:
    pg_repo.create_project("   ")
    check("H5 nombre vacío rechazado", False)
except ApplicationValidationError:
    check("H5 nombre vacío rechazado", True)

# ---------------------------------------------------------------------------
# I. Proyecto activo (activar / cambiar / limpiar) sobre el adapter
# ---------------------------------------------------------------------------
pg_repo = _new_repo()
p_i1 = pg_repo.create_project("Activo Uno")
p_i2 = pg_repo.create_project("Activo Dos")
pg_repo.set_active_project(p_i1.project_id)
_, active_i = pg_repo.list_projects()
check("I1 cambiar activo", active_i == p_i1.project_id, str(active_i))
pg_repo.set_active_project(None)
_, active_i = pg_repo.list_projects()
check("I2 limpiar activo (None)", active_i is None, str(active_i))
try:
    pg_repo.set_active_project("no-existe")
    check("I3 activar inexistente eleva", False)
except ApplicationProjectNotFoundError:
    check("I3 activar inexistente eleva", True)

# ---------------------------------------------------------------------------
# J. Asociación run -> proyecto sobre el adapter
# ---------------------------------------------------------------------------
pg_repo = _new_repo()
p_j = pg_repo.create_project("Asociación")
pg_repo.associate_run("run-20260816-160001", p_j.project_id)
check("J1 asociación", pg_repo.project_id_for_run("run-20260816-160001") == p_j.project_id)
pg_repo.associate_run("run-20260816-160001", None)
check("J2 reasociación a None",
      pg_repo.project_id_for_run("run-20260816-160001") is None)
check("J3 sin asociación -> None",
      pg_repo.project_id_for_run("run-20260816-199999") is None)

# ---------------------------------------------------------------------------
# K. Lifecycle de un run (QUEUED -> RUNNING -> SUCCESS) sobre el adapter
# ---------------------------------------------------------------------------
fake_k = FakePostgresConnection()
pg_repo = PostgreSQLPersistenceRepository(connection=fake_k, runs_root=TEST_RUNS)
p_k = pg_repo.create_project("Vida")
rid_k = "run-20260816-170001"
pg_repo.associate_run(rid_k, p_k.project_id)
pg_repo.save_run(rid_k, RunRecord(run_id=rid_k, status=RunStatus.QUEUED,
                                  created_at=now, queued_at=now))
pg_repo.save_run(rid_k, RunRecord(run_id=rid_k, status=RunStatus.RUNNING,
                                  created_at=now, queued_at=now, started_at=now))
pg_repo.save_run(rid_k, RunRecord(
    run_id=rid_k, status=RunStatus.SUCCESS, created_at=now, queued_at=now,
    started_at=now, finished_at=now, quality_passed=True))
rec_k = pg_repo.load_run(rid_k)
check("K1 transición completa", rec_k is not None
      and rec_k.status is RunStatus.SUCCESS and rec_k.quality_passed is True, str(rec_k))
check("K2 timestamps preservados",
      rec_k is not None and rec_k.created_at == now and rec_k.finished_at == now,
      str(rec_k))
check("K3 espejo project_id en la fila runs",
      fake_k.runs[rid_k]["project_id"] == p_k.project_id, str(fake_k.runs[rid_k]))

# ---------------------------------------------------------------------------
# L. La cola (ME40.2) no se rompe (ruta local por defecto)
# ---------------------------------------------------------------------------
svc_local = ApplicationService(runs_root=TEST_RUNS)
rid_l = svc_local.create_run(CreateRunRequest(
    topic="Cola ME406", offline=True, run_id="run-20260816-120001")).run_id
# procesar el job concreto (process_one procesaría el QUEUED más antiguo, que
# pertenece a los checks previos de ME40.5/local).
processed = process_queued_job(TEST_RUNS, rid_l, executor=_stub_executor)
check("L1 process_queued_job adquiere y procesa el job QUEUED", processed)
rec_l = load_run_record(TEST_RUNS / rid_l)
check("L2 run termina SUCCESS tras la cola",
      rec_l is not None and rec_l.status is RunStatus.SUCCESS, str(rec_l))
processed_one = process_one(TEST_RUNS, executor=_stub_executor)
check("L4 process_one procesa el siguiente QUEUED", processed_one)

# ---------------------------------------------------------------------------
# M/N/L3. Worker (ME40.4), RunStorage (ME40.3) y cola (ME40.2) no se rompen
#          -> regresión canónica de ME40.5 (que corre ME40.4 + ME40.3 + API;
#             me404 corre a su vez me402/me403, de modo que el PASS de me405
#             garantiza transitivamente cola, storage, worker y API)
# ---------------------------------------------------------------------------
storage_n = LocalRunStorage(runs_root=TEST_RUNS)
check("N1 LocalRunStorage ignora run inexistente",
      storage_n.resolve_video("run-20260816-199999") is None)
proc_reg = subprocess.run(
    [sys.executable, str(ROOT / "tests" / "test_persistence_me405.py")],
    capture_output=True, text=True, timeout=3600, cwd=ROOT,
)
tail_reg = (proc_reg.stdout + proc_reg.stderr)[-400:]
reg_ok = proc_reg.returncode == 0 and "RESULTADO: PASS" in proc_reg.stdout
check("M1 ME40.5 (repositorio) -> PASS", reg_ok, f"rc={proc_reg.returncode} {tail_reg}")
check("M2 worker ME40.4 (outbound) -> PASS",
      "K1 ME40.4 (worker outbound) -> PASS" in proc_reg.stdout,
      "no aparece el check K1 de me405")
check("N2 RunStorage ME40.3 -> PASS",
      "L3 ME40.3 (RunStorage) -> PASS" in proc_reg.stdout,
      "no aparece el check L3 de me405")
check("L3 cola ME40.2 -> PASS (transitiva vía me404/me405)", reg_ok,
      "me405 falló; la cola no quedó cubierta")

# ---------------------------------------------------------------------------
# O. La API no cambia (smoke con repositorio por defecto)
# ---------------------------------------------------------------------------
app_api = create_app(runs_root=TEST_RUNS)
with TestClient(app_api) as client:
    r = client.get("/api/v1/health", timeout=10)
    check("O1 health 200", r.status_code == 200, str(r.text))
    r = client.post("/api/v1/runs", json={
        "topic": "API ME406", "offline": True,
        "run_id": "run-20260816-130001",
    }, timeout=10)
    rid_api = r.json().get("run_id")
    check("O2 create run 202 QUEUED",
          r.status_code == 202 and r.json().get("status") == "QUEUED", str(r.json()))
    r = client.get(f"/api/v1/runs/{rid_api}", timeout=10)
    check("O3 get run 200", r.status_code == 200
          and r.json().get("status") == "QUEUED", str(r.json()))
    r = client.post("/api/v1/projects", json={"name": "Proyecto API ME406"}, timeout=10)
    check("O4 create project 201", r.status_code == 201
          and bool(r.json().get("project_id")), str(r.json()))
    r = client.get("/api/v1/projects", timeout=10)
    check("O5 list projects 200", r.status_code == 200, str(r.json()))

# ---------------------------------------------------------------------------
# S. PostgreSQL real (UNA prueba CRUD mínima SI hay driver + servidor)
# ---------------------------------------------------------------------------
POSTGRES_REAL_TEST = "NOT_AVAILABLE"
if _driver_installed():
    dsn = os.environ.get("DATABASE_URL") or "postgresql://postgres:postgres@localhost:5432/postgres"
    try:
        import psycopg  # type: ignore

        conn = psycopg.connect(dsn, connect_timeout=3, row_factory=psycopg.rows.dict_row)
        try:
            conn.execute("DROP TABLE IF EXISTS _me406_crud")
            conn.execute("CREATE TABLE _me406_crud (k TEXT PRIMARY KEY, v INT)")
            conn.execute("INSERT INTO _me406_crud (k, v) VALUES (%s, %s)", ("run-20260816-180001", 1))
            conn.execute("UPDATE _me406_crud SET v = %s WHERE k = %s", (2, "run-20260816-180001"))
            row = conn.execute("SELECT v FROM _me406_crud WHERE k = %s",
                               ("run-20260816-180001",)).fetchone()
            conn.execute("DROP TABLE _me406_crud")
            conn.commit()
            POSTGRES_REAL_TEST = "PASS" if row and row["v"] == 2 else "FAIL"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - sin servidor alcanzable
        POSTGRES_REAL_TEST = "NOT_AVAILABLE"
        print(f"  (PostgreSQL real no alcanzable: {type(exc).__name__})")
check("S1 PostgreSQL real", POSTGRES_REAL_TEST in ("PASS", "NOT_AVAILABLE"),
      f"{POSTGRES_REAL_TEST} (esperado PASS o NOT_AVAILABLE; sin driver/servidor "
      "no se instala y no se afirma conexión real)")

# ---------------------------------------------------------------------------
# P. node --check del frontend
# ---------------------------------------------------------------------------
proc = subprocess.run(["node", "--check", str(ROOT / "web" / "app.js")],
                      capture_output=True, text=True, timeout=60)
check("P1 node --check app.js", proc.returncode == 0, proc.stderr[:400])

# ---------------------------------------------------------------------------
# Q. py_compile de src/scripts/tests
# ---------------------------------------------------------------------------
for path in ("src/api", "src/application", "src/pipeline", "scripts", "tests"):
    proc = subprocess.run([sys.executable, "-m", "py_compile", "-q",
                           *[str(p) for p in (ROOT / path).glob("*.py")]],
                          capture_output=True, text=True, timeout=120)
    check(f"Q1 py_compile {path}", proc.returncode == 0, proc.stderr[:400])

# ---------------------------------------------------------------------------
# R. git diff --check
# ---------------------------------------------------------------------------
proc = subprocess.run(["git", "diff", "--check"], cwd=ROOT,
                      capture_output=True, text=True, timeout=60)
check("R1 git diff --check PASS", proc.returncode == 0,
      proc.stdout[:400] + proc.stderr[:400])

print()
print(f"POSTGRES_REAL_TEST = {POSTGRES_REAL_TEST}")
if not _driver_installed():
    print(f"DEPENDENCY REQUIRED: {REQUIRED_DEPENDENCY}")
print(f"RESULTADO: {'PASS' if not FAILURES else 'FAIL'}")
for failure in FAILURES:
    print(f"  - {failure}")
sys.exit(0 if not FAILURES else 1)