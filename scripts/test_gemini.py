"""Cliente de prueba para Google Gemini.

Script mínimo de validación de la integración con el Gemini API para
AI Shorts Factory. Lee la clave desde un archivo ".env", envía un prompt
simple y muestra la respuesta por consola.

Uso:
    pip install -r requirements.txt
    python scripts/test_gemini.py

Variables de entorno (ver .env.example):
    GEMINI_API_KEY  (obligatoria)  Clave de API de Google AI Studio.
    GEMINI_MODEL    (opcional)     Modelo a usar; por defecto "gemini-3.6-flash".
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Carga de variables de entorno desde ".env"
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent

# Se intenta cargar primero el ".env" de la carpeta del script y luego el del
# directorio de trabajo (raíz del proyecto). load_dotenv() no falla si no existe.
load_dotenv(SCRIPT_DIR / ".env")
load_dotenv()


def get_api_key() -> str:
    """Obtiene la clave de API desde el entorno.

    Raises:
        ValueError: si GEMINI_API_KEY no está definida.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or api_key.startswith("REEMPLAZAR"):
        raise ValueError(
            "GEMINI_API_KEY no está definida. Copia scripts/.env.example a "
            "scripts/.env (o a .env en la raíz) y completa la clave."
        )
    return api_key


def get_model() -> str:
    """Obtiene el modelo a usar desde el entorno (con valor por defecto)."""
    return os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")


# ---------------------------------------------------------------------------
# Lógica principal
# ---------------------------------------------------------------------------

def run() -> None:
    """Ejecuta una llamada de prueba a Gemini e imprime la respuesta."""
    # Import diferido para que el error de dependencia sea claro al ejecutar.
    from google import genai
    from google.genai import errors as genai_errors

    api_key = get_api_key()
    model = get_model()

    # El cliente se crea con la clave explícita (también podría omitirse y
    # leería GEMINI_API_KEY del entorno automáticamente).
    client = genai.Client(api_key=api_key)

    prompt = "Responde en una frase: ¿qué es AI Shorts Factory?"

    print(f"Enviando prompt al modelo '{model}'...")
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
    except genai_errors.ClientError as exc:
        # Errores HTTP devueltos por la API (clave inválida, cuota, permisos...).
        raise SystemExit(f"Error del Gemini API (HTTP {exc.code}): {exc.message}") from exc
    except Exception as exc:  # noqa: BLE001 - límite del script de prueba
        raise SystemExit(f"Error inesperado al llamar a Gemini: {exc}") from exc

    # "response.text" es el texto generado. Puede ser None si el modelo no
    # devolvió contenido (p. ej. bloqueo de seguridad del prompt).
    if not response.text:
        raise SystemExit("La API respondió sin contenido de texto.")

    print("\nRespuesta de Gemini:")
    print("---------------------")
    print(response.text)
    print("---------------------")
    print("Prueba completada con éxito.")


if __name__ == "__main__":
    run()
