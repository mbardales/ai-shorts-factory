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

No pretende producir contenido creativo real: genera un ContentPackage válido
(identidad, investigación, SEO, guion, escenas, narración y estado) a partir
del tema, con tres escenas visuales específicas y coherentes (apertura, primer
plano y cierre) listas para convertirse en prompts de imagen, suficiente para
probar el pipeline técnicamente.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
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

#: Elimina un numeral inicial del tema (p. ej. "5 inventos..." -> "inventos...").
_LEADING_NUMBER_RE = re.compile(r"^\d+\s*[-–—]?\s*")

#: Elimina el artículo inicial del tema para derivar el sujeto visual.
_LEADING_ARTICLE_RE = re.compile(
    r"^(el|la|los|las|un|una|unos|unas)\s+", re.IGNORECASE
)

#: Palabras significativas para contar (ignora números y símbolos).
_WORD_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ¿¡]+")

#: Rango objetivo de palabras de la narración para un Short de ~10-20 s.
MIN_NARRATION_WORDS = 45
MAX_NARRATION_WORDS = 70

#: Frase añadida al desarrollo cuando la narración cae por debajo del mínimo.
_LOW_WORD_FILLER = "Y hay más de lo que parece a primera vista."

#: Ganchos alternativos. ``subject`` se inserta como objeto de preposición para
#: evitar la concordancia género/número con el tema y mantener el español natural.
_HOOKS: tuple = (
    lambda subject: f"¿Sabías que detrás de {subject} hay más de lo que imaginas?",
    lambda subject: f"Hay un detalle sorprendente sobre {subject} que casi nadie conoce.",
    lambda subject: f"Esto está cambiando la forma en que pensamos en {subject}.",
    lambda subject: f"Pocos saben lo que hay detrás de {subject}.",
)

#: Estructuras de desarrollo alternativas (varían el eje del argumento).
_DEVELOPMENTS: tuple = (
    lambda subject: (
        f"Hoy te contamos todo sobre {subject}: qué es, cómo funciona y por "
        f"qué importa de verdad."
    ),
    lambda subject: (
        f"Vamos a recorrer {subject}: su origen, su evolución y el impacto "
        f"que ya está dejando en nuestra vida cotidiana."
    ),
    lambda subject: (
        f"Analizamos {subject} desde el problema hasta la solución y sus "
        f"consecuencias, sin dejarnos ningún detalle fuera."
    ),
    lambda subject: (
        f"Te mostramos el dato clave sobre {subject}, explicamos por qué "
        f"sucede y qué implica para tu día a día."
    ),
)

#: Llamadas a la acción alternativas (cierres naturales).
_CTAS: tuple = (
    "Mira hasta el final y síguenos para más contenido como este.",
    "Si te ha gustado, comparte este video y síguenos.",
    "Dale like si quieres saber más y activa la campana.",
    "Cuéntanos qué te ha parecido en los comentarios y síguenos.",
)

#: Estilo visual por categoría del tema: (estilo, iluminación, paleta).
#: Los tres componentes se comparten entre las escenas de un mismo tema para
#: mantener coherencia, mientras los planos varían la composición.
_CATEGORY_STYLES: dict[str, tuple[str, str, str]] = {
    "tecnologia": (
        "estilo futurista cinematográfico",
        "iluminación con brillos de neón y tonos fríos",
        "paleta cromática de azul eléctrico y gris plata",
    ),
    "ciencia": (
        "estilo documental de laboratorio",
        "iluminación limpia y uniforme",
        "paleta cromática de blanco y cian",
    ),
    "historia": (
        "estilo histórico cinematográfico",
        "iluminación cálida de época",
        "paleta cromática de sepia y marrón",
    ),
    "ia": (
        "estilo tecnológico editorial",
        "iluminación dramática de alto contraste",
        "paleta cromática de violeta y negro",
    ),
    "curiosidad": (
        "estilo documental realista",
        "iluminación natural suave",
        "paleta cromática cálida y natural",
    ),
}

