"""Registro de artefactos generados por un run.

ME40.9A: abstracción mínima de artefactos. El worker produce
``output/runs/<run_id>/output/video/<filename>.mp4``; esta metadata se
registra de forma local (sin copiar ni mover el archivo) para que el control
plane pueda publicarla más adelante. No modifica ``PipelineResult``,
``RunRecord``, ``RunStorage`` ni la API pública.

El artefacto físico NO se toca: solo se lee (stat) para calcular el tamaño y
la referencia se guarda SIEMPRE relativa al directorio del run.
"""

from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from pipeline.context import validate_run_id
from pipeline.exceptions import PipelineValidationError
from pipeline.lifecycle import checked_run_dir

logger = logging.getLogger(__name__)

ARTIFACTS_FILE = "artifacts.json"
DEFAULT_KIND = "video"
LOCAL_STORAGE = "local"

_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}
_DEFAULT_CONTENT_TYPE = "application/octet-stream"


class ArtifactError(Exception):
    """Error base del módulo de artefactos."""


class ArtifactValidationError(ArtifactError):
    """Metadata o ruta de artefacto inválida (traversal, run_id, campos)."""


class ArtifactNotFoundError(ArtifactError):
    """El artefacto solicitado no existe (o el archivo físico no está)."""


def content_type_for(filename: str) -> str:
    """Devuelve el ``Content-Type`` estimado a partir de la extensión."""
    suffix = Path(filename).suffix.lower()
    return _CONTENT_TYPES.get(suffix, _DEFAULT_CONTENT_TYPE)


@dataclass(frozen=True)
class ArtifactRecord:
    """Metadata inmutable de un artefacto generado por un run."""

    artifact_id: str
    run_id: str
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    reference: str
    storage: str

    def to_dict(self) -> dict[str, Any]:
        """Devuelve la representación serializable del registro."""
        return {
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "reference": self.reference,
            "storage": self.storage,
        }

    @classmethod
    def from_dict(cls, data: object) -> ArtifactRecord:
        """Reconstruye un registro desde un ``dict`` (o eleva error)."""
        if not isinstance(data, dict):
            raise ArtifactValidationError("ArtifactRecord.from_dict requiere un dict.")
        artifact_id = data.get("artifact_id")
        run_id = data.get("run_id")
        kind = data.get("kind")
        filename = data.get("filename")
        content_type = data.get("content_type")
        size_bytes = data.get("size_bytes")
        reference = data.get("reference")
        storage = data.get("storage")
        for nombre, valor in (
            ("artifact_id", artifact_id),
            ("run_id", run_id),
            ("kind", kind),
            ("filename", filename),
            ("content_type", content_type),
            ("reference", reference),
            ("storage", storage),
        ):
            if not isinstance(valor, str) or not valor:
                raise ArtifactValidationError(f"Campo '{nombre}' inválido o ausente.")
        if not isinstance(size_bytes, int) or size_bytes < 0:
            raise ArtifactValidationError("size_bytes inválido o ausente.")
        return cls(
            artifact_id=artifact_id,
            run_id=run_id,
            kind=kind,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            reference=reference,
            storage=storage,
        )


class ArtifactStore(ABC):
    """Contrato de registro de artefactos (independiente del respaldo)."""

    @abstractmethod
    def publish(
        self, run_id: str, source: Path, *, kind: str = DEFAULT_KIND
    ) -> ArtifactRecord:
        """Registra la metadata de un artefacto físico existente.

        Args:
            run_id: identificador de la ejecución.
            source: ruta física del artefacto (dentro de ``runs_root/<run_id>``).
            kind: tipo lógico del artefacto (por defecto ``video``).

        Returns:
            El registro con la metadata derivada del archivo.

        Raises:
            ArtifactValidationError: si el run_id es inválido o el archivo
                queda fuera del directorio del run.
            ArtifactNotFoundError: si el archivo físico no existe.
        """

    @abstractmethod
    def list(self, run_id: str) -> list[ArtifactRecord]:
        """Devuelve los artefactos registrados para una ejecución."""

    @abstractmethod
    def get(self, artifact_id: str) -> Optional[ArtifactRecord]:
        """Devuelve un artefacto por id, o ``None`` si no existe."""


