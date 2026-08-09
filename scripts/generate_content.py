"""Genera contenido de un YouTube Short a partir de un tema usando IA.

Flujo:

1. Solicita un tema por consola.
2. Construye un prompt para Gemini mediante :mod:`prompt_engine`.
3. Llama al proveedor mediante :class:`ai.AIAdapter`.
4. Parsea el JSON y lo convierte en un :class:`content.ContentPackage` con el
   validator robusto (rellena campos ausentes con valores por defecto).
5. Persiste el resultado en ``output/content.json`` (UTF-8).

Uso:

    python scripts/generate_content.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

# --- Ajuste del path para poder importar los paquetes de src/ ----------------
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai import AIAdapter, GeminiProvider  # noqa: E402
from ai.exceptions import AIError  # noqa: E402
from config import load_project_env  # noqa: E402
from content import (  # noqa: E402
    CONTENT_PACKAGE_SCHEMA,
    ContentPackage,
    ContentValidationError,
    content_package_from_dict,
    content_package_to_json,
)
from prompt_engine import build_prompt  # noqa: E402

logger = logging.getLogger("generate_content")

#: Ruta del archivo de salida.
OUTPUT_PATH = ROOT / "output" / "content.json"
#: Modelo por defecto si no hay variable de entorno ``GEMINI_MODEL``.
DEFAULT_MODEL = "gemini-3.6-flash"
#: Máximo de caracteres del título de YouTube.
MAX_TITLE_LENGTH = 100


def resolve_model() -> str:
    """Devuelve el modelo de Gemini a usar (env o el predeterminado).

    Normaliza el prefijo ``models/`` si la variable lo incluye.
    """
    model = os.environ.get("GEMINI_MODEL", "").strip()
    if model.startswith("models/"):
        model = model[len("models/") :]
    return model or DEFAULT_MODEL


def extract_json(text: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON de la respuesta del modelo.

    Raises:
        ValueError: si la respuesta no contiene un objeto JSON válido.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("La respuesta del modelo no contiene un objeto JSON.")
    return json.loads(text[start : end + 1])


def clamp_title(title: str) -> str:
    """Recorta un título a los 100 caracteres de YouTube."""
    title = title.strip()
    if len(title) <= MAX_TITLE_LENGTH:
        return title
    return title[: MAX_TITLE_LENGTH - 3].rstrip() + "..."


def ensure_identity(raw: dict[str, Any], topic: str) -> dict[str, Any]:
    """Garantiza identidad con ``id`` y ``title`` (usa el tema como respaldo).

    El resto de objetos de valor los completa el validator robusto.
    """
    identity = raw.get("identity") if isinstance(raw.get("identity"), dict) else {}
    normalized = dict(raw)
    normalized["identity"] = {
        "id": str(identity.get("id") or uuid.uuid4().hex[:8]),
        "title": clamp_title(str(identity.get("title") or topic)),
        "language": identity.get("language") or "es",
    }
    return normalized


def to_content_package(raw: dict[str, Any], topic: str) -> ContentPackage:
    """Convierte la respuesta del modelo en un :class:`ContentPackage`.

    El validator rellena con valores por defecto cualquier campo ausente. Si
    la respuesta fuera estructuralmente irrecuperable (no un dict), se
    devuelve un paquete mínimo válido conservando el tema.
    """
    normalized = ensure_identity(_without_unrealized(raw), topic)
    try:
        return content_package_from_dict(normalized)
    except (ContentValidationError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Respuesta no válida como ContentPackage: %s", exc)
        package = content_package_from_dict({"identity": {"title": clamp_title(topic)}})
        logger.warning("Se guardará un paquete mínimo con el tema como título.")
        return package


def _without_unrealized(raw: dict[str, Any]) -> dict[str, Any]:
    """Descarta secciones que aún no representan información real.

    En la generación no existen activos, publicación ni métricas; se eliminan
    de la respuesta (aunque el modelo las proponga) y el validator las
    inicializa en null. Así el JSON solo contiene información realmente
    existente.
    """
    return {
        key: value
        for key, value in raw.items()
        if key not in {"assets", "publication", "analytics"}
    }


def generate_package(adapter: AIAdapter, topic: str) -> dict[str, Any]:
    """Obtiene la respuesta del modelo como dict, estructurada si es posible.

    Intenta Structured Output (``response_schema``) y, si el proveedor o el
    modelo no lo soportan (``parsed`` es ``None``), cae a texto libre parseado
    con :func:`extract_json`.
    """
    prompt = build_prompt(topic)
    structured = adapter.generate_structured(
        prompt,
        CONTENT_PACKAGE_SCHEMA,
        max_output_tokens=8192,
    )
    if structured is not None:
        logger.info("Respuesta estructurada obtenida directamente de Gemini.")
        return structured
    logger.warning(
        "El modelo no devolvió salida estructurada; se parseará el texto."
    )
    text = adapter.generate_text(prompt, max_output_tokens=8192)
    return extract_json(text)


def main() -> int:
    """Punto de entrada del script. Devuelve 0 en éxito, 1 en error."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_project_env()

    topic = input("Tema:\n").strip()
    if not topic:
        logger.error("El tema no puede estar vacío.")
        return 1

    try:
        adapter = AIAdapter(GeminiProvider(model=resolve_model()))
        logger.info("Generando contenido para: %s", topic)
        raw = generate_package(adapter, topic)
        package = to_content_package(raw, topic)
    except AIError as exc:
        logger.error("Error del proveedor de IA: %s", exc)
        return 1
    except ValueError as exc:
        logger.error("Error al interpretar la respuesta: %s", exc)
        return 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        content_package_to_json(package),
        encoding="utf-8",
    )
    logger.info("Contenido guardado en: %s", OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
