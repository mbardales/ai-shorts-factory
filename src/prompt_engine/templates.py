"""Plantillas de prompts para la generación de contenido.

Contiene el texto base del prompt y la estructura JSON de ejemplo que se
envía a Gemini para fijar el formato de la respuesta. Las funciones que
componen el prompt final viven en ``prompt_engine.builder``.
"""

from __future__ import annotations

#: Estructura JSON de ejemplo enviada en el prompt para fijar el formato.
JSON_SCHEMA: dict = {
    "identity": {
        "id": "generado-automaticamente",
        "title": "titulo_atractivo",
        "language": "es",
    },
    "research": {
        "topic": "el_tema",
        "keywords": ["palabra", "clave"],
        "sources": ["fuente1"],
        "notes": "resumen de la investigacion",
    },
    "seo": {
        "description": "descripcion del short (max 5000 caracteres)",
        "tags": ["etiqueta1", "etiqueta2"],
        "keywords": ["posicionamiento"],
    },
    "script": {
        "hook": "frase de enganche",
        "development": "cuerpo del guion",
        "call_to_action": "llamada a la accion (opcional)",
    },
    "visuals": {
        "scenes": [{"description": "que ocurre en la escena", "timing_seconds": 3}],
        "style_notes": "estilo visual",
    },
    "narration": {"text": "texto de la locucion", "voice": "configuracion de voz"},
    "assets": {
        "audio": None,
        "music": None,
        "branding": None,
        "font": None,
        "video": None,
        "thumbnail": None,
    },
    "publication": {
        "platform": "youtube",
        "video_id": None,
        "published_at": None,
        "privacy_status": None,
        "url": None,
    },
    "analytics": {"views": None, "likes": None, "comments": None, "shares": None},
    "status": {"stage": "idea", "updated_at": None},
}

#: Plantilla base del prompt. ``topic`` se sustituye por el tema y ``schema``
#: por la estructura JSON de ejemplo (serializada con ``ensure_ascii=False``).
PROMPT_TEMPLATE: str = (
    "Eres un guionista experto en YouTube Shorts. "
    "Genera el contenido completo para un Short sobre el tema indicado.\n\n"
    "Tema: {topic}\n\n"
    "Responde UNICAMENTE con un objeto JSON valido, sin texto adicional, "
    "sin bloques de codigo y sin comillas fuera del JSON.\n\n"
    "Estructura JSON requerida (usa exactamente estas claves):\n"
    "{schema}\n\n"
    "Reglas:\n"
    "{rules}"
)

#: Reglas de generación aplicadas al prompt.
RULES: tuple[str, ...] = (
    "'identity.title': titulo llamativo de 100 caracteres o menos.",
    "'script.hook': frase de enganche para los primeros segundos.",
    "'script.development': guion corto y dinamico para Shorts.",
    "'visuals.scenes': 3 a 6 escenas con su 'timing_seconds'.",
    "'narration.text': locucion completa, en espanol.",
    "'seo.description': maximo 5000 caracteres; 'seo.tags': maximo 30.",
    "'status.stage': usa 'idea'.",
)
