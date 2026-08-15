"""Contexto de ejecución (``run``) del pipeline de AI Shorts Factory.

Cada ejecución del pipeline se identifica con un :class:`RunContext` que
establece su ``run_id`` (único y ordenable) y conoce la estructura de
directorios asociada: la raíz global de ejecuciones (``runs_root``) y, dentro
de ella, el directorio propio de la ejecución (``run_dir``) con sus
subdirectorios de entrada (``input/``) y salida (``output/``).

Convención de rutas (igual que en ``media.paths``, la raíz del proyecto se
deriva de la ubicación de este módulo):

- ``runs_root`` = ``<proyecto>/output/runs`` (raíz de todas las ejecuciones).
- ``run_dir`` = ``<runs_root>/<run_id>``.
- ``input_dir`` = ``<run_dir>/input`` (insumos de la ejecución).
- ``output_dir`` = ``<run_dir>/output`` (artefactos generados).

El módulo es **puro en cuanto a proveedores**: no depende de ``ai``, ``image``
ni ``audio``; solo usa la biblioteca estándar y el paquete ``pipeline``. Crea
los directorios de forma **implícita** al preparar el contexto (no se exige un
``mkdir`` manual por parte del llamador).

- :func:`generate_run_id`: genera un ``run_id`` nuevo y ordenable.
- :class:`RunDirectory`: acceso a un directorio de ejecución concreto.
- :class:`RunContext`: contexto de una ejecución (establece el ``run_id``).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .exceptions import PipelineNotFoundError, PipelineValidationError
from .models import _utc_now

logger = logging.getLogger(__name__)

#: Raíz del proyecto (dos niveles por encima de este módulo).
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Variable de entorno opcional que sobrescribe la raíz de ejecuciones
#: (aisla las pruebas de la salida real del repo).
RUNS_ROOT_ENV = "PIPELINE_RUNS_ROOT"

#: Raíz por defecto de todas las ejecuciones del pipeline. Se puede
#: sobrescribir con ``PIPELINE_RUNS_ROOT`` (por ejemplo en tests con árbol
#: temporal, sin tocar ``output/`` real).
RUNS_ROOT = Path(os.environ.get(RUNS_ROOT_ENV, PROJECT_ROOT / "output" / "runs"))

#: Expresión regular del formato de ``run_id``: ``run-YYYYMMDD-HHMMSS`` (UTC).
RUN_ID_PATTERN = re.compile(r"^run-\d{8}-\d{6}$")


def generate_run_id(now: datetime | None = None) -> str:
    """Genera un ``run_id`` único y ordenable.

    Formato: ``run-YYYYMMDD-HHMMSS`` en UTC. El componente de hora permite
    ordenar ejecuciones cronológicamente por nombre.

    Args:
        now: momento de referencia; por defecto la hora UTC actual.

    Returns:
        Identificador de ejecución, por ejemplo ``"run-20260811-143505"``.
    """
    moment = now or datetime.now(timezone.utc)
    return f"run-{moment.strftime('%Y%m%d-%H%M%S')}"


def is_valid_run_id(value: object) -> bool:
    """Indica si ``value`` cumple el formato de :func:`RUN_ID_PATTERN`.

    Args:
        value: valor a comprobar.

    Returns:
        ``True`` si es una cadena con el formato ``run-YYYYMMDD-HHMMSS``.
    """
    return isinstance(value, str) and RUN_ID_PATTERN.match(value) is not None


def validate_run_id(value: object) -> str:
    """Valida un ``run_id`` y devuelve la cadena si es válida.

    Args:
        value: identificador de ejecución.

    Returns:
        El ``run_id`` como cadena normalizada.

    Raises:
        PipelineValidationError: si ``value`` no es un ``run_id`` válido.
    """
    if not is_valid_run_id(value):
        raise PipelineValidationError(
            [
                f"'{value}' no es un run_id válido. Formato esperado: "
                "'run-YYYYMMDD-HHMMSS' (UTC)."
            ]
        )
    return str(value)


@dataclass(frozen=True)
class RunDirectory:
    """Acceso de solo lectura a un directorio de ejecución.

    Attributes:
        run_id: identificador de la ejecución (``run-YYYYMMDD-HHMMSS``).
        runs_root: raíz global de ejecuciones.
    """

    run_id: str
    runs_root: Path = RUNS_ROOT

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        object.__setattr__(self, "runs_root", Path(self.runs_root))

    @property
    def run_dir(self) -> Path:
        """Directorio propio de la ejecución (``runs_root / run_id``)."""
        return self.runs_root / self.run_id

    @property
    def input_dir(self) -> Path:
        """Directorio de entrada de la ejecución (``run_dir / input``)."""
        return self.run_dir / "input"

    @property
    def output_dir(self) -> Path:
        """Directorio de salida de la ejecución (``run_dir / output``)."""
        return self.run_dir / "output"

    def exists(self) -> bool:
        """Indica si el directorio de la ejecución existe en disco."""
        return self.run_dir.is_dir()

    def resolve_output(self, relative: str | Path) -> Path:
        """Resuelve una ruta relativa dentro del directorio de salida.

        Args:
            relative: ruta relativa (sin ``..`` ni rutas absolutas).

        Returns:
            Ruta absoluta dentro de :attr:`output_dir`.

        Raises:
            PipelineValidationError: si la ruta no es relativa segura.
        """
        candidate = Path(relative)
        if candidate.is_absolute() or bool(candidate.drive) or bool(candidate.root):
            raise PipelineValidationError(
                [f"La ruta debe ser relativa al directorio de salida: {relative!r}"]
            )
        if ".." in candidate.parts or "\x00" in str(candidate):
            raise PipelineValidationError(
                [f"Ruta relativa no segura dentro del run: {relative!r}"]
            )
        return self.output_dir / candidate


@dataclass(frozen=True)
class RunContext:
    """Contexto de una ejecución del pipeline.

    Establece el ``run_id`` y expone la estructura de directorios de la
    ejecución (raíz global, directorio de la ejecución y subdirectorios de
    entrada/salida) delegando en :class:`RunDirectory`.

    Attributes:
        run_id: identificador de la ejecución (``run-YYYYMMDD-HHMMSS``).
        runs_root: raíz global de ejecuciones.
    """

    run_id: str
    runs_root: Path = RUNS_ROOT

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        object.__setattr__(self, "runs_root", Path(self.runs_root))

    @property
    def directory(self) -> RunDirectory:
        """:class:`RunDirectory` correspondiente a esta ejecución."""
        return RunDirectory(run_id=self.run_id, runs_root=self.runs_root)

    @property
    def run_dir(self) -> Path:
        """Directorio propio de la ejecución."""
        return self.directory.run_dir

    @property
    def input_dir(self) -> Path:
        """Directorio de entrada de la ejecución."""
        return self.directory.input_dir

    @property
    def output_dir(self) -> Path:
        """Directorio de salida de la ejecución."""
        return self.directory.output_dir

    def prepare(self) -> RunDirectory:
        """Crea (si no existen) los directorios de la ejecución.

        Crea de forma **implícita** ``run_dir/input`` y ``run_dir/output`` y
        escribe el estado ``RUNNING`` del run en ``run_dir/run.json`` (escritura
        atómica, idempotente: en un re-run conserva el ``created_at`` original).
        Idempotente: si los directorios ya existen, no los recrea.

        Returns:
            El :class:`RunDirectory` ya preparado (los directorios existen).
        """
        directory = self.directory
        for path in (directory.run_dir, directory.input_dir, directory.output_dir):
            if not path.exists():
                logger.info("Creando directorio de ejecución: %s", path)
                path.mkdir(parents=True, exist_ok=True)
        self._mark_running(directory.run_dir)
        return directory

    def _mark_running(self, run_dir: Path) -> None:
        """Escribe (o actualiza) el estado del run a ``RUNNING``."""
        from .lifecycle import RunRecord, RunStatus, load_run_record, write_run_record

        previous = load_run_record(run_dir)
        now = _utc_now()
        write_run_record(
            run_dir,
            RunRecord(
                run_id=self.run_id,
                status=RunStatus.RUNNING,
                created_at=previous.created_at if previous else now,
                started_at=now,
            ),
        )

    @classmethod
    def create(cls, *, now: datetime | None = None) -> "RunContext":
        """Crea un contexto nuevo con un ``run_id`` generado automáticamente.

        Args:
            now: momento de referencia para el ``run_id``.

        Returns:
            Contexto listo para usarse; no crea directorios hasta :meth:`prepare`.
        """
        return cls(run_id=generate_run_id(now=now))

    @classmethod
    def from_directory(cls, run_dir: Path, *, runs_root: Path = RUNS_ROOT) -> "RunContext":
        """Construye un contexto desde un directorio de ejecución existente.

        Args:
            run_dir: directorio de la ejecución (debe llamarse igual que el
                ``run_id``).
            runs_root: raíz global de ejecuciones esperada.

        Returns:
            Contexto de la ejecución contenida en ``run_dir``.

        Raises:
            PipelineNotFoundError: si ``run_dir`` no existe en ``runs_root``.
        """
        candidate = Path(run_dir)
        if not candidate.is_dir():
            raise PipelineNotFoundError(
                f"No existe el directorio de ejecución: {candidate}"
            )
        run_id = validate_run_id(candidate.name)
        return cls(run_id=run_id, runs_root=runs_root)
