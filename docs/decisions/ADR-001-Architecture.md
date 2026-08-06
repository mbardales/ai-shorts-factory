# ADR-001-Architecture.md

## Propósito

Registrar la primera decisión de arquitectura del proyecto **AI Shorts Factory**: adoptar una organización del repositorio basada en **áreas funcionales** (prompts, workflows, assets, configuración, documentación) en lugar de una estructura plana.

## Estado

- **Etapa:** Aceptada / vigente.
- **Decisión:** 2026-08-06.
- **Reemplaza a:** — (sin decisión previa).

## Descripción

### Contexto

El proyecto es un analizador y generador de YouTube Shorts (según `README.md`). En el momento de la decisión no existe código, lenguaje ni herramienta de build definidos. El repositorio fue creado como un esqueleto de directorios en el commit `chore: initialize project structure`.

Se identificaron varios tipos de contenido que el proyecto producirá y consumirá: instrucciones para modelos de lenguaje (prompts), secuencias de procesos (workflows), activos multimedia, configuración y documentación. Mezclarlos en una estructura plana dificultaría localizar contenido, reutilizar piezas y escalar el sistema.

### Decisión

Adoptar una estructura de directorios por **áreas funcionales**:

```
prompts/    chains/   system/   templates/   user/
workflows/  core/     shared/   templates/   testing/
assets/     audio/    branding/ fonts/       music/
config/
docs/       architecture/ decisions/ guides/ roadmap/
examples/   scripts/   backups/  logs/
```

Reglas asociadas:

- Todo contenido nuevo se ubica en el área correspondiente; no en la raíz.
- `prompts/` contiene exclusivamente contenido destinado a LLM.
- `workflows/` contiene la orquestación de procesos; `workflows/testing/` reúne el material de pruebas.
- `config/` centraliza la configuración de la aplicación.
- `docs/decisions/` aloja los ADR; `docs/roadmap/` aloja la planificación.

### Consecuencias

**Positivas:**

- Localización predecible del contenido (cada tipo tiene un único lugar).
- Facilita reutilizar prompts y pasos de workflow.
- Separa configuración, documentación y activos del código futuro.

**Negativas / pendientes:**

- La estructura podría resultar sobredimensionada si el sistema fuera pequeño; se acepta por el alcance previsto (dos flujos, análisis y generación).
- Aún no se definen los límites exactos entre `prompts/` y `workflows/` (contrato entre ambos).
- Sin stack definido, algunos directorios (`scripts/`, `backups/`, `logs/`) no tienen convenciones concretas.

### Alternativas consideradas

1. **Estructura plana en la raíz.** Descartada: dificulta localizar y reutilizar contenido al crecer.
2. **Agrupar por flujo (analizador / generador).** Descartada por ahora: los flujos comparten piezas comunes (`workflows/shared/`, prompts base), por lo que agrupar por área es más flexible.
3. **No definir estructura hasta elegir el stack.** Descartada: la estructura de contenido (prompts, workflows, assets) es independiente del lenguaje.

## Secciones principales

- [Referencias](#referencias)

### Referencias

- [SYSTEM_OVERVIEW.md](../architecture/SYSTEM_OVERVIEW.md)
- [MODULES.md](../architecture/MODULES.md)
- [DATA_FLOW.md](../architecture/DATA_FLOW.md)
- [DEVELOPMENT_GUIDE.md](../guides/DEVELOPMENT_GUIDE.md)

## Pendientes (TODO)

- [ ] Revisar este ADR cuando se elija el stack tecnológico (los límites `prompts/` ↔ `workflows/` podrían refinarse).
- [ ] Crear ADR-002 cuando se decida el lenguaje/framework.
- [ ] Confirmar si `config/` incluye secretos y, de ser así, cómo se gestionan fuera del repositorio.
- [ ] Actualizar las consecuencias conforme se implemente el primer pipeline.
