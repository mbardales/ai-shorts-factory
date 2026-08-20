# -*- coding: utf-8 -*-
"""ME40.9C.2 - Tests del adaptador Boto3S3Client (parte A-J).

ME40.9C.1 definió S3CompatibleArtifactStore con un cliente inyectable;
ME40.9C.2 añade ``Boto3S3Client`` (adaptador real sobre boto3, import
perezoso) en ``src/application/artifact_store_factory.py`` y la factory
``build_artifact_store``. Este arnés cubre el cliente boto3 y su integración
con S3CompatibleArtifactStore usando un módulo ``boto3`` FAKE (sin red).

Tests (A-J), stdlib puro, sin pytest, sin HTTP real, sin boto3 real:

- A. Boto3S3Client construction.
- B. endpoint correcto.
- C. region correcto.
- D. credentials no expuestas.
- E. put_object traduce correctamente.
- F. content_type correcto.
- G. metadata correcta.
- H. list_objects traduce correctamente (list + head).
- I. metadata round-trip.
- J. S3CompatibleArtifactStore funciona con Boto3S3Client.

Uso:

    .venv\\Scripts\\python.exe tests/test_s3_artifacts_me409c2.py
"""

from __future__ import annotations

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
TEMP_ROOT = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "opencode" / "me409c2"
TEST_DATA = TEMP_ROOT / "data"
TEST_DATA.mkdir(parents=True, exist_ok=True)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# --- Módulo boto3 FAKE (antes de construir Boto3S3Client) ------------------


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
        self.calls.append(("get_paginator", name))
        return _FakePaginator(self)

    def head_object(self, **kw):
        self.calls.append(("head_object", kw))
        obj = self.objects.get(kw["Key"], {})
        return {"Metadata": obj.get("metadata") or {}}

    def generate_presigned_url(self, method, *, Params, ExpiresIn):
        self.calls.append(("generate_presigned_url", (method, Params, ExpiresIn)))
        return (
            f"https://presigned.example.invalid/{Params['Bucket']}/{Params['Key']}"
            f"?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=fake"
        )


class _FakePaginator:
    def __init__(self, raw):
        self._raw = raw

    def paginate(self, **kw):
        self._raw.calls.append(("paginate", kw))
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

from application.artifact_store_factory import (  # noqa: E402
    Boto3S3Client,
    S3ListedObject,
)
from application.s3_artifacts import (  # noqa: E402
    S3CompatibleArtifactStore,
    S3ArtifactError,
)

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


BUCKET = "ai-shorts-factory-media"
ENDPOINT = "https://fake-s3.example.invalid"
REGION = "auto"
ACCESS_KEY = "AKIA_FAKE_ME409C2"
SECRET_KEY = "s3cr3t0-fake-me409c2"
RUN_1 = "run-20260818-040001"
KEY = f"runs/{RUN_1}/artifacts/{RUN_1}-video-abcdef123456/video.mp4"
METADATA = {
    "artifact_id": f"{RUN_1}-video-abcdef123456",
    "run_id": RUN_1,
    "kind": "video",
    "filename": "video.mp4",
    "content_type": "video/mp4",
    "size_bytes": "4096",
    "reference": KEY,
    "storage": "s3-compatible",
}
BODY = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096


def build_client() -> Boto3S3Client:
    return Boto3S3Client(endpoint=ENDPOINT, region=REGION, access_key=ACCESS_KEY, secret_key=SECRET_KEY)


# ---------------------------------------------------------------------------
# A. Boto3S3Client construction.
# ---------------------------------------------------------------------------

client = build_client()
check(isinstance(client, Boto3S3Client), "A1 Boto3S3Client construido")
check(isinstance(client._raw, FakeRawS3), "A2 cliente boto3 real (fake) construido")

# ---------------------------------------------------------------------------
# B. endpoint correcto.
# ---------------------------------------------------------------------------

check(client._raw.kwargs["endpoint_url"] == ENDPOINT, "B1 endpoint pasado a boto3")

# ---------------------------------------------------------------------------
# C. region correcto.
# ---------------------------------------------------------------------------

check(client._raw.kwargs["region_name"] == REGION, "C1 region pasado a boto3")

# ---------------------------------------------------------------------------
# D. credentials no expuestas.
# ---------------------------------------------------------------------------

check(ACCESS_KEY not in repr(client) and SECRET_KEY not in repr(client), "D1 repr sin credenciales")
check(
    not hasattr(client, "access_key") and not hasattr(client, "secret_key"),
    "D2 el adapter no guarda las claves como atributos",
)
fuente_factory = (SRC / "application" / "artifact_store_factory.py").read_text(encoding="utf-8")
lineas_log = [l for l in fuente_factory.splitlines() if "logger." in l]
check(
    lineas_log != [] and all(
        "access_key" not in l and "secret_key" not in l for l in lineas_log
    ),
    "D3 el módulo no loguea credenciales en ninguna llamada logger",
)

