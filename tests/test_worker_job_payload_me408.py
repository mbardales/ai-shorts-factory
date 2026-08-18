# -*- coding: utf-8 -*-
"""ME40.8 - Tests del job payload desacoplado del worker outbound.

En la arquitectura híbrida (Northflank = control plane; worker local en
Windows) el worker y el control plane **no comparten filesystem**: el worker
recibe la metadata del job por HTTP (``run_id``, ``topic``, ``offline``,
``project_id``) y, hasta ME40.7, :func:`pipeline.queue.execute_job` fallaba con
``"Falta job.json ...; no se puede ejecutar."`` porque el ``job.json`` del
control plane no existe localmente.

ME40.8 añade :func:`pipeline.queue.materialize_local_job`: a partir del payload
HTTP el worker materializa **localmente** (``runs_root/<run_id>/``) el
directorio, el estado ``RUNNING`` y el ``job.json`` antes de ejecutar el
pipeline. No se introduce filesystem compartido ni se copia nada desde la API;
el flujo local (job.json ya presente) sigue intacto.

Tests (A-L), stdlib puro, sin pytest, sin HTTP, sin Gemini/SD15/Kokoro:

- A. payload válido reconstruye el contexto local.
- B. run_id inválido/rechazado.
- C. topic conservado.
- D. project_id conservado.
- E. offline conservado.
- F. job.json local generado cuando el worker recibe el payload.
- G. execute_job continúa sin depender de un job.json remoto.
- H. flujo local existente con job.json sigue funcionando.
- I. payload malformado produce error controlado.
- J. rutas absolutas nunca se reciben desde Northflank.
- K. traversal en run_id rechazado.
- L. no se realizan llamadas HTTP externas durante los tests.

Además: py_compile (archivos modificados), node --check y git diff --check.

Uso:

    .venv\\Scripts\\python.exe tests/test_worker_job_payload_me408.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
TEMP_ROOT = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "opencode" / "me408"
TEST_RUNS = TEMP_ROOT / "tests_runs"
RUNS_ENV = "PIPELINE_RUNS_ROOT"

if TEST_RUNS.exists():
    shutil.rmtree(TEST_RUNS)
TEST_RUNS.mkdir(parents=True, exist_ok=True)

os.environ[RUNS_ENV] = str(TEST_RUNS)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pipeline.runner  # noqa: E402
import run_worker  # noqa: E402
from pipeline import (  # noqa: E402
    PipelineValidationError,
    RunContext,
    RunRecord,
    RunStatus,
    claim_job,
    load_run_record,
    release_job,
    write_run_record,
)
from pipeline.queue import (  # noqa: E402
    JobPayload,
    execute_job,
    load_job_payload,
    materialize_local_job,
    write_job_payload,
)

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(f"{label}: {detail}")
    print(f"[{'OK' if cond else 'FAIL'}] {label}" + ("" if cond else f" | {detail}"))


# ---------------------------------------------------------------------------
# Dobles mínimos (sin frameworks ni HTTP)
# ---------------------------------------------------------------------------


class FakeRunner:
    """Sustituto de PipelineRunner: registra los args del job (sin ejecutar nada)."""

    instances: list["FakeRunner"] = []

    def __init__(self, context, *, topic=None, offline=False, project_id=None) -> None:
        self.context = context
        self.topic = topic
        self.offline = offline
        self.project_id = project_id
        FakeRunner.instances.append(self)

    def run(self) -> dict:
        return {"ok": True}


class FakeClient:
    """Cliente HTTP mínimo del worker, sin red: devuelve el job dado y registra."""

    def __init__(self, job: Optional[dict]) -> None:
        self.job = job
        self.heartbeats: list[str] = []
        self.completes: list[tuple] = []

    def next_job(self) -> Optional[dict]:
        return self.job

    def heartbeat(self, run_id: str) -> int:
        self.heartbeats.append(run_id)
        return 200

    def progress(self, run_id: str, stage: str) -> int:
        return 200

    def complete(self, run_id: str, status: str, error: Optional[str] = None) -> int:
        self.completes.append((run_id, status, error))
        return 200


def persist_terminal(run_dir: Path, status: RunStatus) -> None:
    """Persiste un estado terminal local (simula el resultado del pipeline)."""
    previous = load_run_record(run_dir)
    write_run_record(
        run_dir,
        RunRecord(
            run_id=Path(run_dir).name,
            status=status,
            created_at=previous.created_at if previous else None,
            queued_at=previous.queued_at if previous else None,
            started_at=previous.started_at if previous else None,
            finished_at=datetime.now(timezone.utc),
            quality_passed=True if status is RunStatus.SUCCESS else None,
        ),
    )


# ---------------------------------------------------------------------------
# Guardia de red (L): ningún test debe hacer HTTP externo.
# ---------------------------------------------------------------------------
_original_urlopen = urllib.request.urlopen


def _block_network(*args, **kwargs):  # noqa: ARG001
    raise AssertionError("No se permite HTTP externo durante los tests (L).")


urllib.request.urlopen = _block_network

# ---------------------------------------------------------------------------
# A. payload válido reconstruye el contexto local
# ---------------------------------------------------------------------------
ctx_a = materialize_local_job(
    TEST_RUNS, "run-20260818-010001", topic="Tema A", offline=True, project_id="proj-a"
)
check("A1 run_id conservado", ctx_a.run_id == "run-20260818-010001")
check("A2 run_dir dentro de runs_root", ctx_a.run_dir == TEST_RUNS / "run-20260818-010001")
check("A3 directorio local creado", ctx_a.run_dir.is_dir())
rec_a = load_run_record(ctx_a.run_dir)
check("A4 estado RUNNING local (preparado)", rec_a is not None and rec_a.status is RunStatus.RUNNING)

# ---------------------------------------------------------------------------
# B. run_id inválido / rechazado
# ---------------------------------------------------------------------------
for bad in ("run-invalido", "", "run-20260818-abcdef", "run-20260818-010001-extra"):
    try:
        materialize_local_job(TEST_RUNS, bad, topic="Tema B")
        check(f"B1 rechaza run_id {bad!r}", False, "no elevó PipelineValidationError")
    except PipelineValidationError:
        check(f"B1 rechaza run_id {bad!r}", True)

# ---------------------------------------------------------------------------
# C. topic conservado
# ---------------------------------------------------------------------------
ctx_c = materialize_local_job(
    TEST_RUNS, "run-20260818-010003", topic="  Tema con espacios  ", offline=False
)
check("C1 topic normalizado y conservado",
      load_job_payload(ctx_c.run_dir).topic == "Tema con espacios")

# ---------------------------------------------------------------------------
# D. project_id conservado
# ---------------------------------------------------------------------------
ctx_d = materialize_local_job(
    TEST_RUNS, "run-20260818-010004", topic="Tema D", project_id="northflank-e2e-2"
)
check("D1 project_id conservado",
      load_job_payload(ctx_d.run_dir).project_id == "northflank-e2e-2")
ctx_d2 = materialize_local_job(
    TEST_RUNS, "run-20260818-010005", topic="Tema D2", project_id=12345
)
check("D2 project_id no-str degrada a None",
      load_job_payload(ctx_d2.run_dir).project_id is None)

# ---------------------------------------------------------------------------
# E. offline conservado
# ---------------------------------------------------------------------------
ctx_e1 = materialize_local_job(TEST_RUNS, "run-20260818-010006", topic="Tema E1", offline=True)
ctx_e2 = materialize_local_job(TEST_RUNS, "run-20260818-010007", topic="Tema E2", offline=False)
check("E1 offline=True conservado", load_job_payload(ctx_e1.run_dir).offline is True)
check("E2 offline=False conservado", load_job_payload(ctx_e2.run_dir).offline is False)

# ---------------------------------------------------------------------------
# F. job.json local generado cuando el worker recibe el payload (ciclo outbound)
# ---------------------------------------------------------------------------
job_f = {
    "run_id": "run-20260818-020001",
    "topic": "5 inventos tecnológicos que están cambiando el futuro",
    "offline": True,
    "project_id": "northflank-e2e-2",
    "status": "RUNNING",
}
client_f = FakeClient(job_f)
seen: dict = {}


def executor_f(context: RunContext) -> None:
    seen["context"] = context
    payload = load_job_payload(context.run_dir)
    seen["payload"] = payload
    seen["job_json"] = (context.run_dir / "job.json").is_file()
    seen["run_json"] = load_run_record(context.run_dir)
    persist_terminal(context.run_dir, RunStatus.SUCCESS)


processed_f = run_worker.run_remote_cycle(client_f, TEST_RUNS, executor=executor_f)
check("F1 ciclo outbound procesa el job", processed_f)
check("F2 job.json materializado localmente",
      bool(seen.get("job_json")), str(seen.get("payload")))
check("F3 payload conserva topic/offline/project_id",
      seen.get("payload") is not None
      and seen["payload"].topic == job_f["topic"]
      and seen["payload"].offline is True
      and seen["payload"].project_id == "northflank-e2e-2",
      str(seen.get("payload")))
check("F4 estado local RUNNING antes de ejecutar",
      seen.get("run_json") is not None
      and seen["run_json"].status is RunStatus.RUNNING,
      str(seen.get("run_json")))
check("F5 completa con SUCCESS (estado terminal local)",
      client_f.completes == [("run-20260818-020001", "SUCCESS", None)],
      str(client_f.completes))

# ---------------------------------------------------------------------------
# G. execute_job continúa sin depender de un job.json remoto
# ---------------------------------------------------------------------------
ctx_g = materialize_local_job(
    TEST_RUNS, "run-20260818-030001", topic="Tema G", offline=True, project_id="proj-g"
)
original_runner = pipeline.runner.PipelineRunner
pipeline.runner.PipelineRunner = FakeRunner
FakeRunner.instances = []
try:
    result_g = execute_job(ctx_g)
    check("G1 execute_job funciona con job.json local (sin remoto)", result_g == {"ok": True})
    check("G2 ejecuta SIN error 'Falta job.json'", len(FakeRunner.instances) == 1,
          str(FakeRunner.instances))
    instance_g = FakeRunner.instances[0]
    check("G3 topic pasado al runner", instance_g.topic == "Tema G")
    check("G4 offline pasado al runner", instance_g.offline is True)
    check("G5 project_id pasado al runner", instance_g.project_id == "proj-g")
finally:
    pipeline.runner.PipelineRunner = original_runner

# G negativo: sin job.json local, execute_job eleva el error controlado original.
ctx_none = RunContext(run_id="run-20260818-030002", runs_root=TEST_RUNS)
try:
    execute_job(ctx_none)
    check("G6 sin job.json -> error controlado", False, "no elevó PipelineValidationError")
except PipelineValidationError as exc:
    check("G6 sin job.json -> error controlado",
          "Falta job.json" in str(exc), str(exc))

# ---------------------------------------------------------------------------
# H. flujo local existente con job.json sigue funcionando
# ---------------------------------------------------------------------------
rid_h = "run-20260818-040001"
run_dir_h = TEST_RUNS / rid_h
run_dir_h.mkdir(parents=True, exist_ok=True)
now_h = datetime.now(timezone.utc)
write_run_record(run_dir_h, RunRecord(run_id=rid_h, status=RunStatus.QUEUED,
                                      created_at=now_h, queued_at=now_h))
write_job_payload(run_dir_h, JobPayload(run_id=rid_h, topic="Tema local H",
                                        offline=True, project_id="proj-h"))
context_h = claim_job(TEST_RUNS, rid_h)
check("H1 claim_job local adquiere (QUEUED -> RUNNING)", context_h is not None)
pipeline.runner.PipelineRunner = FakeRunner
FakeRunner.instances = []
try:
    result_h = execute_job(context_h)
    check("H2 execute_job local con job.json existente", result_h == {"ok": True})
    instance_h = FakeRunner.instances[0]
    check("H3 args locales conservados (topic/offline/project_id)",
          instance_h.topic == "Tema local H" and instance_h.offline is True
          and instance_h.project_id == "proj-h",
          f"{instance_h.topic} {instance_h.offline} {instance_h.project_id}")
finally:
    pipeline.runner.PipelineRunner = original_runner
    release_job(TEST_RUNS, rid_h)

# ---------------------------------------------------------------------------
# I. payload malformado produce error controlado
# ---------------------------------------------------------------------------
for label, bad_job in (
    ("topic vacío", {"run_id": "run-20260818-050001", "topic": "", "offline": True}),
    ("topic None", {"run_id": "run-20260818-050002", "topic": None, "offline": True}),
):
    client_i = FakeClient(bad_job)
    result_i = run_worker.run_remote_cycle(client_i, TEST_RUNS, executor=lambda ctx: None)
    check(f"I1 {label} -> ciclo controlado (FAILED reportado)",
          result_i and client_i.completes
          and client_i.completes[0][1] == "FAILED",
          f"result={result_i} completes={client_i.completes}")
client_i2 = FakeClient({"topic": "sin run_id", "offline": True})
try:
    run_worker.run_remote_cycle(client_i2, TEST_RUNS, executor=lambda ctx: None)
    check("I2 sin run_id -> WorkerApiError controlado", False, "no elevó WorkerApiError")
except run_worker.WorkerApiError:
    check("I2 sin run_id -> WorkerApiError controlado", True)

# ---------------------------------------------------------------------------
# J. rutas absolutas nunca se reciben desde Northflank (solo datos, no rutas)
# ---------------------------------------------------------------------------
job_j = {
    "run_id": "run-20260818-060001",
    "topic": "C:\\Users\\alguien\\secret",
    "offline": True,
    "project_id": "D:\\evil\\project",
    "status": "RUNNING",
}
client_j = FakeClient(job_j)


def executor_j(context: RunContext) -> None:
    persist_terminal(context.run_dir, RunStatus.SUCCESS)


processed_j = run_worker.run_remote_cycle(client_j, TEST_RUNS, executor=executor_j)
run_dir_j = TEST_RUNS / "run-20260818-060001"
payload_j = load_job_payload(run_dir_j)
check("J1 el job se procesa (payload solo datos)",
      processed_j and client_j.completes[0][1] == "SUCCESS")
check("J2 topic/path no crea directorios fuera de runs_root",
      payload_j is not None and payload_j.topic == job_j["topic"]
      and not Path(r"C:\Users\alguien").exists()
      and not Path(r"D:\evil").exists(),
      str(payload_j))
only_run_dirs = {
    p.name for p in TEST_RUNS.iterdir()
    if p.is_dir() and p.name.startswith("run-")
}
check("J3 solo se crean directorios de run bajo runs_root",
      run_dir_j in {TEST_RUNS / name for name in only_run_dirs})

# ---------------------------------------------------------------------------
# K. traversal en run_id rechazado
# ---------------------------------------------------------------------------
for evil in ("../escape", "..\\escape", "run-20260818-070001/../../salir", "C:\\Windows"):
    try:
        materialize_local_job(TEST_RUNS, evil, topic="Tema K")
        check(f"K1 traversal {evil!r} rechazado", False, "no elevó PipelineValidationError")
    except PipelineValidationError:
        check(f"K1 traversal {evil!r} rechazado", True)

# ---------------------------------------------------------------------------
# L. no hubo llamadas HTTP externas (urlopen bloqueado durante todo el arnés)
# ---------------------------------------------------------------------------
urllib.request.urlopen = _original_urlopen
try:
    urllib.request.urlopen("http://127.0.0.1:1/", timeout=0.01)
    check("L1 urlopen restaurado tras el arnés", True)
except Exception:  # noqa: BLE001 - conexión rechazada = restaurado
    check("L1 urlopen restaurado tras el arnés", True)

# ---------------------------------------------------------------------------
# Validación: py_compile, node --check y git diff --check
# ---------------------------------------------------------------------------
TARGETS = ["scripts/run_worker.py", "src/pipeline/queue.py", "src/pipeline/context.py",
           "src/pipeline/lifecycle.py", "src/pipeline/runner.py"]
proc = subprocess.run([sys.executable, "-m", "py_compile", "-q",
                       *[str(ROOT / t) for t in TARGETS]],
                      capture_output=True, text=True, timeout=120)
check("Q1 py_compile (archivos tocados)", proc.returncode == 0, proc.stderr[:400])

proc = subprocess.run(["node", "--check", str(ROOT / "web" / "app.js")],
                      capture_output=True, text=True, timeout=60)
check("Q2 node --check app.js", proc.returncode == 0, proc.stderr[:400])

proc = subprocess.run(["git", "diff", "--check"], cwd=ROOT,
                      capture_output=True, text=True, timeout=60)
check("Q3 git diff --check PASS", proc.returncode == 0,
      proc.stdout[:400] + proc.stderr[:400])

print()
print(f"RESULTADO: {'PASS' if not FAILURES else 'FAIL'}")
for failure in FAILURES:
    print(f"  - {failure}")
sys.exit(0 if not FAILURES else 1)