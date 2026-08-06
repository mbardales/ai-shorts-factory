# BACKLOG.md

## Propósito

Consolidar el trabajo pendiente del proyecto **AI Shorts Factory** en un único backlog priorizado, de modo que la planificación sea trazable entre el estado del proyecto, el MVP y las decisiones de arquitectura.

## Estado

- **Etapa:** Borrador inicial. Sin tareas ejecutadas.
- **Última actualización:** 2026-08-06.

## Descripción

El backlog agrupa los pendientes por área. Cada elemento incluye una prioridad (Alta / Media / Baja) y, cuando aplica, la referencia al documento que lo origina. **Los elementos reflejan trabajo de definición y diseño**; no se ha implementado funcionalidad.

## Secciones principales

### 1. Definición técnica

| Prioridad | Tarea | Referencia |
|---|---|---|
| Alta | Elegir el lenguaje de programación y el sistema de build | [DEVELOPMENT_GUIDE.md](../guides/DEVELOPMENT_GUIDE.md) |
| Alta | Definir dependencias y entorno de ejecución | [DEVELOPMENT_GUIDE.md](../guides/DEVELOPMENT_GUIDE.md) |
| Alta | Crear `.gitignore` | [GIT_WORKFLOW.md](../guides/GIT_WORKFLOW.md) |
| Media | Configurar herramientas de calidad (lint, formatter, typecheck, tests) | [DEVELOPMENT_GUIDE.md](../guides/DEVELOPMENT_GUIDE.md) |

### 2. Arquitectura y diseño

| Prioridad | Tarea | Referencia |
|---|---|---|
| Alta | Concretar el alcance funcional del analizador de Shorts | [MVP.md](MVP.md), [SYSTEM_OVERVIEW.md](../architecture/SYSTEM_OVERVIEW.md) |
| Alta | Concretar el alcance funcional del generador de Shorts | [MVP.md](MVP.md), [SYSTEM_OVERVIEW.md](../architecture/SYSTEM_OVERVIEW.md) |
| Media | Confirmar los proveedores de IA / modelos LLM a utilizar | [SYSTEM_OVERVIEW.md](../architecture/SYSTEM_OVERVIEW.md) |
| Media | Definir el contrato entre `prompts/` y `workflows/` | [DATA_FLOW.md](../architecture/DATA_FLOW.md) |
| Media | Definir formatos de entrada y salida de cada etapa del flujo | [DATA_FLOW.md](../architecture/DATA_FLOW.md) |
| Baja | Definir arquitectura interna de cada módulo | [MODULES.md](../architecture/MODULES.md) |

### 3. Implementación (MVP)

| Prioridad | Tarea | Referencia |
|---|---|---|
| Alta | Construir el primer pipeline mínimo (análisis o generación) | [MVP.md](MVP.md) |
| Media | Definir criterios de aceptación concretos del MVP | [MVP.md](MVP.md) |

### 4. Documentación y gobernanza

| Prioridad | Tarea | Referencia |
|---|---|---|
| Media | Definir política de revisión de PR y protección de `main` | [GIT_WORKFLOW.md](../guides/GIT_WORKFLOW.md) |
| Baja | Evaluar herramientas de conventional commits y hooks | [GIT_WORKFLOW.md](../guides/GIT_WORKFLOW.md) |
| Baja | Añadir ejemplos iniciales en `examples/` | [MODULES.md](../architecture/MODULES.md) |

## Pendientes (TODO)

- [ ] Revisar, completar y priorizar este backlog con el equipo.
- [ ] Desglosar cada tarea en subtareas ejecutables.
- [ ] Vincular cada tarea a su criterio de aceptación.
- [ ] Mover las tareas completadas a un registro de "Hecho" o histórico.
- [ ] Mantener este documento sincronizado con [PROJECT_STATE.md](../PROJECT_STATE.md) y [MVP.md](MVP.md).