class LocalArtifactStore(ArtifactStore):
    """Registro local de artefactos dentro del directorio de cada run.

    La metadata se persiste en ``<run_dir>/artifacts.json``. El archivo físico
    NO se copia, NO se mueve y NO se modifica; la referencia guardada es
    siempre relativa al run (nunca absoluta).
    """

    def __init__(self, runs_root: Path) -> None:
        self._runs_root = Path(runs_root)

    # -- utilidades ---------------------------------------------------------

    def _run_dir(self, run_id: str) -> Path:
        try:
            validated = validate_run_id(run_id)
        except PipelineValidationError as exc:
            raise ArtifactValidationError(str(exc)) from exc
        return checked_run_dir(
            self._runs_root / validated, runs_root=self._runs_root
        )

    def _artifacts_path(self, run_dir: Path) -> Path:
        return run_dir / ARTIFACTS_FILE

    def _load(self, run_dir: Path) -> list[ArtifactRecord]:
        """Lee la metadata de forma defensiva: corrupta -> lista vacía."""
        path = self._artifacts_path(run_dir)
        if not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("artifacts.json corrupto en %s: %s", run_dir, exc)
            return []
        if not isinstance(raw, list):
            return []
        records: list[ArtifactRecord] = []
        for entrada in raw:
            try:
                records.append(ArtifactRecord.from_dict(entrada))
            except ArtifactValidationError:
                logger.warning("Entrada de artefacto inválida ignorada en %s.", run_dir)
        return records

    def _save(self, run_dir: Path, records: list[ArtifactRecord]) -> None:
        data = [record.to_dict() for record in records]
        path = self._artifacts_path(run_dir)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)

    def _resolve_source(self, run_dir: Path, source: Path) -> Path:
        """Resuelve y valida la ruta física del artefacto dentro del run."""
        candidate = source if source.is_absolute() else run_dir / source
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise ArtifactValidationError(
                f"No se puede resolver el artefacto {source}: {exc}"
            ) from exc
        run_root = run_dir.resolve()
        if not resolved.is_relative_to(run_root):
            raise ArtifactValidationError(
                f"El artefacto queda fuera del run (traversal): {source}"
            )
        return resolved

    # -- API ----------------------------------------------------------------

    def publish(
        self, run_id: str, source: Path, *, kind: str = DEFAULT_KIND
    ) -> ArtifactRecord:
        run_dir = self._run_dir(run_id)
        resolved = self._resolve_source(run_dir, Path(source))
        if not resolved.is_file():
            raise ArtifactNotFoundError(f"No existe el archivo del artefacto: {resolved}")
        size_bytes = resolved.stat().st_size
        reference = resolved.relative_to(run_dir.resolve()).as_posix()
        record = ArtifactRecord(
            artifact_id=f"{run_id}-{kind}-{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            kind=kind,
            filename=resolved.name,
            content_type=content_type_for(resolved.name),
            size_bytes=size_bytes,
            reference=reference,
            storage=LOCAL_STORAGE,
        )
        records = self._load(run_dir)
        records.append(record)
        self._save(run_dir, records)
        logger.info("Artefacto %s registrado para %s.", record.artifact_id, run_id)
        return record

    def list(self, run_id: str) -> list[ArtifactRecord]:
        run_dir = self._run_dir(run_id)
        return self._load(run_dir)

    def get(self, artifact_id: str) -> Optional[ArtifactRecord]:
        if not isinstance(artifact_id, str) or not artifact_id:
            return None
        root = self._runs_root
        if not root.is_dir():
            return None
        for entrada in root.iterdir():
            try:
                run_dir = self._run_dir(entrada.name)
            except ArtifactValidationError:
                continue
            for record in self._load(run_dir):
                if record.artifact_id == artifact_id:
                    return record
        return None
