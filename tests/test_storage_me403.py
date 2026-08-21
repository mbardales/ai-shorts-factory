# -*- coding: utf-8 -*-
"""ME40.3 - Tests de la abstracción de almacenamiento (RunStorage/LocalRunStorage).

Verifica (offline, sin red, sin Gemini, sin SD15/Kokoro) que la capa
Application/API ya no conoce el filesystem de los runs directamente:

- ``RunStorage`` define el contrato mínimo (directorio del run, video,
  assets) y ``LocalRunStorage`` lo implementa sobre ``PIPELINE_RUNS_ROOT``
  manteniendo el layout actual.
- ``ApplicationService.get_run_video`` delega en ``RunStorage``.
- La API nunca devuelve rutas absolutas (``video_path``/``video_url`` son URLs
  relativas de la propia API).
- Traversal/rutas absolutas del cliente → 4xx.
- Historial, proyectos, pipeline CLI y ``--offline`` siguen funcionando.
- Compatibilidad con runs generados antes de ME40.3 (run.json sin ``queued_at``).

Checks cubiertos (A-S):

- A. ``LocalRunStorage`` inicializa correctamente.
- B. puede localizar un run existente.
- C. puede localizar un video válido.
- D. run inexistente → ``None``/404 según la capa.
- E. video inexistente → ``None``/404.
- F. ``SUCCESS`` con video → 200.
- G. ``QUALITY_FAILED`` con video → 200.
- H. ``RUNNING`` sin video → 404.
- I. ``FAILED`` sin video → 404.
- J. ``UNKNOWN`` → 404.
- K. traversal → 4xx.
- L. ruta absoluta externa → 4xx.
- M. no se expone ruta absoluta al cliente.
- N. el frontend sigue resolviendo el video (``buildVideoUrl``/``videoSrc``).
- O. el historial continúa funcionando.
- P. los proyectos continúan funcionando.
- Q. la CLI del pipeline continúa funcionando.
- R. ``--offline`` continúa funcionando.
- S. ``git diff --check`` PASS.

Uso (sin framework de test, solo stdlib + fastapi TestClient):

    .venv\\Scripts\\python.exe tests\\test_storage_me403.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TEMP_ROOT = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "opencode" / "me403"
TEST_RUNS = TEMP_ROOT / "tests_runs"
RUNS_ENV = "PIPELINE_RUNS_ROOT"

for path in (TEST_RUNS,):
    if path.exists():
        shutil.rmtree(path)
TEST_RUNS.mkdir(parents=True, exist_ok=True)

os.environ[RUNS_ENV] = str(TEST_RUNS)
#: ME40.9F: la API carga el .env raíz; estos tests validan almacenamiento
#: local, así que fijan el backend explícitamente (el env gana sobre .env).
os.environ["OBJECT_STORAGE_BACKEND"] = "local"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from application import (  # noqa: E402
    ApplicationService,
    LocalRunStorage,
    RunStorage,
    StorageError,
)
from application.artifacts import LocalArtifactStore  # noqa: E402
from api.app import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pipeline import (  # noqa: E402
    RunRecord,
    RunStatus,
    load_run_record,
    write_run_record,
)

FAILURES: list[str] = []

#: Bytes de un MP4 falso (ftyp dentro de los primeros 64 bytes).
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(f"{label}: {detail}")
    print(f"[{'OK' if cond else 'FAIL'}] {label}" + ("" if cond else f" | {detail}"))


def make_run(
    run_id: str,
    status: RunStatus,
    *,
    video: bool = False,
    error: str | None = None,
    quality_passed: bool | None = None,
    legacy: bool = False,
    with_output: bool = True,
) -> Path:
    """Crea un run persistido (y opcionalmente su video) bajo TEST_RUNS."""
    run_dir = TEST_RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if with_output:
        (run_dir / "output").mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    record = RunRecord(
        run_id=run_id,
        status=status,
        created_at=now,
        queued_at=None if legacy else now,
        started_at=now,
        finished_at=now,
        error=error,
        quality_passed=quality_passed,
    )
    write_run_record(run_dir, record)
    if with_output:
        (run_dir / "output" / "content.json").write_text(
            json.dumps({"topic": run_id}, ensure_ascii=False), encoding="utf-8"
        )
    if video:
        vdir = run_dir / "output" / "video"
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "video.mp4").write_bytes(FAKE_MP4)
    return run_dir


# Fixtures (run_ids explícitos, evitan colisiones por resolución de segundos).
RUN_SUCCESS = "run-20260817-100001"
RUN_QF = "run-20260817-100002"
RUN_RUNNING = "run-20260817-100003"
RUN_FAILED = "run-20260817-100004"
RUN_UNKNOWN = "run-20260817-100005"
RUN_NO_VIDEO = "run-20260817-100006"
RUN_LEGACY = "run-20260817-100007"
RUN_NONEXIST = "run-20260817-199999"

make_run(RUN_SUCCESS, RunStatus.SUCCESS, video=True, quality_passed=True)
make_run(RUN_QF, RunStatus.QUALITY_FAILED, video=True, quality_passed=False)
make_run(RUN_RUNNING, RunStatus.RUNNING)
make_run(RUN_FAILED, RunStatus.FAILED, error="Fallo simulado")
# UNKNOWN: directorio sin run.json legible.
make_run(RUN_UNKNOWN, RunStatus.UNKNOWN, with_output=False)
(run_unknown_dir := TEST_RUNS / RUN_UNKNOWN).mkdir(parents=True, exist_ok=True)
(run_unknown_dir / "run.json").write_text("{corrupto", encoding="utf-8")
make_run(RUN_NO_VIDEO, RunStatus.SUCCESS, quality_passed=True)
# Run generado antes de ME40.3: run.json SIN queued_at.
make_run(RUN_LEGACY, RunStatus.SUCCESS, video=True, quality_passed=True, legacy=True)

# ME40.9E: la entrega del video pasa por artifacts publicados (artifacts.json).
# Los fixtures con video servible se publican en el store local; los runs sin
# video (RUNNING/FAILED/UNKNOWN/NO_VIDEO) NO se publican -> 404.
artifact_store = LocalArtifactStore(TEST_RUNS)
artifact_store.publish(RUN_SUCCESS, "output/video/video.mp4", kind="video")
artifact_store.publish(RUN_QF, "output/video/video.mp4", kind="video")

# ---------------------------------------------------------------------------
# A-C. LocalRunStorage: contrato, localización de run y de video
# ---------------------------------------------------------------------------
storage = LocalRunStorage(runs_root=TEST_RUNS)
check("A1 LocalRunStorage es un RunStorage", isinstance(storage, RunStorage))
check("A2 inicializa con runs_root explícito", storage._root() == TEST_RUNS)
default_storage = LocalRunStorage()
check("A3 inicializa con runs_root por defecto (env)",
      default_storage._root() == TEST_RUNS, str(default_storage._root()))

run_dir = storage.run_dir(RUN_SUCCESS)
check("B1 localiza run existente", run_dir == TEST_RUNS / RUN_SUCCESS, str(run_dir))
check("B2 run inexistente -> None", storage.run_dir(RUN_NONEXIST) is None)

video = storage.resolve_video(RUN_SUCCESS)
check("C1 resuelve video válido", video is not None and video.name == "video.mp4", str(video))
check("C2 video dentro de output/video",
      video is not None and video.parent.name == "video" and video.parent.parent.name == "output")
check("C3 has_video True", storage.has_video(RUN_SUCCESS))
fh = storage.open_video(RUN_SUCCESS)
check("C4 open_video abre binario y devuelve el contenido",
      fh is not None and fh.read() == FAKE_MP4)
if fh is not None:
    fh.close()

assets = storage.list_assets(RUN_SUCCESS)
check("C5 lista assets (content.json y video)", "content.json" in assets and "video/video.mp4" in assets, str(assets))
check("C6 list_assets sin output -> vacío", storage.list_assets(RUN_UNKNOWN) == ())

# ---------------------------------------------------------------------------
# D/E. Storage: run y video inexistentes -> None
# ---------------------------------------------------------------------------
check("D1 resolve_video run inexistente -> None", storage.resolve_video(RUN_NONEXIST) is None)
check("E1 resolve_video video inexistente -> None", storage.resolve_video(RUN_NO_VIDEO) is None)
check("E2 open_video sin video -> None", storage.open_video(RUN_NO_VIDEO) is None)

# Compatibilidad con runs previos a ME40.3 (sección 12).
check("12-1 run legacy (sin queued_at) leído por LocalRunStorage",
      storage.resolve_video(RUN_LEGACY) is not None)
rec = load_run_record(TEST_RUNS / RUN_LEGACY)
check("12-2 run legacy conserva SUCCESS", rec is not None and rec.status is RunStatus.SUCCESS)

# ---------------------------------------------------------------------------
# Capa API: estados del video endpoint (F-J) + 404s (D/E)
# ---------------------------------------------------------------------------
app = create_app(runs_root=TEST_RUNS)
service = ApplicationService(runs_root=TEST_RUNS)
check("P0 el servicio usa un RunStorage", isinstance(service._storage, RunStorage))

with TestClient(app) as c:
    rv = c.get(f"/api/v1/runs/{RUN_SUCCESS}/video", timeout=10)
    check("F1 SUCCESS con video -> 200",
          rv.status_code == 200 and rv.headers.get("content-type", "").startswith("video/mp4"),
          f"status={rv.status_code}")
    check("F2 MP4 válido (ftyp)", b"ftyp" in rv.content[:64], f"bytes={len(rv.content)}")
    cd = rv.headers.get("content-disposition", "")
    check("F3 sin Content-Disposition attachment (inline para reproducir)",
          "attachment" not in cd.lower(), cd or "(sin header)")

    rv = c.get(f"/api/v1/runs/{RUN_QF}/video", timeout=10)
    check("G1 QUALITY_FAILED con video -> 200",
          rv.status_code == 200 and b"ftyp" in rv.content[:64], f"status={rv.status_code}")

    rv = c.get(f"/api/v1/runs/{RUN_RUNNING}/video", timeout=10)
    check("H1 RUNNING sin video -> 404", rv.status_code == 404, f"status={rv.status_code}")

    rv = c.get(f"/api/v1/runs/{RUN_FAILED}/video", timeout=10)
    check("I1 FAILED sin video -> 404", rv.status_code == 404, f"status={rv.status_code}")

    rv = c.get(f"/api/v1/runs/{RUN_UNKNOWN}/video", timeout=10)
    check("J1 UNKNOWN -> 404", rv.status_code == 404, f"status={rv.status_code}")

    rv = c.get(f"/api/v1/runs/{RUN_NONEXIST}/video", timeout=10)
    check("D2 run inexistente -> 404", rv.status_code == 404, f"status={rv.status_code}")

    rv = c.get(f"/api/v1/runs/{RUN_NO_VIDEO}/video", timeout=10)
    check("E3 SUCCESS sin video -> 404", rv.status_code == 404, f"status={rv.status_code}")

    # Service layer: get_run_video levanta las excepciones correctas.
    from application.exceptions import (  # noqa: E402
        ApplicationRunNotFoundError,
        ApplicationValidationError,
    )

    try:
        service.get_run_video(RUN_NONEXIST)
        check("D3 get_run_video run inexistente -> RunNotFound", False, "no lanzó")
    except ApplicationRunNotFoundError:
        check("D3 get_run_video run inexistente -> RunNotFound", True)

    try:
        service.get_run_video(RUN_NO_VIDEO)
        check("E4 get_run_video sin video -> RunNotFound", False, "no lanzó")
    except ApplicationRunNotFoundError:
        check("E4 get_run_video sin video -> RunNotFound", True)

# ---------------------------------------------------------------------------
# K/L. Seguridad: traversal y rutas absolutas -> 4xx
# ---------------------------------------------------------------------------
for bad in ("..", "../etc/passwd", "run-20260817-100001/../..", "../../../etc/passwd"):
    try:
        storage.resolve_video(bad)
        check(f"K1 traversal rechazado: {bad!r}", False, "no lanzó StorageError")
    except StorageError:
        check(f"K1 traversal rechazado: {bad!r}", True)

for bad in ("C:\\Windows\\system32\\config\\SAM", "/etc/passwd", "D:\\runs\\run-x"):
    try:
        storage.run_dir(bad)
        check(f"L1 ruta absoluta rechazada: {bad!r}", False, "no lanzó StorageError")
    except StorageError:
        check(f"L1 ruta absoluta rechazada: {bad!r}", True)

with TestClient(app) as c:
    # run_id que atraviesa el route (segmento único) pero es inválido/inseguro.
    rv = c.get("/api/v1/runs/../../video", timeout=10)
    check("K2 traversal en API -> 4xx", rv.status_code in (400, 404), f"status={rv.status_code}")
    rv = c.get("/api/v1/runs/run-20260817-100001/../../x/video", timeout=10)
    check("K3 traversal multi-segmento en API -> 4xx",
          rv.status_code in (400, 404, 405), f"status={rv.status_code}")
    # Ruta absoluta (backslashes literal en el segmento) -> 400 por validación.
    rv = c.get("/api/v1/runs/C%3A%5CWindows%5Csystem32%5Cconfig%5CSAM/video", timeout=10)
    check("L2 ruta absoluta en API -> 400",
          rv.status_code == 400, f"status={rv.status_code} body={rv.text[:120]}")

# ---------------------------------------------------------------------------
# M. No se exponen rutas absolutas al cliente
# ---------------------------------------------------------------------------
with TestClient(app) as c:
    rj = c.get(f"/api/v1/runs/{RUN_SUCCESS}", timeout=10).json()
    vp = rj.get("video_path")
    check("M1 video_path es URL relativa de la API",
          vp == f"/api/v1/runs/{RUN_SUCCESS}/video", str(vp))
    check("M2 video_path no es una ruta absoluta del filesystem",
          vp is not None
          and not re.match(r"^[a-zA-Z]:", vp)
          and "\\" not in vp
          and vp.startswith("/api/v1/runs/"),
          str(vp))
    rj = c.get(f"/api/v1/runs/{RUN_RUNNING}", timeout=10).json()
    check("M3 video_path null sin video", rj.get("video_path") is None, str(rj))
    hist = c.get("/api/v1/runs", timeout=10).json()
    leaks = [
        x.get("video_url")
        for x in hist
        if x.get("video_url")
        and (re.match(r"^[a-zA-Z]:", str(x.get("video_url"))) or str(x.get("video_url")).startswith("/"))
        and not str(x.get("video_url")).startswith("/api/")
    ]
    check("M4 historial sin rutas absolutas en video_url", leaks == [], str(leaks))

# ---------------------------------------------------------------------------
# O. Historial continúa funcionando
# ---------------------------------------------------------------------------
with TestClient(app) as c:
    hist = c.get("/api/v1/runs", timeout=10).json()
    by_id = {x["run_id"]: x for x in hist}
    check("O1 historial incluye SUCCESS con video_url",
          by_id.get(RUN_SUCCESS, {}).get("video_url") == f"/api/v1/runs/{RUN_SUCCESS}/video",
          str(by_id.get(RUN_SUCCESS)))
    check("O2 QUALITY_FAILED conserva video_url",
          bool(by_id.get(RUN_QF, {}).get("video_url")), str(by_id.get(RUN_QF)))
    check("O3 RUNNING sin video_url",
          by_id.get(RUN_RUNNING, {}).get("video_url") is None, str(by_id.get(RUN_RUNNING)))
    check("O4 FAILED sin video_url",
          by_id.get(RUN_FAILED, {}).get("video_url") is None, str(by_id.get(RUN_FAILED)))

# ---------------------------------------------------------------------------
# N. Frontend: videoSrc/buildVideoUrl siguen resolviendo (API_BASE + URL)
# ---------------------------------------------------------------------------
js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
check("N1 frontend maneja URLs /api/", 'normalized.startsWith("/api/")' in js, "falta rama API")
check("N2 frontend conserva videoSrc", "function videoSrc" in js)


def mirror_build_video_url(video_path: str) -> str:
    if not video_path:
        return ""
    if re.match(r"^https?://", video_path):
        return video_path
    normalized = str(video_path).replace("\\", "/")
    if normalized.startswith("/api/"):
        return f"http://127.0.0.1:8000{normalized}"
    marker = "/runs/"
    idx = normalized.rfind(marker)
    if idx != -1:
        return f"http://127.0.0.1:8001/{normalized[idx + len(marker):]}"
    return f"http://127.0.0.1:8001/{normalized.rstrip('/').split('/')[-1]}"


def mirror_video_src(video_url: str) -> str:
    if not video_url:
        return ""
    if re.match(r"^https?://", video_url):
        return video_url
    return f"http://127.0.0.1:8000{video_url}"


with TestClient(app) as c:
    rj = c.get(f"/api/v1/runs/{RUN_SUCCESS}", timeout=10).json()
    src = mirror_build_video_url(rj.get("video_path"))
    check("N3 buildVideoUrl resuelve video_path a la API",
          src == f"http://127.0.0.1:8000/api/v1/runs/{RUN_SUCCESS}/video", src)
    src = mirror_video_src(f"/api/v1/runs/{RUN_SUCCESS}/video")
    check("N4 videoSrc resuelve video_url del historial a la API",
          src == f"http://127.0.0.1:8000/api/v1/runs/{RUN_SUCCESS}/video", src)

proc = subprocess.run(["node", "--check", str(ROOT / "web" / "app.js")],
                      capture_output=True, text=True, timeout=60)
check("N5 node --check app.js", proc.returncode == 0, proc.stderr[:400])

# ---------------------------------------------------------------------------
# P. Proyectos continúan funcionando
# ---------------------------------------------------------------------------
with TestClient(app) as c:
    r = c.post("/api/v1/projects", json={"name": "Proyecto ME403"}, timeout=10)
    pid = r.json().get("project_id")
    check("P1 crear proyecto 201", r.status_code == 201 and pid, str(r.json()))
    r = c.post("/api/v1/runs", json={"topic": "Tema con proyecto",
                                     "run_id": "run-20260817-100010",
                                     "project_id": pid, "offline": True}, timeout=30)
    runp = r.json().get("run_id")
    check("P2 POST run con proyecto 202", r.status_code == 202 and runp, str(r.json()))
    reg = json.loads((TEST_RUNS / "projects.json").read_text(encoding="utf-8"))
    check("P3 asociación run-proyecto persistida",
          reg.get("run_projects", {}).get(runp) == pid, str(reg.get("run_projects")))
    overview = c.get("/api/v1/projects", timeout=10).json()
    check("P4 GET projects lista el proyecto y lo deja activo",
          overview.get("active_project_id") == pid, str(overview))
    rj = c.get(f"/api/v1/runs/{runp}", timeout=10).json()
    check("P5 GET run refleja project_id", rj.get("project_id") == pid, str(rj))

# ---------------------------------------------------------------------------
# Q/R. CLI del pipeline y --offline continúan funcionando
# ---------------------------------------------------------------------------
cli_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
proc = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "run_pipeline.py"),
     "El ciclo del agua", "--offline", "--run-id", cli_id],
    capture_output=True, text=True, timeout=600, env={**os.environ, RUNS_ENV: str(TEST_RUNS)},
)
check("R1 run_pipeline --offline exit 0", proc.returncode == 0,
      f"rc={proc.returncode} stderr={proc.stderr[-400:]}")
rec = load_run_record(TEST_RUNS / cli_id)
check("R2 offline SUCCESS", rec is not None and rec.status is RunStatus.SUCCESS, str(rec))
check("R3 offline Quality Gate PASS", rec is not None and rec.quality_passed is True, str(rec))
video_cli = storage.resolve_video(cli_id)
check("R4 LocalRunStorage resuelve el video del run offline", video_cli is not None, str(video_cli))
# ME40.9E: el pipeline CLI no publica artifacts (solo el worker); para la
# entrega por artifacts.json el test publica el video renderizado.
artifact_store.publish(cli_id, video_cli, kind="video")
with TestClient(app) as c:
    rv = c.get(f"/api/v1/runs/{cli_id}/video", timeout=10)
    check("R5 API sirve el video del run offline",
          rv.status_code == 200 and b"ftyp" in rv.content[:64], f"status={rv.status_code}")

proc = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "run_pipeline.py"), "--list-runs"],
    capture_output=True, text=True, timeout=60, env={**os.environ, RUNS_ENV: str(TEST_RUNS)},
)
list_out = proc.stdout + proc.stderr
check("Q1 run_pipeline --list-runs exit 0 y lista runs",
      proc.returncode == 0 and RUN_SUCCESS in list_out and cli_id in list_out,
      f"rc={proc.returncode} {list_out[-400:]}")

# ---------------------------------------------------------------------------
# S. git diff --check
# ---------------------------------------------------------------------------
proc = subprocess.run(["git", "diff", "--check"], cwd=ROOT, capture_output=True, text=True, timeout=60)
check("S1 git diff --check PASS", proc.returncode == 0, proc.stdout[:400] + proc.stderr[:400])

print()
print(f"RESULTADO: {'PASS' if not FAILURES else 'FAIL'}")
for f in FAILURES:
    print(f"  - {f}")
sys.exit(0 if not FAILURES else 1)