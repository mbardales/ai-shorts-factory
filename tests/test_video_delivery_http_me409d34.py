"""ME40.9D.3.4 - Entrega HTTP del video mediante presigned URL.

``GET /api/v1/runs/{run_id}/video`` ahora obtiene la URL temporal vía
``ApplicationService -> ArtifactAccess -> ArtifactStore`` y redirige con **307**
a la presigned URL. La API **no descarga ni hace proxy** del MP4, no expone
object keys ni rutas del filesystem, y ``expires_in`` es controlado por el
servidor (constante, no aceptado del cliente).

Tests (A-F), stdlib puro, TestClient con fakes, sin red real:

- A. Video publicada -> 307 + Location a la presigned URL (body vacío).
- B. Sin video publicada -> 404 (nuevo ApplicationVideoNotFoundError).
- C. run_id inválido / run inexistente -> comportamiento HTTP existente.
- D. Backend local sin presign -> error HTTP claro (500), sin URL inventada.
- E. Sin descarga en la API (solo se delega en ArtifactAccess).
- F. /artifacts intacto + expires_in controlado por servidor.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi.testclient import TestClient

from api.app import VIDEO_URL_EXPIRES_SECONDS, create_app
from application.artifact_access import ArtifactAccess
from application.artifacts import (
    ArtifactRecord,
    ArtifactStore,
    ArtifactUrlUnavailableError,
)
from application.exceptions import ApplicationError

RUN_ID = "run-20260819-101505"
NO_VIDEO_RUN = "run-20260819-101506"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def make_record(run_id: str, kind: str, artifact_id: str) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id,
        run_id=run_id,
        kind=kind,
        filename="video.mp4" if kind == "video" else "scene.png",
        content_type=("video/mp4" if kind == "video" else "image/png"),
        size_bytes=4096,
        reference=f"runs/{run_id}/artifacts/{artifact_id}/video.mp4",
        storage="s3-compatible",
    )


VIDEO = make_record(RUN_ID, "video", f"{RUN_ID}-video-aaa111bbb222")
IMAGE = make_record(RUN_ID, "image", f"{RUN_ID}-image-ccc333ddd444")
IMAGE_NO_VIDEO = make_record(
    NO_VIDEO_RUN, "image", f"{NO_VIDEO_RUN}-image-eee555fff666"
)


class FakeStore(ArtifactStore):
    """Fake del contrato: registra llamadas, emite URLs o eleva error."""

    def __init__(
        self, records_by_run: dict[str, list[ArtifactRecord]], *, url_error=None
    ) -> None:
        self.records = dict(records_by_run)
        self.url_calls: list[tuple[str, int]] = []
        self.url_error = url_error

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
        self.url_calls.append((artifact_id, expires_in))
        if self.url_error is not None:
            raise self.url_error
        return (
            f"https://presigned.example.invalid/runs/{artifact_id.split('-video-')[0]}/"
            f"artifacts/{artifact_id}/video.mp4?X-Amz-Expires={expires_in}"
            f"&X-Amz-Signature=fake"
        )


_TMP_DIRS: list[tempfile.TemporaryDirectory] = []


def make_client(store: FakeStore):
    tmp = tempfile.TemporaryDirectory(prefix="me409d34-")
    _TMP_DIRS.append(tmp)
    app = create_app(runs_root=Path(tmp.name), artifact_access=ArtifactAccess(store))
    return TestClient(app, follow_redirects=False)


# ----------------------------------------------------------------------
# A. Video publicada -> 307 + Location presigned (body vacío).
# ----------------------------------------------------------------------

store = FakeStore({RUN_ID: [VIDEO, IMAGE]})
client = make_client(store)

created = client.post(
    "/api/v1/runs",
    json={"topic": "demo", "run_id": RUN_ID, "offline": True},
)
check(created.status_code == 202, "A0 run creado (202)")

resp = client.get(f"/api/v1/runs/{RUN_ID}/video")
check(
    resp.status_code == 307,
    "A1 /video redirige con HTTP 307 (redirect temporal)",
)
location = resp.headers.get("location", "")
check(
    location.startswith("https://presigned.example.invalid/"),
    "A2 Location apunta a la presigned URL",
)
check(
    resp.content == b"",
    "A3 body vacío (la API no descarga ni hace proxy del MP4)",
)
check(
    "X-Amz-Expires" in location and "X-Amz-Signature" in location,
    "A4 la URL es temporal y firmada",
)
check(
    store.url_calls == [(VIDEO.artifact_id, VIDEO_URL_EXPIRES_SECONDS)],
    "A5 expires_in controlado por servidor (constante), no del cliente",
)
check(
    str(Path(tempfile.gettempdir())) not in location and "\\" not in location,
    "A6 sin rutas del filesystem en la respuesta",
)

# ----------------------------------------------------------------------
# B. Sin video publicada -> 404.
# ----------------------------------------------------------------------

no_video_store = FakeStore({NO_VIDEO_RUN: [IMAGE_NO_VIDEO]})
no_video_client = make_client(no_video_store)
no_video_client.post(
    "/api/v1/runs",
    json={"topic": "demo", "run_id": NO_VIDEO_RUN, "offline": True},
)
rv = no_video_client.get(f"/api/v1/runs/{NO_VIDEO_RUN}/video")
check(
    rv.status_code == 404,
    "B1 run sin video publicada -> 404",
)
check(
    no_video_store.url_calls == [],
    "B2 no se intentó generar URL (sin artifact de video)",
)

# ----------------------------------------------------------------------
# C. run_id inválido / run inexistente -> comportamiento HTTP existente.
# ----------------------------------------------------------------------

check(
    client.get("/api/v1/runs/run-invalid/video").status_code == 400,
    "C1 run_id inválido -> 400 (ApplicationValidationError)",
)
check(
    client.get("/api/v1/runs/run-20260819-999999/video").status_code == 404,
    "C2 run inexistente -> 404 (ApplicationRunNotFoundError)",
)

# ----------------------------------------------------------------------
# D. Backend local sin presign -> error HTTP claro, sin URL inventada.
# ----------------------------------------------------------------------

local_store = FakeStore(
    {RUN_ID: [VIDEO]},
    url_error=ArtifactUrlUnavailableError("el backend local no firma URLs"),
)
local_client = make_client(local_store)
local_client.post(
    "/api/v1/runs",
    json={"topic": "demo", "run_id": RUN_ID, "offline": True},
)
rv = local_client.get(f"/api/v1/runs/{RUN_ID}/video")
check(
    rv.status_code == 500,
    "D1 backend local sin presign -> 500 (error HTTP claro, sin inventar URL)",
)
check(
    "https://" not in rv.text and "presigned" not in rv.text,
    "D2 la respuesta de error no contiene ninguna URL",
)
check(
    "http" not in rv.text.lower(),
    "D3 sin URLs públicas en el error",
)

# ----------------------------------------------------------------------
# E. Sin descarga en la API.
# ----------------------------------------------------------------------

check(
    all(
        call[1] == VIDEO_URL_EXPIRES_SECONDS for call in store.url_calls
    ),
    "E1 la API solo delega en ArtifactAccess (get_video_temporary_url)",
)
check(
    "location" in resp.headers and resp.history == [] and resp.content == b"",
    "E2 respuesta de redirect sin cuerpo y sin seguimiento (sin proxy/streaming)",
)

# ----------------------------------------------------------------------
# F. /artifacts intacto.
# ----------------------------------------------------------------------

artifacts = client.get(f"/api/v1/runs/{RUN_ID}/artifacts")
check(
    artifacts.status_code == 200
    and len(artifacts.json()["artifacts"]) == 2,
    "F1 GET /api/v1/runs/{id}/artifacts sigue intacto (200, 2 artifacts)",
)

print()
print("RESULTADO: PASS")

for _tmp in _TMP_DIRS:
    _tmp.cleanup()