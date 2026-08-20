# -*- coding: utf-8 -*-
"""ME40.9D.3.2 - Presigned URL S3/R2 (get_temporary_url).

Implementa ``get_temporary_url()`` para :class:`S3CompatibleArtifactStore`
usando boto3 únicamente a través de :class:`Boto3S3Client`. El cliente se
amplía con ``presign_get_url`` (``generate_presigned_url``) y el store firma
URLs temporales del artefacto privado **sin descargar el objeto**.

Tests (A-F), stdlib puro, sin pytest, sin HTTP real (socket/urlopen
bloqueados), sin boto3 real (módulo FAKE):

- A. Protocolo ampliado (presign_get_url).
- B. Boto3S3Client.presign_get_url traduce a generate_presigned_url.
- C. S3CompatibleArtifactStore.get_temporary_url end-to-end.
- D. Validaciones y errores (ValidationError / NotFound / Unavailable / S3ArtifactError).
- E. Seguridad: sin credenciales en URL, repr, logs ni ArtifactRecord; sin descarga.
- F. Red bloqueada y restauración.

Uso:

    .venv\\Scripts\\python.exe tests/test_artifact_presigned_url_me409d32.py
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import types
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# --- Módulo boto3 FAKE (antes de construir Boto3S3Client) -------------------


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
)
from application.artifacts import (  # noqa: E402
    ArtifactNotFoundError,
    ArtifactRecord,
    ArtifactUrlUnavailableError,
    ArtifactValidationError,
    LocalArtifactStore,
)
from application.s3_artifacts import (  # noqa: E402
    S3ArtifactError,
    S3CompatibleArtifactStore,
    S3Client,
)

# Bloqueo de red desde el inicio: ningún check puede abrir sockets HTTP.
_orig_socket = socket.socket
_orig_urlopen = urllib.request.urlopen


def _blocked(*_args, **_kwargs):
    raise AssertionError("HTTP externo no permitido durante los tests ME40.9D.3.2")


socket.socket = _blocked
urllib.request.urlopen = _blocked

FAILURES: list[str] = []


def check(ok: bool, label: str, detalle: str = "") -> None:
    if ok:
        print(f"[OK] {label}")
    else:
        print(f"[FAIL] {label} {detalle}".rstrip())
        FAILURES.append(label)


def check_raises(exc_type, fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except exc_type:
        return True
    except Exception as exc:  # noqa: BLE001 - diagnóstico
        print(f"   (error inesperado: {type(exc).__name__}: {exc})")
        return False
    return False


BUCKET = "ai-shorts-factory-media"
ENDPOINT = "https://fake-s3.example.invalid"
REGION = "auto"
ACCESS_KEY = "AKIA_FAKE_ME409D32"
SECRET_KEY = "s3cr3t0-fake-me409d32"
RUN_1 = "run-20260819-101501"
BODY = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096


class MinimalS3Client:
    """Cliente S3 mínimo sin presign (para probar ArtifactUrlUnavailableError)."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}

    def put_object(
        self, bucket, key, *, body, content_type, metadata
    ) -> object:
        self.objects[key] = dict(metadata)
        return None

    def list_objects(self, bucket, prefix) -> list[object]:
        class _Entry:
            def __init__(self, key, metadata):
                self.key = key
                self.metadata = metadata

        return [_Entry(k, v) for k, v in self.objects.items() if k.startswith(prefix)]


class FailingPresignClient(MinimalS3Client):
    def presign_get_url(self, bucket: str, key: str, *, expires_in: int) -> str:
        raise RuntimeError("el proveedor presign ha fallado")


def build_boto_client() -> Boto3S3Client:
    return Boto3S3Client(
        endpoint=ENDPOINT,
        region=REGION,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
    )


def build_store(client: object) -> S3CompatibleArtifactStore:
    return S3CompatibleArtifactStore(
        bucket=BUCKET,
        endpoint=ENDPOINT,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        region=REGION,
        client=client,
    )