#: Estilo general por defecto cuando el tema no coincide con ninguna categoría.
_DEFAULT_STYLE: tuple[str, str, str] = (
    "estilo realista",
    "iluminación natural equilibrada",
    "paleta cromática realista",
)

#: Palabras clave por categoría (el tema se normaliza sin acentos antes de
#: buscar). El orden del dict define la prioridad: "ia" antes que "tecnologia".
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ia": (
        "inteligencia artificial",
        "machine learning",
        "algoritmo",
        "neuronal",
        "automatiz",
        "redes neuronales",
    ),
    "tecnologia": (
        "tecnolog",
        "invento",
        "robot",
        "digital",
        "gadget",
        "software",
        "movil",
        "computador",
        "internet",
        "dispositivo",
        "chip",
        "electronica",
    ),
    "ciencia": (
        "ciencia",
        "cientific",
        "fisica",
        "quimica",
        "biologia",
        "astro",
        "espacio",
        "planeta",
        "tierra",
        "atomo",
        "molecula",
        "celula",
        "universo",
        "gravedad",
        "energia",
        "agujero",
    ),
    "historia": (
        "historia",
        "historic",
        "antigu",
        "pasado",
        "imperio",
        "revolucion",
        "guerra",
        "siglo",
        "medieval",
        "romano",
        "civilizacion",
        "arqueologia",
    ),
    "curiosidad": (
        "curios",
        "dato",
        "mito",
        "sabias",
        "que pasa",
        "quedaria",
        "cerebro",
        "sueno",
        "duermes",
        "secreto",
        "detalle",
    ),
}

