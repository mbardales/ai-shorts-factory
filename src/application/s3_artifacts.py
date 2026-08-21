"""Adaptador S3-compatible de :class:`ArtifactStore` (ME40.9C.1).

No conecta ningún proveedor real: no usa ``requests``/``urllib``, no crea
conexiones HTTP y no depende de ``boto3`` (aún no se instala nada). El cliente
S3 es una dependencia **inyectable** definida por un protocolo mínimo
(:class:`S3Client`); el adapter solo habla con ese cliente, de modo que
funciona conceptualmente con cualquier API S3-compatible (el nombre del
proveedor concreto no se acopla a este módulo).

Cada objeto se sube con una **object key determinista controlada por el
servidor** (nunca arbitraria del usuario):

    runs/<run_id>/artifacts/<artifact_id>/<filename>

La metadata del artefacto viaja como metadata de usuario del objeto; nunca se
incluyen credenciales y nunca se generan URLs públicas. ``get()``/``list()``
solo consultan metadata a través del cliente: no descargan el archivo.

``access_key``/``secret_key`` se aceptan en el constructor (para el futuro
cableado del cliente real en ME40.9C.2) pero este módulo jamás las loguea, no
las incluye en :class:`ArtifactRecord` y no las usa para crear conexiones.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable
from urllib.parse import urlsplit

from application.artifacts import (
    ArtifactError,
    ArtifactNotFoundError,
    ArtifactRecord,
    ArtifactStore,
    ArtifactUrlUnavailableError,
    ArtifactValidationError,
    content_type_for,
    validate_expires_in,
)
from pipeline.context import RUN_ID_PATTERN, validate_run_id
from pipeline.exceptions import PipelineValidationError

logger = logging.getLogger(__name__)

S3_STORAGE = "s3-compatible"
DEFAULT_REGION = "auto"
_RUN_PREFIX = "runs"
_ARTIFACTS_DIR = "artifacts"
_RUN_ID_LEN = 19  # len("run-YYYYMMDD-HHMMSS")


class S3ArtifactError(ArtifactError):
    """Error de aplicación al operar con el cliente S3 inyectado."""


@runtime_checkable
class S3Client(Protocol):
    """Protocolo mínimo de un cliente S3-compatible (inyectable).

    El adapter nunca construye conexiones HTTP por sí mismo: delega todas las
    operaciones en este cliente. Implementaciones reales (p. ej. boto3 o un
    wrapper) deben adaptarse a esta firma.
    """

    def put_object(
        self,
        bucket: str,
        key: str,
        *,
        body: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> object:
        """Sube un objeto con su metadata de usuario."""

    def list_objects(self, bucket: str, prefix: str) -> list[object]:
        """Lista objetos bajo un prefijo (cada entrada con ``.key`` y ``.metadata``)."""

    def presign_get_url(self, bucket: str, key: str, *, expires_in: int) -> str:
        """Genera una URL temporal firmada (GET) para un objeto privado.

        No descarga el objeto: solo firma una URL de descarga con expiración
        limitada (presign). ``expires_in`` es la validez en segundos.
        """


class S3CompatibleArtifactStore(ArtifactStore):
    """Registro de artefactos en un bucket S3-compatible.

    Args:
        bucket: nombre del bucket.
        endpoint: URL del endpoint S3-compatible.
        access_key: clave de acceso (se pasa en tiempo de ejecución, nunca
            hardcodeada; no se loguea ni se guarda en ArtifactRecord).
        secret_key: clave secreta (idem access_key).
        region: región (por defecto ``auto``).
        client: cliente S3 inyectable (necesario para operar; sin él las
            operaciones elevan :class:`S3ArtifactError`).
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str,
        access_key: str,
        secret_key: str,
        region: str = DEFAULT_REGION,
        client: Optional[S3Client] = None,
    ) -> None:
        if not isinstance(bucket, str) or not bucket.strip():
            raise ValueError("bucket es obligatorio.")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("endpoint es obligatorio.")
        if not isinstance(access_key, str) or not access_key.strip():
            raise ValueError("access_key es obligatoria.")
        if not isinstance(secret_key, str) or not secret_key.strip():
            raise ValueError("secret_key es obligatoria.")
        self._bucket = bucket.strip()
        self._endpoint = endpoint.strip()
        self._access_key = access_key.strip()
        self._secret_key = secret_key.strip()
        self._region = (region.strip() or DEFAULT_REGION)
        self._client = client

    def describe(self) -> dict[str, str]:
        """Huella operativa no sensible del backend S3 (ME40.9F).

        Solo expone identidad operativa pública: backend, bucket y host del
        endpoint (sin esquema, puerto, path, credenciales ni región). Jamás
        incluye ``access_key``/``secret_key`` ni la URL completa del endpoint.
        """
        host = urlsplit(self._endpoint).hostname or ""
        huella = {"backend": "s3", "bucket": self._bucket}
        if host:
            huella["endpoint_host"] = host
        return huella

    # -- utilidades internas -------------------------------------------------

    def _require_client(self) -> S3Client:
        if self._client is None:
            raise S3ArtifactError(
                "Sin cliente S3 inyectado: este adapter no crea conexiones "
                "HTTP por sí mismo."
            )
        return self._client

    @staticmethod
    def _validate_run_id(run_id: object) -> str:
        try:
            return validate_run_id(run_id)
        except PipelineValidationError as exc:
            raise ArtifactValidationError(str(exc)) from exc

    @staticmethod
    def _safe_filename(filename: str) -> str:
        """Devuelve un nombre de archivo seguro (solo basename, sin ``..``)."""
        if not isinstance(filename, str) or not filename:
            raise ArtifactValidationError("Nombre de archivo vacío.")
        if "/" in filename or "\\" in filename or "\x00" in filename:
            raise ArtifactValidationError(
                f"Nombre de archivo inseguro: {filename!r}"
            )
        name = Path(filename).name
        if not name or name in (".", ".."):
            raise ArtifactValidationError(
                f"Nombre de archivo inseguro: {filename!r}"
            )
        return name

    @staticmethod
    def _object_key(run_id: str, artifact_id: str, filename: str) -> str:
        return f"{_RUN_PREFIX}/{run_id}/{_ARTIFACTS_DIR}/{artifact_id}/{filename}"

    @staticmethod
    def _object_prefix(run_id: str, artifact_id: Optional[str] = None) -> str:
        base = f"{_RUN_PREFIX}/{run_id}/{_ARTIFACTS_DIR}/"
        return base + (f"{artifact_id}/" if artifact_id else "")

    @staticmethod
    def _run_id_from_artifact_id(artifact_id: object) -> Optional[str]:
        if not isinstance(artifact_id, str):
            return None
        candidato = artifact_id[:_RUN_ID_LEN]
        return candidato if RUN_ID_PATTERN.match(candidato) else None

    @staticmethod
    def _record_from_object(entry: object) -> Optional[ArtifactRecord]:
        """Reconstruye un ArtifactRecord desde la metadata del objeto (o None)."""
        meta = getattr(entry, "metadata", None)
        if not isinstance(meta, dict):
            return None
        datos = dict(meta)
        size = datos.get("size_bytes")
        try:
            datos["size_bytes"] = int(size)
        except (TypeError, ValueError):
            return None
        try:
            return ArtifactRecord.from_dict(datos)
        except ArtifactValidationError:
            return None

    # -- API ----------------------------------------------------------------

    def publish(
        self, run_id: object, source: Path, *, kind: str = "video"
    ) -> ArtifactRecord:
        run_id = self._validate_run_id(run_id)
        client = self._require_client()
        source = Path(source)
        if not source.is_file():
            raise ArtifactNotFoundError(
                f"No existe el archivo del artefacto: {source}"
            )
        filename = self._safe_filename(source.name)
        size_bytes = source.stat().st_size
        artifact_id = f"{run_id}-{kind}-{uuid.uuid4().hex[:12]}"
        key = self._object_key(run_id, artifact_id, filename)
        record = ArtifactRecord(
            artifact_id=artifact_id,
            run_id=run_id,
            kind=kind,
            filename=filename,
            content_type=content_type_for(filename),
            size_bytes=size_bytes,
            reference=key,
            storage=S3_STORAGE,
        )
        metadata = {
            clave: (str(valor) if isinstance(valor, int) else valor)
            for clave, valor in record.to_dict().items()
        }
        body = source.read_bytes()
        try:
            client.put_object(
                bucket=self._bucket,
                key=key,
                body=body,
                content_type=record.content_type,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 - error de cliente -> aplicación
            logger.exception("Fallo al subir el artefacto %s.", artifact_id)
            raise S3ArtifactError(
                f"No se pudo subir el artefacto a S3: {exc}"
            ) from exc
        logger.info("Artefacto %s subido a s3://%s/%s.", artifact_id, self._bucket, key)
        return record

    def list(self, run_id: object) -> list[ArtifactRecord]:
        run_id = self._validate_run_id(run_id)
        client = self._require_client()
        try:
            entries = client.list_objects(
                self._bucket, self._object_prefix(run_id)
            )
        except Exception as exc:  # noqa: BLE001 - error de cliente -> aplicación
            raise S3ArtifactError(f"No se pudo listar artefactos de S3: {exc}") from exc
        records: list[ArtifactRecord] = []
        for entry in entries:
            record = self._record_from_object(entry)
            if record is not None and record.run_id == run_id:
                records.append(record)
        return records

    def get(self, artifact_id: object) -> Optional[ArtifactRecord]:
        client = self._require_client()
        run_id = self._run_id_from_artifact_id(artifact_id)
        if run_id is None:
            return None
        try:
            entries = client.list_objects(
                self._bucket, self._object_prefix(run_id, artifact_id)
            )
        except Exception as exc:  # noqa: BLE001 - error de cliente -> aplicación
            raise S3ArtifactError(f"No se pudo recuperar el artefacto de S3: {exc}") from exc
        for entry in entries:
            record = self._record_from_object(entry)
            if record is not None and record.artifact_id == artifact_id:
                return record
        return None

    def get_temporary_url(self, artifact_id: object, expires_in: int) -> str:
        """URL temporal firmada (presign) de un artefacto privado (ME40.9D.3.2).

        Valida la entrada, localiza el artefacto **sin descargarlo** (solo
        metadata vía :meth:`get`) y delega la firma en el cliente inyectado
        (``presign_get_url``). La URL es temporal (nunca pública permanente),
        el bucket permanece privado y no se exponen credenciales.

        Args:
            artifact_id: identificador único del artefacto.
            expires_in: validez de la URL en segundos (entero positivo).

        Returns:
            URL temporal firmada (GET) con expiración limitada.

        Raises:
            ArtifactValidationError: si ``artifact_id`` o ``expires_in`` son
                inválidos.
            ArtifactNotFoundError: si no existe el artefacto.
            ArtifactUrlUnavailableError: si el cliente inyectado no soporta
                presign.
            S3ArtifactError: si el cliente falla al firmar la URL.
        """
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ArtifactValidationError("artifact_id inválido o ausente.")
        validate_expires_in(expires_in)
        client = self._require_client()
        record = self.get(artifact_id)
        if record is None:
            raise ArtifactNotFoundError(f"No existe el artefacto: {artifact_id}")
        presign = getattr(client, "presign_get_url", None)
        if not callable(presign):
            raise ArtifactUrlUnavailableError(
                "El cliente S3 inyectado no soporta generar URLs temporales "
                "firmadas (presign)."
            )
        try:
            return presign(
                bucket=self._bucket,
                key=record.reference,
                expires_in=expires_in,
            )
        except ArtifactUrlUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - error de cliente -> aplicación
            raise S3ArtifactError(
                f"No se pudo generar la URL temporal del artefacto "
                f"{artifact_id}: {exc}"
            ) from exc
