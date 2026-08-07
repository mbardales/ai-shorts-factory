"""Prompt Engine de AI Shorts Factory.

Compone prompts listos para Gemini de forma desacoplada del resto del
sistema:

- ``builder``: ``build_prompt``, que devuelve el prompt final.
- ``templates``: plantillas base y estructura JSON de ejemplo.

El Prompt Engine **no** depende de ``ai`` ni de ``content``.
"""

from .builder import build_prompt
from .templates import JSON_SCHEMA, PROMPT_TEMPLATE, RULES

__all__ = [
    "build_prompt",
    "JSON_SCHEMA",
    "PROMPT_TEMPLATE",
    "RULES",
]
