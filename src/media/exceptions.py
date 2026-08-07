"""Jerarquía de excepciones del módulo ``media`` de AI Shorts Factory.

Todas las excepciones derivan de :class:`MediaError` para que los consumidores
puedan capturar errores de multimedia de forma homogénea y, a la vez, con
distinción por causa.

Jerarquía:

- ``MediaError``: base genérica.
  - ``MediaConfigurationError``: configuración inválida o incompleta.
  - ``MediaNotFoundError``: un activo o recurso solicitado no existe.
  - ``MediaValidationError``: dato inválido (metadatos, rutas, nombres).
  - ``MediaUnsupportedError``: tipo de medio, formato o extensión no soportado.
  - ``StorageError``: error de un almacén de activos.
    - ``StorageReadError``: fallo al leer un activo.
    - ``StorageWriteError``: fallo al escribir un activo.
    - ``StorageExistsError``: el destino ya existe (sin sobrescritura).
    - ``StorageNotFoundError``: el activo no existe en el almacén.
"""

from __future__ import annotations


class MediaError(Exception):
    """Error base de todas las excepciones del módulo ``media``."""


class MediaConfigurationError(MediaError):
    """Configuración inválida o incompleta (raíces, extensiones, registros)."""


class MediaNotFoundError(MediaError):
    """Un activo o recurso multimedia solicitado no existe."""


class MediaValidationError(MediaError):
    """Dato inválido: metadatos incompletos, rutas inseguras o nombres mal formados."""


class MediaUnsupportedError(MediaError):
    """Tipo de medio, formato o extensión no soportado por la infraestructura."""


class StorageError(MediaError):
    """Error general ocurrido en un almacén de activos multimedia."""


class StorageReadError(StorageError):
    """Fallo al leer un activo del almacén."""


class StorageWriteError(StorageError):
    """Fallo al escribir un activo en el almacén."""


class StorageExistsError(StorageError):
    """El destino ya existe y la operación no permite sobrescribirlo."""


class StorageNotFoundError(StorageError):
    """El activo solicitado no existe en el almacén."""
