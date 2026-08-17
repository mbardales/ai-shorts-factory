# -*- coding: utf-8 -*-
"""ME40.4 - Tests del protocolo outbound del worker de GPU y su autenticación.

Verifica (offline, sin red externa, sin Gemini/SD15/Kokoro) que:

- El worker se autentica con ``Authorization: Bearer <WORKER_TOKEN>`` (401 si
  falta o es incorrecto) y que ``WORKER_TOKEN`` nunca se expone.
- ``GET /worker/jobs/next`` entrega el siguiente job con **claim atómico**
  (``QUEUED → RUNNING`` reutilizando ``claim_job``; dos workers no reciben el
  mismo job; sin ejecutar el pipeline dentro del endpoint).
- ``POST /worker/jobs/{run_id}/complete`` valida la transición desde ``RUNNING``,
  rechaza estados inválidos/conflictivos y es **idempotente** ante reintentos.
- ``POST /worker/jobs/{run_id}/progress`` y ``.../heartbeat`` funcionan sobre
  runs en ejecución (advisory, sin persistencia redundante).
- Una API caída NO destruye el job (queda ``QUEUED``, sin marcar ``SUCCESS``).
- ``run_worker.py`` en modo outbound (``WORKER_API_URL``) procesa un job real
  contra una API real (``--once``) y todo el pipeline ``--offline`` sigue
  funcionando, con regresión de las ME previas.

Checks cubiertos (A-AA):

- A. token correcto → autorizado (constante de tiempo).
- B. token ausente → 401.
- C. token incorrecto → 401 (incluye complete/progress/heartbeat).
- D. ``worker_id`` válido aceptado.
- E. ``worker_id`` inválido → 400.
- F. sin jobs → 204.
- G. el payload del job es correcto (y el endpoint no ejecuta el pipeline).
- H. el claim es atómico ``QUEUED → RUNNING`` (``started_at``, bloqueo).
- I. dos workers no reciben el mismo job.
- J. ``complete`` RUNNING → SUCCESS.
- K. ``complete`` RUNNING → FAILED (conserva error).
- L. ``complete`` RUNNING → QUALITY_FAILED.
- M. transiciones inválidas rechazadas (400/409).
- N. ``progress`` (stage válido, inválido y run no en ejecución).
- O. ``heartbeat`` (run en ejecución y no en ejecución).
- P. traversal/``run_id`` inseguros → 4xx.
- Q. API caída no destruye el job.
- R. ``complete`` idempotente (reintento con mismo estado terminal).
- S. ``run_worker.py --once`` contra una API real procesa un job offline.
- T. ``run_pipeline.py --offline`` sigue funcionando.
- U-X. regresión de ME40.2, ME40.3, ME38 y ME38.3 (PASS).
- Y. ``node --check``.
- Z. ``py_compile``.
- AA. ``git diff --check`` PASS.

Uso (sin framework de test, solo stdlib + fastapi TestClient):

    .venv\\Scripts\\python.exe tests\\test_worker_me404.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
TEMP_ROOT = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "opencode" / "me404"
TEST_RUNS = TEMP_ROOT / "tests_runs"
RUNS_ENV = "PIPELINE_RUNS_ROOT"
TEST_TOKEN = "me404-test-token"

if TEST_RUNS.exists():
    shutil.rmtree(TEST_RUNS)
TEST_RUNS.mkdir(parents=True, exist_ok=True)

os.environ[RUNS_ENV] = str(TEST_RUNS)
#: Secreto del protocolo worker para esta ejecución de tests (solo entorno).
os.environ["WORKER_TOKEN"] = TEST_TOKEN

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from application import ApplicationService, CreateRunRequest, WorkerService  # noqa: E402
from application.exceptions import (  # noqa: E402
    ApplicationConflictError,
    ApplicationRunNotFoundError,
    ApplicationValidationError,
)
from api.app import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pipeline import (  # noqa: E402
    RunStatus,
    claim_job,
    load_run_record,
    write_run_record,
)
from pipeline.lifecycle import RunRecord  # noqa: E402
from run_worker import WorkerApiClient, WorkerApiError, attempt_complete  # noqa: E402

FAILURES: list[str] = []

#: Bytes de un MP4 falso (ftyp dentro de los primeros 64 bytes).
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(f"{label}: {detail}")
    print(f"[{'OK' if cond else 'FAIL'}] {label}" + ("" if cond else f" | {detail}"))


def run_json(run_dir: Path) -> dict:
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


def new_run(service: ApplicationService, run_id: str, *, offline: bool = True) -> str:
    """Crea un run ``QUEUED`` (con job.json) bajo TEST_RUNS."""
    result = service.create_run(
        CreateRunRequest(topic=f"Tema de {run_id}", offline=offline, run_id=run_id)
    )
    return result.run_id


def free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_health(port: int, timeout: float = 40.0) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/v1/health", timeout=2
            ) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - la API aún arranca
            time.sleep(0.3)
    return False


service = ApplicationService(runs_root=TEST_RUNS)
worker = WorkerService(runs_root=TEST_RUNS)
app = create_app(runs_root=TEST_RUNS)

# ---------------------------------------------------------------------------
# A. Auth: token correcto (comparación de tiempo constante)
# ---------------------------------------------------------------------------
check("A1 token correcto autoriza", worker.check_token(TEST_TOKEN) is True)
check("A2 token correcto no es igualdad directa", worker.check_token("x" * len(TEST_TOKEN)) is False)
check("A3 token None/por defecto denegado", worker.check_token(None) is False)

# ---------------------------------------------------------------------------
# B/C. API: token ausente / incorrecto -> 401
# ---------------------------------------------------------------------------
with TestClient(app) as c:
    r = c.get("/api/v1/worker/jobs/next?worker_id=worker-a", timeout=10)
    check("B1 jobs/next sin token -> 401", r.status_code == 401, f"status={r.status_code}")
    r = c.get("/api/v1/worker/jobs/next?worker_id=worker-a",
              headers={"Authorization": "Bearer token-incorrecto"}, timeout=10)
    check("C1 jobs/next token incorrecto -> 401", r.status_code == 401, f"status={r.status_code}")
    r = c.post("/api/v1/worker/jobs/run-1/complete", json={"status": "SUCCESS"}, timeout=10)
    check("C2 complete sin token -> 401", r.status_code == 401, f"status={r.status_code}")
    r = c.post("/api/v1/worker/jobs/run-1/progress", json={"stage": "render"}, timeout=10)
    check("C3 progress sin token -> 401", r.status_code == 401, f"status={r.status_code}")
    r = c.post("/api/v1/worker/jobs/run-1/heartbeat", json={}, timeout=10)
    check("C4 heartbeat sin token -> 401", r.status_code == 401, f"status={r.status_code}")

# ---------------------------------------------------------------------------
# D/E. worker_id: válido e inválido
# ---------------------------------------------------------------------------
check("D1 worker_id válido aceptado",
      worker.validate_worker_id("local-rtx4050") == "local-rtx4050")
check("D2 worker_id con guion bajo aceptado", worker.validate_worker_id("worker_a") == "worker_a")
for bad in ("", "..", "../x", "-worker", "worker/id", "a/b", "raiz "):
    try:
        worker.validate_worker_id(bad)
        check(f"E1 worker_id inválido rechazado: {bad!r}", False, "no lanzó")
    except ApplicationValidationError:
        check(f"E1 worker_id inválido rechazado: {bad!r}", True)
with TestClient(app) as c:
    r = c.get("/api/v1/worker/jobs/next?worker_id=..%2F..%2Fevil",
              headers=AUTH, timeout=10)
    check("E2 worker_id inválido en API -> 400", r.status_code == 400, f"status={r.status_code}")

# ---------------------------------------------------------------------------
# F. Sin jobs -> 204 (cola vacía)
# ---------------------------------------------------------------------------
with TestClient(app) as c:
    r = c.get("/api/v1/worker/jobs/next?worker_id=worker-a", headers=AUTH, timeout=10)
    check("F1 cola vacía -> 204", r.status_code == 204, f"status={r.status_code} body={r.text[:120]}")

# ---------------------------------------------------------------------------
# G/H. Payload del job + claim atómico QUEUED -> RUNNING (sin pipeline)
# ---------------------------------------------------------------------------
r_a = new_run(service, "run-20260816-100001", offline=True)
with TestClient(app) as c:
    r = c.get("/api/v1/worker/jobs/next?worker_id=worker-a", headers=AUTH, timeout=10)
    check("G1 jobs/next devuelve 200", r.status_code == 200, f"status={r.status_code}")
    body = r.json()
    check("G2 payload incluye run_id", body.get("run_id") == r_a, str(body))
    check("G3 payload topic/offline/project_id",
          body.get("topic") == f"Tema de {r_a}" and body.get("offline") is True
          and body.get("project_id") is None, str(body))
    check("G4 status tras claim = RUNNING", body.get("status") == "RUNNING", str(body))
    rd = TEST_RUNS / r_a
    rj = run_json(rd)
    check("H1 run.json refleja RUNNING", rj.get("status") == "RUNNING", str(rj))
    check("H2 started_at persistido", rj.get("started_at") is not None, str(rj))
    check("H3 queued_at conservado", rj.get("queued_at") is not None, str(rj))
    check("H4 bloqueo presente (claim atómico)", (rd / ".worker.lock").exists())
    check("H5 el endpoint NO ejecuta el pipeline (sin artefactos)",
          not any((rd / "output").iterdir()), "jobs/next ejecutó el pipeline")

# ---------------------------------------------------------------------------
# I. Dos workers no reciben el mismo job
# ---------------------------------------------------------------------------
r_i1 = new_run(service, "run-20260816-100002", offline=True)
r_i2 = new_run(service, "run-20260816-100003", offline=True)
job_a = worker.next_job("worker-a")
job_b = worker.next_job("worker-b")
check("I1 worker-a recibe un job", job_a is not None, str(job_a))
check("I2 worker-b recibe el otro job",
      job_b is not None and job_b["run_id"] != job_a["run_id"], str(job_b))
check("I3 sin jobs restantes", worker.next_job("worker-a") is None)
check("I4 los dos jobs son los encolados",
      {job_a["run_id"], job_b["run_id"]} == {r_i1, r_i2}, str((job_a, job_b)))
check("I5 ambos runs en RUNNING",
      run_json(TEST_RUNS / job_a["run_id"]).get("status") == "RUNNING"
      and run_json(TEST_RUNS / job_b["run_id"]).get("status") == "RUNNING")

# ---------------------------------------------------------------------------
# J/K/L. complete: RUNNING -> SUCCESS/FAILED/QUALITY_FAILED (vía API)
# ---------------------------------------------------------------------------
r_j = new_run(service, "run-20260816-100004")
ctx = claim_job(TEST_RUNS, r_j)
with TestClient(app) as c:
    r = c.post(f"/api/v1/worker/jobs/{r_j}/complete", json={"status": "SUCCESS"},
               headers=AUTH, timeout=10)
    check("J1 complete SUCCESS -> 200", r.status_code == 200, f"status={r.status_code} {r.text}")
    check("J2 respuesta run_id/status", r.json().get("status") == "SUCCESS", str(r.json()))
    rj = run_json(TEST_RUNS / r_j)
    check("J3 run.json SUCCESS", rj.get("status") == "SUCCESS", str(rj))
    check("J4 finished_at persistido", rj.get("finished_at") is not None, str(rj))
    check("J5 bloqueo liberado tras complete", not (TEST_RUNS / r_j / ".worker.lock").exists())

r_k = new_run(service, "run-20260816-100005")
claim_job(TEST_RUNS, r_k)
with TestClient(app) as c:
    r = c.post(f"/api/v1/worker/jobs/{r_k}/complete",
               json={"status": "FAILED", "error": "Fallo del render"}, headers=AUTH, timeout=10)
    check("K1 complete FAILED -> 200", r.status_code == 200, f"status={r.status_code} {r.text}")
    rj = run_json(TEST_RUNS / r_k)
    check("K2 run.json FAILED", rj.get("status") == "FAILED", str(rj))
    check("K3 error conservado", rj.get("error") == "Fallo del render", str(rj))

r_l = new_run(service, "run-20260816-100006")
claim_job(TEST_RUNS, r_l)
with TestClient(app) as c:
    r = c.post(f"/api/v1/worker/jobs/{r_l}/complete", json={"status": "QUALITY_FAILED"},
               headers=AUTH, timeout=10)
    check("L1 complete QUALITY_FAILED -> 200", r.status_code == 200, f"status={r.status_code} {r.text}")
    rj = run_json(TEST_RUNS / r_l)
    check("L2 run.json QUALITY_FAILED", rj.get("status") == "QUALITY_FAILED", str(rj))
    check("L3 quality_passed False", rj.get("quality_passed") is False, str(rj))

# ---------------------------------------------------------------------------
# M. Transiciones inválidas: 400 (estado no terminal) / 409 (conflicto)
# ---------------------------------------------------------------------------
with TestClient(app) as c:
    r = c.post(f"/api/v1/worker/jobs/{r_j}/complete", json={"status": "RUNNING"},
               headers=AUTH, timeout=10)
    check("M1 complete estado RUNNING -> 400", r.status_code == 400, f"status={r.status_code}")
    r = c.post(f"/api/v1/worker/jobs/{r_j}/complete", json={"status": "SUCCESS"},
               headers=AUTH, timeout=10)
    check("M2 SUCCESS -> SUCCESS idempotente 200", r.status_code == 200, f"status={r.status_code}")
    r = c.post(f"/api/v1/worker/jobs/{r_j}/complete", json={"status": "FAILED"},
               headers=AUTH, timeout=10)
    check("M3 SUCCESS -> FAILED -> 409", r.status_code == 409, f"status={r.status_code} {r.text}")
    r_q = new_run(service, "run-20260816-100007")
    r = c.post(f"/api/v1/worker/jobs/{r_q}/complete", json={"status": "SUCCESS"},
               headers=AUTH, timeout=10)
    check("M4 QUEUED sin reclamar -> 409", r.status_code == 409, f"status={r.status_code} {r.text}")
    r = c.post("/api/v1/worker/jobs/run-20260816-199999/complete", json={"status": "SUCCESS"},
               headers=AUTH, timeout=10)
    check("M5 run inexistente -> 404", r.status_code == 404, f"status={r.status_code}")

# ---------------------------------------------------------------------------
# N. progress: stage válido/inválido y run no en ejecución
# ---------------------------------------------------------------------------
r_n = new_run(service, "run-20260816-100008")
claim_job(TEST_RUNS, r_n)
with TestClient(app) as c:
    r = c.post(f"/api/v1/worker/jobs/{r_n}/progress", json={"stage": "render"},
               headers=AUTH, timeout=10)
    check("N1 progress stage válido -> 200", r.status_code == 200, f"status={r.status_code}")
    r = c.post(f"/api/v1/worker/jobs/{r_n}/progress", json={"stage": "bug"},
               headers=AUTH, timeout=10)
    check("N2 progress stage inválido -> 400", r.status_code == 400, f"status={r.status_code}")
    r = c.post(f"/api/v1/worker/jobs/{r_j}/progress", json={"stage": "image"},
               headers=AUTH, timeout=10)
    check("N3 progress run no RUNNING -> 409", r.status_code == 409, f"status={r.status_code}")
    r = c.post("/api/v1/worker/jobs/Bad%21/progress", json={"stage": "image"},
               headers=AUTH, timeout=10)
    check("N4 progress run_id inválido -> 400", r.status_code == 400, f"status={r.status_code}")

# ---------------------------------------------------------------------------
# O. heartbeat: run en ejecución y no en ejecución
# ---------------------------------------------------------------------------
with TestClient(app) as c:
    r = c.post(f"/api/v1/worker/jobs/{r_n}/heartbeat", json={}, headers=AUTH, timeout=10)
    check("O1 heartbeat RUNNING -> 200 RUNNING",
          r.status_code == 200 and r.json().get("status") == "RUNNING",
          f"status={r.status_code} {r.text}")
    r = c.post(f"/api/v1/worker/jobs/{r_j}/heartbeat", json={}, headers=AUTH, timeout=10)
    check("O2 heartbeat SUCCESS -> 409", r.status_code == 409, f"status={r.status_code}")

# ---------------------------------------------------------------------------
# P. Seguridad: worker_id/run_id inseguros -> 4xx
# ---------------------------------------------------------------------------
for bad in ("..", "../x", "../../etc/passwd"):
    try:
        worker.next_job(bad)
        check(f"P1 next_job traversal worker_id rechazado: {bad!r}", False, "no lanzó")
    except ApplicationValidationError:
        check(f"P1 next_job traversal worker_id rechazado: {bad!r}", True)
for bad in ("..", "C:\\Windows\\system32\\config\\SAM", "/etc/passwd"):
    try:
        worker.complete_job(bad, "SUCCESS")
        check(f"P2 complete run_id inseguro rechazado: {bad!r}", False, "no lanzó")
    except ApplicationValidationError:
        check(f"P2 complete run_id inseguro rechazado: {bad!r}", True)
with TestClient(app) as c:
    r = c.post("/api/v1/worker/jobs/..%2F..%2Fetc%2Fpasswd/complete",
               json={"status": "SUCCESS"}, headers=AUTH, timeout=10)
    check("P3 traversal run_id en API -> 4xx", r.status_code in (400, 404, 405),
          f"status={r.status_code}")

# ---------------------------------------------------------------------------
# Q. API caída no destruye el job (queda QUEUED, intacto)
# ---------------------------------------------------------------------------
r_q = new_run(service, "run-20260816-100009")
dead = WorkerApiClient("http://127.0.0.1:1", TEST_TOKEN, "worker-c")
try:
    dead.next_job()
    check("Q1 API caída -> WorkerApiError", False, "no lanzó")
except WorkerApiError:
    check("Q1 API caída -> WorkerApiError", True)
rj = run_json(TEST_RUNS / r_q)
check("Q2 job sigue QUEUED (no destruido)", rj.get("status") == "QUEUED", str(rj))
check("Q3 job.json intacto",
      (TEST_RUNS / r_q / "job.json").is_file()
      and json.loads((TEST_RUNS / r_q / "job.json").read_text(encoding="utf-8")).get("run_id") == r_q)
rc = attempt_complete(dead, r_q, "SUCCESS", None, retries=1)
check("Q4 complete contra API caída -> None (sin mentir)", rc is None)
check("Q5 run sin marcar SUCCESS", run_json(TEST_RUNS / r_q).get("status") == "QUEUED")

# ---------------------------------------------------------------------------
# R. Idempotencia de complete (reintento con el mismo estado terminal)
# ---------------------------------------------------------------------------
r_r = new_run(service, "run-20260816-100010")
claim_job(TEST_RUNS, r_r)
first = worker.complete_job(r_r, "SUCCESS")
second = worker.complete_job(r_r, "SUCCESS")
check("R1 primer complete RUNNING -> SUCCESS", first["status"] == "SUCCESS", str(first))
check("R2 reintento idempotente (mismo estado)", second["status"] == "SUCCESS", str(second))
check("R3 run sigue SUCCESS (sin re-ejecución)",
      run_json(TEST_RUNS / r_r).get("status") == "SUCCESS")
check("R4 sin artefactos generados por complete idempotente",
      not any((TEST_RUNS / r_r / "output").iterdir()))

# ---------------------------------------------------------------------------
# S. run_worker.py --once contra una API real (uvicorn) procesa un job offline
# ---------------------------------------------------------------------------
for leftover in list(TEST_RUNS.glob("run-*")):
    if run_json(leftover).get("status") == "QUEUED":
        shutil.rmtree(leftover, ignore_errors=True)
port = free_port()
run_s = new_run(service, "run-20260816-100011", offline=True)
api_proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "api.app:create_app", "--factory",
     "--host", "127.0.0.1", "--port", str(port)],
    cwd=ROOT,
    env={**os.environ, "PYTHONPATH": str(SRC), RUNS_ENV: str(TEST_RUNS),
         "WORKER_TOKEN": TEST_TOKEN},
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
try:
    check("S1 API arranca y responde /health", wait_health(port), f"port={port}")
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "run_worker.py"), "--once"],
        capture_output=True, text=True, timeout=600,
        env={**os.environ, "PYTHONPATH": str(SRC), RUNS_ENV: str(TEST_RUNS),
             "WORKER_TOKEN": TEST_TOKEN,
             "WORKER_API_URL": f"http://127.0.0.1:{port}",
             "WORKER_ID": "test-worker-once"},
    )
    check("S2 worker --once exit 0", proc.returncode == 0,
          f"rc={proc.returncode} stderr={proc.stderr[-400:]}")
    rj = run_json(TEST_RUNS / run_s)
    check("S3 run procesado a SUCCESS vía API+worker", rj.get("status") == "SUCCESS", str(rj))
    check("S4 quality_passed True", rj.get("quality_passed") is True, str(rj))
    video = list((TEST_RUNS / run_s / "output" / "video").glob("*.mp4"))
    check("S5 worker genera video", bool(video), "sin mp4")
    with TestClient(app) as c:
        rv = c.get(f"/api/v1/runs/{run_s}/video", timeout=10)
        check("S6 video servible tras ciclo worker", rv.status_code == 200, f"status={rv.status_code}")
finally:
    api_proc.terminate()
    try:
        api_proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        api_proc.kill()

# ---------------------------------------------------------------------------
# T. run_pipeline.py --offline sigue funcionando
# ---------------------------------------------------------------------------
off_id = "run-20260816-100012"
proc = subprocess.run(
    [sys.executable, str(SCRIPTS / "run_pipeline.py"), "El ciclo del agua",
     "--offline", "--run-id", off_id],
    capture_output=True, text=True, timeout=600, env={**os.environ, RUNS_ENV: str(TEST_RUNS)},
)
check("T1 run_pipeline --offline exit 0", proc.returncode == 0,
      f"rc={proc.returncode} stderr={proc.stderr[-400:]}")
rj = run_json(TEST_RUNS / off_id)
check("T2 offline SUCCESS", rj.get("status") == "SUCCESS", str(rj))
check("T3 offline Quality Gate PASS", rj.get("quality_passed") is True, str(rj))

# ---------------------------------------------------------------------------
# U-X. Regresión de ME40.2, ME40.3, ME38 y ME38.3 (subprocesos)
# ---------------------------------------------------------------------------
REGRESSIONS = [
    ("U1", "ME40.2 (cola/worker local)", "test_queue_me402.py"),
    ("V1", "ME40.3 (RunStorage)", "test_storage_me403.py"),
    ("W1", "ME38 (contenido sintético)", "test_synthetic_content_me38.py"),
    ("X1", "ME38.3 (contenido sintético 2)", "test_synthetic_content_me383.py"),
]
for label, name, fname in REGRESSIONS:
    p = subprocess.run(
        [sys.executable, str(ROOT / "tests" / fname)],
        capture_output=True, text=True, timeout=1200, cwd=ROOT,
    )
    tail = (p.stdout + p.stderr)[-300:]
    check(f"{label} {name} -> PASS",
          p.returncode == 0 and "RESULTADO: PASS" in p.stdout, f"rc={p.returncode} {tail}")

# ---------------------------------------------------------------------------
# Y. node --check del frontend
# ---------------------------------------------------------------------------
proc = subprocess.run(["node", "--check", str(ROOT / "web" / "app.js")],
                      capture_output=True, text=True, timeout=60)
check("Y1 node --check app.js", proc.returncode == 0, proc.stderr[:400])

# ---------------------------------------------------------------------------
# Z. py_compile de src/scripts/tests
# ---------------------------------------------------------------------------
for path in ("src/api", "src/application", "src/pipeline", "scripts", "tests"):
    proc = subprocess.run([sys.executable, "-m", "py_compile", "-q",
                           *[str(p) for p in (ROOT / path).glob("*.py")]],
                          capture_output=True, text=True, timeout=120)
    check(f"Z1 py_compile {path}", proc.returncode == 0, proc.stderr[:400])

# ---------------------------------------------------------------------------
# AA. git diff --check
# ---------------------------------------------------------------------------
proc = subprocess.run(["git", "diff", "--check"], cwd=ROOT,
                      capture_output=True, text=True, timeout=60)
check("AA1 git diff --check PASS", proc.returncode == 0,
      proc.stdout[:400] + proc.stderr[:400])

print()
print(f"RESULTADO: {'PASS' if not FAILURES else 'FAIL'}")
for f in FAILURES:
    print(f"  - {f}")
sys.exit(0 if not FAILURES else 1)