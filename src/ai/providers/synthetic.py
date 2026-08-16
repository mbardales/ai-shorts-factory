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

Estructura narrativa (ME38): HOOK → IDEA 1 → IDEA 2 → IDEA 3 → CIERRE/CTA
(cinco escenas por defecto; seis solo cuando el tema lo justifica). Cada escena
representa una idea concreta del guion y lleva su propia descripción visual, el
gancho no repite literalmente el título, la narración aporta explicaciones
generales correctas (sin datos inventados) y el cierre es una llamada a la
acción contextual.

Para temas enumerativos (ME38.3): cuando el tema anuncia un número y un
concepto enumerativo ("5 inventos...", "7 hábitos...", "los X..."), el proveedor
desarrolla realmente N elementos concretos, cada uno con su propia escena y su
descripción visual específica (HOOK + N escenas de elementos, sin escena vacía
dedicada al CTA). Los elementos proceden de un catálogo de conceptos extensible
por categoría (no hardcodeado a un solo tema).
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

#: Número de escenas objetivo por defecto (HOOK + 3 ideas + CIERRE).
DEFAULT_SCENE_COUNT = 5

#: Máximo de escenas (solo cuando el tema lo justifica, p. ej. un número ≥ 6).
MAX_SCENE_COUNT = 6

#: Máximo de elementos concretos para un tema enumerativo (HOOK + N escenas,
#: sin escena vacía dedicada al CTA).
MAX_ENUM_ELEMENTS = 5

#: Duración base de cada tipo de escena en segundos (el manifest la ajusta al
#: audio real después).
HOOK_TIMING_SECONDS = 2
IDEA_TIMING_SECONDS = 3
CIERRE_TIMING_SECONDS = 2

#: Extrae un numeral inicial del tema ("5 inventos..." -> "5").
_LEADING_NUMBER_RE = re.compile(r"^\d+")

#: Sustantivos que indican un tema enumerativo ("5 inventos", "7 hábitos",
#: "los avances", "varias razones"...). Se usan sobre el texto original del
#: tema (con acentos), por eso no se normalizan.
_ENUM_NOUN_PATTERN = (
    r"(?:inventos?|cosas?|razones?|ejemplos?|formas?|señales?|claves?|hábitos?|"
    r"errores?|mitos?|tecnologías?|avances?|ideas?|maneras?|trucos?|consejos?|"
    r"aplicaciones?|beneficios?|secretos?|datos?|pruebas?|motivos?|causas?|"
    r"hechos?|pasos?|tipos?|señales?)"
)

#: Número + sustantivo enumerativo en el tema.
_ENUM_COUNT_RE = re.compile(r"(\d+)\s+(" + _ENUM_NOUN_PATTERN + r")\b", re.IGNORECASE)

#: "los X" / "las X" sin número explícito (lista implícita).
_ENUM_LIST_RE = re.compile(r"\b(?:los|las)\s+(" + _ENUM_NOUN_PATTERN + r")\b", re.IGNORECASE)

#: Número de elementos por defecto cuando el tema enumera sin número ("los X").
DEFAULT_ENUM_COUNT = 5

#: Números en palabras para los ganchos enumerativos.
_NUMBER_WORDS: dict[int, str] = {
    1: "uno", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis",
    7: "siete", 8: "ocho", 9: "nueve", 10: "diez", 11: "once", 12: "doce",
}

#: Palabras significativas para contar (ignora números y símbolos).
_WORD_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ¿¡]+")

#: Rango objetivo de palabras de la narración para un Short.
MIN_NARRATION_WORDS = 45
MAX_NARRATION_WORDS = 90

#: Frase añadida al desarrollo cuando la narración cae por debajo del mínimo.
_LOW_WORD_FILLER = "Y esto es solo el principio de una historia mucho más grande."