# ---------------------------------------------------------------------------
# A. Protocolo ampliado.
# ---------------------------------------------------------------------------

boto_client = build_boto_client()
check(
    isinstance(boto_client, Boto3S3Client)
    and isinstance(boto_client, S3Client),
    "A1 Boto3S3Client cumple el protocolo S3Client ampliado",
)
check(
    callable(getattr(S3Client, "presign_get_url", None)),
    "A2 S3Client define presign_get_url (operación mínima de presign)",
)
check(
    callable(getattr(Boto3S3Client, "presign_get_url", None)),
    "A3 Boto3S3Client implementa presign_get_url",
)

# ---------------------------------------------------------------------------
# B. Boto3S3Client.presign_get_url -> generate_presigned_url.
# ---------------------------------------------------------------------------

raw = boto_client._raw
presigned = boto_client.presign_get_url(BUCKET, "runs/x/artifacts/y/video.mp4", expires_in=300)
check(
    isinstance(presigned, str) and presigned.startswith("https://"),
    "B1 presign_get_url devuelve una URL https",
)
calls = [c for c in raw.calls if c[0] == "generate_presigned_url"]
check(
    len(calls) == 1
    and calls[0][1][0] == "get_object"
    and calls[0][1][1] == {"Bucket": BUCKET, "Key": "runs/x/artifacts/y/video.mp4"}
    and calls[0][1][2] == 300,
    "B2 traduce a generate_presigned_url(get_object, Params, ExpiresIn=300)",
)
check(
    "X-Amz-Expires=300" in presigned and "X-Amz-Signature=fake" in presigned,
    "B3 la URL es temporal y firmada (no pública permanente)",
)
check(
    ACCESS_KEY not in presigned and SECRET_KEY not in presigned,
    "B4 la URL firmada no contiene credenciales",
)

