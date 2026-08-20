"""ME40.9D.3.3 - Acceso de aplicación a video temporal (presigned URL).

Verifica la integración de la URL temporal firmada en las capas de
aplicación, sin tocar FastAPI:

- :class:`ArtifactAccess.get_video_temporary_url(run_id, expires_in)`: valida
  ``run_id``/``expires_in``, localiza SOLO el artifact ``kind=video`` del run y
  delega en ``ArtifactStore.get_temporary_url`` (única fuente de URLs).
- :class:`ApplicationService.get_video_temporary_url`` delega únicamente en
  ``ArtifactAccess``: no conoce S3/boto3/buckets/object keys, no toca el
  filesystem y no genera URLs.

Tests (A-C), stdlib puro, sin red, sin pytest, fakes inyectados.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from application.artifact_access import ArtifactAccess
from application.artifacts import (
    ArtifactRecord,
    ArtifactStore,
    ArtifactUrlUnavailableError,
    ArtifactValidationError,
)
from application.exceptions import (
    ApplicationError,
    ApplicationRunNotFoundError,
    ApplicationValidationError,
)
from application.service import ApplicationService

RUN_ID = "run-20260819-101503"
OTHER_RUN = "run-20260819-101504"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def check_raises(exc_type, fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except exc_type:
        return True
    except Exception as exc:  # noqa: BLE001 - diagnóstico
        print(f"   (error inesperado: {type(exc).__name__}: {exc})")
        return False
    return False


def make_record(run_id: str, kind: str, artifact_id: str) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id,
        run_id=run_id,
        kind=kind,
        filename="video.mp4" if kind == "video" else "scene.png",
        content_type=("video/mp4" if kind == "video" else "image/png"),
        size_bytes=4096,
        reference=f"runs/{run_id}/artifacts/{artifact_id}/demo",
        storage="s3-compatible",
    )


VIDEO = make_record(RUN_ID, "video", f"{RUN_ID}-video-aaa111bbb222")
IMAGE = make_record(RUN_ID, "image", f"{RUN_ID}-image-ccc333ddd444")
OTHER_VIDEO = make_record(OTHER_RUN, "video", f"{OTHER_RUN}-video-eee555fff666")


class FakeStore(ArtifactStore):
    """Fake del contrato: registra llamadas y emite URLs (nunca descarga)."""

    def __init__(
        self, records_by_run: dict[str, list[ArtifactRecord]], *, url_error=None
    ) -> None:
        self.records = dict(records_by_run)
        self.list_calls: list[str] = []
        self.url_calls: list[tuple[str, int]] = []
        self.url_error = url_error

    def publish(self, run_id, source, *, kind="video"):
        raise NotImplementedError

    def list(self, run_id):
        self.list_calls.append(run_id)
        return list(self.records.get(run_id, []))

    def get(self, artifact_id):
        for records in self.records.values():
            for record in records:
                if record.artifact_id == artifact_id:
                    return record
        return None

    def get_temporary_url(self, artifact_id, expires_in):
        self.url_calls.append((artifact_id, expires_in))
        if self.url_error is not None:
            raise self.url_error
        return (
            f"https://presigned.example.invalid/{artifact_id}"
            f"?X-Amz-Expires={expires_in}&X-Amz-Signature=fake"
        )


class FakeRepository:
    def __init__(self, runs) -> None:
        self.runs = set(runs)

    def run_exists(self, run_id):
        return run_id in self.runs


# ----------------------------------------------------------------------
# A. ArtifactAccess.get_video_temporary_url
# ----------------------------------------------------------------------

store = FakeStore({RUN_ID: [VIDEO, IMAGE], OTHER_RUN: [OTHER_VIDEO]})
access = ArtifactAccess(store)

url = access.get_video_temporary_url(RUN_ID, 300)
check(
    isinstance(url, str) and url.startswith("https://"),
    "A1 devuelve una URL temporal para el video del run",
)

check(
    store.url_calls == [(VIDEO.artifact_id, 300)],
    "A2 delega en ArtifactStore.get_temporary_url con el artifact de video",
)

check(
    store.list_calls == [RUN_ID],
    "A3 la búsqueda se limita al run solicitado (una sola llamada a list)",
)

check(
    f"{VIDEO.artifact_id}" in url and "X-Amz-Expires=300" in url,
    "A4 la URL corresponde al video y refleja la expiración",
)

check(
    access.get_video_temporary_url(OTHER_RUN, 300)
    == store.get_temporary_url(OTHER_VIDEO.artifact_id, 300),
    "A5 aislamiento por run: otro run devuelve su propio video",
)

sin_video = FakeStore({RUN_ID: [IMAGE]})
check(
    ArtifactAccess(sin_video).get_video_temporary_url(RUN_ID, 300) is None,
    "A6 run sin video publicada -> None (mismo patrón que get_video)",
)

check(
    ArtifactAccess(FakeStore({})).get_video_temporary_url(RUN_ID, 300) is None,
    "A7 run sin artifacts -> None",
)

check(
    check_raises(
        ArtifactValidationError,
        access.get_video_temporary_url,
        "run-invalid",
        300,
    ),
    "A8 run_id inválido -> ArtifactValidationError",
)

for expires in (0, -1, "300", True):
    check(
        check_raises(
            ArtifactValidationError,
            access.get_video_temporary_url,
            RUN_ID,
            expires,
        ),
        f"A9 expires_in={expires!r} -> ArtifactValidationError",
    )

no_presign = FakeStore({RUN_ID: [VIDEO]}, url_error=ArtifactUrlUnavailableError("no presign"))
check(
    check_raises(
        ArtifactUrlUnavailableError,
        ArtifactAccess(no_presign).get_video_temporary_url,
        RUN_ID,
        300,
    ),
    "A10 backend sin presign -> ArtifactUrlUnavailableError (propagado)",
)


# ----------------------------------------------------------------------
# B. ApplicationService.get_video_temporary_url
# ----------------------------------------------------------------------

def build_service(
    records_by_run: dict[str, list[ArtifactRecord]],
    runs,
    *,
    url_error=None,
) -> ApplicationService:
    store = FakeStore(records_by_run, url_error=url_error)
    return ApplicationService(
        runs_root=Path(tempfile.mkdtemp(prefix="me409d33-")),
        repository=FakeRepository(runs),
        artifact_access=ArtifactAccess(store),
    ), store


with tempfile.TemporaryDirectory(prefix="me409d33-") as tmp:
    service, s_store = build_service(
        {RUN_ID: [VIDEO, IMAGE]}, [RUN_ID]
    )
    service._runs_root = Path(tmp)
    service_url = service.get_video_temporary_url(RUN_ID, 300)
    expected_url = (
        f"https://presigned.example.invalid/{VIDEO.artifact_id}"
        f"?X-Amz-Expires=300&X-Amz-Signature=fake"
    )
    check(
        service_url == expected_url,
        "B1 ApplicationService delega en ArtifactAccess (misma URL, no genera)",
    )
    check(
        s_store.url_calls == [(VIDEO.artifact_id, 300)],
        "B2 la expiración se propaga al store vía ArtifactAccess",
    )

    check(
        check_raises(
            ApplicationValidationError,
            service.get_video_temporary_url,
            "run-invalid",
            300,
        ),
        "B3 run_id inválido -> ApplicationValidationError (400 futuro)",
    )

    check(
        check_raises(
            ApplicationValidationError,
            service.get_video_temporary_url,
            RUN_ID,
            0,
        ),
        "B4 expires_in inválido -> ApplicationValidationError (400 futuro)",
    )

    check(
        check_raises(
            ApplicationRunNotFoundError,
            service.get_video_temporary_url,
            "run-20260819-999999",
            300,
        ),
        "B5 run inexistente -> ApplicationRunNotFoundError (404 futuro)",
    )

    sin_video_service, _ = build_service({RUN_ID: [IMAGE]}, [RUN_ID])
    sin_video_service._runs_root = Path(tmp)
    check(
        sin_video_service.get_video_temporary_url(RUN_ID, 300) is None,
        "B6 run sin video -> None",
    )

    no_presign_service, _ = build_service(
        {RUN_ID: [VIDEO]}, [RUN_ID], url_error=ArtifactUrlUnavailableError("no presign")
    )
    no_presign_service._runs_root = Path(tmp)
    check(
        check_raises(
            ApplicationError,
            no_presign_service.get_video_temporary_url,
            RUN_ID,
            300,
        ),
        "B7 backend sin presign -> ApplicationError (dominio traducido)",
    )


# ----------------------------------------------------------------------
# C. Delegación e independencia (sin S3/boto3/filesystem en el service)
# ----------------------------------------------------------------------

with tempfile.TemporaryDirectory(prefix="me409d33-") as tmp:
    store = FakeStore({RUN_ID: [VIDEO]})
    service = ApplicationService(
        runs_root=Path(tmp),
        repository=FakeRepository([RUN_ID]),
        artifact_access=ArtifactAccess(store),
    )

    service.get_video_temporary_url(RUN_ID, 300)
    check(
        store.list_calls == [RUN_ID] and store.url_calls == [(VIDEO.artifact_id, 300)],
        "C1 el service solo habla con ArtifactAccess (list + get_temporary_url)",
    )

    url_service = service.get_video_temporary_url(RUN_ID, 60)
    check(
        url_service == store.get_temporary_url(VIDEO.artifact_id, 60),
        "C2 el service devuelve la URL emitida por el store (sin generarla)",
    )

    # get_video() y list_artifacts() intactos (regresión del acceso).
    check(
        access.get_video(RUN_ID) == VIDEO,
        "C3 get_video() sigue intacto",
    )
    check(
        access.list_artifacts(RUN_ID) == [VIDEO, IMAGE],
        "C4 list_artifacts() sigue intacto",
    )


print()
print("RESULTADO: PASS")