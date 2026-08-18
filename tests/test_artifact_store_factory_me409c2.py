# -*- coding: utf-8 -*-
"""ME40.9C.2 - Tests de la factory de ArtifactStore y la integración del worker.

``build_artifact_store(runs_root)`` selecciona el backend por
``OBJECT_STORAGE_BACKEND``: ``local`` (LocalArtifactStore) o ``s3``
(S3CompatibleArtifactStore con Boto3S3Client). El worker outbound construye el
store con la factory y lo pasa a ``run_remote_cycle``; el modo local y
``process_one``/``run_cycle`` quedan intactos. Sin HTTP real (boto3 fake).

Tests (K-AB), stdlib puro, sin pytest:

- K. backend local selecciona LocalArtifactStore.
- L. backend s3 selecciona S3CompatibleArtifactStore.
- M. backend desconocido falla claramente.
- N. configuración S3 incompleta falla claramente.
- O. credenciales nunca aparecen en ArtifactRecord.
- P. credenciales nunca aparecen en logs.
- Q. no URLs públicas generadas.
- R. modo local sigue funcionando.
- S. run_remote_cycle acepta el store (y backward compatibility sin store).
- T. no cambia contrato WorkerApiClient.
- U. no cambia PipelineResult.
- V. no cambia RunRecord.
- W. no cambia API.
- X. no cambia frontend.
- Y. no modifica requirements-api.txt (ni Dockerfile).
- Z. no realiza HTTP externo durante tests.
- AA. py_compile.
- AB. git diff --check y alcance de archivos.

Uso:

    .venv\\Scripts\\python.exe tests/test_artifact_store_factory_me409c2.py
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import types
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
TEMP_ROOT = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "opencode" / "me409c2"
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

# --- Módulo boto3 FAKE (construcción de la factory sin red real) -----------


class FakeRawS3:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.objects = {}

    def put_object(self, **kw):
        self.calls.append(("put_object", kw))
        self.objects[kw["Key"]] = {
            "content_type": kw.get("ContentType"),
            "metadata": dict(kw.get("Metadata") or {}),
        }
        return {}

    def get_paginator(self, name):
        return _FakePaginator(self)

    def head_object(self, **kw):
        obj = self.objects.get(kw["Key"], {})
        return {"Metadata": obj.get("metadata") or {}}


class _FakePaginator:
    def __init__(self, raw):
        self._raw = raw

    def paginate(self, **kw):
        prefix = kw.get("Prefix", "")
        keys = [k for k in sorted(self._raw.objects) if k.startswith(prefix)]
        pages = [{"Contents": [{"Key": k} for k in keys]}] if keys else [{}]
        return iter(pages)


def _install_fake_boto3():
    mod = types.ModuleType("boto3")

    def client(service_name, **kwargs):
        assert service_name == "s3"
        return FakeRawS3(**kwargs)

    mod.client = client
    sys.modules["boto3"] = mod


_install_fake_boto3()

import run_worker  # noqa: E402
from application.artifact_store_factory import (  # noqa: E402
    ArtifactStoreConfigError,
    BACKEND_LOCAL,
    BACKEND_S3,
    Boto3S3Client,
    build_artifact_store,
    OBJECT_STORAGE_BACKEND,
    OBJECT_STORAGE_BUCKET,
    OBJECT_STORAGE_ENDPOINT,
    OBJECT_STORAGE_REGION,
    OBJECT_STORAGE_ACCESS_KEY,
    OBJECT_STORAGE_SECRET_KEY,
)
from application.artifacts import LocalArtifactStore, ArtifactStore  # noqa: E402
from application.s3_artifacts import S3CompatibleArtifactStore  # noqa: E402
from pipeline import RunContext  # noqa: E402
from pipeline.lifecycle import RunRecord, RunStatus, write_run_record  # noqa: E402
from pipeline.models import PipelineResult  # noqa: E402

# Bloqueo de red desde el inicio: ningún check puede abrir sockets HTTP.
_orig_socket = socket.socket
_orig_urlopen = urllib.request.urlopen


def _blocked(*_args, **_kwargs):
    raise AssertionError("HTTP externo no permitido durante los tests ME40.9C.2")


socket.socket = _blocked
urllib.request.urlopen = _blocked

FAILURES: list[str] = []


def check(ok: bool, label: str, detalle: str = "") -> None:
    if ok:
        print(f"[OK] {label}")
    else:
        print(f"[FAIL] {label} {detalle}".rstrip())
        FAILURES.append(label)


def check_raises(exc_tipo: type, fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except exc_tipo:
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"   (excepción distinta: {type(exc).__name__}: {exc})")
        return False
    return False


BUCKET = "ai-shorts-factory-media"
ENDPOINT = "https://fake-s3.example.invalid"
ACCESS_KEY = "AKIA_FAKE_FACTORY_ME409C2"
SECRET_KEY = "s3cr3t0-fake-factory-me409c2"
VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096
RUN_1 = "run-20260818-050001"

_ENV_VARS = [
    OBJECT_STORAGE_BACKEND,
    OBJECT_STORAGE_ENDPOINT,
    OBJECT_STORAGE_BUCKET,
    OBJECT_STORAGE_REGION,
    OBJECT_STORAGE_ACCESS_KEY,
    OBJECT_STORAGE_SECRET_KEY,
]
_ENV_BACKUP = {v: os.environ.get(v) for v in _ENV_VARS}


def _set_env(backend: str, *, completo: bool = True) -> None:
    os.environ[OBJECT_STORAGE_BACKEND] = backend
    os.environ[OBJECT_STORAGE_ENDPOINT] = ENDPOINT if completo else ""
    os.environ[OBJECT_STORAGE_BUCKET] = BUCKET if completo else ""
    os.environ[OBJECT_STORAGE_REGION] = "auto" if completo else ""
    os.environ[OBJECT_STORAGE_ACCESS_KEY] = ACCESS_KEY if completo else ""
    os.environ[OBJECT_STORAGE_SECRET_KEY] = SECRET_KEY if completo else ""


def _clear_env() -> None:
    for v in _ENV_VARS:
        os.environ.pop(v, None)


def run_dir(run_id: str) -> Path:
    return TEST_RUNS / run_id


def make_video(run_id: str, nombre: str = "video.mp4") -> Path:
    d = run_dir(run_id) / "output" / "video"
    d.mkdir(parents=True, exist_ok=True)
    p = d / nombre
    p.write_bytes(VIDEO_BYTES)
    return p


class FakeClient:
    def __init__(self, jobs=None, timeline=None):
        self._jobs = list(jobs or [])
        self.completed = []
        self.timeline = timeline

    def next_job(self):
        if self._jobs:
            return self._jobs.pop(0)
        return None

    def heartbeat(self, run_id):
        return 200

    def complete(self, run_id, status, error=None):
        self.completed.append((run_id, status, error))
        return 200


def write_success(context: RunContext) -> Path:
    context.prepare()
    write_run_record(
        context.run_dir,
        RunRecord(run_id=context.run_id, status=RunStatus.SUCCESS),
    )
    video_dir = context.run_dir / "output" / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "video.mp4").write_bytes(VIDEO_BYTES)
    return context.run_dir


def make_executor(setup):
    def executor(context):
        setup(context)
        return {"ok": True}

    return executor


def job_payload(run_id: str) -> dict:
    return {"run_id": run_id, "topic": "tema", "offline": True, "project_id": None}


# ---------------------------------------------------------------------------
# K. backend local selecciona LocalArtifactStore.
# ---------------------------------------------------------------------------

_set_env("local")
store_k = build_artifact_store(TEST_RUNS)
check(isinstance(store_k, LocalArtifactStore), "K1 backend 'local' -> LocalArtifactStore")
_set_env("")
check(isinstance(build_artifact_store(TEST_RUNS), LocalArtifactStore), "K2 backend vacío -> LocalArtifactStore")

# ---------------------------------------------------------------------------
# L. backend s3 selecciona S3CompatibleArtifactStore.
# ---------------------------------------------------------------------------

_set_env("s3")
store_l = build_artifact_store(TEST_RUNS)
check(isinstance(store_l, S3CompatibleArtifactStore), "L1 backend 's3' -> S3CompatibleArtifactStore")
check(isinstance(store_l._client, Boto3S3Client), "L2 cliente Boto3S3Client inyectado")
check(store_l._bucket == BUCKET and store_l._endpoint == ENDPOINT, "L3 bucket/endpoint de configuración")
check(store_l._region == "auto", "L4 region por defecto 'auto'")

# ---------------------------------------------------------------------------
# M. backend desconocido falla claramente.
# ---------------------------------------------------------------------------

_set_env("azure")
check(
    check_raises(ArtifactStoreConfigError, build_artifact_store, TEST_RUNS),
    "M1 backend desconocido -> ArtifactStoreConfigError",
)
try:
    build_artifact_store(TEST_RUNS)
except ArtifactStoreConfigError as exc:
    check("azure" in str(exc) and "local" in str(exc) and "s3" in str(exc), "M2 mensaje claro con backends válidos")
else:
    check(False, "M2 mensaje claro con backends válidos")

# ---------------------------------------------------------------------------
# N. configuración S3 incompleta falla claramente.
# ---------------------------------------------------------------------------

_set_env("s3")
os.environ[OBJECT_STORAGE_ENDPOINT] = ""
check(
    check_raises(ArtifactStoreConfigError, build_artifact_store, TEST_RUNS),
    "N1 falta ENDPOINT -> error",
)
try:
    build_artifact_store(TEST_RUNS)
except ArtifactStoreConfigError as exc:
    check("OBJECT_STORAGE_ENDPOINT" in str(exc), "N2 mensaje menciona la variable que falta")
else:
    check(False, "N2 mensaje menciona la variable que falta")
os.environ[OBJECT_STORAGE_ENDPOINT] = ENDPOINT
os.environ[OBJECT_STORAGE_BUCKET] = ""
check(
    check_raises(ArtifactStoreConfigError, build_artifact_store, TEST_RUNS),
    "N3 falta BUCKET -> error",
)
os.environ[OBJECT_STORAGE_BUCKET] = BUCKET
os.environ[OBJECT_STORAGE_ACCESS_KEY] = ""
check(
    check_raises(ArtifactStoreConfigError, build_artifact_store, TEST_RUNS),
    "N4 falta ACCESS_KEY -> error",
)
os.environ[OBJECT_STORAGE_ACCESS_KEY] = ACCESS_KEY
os.environ[OBJECT_STORAGE_SECRET_KEY] = ""
check(
    check_raises(ArtifactStoreConfigError, build_artifact_store, TEST_RUNS),
    "N5 falta SECRET_KEY -> error",
)

# ---------------------------------------------------------------------------
# O. credenciales nunca aparecen en ArtifactRecord.
# ---------------------------------------------------------------------------

_set_env("s3")
store_o = build_artifact_store(TEST_RUNS)
rec_o = store_o.publish(RUN_1, make_video(RUN_1), kind="video")
check(
    ACCESS_KEY not in rec_o.to_dict().values() and SECRET_KEY not in rec_o.to_dict().values(),
    "O1 sin credenciales en ArtifactRecord",
)
check("access_key" not in json.dumps(rec_o.to_dict()) and "secret_key" not in json.dumps(rec_o.to_dict()),
      "O2 sin claves access_key/secret_key serializadas")

# ---------------------------------------------------------------------------
# P. credenciales nunca aparecen en logs.
# ---------------------------------------------------------------------------

buf = io.StringIO()
handler = logging.StreamHandler(buf)
root_logger = logging.getLogger()
nivel_previo = root_logger.level
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(handler)
_set_env("s3")
try:
    store_p = build_artifact_store(TEST_RUNS)
    store_p.publish(RUN_1, make_video(RUN_1), kind="video")
    store_p.list(RUN_1)
except Exception as exc:  # noqa: BLE001
    print(f"   (publish/list falló: {type(exc).__name__}: {exc})")
finally:
    root_logger.removeHandler(handler)
    root_logger.setLevel(nivel_previo)
texto_log = buf.getvalue()
check(
    ACCESS_KEY not in texto_log and SECRET_KEY not in texto_log,
    "P1 credenciales nunca aparecen en logs",
)

# ---------------------------------------------------------------------------
# Q. no URLs públicas generadas.
# ---------------------------------------------------------------------------

check(
    rec_o.reference.startswith("runs/") and "http" not in rec_o.reference.lower(),
    "Q1 reference es object key, sin URLs públicas",
)

# ---------------------------------------------------------------------------
# R. modo local sigue funcionando.
# ---------------------------------------------------------------------------

_set_env("local")
store_r = build_artifact_store(TEST_RUNS)
run_id_r = "run-20260818-050002"
rec_r = store_r.publish(run_id_r, make_video(run_id_r), kind="video")
artifacts_r = run_dir(run_id_r) / "artifacts.json"
check(rec_r.kind == "video", "R1 publish local OK")
check(artifacts_r.is_file(), "R2 artifacts.json creado en el run local")
check(store_r.list(run_id_r) == [rec_r], "R3 list local OK")

# ---------------------------------------------------------------------------
# S. run_remote_cycle acepta el store (+ backward compatibility sin store).
# ---------------------------------------------------------------------------

check(run_worker.build_artifact_store is build_artifact_store, "S1 run_worker usa la factory importada")

# S2: store explícito (local) pasado a run_remote_cycle.
run_id_s = "run-20260818-050003"
ctx_s = RunContext(run_id=run_id_s, runs_root=TEST_RUNS)
client_s = FakeClient([job_payload(run_id_s)])
run_worker.run_remote_cycle(
    client_s, TEST_RUNS, executor=make_executor(write_success), artifact_store=store_r
)
check(
    client_s.completed == [(run_id_s, "SUCCESS", None)],
    "S2 run_remote_cycle acepta artifact_store y completa SUCCESS",
)
check(
    (run_dir(run_id_s) / "artifacts.json").is_file(),
    "S3 publish hecho en el store pasado",
)

# S4: sin store explícito -> backward compatibility (LocalArtifactStore).
run_id_bc = "run-20260818-050004"
client_bc = FakeClient([job_payload(run_id_bc)])
run_worker.run_remote_cycle(
    client_bc, TEST_RUNS, executor=make_executor(write_success)
)
check(
    client_bc.completed == [(run_id_bc, "SUCCESS", None)]
    and (run_dir(run_id_bc) / "artifacts.json").is_file(),
    "S4 sin artifact_store sigue usando LocalArtifactStore (backward compatible)",
)

# ---------------------------------------------------------------------------
# T. no cambia contrato WorkerApiClient.
# ---------------------------------------------------------------------------

check(
    all(hasattr(run_worker.WorkerApiClient, m) for m in ("next_job", "heartbeat", "progress", "complete")),
    "T1 WorkerApiClient conserva el contrato outbound",
)

# ---------------------------------------------------------------------------
# U. no cambia PipelineResult.
# ---------------------------------------------------------------------------

check(
    hasattr(PipelineResult, "__dataclass_fields__")
    and "success" in PipelineResult.__dataclass_fields__
    and "video_path" in PipelineResult.__dataclass_fields__,
    "U1 PipelineResult intacto (dataclass con success/video_path)",
)
fuente_worker = (SCRIPTS / "run_worker.py").read_text(encoding="utf-8")
check("PipelineResult" not in fuente_worker, "U2 run_worker no toca PipelineResult")

# ---------------------------------------------------------------------------
# V. no cambia RunRecord.
# ---------------------------------------------------------------------------

run_record_v = RunRecord(run_id=RUN_1, status=RunStatus.SUCCESS)
campos_v = set(run_record_v.to_dict())
check(
    {"run_id", "status", "error", "quality_passed"}.issubset(campos_v),
    "V1 RunRecord conserva sus campos",
)

# ---------------------------------------------------------------------------
# W. no cambia API.  X. no cambia frontend.  Y. requirements-api/Dockerfile.
# ---------------------------------------------------------------------------

st = subprocess.run(
    ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=str(ROOT)
).stdout.splitlines()
todas = [l[3:].replace("/", "\\") for l in st]
check(
    not any(f.startswith("src\\api") for f in todas),
    "W1 no hay cambios en src/api",
)
check(
    not any(f.startswith("web") for f in todas),
    "X1 no hay cambios en frontend",
)
check(
    not any("requirements-api" in f or "Dockerfile" in f or f.startswith("src\\pipeline") for f in todas),
    "Y1 no cambia requirements-api.txt ni Dockerfile ni src/pipeline",
)
check(
    not any(f.startswith("src\\application\\artifacts") or f.startswith("src\\application\\s3_artifacts") for f in todas),
    "Y2 no cambian artifacts.py ni s3_artifacts.py",
)

# ---------------------------------------------------------------------------
# Z. no realiza HTTP externo durante tests.
# ---------------------------------------------------------------------------

z_ok = True
try:
    build_artifact_store(TEST_RUNS)
    store_r.list(run_id_r)
except Exception as exc:  # noqa: BLE001
    z_ok = False
    print(f"   (HTTP/socket no bloqueado: {type(exc).__name__}: {exc})")
check(z_ok, "Z1 sin HTTP externo durante todos los checks")

# ---------------------------------------------------------------------------
# AA. py_compile.  AB. git diff --check.
# ---------------------------------------------------------------------------

AA_checks = []
for nombre, archivo in (
    ("AA1 py_compile run_worker.py", SCRIPTS / "run_worker.py"),
    ("AA2 py_compile artifact_store_factory.py", SRC / "application" / "artifact_store_factory.py"),
    ("AA3 py_compile s3_artifacts.py", SRC / "application" / "s3_artifacts.py"),
):
    AA_checks.append((nombre, subprocess.run(
        [sys.executable, "-m", "py_compile", str(archivo)],
        capture_output=True, text=True, cwd=str(ROOT),
    ).returncode == 0))
for nombre, ok in AA_checks:
    check(ok, nombre)

diff = subprocess.run(
    ["git", "diff", "--check"], capture_output=True, text=True, cwd=str(ROOT)
)
check(diff.returncode == 0, "AB1 git diff --check")
if diff.returncode != 0:
    print(diff.stdout)

# ---------------------------------------------------------------------------
# Restauración de entorno y bloqueo de red.
# ---------------------------------------------------------------------------

for v, valor in _ENV_BACKUP.items():
    if valor is None:
        os.environ.pop(v, None)
    else:
        os.environ[v] = valor
socket.socket = _orig_socket
urllib.request.urlopen = _orig_urlopen
check(socket.socket is _orig_socket, "Z2 socket restaurado tras el arnés")

print()
if FAILURES:
    print(f"FALLOS ({len(FAILURES)}): {FAILURES}")
    print("RESULTADO: FAIL")
    sys.exit(1)
print("RESULTADO: PASS")