# ---------------------------------------------------------------------------
# C. S3CompatibleArtifactStore.get_temporary_url (end-to-end, sin descarga).
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory(prefix="me409d32-") as tmp:
    source = Path(tmp) / "video.mp4"
    source.write_bytes(BODY)
    store = build_store(build_boto_client())
    raw = store._client._raw
    rec = store.publish(RUN_1, source, kind="video")
    check(store.get(rec.artifact_id) == rec, "C0 get funciona antes de presign")

    url = store.get_temporary_url(rec.artifact_id, 300)
    check(
        isinstance(url, str) and url.startswith("https://"),
        "C1 get_temporary_url devuelve una URL https",
    )
    check(
        rec.reference in url,
        "C2 la URL contiene la object key/reference del artefacto",
    )
    presign_calls = [c for c in raw.calls if c[0] == "generate_presigned_url"]
    check(
        presign_calls and presign_calls[-1][1][2] == 300,
        "C3 expiración (300s) pasada a generate_presigned_url",
    )
    check(
        ACCESS_KEY not in url and SECRET_KEY not in url,
        "C4 la URL no expone credenciales",
    )
    check(
        not any(c[0] == "get_object" for c in raw.calls),
        "C5 sin descarga: get_object nunca se llama (solo metadata + presign)",
    )
    check(
        "X-Amz-Expires" in url and "X-Amz-Signature" in url,
        "C6 URL temporal firmada; el bucket permanece privado",
    )
    check(
        store.list(RUN_1) == [rec],
        "C7 publish/list/get siguen funcionando tras presign",
    )

    check(
        check_raises(
            ArtifactValidationError,
            store.get_temporary_url,
            "",
            300,
        ),
        "D1 artifact_id vacío -> ArtifactValidationError",
    )
    check(
        check_raises(
            ArtifactValidationError,
            store.get_temporary_url,
            rec.artifact_id,
            0,
        ),
        "D2 expires_in=0 -> ArtifactValidationError",
    )
    check(
        check_raises(
            ArtifactValidationError,
            store.get_temporary_url,
            rec.artifact_id,
            -1,
        ),
        "D3 expires_in negativo -> ArtifactValidationError",
    )
    check(
        check_raises(
            ArtifactValidationError,
            store.get_temporary_url,
            rec.artifact_id,
            "300",
        ),
        "D4 expires_in no entero -> ArtifactValidationError",
    )
    check(
        check_raises(
            ArtifactValidationError,
            store.get_temporary_url,
            rec.artifact_id,
            True,
        ),
        "D5 expires_in booleano -> ArtifactValidationError",
    )
    check(
        check_raises(
            ArtifactNotFoundError,
            store.get_temporary_url,
            "run-20260819-101502-video-000000000000",
            300,
        ),
        "D6 artefacto inexistente -> ArtifactNotFoundError",
    )

    # Cliente sin presign -> ArtifactUrlUnavailableError.
    minimal_store = build_store(MinimalS3Client())
    mrec = minimal_store.publish(RUN_1, source, kind="video")
    check(
        check_raises(
            ArtifactUrlUnavailableError,
            minimal_store.get_temporary_url,
            mrec.artifact_id,
            300,
        ),
        "D7 cliente sin presign -> ArtifactUrlUnavailableError",
    )

    # Cliente con presign que falla -> S3ArtifactError (sin credenciales).
    failing_store = build_store(FailingPresignClient())
    frec = failing_store.publish(RUN_1, source, kind="video")
    try:
        failing_store.get_temporary_url(frec.artifact_id, 300)
    except S3ArtifactError as exc:
        check(
            ACCESS_KEY not in str(exc) and SECRET_KEY not in str(exc),
            "D8 fallo de presign -> S3ArtifactError sin credenciales en el mensaje",
        )
    else:
        raise AssertionError("D8 debería elevar S3ArtifactError")

    # LocalArtifactStore permanece sin URL pública.
    local_store = LocalArtifactStore(Path(tmp) / "runs")
    run_dir = Path(tmp) / "runs" / RUN_1 / "output" / "video"
    run_dir.mkdir(parents=True, exist_ok=True)
    local_source = run_dir / "local.mp4"
    local_source.write_bytes(BODY)
    local_rec = local_store.publish(RUN_1, local_source)
    check(
        check_raises(
            ArtifactUrlUnavailableError,
            local_store.get_temporary_url,
            local_rec.artifact_id,
            300,
        ),
        "D9 LocalArtifactStore sigue sin URL pública (ArtifactUrlUnavailableError)",
    )

    # Seguridad adicional.
    check(
        ACCESS_KEY not in repr(store) and SECRET_KEY not in repr(store),
        "E1 repr(store) sin credenciales",
    )
    check(
        ACCESS_KEY not in repr(boto_client) and SECRET_KEY not in repr(boto_client),
        "E2 repr(Boto3S3Client) sin credenciales",
    )
    check(
        all(
            ACCESS_KEY not in str(v) and SECRET_KEY not in str(v)
            for v in rec.to_dict().values()
        ),
        "E3 ArtifactRecord.to_dict sin credenciales",
    )
    check(
        rec.storage == "s3-compatible" and rec.reference.startswith("runs/"),
        "E4 reference sigue siendo object key interna (sin URL pública)",
    )

# ---------------------------------------------------------------------------
# F. Red bloqueada y restauración.
# ---------------------------------------------------------------------------

try:
    _blocked()
except AssertionError:
    check(True, "F1 socket/urlopen bloqueados durante el arnés")
else:
    check(False, "F1 socket/urlopen bloqueados durante el arnés")

socket.socket = _orig_socket
urllib.request.urlopen = _orig_urlopen
check(socket.socket is _orig_socket, "F2 socket restaurado tras el arnés")

print()
if FAILURES:
    print(f"FALLOS ({len(FAILURES)}): {FAILURES}")
    print("RESULTADO: FAIL")
    sys.exit(1)
print("RESULTADO: PASS")