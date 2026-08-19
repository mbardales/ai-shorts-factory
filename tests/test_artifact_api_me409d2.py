"""ME40.9D.2 - Contrato de acceso a artifacts."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from application.artifacts import ArtifactRecord, ArtifactStore
from application.artifact_access import ArtifactAccess


RUN_ID = "run-20260818-230227"


class FakeArtifactStore(ArtifactStore):
    def __init__(self) -> None:
        self.records: dict[str, list[ArtifactRecord]] = {}
        self.list_calls: list[str] = []

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


def make_record(
    *,
    run_id: str,
    artifact_id: str,
    kind: str = "video",
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id,
        run_id=run_id,
        kind=kind,
        filename="demo.mp4",
        content_type="video/mp4",
        size_bytes=123456,
        reference=(
            f"runs/{run_id}/artifacts/"
            f"{artifact_id}/demo.mp4"
        ),
        storage="s3-compatible",
    )


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


store = FakeArtifactStore()

video = make_record(
    run_id=RUN_ID,
    artifact_id=f"{RUN_ID}-video-abc123",
)

image = make_record(
    run_id=RUN_ID,
    artifact_id=f"{RUN_ID}-image-abc123",
    kind="image",
)

other_run = make_record(
    run_id="run-20260818-230228",
    artifact_id="run-20260818-230228-video-other",
)

store.records[RUN_ID] = [video, image]
store.records[other_run.run_id] = [other_run]

access = ArtifactAccess(store)


# ----------------------------------------------------------------------
# A. Construcción
# ----------------------------------------------------------------------

check(
    isinstance(access, ArtifactAccess),
    "A1 ArtifactAccess se construye correctamente",
)

check(
    access.get_video(RUN_ID) == video,
    "A2 get_video devuelve el video correcto",
)


# ----------------------------------------------------------------------
# B. Aislamiento por run
# ----------------------------------------------------------------------

check(
    access.get_video(other_run.run_id) == other_run,
    "B1 get_video funciona para otro run",
)

check(
    access.get_video("run-20260818-230229") is None,
    "B2 run válido sin artifacts devuelve None",
)


# ----------------------------------------------------------------------
# C. Filtrado por kind
# ----------------------------------------------------------------------

check(
    access.get_video(RUN_ID).kind == "video",
    "C1 get_video solo devuelve kind=video",
)

image_only_store = FakeArtifactStore()
image_only_store.records[RUN_ID] = [image]

image_only_access = ArtifactAccess(image_only_store)

check(
    image_only_access.get_video(RUN_ID) is None,
    "C2 un run con solo imágenes devuelve None",
)


# ----------------------------------------------------------------------
# D. Consulta al ArtifactStore
# ----------------------------------------------------------------------

check(
    store.list_calls == [
        RUN_ID,
        other_run.run_id,
        "run-20260818-230229",
        RUN_ID,
    ],
    "D1 get_video consulta ArtifactStore.list() con el run solicitado",
)


# ----------------------------------------------------------------------
# E. Metadata
# ----------------------------------------------------------------------

payload = video.to_dict()

check(
    payload["artifact_id"] == video.artifact_id,
    "E1 artifact_id conservado",
)

check(
    payload["run_id"] == RUN_ID,
    "E2 run_id conservado",
)

check(
    payload["kind"] == "video",
    "E3 kind video conservado",
)

check(
    payload["content_type"] == "video/mp4",
    "E4 content_type conservado",
)

check(
    payload["size_bytes"] == 123456,
    "E5 size_bytes conservado",
)

check(
    payload["reference"].startswith(
        f"runs/{RUN_ID}/artifacts/"
    ),
    "E6 reference es object key interna",
)


# ----------------------------------------------------------------------
# F. Seguridad
# ----------------------------------------------------------------------

check(
    "http://" not in payload["reference"]
    and "https://" not in payload["reference"],
    "F1 reference no contiene URL pública",
)

check(
    not Path(payload["reference"]).is_absolute(),
    "F2 reference no es una ruta absoluta",
)

check(
    ".." not in payload["reference"].split("/"),
    "F3 reference no contiene traversal",
)


# ----------------------------------------------------------------------
# G. Run inválido
# ----------------------------------------------------------------------

try:
    access.get_video("run-invalid")
except Exception:
    print("[OK] G1 run_id inválido rechazado")
else:
    raise AssertionError(
        "G1 run_id inválido debería ser rechazado"
    )


# ----------------------------------------------------------------------
# H. Endpoint HTTP: GET /api/v1/runs/{run_id}/artifacts (ME40.9D.2)
# ----------------------------------------------------------------------

import tempfile

from fastapi.testclient import TestClient

from api.app import create_app

api_store = FakeArtifactStore()
api_store.records[RUN_ID] = [video, image]
api_store.records[other_run.run_id] = [other_run]

api_access = ArtifactAccess(api_store)

with tempfile.TemporaryDirectory(prefix="me409d2-") as tmp:
    app = create_app(
        runs_root=Path(tmp),
        artifact_access=api_access,
    )
    client = TestClient(app)

    created = client.post(
        "/api/v1/runs",
        json={"topic": "demo", "run_id": RUN_ID, "offline": True},
    )
    check(
        created.status_code == 202,
        "H1 POST /api/v1/runs crea el run (202)",
    )

    response = client.get(f"/api/v1/runs/{RUN_ID}/artifacts")
    check(
        response.status_code == 200,
        "H2 GET /artifacts responde 200 para un run existente",
    )

    body = response.json()
    check(
        body["run_id"] == RUN_ID,
        "H3 run_id de la respuesta conservado",
    )

    check(
        isinstance(body["artifacts"], list),
        "H4 artifacts es una lista",
    )

    by_id = {item["artifact_id"]: item for item in body["artifacts"]}
    check(
        len(by_id) == 2
        and video.artifact_id in by_id
        and image.artifact_id in by_id,
        "H5 se listan todos los artifacts publicados del run",
    )

    video_payload = by_id[video.artifact_id]
    check(
        video_payload["run_id"] == RUN_ID
        and video_payload["kind"] == "video"
        and video_payload["filename"] == "demo.mp4"
        and video_payload["content_type"] == "video/mp4"
        and video_payload["size_bytes"] == 123456
        and video_payload["storage"] == "s3-compatible",
        "H6 serialización completa de la metadata del artifact",
    )

    check(
        video_payload["reference"].startswith(
            f"runs/{RUN_ID}/artifacts/"
        ),
        "H7 reference es object key interna (sin URL pública)",
    )

    check(
        "http://" not in video_payload["reference"]
        and "https://" not in video_payload["reference"]
        and not Path(video_payload["reference"]).is_absolute(),
        "H8 la respuesta no expone URLs públicas ni rutas absolutas",
    )

    other_created = client.post(
        "/api/v1/runs",
        json={
            "topic": "demo",
            "run_id": other_run.run_id,
            "offline": True,
        },
    )
    check(
        other_created.status_code == 202,
        "H9 POST crea un segundo run",
    )

    other_response = client.get(
        f"/api/v1/runs/{other_run.run_id}/artifacts"
    )
    other_body = other_response.json()
    check(
        other_response.status_code == 200
        and len(other_body["artifacts"]) == 1
        and other_body["artifacts"][0]["artifact_id"]
        == other_run.artifact_id,
        "H10 aislamiento por run: solo se listan sus artifacts",
    )

    invalid = client.get("/api/v1/runs/run-invalid/artifacts")
    check(
        invalid.status_code == 400,
        "H11 run_id inválido -> 400 (ApplicationValidationError)",
    )

    missing = client.get(
        "/api/v1/runs/run-20260818-999999/artifacts"
    )
    check(
        missing.status_code == 404,
        "H12 run inexistente -> 404",
    )

    run_status = client.get(f"/api/v1/runs/{RUN_ID}")
    check(
        run_status.status_code == 200,
        "H13 GET /api/v1/runs/{run_id} sigue funcionando",
    )

    video_endpoint = client.get(f"/api/v1/runs/{RUN_ID}/video")
    check(
        video_endpoint.status_code == 404,
        "H14 GET /api/v1/runs/{run_id}/video sigue funcionando (404 sin video)",
    )

    check(
        client.get("/api/v1/runs/run-invalid").status_code == 400,
        "H15 /runs/{run_id} mantiene el 400 para run_id inválido",
    )

    api_access_empty = ArtifactAccess(FakeArtifactStore())
    empty_app = create_app(
        runs_root=Path(tmp),
        artifact_access=api_access_empty,
    )
    empty_client = TestClient(empty_app)
    empty = empty_client.get(f"/api/v1/runs/{RUN_ID}/artifacts")
    check(
        empty.status_code == 200 and empty.json()["artifacts"] == [],
        "H16 run sin artifacts devuelve lista vacía (200)",
    )


# ----------------------------------------------------------------------
# I. Fail-fast: backend S3/R2 mal configurado (ME40.9D.2)
# ----------------------------------------------------------------------

import os

from fastapi import FastAPI

from application.artifact_store_factory import ArtifactStoreConfigError

_saved_env = {
    name: os.environ.get(name)
    for name in (
        "OBJECT_STORAGE_BACKEND",
        "OBJECT_STORAGE_ENDPOINT",
        "OBJECT_STORAGE_BUCKET",
        "OBJECT_STORAGE_REGION",
        "OBJECT_STORAGE_ACCESS_KEY",
        "OBJECT_STORAGE_SECRET_KEY",
    )
}

try:
    for name in _saved_env:
        os.environ.pop(name, None)
    os.environ["OBJECT_STORAGE_BACKEND"] = "s3"
    try:
        create_app()
    except ArtifactStoreConfigError:
        print(
            "[OK] I1 create_app falla al arrancar (fail-fast) con backend S3 mal configurado"
        )
    else:
        raise AssertionError(
            "I1 create_app debería propagar ArtifactStoreConfigError con backend S3 inválido"
        )
finally:
    for name, value in _saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

local_app = create_app()
check(
    isinstance(local_app, FastAPI),
    "I2 create_app con backend local (por defecto) sigue funcionando",
)


print()
print("RESULTADO: PASS")
