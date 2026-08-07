"""Construcción de prompts listos para Gemini.

``build_prompt`` compone la plantilla base con el tema proporcionado y la
estructura JSON de ejemplo, devolviendo el prompt final listo para enviar al
proveedor. No depende de ``ai`` ni de ``content``: solo de ``templates``.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from .templates import JSON_SCHEMA, PROMPT_TEMPLATE, RULES


def build_prompt(
    topic: str,
    *,
    schema: Optional[Mapping[str, Any]] = None,
    rules: Optional[tuple[str, ...]] = None,
) -> str:
    """Construye el prompt completo para generar contenido de un Short.

    Args:
        topic: tema del Short.
        schema: estructura JSON de ejemplo (por defecto ``templates.JSON_SCHEMA``).
        rules: reglas de generación (por defecto ``templates.RULES``).

    Returns:
        Prompt listo para Gemini como texto plano.
    """
    schema_text = json.dumps(schema or JSON_SCHEMA, ensure_ascii=False, indent=2)
    rules_text = "\n".join(f"- {rule}" for rule in (rules or RULES))
    return PROMPT_TEMPLATE.format(topic=topic, schema=schema_text, rules=rules_text)
