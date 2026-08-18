# -*- coding: utf-8 -*-
"""ME40.9B - Tests del publish de artefactos del worker outbound.

El pipeline genera ``output/runs/<run_id>/output/video/<nombre>.mp4`` pero el
control plane solo recibe SUCCESS. ME40.9B integra :class:`LocalArtifactStore`
en ``scripts/run_worker.py``: tras un pipeline en SUCCESS el worker registra la
metadata del video en ``artifacts.json`` (dentro del run local, sin copiar ni
mover el MP4) y SOLO entonces informa SUCCESS. Si el video no existe, la ruta
es inválida/traversal o el publish falla, se reporta FAILED de forma
controlada. No se modifica PipelineResult/Runner/renderer/QualityGate/
RunRecord/RunStorage/API/frontend; el modo local sigue igual.

Tests (A-S), stdlib puro, sin pytest, sin HTTP, sin pipeline real (mocks de
executor y ArtifactStore), sin Gemini/SD15/Kokoro/FFmpeg/GPU:

- A. Worker importa ArtifactStore/LocalArtifactStore correctamente.
- B. SUCCESS + video existente -> publish ejecutado.
- C. artifacts.json creado en el run local.
- D. artifact kind == video.
- E. filename correcto.
- F. content_type == video/mp4.
- G. size_bytes correcto.
- H. reference relativa.
- I. video inexistente -> FAILED controlado.
- J. video_path inválido/traversal -> FAILED controlado.
- K. publish falla -> FAILED controlado.
- L. SUCCESS solo después de publicar el artefacto.
- M. complete FAILED cuando publish falla.
- N. no se copia ni mueve el MP4.
- O. no se realizan llamadas HTTP externas durante los tests.
- P. la API pública del worker no cambia.
- Q. el worker local existente sigue funcionando.
- R. py_compile (run_worker.py + artifacts.py).
- S. git diff --check.

Uso:

    .venv\\Scripts\\python.exe tests/test_worker_artifact_publish_me409b.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
TEMP_ROOT = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "opencode" / "me409b"
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

import application.artifacts  # noqa: E402
import run_worker  # noqa: E402
from application.artifacts import ArtifactRecord, LocalArtifactStore  # noqa: E402
from pipeline import RunContext  # noqa: E402
from pipeline.lifecycle import RunRecord, RunStatus, write_run_record  # noqa: E402

OUTSIDE_FILE = TEST_RUNS.parent / "outside_me409b.mp4"
OUTSIDE_FILE.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512)

# Bloqueo de red desde el inicio: ningún check puede abrir sockets HTTP.
_orig_socket = socket.socket
_orig_urlopen = urllib.request.urlopen


def _blocked(*_args, **_kwargs):
    raise AssertionError("HTTP externo no permitido durante los tests ME40.9B")


socket.socket = _blocked
urllib.request.urlopen = _blocked

FAILURES: list[str] = []


def check(ok: bool, label: str, detalle: str = "") -> None:
    if ok:
        print(f"[OK] {label}")
    else:
        print(f"[FAIL] {label} {detalle}".rstrip())
        FAILURES.append(label)


VIDEO_PAYLOAD = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8192
RUN_SEQ = 0


def next_run_id() -> str:
    global RUN_SEQ
    RUN_SEQ += 1
    return f"run-20260818-{9000 + RUN_SEQ:06d}"


def job_payload(run_id: str) -> dict:
    return {"run_id": run_id, "topic": "tema de prueba", "offline": True, "project_id": None}


class FakeClient:
    """Cliente falso del protocolo outbound (sin red)."""

    def __init__(self, jobs: Optional[list[dict]] = None, timeline: Optional[list] = None) -> None:
        self._jobs = list(jobs or [])
        self.completed: list[tuple] = []
        self.timeline = timeline

    def next_job(self) -> Optional[dict]:
        if self._jobs:
            return self._jobs.pop(0)
        return None

    def heartbeat(self, run_id: str) -> int:
        return 200

    def progress(self, run_id: str, stage: str) -> int:
        return 200

    def complete(self, run_id: str, status: str, error: Optional[str] = None) -> int:
        self.completed.append((run_id, status, error))
        if self.timeline is not None:
            self.timeline.append(f"complete:{status}")
        return 200


def write_success(context: RunContext, *, with_video: bool = True) -> Path:
    """Prepara un run local con run.json SUCCESS (y video opcional)."""
    context.prepare()
    write_run_record(
        context.run_dir,
        RunRecord(run_id=context.run_id, status=RunStatus.SUCCESS),
    )
    if with_video:
        video_dir = context.run_dir / "output" / "video"
        video_dir.mkdir(parents=True, exist_ok=True)
        (video_dir / "video.mp4").write_bytes(VIDEO_PAYLOAD)
    return context.run_dir


def make_executor(setup) -> object:
    def executor(context: RunContext) -> object:
        setup(context)
        return {"ok": True}

    return executor


# ---------------------------------------------------------------------------
# A. Worker importa ArtifactStore correctamente.
# ---------------------------------------------------------------------------

check(run_worker.ArtifactStore is application.artifacts.ArtifactStore, "A1 ArtifactStore importado")
check(run_worker.LocalArtifactStore is application.artifacts.LocalArtifactStore, "A2 LocalArtifactStore importado")
check(callable(run_worker.publish_video_artifact), "A3 publish_video_artifact disponible")

# ---------------------------------------------------------------------------
# B. SUCCESS + video existente -> publish ejecutado.
# ---------------------------------------------------------------------------

run_id_b = next_run_id()
ctx_b = RunContext(run_id=run_id_b, runs_root=TEST_RUNS)
client_b = FakeClient([job_payload(run_id_b)])
try:
    result_b = run_worker.run_remote_cycle(
        client_b, TEST_RUNS, executor=make_executor(write_success)
    )
    b_ok = True
except Exception as exc:  # noqa: BLE001
    b_ok = False
    print(f"   (ciclo falló: {type(exc).__name__}: {exc})")
check(b_ok, "B1 ciclo outbound completado")
check(
    b_ok and result_b is True and client_b.completed == [(run_id_b, "SUCCESS", None)],
    "B2 complete con SUCCESS",
)
artifacts_b = (ctx_b.run_dir / "artifacts.json")
check(b_ok and artifacts_b.is_file(), "B3 publish ejecutado (artifacts.json existe)")

# ---------------------------------------------------------------------------
# C. artifacts.json creado (dentro del run local).
# ---------------------------------------------------------------------------

check(b_ok and artifacts_b.is_file(), "C1 artifacts.json creado en el run local")
c_ok = False
if b_ok:
    try:
        data_c = json.loads(artifacts_b.read_text(encoding="utf-8"))
        c_ok = isinstance(data_c, list) and len(data_c) == 1
    except ValueError:
        c_ok = False
check(c_ok, "C2 artifacts.json es una lista JSON con un registro")

# ---------------------------------------------------------------------------
# D. artifact kind == video.
# ---------------------------------------------------------------------------

rec_b = LocalArtifactStore(TEST_RUNS).list(run_id_b)
check(b_ok and len(rec_b) == 1 and rec_b[0].kind == "video", "D1 artifact kind == video")

# ---------------------------------------------------------------------------
# E. filename correcto.
# ---------------------------------------------------------------------------

check(b_ok and rec_b and rec_b[0].filename == "video.mp4", "E1 filename video.mp4")

# ---------------------------------------------------------------------------
# F. content_type == video/mp4.
# ---------------------------------------------------------------------------

check(b_ok and rec_b and rec_b[0].content_type == "video/mp4", "F1 content_type video/mp4")

# ---------------------------------------------------------------------------
# G. size_bytes correcto.
# ---------------------------------------------------------------------------

check(
    b_ok and rec_b and rec_b[0].size_bytes == len(VIDEO_PAYLOAD),
    "G1 size_bytes coincide con el archivo real",
)

# ---------------------------------------------------------------------------
# H. reference relativa.
# ---------------------------------------------------------------------------

check(
    b_ok and rec_b and rec_b[0].reference == "output/video/video.mp4",
    "H1 reference relativa al run",
)
check(
    b_ok and rec_b and not Path(rec_b[0].reference).is_absolute(),
    "H2 reference nunca absoluta",
)

# ---------------------------------------------------------------------------
# I. video inexistente -> FAILED controlado.
# ---------------------------------------------------------------------------

run_id_i = next_run_id()
ctx_i = RunContext(run_id=run_id_i, runs_root=TEST_RUNS)
client_i = FakeClient([job_payload(run_id_i)])
run_worker.run_remote_cycle(
    client_i, TEST_RUNS, executor=make_executor(
        lambda c: write_success(c, with_video=False)
    )
)
check(
    client_i.completed and client_i.completed[0][1] == "FAILED",
    "I1 video inexistente -> complete FAILED",
)
check(
    client_i.completed and "artefacto" in (client_i.completed[0][2] or ""),
    "I2 error controlado menciona la publicación del artefacto",
)

# ---------------------------------------------------------------------------
# J. video_path inválido/traversal -> FAILED controlado.
# ---------------------------------------------------------------------------

run_id_j = next_run_id()
ctx_j = RunContext(run_id=run_id_j, runs_root=TEST_RUNS)
client_j = FakeClient([job_payload(run_id_j)])
orig_find = run_worker._find_video
run_worker._find_video = lambda run_dir: OUTSIDE_FILE
try:
    run_worker.run_remote_cycle(
        client_j, TEST_RUNS, executor=make_executor(write_success)
    )
finally:
    run_worker._find_video = orig_find
check(
    client_j.completed and client_j.completed[0][1] == "FAILED",
    "J1 video fuera del run -> complete FAILED",
)
check(
    client_j.completed and "artefacto" in (client_j.completed[0][2] or ""),
    "J2 error controlado menciona la publicación del artefacto",
)

# ---------------------------------------------------------------------------
# K. publish falla -> FAILED controlado.
# ---------------------------------------------------------------------------


class BrokenStore:
    def __init__(self) -> None:
        self.calls = []

    def list(self, run_id: str):
        self.calls.append(("list", run_id))
        return []

    def publish(self, run_id: str, source: Path, **kwargs):
        self.calls.append(("publish", run_id, str(source), kwargs.get("kind")))
        raise RuntimeError("publish caído (fallo simulado)")


broken = BrokenStore()
run_id_k = next_run_id()
client_k = FakeClient([job_payload(run_id_k)])
run_worker.run_remote_cycle(
    client_k, TEST_RUNS, executor=make_executor(write_success), artifact_store=broken
)
check(broken.calls and broken.calls[0][0] == "list", "K1 publish intentó publicar")
check(
    client_k.completed and client_k.completed[0][1] == "FAILED",
    "K2 publish falla -> complete FAILED",
)
check(
    client_k.completed and "artefacto" in (client_k.completed[0][2] or ""),
    "K3 error controlado menciona la publicación del artefacto",
)

# ---------------------------------------------------------------------------
# L. SUCCESS solo después de publicar el artefacto.
# ---------------------------------------------------------------------------

timeline: list[str] = []


class RecorderStore:
    def list(self, run_id: str):
        timeline.append(f"list:{run_id}")
        return []

    def publish(self, run_id: str, source: Path, **kwargs):
        timeline.append(f"publish:{kwargs.get('kind')}")
        return ArtifactRecord(
            artifact_id=f"{run_id}-video-abcdef",
            run_id=run_id,
            kind="video",
            filename=Path(source).name,
            content_type="video/mp4",
            size_bytes=0,
            reference="output/video/video.mp4",
            storage="local",
        )


run_id_l = next_run_id()
client_l = FakeClient([job_payload(run_id_l)], timeline=timeline)
run_worker.run_remote_cycle(
    client_l, TEST_RUNS, executor=make_executor(write_success), artifact_store=RecorderStore()
)
check(
    timeline == [f"list:{run_id_l}", "publish:video", "complete:SUCCESS"],
    "L1 SUCCESS solo después de publish (orden list->publish->complete)",
)
check(client_l.completed == [(run_id_l, "SUCCESS", None)], "L2 complete SUCCESS tras publish OK")

# ---------------------------------------------------------------------------
# M. complete FAILED cuando publish falla.
# ---------------------------------------------------------------------------

run_id_m = next_run_id()
client_m = FakeClient([job_payload(run_id_m)])
run_worker.run_remote_cycle(
    client_m, TEST_RUNS, executor=make_executor(write_success), artifact_store=BrokenStore()
)
check(
    client_m.completed and client_m.completed[0][1] == "FAILED",
    "M1 complete FAILED cuando publish falla",
)

# ---------------------------------------------------------------------------
# N. no se copia ni mueve el MP4.
# ---------------------------------------------------------------------------

video_b = ctx_b.run_dir / "output" / "video" / "video.mp4"
bytes_antes = video_b.read_bytes()
run_worker.run_remote_cycle(
    FakeClient([job_payload(run_id_b)]),
    TEST_RUNS,
    executor=make_executor(write_success),
)
check(
    video_b.exists() and video_b.read_bytes() == bytes_antes,
    "N1 el MP4 sigue en su ruta original con los mismos bytes",
)
check(len(LocalArtifactStore(TEST_RUNS).list(run_id_b)) == 1, "N2 sin duplicados tras re-publicar")

# ---------------------------------------------------------------------------
# O. no HTTP externo.
# ---------------------------------------------------------------------------

o_ok = True
try:
    for rid in (run_id_b, run_id_i, run_id_j):
        RunContext(run_id=rid, runs_root=TEST_RUNS)
except Exception as exc:  # noqa: BLE001
    o_ok = False
    print(f"   (HTTP/socket no bloqueado: {type(exc).__name__}: {exc})")
check(o_ok, "O1 sin HTTP externo durante todos los checks")

# ---------------------------------------------------------------------------
# P. la API pública del worker no cambia.
# ---------------------------------------------------------------------------

check(
    all(hasattr(run_worker.WorkerApiClient, m) for m in ("next_job", "heartbeat", "progress", "complete")),
    "P1 WorkerApiClient conserva el contrato outbound",
)
st = subprocess.run(
    ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=str(ROOT)
).stdout.splitlines()
modificados = [l for l in st if l.startswith(" M ") or l.startswith("M ")]
untracked = [l for l in st if l.startswith("?? ")]
modificados_sin_agents = [
    l[3:].replace("/", "\\") for l in modificados if "AGENTS.md" not in l
]
check(
    set(modificados_sin_agents) == {"scripts\\run_worker.py"},
    f"P2 solo run_worker.py modificado (además de AGENTS.md preexistente): {modificados_sin_agents}",
)
allowed_untracked = {
    "src\\application\\artifacts.py",
    "tests\\test_artifacts_me409a.py",
    "tests\\test_worker_artifact_publish_me409b.py",
}
check(
    set(l[3:].replace("/", "\\") for l in untracked) <= allowed_untracked,
    f"P3 solo archivos ME40.9A/B creados: {[l[3:] for l in untracked]}",
)

# ---------------------------------------------------------------------------
# Q. el worker local existente sigue funcionando.
# ---------------------------------------------------------------------------

called: list[bool] = []
orig_process_one = run_worker.process_one
run_worker.process_one = lambda rr: (called.append(True), False)[1]
try:
    run_worker.run_cycle(TEST_RUNS)
finally:
    run_worker.process_one = orig_process_one
check(called == [True], "Q1 run_cycle sigue delegando en process_one (modo local intacto)")
check(run_worker.process_one is orig_process_one, "Q2 process_one restaurado")

# ---------------------------------------------------------------------------
# R. py_compile.
# ---------------------------------------------------------------------------

R = []
for nombre, archivo in (
    ("R1 py_compile run_worker.py", SCRIPTS / "run_worker.py"),
    ("R2 py_compile artifacts.py", SRC / "application" / "artifacts.py"),
):
    R.append((nombre, subprocess.run(
        [sys.executable, "-m", "py_compile", str(archivo)],
        capture_output=True, text=True, cwd=str(ROOT),
    ).returncode == 0))
for nombre, ok in R:
    check(ok, nombre)

# ---------------------------------------------------------------------------
# S. git diff --check.
# ---------------------------------------------------------------------------

diff = subprocess.run(
    ["git", "diff", "--check"], capture_output=True, text=True, cwd=str(ROOT)
)
check(diff.returncode == 0, "S1 git diff --check")
if diff.returncode != 0:
    print(diff.stdout)

# ---------------------------------------------------------------------------
# Restauración del bloqueo de red.
# ---------------------------------------------------------------------------

socket.socket = _orig_socket
urllib.request.urlopen = _orig_urlopen
check(socket.socket is _orig_socket, "O2 socket restaurado tras el arnés")

print()
if FAILURES:
    print(f"FALLOS ({len(FAILURES)}): {FAILURES}")
    print("RESULTADO: FAIL")
    sys.exit(1)
print("RESULTADO: PASS")
