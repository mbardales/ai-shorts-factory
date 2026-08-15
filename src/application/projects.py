"""Registro de proyectos dentro de ``runs_root`` (ME36.3).

Persistencia **simple y sin base de datos**: un único ``projects.json`` en la
raíz de ejecuciones guarda los proyectos, el proyecto activo y la asociación
``run_id -> project_id`` de cada run creado por la API.

La asociación se escribe de forma atómica (``.tmp`` + ``os.replace``) y la
lectura es defensiva (un archivo ausente o corrupto se degrada a un registro
vacío). El ``project_id`` **nunca** se usa para construir rutas del filesystem
(solo como clave del JSON); aun así se valida su formato estricto en el borde
de la API para rechazar cualquier intento de path traversal (``/``, ``\\``,
``..``, espacios, etc.).

- :func:`validate_project_id`: formato estricto ``[A-Za-z0-9][A-Za-z0-9_-]*``.
- :func:`create_project`, :func:`list_projects`, :func:`set_active_project`.
- :func:`associate_run`, :func:`project_id_for_run`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .exceptions import ApplicationProjectNotFoundError, ApplicationValidationError
from .models import ProjectRecord

logger = logging.getLogger(__name__)

#: Nombre del archivo de registro dentro de ``runs_root``.
PROJECTS_FILE = "projects.json"

#: Prefijo del archivo temporal usado para la escritura atómica.
_TMP_SUFFIX = ".tmp"

#: Formato estricto de ``project_id`` (sin separadores de ruta, sin ``..``).
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def validate_project_id(value: object) -> str:
    """Valida un ``project_id`` y devuelve la cadena si es seguro.

    Rechaza cualquier valor que no cumpla el formato estricto (solo letras,
    números, ``_`` y ``-``), lo que impide path traversal y rutas arbitrarias.

    Args:
        value: identificador de proyecto propuesto.

    Returns:
        El ``project_id`` como cadena normalizada.

    Raises:
        ApplicationValidationError: si el valor no es seguro.
    """
    if not isinstance(value, str) or not PROJECT_ID_PATTERN.match(value):
        raise ApplicationValidationError(
            "'project_id' inválido: solo letras, números, '_' y '-' "
            "(sin rutas, separadores ni '..')."
        )
    return value


def project_id_from_name(name: str, existing: set[str]) -> str:
    """Genera un ``project_id`` a partir del nombre (slug único).

    Normaliza el nombre a minúsculas y caracteres seguros; si el resultado
    colisiona con los ids existentes, añade un sufijo numérico.

    Args:
        name: nombre del proyecto.
        existing: ids ya registrados.

    Returns:
        Un ``project_id`` seguro y único.
    """
    base = re.sub(r"[^A-Za-z0-9]+", "-", name.strip().lower()).strip("-")
    base = base[:40] or "proyecto"
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _registry_path(runs_root: Path) -> Path:
    """Ruta del archivo de registro dentro de ``runs_root``."""
    return Path(runs_root) / PROJECTS_FILE


def load_registry(runs_root: Path) -> dict:
    """Lee el registro de proyectos de forma defensiva.

    Un archivo ausente o corrupto se degrada a un registro vacío (sin lanzar).
    """
    path = _registry_path(runs_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        logger.warning("projects.json ilegible o ausente en: %s", path)
    return {"active_project_id": None, "projects": [], "run_projects": {}}


def save_registry(runs_root: Path, registry: dict) -> Path:
    """Persiste el registro de proyectos de forma atómica."""
    path = _registry_path(runs_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{PROJECTS_FILE}{_TMP_SUFFIX}")
    payload = json.dumps(registry, ensure_ascii=False, indent=2)
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    logger.debug("projects.json escrito en: %s", path)
    return path


def _parse_projects(raw: object) -> list[ProjectRecord]:
    """Convierte la lista cruda del JSON a :class:`ProjectRecord`."""
    projects: list[ProjectRecord] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        project_id = item.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            continue
        description = item.get("description")
        created_at = item.get("created_at")
        projects.append(
            ProjectRecord(
                project_id=project_id,
                name=str(item.get("name") or project_id),
                description=description if isinstance(description, str) else None,
                created_at=created_at if isinstance(created_at, str) else None,
            )
        )
    return projects


def create_project(
    runs_root: Path, name: str, description: Optional[str] = None
) -> ProjectRecord:
    """Crea un proyecto y lo deja como activo.

    Args:
        runs_root: raíz de ejecuciones.
        name: nombre del proyecto (no vacío).
        description: descripción opcional.

    Returns:
        El :class:`ProjectRecord` creado.

    Raises:
        ApplicationValidationError: si el nombre está vacío.
    """
    clean_name = (name or "").strip()
    if not clean_name:
        raise ApplicationValidationError("El nombre del proyecto no puede estar vacío.")
    clean_description = (description or "").strip() or None
    registry = load_registry(runs_root)
    existing = {p["project_id"] for p in registry["projects"]}
    project_id = project_id_from_name(clean_name, existing)
    record = ProjectRecord(
        project_id=project_id,
        name=clean_name,
        description=clean_description,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    registry["projects"].append(
        {
            "project_id": record.project_id,
            "name": record.name,
            "description": record.description,
            "created_at": record.created_at,
        }
    )
    registry["active_project_id"] = record.project_id
    save_registry(runs_root, registry)
    logger.info("Proyecto creado: %s (%s).", record.name, record.project_id)
    return record


def list_projects(runs_root: Path) -> tuple[list[ProjectRecord], Optional[str]]:
    """Lista los proyectos y el proyecto activo.

    Returns:
        Tupla con los proyectos (en orden de creación) y el id del activo.
    """
    registry = load_registry(runs_root)
    active = registry.get("active_project_id")
    return (
        _parse_projects(registry.get("projects")),
        active if isinstance(active, str) else None,
    )


def get_project(runs_root: Path, project_id: str) -> ProjectRecord:
    """Devuelve un proyecto por id (o eleva si no existe)."""
    validate_project_id(project_id)
    for project in _parse_projects(load_registry(runs_root).get("projects")):
        if project.project_id == project_id:
            return project
    raise ApplicationProjectNotFoundError(f"No existe el proyecto: {project_id}")


def set_active_project(runs_root: Path, project_id: Optional[str]) -> None:
    """Establece (o limpia) el proyecto activo.

    Args:
        runs_root: raíz de ejecuciones.
        project_id: id del proyecto a activar, o ``None`` para "sin proyecto".

    Raises:
        ApplicationProjectNotFoundError: si el proyecto no existe.
    """
    registry = load_registry(runs_root)
    if project_id is not None:
        validate_project_id(project_id)
        known = {p["project_id"] for p in registry["projects"]}
        if project_id not in known:
            raise ApplicationProjectNotFoundError(f"No existe el proyecto: {project_id}")
    registry["active_project_id"] = project_id
    save_registry(runs_root, registry)
    logger.info("Proyecto activo: %s.", project_id)


def associate_run(
    runs_root: Path, run_id: str, project_id: Optional[str]
) -> None:
    """Registra la asociación de un run con un proyecto.

    Se invoca en la creación del run (antes de ejecutar el pipeline) para que
    la asociación persista aunque el pipeline falle después.

    Args:
        runs_root: raíz de ejecuciones.
        run_id: identificador del run.
        project_id: id del proyecto (o ``None`` para "sin proyecto").
    """
    registry = load_registry(runs_root)
    registry["run_projects"][run_id] = project_id
    save_registry(runs_root, registry)
    logger.debug("Run %s asociado al proyecto %s.", run_id, project_id)


def project_id_for_run(runs_root: Path, run_id: str) -> Optional[str]:
    """Devuelve el ``project_id`` asociado a un run (o ``None``)."""
    registry = load_registry(runs_root)
    value = registry.get("run_projects", {}).get(run_id)
    return value if isinstance(value, str) else None