#: Ganchos deterministas de distintos tipos: pregunta, dato, afirmación,
#: paradoja y curiosidad. ``{e}`` se sustituye por la esencia del tema
#: (sustantivo corto de la categoría), de modo que el gancho nunca repite
#: literalmente el título. Cada gancho anticipa el contenido (promesa).
_HOOKS: tuple[str, ...] = (
    "¿Qué hay detrás de {e} que la mayoría de la gente pasa por alto?",
    "Hay una idea sencilla sobre {e} que cambia por completo cómo la ves.",
    "Hoy vas a entender {e} mejor de lo que la entendías ayer.",
    "Lo más curioso de {e} no es lo que imaginas, sino lo que vamos a ver.",
    "Antes de terminar este video, vas a mirar {e} con otros ojos.",
)

#: Ganchos enumerativos: prometen el número de elementos y el sustantivo del
#: tema ("cinco inventos", "siete hábitos"...). ``{n}`` es el número en
#: palabras, ``{noun}`` el sustantivo enumerativo, ``{art}`` el artículo
#: determinado ("los"/"las"), ``{det}`` el demostrativo ("estos"/"estas") y
#: ``{e}`` la esencia. Se eligen cortos para respetar el rango 45-90 palabras.
_ENUM_HOOKS: tuple[str, ...] = (
    "Hoy te presento {n} {noun} que están redefiniendo {e}.",
    "¿Conoces {art} {n} {noun} que más están cambiando {e}?",
    "Te resumo en {n} {noun} lo que explica {e}.",
    "{det} son {art} {n} {noun} que definen {e}.",
    "Antes de terminar, {n} {noun} que explican {e}.",
)

#: Sustantivos enumerativos femeninos (plural) para la concordancia de género
#: de artículos y demostrativos ("las cinco razones", "estas cinco cosas").
_ENUM_FEMININE: frozenset[str] = frozenset({
    "cosa", "cosas", "razon", "razones", "razón", "razones", "senal",
    "señal", "señales", "clave", "claves", "forma", "formas", "tecnologia",
    "tecnología", "tecnologías", "idea", "ideas", "manera", "maneras",
    "aplicacion", "aplicación", "aplicaciones", "prueba", "pruebas",
    "causa", "causas",
})

#: Ideas concretas del guion, emparejadas con su descripción visual específica.
#: Cada entrada es ``(idea narrada, descripción visual)``. Las ideas son
#: explicaciones generales correctas, sin datos inventados (números, fechas o
#: estadísticas que aparenten ser reales). Las descripciones visuales varían
#: plano, ángulo, composición y elementos para mantener la diversidad.
_IDEA_PAIRS: tuple[tuple[str, str], ...] = (
    (
        "Todo avance real nace de un problema concreto que alguien decidió resolver.",
        "Primer plano de unas manos rodeando un objeto que empieza a cobrar vida, "
        "fondo oscuro desenfocado y tensión visual, composición centrada, "
        "{style}, {lighting}, {palette}.",
    ),
    (
        "Los cambios que transforman vidas empiezan pequeños, imperfectos y llenos de pruebas.",
        "Plano general de un espacio de trabajo con prototipos, papeles y herramientas "
        "esparcidas, un creador observando una pieza, composición en diagonal, "
        "{style}, {lighting}, {palette}.",
    ),
    (
        "La clave no está en la complejidad, sino en lo fácil que resulta usarla cada día.",
        "Plano detalle de un mecanismo en funcionamiento con piezas visibles, cámara "
        "muy cercana en ángulo bajo y composición asimétrica, {style}, {lighting}, "
        "{palette}.",
    ),
    (
        "Ninguna idea revolucionaria aparece sola: se apoya en los descubrimientos que vienen antes.",
        "Plano cenital de una mesa con objetos conectados entre sí formando una red, "
        "composición simétrica vista desde arriba, {style}, {lighting}, {palette}.",
    ),
    (
        "Una idea triunfa cuando entra en la vida cotidiana de las personas.",
        "Plano medio de personas de distintas edades integrando la novedad en su rutina "
        "diaria, composición natural con profundidad, {style}, {lighting}, {palette}.",
    ),
    (
        "El futuro no está en un solo avance, sino en la combinación de muchos.",
        "Plano lateral de una línea de evolución que avanza hacia un horizonte brillante, "
        "composición en perspectiva, {style}, {lighting}, {palette}.",
    ),
)

