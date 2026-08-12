"""Proveedor sintético de contenido para desarrollo y pruebas.

Implementa :class:`ai.base.BaseAIProvider` sin depender de APIs externas ni de
proveedores comerciales. Genera un ContentPackage determinista (JSON) a partir
de un tema usando exclusivamente la biblioteca estándar de Python.

Su objetivo es permitir validar el pipeline completo de AI Shorts Factory
(incluido el ``PipelineRunner`` de ME27) de forma totalmente offline: sin API
key, sin red y sin dependencias nuevas.

El proveedor:

- no realiza llamadas HTTP;
- no requiere API key;
- devuelve un :class:`ai.base.GenerationResult` con un JSON estructurado
  compatible con ``CONTENT_PACKAGE_SCHEMA``;
- respeta el contrato de :class:`ai.base.BaseAIProvider`;
- es determinista: el mismo tema produce exactamente el mismo contenido;
- usa Structured Output: ``parsed`` se rellena siempre con el dict generado.

No pretende producir contenido creativo real: genera una plantilla mínima
válida (identidad, investigación, SEO, guion, escenas, narración y estado) a
partir del tema, suficiente para probar el pipeline técnicamente.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from ..base import BaseAIProvider, GenerationOptions, GenerationResult
from ..exceptions import ProviderError

logger = logging.getLogger(__name__)


#: Modelo lógico por defecto del proveedor sintético.
DEFAULT_MODEL = "synthetic-v1"

#: Número de escenas que se generan por defecto.
DEFAULT_SCENE_COUNT = 3

#: Duración base de cada escena en segundos.
DEFAULT_SCENE_TIMING = 2


class SyntheticContentProvider(BaseAIProvider):
    """Proveedor local que genera un ContentPackage JSON sintético.

    Está destinado exclusivamente a desarrollo, pruebas e integración del
    pipeline. No representa generación mediante IA.

    Args:
        model: identificador lógico del proveedor sintético.
        options: parámetros de generación opcionales.
    """

    name: str = "synthetic-content"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        options: Optional[GenerationOptions] = None,
    ) -> None:
        super().__init__(model, options=options)
        self._logger = logging.getLogger(f"{__name__}.SyntheticContentProvider")

    def generate(self, prompt: str, **kwargs: Any) -> GenerationResult:
        """Genera un ContentPackage JSON sintético a partir del prompt.

        El ``prompt`` suele ser el prompt compuesto por el Prompt Engine, que
        incluye el tema; este proveedor lo ignora estructuralmente y deriva el
        contenido de forma determinista desde el texto de entrada (la semilla
        se calcula con SHA-256 del prompt). Si el llamador pasa el tema real
        por ``kwargs["topic"]``, se usa ese en lugar del prompt completo.

        Args:
            prompt: texto de entrada (tema / prompt del usuario).
            **kwargs: ``topic`` (tema real del short, preferido sobre
                ``prompt``); ``response_schema`` se acepta sin efecto.

        Returns:
            :class:`GenerationResult` con el JSON estructurado en ``parsed``.

        Raises:
            ValueError: si el prompt está vacío o en blanco.
            ai.exceptions.ProviderError: si el tema no puede procesarse.
        """
        if not prompt and not kwargs.get("topic"):
            raise ValueError("El prompt no puede estar vacío.")

        topic = kwargs.get("topic") or prompt.strip()
        try:
            package = self._build_package(topic)
        except Exception as exc:  # noqa: BLE001 - envolver errores inesperados
            self._logger.exception("Error al generar contenido sintético.")
            raise ProviderError(
                f"Error al generar contenido sintético: {exc}"
            ) from exc

        text = json.dumps(package, ensure_ascii=False)
        self._logger.debug(
            "Contenido sintético generado: %d caracteres, %d escenas.",
            len(text),
            len(package.get("visuals", {}).get("scenes", [])),
        )
        return GenerationResult(
            text=text,
            model=self.model,
            finish_reason="stop",
            parsed=package,
        )

    # ------------------------------------------------------------------
    # Ayudantes privados
    # ------------------------------------------------------------------

    @staticmethod
    def _stable_slug(topic: str, *, length: int = 12) -> str:
        """Deriva un identificador corto y estable a partir del tema."""
        digest = hashlib.sha256(topic.encode("utf-8")).hexdigest()
        return f"syn-{digest[:length]}"

    @staticmethod
    def _words(topic: str) -> list[str]:
        """Devuelve las palabras significativas del tema (normalizadas)."""
        import re
        import unicodedata

        text = unicodedata.normalize("NFKD", topic)
        text = text.encode("ascii", "ignore").decode("ascii").lower()
        words = [word for word in re.findall(r"[a-z0-9]+", text) if len(word) > 2]
        return words or ["tema"]

    def _build_package(self, topic: str) -> dict[str, Any]:
        """Construye el dict serializable del ContentPackage sintético."""
        words = self._words(topic)
        content_id = self._stable_slug(topic)
        scene_texts = (
            f"Introducción a {words[0]}.",
            f"Desarrollo del tema {topic}.",
            f"Cierre sobre {words[-1]}.",
        )

        scenes = [
            {
                "description": scene_texts[index],
                "timing_seconds": DEFAULT_SCENE_TIMING,
            }
            for index in range(DEFAULT_SCENE_COUNT)
        ]

        narration = (
            f"{scene_texts[0]} {scene_texts[1]} {scene_texts[2]}"
        )

        return {
            "identity": {
                "id": content_id,
                "title": f"Short sobre {topic}"[:100],
                "language": "es",
            },
            "research": {
                "topic": topic,
                "keywords": list(dict.fromkeys(words))[:8],
                "sources": [],
                "notes": "Contenido generado de forma sintética (offline).",
            },
            "seo": {
                "description": f"Un short sobre {topic}.",
                "tags": list(dict.fromkeys(words))[:6],
                "keywords": list(dict.fromkeys(words))[:8],
            },
            "script": {
                "hook": scene_texts[0],
                "development": scene_texts[1],
                "call_to_action": "Síguenos para más contenido.",
            },
            "visuals": {
                "scenes": scenes,
                "style_notes": "Estilo sintético de prueba (determinista).",
            },
            "narration": {
                "text": narration,
                "voice": None,
            },
            "status": {
                "stage": "generated",
                "updated_at": "",
            },
        }
