# SYSTEM_OVERVIEW.md

## Propósito

Documentar la visión general del sistema **AI Shorts Factory**: su objetivo, alcance, componentes previstos y la relación entre las principales áreas del repositorio. Sirve como punto de entrada para cualquier persona (o agente) que necesite comprender el proyecto antes de trabajar en él.

## Estado

- **Etapa:** Inicial / planificación.
- **Código:** No existe código fuente, dependencias, ni sistema de build. El repositorio contiene únicamente el esqueleto de directorios (archivos `.gitkeep`).
- **Última actualización:** 2026-08-06.

## Descripción

El proyecto se define en el `README.md` como un **analizador y generador de YouTube Shorts**:

- **Análisis:** estudiar videos cortos de YouTube (YouTube Shorts).
- **Generación:** producir contenido corto de forma automatizada o asistida.

En esta fase, el objetivo del sistema está definido conceptualmente, pero **no hay funcionalidades implementadas**. La estructura de directorios del repositorio codifica la arquitectura prevista:

| Área | Directorio | Rol previsto |
|---|---|---|
| Contenido para modelos de lenguaje | `prompts/` | Instrucciones y plantillas de prompts para LLM |
| Orquestación | `workflows/` | Secuencias de pasos que componen los procesos |
| Activos multimedia | `assets/` | Audio, música, branding, tipografías |
| Configuración | `config/` | Configuración de la aplicación |
| Documentación | `docs/` | Arquitectura, decisiones, guías, roadmap |
| Soporte | `scripts/`, `examples/`, `backups/`, `logs/` | Utilidades, ejemplos, respaldos y registros |

> **Nota:** ninguna de estas áreas contiene código ni contenido todavía. Las descripciones expresan la intención de diseño, no funcionalidad existente.

## Secciones principales

- [Módulos previstos](MODULES.md) — desglose por área del repositorio.
- [Flujo de datos](DATA_FLOW.md) — recorrido de datos entre las áreas del sistema.
- [Guía de desarrollo](guides/DEVELOPMENT_GUIDE.md) — convenciones y entorno.
- [Guía de Git](guides/GIT_WORKFLOW.md) — flujo de trabajo con el repositorio.
- [Roadmap](roadmap/MVP.md) y [Backlog](roadmap/BACKLOG.md) — plan de trabajo.
- [ADR-001](decisions/ADR-001-Architecture.md) — decisiones de arquitectura registradas.
- [Estado del proyecto](PROJECT_STATE.md) — estado actual consolidado.

## Pendientes (TODO)

- [ ] Definir el lenguaje de programación y el sistema de build.
- [ ] Definir las dependencias y el entorno de ejecución.
- [ ] Concretar el alcance funcional del analizador de Shorts.
- [ ] Concretar el alcance funcional del generador de Shorts.
- [ ] Confirmar los proveedores de IA / modelos LLM a utilizar.
- [ ] Detallar los límites entre `prompts/`, `workflows/` y `config/`.
- [ ] Actualizar este documento cuando exista código fuente.
