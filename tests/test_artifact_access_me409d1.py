from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from application.artifact_access import ArtifactAccess
from application.artifacts import ArtifactRecord


RUN_ID = "run-20260818-231500"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


class FakeArtifactStore:
    def __init__(self, records: list[ArtifactRecord]) -> None:
        self.records = records
        self.list_calls: list[str] = []

    def list(self, run_id: str) -> list[ArtifactRecord]:
        self.list_calls.append(run_id)
        return list(self.records)


VIDEO = ArtifactRecord(
    artifact_id=f"{RUN_ID}-video-abcdef123456",
    run_id=RUN_ID,
    kind="video",
    filename="test.mp4",
    content_type="video/mp4",
    size_bytes=1234,
    reference=(
        f"runs/{RUN_ID}/artifacts/"
        f"{RUN_ID}-video-abcdef123456/test.mp4"
    ),
    storage="s3-compatible",
)

IMAGE = ArtifactRecord(
    artifact_id=f"{RUN_ID}-image-abcdef123456",
    run_id=RUN_ID,
    kind="image",
    filename="scene.png",
    content_type="image/png",
    size_bytes=100,
    reference=(
        f"runs/{RUN_ID}/artifacts/"
        f"{RUN_ID}-image-abcdef123456/scene.png"
    ),
    storage="s3-compatible",
)

OTHER_RUN = ArtifactRecord(
    artifact_id="run-20260818-231501-video-abcdef123456",
    run_id="run-20260818-231501",
    kind="video",
    filename="other.mp4",
    content_type="video/mp4",
    size_bytes=999,
    reference=(
        "runs/run-20260818-231501/artifacts/"
        "run-20260818-231501-video-abcdef123456/other.mp4"
    ),
    storage="s3-compatible",
)


# A. Construcción
store = FakeArtifactStore([VIDEO, IMAGE, OTHER_RUN])
access = ArtifactAccess(store)

check(
    access._store is store,
    "A1 ArtifactAccess conserva el ArtifactStore inyectado",
)


# B. Video correcto
found = access.get_video(RUN_ID)

check(
    found == VIDEO,
    "B1 get_video devuelve el artifact de video correcto",
)


# C. Aísla por run
store_other = FakeArtifactStore([OTHER_RUN])
access_other = ArtifactAccess(store_other)

check(
    access_other.get_video(RUN_ID) is None,
    "C1 get_video no devuelve video de otro run",
)


# D. Ignora artifacts que no sean video
store_no_video = FakeArtifactStore([IMAGE])
access_no_video = ArtifactAccess(store_no_video)

check(
    access_no_video.get_video(RUN_ID) is None,
    "D1 get_video ignora artifacts cuyo kind no es video",
)


# E. Consulta exactamente el run solicitado
check(
    store.list_calls == [RUN_ID],
    "E1 get_video consulta list() con el run solicitado",
)


# F. Sin artifacts
empty_store = FakeArtifactStore([])
empty_access = ArtifactAccess(empty_store)

check(
    empty_access.get_video(RUN_ID) is None,
    "F1 get_video devuelve None si no existen artifacts",
)


# G. Run inválido
invalid_store = FakeArtifactStore([])
invalid_access = ArtifactAccess(invalid_store)

try:
    invalid_access.get_video("run-invalid")
except Exception:
    print("[OK] G1 run_id inválido rechazado")
else:
    raise AssertionError("G1 run_id inválido debería ser rechazado")


print()
print("RESULTADO: PASS")
