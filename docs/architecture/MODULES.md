# MODULES.md

## Propósito

Describir las áreas (módulos previstos) del proyecto **AI Shorts Factory** tal como están codificadas en la estructura de directorios del repositorio. Su objetivo es que quien trabaje en el proyecto sepa dónde debe ubicar cada tipo de contenido o código nuevo.

## Estado

- **Etapa:** Esquema de directorios definido; sin contenido ni código en los módulos.
- **Última actualización:** 2026-08-06.

## Descripción

El proyecto aún no tiene código. La siguiente tabla describe el **rol previsto** de cada directorio, según el esqueleto creado en el commit `chore: initialize project structure`.

### `prompts/`

Contenido destinado a modelos de lenguaje (LLM):

| Subdirectorio | Rol previsto |
|---|---|
| `chains/` | Secuencias de prompts encadenados (pasos múltiples) |
| `system/` | Prompts de sistema / instrucciones base |
| `templates/` | Plantillas de prompts reutilizables |
| `user/` | Prompts del usuario / entradas |

### `workflows/`

Orquestación de los procesos del sistema:

| Subdirectorio | Rol previsto |
|---|---|
| `core/` | Workflows principales del sistema |
| `shared/` | Pasos o utilidades compartidas entre workflows |
| `templates/` | Plantillas de workflows |
| `testing/` | Workflows y material para pruebas |

### `assets/`

Activos multimedia:

| Subdirectorio | Rol previsto |
|---|---|
| `audio/` | Pistas de audio |
| `music/` | Música |
| `branding/` | Imágenes e identidad visual |
| `fonts/` | Tipografías |

### `config/`

Configuración de la aplicación (aún vacío; sin archivos).

### `docs/`

Documentación del proyecto:

| Subdirectorio | Rol previsto |
|---|---|
| `architecture/` | Vistas de arquitectura y diseño |
| `decisions/` | Registro de decisiones de arquitectura (ADR) |
| `guides/` | Guías de desarrollo y operación |
| `roadmap/` | Planificación (MVP, backlog) |

### `scripts/`, `examples/`, `backups/`, `logs/`

- `scripts/` — utilidades y automatización.
- `examples/` — ejemplos de uso.
- `backups/` — respaldos.
- `logs/` — registros (logs) de ejecución.

## Secciones principales

- [Visión general del sistema](SYSTEM_OVERVIEW.md) — contexto del proyecto.
- [Flujo de datos](DATA_FLOW.md) — cómo se relacionan las áreas entre sí.

## Pendientes (TODO)

- [ ] Decidir qué directorios se convierten en módulos de código reales y cuáles permanecen como contenido/configuración.
- [ ] Definir la arquitectura interna de cada módulo (una vez exista código).
- [ ] Definir la dependencia entre `prompts/` y `workflows/` (quién consume qué).
- [ ] Añadir ejemplos iniciales en `examples/` para validar la estructura.
- [ ] Actualizar este documento conforme los módulos se implementen.