#: Llamadas a la acción contextuales y variadas (nunca la plantilla genérica
#: "Mira hasta el final y síguenos"). ``{e}`` se sustituye por la esencia.
_CTAS: tuple[str, ...] = (
    "¿Cuál de estas ideas te ha sorprendido más? Cuéntanoslo en los comentarios.",
    "Si quieres profundizar en {e}, síguenos para ver la siguiente parte.",
    "Comparte este video con alguien a quien le interese {e}.",
    "Esto es solo el principio: detrás de {e} hay mucho más que veremos pronto.",
    "Déjanos un comentario si te gustaría que exploremos más sobre {e}.",
)

#: CTAs contextuales para temas enumerativos. ``{n}`` es el número en palabras,
#: ``{noun}`` el sustantivo enumerativo, ``{det}`` el demostrativo
#: ("estos"/"estas") y ``{e}`` la esencia.
_ENUM_CTAS: tuple[str, ...] = (
    "¿Cuál de {det} {n} {noun} te ha llamado más la atención? Cuéntanoslo.",
    "Si quieres que profundicemos en cualquiera de {det} {n} {noun}, dínoslo en "
    "los comentarios.",
    "Comparte este video con alguien a quien le interesen {det} {n} {noun}.",
    "¿Se te ocurre algún {noun} más que debería estar en la lista? "
    "Déjanos tu comentario.",
    "Cuéntanos cuál de {det} {n} {noun} usarías primero en tu día a día.",
)

#: Esencia corta (sustantivo) por categoría, usada en hook/CTA y en los planos
#: de apertura y cierre. Se elige para que encaje gramaticalmente tras "de ".
_CATEGORY_ESSENCE: dict[str, str] = {
    "tecnologia": "la tecnología moderna",
    "ciencia": "los nuevos descubrimientos",
    "historia": "los grandes episodios del pasado",
    "ia": "la inteligencia artificial",
    "curiosidad": "los pequeños detalles de cada día",
}

#: Esencia por defecto cuando el tema no coincide con ninguna categoría.
_DEFAULT_ESSENCE = "este tema"

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

#: Planos de apertura (wide, establecido) y de cierre (detalle) que usan la
#: esencia del tema. Se conservan intactos; la diversidad de las escenas
#: intermedias la aportan las descripciones de ``_IDEA_PAIRS``.
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

