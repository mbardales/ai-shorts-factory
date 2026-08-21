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


class ArtifactUrlUnavailableError(ArtifactError):
    """El backend no puede emitir una URL temporal de descarga del artefacto.

    Se eleva de forma explícita (nunca se inventa una URL pública) cuando el
    respaldo actual no soporta URLs temporales firmadas: el backend local (que
    sirve a través del control plane) o un cliente S3 aún sin capacidad de
    presign (ME40.9D.3.1).
    """


class ArtifactContentUnavailableError(ArtifactError):
    """El backend no puede servir el contenido del artefacto en línea.

    Solo los backends que custodian el archivo físicamente (p. ej. el local)
    pueden leerlo y entregarlo; los backends remotos entregan mediante URLs
    temporales firmadas (nunca descargando en nombre del cliente, ME40.9E).
    """


def validate_expires_in(expires_in: object) -> int:
    """Valida la expiración de una URL temporal (segundos, entero positivo).

    Args:
        expires_in: validez solicitada en segundos.

    Returns:
        El valor normalizado (entero positivo).

    Raises:
        ArtifactValidationError: si no es un entero positivo (``bool`` no vale).
    """
    if isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in <= 0:
        raise ArtifactValidationError(
            "expires_in debe ser un entero positivo (segundos)."
        )
    return expires_in


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

    @abstractmethod
    def get_temporary_url(self, artifact_id: str, expires_in: int) -> str:
        """Obtiene una URL temporal de descarga de un artefacto privado.

        Prepara la entrega de artefactos (ME40.9D.3.1): el artefacto es
        privado, por lo que una URL temporal con expiración limitada solo se
        emite si el backend puede firmarla de forma segura. Ninguna
        implementación genera URLs públicas ni expone credenciales.

        Args:
            artifact_id: identificador único del artefacto.
            expires_in: validez de la URL en segundos (entero positivo).

        Returns:
            URL temporal de descarga con expiración limitada.

        Raises:
            ArtifactValidationError: si ``artifact_id`` o ``expires_in`` son
                inválidos.
            ArtifactNotFoundError: si no existe el artefacto.
            ArtifactUrlUnavailableError: si el backend no puede emitir una URL
                temporal segura (p. ej. local o cliente S3 sin presign).
        """

    @property
    def supports_temporary_url(self) -> bool:
        """Indica si el backend emite URLs temporales firmadas (ME40.9E).

        Por defecto ``True`` (los backends remotos entregan por presign); los
        backends que custodian el archivo localmente lo sobrescriben a
        ``False`` y sirven el contenido vía :meth:`read_content`.
        """
        return True

    def read_content(self, artifact_id: str) -> bytes:
        """Lee el contenido físico del artefacto (solo backends custodios).

        Implementación por defecto: **ninguna**. Los backends remotos no
        descargan objetos en nombre del cliente (la entrega es por URL
        temporal); solo el backend que custodia el archivo localmente
        (:class:`LocalArtifactStore`) implementa la lectura segura.

        Args:
            artifact_id: identificador único del artefacto.

        Returns:
            Contenido binario completo del artefacto.

        Raises:
            ArtifactContentUnavailableError: si el backend no sirve contenido
                en línea (comportamiento por defecto).
        """
        raise ArtifactContentUnavailableError(
            "El backend de artefactos no sirve contenido en línea; use "
            "get_temporary_url."
        )

    def describe(self) -> dict[str, str]:
        """Huella operativa no sensible del backend (ME40.9F).

        Se usa para detectar configuraciones divergentes entre procesos
        (API vs worker) sin exponer credenciales: solo identidad de backend
        y parámetros operativos públicos (p. ej. bucket, host del endpoint).
        Los stores concretos la sobrescriben; el valor por defecto es
        deliberadamente neutro para stores inyectados que no la declaren.
        """
        return {"backend": "unknown"}


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

    def get_temporary_url(self, artifact_id: str, expires_in: int) -> str:
        """URL temporal de descarga de un artefacto privado (backend local).

        El backend local NO emite URLs públicas: el artefacto es privado y se
        servirá a través del control plane. Comportamiento explícito y seguro:
        valida la entrada, comprueba que el artefacto exista y eleva
        :class:`ArtifactUrlUnavailableError` (nunca inventa una URL).
        """
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ArtifactValidationError("artifact_id inválido o ausente.")
        validate_expires_in(expires_in)
        if self.get(artifact_id) is None:
            raise ArtifactNotFoundError(f"No existe el artefacto: {artifact_id}")
        raise ArtifactUrlUnavailableError(
            "El backend local no emite URLs temporales de descarga: el "
            "artefacto es privado y se servirá a través del control plane, "
            "no con una URL pública."
        )

    @property
    def supports_temporary_url(self) -> bool:
        """El backend local custodia el archivo: entrega inline, sin presign."""
        return False

    def read_content(self, artifact_id: str) -> bytes:
        """Lee el contenido físico del artefacto (entrega segura ME40.9E).

        Única vía de lectura del archivo: resuelve el registro por
        ``artifact_id``, reconstruye el directorio del run validado y resuelve
        la referencia **dentro** del run (guard de traversal), manteniendo el
        aislamiento estricto por ``run_id``. Nunca sirve archivos fuera del
        run ni rutas absolutas del cliente.

        Args:
            artifact_id: identificador único del artefacto.

        Returns:
            Contenido binario completo del artefacto.

        Raises:
            ArtifactValidationError: si ``artifact_id`` es inválido o la
                referencia queda fuera del directorio del run (traversal).
            ArtifactNotFoundError: si el registro o el archivo físico no
                existen.
        """
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ArtifactValidationError("artifact_id inválido o ausente.")
        record = self.get(artifact_id)
        if record is None:
            raise ArtifactNotFoundError(f"No existe el artefacto: {artifact_id}")
        run_dir = self._run_dir(record.run_id)
        candidate = run_dir / record.reference
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise ArtifactValidationError(
                f"No se puede resolver el artefacto {record.reference}: {exc}"
            ) from exc
        if not resolved.is_relative_to(run_dir.resolve()):
            raise ArtifactValidationError(
                f"El artefacto queda fuera del run (traversal): {record.reference}"
            )
        if not resolved.is_file():
            raise ArtifactNotFoundError(
                f"No existe el archivo del artefacto: {record.reference}"
            )
        return resolved.read_bytes()

    def describe(self) -> dict[str, str]:
        """Huella del backend local: solo identidad (sin rutas ni secretos)."""
        return {"backend": "local"}
