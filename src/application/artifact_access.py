"""Capa de acceso lógico a artefactos de una ejecución.

ME40.9D.1:
- Reutiliza ArtifactStore como única fuente de metadata.
- No conoce filesystem, S3, R2 ni URLs.
- Permite localizar el artifact de video asociado a un run.
"""

from __future__ import annotations

from typing import Optional

from application.artifacts import ArtifactRecord, ArtifactStore, ArtifactValidationError
from pipeline.context import validate_run_id
from pipeline.exceptions import PipelineValidationError


class ArtifactAccess:
    """Consulta artifacts publicados independientemente de su almacenamiento."""

    def __init__(self, store: ArtifactStore) -> None:
        if store is None:
            raise ValueError("store es obligatorio.")
        self._store = store

    def get_video(self, run_id: str) -> Optional[ArtifactRecord]:
        """Devuelve el artifact de video de un run, si existe.

        La búsqueda se limita al ``run_id`` solicitado y al ``kind == "video"``.
        No accede directamente al filesystem ni descarga objetos.
        """
        try:
            validated_run_id = validate_run_id(run_id)
        except PipelineValidationError as exc:
            raise ArtifactValidationError(str(exc)) from exc

        records = self._store.list(validated_run_id)

        for record in records:
            if (
                record.run_id == validated_run_id
                and record.kind == "video"
            ):
                return record

        return None
