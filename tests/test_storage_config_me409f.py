"""ME40.9F - Paridad operativa de configuración API/worker (OBJECT_STORAGE_*).

Valida:
- A. ``describe_storage_config``: huella pura de env, sin secretos.
- B. ``create_app`` hereda el ``.env`` raíz; el env explícito gana
  (``override=False``).
- C. Coherencia ``/health`` <-> entrega real de ``/video`` (local inline y
  remoto 307).
- D. Verificación cruzada del worker: mismatch -> fail-fast antes de reclamar;
  API caída o sin huella -> continúa (contrato ME40.4 intacto).
- E. Regresión legacy (me404 con sus transitivos, me409a/c2/d34/e).

Nunca se exponen credenciales en huellas, logs ni respuestas.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
TEMP_ROOT = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "opencode" / "me409f"
TEST_RUNS = TEMP_ROOT / "runs"

if TEST_RUNS.exists():
    shutil.rmtree(TEST_RUNS)
TEST_RUNS.mkdir(parents=True, exist_ok=True)

os.environ["PIPELINE_RUNS_ROOT"] = str(TEST_RUNS)
os.environ["WORKER_TOKEN"] = "me409f-token"
#: Los tests validan la entrega LOCAL; el env explícito gana sobre el .env.
os.environ["OBJECT_STORAGE_BACKEND"] = "local"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import config as config_mod  # noqa: E402
from application.artifact_access import ArtifactAccess  # noqa: E402
from application.artifact_store_factory import (  # noqa: E402
    ArtifactStoreConfigError,
    build_artifact_store,
    describe_storage_config,
)
from application.artifacts import (  # noqa: E402
    ArtifactRecord,
    ArtifactStore,
    LocalArtifactStore,
)
from application.s3_artifacts import S3CompatibleArtifactStore  # noqa: E402

_ENV_VARS = (
    "OBJECT_STORAGE_BACKEND",
    "OBJECT_STORAGE_ENDPOINT",
    "OBJECT_STORAGE_BUCKET",
    "OBJECT_STORAGE_REGION",
    "OBJECT_STORAGE_ACCESS_KEY",
    "OBJECT_STORAGE_SECRET_KEY",
)

_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 48

_checks_ok = True


def check(ok: bool, label: str, detail: str = "") -> bool:
    global _checks_ok
    if not ok:
        _checks_ok = False
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" | {detail}" if detail and not ok else ""))
    return ok


@contextmanager
def env_scope(**valores: str):
    """Fija variables de entorno temporalmente y restaura el estado previo."""
    guardadas = {name: os.environ.get(name) for name in valores}
    try:
        for name, value in valores.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, old in guardadas.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old


# ---------------------------------------------------------------------------
# A. describe_storage_config: lectura pura de env, sin secretos.
# ---------------------------------------------------------------------------

with env_scope(**{v: None for v in _ENV_VARS}):
    check(
        describe_storage_config() == {"backend": "local"},
        "A1 sin configuración -> huella local exacta",
        str(describe_storage_config()),
    )

_SECRETO_AK = "AKIA-FAKE-123456"
_SECRETO_SK = "supersecreto-no-filtrar"
_ENDPOINT_S3 = "https://acct123.r2.cloudflarestorage.com:8443/ruta?x=1"

with env_scope(
    OBJECT_STORAGE_BACKEND="s3",
    OBJECT_STORAGE_ENDPOINT=_ENDPOINT_S3,
    OBJECT_STORAGE_BUCKET="media-bucket",
    OBJECT_STORAGE_REGION="auto",
    OBJECT_STORAGE_ACCESS_KEY=_SECRETO_AK,
    OBJECT_STORAGE_SECRET_KEY=_SECRETO_SK,
):
    huella = describe_storage_config()
    check(
        huella == {
            "backend": "s3",
            "bucket": "media-bucket",
            "endpoint_host": "acct123.r2.cloudflarestorage.com",
        },
        "A2 huella s3: backend/bucket/host del endpoint (sin puerto/path/query)",
        str(huella),
    )
    plano = json.dumps(huella, ensure_ascii=False)
    check(
        _SECRETO_AK not in plano
        and _SECRETO_SK not in plano
        and "https" not in plano
        and "@" not in plano
        and "8443" not in plano
        and "ruta" not in plano,
        "A3 la huella no contiene credenciales ni URL completa",
        plano,
    )
    try:
        store_a3 = build_artifact_store(TEST_RUNS)
    except ArtifactStoreConfigError as exc:
        check(True, f"A3b build s3 sin boto3 falla claro ({exc})")
    else:
        check(
            isinstance(store_a3, S3CompatibleArtifactStore),
            "A3b build s3 construye el store (boto3 presente)",
        )

with env_scope(OBJECT_STORAGE_BACKEND="gcs"):
    check(
        describe_storage_config() == {"backend": "gcs"},
        "A4 backend desconocido se reporta tal cual (sin crash)",
    )

with env_scope(
    OBJECT_STORAGE_BACKEND="s3",
    OBJECT_STORAGE_ENDPOINT="host-sin-esquema.example",
    OBJECT_STORAGE_BUCKET="b",
):
    check(
        describe_storage_config() == {"backend": "s3", "bucket": "b"},
        "A5 endpoint sin esquema: host omitido, sin crash",
        str(describe_storage_config()),
    )

# ---------------------------------------------------------------------------
# B. .env heredado vs env explícito (override=False).
# ---------------------------------------------------------------------------

from api.app import _build_artifact_access  # noqa: E402

_env_file_real = config_mod.DEFAULT_ENV_FILE
_env_tmp = TEMP_ROOT / "fake.env"
_env_tmp.write_text(
    "\n".join(
        [
            "OBJECT_STORAGE_BACKEND=s3",
            f"OBJECT_STORAGE_ENDPOINT={_ENDPOINT_S3}",
            "OBJECT_STORAGE_BUCKET=env-bucket",
            "OBJECT_STORAGE_REGION=auto",
            f"OBJECT_STORAGE_ACCESS_KEY={_SECRETO_AK}",
            f"OBJECT_STORAGE_SECRET_KEY={_SECRETO_SK}",
        ]
    )
    + "\n",
    encoding="utf-8",
)

try:
    config_mod.DEFAULT_ENV_FILE = _env_tmp
    with env_scope(**{v: None for v in _ENV_VARS}):
        access_env = _build_artifact_access(None)
        check(
            isinstance(access_env.store, S3CompatibleArtifactStore),
            "B1 create_app hereda backend s3 del .env raíz (paridad con worker)",
        )
        check(
            access_env.store.describe() == describe_storage_config(),
            "B2 la huella del store coincide con la declarada en env",
            f"{access_env.store.describe()} vs {describe_storage_config()}",
        )

        os.environ["OBJECT_STORAGE_BACKEND"] = "local"
        access_local = _build_artifact_access(None)
        check(
            isinstance(access_local.store, LocalArtifactStore),
            "B3 el env explícito del proceso gana sobre el .env (override=False)",
        )
finally:
    config_mod.DEFAULT_ENV_FILE = _env_file_real
    os.environ["OBJECT_STORAGE_BACKEND"] = "local"

# ---------------------------------------------------------------------------
# C. /health <-> /video coherentes (local inline y remoto 307).
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402


def _publicar_video(runs_root: Path, run_id: str) -> None:
    video_dir = runs_root / run_id / "output" / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "video.mp4").write_bytes(_MP4_BYTES)
    store = LocalArtifactStore(runs_root)
    store.publish(run_id, video_dir / "video.mp4", kind="video")


_RUN_C1 = "run-20260821-210001"
_runs_c1 = TEMP_ROOT / "c_local"
_publicar_video(_runs_c1, _RUN_C1)

app_c1 = create_app(runs_root=_runs_c1)
with TestClient(app_c1, follow_redirects=False) as c:
    r = c.get("/api/v1/health")
    cuerpo = r.json()
    check(
        r.status_code == 200
        and cuerpo.get("status") == "ok"
        and cuerpo.get("artifacts") == {"backend": "local"},
        "C1 /health expone huella local aditiva",
        str(cuerpo),
    )
    r = c.get(f"/api/v1/runs/{_RUN_C1}/video")
    check(
        r.status_code == 200
        and r.headers.get("content-type") == "video/mp4"
        and r.content == _MP4_BYTES
        and "attachment" not in r.headers.get("content-disposition", ""),
        "C2 /video local sigue siendo 200 inline con el contenido exacto",
        f"status={r.status_code} ct={r.headers.get('content-type')}",
    )


class FakeRemoteStore(ArtifactStore):
    """Store remoto falso: presign + huella s3 (hereda defaults de la ABC)."""

    def __init__(self) -> None:
        self.records: dict[str, list[ArtifactRecord]] = {}

    def publish(self, run_id, source, *, kind="video"):
        raise NotImplementedError

    def list(self, run_id):
        return list(self.records.get(run_id, []))

    def get(self, artifact_id):
        for records in self.records.values():
            for record in records:
                if record.artifact_id == artifact_id:
                    return record
        return None

    def get_temporary_url(self, artifact_id, expires_in):
        return f"https://signed.example.invalid/{artifact_id}?X-Amz-Expires={expires_in}"

    def describe(self):
        return {
            "backend": "s3",
            "bucket": "fake-bucket",
            "endpoint_host": "fake.example",
        }


_RUN_C2 = "run-20260821-210002"
(TEMP_ROOT / _RUN_C2).mkdir(parents=True, exist_ok=True)
_record_c2 = ArtifactRecord(
    artifact_id=f"{_RUN_C2}-video-0000000000000",
    run_id=_RUN_C2,
    kind="video",
    filename="video.mp4",
    content_type="video/mp4",
    size_bytes=len(_MP4_BYTES),
    reference="output/video/video.mp4",
    storage="s3-compatible",
)
_fake_store = FakeRemoteStore()
_fake_store.records[_RUN_C2] = [_record_c2]

app_c2 = create_app(runs_root=TEMP_ROOT, artifact_access=ArtifactAccess(_fake_store))
with TestClient(app_c2, follow_redirects=False) as c:
    r = c.get("/api/v1/health")
    check(
        r.status_code == 200
        and r.json().get("artifacts") == _fake_store.describe(),
        "C3 /health refleja el acceso REAL inyectado (no solo el env)",
        str(r.json()),
    )
    r = c.get(f"/api/v1/runs/{_RUN_C2}/video")
    check(
        r.status_code == 307
        and r.headers.get("location", "").startswith("https://signed.example.invalid/"),
        "C4 /video remoto sigue siendo 307 a URL firmada",
        f"status={r.status_code} loc={r.headers.get('location')}",
    )

# ---------------------------------------------------------------------------
# D. Verificación cruzada del worker (fail-fast vs continuar).
# ---------------------------------------------------------------------------

import run_worker  # noqa: E402


class FakeHealthClient:
    """Sustituto de WorkerApiClient para observar la decisión de arranque."""

    instancias: list["FakeHealthClient"] = []

    def __init__(self, api_url: str, token: str, worker_id: str) -> None:
        self.api_url = api_url
        self.next_job_calls = 0
        FakeHealthClient.instancias.append(self)

    def fetch_health(self) -> dict:
        if FakeHealthClient.salud is None:
            raise run_worker.WorkerApiError("API caída (simulada)")
        return FakeHealthClient.salud

    def next_job(self):
        self.next_job_calls += 1
        return None


FakeHealthClient.salud: dict | None = None

_args_once = argparse.Namespace(once=True, interval=0.0)


def _ejecutar_run_remote(salud: dict | None) -> tuple[int, int, str]:
    FakeHealthClient.salud = salud
    FakeHealthClient.instancias.clear()
    original = run_worker.WorkerApiClient
    run_worker.WorkerApiClient = FakeHealthClient
    capturador = _CapturadorLogs()
    raiz = logging.getLogger()
    raiz.addHandler(capturador)
    try:
        rc = run_worker.run_remote("http://localhost:9", TEST_RUNS, _args_once)
    finally:
        raiz.removeHandler(capturador)
        run_worker.WorkerApiClient = original
    llamadas = sum(i.next_job_calls for i in FakeHealthClient.instancias)
    return rc, llamadas, capturador.texto()


class _CapturadorLogs(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self._buffer: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self._buffer.append(record.getMessage())

    def texto(self) -> str:
        return "\n".join(self._buffer)


rc, llamadas, _ = _ejecutar_run_remote({"status": "ok", "artifacts": {"backend": "local"}})
check(rc == 0 and llamadas == 1, "D1 huella idéntica -> continúa y reclama jobs", f"rc={rc} llamadas={llamadas}")

rc, llamadas, _ = _ejecutar_run_remote(
    {"status": "ok", "artifacts": {"backend": "s3", "bucket": "otro"}}
)
check(rc == 1 and llamadas == 0, "D2 mismatch -> exit 1 ANTES de reclamar jobs", f"rc={rc} llamadas={llamadas}")

rc, llamadas, _ = _ejecutar_run_remote({"status": "ok"})
check(rc == 0 and llamadas == 1, "D3 API sin huella (versión previa) -> continúa", f"rc={rc} llamadas={llamadas}")

rc, llamadas, _ = _ejecutar_run_remote(None)
check(rc == 0 and llamadas == 1, "D4 API caída -> warning y continúa (Q1 intacto)", f"rc={rc} llamadas={llamadas}")

check(
    run_worker.storage_config_mismatch(
        {"backend": "local"},
        {"artifacts": {"backend": "local", "bucket": "extra-api"}},
    )
    is False,
    "D5 claves extra en la API no son mismatch (rolling upgrade)",
)
check(
    run_worker.storage_config_mismatch(
        {"backend": "s3", "bucket": "a"},
        {"artifacts": {"backend": "s3", "bucket": "b"}},
    )
    is True,
    "D6 bucket distinto -> mismatch",
)

_SECRETO_LOG = "AKIA-LOG-LEAK-999"
with env_scope(
    OBJECT_STORAGE_BACKEND="s3",
    OBJECT_STORAGE_ENDPOINT="https://leak.example",
    OBJECT_STORAGE_BUCKET="leak-bucket",
    OBJECT_STORAGE_REGION="auto",
    OBJECT_STORAGE_ACCESS_KEY=_SECRETO_LOG,
    OBJECT_STORAGE_SECRET_KEY="sk-log-leak",
):
    class FakeLeakClient(FakeHealthClient):
        def fetch_health(self) -> dict:
            return {"status": "ok"}  # API sin huella: continúa

    FakeHealthClient.instancias.clear()
    original = run_worker.WorkerApiClient
    run_worker.WorkerApiClient = FakeLeakClient
    capturador = _CapturadorLogs()
    raiz = logging.getLogger()
    raiz.addHandler(capturador)
    try:
        rc = run_worker.run_remote("http://localhost:9", TEST_RUNS, _args_once)
    finally:
        raiz.removeHandler(capturador)
        run_worker.WorkerApiClient = original
    texto_logs = capturador.texto()
    check(
        rc == 0 and _SECRETO_LOG not in texto_logs and "sk-log-leak" not in texto_logs,
        "D7 ningún secreto aparece en los logs del worker",
        texto_logs[-300:],
    )

# ---------------------------------------------------------------------------
# E. Regresión legacy (subprocesos; me404 cubre me402/me403 transitivos).
# ---------------------------------------------------------------------------

def _regresion(ruta: Path, label: str, timeout: int = 900) -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tests" / ruta)],
        capture_output=True, text=True, timeout=timeout, cwd=str(ROOT),
    )
    cola = (proc.stdout + proc.stderr)[-400:]
    check(
        proc.returncode == 0 and "RESULTADO: PASS" in proc.stdout,
        label,
        f"rc={proc.returncode} {cola}",
    )


_regresion(Path("test_worker_me404.py"), "E1 ME40.4 (+ME40.2/ME40.3 transitivos) PASS", timeout=900)
_regresion(Path("test_local_video_delivery_me409e.py"), "E2 ME40.9E (entrega local) PASS", timeout=600)
_regresion(Path("test_artifact_store_factory_me409c2.py"), "E3 ME40.9C.2 (factory) PASS", timeout=600)
_regresion(Path("test_artifacts_me409a.py"), "E4 ME40.9A (dominio artifacts) PASS", timeout=600)
_regresion(Path("test_video_delivery_http_me409d34.py"), "E5 ME40.9D.3.4 (contrato HTTP) PASS", timeout=600)

# ---------------------------------------------------------------------------
# py_compile + git diff --check.
# ---------------------------------------------------------------------------

proc = subprocess.run(
    [sys.executable, "-m", "py_compile",
     str(SRC / "api" / "app.py"),
     str(SRC / "api" / "models.py"),
     str(SRC / "application" / "artifacts.py"),
     str(SRC / "application" / "artifact_access.py"),
     str(SRC / "application" / "artifact_store_factory.py"),
     str(SRC / "application" / "s3_artifacts.py"),
     str(SCRIPTS / "run_worker.py")],
    capture_output=True, text=True,
)
check(proc.returncode == 0, "F1 py_compile módulos ME40.9F", proc.stderr[-300:])

diff = subprocess.run(["git", "diff", "--check"], capture_output=True, text=True, cwd=str(ROOT))
check(diff.returncode == 0, "F2 git diff --check", diff.stdout[-200:])

print()
print("RESULTADO: PASS" if _checks_ok else "RESULTADO: FAIL")
sys.exit(0 if _checks_ok else 1)
