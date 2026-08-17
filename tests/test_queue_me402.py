# -*- coding: utf-8 -*-
"""ME40.2 - Tests de la cola local y el ciclo de vida asíncrono del run.

Verifica (offline, sin red, sin Gemini, sin SD15/Kokoro) que la creación de un
run ya no ejecuta el pipeline dentro del request HTTP: ``POST /runs`` devuelve
``202`` con estado ``QUEUED``, un worker local (``scripts/run_worker.py`` y la
cola ``pipeline.queue``) adquiere el job y lo lleva ``QUEUED → RUNNING →
SUCCESS/QUALITY_FAILED/FAILED``, y la API/historial/proyectos/video siguen
funcionando.

Los ejecutores de los tests son **fakes** (escriben el estado terminal sin
generar video real); el pipeline real se valida con ``run_pipeline.py
--offline`` (N/O) y en el E2E.

Checks cubiertos (A-Q):

- A. ``POST /runs`` devuelve 202.
- B. el estado inicial del run es ``QUEUED``.
- C. el pipeline NO se ejecuta dentro del request (sin ``output/``).
- D. el worker recoge el job (``process_one`` / ``run_worker.py --once``).
- E. ``QUEUED`` → ``RUNNING`` (``claim_job``).
- F. ``RUNNING`` → ``SUCCESS`` (ejecutor persiste el estado final).
- G. ``QUALITY_FAILED`` conserva el video.
- H. ``FAILED`` conserva el error.
- I. ``GET /runs/{id}`` refleja los estados.
- J. el frontend reconoce ``QUEUED`` (etiqueta "En cola").
- K. los proyectos funcionan (asociación run-proyecto).
- L. el historial funciona.
- M. el video funciona (descarga).
- N. ``--offline`` funciona (pipeline sintético vía ``run_pipeline.py``).
- O. ``run_pipeline.py`` funciona (CLI síncrono y ``--list-runs``).
- P. sin workers huérfanos ni bloqueos residuales.
- Q. ``git diff --check`` PASS.

Uso (sin framework, solo stdlib):

    .venv\\Scripts\\python.exe tests\\test_queue_me402.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TEMP_ROOT = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "opencode" / "me402"
TEST_RUNS = TEMP_ROOT / "tests_runs"
TEST_RUNS_EMPTY = TEMP_ROOT / "worker_smoke"
RUNS_ENV = "PIPELINE_RUNS_ROOT"

for path in (TEST_RUNS, TEST_RUNS_EMPTY):
    if path.exists():
        shutil.rmtree(path)
TEST_RUNS.mkdir(parents=True, exist_ok=True)
TEST_RUNS_EMPTY.mkdir(parents=True, exist_ok=True)

os.environ[RUNS_ENV] = str(TEST_RUNS)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from application import ApplicationService, CreateRunRequest
from api.app import create_app
from fastapi.testclient import TestClient
from pipeline import (
    RunRecord,
    RunStatus,
    claim_job,
    find_orphan_jobs,
    find_queued_jobs,
    load_job_payload,
    load_run_record,
    process_one,
    process_queued_job,
    release_job,
    write_run_record,
)
from pipeline.lifecycle import checked_run_dir

FAILURES: list[str] = []

#: Bytes de un MP4 falso (ftyp dentro de los primeros 64 bytes).
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(f"{label}: {detail}")
    print(f"[{'OK' if cond else 'FAIL'}] {label}" + ("" if cond else f" | {detail}"))


def run_json(run_dir: Path) -> dict:
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


def fake_executor(status, *, error=None, quality_passed=None, video=False):
    """Devuelve un ejecutor fake que persiste un estado terminal."""

    def _exec(context) -> object:
        if video:
            vdir = context.output_dir / "video"
            vdir.mkdir(parents=True, exist_ok=True)
            (vdir / "video.mp4").write_bytes(FAKE_MP4)
        prev = load_run_record(context.run_dir)
        write_run_record(
            context.run_dir,
            RunRecord(
                run_id=context.run_id,
                status=status,
                created_at=prev.created_at if prev else None,
                queued_at=prev.queued_at if prev else None,
                started_at=prev.started_at if prev else None,
                finished_at=datetime.now(timezone.utc),
                error=error,
                quality_passed=quality_passed,
            ),
        )
        return None

    return _exec


def count_ffmpeg_procs() -> int:
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return -1
    return sum(1 for line in out.stdout.splitlines() if "ffmpeg" in line.lower())


# ---------------------------------------------------------------------------
# A. POST /runs devuelve 202 y B. estado inicial QUEUED
# ---------------------------------------------------------------------------
app = create_app(runs_root=TEST_RUNS)
service = ApplicationService(runs_root=TEST_RUNS)

with TestClient(app) as c:
    r = c.post(
        "/api/v1/runs",
        json={"topic": "Cinco inventos que cambiaron la historia",
              "run_id": "run-20260816-100001", "offline": False},
        timeout=30,
    )
    check("A1 POST /runs devuelve 202", r.status_code == 202, f"status={r.status_code} {r.text}")
    body = r.json()
    run1 = body.get("run_id")
    check("B1 estado inicial QUEUED", body.get("status") == "QUEUED", str(body))
    check("B2 run_id presente", run1 == "run-20260816-100001", str(body))
    check("B3 topic vacio -> 400", c.post("/api/v1/runs", json={"topic": " "}, timeout=30).status_code == 400)

    # C. El pipeline NO se ejecuta dentro del request.
    run_dir = TEST_RUNS / run1
    rj = run_json(run_dir)
    check("C1 run.json persistido QUEUED", rj.get("status") == "QUEUED", str(rj))
    check("C2 sin output/ (pipeline no ejecutado)",
          not (run_dir / "output").exists(), "se creo output/")
    check("C3 queued_at persistido", rj.get("queued_at") is not None, str(rj))
    job = load_job_payload(run_dir)
    check("C4 job.json con parametros",
          job is not None and job.topic.startswith("Cinco") and job.offline is False,
          str(job))

    # I. GET /runs/{id} refleja QUEUED.
    rr = c.get(f"/api/v1/runs/{run1}", timeout=10)
    check("I1 GET run refleja QUEUED",
          rr.status_code == 200 and rr.json().get("status") == "QUEUED", str(rr.json()))

    # E. QUEUED -> RUNNING (claim_job adquiere el job).
    ctx1 = claim_job(TEST_RUNS, run1)
    check("E1 claim_job adquiere el job", ctx1 is not None and ctx1.run_id == run1, str(ctx1))
    rj = run_json(run_dir)
    check("E2 estado RUNNING tras claim", rj.get("status") == "RUNNING", str(rj))
    check("E3 started_at tras claim", rj.get("started_at") is not None, str(rj))
    check("E4 queued_at conservado en RUNNING", rj.get("queued_at") is not None, str(rj))
    rr = c.get(f"/api/v1/runs/{run1}", timeout=10)
    check("I2 GET run refleja RUNNING",
          rr.status_code == 200 and rr.json().get("status") == "RUNNING", str(rr.json()))
    # Dos workers: el segundo claim no adquiere (bloqueo atómico).
    check("E5 dos workers: segundo claim -> None",
          claim_job(TEST_RUNS, run1) is None, "el segundo worker adquirió el job")

    # F. RUNNING -> SUCCESS (el worker ejecuta sobre el contexto adquirido).
    t0 = time.monotonic()
    fake_executor(RunStatus.SUCCESS, quality_passed=True)(ctx1)
    release_job(TEST_RUNS, run1)
    rj = run_json(run_dir)
    check("F1 estado SUCCESS tras procesar", rj.get("status") == "SUCCESS", str(rj))
    check("F2 quality_passed True", rj.get("quality_passed") is True, str(rj))
    check("F3 queued_at conservado en SUCCESS", rj.get("queued_at") is not None, str(rj))
    check("F4 finished_at tras procesar", rj.get("finished_at") is not None, str(rj))
    check("F5 sin bloqueo residual",
          not (run_dir / ".worker.lock").exists(), "queda .worker.lock")
    rr = c.get(f"/api/v1/runs/{run1}", timeout=10)
    check("I3 GET run refleja SUCCESS",
          rr.status_code == 200 and rr.json().get("status") == "SUCCESS", str(rr.json()))
    print(f"     ciclo QUEUED->RUNNING->SUCCESS completado en {time.monotonic() - t0:.2f}s")

    # D. El worker recoge el job (process_one sobre un run QUEUED nuevo).
    run_d = service.create_run(CreateRunRequest(
        topic="Tema encolado", offline=True, run_id="run-20260816-100006")).run_id
    check("D1 process_one recoge el job",
          process_one(TEST_RUNS, executor=fake_executor(RunStatus.SUCCESS, quality_passed=True)) is True,
          "process_one no procesó nada")
    rj = run_json(TEST_RUNS / run_d)
    check("D2 job procesado a SUCCESS", rj.get("status") == "SUCCESS", str(rj))

    # J. Frontend reconoce QUEUED.
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    check("J1 frontend define STATUS_QUEUED", 'STATUS_QUEUED = "QUEUED"' in js, "falta constante")
    check("J2 etiqueta 'En cola'", "En cola" in js, "falta etiqueta")
    check("J3 class de estado para QUEUED", "STATUS_QUEUED]: \"info\"" in js, "falta clase")

    # K. Proyectos funcionan.
    r = c.post("/api/v1/projects", json={"name": "Proyecto ME402"}, timeout=10)
    pid = r.json().get("project_id")
    check("K1 crear proyecto 201", r.status_code == 201 and pid, str(r.json()))
    r = c.post(
        "/api/v1/runs",
        json={"topic": "Hábitos de estudio", "project_id": pid,
              "run_id": "run-20260816-100002", "offline": False},
        timeout=30,
    )
    run2 = r.json().get("run_id")
    check("K2 POST run con proyecto 202", r.status_code == 202 and run2, str(r.json()))
    reg = json.loads((TEST_RUNS / "projects.json").read_text(encoding="utf-8"))
    check("K3 asociacion run-proyecto persistida",
          reg.get("run_projects", {}).get(run2) == pid, str(reg.get("run_projects")))
    check("K4 GET projects lista el proyecto",
          c.get("/api/v1/projects", timeout=10).json().get("active_project_id") == pid)
    process_one(TEST_RUNS, executor=fake_executor(RunStatus.SUCCESS, video=True, quality_passed=True))
    rr = c.get(f"/api/v1/runs/{run2}", timeout=10)
    check("K5 GET run refleja project_id",
          rr.json().get("project_id") == pid, str(rr.json()))

    # L. Historial funciona.
    rr = c.get("/api/v1/runs", timeout=10)
    history = rr.json()
    statuses = {x["run_id"]: x["status"] for x in history}
    check("L1 historial incluye ambos runs", run1 in statuses and run2 in statuses, str(statuses))
    check("L2 historial SUCCESS", statuses.get(run1) == "SUCCESS" and statuses.get(run2) == "SUCCESS", str(statuses))

    # G. QUALITY_FAILED conserva el video.
    r = c.post("/api/v1/runs", json={"topic": "Tema con calidad insuficiente",
                                     "run_id": "run-20260816-100003", "offline": False}, timeout=30)
    run3 = r.json().get("run_id")
    process_one(TEST_RUNS, executor=fake_executor(RunStatus.QUALITY_FAILED, video=True))
    rr = c.get(f"/api/v1/runs/{run3}", timeout=10)
    j = rr.json()
    check("G1 estado QUALITY_FAILED", j.get("status") == "QUALITY_FAILED", str(j))
    check("G2 video_path conservado", bool(j.get("video_path")), str(j))
    rv = c.get(f"/api/v1/runs/{run3}/video", timeout=10)
    check("G3 video servible en QUALITY_FAILED", rv.status_code == 200 and b"ftyp" in rv.content[:64], f"status={rv.status_code} bytes={len(rv.content)}")

    # M. Video funciona (SUCCESS con video).
    rv = c.get(f"/api/v1/runs/{run2}/video", timeout=10)
    check("M1 GET video 200 video/mp4",
          rv.status_code == 200 and rv.headers.get("content-type", "").startswith("video/mp4"),
          f"status={rv.status_code}")
    check("M2 MP4 valido (ftyp)", b"ftyp" in rv.content[:64], f"bytes={len(rv.content)}")

    # H. FAILED conserva el error.
    r = c.post("/api/v1/runs", json={"topic": "Tema que fallara",
                                     "run_id": "run-20260816-100004", "offline": False}, timeout=30)
    run4 = r.json().get("run_id")
    process_one(TEST_RUNS, executor=fake_executor(RunStatus.FAILED, error="Fallo simulado del ejecutor"))
    rr = c.get(f"/api/v1/runs/{run4}", timeout=10)
    j = rr.json()
    check("H1 estado FAILED", j.get("status") == "FAILED", str(j))
    check("H2 error conservado", j.get("error") == "Fallo simulado del ejecutor", str(j))

    # Ejecutor que eleva -> FAILED automático (sin workers huérfanos).
    r = c.post("/api/v1/runs", json={"topic": "Tema con crash",
                                     "run_id": "run-20260816-100005", "offline": False}, timeout=30)
    run_crash = r.json().get("run_id")
    def boom(context):  # noqa: ANN001
        raise RuntimeError("crash del worker")
    process_one(TEST_RUNS, executor=boom)
    rj = run_json(TEST_RUNS / run_crash)
    check("H3 crash del ejecutor -> FAILED persistido",
          rj.get("status") == "FAILED" and "crash" in (rj.get("error") or ""), str(rj))

# ---------------------------------------------------------------------------
# Orfanos: estructura de detección (sin recuperación)
# ---------------------------------------------------------------------------
# QUEUED + lock (worker murió entre lock y RUNNING).
r = service.create_run(CreateRunRequest(topic="Orfanos", offline=True, run_id="run-20260816-100007"))
run6 = r.run_id
(TEST_RUNS / run6 / ".worker.lock").write_text("99999", encoding="utf-8")
orphans = {o.run_id for o in find_orphan_jobs(TEST_RUNS)}
check("P1 QUEUED+lock detectado como huerfano", run6 in orphans, str(orphans))
# RUNNING sin liberar (worker murió en el pipeline).
ctx7 = claim_job(TEST_RUNS, service.create_run(CreateRunRequest(topic="Otro orfano", offline=True, run_id="run-20260816-100008")).run_id)
run7 = ctx7.run_id
orphans = {o.run_id for o in find_orphan_jobs(TEST_RUNS)}
check("P2 RUNNING detectado como huerfano", run7 in orphans, str(orphans))
shutil.rmtree(TEST_RUNS / run6, ignore_errors=True)
shutil.rmtree(TEST_RUNS / run7, ignore_errors=True)

# run_worker.py --once (smoke): cola vacía -> exit 0 sin ejecutar nada.
proc = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "run_worker.py"), "--once"],
    capture_output=True, text=True, timeout=60,
    env={**os.environ, RUNS_ENV: str(TEST_RUNS_EMPTY)},
)
check("D2 run_worker --once exit 0 (cola vacia)", proc.returncode == 0,
      f"rc={proc.returncode} stderr={proc.stderr[-400:]}")

# ---------------------------------------------------------------------------
# N. --offline funciona (pipeline sintético real vía run_pipeline.py)
# ---------------------------------------------------------------------------
off_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
proc = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "run_pipeline.py"),
     "El ciclo del agua", "--offline", "--run-id", off_id],
    capture_output=True, text=True, timeout=600, env={**os.environ, RUNS_ENV: str(TEST_RUNS)},
)
check("N1 run_pipeline --offline exit 0", proc.returncode == 0,
      f"rc={proc.returncode} stderr={proc.stderr[-400:]}")
rj = run_json(TEST_RUNS / off_id)
check("N2 offline SUCCESS", rj.get("status") == "SUCCESS", str(rj))
check("N3 offline Quality Gate PASS", rj.get("quality_passed") is True, str(rj))
off_video = list((TEST_RUNS / off_id / "output" / "video").glob("*.mp4"))
check("N4 offline genera video", bool(off_video), "sin mp4")

# O. run_pipeline.py funciona (CLI síncrono + listado/inspección).
proc = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "run_pipeline.py"), "--list-runs"],
    capture_output=True, text=True, timeout=60, env={**os.environ, RUNS_ENV: str(TEST_RUNS)},
)
check("O1 run_pipeline --list-runs exit 0", proc.returncode == 0,
      f"rc={proc.returncode} stderr={proc.stderr[-400:]}")
list_out = proc.stdout + proc.stderr
check("O2 --list-runs muestra el run offline", off_id in list_out, list_out[-400:])
proc = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "run_pipeline.py"), "--inspect-run", off_id],
    capture_output=True, text=True, timeout=60, env={**os.environ, RUNS_ENV: str(TEST_RUNS)},
)
inspect_out = proc.stdout + proc.stderr
check("O3 --inspect-run exit 0 y SUCCESS", proc.returncode == 0 and "SUCCESS" in inspect_out,
      f"rc={proc.returncode} {inspect_out[-400:]}")

# ---------------------------------------------------------------------------
# P. Sin workers huérfanos (final) + Q. git diff --check
# ---------------------------------------------------------------------------
orphans = find_orphan_jobs(TEST_RUNS)
check("P3 sin runs huerfanos al final", orphans == [], str([o.run_id for o in orphans]))
locks = list(TEST_RUNS.rglob("*.worker.lock"))
check("P4 sin .worker.lock residuales", locks == [], str(locks))
nprocs = count_ffmpeg_procs()
check("P5 sin procesos ffmpeg huerfanos", nprocs == 0, f"ffmpeg_procs={nprocs}")

proc = subprocess.run(["git", "diff", "--check"], cwd=ROOT, capture_output=True, text=True, timeout=60)
check("Q1 git diff --check PASS", proc.returncode == 0, proc.stdout[:400] + proc.stderr[:400])

print()
print(f"RESULTADO: {'PASS' if not FAILURES else 'FAIL'}")
for f in FAILURES:
    print(f"  - {f}")
sys.exit(0 if not FAILURES else 1)