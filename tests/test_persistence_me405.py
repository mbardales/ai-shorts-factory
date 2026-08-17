# -*- coding: utf-8 -*-
"""ME40.5 - Tests de la abstracción de repositorio de persistencia.

Verifica (offline, sin red, sin Gemini/SD15/Kokoro, sin dependencias nuevas)
que ``ApplicationService`` y la API persisten su metadata (estado de runs,
proyectos, proyecto activo y asociación run→proyecto) **únicamente** a través
del contrato :class:`application.PersistenceRepository`, con la implementación
local :class:`LocalPersistenceRepository` sobre el mecanismo JSON existente.

- El contrato cubre solo lo que el servicio necesita (runs + proyectos); no es
  un CRUD genérico ni duplica modelos (``RunRecord``/``ProjectRecord``).
- ``LocalPersistenceRepository`` reutiliza ``pipeline.lifecycle`` (``run.json``)
  y ``application.projects`` (``projects.json``); no cambia el formato de los
  archivos y es compatible con runs/proyectos ya generados.
- La cola (ME40.2) y el worker (ME40.4) siguen funcionando intactos.
- La API no cambia (respuestas idénticas) y un repositorio alternativo (p. ej.
  uno en memoria o PostgreSQL en el futuro) sustituye al local sin tocar
  ``ApplicationService``.

Checks cubiertos (A-Q):

- A. el repositorio crea y lee un run.
- B. el repositorio actualiza un run.
- C. el repositorio lista los runs.
- D. los proyectos funcionan vía repositorio.
- E. el proyecto activo funciona (crear/activar/limpiar).
- F. la asociación run → proyecto funciona.
- G. compatibilidad con runs/proyectos preexistentes (formato intacto).
- H. ``ApplicationService`` depende del contrato (y un repo alternativo vale).
- I. regresión de la API (runs, proyectos, activo, video).
- J. regresión de la cola (ME40.2).
- K. regresión del worker outbound (ME40.4).
- L. regresión de RunStorage (ME40.3).
- M. regresión de proyectos/historial (filtros y asociación).
- N. regresión de seguridad (traversal/rutas inseguras).
- O. ``node --check``.
- P. ``py_compile``.
- Q. ``git diff --check`` PASS.

Uso (sin framework de test, solo stdlib + fastapi TestClient):

    .venv\\Scripts\\python.exe tests\\test_persistence_me405.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
TEMP_ROOT = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "opencode" / "me405"
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
    LocalPersistenceRepository,
    PersistenceRepository,
)
from application.exceptions import (  # noqa: E402
    ApplicationProjectNotFoundError,
    ApplicationRunNotFoundError,
    ApplicationValidationError,
)
from api.app import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pipeline import (  # noqa: E402
    PipelineValidationError,
    RunRecord,
    RunStatus,
    claim_job,
    find_orphan_jobs,
    find_queued_jobs,
    load_run_record,
    process_one,
    release_job,
    write_run_record,
)

FAILURES: list[str] = []

#: Bytes de un MP4 falso (ftyp dentro de los primeros 64 bytes).
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048

#: Runs creados "antes de ME40.5" (compatibilidad de formato).
PRE_RUN = "run-20260816-090000"


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(f"{label}: {detail}")
    print(f"[{'OK' if cond else 'FAIL'}] {label}" + ("" if cond else f" | {detail}"))


def run_json(run_dir: Path) -> dict:
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


def new_run(service: ApplicationService, run_id: str, *, project_id: Optional[str] = None) -> str:
    return service.create_run(
        CreateRunRequest(topic=f"Tema de {run_id}", offline=True,
                         project_id=project_id, run_id=run_id)
    ).run_id


def make_success_with_video(run_id: str) -> None:
    """Persiste SUCCESS con un video falso (sin pipeline real)."""
    run_dir = TEST_RUNS / run_id
    vdir = run_dir / "output" / "video"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "video.mp4").write_bytes(FAKE_MP4)
    prev = load_run_record(run_dir)
    write_run_record(
        run_dir,
        RunRecord(
            run_id=run_id,
            status=RunStatus.SUCCESS,
            created_at=prev.created_at if prev else None,
            queued_at=prev.queued_at if prev else None,
            started_at=prev.started_at if prev else None,
            finished_at=datetime.now(timezone.utc),
            quality_passed=True,
        ),
    )


class InMemoryPersistenceRepository(PersistenceRepository):
    """Implementación alternativa (en memoria) del contrato: demuestra que
    ``ApplicationService`` no depende del JSON ni del filesystem."""

    def __init__(self, runs_root: Path) -> None:
        self.runs_root = Path(runs_root)
        self._runs: dict[str, RunRecord] = {}
        self._projects: list = []
        self._active: Optional[str] = None
        self._run_projects: dict[str, Optional[str]] = {}
        self._counter = 0

    def save_run(self, run_id: str, record: RunRecord) -> None:
        self._runs[run_id] = record
        (self.runs_root / run_id).mkdir(parents=True, exist_ok=True)

    def load_run(self, run_id: str) -> Optional[RunRecord]:
        return self._runs.get(run_id)

    def run_exists(self, run_id: str) -> bool:
        return run_id in self._runs

    def list_runs(self) -> list[RunRecord]:
        return sorted(
            self._runs.values(), key=lambda r: (r.created_at or r.started_at, r.run_id)
        )

    def create_project(self, name: str, description: Optional[str] = None):
        self._counter += 1
        from application import ProjectRecord

        record = ProjectRecord(
            project_id=f"mem-{self._counter}",
            name=name,
            description=description,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._projects.append(record)
        self._active = record.project_id
        return record

    def list_projects(self):
        return list(self._projects), self._active

    def get_project(self, project_id: str):
        for project in self._projects:
            if project.project_id == project_id:
                return project
        raise ApplicationProjectNotFoundError(f"No existe el proyecto: {project_id}")

    def set_active_project(self, project_id: Optional[str]) -> None:
        self._active = project_id

    def associate_run(self, run_id: str, project_id: Optional[str]) -> None:
        self._run_projects[run_id] = project_id

    def project_id_for_run(self, run_id: str) -> Optional[str]:
        return self._run_projects.get(run_id)


repo = LocalPersistenceRepository(runs_root=TEST_RUNS)
service = ApplicationService(runs_root=TEST_RUNS)
app = create_app(runs_root=TEST_RUNS)

# ---------------------------------------------------------------------------
# A. El repositorio crea y lee un run
# ---------------------------------------------------------------------------
r_a = "run-20260816-100001"
now = datetime.now(timezone.utc)
repo.save_run(r_a, RunRecord(run_id=r_a, status=RunStatus.QUEUED,
                             created_at=now, queued_at=now))
loaded = repo.load_run(r_a)
check("A1 load_run tras save_run", loaded is not None, str(loaded))
check("A2 campos persistidos",
      loaded is not None and loaded.run_id == r_a
      and loaded.status is RunStatus.QUEUED
      and loaded.created_at == now and loaded.queued_at == now, str(loaded))
check("A3 run.json existe (formato intacto)",
      (TEST_RUNS / r_a / "run.json").is_file())
check("A4 run.json con keys del formato",
      {"run_id", "status", "created_at", "queued_at"} <= set(run_json(TEST_RUNS / r_a)))
check("A5 load_run run inexistente -> None", repo.load_run("run-20260816-199999") is None)
check("A6 run_exists True/False",
      repo.run_exists(r_a) is True and repo.run_exists("run-20260816-199999") is False)

# ---------------------------------------------------------------------------
# B. El repositorio actualiza un run
# ---------------------------------------------------------------------------
repo.save_run(r_a, RunRecord(run_id=r_a, status=RunStatus.SUCCESS,
                             created_at=now, queued_at=now, started_at=now,
                             finished_at=now, quality_passed=True))
loaded = repo.load_run(r_a)
check("B1 update a SUCCESS", loaded is not None and loaded.status is RunStatus.SUCCESS, str(loaded))
check("B2 quality_passed True", loaded is not None and loaded.quality_passed is True, str(loaded))
check("B3 finished_at persistido", loaded is not None and loaded.finished_at is not None)
check("B4 sin duplicados (un único run.json)",
      len(list((TEST_RUNS / r_a).glob("run.json"))) == 1)

# ---------------------------------------------------------------------------
# C. El repositorio lista los runs
# ---------------------------------------------------------------------------
r_c = "run-20260816-100002"
repo.save_run(r_c, RunRecord(run_id=r_c, status=RunStatus.QUEUED, created_at=now, queued_at=now))
ids = {record.run_id for record in repo.list_runs()}
check("C1 list_runs incluye ambos runs", r_a in ids and r_c in ids, str(ids))
check("C2 list_runs orden cronológico (por nombre)",
      [r.run_id for r in repo.list_runs()] == sorted([r_a, r_c]))

# ---------------------------------------------------------------------------
# D. Proyectos vía repositorio
# ---------------------------------------------------------------------------
p1 = repo.create_project("Proyecto Alpha")
check("D1 create_project devuelve ProjectRecord", p1.project_id and p1.name == "Proyecto Alpha", str(p1))
projects, active = repo.list_projects()
check("D2 list_projects incluye el proyecto", any(p.project_id == p1.project_id for p in projects), str(projects))
check("D3 activo = el creado", active == p1.project_id, str(active))
check("D4 get_project devuelve el proyecto", repo.get_project(p1.project_id).name == "Proyecto Alpha")
check("D5 projects.json intacto", (TEST_RUNS / "projects.json").is_file())

# ---------------------------------------------------------------------------
# E. Proyecto activo
# ---------------------------------------------------------------------------
p2 = repo.create_project("Proyecto Beta")
_, active = repo.list_projects()
check("E1 crear segundo proyecto lo deja activo", active == p2.project_id, str(active))
repo.set_active_project(p1.project_id)
_, active = repo.list_projects()
check("E2 set_active_project cambia el activo", active == p1.project_id, str(active))
repo.set_active_project(None)
_, active = repo.list_projects()
check("E3 limpiar activo (None)", active is None, str(active))

# ---------------------------------------------------------------------------
# F. Asociación run -> proyecto
# ---------------------------------------------------------------------------
repo.associate_run(r_a, p1.project_id)
check("F1 project_id_for_run devuelve el proyecto",
      repo.project_id_for_run(r_a) == p1.project_id)
repo.associate_run(r_a, None)
check("F2 desasociar -> None", repo.project_id_for_run(r_a) is None)

# ---------------------------------------------------------------------------
# G. Compatibilidad con runs/proyectos preexistentes (antes de ME40.5)
# ---------------------------------------------------------------------------
pre_dir = TEST_RUNS / PRE_RUN
pre_dir.mkdir(parents=True, exist_ok=True)
write_run_record(
    pre_dir,
    RunRecord(run_id=PRE_RUN, status=RunStatus.SUCCESS,
              created_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
              queued_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
              started_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
              finished_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
              quality_passed=True),
)
rec = repo.load_run(PRE_RUN)
check("G1 run preexistente leído por el repositorio",
      rec is not None and rec.status is RunStatus.SUCCESS, str(rec))
check("G2 run preexistente listado", PRE_RUN in {r.run_id for r in repo.list_runs()})
# projects.json creado por la capa anterior también se lee.
from application import projects as _projects  # noqa: E402

p_legacy = _projects.create_project(TEST_RUNS, "Proyecto Legado")
_, active = repo.list_projects()
check("G3 projects.json preexistente leído (activo)", active == p_legacy.project_id, str(active))
check("G4 proyecto legado visible vía repo", repo.get_project(p_legacy.project_id).name == "Proyecto Legado")

# ---------------------------------------------------------------------------
# H. ApplicationService depende del contrato
# ---------------------------------------------------------------------------
check("H1 default usa LocalPersistenceRepository",
      isinstance(service._repository, LocalPersistenceRepository))
check("H2 el repositorio cumple el contrato",
      isinstance(service._repository, PersistenceRepository))
# Un repositorio alternativo (en memoria) vale sin tocar el servicio.
mem_repo = InMemoryPersistenceRepository(TEST_RUNS)
svc_mem = ApplicationService(runs_root=TEST_RUNS, repository=mem_repo)
r_h = new_run(svc_mem, "run-20260816-100003")
check("H3 create_run con repo alternativo -> QUEUED",
      svc_mem.get_run(r_h).status == "QUEUED", svc_mem.get_run(r_h).status)
mem_repo.save_run(r_h, RunRecord(run_id=r_h, status=RunStatus.SUCCESS,
                                 created_at=now, queued_at=now, finished_at=now,
                                 quality_passed=True))
check("H4 get_run refleja estado del repo alternativo",
      svc_mem.get_run(r_h).status == "SUCCESS", svc_mem.get_run(r_h).status)
check("H5 list_runs del repo alternativo", any(x.run_id == r_h for x in svc_mem.list_runs()))
p_mem = svc_mem.create_project(CreateProjectRequest(name="Proyecto Memoria"))
check("H6 proyectos del repo alternativo",
      svc_mem.list_projects().active_project_id == p_mem.project_id)
check("H7 metadata NO se escribe al JSON con repo alternativo",
      not (TEST_RUNS / r_h / "run.json").exists() and repo.load_run(r_h) is None,
      "el repo local escribió metadata del run en memoria")
check("H8 el repo local conserva sus runs tras usar repo alternativo",
      PRE_RUN in {x.run_id for x in repo.list_runs()})

# ---------------------------------------------------------------------------
# I. Regresión de la API (runs, proyectos, activo, video)
# ---------------------------------------------------------------------------
with TestClient(app) as c:
    r = c.post("/api/v1/runs", json={"topic": "Tema API ME405",
                                     "run_id": "run-20260816-100004",
                                     "offline": True}, timeout=30)
    check("I1 POST /runs -> 202 QUEUED",
          r.status_code == 202 and r.json().get("status") == "QUEUED", f"{r.status_code} {r.text}")
    run_api = r.json().get("run_id")
    rr = c.get(f"/api/v1/runs/{run_api}", timeout=10)
    check("I2 GET /runs/{id} -> QUEUED", rr.status_code == 200 and rr.json().get("status") == "QUEUED", str(rr.json()))
    hist = c.get("/api/v1/runs", timeout=10).json()
    check("I3 GET /runs incluye el run", any(x["run_id"] == run_api for x in hist))
    r = c.post("/api/v1/projects", json={"name": "Proyecto API"}, timeout=10)
    pid = r.json().get("project_id")
    check("I4 POST /projects -> 201", r.status_code == 201 and pid, f"{r.status_code} {r.text}")
    check("I5 GET /projects activo", c.get("/api/v1/projects", timeout=10).json().get("active_project_id") == pid)
    r = c.post("/api/v1/projects/active", json={"project_id": None}, timeout=10)
    check("I6 POST /projects/active limpia", r.status_code == 200 and r.json().get("project_id") == "", str(r.json()))
    # run -> SUCCESS con video, y video servible (RunStorage).
    claim_job(TEST_RUNS, run_api)
    make_success_with_video(run_api)
    release_job(TEST_RUNS, run_api)
    rv = c.get(f"/api/v1/runs/{run_api}/video", timeout=10)
    check("I7 GET /runs/{id}/video -> 200",
          rv.status_code == 200 and b"ftyp" in rv.content[:64], f"status={rv.status_code}")
    rr = c.get(f"/api/v1/runs/{run_api}", timeout=10)
    check("I8 video_path relativo de la API",
          rr.json().get("video_path") == f"/api/v1/runs/{run_api}/video", str(rr.json()))

# ---------------------------------------------------------------------------
# J. Regresión de la cola (ME40.2) - en proceso, sin pipeline real
# ---------------------------------------------------------------------------
r_j = new_run(service, "run-20260816-100005")
check("J1 find_queued_jobs ve el run", r_j in {x.run_id for x in find_queued_jobs(TEST_RUNS)})
ctx = claim_job(TEST_RUNS, r_j)
check("J2 claim_job -> RUNNING (atómico)", ctx is not None, str(ctx))
check("J3 segundo claim -> None (dos workers)", claim_job(TEST_RUNS, r_j) is None)
check("J4 claim deja bloqueo", (TEST_RUNS / r_j / ".worker.lock").exists())
release_job(TEST_RUNS, r_j)
check("J5 release_job limpia el bloqueo", not (TEST_RUNS / r_j / ".worker.lock").exists())
r_j2 = new_run(service, "run-20260816-100006")

def fake_success(context) -> None:  # noqa: ANN001
    prev = load_run_record(context.run_dir)
    write_run_record(
        context.run_dir,
        RunRecord(
            run_id=context.run_id,
            status=RunStatus.SUCCESS,
            created_at=prev.created_at if prev else None,
            queued_at=prev.queued_at if prev else None,
            started_at=prev.started_at if prev else None,
            finished_at=datetime.now(timezone.utc),
            quality_passed=True,
        ),
    )


check("J6 process_one procesa un QUEUED",
      process_one(TEST_RUNS, executor=fake_success) is True, "no procesó")
rec_c = load_run_record(TEST_RUNS / r_c)
check("J7 el job más antiguo (r_c) procesado a SUCCESS",
      rec_c is not None and rec_c.status is RunStatus.SUCCESS, str(rec_c))
# El pipeline real se valida con la regresión de ME40.4 (que corre --offline).

# ---------------------------------------------------------------------------
# K. Regresión del worker outbound (ME40.4)
# ---------------------------------------------------------------------------
proc = subprocess.run(
    [sys.executable, str(ROOT / "tests" / "test_worker_me404.py")],
    capture_output=True, text=True, timeout=1800, cwd=ROOT,
)
tail = (proc.stdout + proc.stderr)[-400:]
check("K1 ME40.4 (worker outbound) -> PASS",
      proc.returncode == 0 and "RESULTADO: PASS" in proc.stdout,
      f"rc={proc.returncode} {tail}")

# ---------------------------------------------------------------------------
# L. Regresión de RunStorage (ME40.3) - checks puntuales + suite
# ---------------------------------------------------------------------------
from application import LocalRunStorage  # noqa: E402

storage = LocalRunStorage(runs_root=TEST_RUNS)
check("L1 LocalRunStorage resuelve video del run SUCCESS",
      storage.resolve_video(run_api) is not None)
check("L2 LocalRunStorage ignora run inexistente",
      storage.resolve_video("run-20260816-199999") is None)
proc = subprocess.run(
    [sys.executable, str(ROOT / "tests" / "test_storage_me403.py")],
    capture_output=True, text=True, timeout=1800, cwd=ROOT,
)
tail = (proc.stdout + proc.stderr)[-400:]
check("L3 ME40.3 (RunStorage) -> PASS",
      proc.returncode == 0 and "RESULTADO: PASS" in proc.stdout,
      f"rc={proc.returncode} {tail}")

# ---------------------------------------------------------------------------
# M. Proyectos/historial: filtros y asociación
# ---------------------------------------------------------------------------
with TestClient(app) as c:
    r = c.post("/api/v1/projects", json={"name": "Proyecto Filtro"}, timeout=10)
    pid_f = r.json().get("project_id")
    run_f = new_run(service, "run-20260816-100007", project_id=pid_f)
    hist = c.get(f"/api/v1/runs?project_id={pid_f}", timeout=10).json()
    check("M1 filtro por proyecto", any(x["run_id"] == run_f for x in hist), str(hist))
    rr = c.get(f"/api/v1/runs/{run_f}", timeout=10)
    check("M2 run refleja project_id", rr.json().get("project_id") == pid_f, str(rr.json()))
    hist_all = c.get("/api/v1/runs", timeout=10).json()
    check("M3 historial sin proyecto (none)", run_f in {x["run_id"] for x in hist_all})

# ---------------------------------------------------------------------------
# N. Seguridad: traversal / rutas inseguras
# ---------------------------------------------------------------------------
for bad in ("..", "../x", "C:\\Windows\\system32\\config\\SAM", "/etc/passwd"):
    try:
        repo.run_exists(bad)
        check(f"N1 run_exists rechaza {bad!r}", False, "no lanzó")
    except PipelineValidationError:
        check(f"N1 run_exists rechaza {bad!r}", True)
    try:
        repo.save_run(bad, RunRecord(run_id=bad, status=RunStatus.QUEUED))
        check(f"N2 save_run rechaza {bad!r}", False, "no lanzó")
    except PipelineValidationError:
        check(f"N2 save_run rechaza {bad!r}", True)
with TestClient(app) as c:
    r = c.get("/api/v1/runs/..%2F..%2Fetc%2Fpasswd", timeout=10)
    check("N3 traversal en API -> 4xx", r.status_code in (400, 404, 405), f"status={r.status_code}")
    r = c.get("/api/v1/runs/run-20260816-100004/../../video", timeout=10)
    check("N4 multi-segmento en API -> 4xx", r.status_code in (400, 404, 405), f"status={r.status_code}")

# ---------------------------------------------------------------------------
# O. node --check del frontend
# ---------------------------------------------------------------------------
proc = subprocess.run(["node", "--check", str(ROOT / "web" / "app.js")],
                      capture_output=True, text=True, timeout=60)
check("O1 node --check app.js", proc.returncode == 0, proc.stderr[:400])

# ---------------------------------------------------------------------------
# P. py_compile de src/scripts/tests
# ---------------------------------------------------------------------------
for path in ("src/api", "src/application", "src/pipeline", "scripts", "tests"):
    proc = subprocess.run([sys.executable, "-m", "py_compile", "-q",
                           *[str(p) for p in (ROOT / path).glob("*.py")]],
                          capture_output=True, text=True, timeout=120)
    check(f"P1 py_compile {path}", proc.returncode == 0, proc.stderr[:400])

# ---------------------------------------------------------------------------
# Q. git diff --check
# ---------------------------------------------------------------------------
proc = subprocess.run(["git", "diff", "--check"], cwd=ROOT,
                      capture_output=True, text=True, timeout=60)
check("Q1 git diff --check PASS", proc.returncode == 0,
      proc.stdout[:400] + proc.stderr[:400])

print()
print(f"RESULTADO: {'PASS' if not FAILURES else 'FAIL'}")
for f in FAILURES:
    print(f"  - {f}")
sys.exit(0 if not FAILURES else 1)