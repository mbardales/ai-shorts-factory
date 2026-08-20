"""ME40.9D.3.1 - Contrato de entrega de artifacts (URL temporal).

Verifica que :class:`ArtifactStore` expone una operación explícita para
obtener una URL temporal de descarga de un artefacto privado:

- El contrato define ``get_temporary_url(artifact_id, expires_in)``: devuelve
  una URL temporal **o eleva un error de dominio** (nunca inventa URLs públicas).
- ``LocalArtifactStore``: comportamiento explícito y seguro -> eleva
  :class:`ArtifactUrlUnavailableError` (se sirve a través del control plane).
- ``S3CompatibleArtifactStore``: abstracción preparada sin ampliar el cliente
  (:class:`S3Client`/``Boto3S3Client``) -> eleva
  :class:`ArtifactUrlUnavailableError` hasta incorporar presign.
- Seguridad: sin credenciales en ``ArtifactRecord``, logs ni ``repr``; sin
  URLs públicas.

No usa red, no toca pipeline/worker/frontend/RunStorage.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from application.artifacts import (
    ArtifactError,
    ArtifactNotFoundError,
    ArtifactRecord,
    ArtifactStore,
    ArtifactUrlUnavailableError,
    ArtifactValidationError,
    LocalArtifactStore,
)
from application.s3_artifacts import S3CompatibleArtifactStore

RUN_ID = "run-20260819-101500"

ACCESS_KEY = "AKIAEJEMPLO1234567890"
SECRET_KEY = "secreto-super-privado-987654321"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def check_raises(exc_type, fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except exc_type:
        return True
    except Exception as exc:  # noqa: BLE001 - solo para diagnóstico
        print(f"   (error inesperado: {type(exc).__name__}: {exc})")
        return False
    return False


def make_record() -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=f"{RUN_ID}-video-abcdef123456",
        run_id=RUN_ID,
        kind="video",
        filename="video.mp4",
        content_type="video/mp4",
        size_bytes=4096,
        reference=f"runs/{RUN_ID}/artifacts/{RUN_ID}-video-abcdef123456/video.mp4",
        storage="s3-compatible",
    )


class FakeS3Client:
    """Cliente S3 fake mínimo (put/list), sin capacidad de presign."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}

    def put_object(
        self, bucket: str, key: str, *, body: bytes, content_type: str, metadata: dict[str, str]
    ) -> object:
        self.objects[key] = dict(metadata)
        return None

    def list_objects(self, bucket: str, prefix: str) -> list[object]:
        class _Entry:
            def __init__(self, key, metadata):
                self.key = key
                self.metadata = metadata

        return [_Entry(k, v) for k, v in self.objects.items() if k.startswith(prefix)]


def local_store_and_record(tmp: Path) -> tuple[LocalArtifactStore, ArtifactRecord]:
    run_dir = tmp / RUN_ID / "output" / "video"
    run_dir.mkdir(parents=True, exist_ok=True)
    source = run_dir / "video.mp4"
    source.write_bytes(b"\x00" * 4096)
    store = LocalArtifactStore(tmp)
    record = store.publish(RUN_ID, source)
    return store, record


def s3_store_and_record(tmp: Path) -> tuple[S3CompatibleArtifactStore, ArtifactRecord]:
    client = FakeS3Client()
    store = S3CompatibleArtifactStore(
        bucket="bucket-test",
        endpoint="https://s3.example.invalid",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        client=client,
    )
    source = tmp / "video.mp4"
    source.write_bytes(b"\x00" * 4096)
    record = store.publish(RUN_ID, source)
    return store, record


# ----------------------------------------------------------------------
# A. Contrato
# ----------------------------------------------------------------------

check(
    hasattr(ArtifactStore, "get_temporary_url")
    and isinstance(getattr(ArtifactStore, "get_temporary_url", None), object),
    "A1 ArtifactStore define get_temporary_url en el contrato",
)

check(
    LocalArtifactStore.get_temporary_url is not ArtifactStore.get_temporary_url,
    "A2 LocalArtifactStore implementa get_temporary_url",
)

check(
    S3CompatibleArtifactStore.get_temporary_url
    is not ArtifactStore.get_temporary_url,
    "A3 S3CompatibleArtifactStore implementa get_temporary_url",
)

check(
    issubclass(ArtifactUrlUnavailableError, ArtifactError),
    "A4 ArtifactUrlUnavailableError es un error de dominio (ArtifactError)",
)


# ----------------------------------------------------------------------
# B. LocalArtifactStore: comportamiento explícito y seguro
# ----------------------------------------------------------------------

with tempfile.TemporaryDirectory(prefix="me409d31-local-") as tmp:
    store, record = local_store_and_record(Path(tmp))

    check(
        store.list(RUN_ID) == [record],
        "B1 LocalArtifactStore sigue funcionando (publish/list)",
    )

    check(
        store.get(record.artifact_id) == record,
        "B2 get sigue devolviendo el registro",
    )

    check(
        check_raises(
            ArtifactUrlUnavailableError,
            store.get_temporary_url,
            record.artifact_id,
            300,
        ),
        "B3 local eleva ArtifactUrlUnavailableError (sin inventar URL pública)",
    )

    check(
        check_raises(
            ArtifactUrlUnavailableError,
            store.get_temporary_url,
            record.artifact_id,
            9000,
        ),
        "B4 expiración válida amplia no cambia el comportamiento (sigue sin URL)",
    )

    check(
        check_raises(
            ArtifactNotFoundError,
            store.get_temporary_url,
            "run-20260819-101501-video-000000000000",
            300,
        ),
        "B5 local eleva ArtifactNotFoundError si el artefacto no existe",
    )

    check(
        check_raises(
            ArtifactValidationError,
            store.get_temporary_url,
            "",
            300,
        ),
        "B6 artifact_id vacío -> ArtifactValidationError",
    )

    check(
        check_raises(
            ArtifactValidationError,
            store.get_temporary_url,
            record.artifact_id,
            0,
        ),
        "B7 expires_in=0 -> ArtifactValidationError",
    )

    check(
        check_raises(
            ArtifactValidationError,
            store.get_temporary_url,
            record.artifact_id,
            -1,
        ),
        "B8 expires_in negativo -> ArtifactValidationError",
    )

    check(
        check_raises(
            ArtifactValidationError,
            store.get_temporary_url,
            record.artifact_id,
            "300",
        ),
        "B9 expires_in no entero -> ArtifactValidationError",
    )

    check(
        check_raises(
            ArtifactValidationError,
            store.get_temporary_url,
            record.artifact_id,
            True,
        ),
        "B10 expires_in booleano -> ArtifactValidationError",
    )

    check(
        check_raises(
            ArtifactValidationError,
            store.get_temporary_url,
            record.artifact_id,
            1.5,
        ),
        "B11 expires_in flotante -> ArtifactValidationError",
    )