# ---------------------------------------------------------------------------
# E. put_object traduce correctamente.
# ---------------------------------------------------------------------------

client.put_object(bucket=BUCKET, key=KEY, body=BODY, content_type="video/mp4", metadata=METADATA)
puts = [c for c in client._raw.calls if c[0] == "put_object"]
check(len(puts) == 1, "E1 put_object llamado una vez")
if puts:
    args = puts[0][1]
    check(args["Bucket"] == BUCKET and args["Key"] == KEY, "E2 Bucket/Key traducidos")
    check(args["Body"] is BODY, "E3 Body traducido (bytes intactos)")

# ---------------------------------------------------------------------------
# F. content_type correcto.
# ---------------------------------------------------------------------------

check(puts and puts[0][1]["ContentType"] == "video/mp4", "F1 ContentType traducido")

# ---------------------------------------------------------------------------
# G. metadata correcta.
# ---------------------------------------------------------------------------

check(puts and puts[0][1]["Metadata"] == METADATA, "G1 Metadata traducida íntegra")

# ---------------------------------------------------------------------------
# H. list_objects traduce correctamente (list + head).
# ---------------------------------------------------------------------------

entries = client.list_objects(BUCKET, f"runs/{RUN_1}/artifacts/")
check(len(entries) == 1 and isinstance(entries[0], S3ListedObject), "H1 list devuelve S3ListedObject")
check(entries and entries[0].key == KEY, "H2 key del objeto listado correcta")
check(
    "get_paginator" in [c[0] for c in client._raw.calls]
    and "head_object" in [c[0] for c in client._raw.calls],
    "H3 usa list_objects_v2 + head_object (metadata, sin descargar el archivo)",
)

# ---------------------------------------------------------------------------
# I. metadata round-trip.
# ---------------------------------------------------------------------------

check(entries and entries[0].metadata == METADATA, "I1 metadata round-trip (put -> list)")

# ---------------------------------------------------------------------------
# J. S3CompatibleArtifactStore funciona con Boto3S3Client.
# ---------------------------------------------------------------------------

source = TEST_DATA / "video.mp4"
source.write_bytes(BODY)
store = S3CompatibleArtifactStore(
    bucket=BUCKET,
    endpoint=ENDPOINT,
    access_key=ACCESS_KEY,
    secret_key=SECRET_KEY,
    region=REGION,
    client=build_client(),
)
try:
    rec = store.publish(RUN_1, source, kind="video")
    j_ok = True
except Exception as exc:  # noqa: BLE001
    j_ok = False
    print(f"   (publish falló: {type(exc).__name__}: {exc})")
check(j_ok, "J1 publish vía Boto3S3Client")
check(j_ok and store.list(RUN_1) == [rec], "J2 list vía Boto3S3Client")
check(j_ok and store.get(rec.artifact_id) == rec, "J3 get vía Boto3S3Client")
check(j_ok and rec.content_type == "video/mp4", "J4 content_type correcto tras round-trip")
check(j_ok and rec.storage == "s3-compatible", "J5 storage s3-compatible")

# ---------------------------------------------------------------------------
# Validación del repositorio.
# ---------------------------------------------------------------------------

S_checks = []
for nombre, archivo in (
    ("S1 py_compile artifact_store_factory.py", SRC / "application" / "artifact_store_factory.py"),
    ("S2 py_compile s3_artifacts.py", SRC / "application" / "s3_artifacts.py"),
):
    S_checks.append((nombre, subprocess.run(
        [sys.executable, "-m", "py_compile", str(archivo)],
        capture_output=True, text=True, cwd=str(ROOT),
    ).returncode == 0))
for nombre, ok in S_checks:
    check(ok, nombre)

diff = subprocess.run(
    ["git", "diff", "--check"], capture_output=True, text=True, cwd=str(ROOT)
)
check(diff.returncode == 0, "AB1 git diff --check")
if diff.returncode != 0:
    print(diff.stdout)

# ---------------------------------------------------------------------------
# Restauración del bloqueo de red.
# ---------------------------------------------------------------------------

socket.socket = _orig_socket
urllib.request.urlopen = _orig_urlopen
check(socket.socket is _orig_socket, "Z1 socket restaurado tras el arnés")

print()
if FAILURES:
    print(f"FALLOS ({len(FAILURES)}): {FAILURES}")
    print("RESULTADO: FAIL")
    sys.exit(1)
print("RESULTADO: PASS")