#: Catálogo de conceptos concretos por categoría para temas enumerativos
#: (ME38.3). Cada entrada es ``(nombre, frase narrada, descripción visual)``.
#: Los conceptos son reales y verificables (sin estadísticas ni fechas
#: inventadas); las descripciones visuales son específicas de cada concepto.
#: La estructura es extensible: se añade una categoría o un concepto sin
#: cambiar el mecanismo de selección.
_ENUM_CONCEPTS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "tecnologia": (
        (
            "la inteligencia artificial generativa",
            "La inteligencia artificial generativa crea textos e imágenes a "
            "partir de una simple orden.",
            "Primer plano de una pantalla donde un modelo de IA dibuja una "
            "imagen en tiempo real, composición centrada, {style}, {lighting}, "
            "{palette}.",
        ),
        (
            "los robots humanoides",
            "Los robots humanoides ya caminan, hablan y trabajan junto a las "
            "personas.",
            "Plano medio de un robot humanoide caminando por una nave "
            "industrial con personas a su alrededor, {style}, {lighting}, "
            "{palette}.",
        ),
        (
            "las interfaces cerebro-computadora",
            "Las interfaces cerebro-computadora traducen pensamientos en "
            "órdenes para las máquinas.",
            "Primer plano de una diadema de sensores sobre la cabeza de una "
            "persona conectada a una pantalla, {style}, {lighting}, {palette}.",
        ),
        (
            "la computación cuántica",
            "La computación cuántica resuelve en segundos problemas que "
            "tardarían años.",
            "Plano general de un ordenador cuántico con cables dorados y una "
            "cámara criogénica, {style}, {lighting}, {palette}.",
        ),
        (
            "las baterías de nueva generación",
            "Las baterías de nueva generación dan más autonomía a coches y "
            "móviles.",
            "Plano detalle de una batería moderna con celdas apiladas y "
            "conectores brillantes, {style}, {lighting}, {palette}.",
        ),
        (
            "la impresión 3D",
            "La impresión 3D fabrica piezas capa a capa para todo tipo de "
            "industrias.",
            "Plano lateral de una impresora 3D construyendo una pieza "
            "compleja, {style}, {lighting}, {palette}.",
        ),
    ),
    "ia": (
        (
            "los asistentes conversacionales",
            "Los asistentes conversacionales entienden el lenguaje y responden "
            "como una persona.",
            "Primer plano de un teléfono mostrando una conversación fluida con "
            "un asistente, {style}, {lighting}, {palette}.",
        ),
        (
            "el aprendizaje automático",
            "El aprendizaje automático encuentra patrones en datos que las "
            "personas no ven a simple vista.",
            "Plano cenital de una mesa de datos con gráficos y nodos "
            "conectados, {style}, {lighting}, {palette}.",
        ),
        (
            "la visión por computadora",
            "La visión por computadora permite a las máquinas reconocer "
            "objetos y rostros.",
            "Primer plano de una cámara que encuadra y etiqueta objetos en "
            "tiempo real, {style}, {lighting}, {palette}.",
        ),
        (
            "la generación de voz",
            "La generación de voz produce voces casi humanas para narrar "
            "cualquier texto.",
            "Plano detalle de un altavoz con ondas de sonido visibles, "
            "{style}, {lighting}, {palette}.",
        ),
        (
            "los sistemas de recomendación",
            "Los sistemas de recomendación anticipan lo que te gusta según lo "
            "que ya consumes.",
            "Plano medio de una persona viendo sugerencias en una pantalla, "
            "{style}, {lighting}, {palette}.",
        ),
        (
            "la traducción automática",
            "La traducción automática derriba barreras de idioma en tiempo "
            "real.",
            "Plano general de dos personas hablando a través de un traductor "
            "simultáneo, {style}, {lighting}, {palette}.",
        ),
    ),
    "ciencia": (
        (
            "la edición genética",
            "La edición genética permite modificar el ADN con una precisión "
            "sin precedentes.",
            "Primer plano de una doble hélice de ADN siendo editada por una "
            "herramienta molecular, {style}, {lighting}, {palette}.",
        ),
        (
            "los telescopios espaciales",
            "Los telescopios espaciales observan galaxias a miles de millones "
            "de años luz.",
            "Plano general de un telescopio en órbita apuntando a un campo de "
            "estrellas, {style}, {lighting}, {palette}.",
        ),
        (
            "la energía de fusión",
            "La energía de fusión promete una fuente de energía limpia y casi "
            "ilimitada.",
            "Plano general de un reactor de fusión con plasma brillante, "
            "{style}, {lighting}, {palette}.",
        ),
        (
            "los medicamentos personalizados",
            "Los medicamentos personalizados se diseñan según el perfil "
            "genético de cada persona.",
            "Plano detalle de un laboratorio preparando una medicina a medida, "
            "{style}, {lighting}, {palette}.",
        ),
        (
            "la biología sintética",
            "La biología sintética diseña organismos que producen materiales y "
            "medicinas.",
            "Primer plano de microorganismos modificados en un biorreactor, "
            "{style}, {lighting}, {palette}.",
        ),
        (
            "los exoplanetas",
            "Los exoplanetas orbitan estrellas lejanas y podrían albergar "
            "vida.",
            "Plano lateral de un planeta orbitando una estrella con un cielo "
            "extraño, {style}, {lighting}, {palette}.",
        ),
    ),
    "historia": (
        (
            "la escritura",
            "La escritura permitió guardar el conocimiento y transmitirlo a lo "
            "largo del tiempo.",
            "Primer plano de unas manos escribiendo sobre pergamino con luz "
            "cálida, {style}, {lighting}, {palette}.",
        ),
        (
            "la imprenta",
            "La imprenta multiplicó los libros y abrió el conocimiento a todo "
            "el mundo.",
            "Plano medio de una prensa de tipos móviles imprimiendo páginas, "
            "{style}, {lighting}, {palette}.",
        ),
        (
            "la navegación",
            "La navegación conectó continentes y dio inicio a la era de los "
            "descubrimientos.",
            "Plano general de un barco de vela cruzando el océano hacia el "
            "horizonte, {style}, {lighting}, {palette}.",
        ),
        (
            "la revolución industrial",
            "La revolución industrial cambió la forma de producir y de vivir "
            "en las ciudades.",
            "Plano lateral de una fábrica histórica con máquinas de vapor en "
            "marcha, {style}, {lighting}, {palette}.",
        ),
        (
            "el ferrocarril",
            "El ferrocarril acortó distancias y transformó el comercio y los "
            "viajes.",
            "Plano general de una locomotora de vapor avanzando por una vía, "
            "{style}, {lighting}, {palette}.",
        ),
        (
            "la electricidad",
            "La electricidad transformó hogares, fábricas y la vida "
            "cotidiana.",
            "Plano detalle de un interruptor antiguo encendiendo una lámpara, "
            "{style}, {lighting}, {palette}.",
        ),
    ),
    "curiosidad": (
        (
            "los colores del cielo",
            "Los colores del cielo cambian según cómo la luz se dispersa en "
            "la atmósfera.",
            "Plano general de un cielo que pasa del azul al naranja al "
            "atardecer, {style}, {lighting}, {palette}.",
        ),
        (
            "los imanes",
            "Los imanes aprovechan una fuerza invisible que usamos cada día.",
            "Primer plano de limaduras de hierro formando líneas alrededor de "
            "un imán, {style}, {lighting}, {palette}.",
        ),
        (
            "el eco",
            "El eco es el sonido que rebota en las superficies y vuelve a "
            "nosotros.",
            "Plano general de un valle con montañas reflejando ondas de "
            "sonido, {style}, {lighting}, {palette}.",
        ),
        (
            "las burbujas",
            "Las burbujas siempre son esféricas porque la tensión busca la "
            "menor superficie.",
            "Primer plano de burbujas de jabón flotando con reflejos de "
            "colores, {style}, {lighting}, {palette}.",
        ),
        (
            "el sueño",
            "El sueño consolida la memoria y recarga el cerebro mientras "
            "descansas.",
            "Plano medio de una persona durmiendo bajo un mapa de actividad "
            "cerebral, {style}, {lighting}, {palette}.",
        ),
        (
            "la fotosíntesis",
            "La fotosíntesis convierte luz y aire en el oxígeno que "
            "respiramos.",
            "Primer plano de una hoja verde con luz solar brillando a través "
            "de ella, {style}, {lighting}, {palette}.",
        ),
    ),
}

