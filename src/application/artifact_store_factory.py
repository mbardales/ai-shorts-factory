"""Fábrica de :class:`ArtifactStore` según configuración (ME40.9C.2).

Selecciona el backend de artefactos mediante variables de entorno
(``OBJECT_STORAGE_*``) sin acoplar el código al nombre del proveedor:

- ``OBJECT_STORAGE_BACKEND=local`` (o vacío): :class:`LocalArtifactStore`.
- ``OBJECT_STORAGE_BACKEND=s3``: :class:`S3CompatibleArtifactStore` con un
  cliente boto3 real construido AQUÍ (import perezoso de ``boto3``).

:class:`S3CompatibleArtifactStore` sigue recibiendo un cliente compatible y
nunca crea conexiones HTTP por sí mismo. Las credenciales se leen de
``os.environ``, se pasan al cliente boto3 y NUNCA se loguean, nunca se
incluyen en :class:`ArtifactRecord`, nunca se escriben en disco y nunca se
generan URLs públicas: el endpoint, la región y el bucket provienen
exclusivamente de la configuración.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from application.artifacts import ArtifactStore, LocalArtifactStore
from application.s3_artifacts import S3CompatibleArtifactStore

logger = logging.getLogger(__name__)

OBJECT_STORAGE_BACKEND = "OBJECT_STORAGE_BACKEND"
OBJECT_STORAGE_ENDPOINT = "OBJECT_STORAGE_ENDPOINT"
OBJECT_STORAGE_BUCKET = "OBJECT_STORAGE_BUCKET"
OBJECT_STORAGE_REGION = "OBJECT_STORAGE_REGION"
OBJECT_STORAGE_ACCESS_KEY = "OBJECT_STORAGE_ACCESS_KEY"
OBJECT_STORAGE_SECRET_KEY = "OBJECT_STORAGE_SECRET_KEY"

BACKEND_LOCAL = "local"
BACKEND_S3 = "s3"
DEFAULT_REGION = "auto"


class ArtifactStoreConfigError(Exception):
    """Configuración de almacenamiento de artefactos inválida o incompleta."""


@dataclass(frozen=True)
class S3ListedObject:
    """Objeto S3 listado junto con su metadata (sin descargar el contenido)."""

    key: str
    metadata: dict[str, str]


class Boto3S3Client:
    """Adaptador de :class:`S3Client` sobre boto3 (import perezoso).

    No expone credenciales: recibe endpoint/región/keys, construye el cliente
    boto3 de forma perezosa y traduce las operaciones del protocolo S3 mínimo.

    Args:
        endpoint: endpoint S3-compatible (exclusivamente de configuración).
        region: región S3 (por defecto ``auto``).
        access_key: clave de acceso (nunca se loguea ni se expone).
        secret_key: clave secreta (nunca se loguea ni se expone).

    Raises:
        ArtifactStoreConfigError: si faltan credenciales/endpoint o si boto3
            no está instalado.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        if not endpoint or not access_key or not secret_key:
            raise ArtifactStoreConfigError(
                "Faltan endpoint o credenciales para el cliente S3 real."
            )
        try:
            import boto3  # import perezoso: solo en este módulo y al usarlo
        except ImportError as exc:
            raise ArtifactStoreConfigError(
                "boto3 no está instalado; es necesario para el backend 's3'."
            ) from exc
        self._endpoint = endpoint
        self._region = region
        self._raw = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def put_object(
        self,
        bucket: str,
        key: str,
        *,
        body: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> object:
        return self._raw.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            Metadata=metadata,
        )

    def list_objects(self, bucket: str, prefix: str) -> list[S3ListedObject]:
        entries: list[S3ListedObject] = []
        paginator = self._raw.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                head = self._raw.head_object(Bucket=bucket, Key=obj["Key"])
                metadata = dict(head.get("Metadata") or {})
                entries.append(S3ListedObject(key=obj["Key"], metadata=metadata))
        return entries

    def presign_get_url(self, bucket: str, key: str, *, expires_in: int) -> str:
        """Firma una URL temporal (GET) para un objeto privado del bucket.

        Delega en ``generate_presigned_url`` del cliente boto3 (sin descargar
        el objeto). ``expires_in`` es la validez en segundos; el bucket
        permanece privado (URL firmada, nunca pública permanente).
        """
        return self._raw.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _validate_s3_config(
    endpoint: str, bucket: str, access_key: str, secret_key: str
) -> None:
    """Valida la configuración S3 y eleva un error claro si falta algo."""
    faltantes = []
    if not endpoint:
        faltantes.append("OBJECT_STORAGE_ENDPOINT")
    if not bucket:
        faltantes.append("OBJECT_STORAGE_BUCKET")
    if not access_key:
        faltantes.append("OBJECT_STORAGE_ACCESS_KEY")
    if not secret_key:
        faltantes.append("OBJECT_STORAGE_SECRET_KEY")
    if faltantes:
        raise ArtifactStoreConfigError(
            "Configuración S3 incompleta; faltan: "
            + ", ".join(faltantes)
            + "."
        )


def build_artifact_store(runs_root: Path) -> ArtifactStore:
    """Construye el :class:`ArtifactStore` según ``OBJECT_STORAGE_BACKEND``.

    Args:
        runs_root: raíz de ejecuciones (usada por :class:`LocalArtifactStore`).

    Returns:
        :class:`LocalArtifactStore` (backend ``local``/vacío) o
        :class:`S3CompatibleArtifactStore` (backend ``s3``).

    Raises:
        ArtifactStoreConfigError: si el backend es desconocido o la
            configuración S3 está incompleta.
    """
    backend = _env(OBJECT_STORAGE_BACKEND).lower()
    if not backend or backend == BACKEND_LOCAL:
        return LocalArtifactStore(runs_root)
    if backend == BACKEND_S3:
        endpoint = _env(OBJECT_STORAGE_ENDPOINT)
        bucket = _env(OBJECT_STORAGE_BUCKET)
        region = _env(OBJECT_STORAGE_REGION) or DEFAULT_REGION
        access_key = os.environ.get(OBJECT_STORAGE_ACCESS_KEY, "").strip()
        secret_key = os.environ.get(OBJECT_STORAGE_SECRET_KEY, "").strip()
        _validate_s3_config(endpoint, bucket, access_key, secret_key)
        client = Boto3S3Client(
            endpoint=endpoint,
            region=region,
            access_key=access_key,
            secret_key=secret_key,
        )
        logger.info("Almacenamiento de artefactos: s3 (bucket=%s).", bucket)
        return S3CompatibleArtifactStore(
            bucket=bucket,
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            client=client,
        )
    raise ArtifactStoreConfigError(
        f"OBJECT_STORAGE_BACKEND desconocido: {backend!r} "
        f"(se espera '{BACKEND_LOCAL}' o '{BACKEND_S3}')."
    )