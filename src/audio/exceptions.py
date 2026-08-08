"""Jerarquía de excepciones del Audio Core de AI Shorts Factory.

Todas las excepciones del paquete ``audio`` derivan de ``AudioError`` para que
los consumidores puedan capturar errores de audio de forma homogénea y, a la
vez, con distinción por causa.

Jerarquía:

- ``AudioError``: base genérica.
  - ``AudioProviderError``: error de un proveedor de audio/TTS.
  - ``AudioGenerationError``: el proveedor no devolvió una pista válida.
"""

from __future__ import annotations


class AudioError(Exception):
    """Error base de todas las excepciones del módulo ``audio``."""


class AudioProviderError(AudioError):
    """Error general ocurrido en un proveedor de audio/TTS."""


class AudioGenerationError(AudioError):
    """El proveedor no devolvió una pista de audio válida."""
