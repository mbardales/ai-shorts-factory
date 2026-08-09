"""Configuración central del proyecto.

Centraliza la carga del archivo ``.env`` de la raíz del repositorio usando
``python-dotenv`` (dependencia ya declarada en ``scripts/requirements.txt``)
para poblar ``os.environ`` de forma fiable.

Esta es la única ubicación donde los scripts y pipelines deben cargar el
archivo ``.env``: los proveedores (Gemini, Stability, TTS) leen sus variables
desde ``os.environ``, así que esta carga debe ejecutarse antes de construir
cualquier proveedor.

La ruta del ``.env`` se deriva de la ubicación de este módulo
(``src/config/__init__.py`` → raíz del proyecto), por lo que no depende del
directorio de trabajo desde el que se ejecute el script.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from dotenv import load_dotenv

#: Raíz del proyecto (tres niveles por encima de este módulo).
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Archivo ``.env`` de la raíz del proyecto.
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def load_project_env(
    path: Optional[Union[str, Path]] = None,
    *,
    override: bool = False,
) -> bool:
    """Carga el archivo ``.env`` del proyecto en ``os.environ``.

    Usa :func:`dotenv.load_dotenv`, que por defecto (``override=False``) NO
    sobrescribe variables de entorno ya definidas explícitamente en el proceso.

    Args:
        path: ruta al archivo ``.env``. Por defecto, el ``.env`` de la raíz
            del repositorio.
        override: si ``True``, sobrescribe variables de entorno ya definidas.

    Returns:
        ``True`` si el archivo se cargó, ``False`` en caso contrario (p. ej.
        si el archivo no existe).
    """
    env_path = Path(path) if path is not None else DEFAULT_ENV_FILE
    return load_dotenv(env_path, override=override)
