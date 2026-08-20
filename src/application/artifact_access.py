"""Capa de acceso lógico a artefactos de una ejecución.

ME40.9D.1:
- Reutiliza ArtifactStore como única fuente de metadata.
- No conoce filesystem, S3, R2 ni URLs.
- Permite localizar el artifact de video asociado a un run.

ME40.9D.3.3:
- ``get_video_temporary_url`` delega en :meth:`ArtifactStore.get_temporary_url`
  para obtener una URL temporal firmada del video (si el backend puede firmar).
- El store es la única fuente de URLs temporales; esta capa no genera URLs.
"""

from __future__ import annotations

from typing import Optional

from application.artifacts import (
    ArtifactRecord,
    ArtifactStore,
    ArtifactValidationError,
    validate_expires_in,
)
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

    def list_artifacts(self, run_id: str) -> list[ArtifactRecord]:
        """Devuelve todos los artifacts publicados de un run (o lista vacía).

        La búsqueda se limita al ``run_id`` solicitado. No accede directamente
        al filesystem ni descarga objetos.
        """
        try:
            validated_run_id = validate_run_id(run_id)
        except PipelineValidationError as exc:
            raise ArtifactValidationError(str(exc)) from exc

        records = self._store.list(validated_run_id)

        return [
            record
            for record in records
            if record.run_id == validated_run_id
        ]

    def get_video_temporary_url(self, run_id: str, expires_in: int) -> Optional[str]:
        """URL temporal firmada del video de un run, si existe.

        Valida ``run_id`` y ``expires_in``, localiza exclusivamente el artifact
        de video del run (aislamiento por run) y delega la emisión de la URL en
        :meth:`ArtifactStore.get_temporary_url` (el store es la única fuente de
        URLs temporales; esta capa no genera URLs). Si el run no tiene video
        publicada devuelve ``None``.

        Args:
            run_id: identificador de la ejecución.
            expires_in: validez de la URL en segundos (entero positivo).

        Returns:
            URL temporal firmada del video, o ``None`` si el run no tiene video.

        Raises:
            ArtifactValidationError: si ``run_id`` o ``expires_in`` son
                inválidos.
            ArtifactUrlUnavailableError: si el backend no puede firmar.
            ArtifactError: si el store falla al emitir la URL.
        """
        try:
            validated_run_id = validate_run_id(run_id)
        except PipelineValidationError as exc:
            raise ArtifactValidationError(str(exc)) from exc
        validate_expires_in(expires_in)

        video = self.get_video(validated_run_id)
        if video is None:
            return None

        return self._store.get_temporary_url(video.artifact_id, expires_in)
