# PROJECT_STATE.md

## Propósito

Consolidar el estado actual del proyecto **AI Shorts Factory** en un único documento de referencia, para orientar cualquier sesión de trabajo o agente que ingrese al repositorio.

## Estado

- **Etapa del proyecto:** Inicial / planificación.
- **Última actualización:** 2026-08-06.
- **Histórico de cambios:** ver `git log`.

## Descripción

### Qué es el proyecto

Analizador y generador de YouTube Shorts (según `README.md`). El sistema previsto comprende dos flujos centrales: **análisis** de Shorts existentes y **generación** de contenido corto, ambos asistidos por modelos de lenguaje.

### Estado del código

- **No existe** código fuente, dependencias, sistema de build, ni pruebas.
- El repositorio contiene únicamente el esqueleto de directorios (archivos `.gitkeep`).
- Rama: `main`. Historial: 2 commits (`Initial commit`, `chore: initialize project structure`).
- Sin `.gitignore`; sin CI; sin `opencode.json`; sin configuraciones.

### Estructura vigente

Ver [MODULES.md](architecture/MODULES.md). Resumen: áreas funcionales `prompts/`, `workflows/`, `assets/`, `config/`, `docs/`, `examples/`, `scripts/`, `backups/`, `logs/`.

### Documentación

| Área | Documentos |
|---|---|
| Arquitectura | [SYSTEM_OVERVIEW.md](architecture/SYSTEM_OVERVIEW.md), [MODULES.md](architecture/MODULES.md), [DATA_FLOW.md](architecture/DATA_FLOW.md) |
| Guías | [DEVELOPMENT_GUIDE.md](guides/DEVELOPMENT_GUIDE.md), [GIT_WORKFLOW.md](guides/GIT_WORKFLOW.md) |
| Roadmap | [MVP.md](roadmap/MVP.md), [BACKLOG.md](roadmap/BACKLOG.md) |
| Decisiones | [ADR-001-Architecture.md](decisions/ADR-001-Architecture.md) |
| Estado | Este documento |

### Decisiones tomadas

- **ADR-001:** organizar el repositorio por áreas funcionales (prompts, workflows, assets, configuración, documentación). Ver [ADR-001-Architecture.md](decisions/ADR-001-Architecture.md).

### Trabajo en curso / próximos pasos

1. Elegir el stack tecnológico (lenguaje, build, dependencias).
2. Concretar el alcance funcional del analizador y del generador (ver [MVP.md](roadmap/MVP.md)).
3. Crear `.gitignore`.
4. Definir el primer pipeline mínimo a construir.
5. Actualizar este documento al avanzar.

## Secciones principales

- [Qué es el proyecto](#que-es-el-proyecto)
- [Estado del código](#estado-del-codigo)
- [Estructura vigente](#estructura-vigente)
- [Documentación](#documentacion)
- [Decisiones tomadas](#decisiones-tomadas)
- [Trabajo en curso / próximos pasos](#trabajo-en-curso--proximos-pasos)

## Pendientes (TODO)

- [ ] Reflejar aquí cada hito alcanzado (definición de stack, MVP implementado, etc.).
- [ ] Mantener sincronizado este documento con [BACKLOG.md](roadmap/BACKLOG.md) y [MVP.md](roadmap/MVP.md).
- [ ] Incluir los cambios de estado como entradas fechadas al avanzar el proyecto.
- [ ] Confirmar formato de seguimiento (¿historial fechado, viñetas, hito por hito?).
