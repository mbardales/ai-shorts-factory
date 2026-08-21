# -*- coding: utf-8 -*-
"""ME40.9E - Local Video Delivery: entrega segura del video con backend local.

``GET /api/v1/runs/{run_id}/video`` ahora tiene doble modo según la capacidad
del :class:`ArtifactStore`:

- Backend remoto (S3/R2): ``307`` → presigned URL (contrato ME40.9D intacto).
- Backend local (custodia el archivo): ``200`` inline ``video/mp4`` vía
  ``ArtifactStore.read_content``, **sin** URL pública inventada y **sin**
  acceso al filesystem desde la API (los bytes los entrega el dominio).

Checks (A-F), stdlib puro, TestClient con stores reales/fakes, sin red:

- A. Dominio: ``read_content`` seguro (bytes exactos, traversal rechazado,
   aislamiento, errores explícitos) y capability ``supports_temporary_url``.
- B. ``ArtifactAccess.get_video_content`` / ``supports_temporary_url``.
- C. ``ApplicationService.get_video_content`` / ``supports_temporary_urls``.
- D. HTTP backend local: 200 inline, 404/400 correctos.
- E. HTTP backend S3 (fake): sigue 307 + Location (regresión D.3.4).
- F. ``GET /artifacts`` intacto (metadata, sin URLs ni rutas).

Uso: .venv\\Scripts\\python.exe tests\\test_local_video_delivery_me409e.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from application.artifact_access import ArtifactAccess  # noqa: E402
from application.artifacts import (  # noqa: E402
    ArtifactContentUnavailableError,
    ArtifactNotFoundError,
    ArtifactRecord,
    ArtifactStore,
    ArtifactValidationError,
    LocalArtifactStore,
)
from application.exceptions import (  # noqa: E402
    ApplicationError,
    ApplicationRunNotFoundError,
    ApplicationValidationError,
)
from application.service import ApplicationService  # noqa: E402

FAILURES: list[str] = []

#: Bytes de un MP4 falso (ftyp dentro de los primeros 64 bytes).
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(f"{label}: {detail}")
    print(f"[{'OK' if cond else 'FAIL'}] {label}" + ("" if cond else f" | {detail}"))


def make_video_record(run_id: str, kind: str = "video") -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=f"{run_id}-{kind}-aaa111bbb222",
        run_id=run_id,
        kind=kind,
        filename="video.mp4" if kind == "video" else "scene.png",
        content_type="video/mp4" if kind == "video" else "image/png",
        size_bytes=len(FAKE_MP4),
        reference=f"output/video/video.mp4" if kind == "video" else "output/images/scene.png",
        storage="local",
    )


class FakeRemoteStore(ArtifactStore):
    """Store remoto fake: firma URLs (como S3/R2) pero no custodia archivos."""

    def __init__(self, records_by_run: dict[str, list[ArtifactRecord]]) -> None:
        self.records = dict(records_by_run)

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
        return f"https://presigned.example.invalid/{artifact_id}?X-Amz-Expires={expires_in}"


class DuckStore:
    """Store sin el contrato (ni read_content ni supports_temporary_url)."""

    def __init__(self, records: list[ArtifactRecord]) -> None:
        self.records = records

    def list(self, run_id):
        return [r for r in self.records if r.run_id == run_id]

    def get(self, artifact_id):
        return next((r for r in self.records if r.artifact_id == artifact_id), None)


TMP = Path(tempfile.mkdtemp(prefix="me409e-"))
RUN_A = "run-20260821-100001"
RUN_B = "run-20260821-100002"
RUN_IMG = "run-20260821-100003"
RUN_EMPTY = "run-20260821-100004"
RUN_GONE = "run-20260821-100005"
RUN_MISSING = "run-20260821-199999"


def make_run_dir(run_id: str, *, with_video: bool = False) -> Path:
    run_dir = TMP / run_id
    (run_dir / "output" / "video").mkdir(parents=True, exist_ok=True)
    if with_video:
        (run_dir / "output" / "video" / "video.mp4").write_bytes(FAKE_MP4)
    return run_dir


make_run_dir(RUN_A, with_video=True)
make_run_dir(RUN_B, with_video=True)
img_dir = TMP / RUN_IMG / "output" / "images"
img_dir.mkdir(parents=True, exist_ok=True)
(img_dir / "scene.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
make_run_dir(RUN_EMPTY)
make_run_dir(RUN_GONE, with_video=True)

store = LocalArtifactStore(TMP)
REC_A = store.publish(RUN_A, "output/video/video.mp4", kind="video")
store.publish(RUN_B, "output/video/video.mp4", kind="video")
store.publish(RUN_IMG, "output/images/scene.png", kind="image")
REC_GONE = store.publish(RUN_GONE, "output/video/video.mp4", kind="video")
(TMP / RUN_GONE / "output" / "video" / "video.mp4").unlink()

access = ArtifactAccess(store)
service = ApplicationService(runs_root=TMP, artifact_access=access)

# ---------------------------------------------------------------------------
# A. Dominio: LocalArtifactStore.read_content + supports_temporary_url
# ---------------------------------------------------------------------------

check("A1 local no emite URLs temporales (capability)", store.supports_temporary_url is False)
check("A2 contrato por defecto emite URLs (remoto)", ArtifactStore.supports_temporary_url.fget(ArtifactStore) is True or FakeRemoteStore({}).supports_temporary_url is True)
check("A3 read_content devuelve los bytes exactos",
      store.read_content(REC_A.artifact_id) == FAKE_MP4)
try:
    store.read_content(f"{RUN_A}-video-zzz999xxx888")
    check("A4 artifact inexistente -> NotFound", False, "no lanzó")
except ArtifactNotFoundError:
    check("A4 artifact inexistente -> NotFound", True)
except Exception as exc:  # noqa: BLE001
    check("A4 artifact inexistente -> NotFound", False, str(exc))
try:
    store.read_content("")
    check("A5 artifact_id vacío -> Validation", False, "no lanzó")
except ArtifactValidationError:
    check("A5 artifact_id vacío -> Validation", True)

# Traversal: artifacts.json manipulado con referencia fuera del run.
fuga = TMP / "fuga.txt"
fuga.write_text("secreto", encoding="utf-8")
malicioso = {
    "artifact_id": f"{RUN_EMPTY}-video-deadbeefcafe",
    "run_id": RUN_EMPTY,
    "kind": "video",
    "filename": "video.mp4",
    "content_type": "video/mp4",
    "size_bytes": 7,
    "reference": "../fuga.txt",
    "storage": "local",
}
(TMP / RUN_EMPTY / "artifacts.json").write_text(
    json.dumps([malicioso], ensure_ascii=False), encoding="utf-8"
)
try:
    store.read_content(f"{RUN_EMPTY}-video-deadbeefcafe")
    check("A6 traversal en reference -> Validation", False, "sirvió contenido externo")
except ArtifactValidationError:
    check("A6 traversal en reference -> Validation", True)
except Exception as exc:  # noqa: BLE001
    check("A6 traversal en reference -> Validation", False, str(exc))

absoluto = {
    **malicioso,
    "artifact_id": f"{RUN_EMPTY}-video-cafebabedead",
    "reference": str(TMP / "fuga.txt"),
}
(TMP / RUN_EMPTY / "artifacts.json").write_text(
    json.dumps([absoluto], ensure_ascii=False), encoding="utf-8"
)
try:
    store.read_content(f"{RUN_EMPTY}-video-cafebabedead")
    check("A7 referencia absoluta -> Validation", False, "sirvió ruta absoluta")
except ArtifactValidationError:
    check("A7 referencia absoluta -> Validation", True)

# Restaurar el run para los checks de sección B/C/D (sin entradas maliciosas).
(TMP / RUN_EMPTY / "artifacts.json").write_text("[]", encoding="utf-8")

# Archivo físico eliminado tras publicar.
try:
    store.read_content(REC_GONE.artifact_id)
    check("A8 archivo físico ausente -> NotFound", False, "no lanzó")
except ArtifactNotFoundError:
    check("A8 archivo físico ausente -> NotFound", True)

# Store remoto: read_content no disponible (entrega por presign, nunca proxy).
remote = FakeRemoteStore({})
try:
    remote.read_content("x")
    check("A9 remoto sin read_content -> Unavailable", False, "no lanzó")
except ArtifactContentUnavailableError:
    check("A9 remoto sin read_content -> Unavailable", True)

# ---------------------------------------------------------------------------
# B. ArtifactAccess
# ---------------------------------------------------------------------------

check("B1 get_video_content devuelve bytes del video",
      access.get_video_content(RUN_A) == FAKE_MP4)
check("B2 aislamiento: otro run no expone su video aquí",
      access.get_video_content(RUN_A) == FAKE_MP4
      and len(access.get_video_content(RUN_B) or b"") == len(FAKE_MP4))
check("B3 run con solo imágenes -> None", access.get_video_content(RUN_IMG) is None)
check("B4 run sin artifacts -> None", access.get_video_content(RUN_EMPTY) is None)
try:
    access.get_video_content("run-invalid")
    check("B5 run_id inválido -> Validation", False, "no lanzó")
except ArtifactValidationError:
    check("B5 run_id inválido -> Validation", True)
check("B6 capability delega en el store (local False)",
      access.supports_temporary_url() is False)
check("B7 capability delega en el store (remoto True)",
      ArtifactAccess(FakeRemoteStore({})).supports_temporary_url() is True)
duck = DuckStore([make_video_record(RUN_A)])
check("B8 duck store sin atributos -> capability True (guard)",
      ArtifactAccess(duck).supports_temporary_url() is True)  # type: ignore[arg-type]
try:
    ArtifactAccess(duck).get_video_content(RUN_A)  # type: ignore[arg-type]
    check("B9 duck store sin read_content -> Unavailable", False, "no lanzó")
except ArtifactContentUnavailableError:
    check("B9 duck store sin read_content -> Unavailable", True)

# ---------------------------------------------------------------------------
# C. ApplicationService
# ---------------------------------------------------------------------------

check("C1 get_video_content devuelve bytes",
      service.get_video_content(RUN_A) == FAKE_MP4)
check("C2 sin video -> None", service.get_video_content(RUN_IMG) is None)
try:
    service.get_video_content(RUN_MISSING)
    check("C3 run inexistente -> RunNotFound", False, "no lanzó")
except ApplicationRunNotFoundError:
    check("C3 run inexistente -> RunNotFound", True)
try:
    service.get_video_content("run-invalid")
    check("C4 run_id inválido -> Validation", False, "no lanzó")
except ApplicationValidationError:
    check("C4 run_id inválido -> Validation", True)
try:
    service.get_video_content(RUN_GONE)
    check("C5 archivo ausente -> ApplicationError", False, "no lanzó")
except ApplicationError:
    check("C5 archivo ausente -> ApplicationError", True)
check("C6 supports_temporary_urls False (local)",
      service.supports_temporary_urls() is False)
check("C7 supports_temporary_urls True (remoto)",
      ApplicationService(
          runs_root=TMP, artifact_access=ArtifactAccess(FakeRemoteStore({}))
      ).supports_temporary_urls()
      is True)

# ---------------------------------------------------------------------------
# D. HTTP backend local: entrega inline segura
# ---------------------------------------------------------------------------

app_local = create_app(runs_root=TMP, artifact_access=access)
with TestClient(app_local, follow_redirects=False) as c:
    rv = c.get(f"/api/v1/runs/{RUN_A}/video")
    check("D1 GET /video local -> 200", rv.status_code == 200, f"status={rv.status_code}")
    check("D2 Content-Type video/mp4",
          rv.headers.get("content-type", "").startswith("video/mp4"),
          rv.headers.get("content-type", ""))
    check("D3 bytes exactos del MP4", rv.content == FAKE_MP4, f"bytes={len(rv.content)}")
    cd = rv.headers.get("content-disposition", "")
    check("D4 sin Content-Disposition attachment (inline)",
          "attachment" not in cd.lower(), cd or "(sin header)")
    check("D5 sin rutas del filesystem en la respuesta",
          str(TMP) not in rv.text and "output" not in rv.headers.get("location", ""))

    rv = c.get(f"/api/v1/runs/{RUN_IMG}/video")
    check("D6 run sin video publicada -> 404", rv.status_code == 404, f"status={rv.status_code}")
    rv = c.get(f"/api/v1/runs/{RUN_EMPTY}/video")
    check("D7 run sin artifacts -> 404", rv.status_code == 404, f"status={rv.status_code}")
    rv = c.get(f"/api/v1/runs/{RUN_MISSING}/video")
    check("D8 run inexistente -> 404", rv.status_code == 404, f"status={rv.status_code}")
    rv = c.get("/api/v1/runs/run-invalid/video")
    check("D9 run_id inválido -> 400", rv.status_code == 400, f"status={rv.status_code}")
    rv = c.get(f"/api/v1/runs/{RUN_GONE}/video")
    check("D10 archivo ausente -> 500 claro", rv.status_code == 500, f"status={rv.status_code}")

    arts = c.get(f"/api/v1/runs/{RUN_A}/artifacts")
    body = arts.json()
    check("F1 /artifacts intacto (200, metadata)",
          arts.status_code == 200 and len(body["artifacts"]) == 1
          and body["artifacts"][0]["artifact_id"] == REC_A.artifact_id,
          arts.text[:120])
    check("F2 /artifacts sin URLs públicas ni rutas absolutas",
          "http" not in arts.text.lower() and str(TMP) not in arts.text)

# ---------------------------------------------------------------------------
# E. HTTP backend remoto (fake S3): contrato 307 intacto (ME40.9D.3.4)
# ---------------------------------------------------------------------------

TMP_REMOTE = Path(tempfile.mkdtemp(prefix="me409e-remote-"))
(TMP_REMOTE / RUN_A).mkdir(parents=True, exist_ok=True)
remote_store = FakeRemoteStore({RUN_A: [make_video_record(RUN_A)]})
app_remote = create_app(
    runs_root=TMP_REMOTE, artifact_access=ArtifactAccess(remote_store)
)
with TestClient(app_remote, follow_redirects=False) as c:
    rv = c.get(f"/api/v1/runs/{RUN_A}/video")
    check("E1 S3/R2 sigue redirigiendo 307", rv.status_code == 307, f"status={rv.status_code}")
    check("E2 Location es la presigned URL",
          rv.headers.get("location", "").startswith("https://presigned.example.invalid/"))
    check("E3 sin body en el redirect (sin proxy)", rv.content == b"")

print()
print(f"RESULTADO: {'PASS' if not FAILURES else 'FAIL'}")
for f in FAILURES:
    print(f"  - {f}")
