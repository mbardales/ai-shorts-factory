# DEVELOPMENT_GUIDE.md

## Propósito

Establecer la guía de desarrollo del proyecto **AI Shorts Factory**: estado del entorno, convenciones a seguir y qué hacer (y qué evitar) mientras no exista código.

## Estado

- **Etapa:** Inicial. No hay lenguaje, framework, dependencias, sistema de build ni pruebas definidos.
- **Última actualización:** 2026-08-06.

## Descripción

El repositorio es un **esqueleto de directorios** (solo archivos `.gitkeep`). Hasta que se defina el stack tecnológico:

- **No ejecutar** comandos de paquetes, build, lint, typecheck ni pruebas: no existen (sin `package.json`, `pyproject.toml`, manifiestos, etc.).
- **No inventar** herramientas ni configuraciones para el proyecto.

## Secciones principales

### 1. Estructura de directorios

Todo contenido nuevo debe ubicarse en el área correspondiente (ver [MODULES.md](../architecture/MODULES.md)):

- `prompts/` — contenido para LLM (con subdirectorios `chains/`, `system/`, `templates/`, `user/`).
- `workflows/` — orquestación (`core/`, `shared/`, `templates/`, `testing/`).
- `assets/` — multimedia (`audio/`, `branding/`, `fonts/`, `music/`).
- `config/` — configuración de la aplicación.
- `docs/` — documentación.
- `examples/`, `scripts/`, `backups/`, `logs/` — soporte.

### 2. Convenciones

- Los documentos del proyecto se escriben en **español**.
- Mantener los artefactos de build y los secretos fuera de los commits (aún no existe `.gitignore`; debe crearse antes de incluir estos artefactos).
- Respetar el estilo de commits del repositorio (ver [GIT_WORKFLOW.md](GIT_WORKFLOW.md)).

### 3. Documentación

- `docs/PROJECT_STATE.md` es el documento de estado del proyecto; debe mantenerse al día.
- La planificación vive en `docs/roadmap/` (`MVP.md`, `BACKLOG.md`).
- Las decisiones de arquitectura se registran en `docs/decisions/` como ADR.

### 4. Pendientes técnicos (por decidir)

- Lenguaje de programación.
- Framework / sistema de build.
- Gestión de dependencias.
- Herramientas de calidad: lint, formatter, typecheck, tests.
- Configuración de entorno y variables.

## Pendientes (TODO)

- [ ] Elegir el stack tecnológico y documentarlo aquí.
- [ ] Crear `.gitignore` adecuado al stack.
- [ ] Definir comandos de desarrollo (build, test, lint) y registrarlos en esta guía.
- [ ] Agregar esta guía a `AGENTS.md` cuando existan comandos reales.
- [ ] Actualizar esta guía conforme el proyecto evolucione.