#: Planos por escena: estableciendo/wide, medio/close-up y detalle/final.
#: Cada plano fija ángulo, composición y distancia de cámara; el sujeto, el
#: estilo, la iluminación y la paleta se comparten para mantener coherencia.
_SCENE_SHOTS: tuple[str, str, str] = (
    (
        "Plano general establecido de {subject}, encuadre amplio con el "
        "elemento central en el centro de la composición, {style}, {lighting}, "
        "{palette}."
    ),
    (
        "Plano medio cercano de {subject}, encuadre a la altura del sujeto "
        "con composición asimétrica y punto de interés desplazado, {style}, "
        "{lighting}, {palette}."
    ),
    (
        "Plano detalle dinámico de {subject}, cámara muy cercana en picado "
        "con composición diagonal y energía visual, {style}, {lighting}, "
        "{palette}."
    ),
)


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
        import unicodedata

        text = unicodedata.normalize("NFKD", topic)
        text = text.encode("ascii", "ignore").decode("ascii").lower()
        words = [word for word in re.findall(r"[a-z0-9]+", text) if len(word) > 2]
        return words or ["tema"]

    @staticmethod
    def _visual_subject(topic: str) -> str:
        """Deriva el sujeto visual del tema (sin numerales ni artículos).

        Para "5 inventos tecnológicos que están cambiando el futuro" devuelve
        "inventos tecnológicos que están cambiando el futuro". El resultado se
        usa como referente concreto de las descripciones de escena.
        """
        subject = _LEADING_NUMBER_RE.sub("", topic.strip())
        subject = _LEADING_ARTICLE_RE.sub("", subject, count=1)
        return subject or "el tema del video"

    @staticmethod
    def _detect_category(topic: str) -> Optional[str]:
        """Detecta la categoría del tema por palabras clave (sin acentos).

        Returns:
            Clave de categoría (``tecnologia``, ``ciencia``, ``historia``,
            ``ia``, ``curiosidad``) o ``None`` si no se detecta ninguna.
        """
        import unicodedata

        text = unicodedata.normalize("NFKD", topic)
        text = text.encode("ascii", "ignore").decode("ascii").lower()
        for category, keywords in _CATEGORY_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                return category
        return None

    @staticmethod
    def _resolve_visual_style(topic: str) -> tuple[str, str, str]:
        """Resuelve (estilo, iluminación, paleta) para el tema.

        Usa el estilo de la categoría detectada o el estilo realista general.
        """
        category = SyntheticContentProvider._detect_category(topic)
        return _CATEGORY_STYLES.get(category, _DEFAULT_STYLE)

    @staticmethod
    def _scene_descriptions(
        subject: str, style: tuple[str, str, str]
    ) -> tuple[str, str, str]:
        """Descripciones visuales específicas y coherentes (wide, medio, detalle).

        Cada escena usa un plano distinto (estableciendo/amplio, medio/cerrado
        y detalle/final) con ángulo, composición y distancia de cámara propios,
        pero comparten el sujeto, el estilo, la iluminación y la paleta para
        mantener coherencia visual. La composición, el formato y las
        exclusiones los añade ``image.prompts`` de forma automática.
        """
        style_text, lighting_text, palette_text = style
        return tuple(
            template.format(
                subject=subject,
                style=style_text,
                lighting=lighting_text,
                palette=palette_text,
            )
            for template in _SCENE_SHOTS
        )

    @staticmethod
    def _count_words(text: str) -> int:
        """Cuenta las palabras significativas de un texto en español."""
        return len(_WORD_RE.findall(text))

    @staticmethod
    def _stable_indices(topic: str) -> tuple[int, int, int]:
        """Devuelve índices estables (hook, desarrollo, CTA) para el tema.

        Se derivan de tramos distintos del SHA-256 del tema: el mismo tema
        produce siempre la misma combinación y temas distintos la rotan.
        """
        digest = hashlib.sha256(topic.encode("utf-8")).hexdigest()
        hook_index = int(digest[0:8], 16) % len(_HOOKS)
        dev_index = int(digest[8:16], 16) % len(_DEVELOPMENTS)
        cta_index = int(digest[16:24], 16) % len(_CTAS)
        return hook_index, dev_index, cta_index

    def _build_narration(self, subject: str, topic: str) -> tuple[str, str, str]:
        """Construye el guion hablado variable (hook, desarrollo, CTA).

        Selecciona de forma determinista un gancho, una estructura de desarrollo
        y un cierre según el hash estable del tema, garantizando que la narración
        se mantenga en el rango de 45-70 palabras (añadiendo una frase al
        desarrollo si fuera necesario). Las tres partes forman un texto
        coherente: hook + desarrollo + CTA.
        """
        hook_index, dev_index, cta_index = self._stable_indices(topic)
        hook = _HOOKS[hook_index](subject)
        development = _DEVELOPMENTS[dev_index](subject)
        call_to_action = _CTAS[cta_index]
        narration = f"{hook} {development} {call_to_action}"
        if self._count_words(narration) < MIN_NARRATION_WORDS:
            development = f"{development} {_LOW_WORD_FILLER}"
            narration = f"{hook} {development} {call_to_action}"
        return hook, development, call_to_action, narration

    def _build_package(self, topic: str) -> dict[str, Any]:
        """Construye el dict serializable del ContentPackage sintético.

        El guion hablado varía de forma determinista según el hash estable del
        tema: cuatro ganchos, cuatro estructuras de desarrollo y cuatro cierres
        se combinan para evitar la sensación de plantilla. Evita frases
        genéricas y no repite el título literal, sino su sujeto. Las
        descripciones de escena son visuales, específicas y coherentes
        (apertura, primer plano, cierre), independientes de la narración.
        """
        words = self._words(topic)
        content_id = self._stable_slug(topic)
        subject = self._visual_subject(topic)

        hook, development, call_to_action, narration = self._build_narration(
            subject, topic
        )

        scene_descriptions = self._scene_descriptions(
            subject, self._resolve_visual_style(topic)
        )
        scenes = [
            {
                "description": scene_descriptions[index],
                "timing_seconds": DEFAULT_SCENE_TIMING,
            }
            for index in range(DEFAULT_SCENE_COUNT)
        ]

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
                "hook": hook,
                "development": development,
                "call_to_action": call_to_action,
            },
            "visuals": {
                "scenes": scenes,
                "style_notes": "Estilo fotorrealista y coherente (determinista).",
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
