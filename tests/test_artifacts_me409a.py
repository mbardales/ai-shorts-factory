# -*- coding: utf-8 -*-
"""ME40.9A - Tests del Artifact Record / LocalArtifactStore.

El worker genera el video en ``output/runs/<run_id>/output/video/<nombre>.mp4``
pero Northflank solo recibe SUCCESS y ``RunRecord`` no tiene metadata de
artefactos. ME40.9A añade una abstracción mínima de artefactos
(``src/application/artifacts.py``): ``ArtifactRecord`` + ``ArtifactStore`` +
``LocalArtifactStore`` que registra metadata local en ``<run_dir>/artifacts.json``
sin copiar ni mover el MP4 y sin tocar ``PipelineResult``/``RunRecord``/
``RunStorage``/API/frontend.

Tests (A-P), stdlib puro, sin pytest, sin HTTP, sin Gemini/SD15/Kokoro:

- A. ArtifactRecord serializable a dict + JSON.
- B. round-trip to_dict -> from_dict (y vía JSON).
- C. LocalArtifactStore publica un video existente.
- D. metadata persistida en artifacts.json (lista JSON).
- E. filename correcto.
- F. content_type video/mp4.
- G. size_bytes coincide con el archivo real.
- H. referencia relativa al run, nunca absoluta.
- I. traversal rechazado (fuera de run_dir).
- J. run_id inválido rechazado.
- K. archivo inexistente rechazado.
- L. list devuelve los artefactos registrados.
- M. get devuelve un artefacto por id (y None si falta).
- N. metadata corrupta manejada de forma segura.
- O. el MP4 no se modifica.
- P. no se realizan llamadas HTTP externas durante los tests.

Además: py_compile (archivos modificados) y git diff --check.

Uso:

    .venv\\Scripts\\python.exe tests/test_artifacts_me409a.py
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

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TEMP_ROOT = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "opencode" / "me409a"
TEST_RUNS = TEMP_ROOT / "tests_runs"

if TEST_RUNS.exists():
    shutil.rmtree(TEST_RUNS)
TEST_RUNS.mkdir(parents=True, exist_ok=True)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from application.artifacts import (  # noqa: E402
    ARTIFACTS_FILE,
    ArtifactRecord,
    ArtifactStore,
    ArtifactNotFoundError,
    ArtifactValidationError,
    LocalArtifactStore,
    content_type_for,
)

FAILURES: list[str] = []
NEXT_ID = 0


def check(ok: bool, label: str, detalle: str = "") -> None:
    global NEXT_ID
    NEXT_ID += 1
    tag = f"{label}"
    if ok:
        print(f"[OK] {tag}")
    else:
        print(f"[FAIL] {tag} {detalle}".rstrip())
        FAILURES.append(tag)


def check_raises(exc_tipo: type, fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except exc_tipo:
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"   (excepción distinta: {type(exc).__name__}: {exc})")
        return False
    return False


RUN_1 = "run-20260818-010001"
RUN_2 = "run-20260818-010002"
OUTSIDE_FILE = TEST_RUNS.parent / "outside_me409a.mp4"
OUTSIDE_FILE.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1024)

STORE = LocalArtifactStore(TEST_RUNS)


def run_dir(run_id: str) -> Path:
    return TEST_RUNS / run_id


def make_video(run_id: str, nombre: str = "video.mp4", payload: bytes = b"\x00\x00\x00\x18ftypmp42") -> Path:
    d = run_dir(run_id) / "output" / "video"
    d.mkdir(parents=True, exist_ok=True)
    p = d / nombre
    p.write_bytes(payload)
    return p


# ---------------------------------------------------------------------------
# A. ArtifactRecord serializable.
# ---------------------------------------------------------------------------

reg = ArtifactRecord(
    artifact_id="run-20260818-010001-video-abc",
    run_id=RUN_1,
    kind="video",
    filename="video.mp4",
    content_type="video/mp4",
    size_bytes=1234,
    reference="output/video/video.mp4",
    storage="local",
)
d = reg.to_dict()
check(
    isinstance(d, dict) and set(d) == {
        "artifact_id", "run_id", "kind", "filename",
        "content_type", "size_bytes", "reference", "storage",
    },
    "A1 ArtifactRecord.to_dict tiene los 8 campos",
)
try:
    json.dumps(d)
    ok = True
except (TypeError, ValueError):
    ok = False
check(ok, "A2 to_dict es serializable a JSON")

# ---------------------------------------------------------------------------
# B. Round-trip.
# ---------------------------------------------------------------------------

reconstruido = ArtifactRecord.from_dict(d)
check(reconstruido == reg, "B1 round-trip from_dict(to_dict) == original")
via_json = ArtifactRecord.from_dict(json.loads(json.dumps(d)))
check(via_json == reg, "B2 round-trip vía JSON == original")

# ---------------------------------------------------------------------------
# C. LocalArtifactStore publica un video existente.
# ---------------------------------------------------------------------------

video_1 = make_video(RUN_1, payload=b"MP40" + b"\x00" * 4096)
try:
    publicado = STORE.publish(RUN_1, video_1, kind="video")
    pub_ok = True
except Exception as exc:  # noqa: BLE001
    pub_ok = False
    print(f"   (publish falló: {type(exc).__name__}: {exc})")
check(pub_ok, "C1 publish video existente OK")
check(pub_ok and publicado.run_id == RUN_1, "C2 run_id conservado")
check(pub_ok and publicado.kind == "video", "C3 kind video")

# ---------------------------------------------------------------------------
# D. metadata artifacts.json creada.
# ---------------------------------------------------------------------------

artifacts_path = run_dir(RUN_1) / ARTIFACTS_FILE
check(artifacts_path.is_file(), "D1 artifacts.json creado en run_dir")
raw_ok = False
if artifacts_path.is_file():
    try:
        data = json.loads(artifacts_path.read_text(encoding="utf-8"))
        raw_ok = isinstance(data, list) and len(data) == 1
    except ValueError:
        raw_ok = False
check(raw_ok, "D2 artifacts.json es una lista JSON con el registro")

# ---------------------------------------------------------------------------
# E. filename correcto.
# ---------------------------------------------------------------------------

check(pub_ok and publicado.filename == "video.mp4", "E1 filename video.mp4")

# ---------------------------------------------------------------------------
# F. content_type video/mp4.
# ---------------------------------------------------------------------------

check(pub_ok and publicado.content_type == "video/mp4", "F1 content_type video/mp4")
check(content_type_for("clip.webm") == "video/webm", "F2 content_type webm")
check(content_type_for("desconocido.xyz") == "application/octet-stream", "F3 content_type default")

# ---------------------------------------------------------------------------
# G. size_bytes correcto.
# ---------------------------------------------------------------------------

check(
    pub_ok and publicado.size_bytes == video_1.stat().st_size,
    "G1 size_bytes coincide con el archivo real",
)

# ---------------------------------------------------------------------------
# H. referencia relativa, nunca absoluta.
# ---------------------------------------------------------------------------

check(
    pub_ok and publicado.reference == "output/video/video.mp4",
    "H1 referencia relativa al run",
)
check(
    pub_ok and not Path(publicado.reference).is_absolute(),
    "H2 referencia no es absoluta",
)
check(
    pub_ok and ".." not in Path(publicado.reference).parts,
    "H3 referencia no escapa (sin ..)",
)

# ---------------------------------------------------------------------------
# I. traversal rechazado.
# ---------------------------------------------------------------------------

# ruta absoluta FUERA del run.
check(
    check_raises(ArtifactValidationError, STORE.publish, RUN_1, OUTSIDE_FILE),
    "I1 ruta absoluta fuera de run_dir rechazada",
)
# ruta relativa con ".." que escapa.
check(
    check_raises(
        ArtifactValidationError,
        STORE.publish,
        RUN_1,
        Path("..") / ".." / "outside_me409a.mp4",
    ),
    "I2 ruta relativa con .. rechazada",
)
# ruta absoluta con .. dentro del propio run hacia afuera.
escape = run_dir(RUN_1).resolve() / ".." / "outside_me409a.mp4"
check(
    check_raises(ArtifactValidationError, STORE.publish, RUN_1, escape),
    "I3 ruta absoluta con .. que escapa rechazada",
)

# ---------------------------------------------------------------------------
# J. run_id inválido rechazado.
# ---------------------------------------------------------------------------

check(
    check_raises(ArtifactValidationError, STORE.publish, "run-mal", video_1),
    "J1 publish con run_id inválido rechazado",
)
check(
    check_raises(ArtifactValidationError, STORE.publish, "", video_1),
    "J2 publish con run_id vacío rechazado",
)
check(
    check_raises(ArtifactValidationError, STORE.list, "../escape"),
    "J3 list con run_id traversal rechazado",
)
check(
    check_raises(ArtifactValidationError, STORE.publish, "run-20260818-010001/../salir", video_1),
    "J4 publish con run_id traversal rechazado",
)

# ---------------------------------------------------------------------------
# K. archivo inexistente rechazado.
# ---------------------------------------------------------------------------

inexistente = make_video(RUN_1, nombre="nope.mp4")
inexistente.unlink()
check(
    check_raises(ArtifactNotFoundError, STORE.publish, RUN_1, inexistente),
    "K1 archivo inexistente rechazado",
)

# ---------------------------------------------------------------------------
# L. list devuelve artefactos.
# ---------------------------------------------------------------------------

video_2 = make_video(RUN_2, nombre="intro.mp4", payload=b"OTRO" + b"\x00" * 64)
publicado_2 = STORE.publish(RUN_2, video_2, kind="video")
lista_1 = STORE.list(RUN_1)
lista_2 = STORE.list(RUN_2)
check(lista_1 == [publicado], "L1 list(RUN_1) devuelve el artefacto publicado")
check(lista_2 == [publicado_2], "L2 list(RUN_2) devuelve el suyo (aislado por run)")
check(STORE.list("run-20260818-999999") == [], "L3 list de run sin metadata devuelve []")

# ---------------------------------------------------------------------------
# M. get devuelve artefacto.
# ---------------------------------------------------------------------------

obtenido = STORE.get(publicado.artifact_id)
check(obtenido == publicado, "M1 get(artifact_id) devuelve el artefacto")
check(STORE.get("no-existe-artifact") is None, "M2 get de id desconocido devuelve None")
check(STORE.get("") is None, "M3 get con id vacío devuelve None")

# ---------------------------------------------------------------------------
# N. metadata corrupta manejada de forma segura.
# ---------------------------------------------------------------------------

corrupto_run = "run-20260818-010003"
run_dir(corrupto_run).mkdir(parents=True, exist_ok=True)
(run_dir(corrupto_run) / ARTIFACTS_FILE).write_text("{no es json", encoding="utf-8")
check(STORE.list(corrupto_run) == [], "N1 artifacts.json corrupto -> lista vacía (sin crash)")
(run_dir(corrupto_run) / ARTIFACTS_FILE).write_text(
    json.dumps([{"artifact_id": 42, "size_bytes": "grande"}]),
    encoding="utf-8",
)
check(STORE.list(corrupto_run) == [], "N2 entradas con campos inválidos ignoradas")
(run_dir(corrupto_run) / ARTIFACTS_FILE).write_text(
    json.dumps([{"artifact_id": "x", "run_id": RUN_1, "kind": "video", "filename": "a.mp4",
                 "content_type": "video/mp4", "size_bytes": 1, "reference": "a.mp4",
                 "storage": "local"}, "basura"]),
    encoding="utf-8",
)
ok_n3 = True
try:
    lista_n3 = STORE.list(corrupto_run)
    ok_n3 = isinstance(lista_n3, list) and len(lista_n3) == 1
except Exception:  # noqa: BLE001
    ok_n3 = False
check(ok_n3, "N3 entradas válidas sobreviven y las inválidas se ignoran")
# publish sigue funcionando tras corrupción (rescribe metadata).
rec_n4 = STORE.publish(corrupto_run, make_video(corrupto_run, nombre="n4.mp4"))
check(
    len(STORE.list(corrupto_run)) == 2,
    "N4 publish regenera la metadata tras corrupción",
)

# ---------------------------------------------------------------------------
# O. el MP4 no se modifica.
# ---------------------------------------------------------------------------

mp4_antes = video_1.read_bytes()
STORE.publish(RUN_1, video_1, kind="video")
check(mp4_antes == video_1.read_bytes(), "O1 el MP4 no se modifica (bytes idénticos)")
check(video_1.stat().st_size == len(mp4_antes), "O2 tamaño del MP4 intacto")

# ---------------------------------------------------------------------------
# P. no se realizan llamadas HTTP externas durante los tests.
# ---------------------------------------------------------------------------

_orig_socket = socket.socket
_orig_urlopen = urllib.request.urlopen


def _blocked(*_args, **_kwargs):
    raise AssertionError("HTTP externo no permitido durante los tests ME40.9A")


socket.socket = _blocked
urllib.request.urlopen = _blocked
bloqueado_ok = True
try:
    STORE.list(RUN_1)
    STORE.get(publicado.artifact_id)
    make_video(RUN_1)
    STORE.publish(RUN_1, video_1, kind="video")
except Exception as exc:  # noqa: BLE001
    bloqueado_ok = False
    print(f"   (HTTP/socket no bloqueado: {type(exc).__name__}: {exc})")
finally:
    socket.socket = _orig_socket
    urllib.request.urlopen = _orig_urlopen
check(bloqueado_ok, "P1 sin HTTP externo durante los tests")
check(socket.socket is _orig_socket, "P2 socket restaurado tras el arnés")

# ---------------------------------------------------------------------------
# Validación del repositorio.
# ---------------------------------------------------------------------------

Q = []

Q.append(("Q1 py_compile artifacts.py", subprocess.run(
    [sys.executable, "-m", "py_compile", str(SRC / "application" / "artifacts.py")],
    capture_output=True, text=True, cwd=str(ROOT),
).returncode == 0))

diff = subprocess.run(
    ["git", "diff", "--check"], capture_output=True, text=True, cwd=str(ROOT)
)
Q.append(("Q2 git diff --check", diff.returncode == 0))
if diff.returncode != 0:
    print(diff.stdout)

for nombre, ok in Q:
    check(ok, nombre)

# ---------------------------------------------------------------------------

print()
if FAILURES:
    print(f"FALLOS ({len(FAILURES)}): {FAILURES}")
    print("RESULTADO: FAIL")
    sys.exit(1)
print("RESULTADO: PASS")
