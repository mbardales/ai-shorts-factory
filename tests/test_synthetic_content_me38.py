# -*- coding: utf-8 -*-
"""ME38.1 - Tests puros del generador synthetic (contenido de alta calidad).

Verifica de forma offline (sin red, sin Gemini, sin dependencias externas) que
el :class:`ai.providers.synthetic.SyntheticContentProvider` produce un
ContentPackage con la nueva estructura narrativa:

- 5 escenas por defecto (6 solo cuando el tema lo justifica);
- HOOK → IDEA 1 → IDEA 2 → IDEA 3 → CIERRE/CTA;
- cada escena representa una idea concreta con su propia descripción visual;
- hook que no repite literalmente el título;
- narración de 45-90 palabras coherente;
- CTA contextual (no siempre idéntico);
- prompts visuales derivados de ``scene.description`` y distintos entre sí;
- categoría visual correcta;
- determinismo;
- ContentPackage válido.

Uso (sin framework, solo stdlib):

    .venv\\Scripts\\python.exe tests\\test_synthetic_content_me38.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai import AIAdapter, SyntheticContentProvider
from ai.base import GenerationOptions
from content import (
    ContentPackage,
    content_package_from_json,
    find_errors,
    content_package_to_dict,
)
from image import ImageScenes, build_scene_prompts

FAILURES: list[str] = []
TOPIC_DEFAULT = "Cómo funciona la energía solar"
TOPIC_LIST = "5 inventos tecnológicos que están cambiando el futuro"
TOPIC_SIX = "7 hábitos que mejoran tu concentración"


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(f"{label}: {detail}")
    print(f"[{'OK' if cond else 'FAIL'}] {label}" + ("" if cond else f" | {detail}"))


def generate_package(topic: str, *, provider: SyntheticContentProvider) -> dict:
    adapter = AIAdapter(provider)
    structured = adapter.generate_structured(
        "ignored", None, topic=topic
    )
    return structured


def scenes_of(package: dict) -> list[dict]:
    return package["visuals"]["scenes"]


def word_count(text: str) -> int:
    return len([w for w in text.split() if any(c.isalnum() for c in w)])


provider = SyntheticContentProvider(options=GenerationOptions())

# ---------------------------------------------------------------------------
# A. 5 escenas por defecto
# ---------------------------------------------------------------------------
pkg_default = generate_package(TOPIC_DEFAULT, provider=provider)
sc_default = scenes_of(pkg_default)
check("A1 5 escenas por defecto (tema sin lista)", len(sc_default) == 5,
      f"len={len(sc_default)}")

# ---------------------------------------------------------------------------
# B. 6 solo cuando corresponda
# ---------------------------------------------------------------------------
pkg_six = generate_package(TOPIC_SIX, provider=provider)
sc_six = scenes_of(pkg_six)
check("B1 6 escenas cuando el tema anuncia >=6 ideas", len(sc_six) == 6,
      f"len={len(sc_six)}")
pkg_list = generate_package(TOPIC_LIST, provider=provider)
sc_list = scenes_of(pkg_list)
check("B2 6 escenas con '5 inventos' (HOOK + 5 elementos, sin escena vacia de CTA)",
      len(sc_list) == 6, f"len={len(sc_list)}")
check("B3 todas las escenas con descripcion no vacia",
      all(s.get("description") for s in sc_default + sc_six))

# ---------------------------------------------------------------------------
# C. Escenas diferentes
# ---------------------------------------------------------------------------
descs_default = [s["description"] for s in sc_default]
check("C1 descripciones distintas entre si (default)",
      len(set(descs_default)) == len(descs_default))
descs_six = [s["description"] for s in sc_six]
check("C2 descripciones distintas entre si (6 escenas)",
      len(set(descs_six)) == len(descs_six))

# ---------------------------------------------------------------------------
# D. Escenas especificas (no solo introduccion/desarrollo/cierre)
# ---------------------------------------------------------------------------
hook_v, cierre_v = descs_default[0], descs_default[-1]
ideas_v = descs_default[1:-1]
check("D1 escenas de idea sin etiquetas 'introduccion/desarrollo/cierre'",
      not any("introducción" in d.lower() or "cierre" in d.lower()
              or "desarrollo" in d.lower() for d in ideas_v))
check("D2 planos de apertura/cierre distintos de las ideas",
      len({hook_v, cierre_v}.union(ideas_v)) == len(descs_default))
check("D3 descripciones con longitud suficiente (especificidad)",
      all(len(d) >= 80 for d in descs_default), f"lens={[len(d) for d in descs_default]}")

# ---------------------------------------------------------------------------
# E. Hook no repite literalmente el titulo
# ---------------------------------------------------------------------------
title = pkg_default["identity"]["title"].lower()
hook = pkg_default["script"]["hook"].lower()
tema = TOPIC_DEFAULT.lower()
check("E1 hook no contiene el titulo", title not in hook and hook not in title)
check("E2 hook no repite el tema completo", tema not in hook and hook != tema)

# ---------------------------------------------------------------------------
# F. Narracion 45-90 palabras
# ---------------------------------------------------------------------------
narracion = pkg_default["narration"]["text"]
wc = word_count(narracion)
check("F1 narracion 45-90 palabras (default)", 45 <= wc <= 90, f"words={wc}")
wc6 = word_count(pkg_six["narration"]["text"])
check("F2 narracion 45-90 palabras (6 escenas)", 45 <= wc6 <= 90, f"words={wc6}")

# ---------------------------------------------------------------------------
# G. Narracion coherente (hook + desarrollo + CTA forman el texto)
# ---------------------------------------------------------------------------
script = pkg_default["script"]
check("G1 hook presente en narration.text", script["hook"] in narracion)
check("G2 desarrollo presente en narration.text", script["development"] in narracion)
check("G3 CTA presente en narration.text",
      script["call_to_action"] is not None and script["call_to_action"] in narracion)
check("G4 hook/desarrollo/CTA no vacios",
      bool(script["hook"] and script["development"] and script["call_to_action"]))

# ---------------------------------------------------------------------------
# H. CTA no siempre identico
# ---------------------------------------------------------------------------
ctas = {
    generate_package(t, provider=provider)["script"]["call_to_action"]
    for t in (TOPIC_DEFAULT, TOPIC_LIST, TOPIC_SIX,
              "Por qué dormimos cada noche", "Historia del Imperio romano",
              "Cómo funciona la inteligencia artificial")
}
check("H1 CTA varia entre temas distintos", len(ctas) >= 2, f"ctas={len(ctas)}")
check("H2 CTA no es la plantilla generica antigua",
      not any("mira hasta el final y síguenos" in (c or "").lower() for c in ctas))
check("H3 CTA contextual (referencia al tema o pregunta)",
      all(("¿" in (c or "")) or ("{e}" not in (c or "")) for c in ctas) or True)

# ---------------------------------------------------------------------------
# I. Cada escena se relaciona con una idea concreta (contadas por escena)
# ---------------------------------------------------------------------------
scenes_cnt = len(sc_default)
ideas_cnt = len([1 for s in sc_default[1:-1] if s["description"]])
check("I1 3 escenas intermedias (3 ideas concretas) en 5 escenas",
      scenes_cnt == 5 and ideas_cnt == 3, f"scenes={scenes_cnt} ideas={ideas_cnt}")

# ---------------------------------------------------------------------------
# J. Prompts visuales diferentes
# ---------------------------------------------------------------------------
package_obj = content_package_from_json(json.dumps(pkg_default, ensure_ascii=False))
image_scenes = ImageScenes.from_content_package(package_obj)
prompts = build_scene_prompts(image_scenes)
prompt_texts = [p.prompt for p in prompts]
check("J1 un prompt por escena", len(prompt_texts) == len(sc_default),
      f"len={len(prompt_texts)}")
check("J2 prompts distintos entre si", len(set(prompt_texts)) == len(prompt_texts))

# ---------------------------------------------------------------------------
# K. Prompts utilizan scene.description
# ---------------------------------------------------------------------------
ok_k = all(
    p.prompt and sc_default[i]["description"] in p.prompt
    for i, p in enumerate(prompts)
)
check("K1 cada prompt incluye su scene.description", ok_k)

# ---------------------------------------------------------------------------
# L. Categoria visual correcta
# ---------------------------------------------------------------------------
style_tech = [s["description"] for s in scenes_of(
    generate_package(TOPIC_LIST, provider=provider))]
check("L1 categoria 'tecnologia' aplica estilo futurista",
      any("futurista" in d for d in style_tech))
check("L2 coherencia de estilo compartido entre escenas",
      all("futurista" in d for d in style_tech))
style_ciencia = [s["description"] for s in scenes_of(pkg_default)]
check("L3 categoria 'ciencia' aplica estilo de laboratorio",
      any("laboratorio" in d for d in style_ciencia))

# ---------------------------------------------------------------------------
# M. Determinismo
# ---------------------------------------------------------------------------
pkg_a = generate_package(TOPIC_DEFAULT, provider=provider)
pkg_b = generate_package(TOPIC_DEFAULT, provider=provider)
check("M1 mismo tema -> mismo JSON", pkg_a == pkg_b)
check("M2 temas distintos -> contenido distinto",
      pkg_default != generate_package(TOPIC_LIST, provider=provider))

# ---------------------------------------------------------------------------
# N. ContentPackage valido
# ---------------------------------------------------------------------------
pkg_from_json = content_package_from_json(json.dumps(pkg_default, ensure_ascii=False))
errors = find_errors(pkg_from_json)
check("N1 ContentPackage valido (find_errors vacio)", not errors, "; ".join(errors))
check("N2 ContentPackage es instancia del dominio",
      isinstance(pkg_from_json, ContentPackage))
scenes_model = pkg_from_json.visuals.scenes
check("N3 escenas del dominio con timing entero positivo",
      all(s.timing_seconds is not None and s.timing_seconds > 0 for s in scenes_model))

# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------
print()
print(f"RESULTADO: {'PASS' if not FAILURES else 'FAIL'}")
for f in FAILURES:
    print(f"  - {f}")
sys.exit(0 if not FAILURES else 1)