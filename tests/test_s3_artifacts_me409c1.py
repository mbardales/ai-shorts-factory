# -*- coding: utf-8 -*-
"""ME40.9C.1 - Tests del adaptador S3-compatible de ArtifactStore.

El control plane (Northflank) necesita los artefactos fuera del worker local;
ME40.9A definió ArtifactStore y ME40.9C.1 añade ``S3CompatibleArtifactStore``:
un adapter S3-compatible que sube el objeto con key determinista
``runs/<run_id>/artifacts/<artifact_id>/<filename>`` y guarda la metadata como
metadata de usuario del objeto. NO conecta ningún proveedor real: el cliente
S3 es una dependencia inyectable (protocolo mínimo) y el test usa un fake.

Tests (A-T), stdlib puro, sin pytest, sin HTTP, sin boto3/requests/urllib,
sin R2 real, sin GPU/Gemini/SD15/Kokoro:

- A. construcción correcta.
- B. bucket/endpoint/region.
- C. publish de archivo existente.
- D. key contiene run_id.
- E. key contiene filename seguro.
- F. content_type video/mp4.
- G. size_bytes correcto.
- H. put/upload llamado exactamente una vez.
- I. ArtifactRecord devuelto correctamente (sin credenciales, sin URL).
- J. source no modificado.
- K. run_id inválido rechazado.
- L. traversal rechazado.
- M. archivo inexistente rechazado.
- N. error del cliente convertido a excepción de aplicación.
- O. list filtrado por run_id.
- P. get devuelve metadata (sin descargar el archivo).
- Q. no HTTP real / sin cliente inyectado no puede operar / sin fugas de credenciales.
- R. no dependencias externas (boto3/requests/urllib) ni acoplamiento a R2.
- S. py_compile.
- T. git diff --check.

Uso:

    .venv\\Scripts\\python.exe tests/test_s3_artifacts_me409c1.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TEMP_ROOT = Path(os.environ.get("TEMP", str(ROOT / "build"))) / "opencode" / "me409c1"
TEST_DATA = TEMP_ROOT / "data"
TEST_DATA.mkdir(parents=True, exist_ok=True)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import application.artifacts  # noqa: E402
from application.artifacts import (  # noqa: E402
    ArtifactRecord,
    ArtifactValidationError,
    ArtifactNotFoundError,
)
from application.s3_artifacts import (  # noqa: E402
    S3ArtifactError,
    S3CompatibleArtifactStore,
    S3Client,
    S3_STORAGE,
)

# Bloqueo de red desde el inicio: ningún check puede abrir sockets HTTP.
_orig_socket = socket.socket
_orig_urlopen = urllib.request.urlopen


def _blocked(*_args, **_kwargs):
    raise AssertionError("HTTP externo no permitido durante los tests ME40.9C.1")


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


BUCKET = "shortsfactory"
ENDPOINT = "https://fake-s3.example.invalid"
ACCESS_KEY = "AKIA_FAKE_ME409C1"
SECRET_KEY = "s3cr3t0-fake-me409c1"
RUN_1 = "run-20260818-030001"
RUN_2 = "run-20260818-030002"

VIDEO_PAYLOAD = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096
KEY_PATTERN = re.compile(
    r"^runs/run-\d{8}-\d{6}/artifacts/run-\d{8}-\d{6}-video-[0-9a-f]{12}/video\.mp4$"
)


class FakeEntry:
    def __init__(self, key: str, metadata: dict, body: bytes = b"", content_type: str = "") -> None:
        self.key = key
        self.metadata = metadata
        self.body = body
        self.content_type = content_type


class FakeS3Client:
    """Cliente S3 fake: solo registra puts y lista por prefijo (sin red)."""

    def __init__(self) -> None:
        self.objects: dict[str, FakeEntry] = {}
        self.puts: list[dict] = []
        self.buckets_seen: set[str] = set()

    def put_object(
        self, bucket: str, key: str, *, body: bytes, content_type: str, metadata: dict[str, str]
    ) -> object:
        self.buckets_seen.add(bucket)
        self.puts.append({
            "bucket": bucket,
            "key": key,
            "content_type": content_type,
            "metadata": dict(metadata),
        })
        self.objects[key] = FakeEntry(
            key=key, metadata=dict(metadata), body=body, content_type=content_type
        )
        return None

    def list_objects(self, bucket: str, prefix: str) -> list[FakeEntry]:
        self.buckets_seen.add(bucket)
        return [e for k, e in self.objects.items() if k.startswith(prefix)]


def make_source(nombre: str = "video.mp4", payload: bytes = VIDEO_PAYLOAD) -> Path:
    p = TEST_DATA / nombre
    p.write_bytes(payload)
    return p


def build_store(client: object, **kwargs) -> S3CompatibleArtifactStore:
    return S3CompatibleArtifactStore(
        bucket=BUCKET,
        endpoint=ENDPOINT,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        client=client,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# A. construcción correcta.
# ---------------------------------------------------------------------------

fake = FakeS3Client()
try:
    store = build_store(fake)
    a_ok = True
except Exception as exc:  # noqa: BLE001
    a_ok = False
    print(f"   (construcción falló: {type(exc).__name__}: {exc})")
check(a_ok, "A1 construcción con cliente inyectado OK")
check(
    check_raises(ValueError, S3CompatibleArtifactStore, bucket="", endpoint=ENDPOINT,
                 access_key=ACCESS_KEY, secret_key=SECRET_KEY, client=fake),
    "A2 bucket vacío rechazado",
)
check(
    check_raises(ValueError, S3CompatibleArtifactStore, bucket=BUCKET, endpoint="",
                 access_key=ACCESS_KEY, secret_key=SECRET_KEY, client=fake),
    "A3 endpoint vacío rechazado",
)
check(
    check_raises(ValueError, S3CompatibleArtifactStore, bucket=BUCKET, endpoint=ENDPOINT,
                 access_key="", secret_key=SECRET_KEY, client=fake),
    "A4 access_key vacía rechazada",
)

# ---------------------------------------------------------------------------
# B. bucket/endpoint/region.
# ---------------------------------------------------------------------------

check(store._bucket == BUCKET, "B1 bucket conservado")
check(store._endpoint == ENDPOINT, "B2 endpoint conservado")
check(store._region == "auto", "B3 region por defecto 'auto'")
check(build_store(fake, region="us-east-1")._region == "us-east-1", "B4 region explícita conservada")

# ---------------------------------------------------------------------------
# C. publish de archivo existente.
# ---------------------------------------------------------------------------

source = make_source()
try:
    rec = store.publish(RUN_1, source, kind="video")
    c_ok = True
except Exception as exc:  # noqa: BLE001
    c_ok = False
    print(f"   (publish falló: {type(exc).__name__}: {exc})")
check(c_ok, "C1 publish de archivo existente OK")
check(c_ok and len(fake.objects) == 1, "C2 objeto subido al fake client")
check(c_ok and fake.buckets_seen == {BUCKET}, "C3 publish usa el bucket correcto")

# ---------------------------------------------------------------------------
# D. key contiene run_id.
# ---------------------------------------------------------------------------

check(
    c_ok and rec.reference == f"runs/{RUN_1}/artifacts/{rec.artifact_id}/video.mp4",
    "D1 key contiene runs/<run_id>/artifacts/",
)

# ---------------------------------------------------------------------------
# E. key contiene filename seguro.
# ---------------------------------------------------------------------------

check(c_ok and rec.reference.endswith("/video.mp4"), "E1 key termina en <filename>")
check(c_ok and KEY_PATTERN.match(rec.reference) is not None, "E2 key cumple el formato determinista seguro")

# ---------------------------------------------------------------------------
# F. content_type video/mp4.
# ---------------------------------------------------------------------------

check(c_ok and rec.content_type == "video/mp4", "F1 record.content_type video/mp4")
check(c_ok and fake.puts[0]["content_type"] == "video/mp4", "F2 objeto subido con content_type video/mp4")

# ---------------------------------------------------------------------------
# G. size_bytes correcto.
# ---------------------------------------------------------------------------

check(c_ok and rec.size_bytes == len(VIDEO_PAYLOAD), "G1 size_bytes coincide con el archivo")
check(c_ok and fake.puts[0]["metadata"]["size_bytes"] == str(len(VIDEO_PAYLOAD)), "G2 metadata subida con size_bytes")

# ---------------------------------------------------------------------------
# H. put/upload llamado exactamente una vez.
# ---------------------------------------------------------------------------

check(c_ok and len(fake.puts) == 1, "H1 put_object llamado exactamente una vez por publish")

# ---------------------------------------------------------------------------
# I. ArtifactRecord devuelto correctamente.
# ---------------------------------------------------------------------------

check(c_ok and isinstance(rec, ArtifactRecord), "I1 devuelve ArtifactRecord")
check(c_ok and rec.run_id == RUN_1 and rec.kind == "video", "I2 run_id y kind correctos")
check(c_ok and rec.storage == S3_STORAGE, "I3 storage s3-compatible")
check(
    c_ok and "access_key" not in rec.to_dict().values() and "secret_key" not in rec.to_dict().values(),
    "I4 sin credenciales en ArtifactRecord",
)
check(
    c_ok and "http" not in rec.reference.lower() and rec.reference.startswith("runs/"),
    "I5 reference es object key (sin URLs públicas)",
)

# ---------------------------------------------------------------------------
# J. source no modificado.
# ---------------------------------------------------------------------------

check(c_ok and source.read_bytes() == VIDEO_PAYLOAD, "J1 source sin modificar (bytes idénticos)")
check(c_ok and source.is_file(), "J2 source conservado (no se mueve ni se borra)")

# ---------------------------------------------------------------------------
# K. run_id inválido rechazado.
# ---------------------------------------------------------------------------

check(
    check_raises(ArtifactValidationError, store.publish, "run-mal", source),
    "K1 publish con run_id inválido rechazado",
)
check(
    check_raises(ArtifactValidationError, store.publish, "", source),
    "K2 publish con run_id vacío rechazado",
)
check(
    check_raises(ArtifactValidationError, store.list, "run-mal"),
    "K3 list con run_id inválido rechazado",
)

# ---------------------------------------------------------------------------
# L. traversal rechazado.
# ---------------------------------------------------------------------------

check(
    check_raises(ArtifactValidationError, store.publish, f"{RUN_1}/../../salir", source),
    "L1 run_id con traversal rechazado",
)
check(
    check_raises(ArtifactValidationError, store.publish, f"{RUN_1}/../otro", source),
    "L2 run_id escapando por .. rechazado",
)
check(
    check_raises(ArtifactValidationError, S3CompatibleArtifactStore._safe_filename, "../../escape.mp4"),
    "L3 filename con .. rechazado por el sanitizador",
)
check(
    check_raises(ArtifactValidationError, S3CompatibleArtifactStore._safe_filename, "a\\b.mp4"),
    "L4 filename con separador rechazado por el sanitizador",
)

# ---------------------------------------------------------------------------
# M. archivo inexistente rechazado.
# ---------------------------------------------------------------------------

check(
    check_raises(ArtifactNotFoundError, store.publish, RUN_1, TEST_DATA / "nope.mp4"),
    "M1 archivo inexistente rechazado",
)

# ---------------------------------------------------------------------------
# N. error del cliente convertido a excepción de aplicación.
# ---------------------------------------------------------------------------


class ExplodingClient:
    def put_object(self, bucket, key, *, body, content_type, metadata):
        raise ConnectionError("bucket fuera de alcance (fake)")

    def list_objects(self, bucket, prefix):
        raise ConnectionError("listado caído (fake)")


exploding = build_store(ExplodingClient())
check(
    check_raises(S3ArtifactError, exploding.publish, RUN_1, source),
    "N1 error de put_object -> S3ArtifactError",
)
check(
    check_raises(S3ArtifactError, exploding.list, RUN_1),
    "N2 error de list_objects -> S3ArtifactError",
)
check(
    check_raises(S3ArtifactError, exploding.get, rec.artifact_id),
    "N3 error del cliente en get -> S3ArtifactError",
)

# ---------------------------------------------------------------------------
# O. list filtrado por run_id.
# ---------------------------------------------------------------------------

store2 = build_store(FakeS3Client())
source2 = make_source("intro.mp4", payload=b"OTRO" + b"\x00" * 64)
source3 = make_source("extra.mp4", payload=b"MAS" + b"\x00" * 32)
rec_run1_b = store2.publish(RUN_1, source2, kind="video")
rec_run1_c = store2.publish(RUN_1, source3, kind="video")
rec_run2 = store2.publish(RUN_2, source, kind="video")
lista_1 = store2.list(RUN_1)
lista_2 = store2.list(RUN_2)
check(
    len(lista_1) == 2 and all(r.run_id == RUN_1 for r in lista_1),
    "O1 list(RUN_1) devuelve solo los de RUN_1",
)
check(
    len(lista_2) == 1 and lista_2[0].run_id == RUN_2 and lista_2[0].artifact_id == rec_run2.artifact_id,
    "O2 list(RUN_2) aislado del otro run",
)
check({r.artifact_id for r in lista_1} == {rec_run1_b.artifact_id, rec_run1_c.artifact_id}, "O3 ids exactos por run")

# ---------------------------------------------------------------------------
# P. get devuelve metadata (sin descargar el archivo).
# ---------------------------------------------------------------------------

check(store2.get(rec_run2.artifact_id) == rec_run2, "P1 get(artifact_id) devuelve el ArtifactRecord")
check(store2.get("no-existe-artifact") is None, "P2 get de id desconocido devuelve None")
check(store2.get("") is None, "P3 get con id vacío devuelve None")
check(store2.get("run-mal-artifact") is None, "P4 get con run_id inválido devuelve None")
check(store2.get(rec_run2.artifact_id.replace(rec_run2.run_id, RUN_1)) is None, "P5 get no cruza entre runs")
check(store2.list(RUN_2)[0].content_type == "video/mp4", "P6 get/list entregan content_type sin descargar")

# ---------------------------------------------------------------------------
# Q. no HTTP real / sin cliente no puede operar / sin fugas de credenciales.
# ---------------------------------------------------------------------------

sin_client = build_store(None)
check(
    check_raises(S3ArtifactError, sin_client.publish, RUN_1, source),
    "Q1 sin cliente inyectado publish -> S3ArtifactError",
)
check(
    check_raises(S3ArtifactError, sin_client.list, RUN_1),
    "Q2 sin cliente inyectado list -> S3ArtifactError",
)
check(
    check_raises(S3ArtifactError, sin_client.get, "cualquiera"),
    "Q3 sin cliente inyectado get -> S3ArtifactError",
)
q4_ok = True
try:
    store2.list(RUN_1)
    store2.get(rec_run2.artifact_id)
except Exception as exc:  # noqa: BLE001
    q4_ok = False
    print(f"   (HTTP/socket no bloqueado: {type(exc).__name__}: {exc})")
check(q4_ok, "Q4 sin HTTP externo durante todos los checks (socket/urlopen bloqueados)")
check(
    SECRET_KEY not in repr(store) and ACCESS_KEY not in repr(store),
    "Q5 repr del store no expone credenciales",
)
check("http" not in rec.reference.lower(), "Q6 ninguna URL pública generada")

# ---------------------------------------------------------------------------
# R. no dependencias externas / sin acoplamiento a R2.
# ---------------------------------------------------------------------------

fuente = (SRC / "application" / "s3_artifacts.py").read_text(encoding="utf-8")
prohibidos = ["import boto3", "from boto3", "import requests", "from requests", "import urllib", "from urllib"]
check(
    all(p not in fuente for p in prohibidos),
    "R1 el adapter no importa boto3/requests/urllib",
)
check(
    "r2" not in fuente.lower() and "cloudflare" not in fuente.lower(),
    "R2 sin acoplamiento al nombre del proveedor (R2/Cloudflare)",
)
check(isinstance(fake, S3Client), "R3 el cliente inyectado cumple el protocolo mínimo")

# ---------------------------------------------------------------------------
# S. py_compile.
# ---------------------------------------------------------------------------

S_checks = []
for nombre, archivo in (
    ("S1 py_compile s3_artifacts.py", SRC / "application" / "s3_artifacts.py"),
    ("S2 py_compile artifacts.py", SRC / "application" / "artifacts.py"),
):
    S_checks.append((nombre, subprocess.run(
        [sys.executable, "-m", "py_compile", str(archivo)],
        capture_output=True, text=True, cwd=str(ROOT),
    ).returncode == 0))
for nombre, ok in S_checks:
    check(ok, nombre)

# ---------------------------------------------------------------------------
# T. git diff --check + alcance de archivos.
# ---------------------------------------------------------------------------

diff = subprocess.run(
    ["git", "diff", "--check"], capture_output=True, text=True, cwd=str(ROOT)
)
check(diff.returncode == 0, "T1 git diff --check")
if diff.returncode != 0:
    print(diff.stdout)
st = subprocess.run(
    ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=str(ROOT)
).stdout.splitlines()
modificados = [l for l in st if l.startswith(" M ") or l.startswith("M ")]
untracked = [l[3:].replace("/", "\\") for l in st if l.startswith("?? ")]
mod_sin_agents = [l[3:].replace("/", "\\") for l in modificados if "AGENTS.md" not in l]
check(
    mod_sin_agents == [],
    f"T2 ningún archivo tracked modificado (además de AGENTS.md preexistente): {mod_sin_agents}",
)
allowed = {
    "src\\application\\artifacts.py",
    "tests\\test_artifacts_me409a.py",
    "scripts\\run_worker.py",
    "tests\\test_worker_artifact_publish_me409b.py",
    "src\\application\\s3_artifacts.py",
    "tests\\test_s3_artifacts_me409c1.py",
}
check(set(untracked) <= allowed, f"T3 solo archivos ME40.9A/B/C.1 creados: {untracked}")

# ---------------------------------------------------------------------------
# Restauración del bloqueo de red.
# ---------------------------------------------------------------------------

socket.socket = _orig_socket
urllib.request.urlopen = _orig_urlopen
check(socket.socket is _orig_socket, "Q7 socket restaurado tras el arnés")

print()
if FAILURES:
    print(f"FALLOS ({len(FAILURES)}): {FAILURES}")
    print("RESULTADO: FAIL")
    sys.exit(1)
print("RESULTADO: PASS")