#: Catálogo por defecto cuando el tema no coincide con ninguna categoría.
_DEFAULT_ENUM_CONCEPTS: tuple[tuple[str, str, str], ...] = (
    (
        "la observación",
        "La observación atenta revela detalles que la mayoría pasa por alto.",
        "Primer plano de unas manos sosteniendo una lupa sobre un objeto "
        "pequeño, {style}, {lighting}, {palette}.",
    ),
    (
        "el método",
        "Un método ordenado convierte un problema enorme en pasos pequeños.",
        "Plano cenital de un escritorio con notas y pasos numerados, {style}, "
        "{lighting}, {palette}.",
    ),
    (
        "la práctica",
        "La práctica repetida convierte lo difícil en algo natural.",
        "Plano medio de unas manos repitiendo un ejercicio una y otra vez, "
        "{style}, {lighting}, {palette}.",
    ),
    (
        "el tiempo",
        "El tiempo bien usado marca la diferencia entre aprender y quedarse "
        "igual.",
        "Plano general de un reloj de arena con granos cayendo, {style}, "
        "{lighting}, {palette}.",
    ),
    (
        "la atención",
        "La atención enfocada hace que una hora rinda más que un día "
        "distraído.",
        "Primer plano de una persona concentrada en una tarea con fondo "
        "desenfocado, {style}, {lighting}, {palette}.",
    ),
    (
        "la curiosidad",
        "La curiosidad bien dirigida abre puertas que nadie esperaba.",
        "Plano lateral de una persona mirando un horizonte con asombro, "
        "{style}, {lighting}, {palette}.",
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

    @classmethod
    def _resolve_visual_style(cls, topic: str) -> tuple[str, str, str]:
        """Resuelve (estilo, iluminación, paleta) para el tema.

        Usa el estilo de la categoría detectada o el estilo realista general.
        """
        category = cls._detect_category(topic)
        return _CATEGORY_STYLES.get(category, _DEFAULT_STYLE)

    @classmethod
    def _essence(cls, topic: str) -> str:
        """Esencia corta del tema para hook/CTA y planos de apertura/cierre."""
        category = cls._detect_category(topic)
        return _CATEGORY_ESSENCE.get(category, _DEFAULT_ESSENCE)

    @classmethod
    def _leading_count(cls, topic: str) -> Optional[int]:
        """Devuelve el numeral inicial del tema como entero (o ``None``)."""
        match = _LEADING_NUMBER_RE.match(topic.strip())
        if not match:
            return None
        try:
            return int(match.group(0))
        except ValueError:
            return None

    @classmethod
    def _scene_count(cls, topic: str) -> int:
        """Número de escenas: 5 por defecto, 6 solo si el tema lo justifica.

        Un tema que comienza con un numeral ≥ 6 anuncia una lista de ideas
        independientes suficiente para seis escenas; en caso contrario se
        mantienen las cinco escenas objetivo (nunca se duplica contenido para
        forzar una sexta).
        """
        count = cls._leading_count(topic)
        if count is not None and count >= MAX_SCENE_COUNT:
            return MAX_SCENE_COUNT
        return DEFAULT_SCENE_COUNT

    @staticmethod
    def _count_words(text: str) -> int:
        """Cuenta las palabras significativas de un texto en español."""
        return len(_WORD_RE.findall(text))

    @staticmethod
    def _stable_indices(topic: str, idea_count: int) -> tuple[int, int, int]:
        """Índices estables (hook, inicio de ventana de ideas, CTA).

        Se derivan de tramos distintos del SHA-256 del tema: el mismo tema
        produce siempre la misma combinación y temas distintos la rotan. La
        ventana de ideas es consecutiva para mantener un arco narrativo
        coherente.
        """
        digest = hashlib.sha256(topic.encode("utf-8")).hexdigest()
        hook_index = int(digest[0:8], 16) % len(_HOOKS)
        window_starts = max(1, len(_IDEA_PAIRS) - idea_count + 1)
        idea_start = int(digest[8:16], 16) % window_starts
        cta_index = int(digest[16:24], 16) % len(_CTAS)
        return hook_index, idea_start, cta_index

    @classmethod
    def _selected_ideas(cls, topic: str, idea_count: int) -> tuple[tuple[str, str], ...]:
        """Selecciona una ventana consecutiva de ideas con su descripción visual."""
        _, idea_start, _ = cls._stable_indices(topic, idea_count)
        return _IDEA_PAIRS[idea_start : idea_start + idea_count]

    @staticmethod
    def _detect_enumerative(topic: str) -> Optional[tuple[int, str]]:
        """Detecta un tema enumerativo: número + concepto ("5 inventos", "los X").

        Returns:
            Tupla ``(N, sustantivo)`` donde ``N`` es el número de elementos
            prometido y ``sustantivo`` el sustantivo enumerativo tal como
            aparece en el tema; o ``None`` si el tema no es enumerativo.
        """
        match = _ENUM_COUNT_RE.search(topic)
        if match:
            try:
                n = int(match.group(1))
            except ValueError:
                n = DEFAULT_ENUM_COUNT
            return n, match.group(2)
        match = _ENUM_LIST_RE.search(topic)
        if match:
            return DEFAULT_ENUM_COUNT, match.group(1)
        return None

    @classmethod
    def _selected_concepts(
        cls, topic: str, count: int
    ) -> tuple[tuple[str, str, str], ...]:
        """Selecciona ``count`` conceptos concretos distintos para el tema.

        Usa la categoría detectada (o el catálogo por defecto) y recorre el
        catálogo desde un offset estable derivado del hash del tema, sin
        repetir conceptos.
        """
        category = cls._detect_category(topic)
        pool = _ENUM_CONCEPTS.get(category, _DEFAULT_ENUM_CONCEPTS)
        count = min(count, len(pool))
        offset = int(hashlib.sha256(topic.encode("utf-8")).hexdigest()[8:16], 16) % len(pool)
        return tuple(pool[(offset + i) % len(pool)] for i in range(count))

    @staticmethod
    def _enum_stable_indices(topic: str) -> tuple[int, int]:
        """Índices estables (hook enumerativo, CTA enumerativo)."""
        digest = hashlib.sha256(topic.encode("utf-8")).hexdigest()
        hook_index = int(digest[0:8], 16) % len(_ENUM_HOOKS)
        cta_index = int(digest[16:24], 16) % len(_ENUM_CTAS)
        return hook_index, cta_index

    @staticmethod
    def _number_to_words(n: int) -> str:
        """Convierte un número a palabras para los ganchos enumerativos."""
        return _NUMBER_WORDS.get(n, str(n))

    @staticmethod
    def _shot_visual(template_index: int, essence: str, style: tuple[str, str, str]) -> str:
        """Construye el plano de apertura (0) o cierre (2) con la esencia."""
        style_text, lighting_text, palette_text = style
        return _SCENE_SHOTS[template_index].format(
            subject=essence,
            style=style_text,
            lighting=lighting_text,
            palette=palette_text,
        )

    @staticmethod
    def _idea_visual(visual: str, style: tuple[str, str, str]) -> str:
        """Completa la descripción visual específica de una idea con el estilo."""
        style_text, lighting_text, palette_text = style
        return visual.format(
            style=style_text,
            lighting=lighting_text,
            palette=palette_text,
        )

    def _build_narration(
        self,
        topic: str,
        essence: str,
        ideas: tuple[tuple[str, str], ...],
    ) -> tuple[str, str, str, str]:
        """Construye el guion hablado: hook + ideas concretas + CTA.

        Selecciona de forma determinista un gancho y un cierre según el hash
        estable del tema; el desarrollo son las ideas seleccionadas, una por
        escena. Garantiza que la narración se mantenga dentro de 45-90 palabras
        (añadiendo una frase general si quedara corta).
        """
        idea_count = len(ideas)
        hook_index, _, cta_index = self._stable_indices(topic, idea_count)
        hook = _HOOKS[hook_index].format(e=essence)
        call_to_action = _CTAS[cta_index].format(e=essence)
        development = " ".join(claim for claim, _ in ideas)
        narration = f"{hook} {development} {call_to_action}"
        if self._count_words(narration) < MIN_NARRATION_WORDS:
            development = f"{development} {_LOW_WORD_FILLER}"
            narration = f"{hook} {development} {call_to_action}"
        return hook, development, call_to_action, narration

    def _build_package(self, topic: str) -> dict[str, Any]:
        """Construye el dict serializable del ContentPackage sintético.

        El guion sigue la estructura HOOK → IDEA 1 → IDEA 2 → IDEA 3 →
        CIERRE/CTA (seis escenas solo si el tema anuncia al menos seis ideas).
        Cada escena representa una idea concreta y lleva su propia descripción
        visual, derivada de esa idea y no del título global. El gancho nunca
        repite literalmente el título; la narración aporta explicaciones
        generales correctas sin datos inventados; el cierre es contextual.

        Si el tema es enumerativo ("5 inventos...", "los X..."), se delega en
        :meth:`_build_enumerative_package`: HOOK → N elementos concretos → CTA
        (sin escena vacía exclusiva para el CTA).
        """
        enum = self._detect_enumerative(topic)
        if enum is not None:
            return self._build_enumerative_package(topic, enum)
        return self._build_general_package(topic)

    def _build_general_package(self, topic: str) -> dict[str, Any]:
        """Construye un ContentPackage no enumerativo (estructura ME38.1)."""
        words = self._words(topic)
        content_id = self._stable_slug(topic)
        essence = self._essence(topic)
        style = self._resolve_visual_style(topic)
        scene_count = self._scene_count(topic)
        idea_count = scene_count - 2  # hook + cierre
        ideas = self._selected_ideas(topic, idea_count)

        hook, development, call_to_action, narration = self._build_narration(
            topic, essence, ideas
        )

        scenes = [
            {
                "description": self._shot_visual(0, essence, style),
                "timing_seconds": HOOK_TIMING_SECONDS,
            }
        ]
        for _, visual in ideas:
            scenes.append(
                {
                    "description": self._idea_visual(visual, style),
                    "timing_seconds": IDEA_TIMING_SECONDS,
                }
            )
        scenes.append(
            {
                "description": self._shot_visual(2, essence, style),
                "timing_seconds": CIERRE_TIMING_SECONDS,
            }
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
                "hook": hook,
                "development": development,
                "call_to_action": call_to_action,
            },
            "visuals": {
                "scenes": scenes,
                "style_notes": (
                    "Estilo fotorrealista y coherente (determinista); cada "
                    "escena representa una idea concreta del guion."
                ),
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

    def _build_enumerative_package(
        self, topic: str, enum: tuple[int, str]
    ) -> dict[str, Any]:
        """Construye un ContentPackage para temas enumerativos (ME38.3).

        Estructura: HOOK → elemento 1 → elemento 2 → ... → elemento N → CTA.
        Cada elemento es un concepto concreto con su propia descripción visual;
        el CTA se integra en la narración (no hay escena vacía para el CTA).
        El número de elementos se limita a un máximo razonable.
        """
        words = self._words(topic)
        content_id = self._stable_slug(topic)
        essence = self._essence(topic)
        style = self._resolve_visual_style(topic)
        n, noun = enum
        n = min(n, MAX_ENUM_ELEMENTS)
        concepts = self._selected_concepts(topic, n)

        hook_index, cta_index = self._enum_stable_indices(topic)
        nword = self._number_to_words(n)
        feminine = noun.lower() in _ENUM_FEMININE
        art = "las" if feminine else "los"
        det = "estas" if feminine else "estos"
        hook = _ENUM_HOOKS[hook_index].format(
            n=nword, noun=noun, art=art, det=det.capitalize(), e=essence
        )
        call_to_action = _ENUM_CTAS[cta_index].format(
            n=nword, noun=noun, art=art, det=det, e=essence
        )
        development = " ".join(claim for _, claim, _ in concepts)
        narration = f"{hook} {development} {call_to_action}"
        if self._count_words(narration) < MIN_NARRATION_WORDS:
            development = f"{development} {_LOW_WORD_FILLER}"
            narration = f"{hook} {development} {call_to_action}"

        scenes = [
            {
                "description": self._shot_visual(0, essence, style),
                "timing_seconds": HOOK_TIMING_SECONDS,
            }
        ]
        for _, _, visual in concepts:
            scenes.append(
                {
                    "description": self._idea_visual(visual, style),
                    "timing_seconds": IDEA_TIMING_SECONDS,
                }
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
                "notes": (
                    "Contenido sintético offline; tema enumerativo con "
                    "conceptos concretos sin datos inventados."
                ),
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
                "style_notes": (
                    "Estilo fotorrealista y coherente (determinista); cada "
                    "escena corresponde a un elemento concreto del tema "
                    "enumerativo."
                ),
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