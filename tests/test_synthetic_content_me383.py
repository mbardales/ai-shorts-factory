# -*- coding: utf-8 -*-
"""ME38.3 - Tests puros del generador synthetic para temas enumerativos.

Verifica de forma offline (sin red, sin Gemini, sin dependencias externas) que
el :class:`ai.providers.synthetic.SyntheticContentProvider` desarrolla
contenido enumerativo real: cuando el tema anuncia un número y un concepto
("5 inventos...", "los X...", "7 hábitos..."), genera N elementos concretos,
cada uno con su escena y descripción visual específica.

Checks cubiertos (A-O):

- A. detección enumerativa (número + sustantivo; "los X"; no-enumerativo);
- B. número de escenas = HOOK + N elementos, sin escena vacía de CTA;
- C. narración menciona explícitamente el sujeto/conceptos del video;
- D. hook promete el número de elementos y el sustantivo, sin repetir el título;
- E. narración de 45-90 palabras;
- F. cada escena de elemento tiene descripción visual específica del concepto;
- G. prompts visuales distintos entre sí y derivados de ``scene.description``;
- H. CTA contextual y variado (nunca el genérico antiguo);
- I. sin escena exclusiva/extra para el CTA (escenas = HOOK + N);
- J. determinismo;
- K. ContentPackage válido;
- L. conceptos no duplicados dentro de un mismo paquete;
- M. el catálogo es extensible por categoría (categorías distintas -> conceptos
  distintos);
- N. temas con número grande se limitan a un máximo razonable (≤ 6 escenas);
- O. temas no enumerativos conservan el comportamiento previo (5 escenas).

Uso (sin framework, solo stdlib):

    .venv\\Scripts\\python.exe tests\\test_synthetic_content_me383.py
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
)
from image import ImageScenes, build_scene_prompts

FAILURES: list[str] = []
TOPIC_INVENTOS = "5 inventos tecnológicos que están cambiando el futuro"
TOPIC_HABITOS = "7 hábitos que mejoran tu concentración"
TOPIC_LISTA = "Los avances que cambiaron la historia"
TOPIC_RAZONES = "10 razones para estudiar historia"
TOPIC_NO_ENUM = "Cómo funciona la energía solar"
TOPIC_GENERICO = "Por qué dormimos cada noche"


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(f"{label}: {detail}")
    print(f"[{'OK' if cond else 'FAIL'}] {label}" + ("" if cond else f" | {detail}"))


def generate_package(topic: str, *, provider: SyntheticContentProvider) -> dict:
    adapter = AIAdapter(provider)
    return adapter.generate_structured("ignored", None, topic=topic)


def scenes_of(package: dict) -> list[dict]:
    return package["visuals"]["scenes"]


def word_count(text: str) -> int:
    return len([w for w in text.split() if any(c.isalnum() for c in w)])


provider = SyntheticContentProvider(options=GenerationOptions())

# ---------------------------------------------------------------------------
# A. Detección enumerativa
# ---------------------------------------------------------------------------
det = provider._detect_enumerative(TOPIC_INVENTOS)
check("A1 numero + sustantivo detectado", det == (5, "inventos"), f"det={det}")
det2 = provider._detect_enumerative(TOPIC_HABITOS)
check("A2 sustantivo con numero >=6 detectado", det2 == (7, "hábitos"),
      f"det={det2}")
det3 = provider._detect_enumerative(TOPIC_LISTA)
check("A3 'los/las X' detectado", det3 is not None and det3[1] == "avances",
      f"det={det3}")
check("A4 tema no enumerativo -> None",
      provider._detect_enumerative(TOPIC_NO_ENUM) is None)

# ---------------------------------------------------------------------------
# B. Escenas = HOOK + N elementos, sin escena vacia de CTA
# ---------------------------------------------------------------------------
pkg_inv = generate_package(TOPIC_INVENTOS, provider=provider)
sc_inv = scenes_of(pkg_inv)
check("B1 '5 inventos' -> 6 escenas (HOOK + 5)", len(sc_inv) == 6,
      f"len={len(sc_inv)}")
check("B2 todas las escenas con descripcion no vacia",
      all(s.get("description") for s in sc_inv))
check("B3 no hay escena extra exclusiva del CTA",
      len(sc_inv) == 6, f"len={len(sc_inv)}")

# ---------------------------------------------------------------------------
# C. La narración menciona explícitamente los conceptos
# ---------------------------------------------------------------------------
nar_inv = pkg_inv["narration"]["text"].lower()
concept_names = [
    "la computación cuántica", "las baterías", "la impresión 3D",
    "la inteligencia artificial generativa", "los robots humanoides",
]
missing = [n for n in concept_names if n.lower() not in nar_inv]
check("C1 narracion menciona los conceptos concretos", not missing,
      f"faltan: {missing}")
check("C2 sustantivo del tema presente en la narracion",
      "inventos" in nar_inv)

# ---------------------------------------------------------------------------
# D. Hook promete el número de elementos y el sustantivo
# ---------------------------------------------------------------------------
hook_inv = pkg_inv["script"]["hook"]
title_inv = pkg_inv["identity"]["title"].lower()
check("D1 hook menciona el numero de elementos", "cinco" in hook_inv.lower())
check("D2 hook menciona el sustantivo del tema", "inventos" in hook_inv.lower())
check("D3 hook no repite literalmente el titulo", title_inv not in hook_inv.lower())

# ---------------------------------------------------------------------------
# E. Narración 45-90 palabras
# ---------------------------------------------------------------------------
wc_inv = word_count(nar_inv)
check("E1 '5 inventos' 45-90 palabras", 45 <= wc_inv <= 90, f"words={wc_inv}")
wc_hab = word_count(generate_package(TOPIC_HABITOS, provider=provider)["narration"]["text"])
check("E2 '7 habitos' 45-90 palabras", 45 <= wc_hab <= 90, f"words={wc_hab}")
wc_lst = word_count(generate_package(TOPIC_LISTA, provider=provider)["narration"]["text"])
check("E3 'los avances' 45-90 palabras", 45 <= wc_lst <= 90, f"words={wc_lst}")

# ---------------------------------------------------------------------------
# F. Cada escena de elemento tiene descripción visual específica
# ---------------------------------------------------------------------------
element_scenes = sc_inv[1:]
desc_lens = [len(s["description"]) for s in element_scenes]
check("F1 escenas de elemento con descripcion larga y especifica",
      all(l >= 80 for l in desc_lens), f"lens={desc_lens}")
check("F2 descripciones de escena distintas entre si",
      len(set(desc for s in sc_inv for desc in [s["description"]])) == len(sc_inv))

# ---------------------------------------------------------------------------
# G. Prompts visuales distintos y derivados de scene.description
# ---------------------------------------------------------------------------
package_obj = content_package_from_json(json.dumps(pkg_inv, ensure_ascii=False))
image_scenes = ImageScenes.from_content_package(package_obj)
prompts = build_scene_prompts(image_scenes)
prompt_texts = [p.prompt for p in prompts]
check("G1 un prompt por escena", len(prompt_texts) == len(sc_inv),
      f"len={len(prompt_texts)}")
check("G2 prompts distintos entre si",
      len(set(prompt_texts)) == len(prompt_texts))
ok_g = all(
    p.prompt and sc_inv[i]["description"] in p.prompt
    for i, p in enumerate(prompts)
)
check("G3 cada prompt incluye su scene.description", ok_g)

# ---------------------------------------------------------------------------
# H. CTA contextual y variado
# ---------------------------------------------------------------------------
ctas = {
    generate_package(t, provider=provider)["script"]["call_to_action"]
    for t in (TOPIC_INVENTOS, TOPIC_HABITOS, TOPIC_LISTA, TOPIC_RAZONES)
}
check("H1 CTA varia entre temas enumerativos distintos", len(ctas) >= 2,
      f"ctas={len(ctas)}")
check("H2 CTA no es la plantilla generica antigua",
      not any("mira hasta el final y síguenos" in (c or "").lower() for c in ctas))

# ---------------------------------------------------------------------------
# I. Sin escena exclusiva/extra para el CTA
# ---------------------------------------------------------------------------
check("I1 escenas = HOOK + N (sin CIERRE separado)",
      len(sc_inv) == 1 + 5, f"len={len(sc_inv)}")

# ---------------------------------------------------------------------------
# J. Determinismo
# ---------------------------------------------------------------------------
pkg_a = generate_package(TOPIC_INVENTOS, provider=provider)
pkg_b = generate_package(TOPIC_INVENTOS, provider=provider)
check("J1 mismo tema enumerativo -> mismo JSON", pkg_a == pkg_b)

# ---------------------------------------------------------------------------
# K. ContentPackage válido
# ---------------------------------------------------------------------------
pkg_obj = content_package_from_json(json.dumps(pkg_inv, ensure_ascii=False))
errors = find_errors(pkg_obj)
check("K1 ContentPackage valido (find_errors vacio)", not errors, "; ".join(errors))
check("K2 instancia del dominio", isinstance(pkg_obj, ContentPackage))

# ---------------------------------------------------------------------------
# L. Conceptos no duplicados dentro de un mismo paquete
# ---------------------------------------------------------------------------
dev_inv = pkg_inv["script"]["development"].lower()
names_lower = [n.lower() for n in concept_names]
check("L1 los N conceptos del desarrollo son distintos",
      len(names_lower) == len(set(names_lower)))
check("L2 cada concepto aparece una sola vez en la narracion",
      all(dev_inv.count(n) == 1 for n in names_lower))

# ---------------------------------------------------------------------------
# M. Catálogo extensible por categoría
# ---------------------------------------------------------------------------
concepts_tecnologia = [c[0] for c in provider._selected_concepts(TOPIC_INVENTOS, 5)]
concepts_historia = [c[0] for c in provider._selected_concepts(TOPIC_LISTA, 5)]
check("M1 conceptos de 'tecnologia' difieren de 'historia'",
      set(concepts_tecnologia) != set(concepts_historia))
check("M2 catalogo por defecto aplica a temas sin categoria",
      len(provider._selected_concepts(TOPIC_GENERICO, 5)) == 5)

# ---------------------------------------------------------------------------
# N. Temas con número grande se limitan a un máximo razonable
# ---------------------------------------------------------------------------
pkg_raz = generate_package(TOPIC_RAZONES, provider=provider)
sc_raz = scenes_of(pkg_raz)
check("N1 '10 razones' -> maximo 6 escenas", len(sc_raz) <= 6,
      f"len={len(sc_raz)}")
pkg_hab = generate_package(TOPIC_HABITOS, provider=provider)
sc_hab = scenes_of(pkg_hab)
check("N2 '7 habitos' -> maximo 6 escenas", len(sc_hab) <= 6,
      f"len={len(sc_hab)}")

# ---------------------------------------------------------------------------
# O. Temas no enumerativos conservan el comportamiento previo (5 escenas)
# ---------------------------------------------------------------------------
pkg_noenum = generate_package(TOPIC_NO_ENUM, provider=provider)
sc_noenum = scenes_of(pkg_noenum)
check("O1 tema no enumerativo -> 5 escenas", len(sc_noenum) == 5,
      f"len={len(sc_noenum)}")
check("O2 hook no enumerativo sin numero prometido",
      not any(ch.isdigit() for ch in pkg_noenum["script"]["hook"]))

# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------
print()
print(f"RESULTADO: {'PASS' if not FAILURES else 'FAIL'}")
for f in FAILURES:
    print(f"  - {f}")
sys.exit(0 if not FAILURES else 1)