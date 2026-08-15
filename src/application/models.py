"""Modelos de la capa Application de AI Shorts Factory (ME30.1).

Define las estructuras de entrada/salida de la API interna de aplicación,
independientes de transporte (sin HTTP todavía). Son ``dataclasses`` frozen y
puras, sin dependencias externas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CreateRunRequest:
    """Petición para crear/ejecutar un run del pipeline.

    Attributes:
        topic: tema del Short (obligatorio, no vacío).
        run_id: identificador opcional del run (``run-YYYYMMDD-HHMMSS``); si se
            omite, se genera automáticamente.
        project_id: identificador opcional del contenido (fuerza
            ``identity.id`` del manifest).
        offline: si ``True``, usa los proveedores sintéticos (sin API ni red).
    """

    topic: str
    run_id: Optional[str] = None
    project_id: Optional[str] = None
    offline: bool = False


@dataclass(frozen=True)
class CreateRunResponse:
    """Respuesta de la creación de un run.

    Attributes:
        run_id: identificador de la ejecución creada.
        status: estado final persistido (``SUCCESS``, ``FAILED``,
            ``QUALITY_FAILED``).
        project_id: proyecto al que se asoció el run (o ``None``).
    """

    run_id: str
    status: str
    project_id: Optional[str] = None


@dataclass(frozen=True)
class RunStatusResponse:
    """Estado consultado de un run existente (solo lectura).

    Attributes:
        run_id: identificador de la ejecución.
        status: estado persistido (``RUNNING``, ``SUCCESS``, ``FAILED``,
            ``QUALITY_FAILED``, ``UNKNOWN``).
        success: ``True``/``False`` si el estado es terminal y determinable;
            ``None`` si el run está en curso o es desconocido.
        video_path: ruta del video renderizado (si existe).
        error: mensaje de error persistido (si falló).
        stage: etapa en curso del pipeline (``content``, ``image``, ``audio``,
            ``manifest``, ``render``, ``quality``) derivada de los artefactos
            presentes; ``None`` si el run no está en ejecución.
        project_id: proyecto al que está asociado el run (o ``None``).
    """

    run_id: str
    status: str
    success: Optional[bool] = None
    video_path: Optional[str] = None
    error: Optional[str] = None
    stage: Optional[str] = None
    project_id: Optional[str] = None


@dataclass(frozen=True)
class RunSummary:
    """Resumen de un run para el historial (solo lectura, sin rutas absolutas).

    Attributes:
        run_id: identificador de la ejecución.
        status: estado persistido (``RUNNING``, ``SUCCESS``, ``FAILED``,
            ``QUALITY_FAILED``, ``UNKNOWN``).
        created_at: instante de creación en ISO 8601 UTC (o ``None``).
        duration_seconds: duración del pipeline en segundos (o ``None``).
        video_url: URL local segura del video (relativa a la API) cuando el
            run es ``SUCCESS``/``QUALITY_FAILED`` y el video existe; ``None``
            en cualquier otro caso.
        error: mensaje de error persistido (si falló).
        project_id: proyecto al que está asociado el run (o ``None``).
    """

    run_id: str
    status: str
    created_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    video_url: Optional[str] = None
    error: Optional[str] = None
    project_id: Optional[str] = None


@dataclass(frozen=True)
class CreateProjectRequest:
    """Petición para crear un proyecto.

    Attributes:
        name: nombre del proyecto (obligatorio, no vacío).
        description: descripción opcional del proyecto.
    """

    name: str
    description: Optional[str] = None


@dataclass(frozen=True)
class ProjectRecord:
    """Proyecto registrado (agrupación de runs).

    Attributes:
        project_id: identificador seguro del proyecto (slug).
        name: nombre legible del proyecto.
        description: descripción opcional del proyecto.
        created_at: instante de creación en ISO 8601 UTC (o ``None``).
    """

    project_id: str
    name: str
    description: Optional[str] = None
    created_at: Optional[str] = None


@dataclass(frozen=True)
class ProjectsOverview:
    """Listado de proyectos con el proyecto activo.

    Attributes:
        projects: proyectos registrados (en orden de creación).
        active_project_id: id del proyecto activo (o ``None``).
    """

    projects: tuple[ProjectRecord, ...]
    active_project_id: Optional[str] = None