# ----------------------------------------------------------------------
# C. S3CompatibleArtifactStore: abstracción preparada
# ----------------------------------------------------------------------

with tempfile.TemporaryDirectory(prefix="me409d31-s3-") as tmp:
    store, record = s3_store_and_record(Path(tmp))

    check(
        store.list(RUN_ID) == [record],
        "C1 S3 sigue funcionando (publish/list)",
    )

    check(
        store.get(record.artifact_id) == record,
        "C2 get sigue devolviendo el registro",
    )

    check(
        check_raises(
            ArtifactUrlUnavailableError,
            store.get_temporary_url,
            record.artifact_id,
            300,
        ),
        "C3 S3 eleva ArtifactUrlUnavailableError (abstracción preparada, "
        "sin ampliar el cliente)",
    )

    check(
        getattr(store._client, "presign_get_url", None) is None,
        "C4 el cliente no se amplió (sin presign en el protocolo)",
    )

    check(
        check_raises(
            ArtifactNotFoundError,
            store.get_temporary_url,
            "run-20260819-101501-video-000000000000",
            300,
        ),
        "C5 artefacto inexistente -> ArtifactNotFoundError",
    )

    check(
        check_raises(
            ArtifactValidationError,
            store.get_temporary_url,
            record.artifact_id,
            0,
        ),
        "C6 expires_in inválido -> ArtifactValidationError",
    )

    sin_cliente = S3CompatibleArtifactStore(
        bucket="bucket-test",
        endpoint="https://s3.example.invalid",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        client=None,
    )
    check(
        check_raises(
            ArtifactError,
            sin_cliente.get_temporary_url,
            record.artifact_id,
            300,
        ),
        "C7 sin cliente inyectado -> error de dominio (nunca URL)",
    )


# ----------------------------------------------------------------------
# D. Seguridad
# ----------------------------------------------------------------------

record = make_record()

check(
    all(
        ACCESS_KEY not in str(v) and SECRET_KEY not in str(v)
        for v in record.to_dict().values()
    ),
    "D1 ArtifactRecord.to_dict() sin credenciales",
)

check(
    ACCESS_KEY not in repr(record) and SECRET_KEY not in repr(record),
    "D2 repr(ArtifactRecord) sin credenciales",
)

reload = ArtifactRecord.from_dict(record.to_dict())
check(
    ACCESS_KEY not in str(reload) and SECRET_KEY not in repr(reload),
    "D3 round-trip from_dict sin credenciales",
)

local_store = LocalArtifactStore(Path(tempfile.mkdtemp(prefix="me409d31-repr-")))
check(
    ACCESS_KEY not in repr(local_store) and SECRET_KEY not in repr(local_store),
    "D4 repr(LocalArtifactStore) sin credenciales",
)

s3_store = S3CompatibleArtifactStore(
    bucket="bucket-test",
    endpoint="https://s3.example.invalid",
    access_key=ACCESS_KEY,
    secret_key=SECRET_KEY,
    client=FakeS3Client(),
)
check(
    ACCESS_KEY not in repr(s3_store) and SECRET_KEY not in repr(s3_store),
    "D5 repr(S3CompatibleArtifactStore) sin credenciales",
)

for store, record, nombre in (
    (local_store, record, "local"),
    (s3_store, record, "s3"),
):
    try:
        store.get_temporary_url(record.artifact_id, 300)
    except ArtifactError as exc:
        check(
            ACCESS_KEY not in str(exc) and SECRET_KEY not in str(exc),
            f"D6 error get_temporary_url ({nombre}) sin credenciales",
        )
    else:
        raise AssertionError(
            f"D6 get_temporary_url ({nombre}) debería elevar error"
        )


def _no_public_url(path: str) -> bool:
    return "http://" not in path and "https://" not in path


with tempfile.TemporaryDirectory(prefix="me409d31-nourl-") as tmp:
    lstore, lrecord = local_store_and_record(Path(tmp))
    sstore, srecord = s3_store_and_record(Path(tmp))

    for store, artifact_id in (
        (lstore, lrecord.artifact_id),
        (sstore, srecord.artifact_id),
    ):
        try:
            url = store.get_temporary_url(artifact_id, 300)
        except ArtifactUrlUnavailableError:
            check(True, f"D7 {type(store).__name__} no genera URL pública (eleva error)")
        else:
            check(
                _no_public_url(url),
                f"D7 {type(store).__name__} no devuelve URL pública",
            )

    check(
        _no_public_url(lrecord.reference) and _no_public_url(srecord.reference),
        "D8 las referencias de los registros no son URLs públicas",
    )


print()
print("RESULTADO: PASS")