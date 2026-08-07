"""Abstracciones de almacenamiento de activos multimedia.

:class:`Storage` define el contrato de un almacén de activos (operaciones con
rutas relativas al almacén), independiente del backend concreto. :class:`LocalStorage`
es la implementación sobre el sistema de archivos local; en el futuro pueden
añadirse backends remotos (S3, etc.) sin cambiar la interfaz.

Todos los métodos reciben y devuelven rutas relativas al almacén. Los errores
del sistema de archivos se traducen a la jerarquía de ``media.exceptions``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator, Union

from .exceptions import (
    MediaConfigurationError,
    StorageError,
    StorageExistsError,
    StorageNotFoundError,
    StorageReadError,
    StorageWriteError,
)
from .paths import ensure_safe_relative

logger = logging.getLogger(__name__)


class Storage(ABC):
    """Almacén de activos multimedia (contrato).

    Las operaciones usan rutas **relativas** al almacén. ``list_files`` devuelve
    rutas relativas; ``resolve`` expone la ubicación física (según backend).
    """

    @property
    @abstractmethod
    def root(self) -> Path:
        """Ubicación física raíz del almacén."""

    @abstractmethod
    def exists(self, rel_path: Union[str, Path]) -> bool:
        """Indica si un activo existe en el almacén."""

    @abstractmethod
    def read_bytes(self, rel_path: Union[str, Path]) -> bytes:
        """Lee el contenido de un activo.

        Raises:
            StorageNotFoundError: si el activo no existe.
            StorageReadError: si falla la lectura.
        """

    @abstractmethod
    def write_bytes(
        self,
        rel_path: Union[str, Path],
        data: bytes,
        *,
        overwrite: bool = True,
    ) -> Path:
        """Escribe el contenido de un activo.

        Args:
            rel_path: ruta relativa del activo.
            data: contenido a escribir.
            overwrite: si ``False`` y el activo ya existe, lanza
                :class:`StorageExistsError`.

        Returns:
            Ubicación física del archivo escrito.

        Raises:
            StorageExistsError: si ``overwrite=False`` y el activo existe.
            StorageWriteError: si falla la escritura.
        """

    @abstractmethod
    def delete(self, rel_path: Union[str, Path]) -> None:
        """Elimina un activo del almacén.

        Raises:
            StorageNotFoundError: si el activo no existe.
            StorageError: si falla la eliminación.
        """

    @abstractmethod
    def list_files(self, prefix: Union[str, Path] = "") -> Iterator[Path]:
        """Itera las rutas relativas de los archivos bajo un prefijo."""

    @abstractmethod
    def resolve(self, rel_path: Union[str, Path]) -> Path:
        """Devuelve la ubicación física de una ruta relativa del almacén."""


class LocalStorage(Storage):
    """Almacén de activos sobre el sistema de archivos local.

    Args:
        root: directorio raíz del almacén.
        auto_create: si ``True``, crea el directorio raíz si no existe.

    Raises:
        MediaConfigurationError: si ``root`` existe pero no es un directorio.
        StorageNotFoundError: si ``root`` no existe y ``auto_create`` es
            ``False``.
    """

    def __init__(self, root: Union[str, Path], *, auto_create: bool = False) -> None:
        self._root = Path(root).resolve()
        self._logger = logging.getLogger(f"{__name__}.LocalStorage")

        if self._root.exists() and not self._root.is_dir():
            raise MediaConfigurationError(
                f"La raíz del almacén no es un directorio: {self._root}"
            )
        if not self._root.exists():
            if auto_create:
                self._root.mkdir(parents=True, exist_ok=True)
            else:
                raise StorageNotFoundError(
                    f"El directorio raíz del almacén no existe: {self._root}"
                )
        self._logger.debug("Almacén local inicializado en: %s", self._root)

    @property
    def root(self) -> Path:
        """Ubicación física raíz del almacén."""
        return self._root

    # ------------------------------------------------------------------
    # Operaciones del contrato
    # ------------------------------------------------------------------

    def exists(self, rel_path: Union[str, Path]) -> bool:
        return self._path(rel_path).exists()

    def read_bytes(self, rel_path: Union[str, Path]) -> bytes:
        path = self._path(rel_path)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StorageNotFoundError(f"Activo no encontrado: {rel_path}") from exc
        except OSError as exc:
            raise StorageReadError(
                f"Error al leer el activo '{rel_path}': {exc}"
            ) from exc

    def write_bytes(
        self,
        rel_path: Union[str, Path],
        data: bytes,
        *,
        overwrite: bool = True,
    ) -> Path:
        path = self._path(rel_path)
        if not overwrite and path.exists():
            raise StorageExistsError(f"El activo ya existe: {rel_path}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise StorageWriteError(
                f"Error al escribir el activo '{rel_path}': {exc}"
            ) from exc
        return path

    def delete(self, rel_path: Union[str, Path]) -> None:
        path = self._path(rel_path)
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise StorageNotFoundError(f"Activo no encontrado: {rel_path}") from exc
        except OSError as exc:
            raise StorageError(f"Error al eliminar '{rel_path}': {exc}") from exc

    def list_files(self, prefix: Union[str, Path] = "") -> Iterator[Path]:
        base = self._path(prefix)
        if not base.is_dir():
            return
        for entry in base.rglob("*"):
            if entry.is_file():
                yield entry.relative_to(self._root)

    def resolve(self, rel_path: Union[str, Path]) -> Path:
        return self._path(rel_path)

    # ------------------------------------------------------------------
    # Ayudantes privados
    # ------------------------------------------------------------------

    def _path(self, rel_path: Union[str, Path]) -> Path:
        """Convierte una ruta relativa validada en una ruta física."""
        return self._root / ensure_safe_relative(rel_path)
