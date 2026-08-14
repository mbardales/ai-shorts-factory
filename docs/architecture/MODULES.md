# MODULES.md

## Propósito

Describir los módulos reales del proyecto **AI Shorts Factory**: su
responsabilidad, componentes, estado y dependencias. Su objetivo es que quien
trabaje en el proyecto sepa dónde vive cada funcionalidad y dónde ubicar código
nuevo. **Refleja el estado del código en la rama `feature/project-manifest`.**

## Estado

- **Etapa:** Implementación — pipeline de generación funcional end-to-end.
- **Última actualización:** 2026-08-13.

## Estructura de `src/`

### `src/config`

Carga centralizada del entorno.

| Componente | Rol |
|---|---|
| `config.py` (carga de `.env`) | `load_project_env()` resuelve el `.env` raíz desde la ruta del módulo (no desde CWD) |

- **Estado:** IMPLEMENTADO.
- **Dependencias:** — (usado por todos los providers).

### `src/content`

Dominio puro del "Content Package": lo que un Short debe decir.

| Componente | Rol |
|---|---|
| `ContentPackage` (agregado) | Contenido completo: identity, research, seo, script, visuals, narration, status |
| `validator` + `CONTENT_PACKAGE_SCHEMA` | Validación estructural del contenido |
| `exceptions.py` | Jerarquía de excepciones del dominio |

- **Estado:** IMPLEMENTADO.
- **Dependencias:** ninguna (dominio puro; no depende de AI/infra).

### `src/prompt_engine`

Composición de prompts para el LLM.

| Componente | Rol |
|---|---|
| `builder.build_prompt(topic, ...)` | Compone plantilla + schema JSON + reglas |
| `templates` (`PROMPT_TEMPLATE`, `JSON_SCHEMA`, `RULES`) | Materiales del prompt |

- **Estado:** IMPLEMENTADO.
- **Dependencias:** `json`/`typing` (nada del dominio; no depende de `ai`).

### `src/ai`

Abstracción de proveedores LLM.

| Componente | Rol |
|---|---|
| `providers/gemini.py` | Provider Gemini (texto) |
| `adapter.py` | Contrato/adaptador entre provider y dominio |

- **Estado:** IMPLEMENTADO.
- **Dependencias:** `content`, `config`.

### `src/media`

Infraestructura compartida para activos de medios.

| Componente | Rol |
|---|---|
| `storage.py` | Persistencia/lectura de activos |
| `metadata.py`, `paths.py` | Metadatos y rutas canónicas de activos |
| `exceptions.py` | Excepciones de la capa |

- **Estado:** IMPLEMENTADO.
- **Dependencias:** `config`.

### `src/image`

Generación de imágenes de escena.

| Componente | Rol |
|---|---|
| `providers/gemini.py` | Provider Imagen de Gemini |
| `providers/stability.py` | Provider Stability |
| `providers/synthetic.py` | Provider sintético determinista (offline) |
| `adaptador` | Contrato + dispatch/fallback |
| `exceptions.py` | Excepciones |

- **Estado:** IMPLEMENTADO.
- **Dependencias:** `media`, `config`.
- Nota: fallback controlado (máx. uno por run); `synthetic` solo explícito.

### `src/audio`

Generación de narración.

| Componente | Rol |
|---|---|
| `providers/gemini.py` | Provider TTS de Gemini |
| `providers/synthetic.py` | Provider sintético (WAV PCM offline) |
| `adaptador` | Contrato + dispatch |
| `exceptions.py` | Excepciones |

- **Estado:** IMPLEMENTADO.
- **Dependencias:** `media`, `config`.
- Nota: `AudioResult` expone `text`, `content`, `metadata`, `model` (sin campo
  `provider`). Sin fallback de audio.

### `src/project`

Project Manifest: agregado que representa el proyecto a renderizar.

| Componente | Rol |
|---|---|
| `ProjectManifest` (agregado) | Activos detectados + contenido + metadatos |
| `serializer`, `validator` | (De)serialización y validación de `project.json` |
| `exceptions.py` | Excepciones |

- **Estado:** IMPLEMENTADO.
- **Dependencias:** `content`, `media`.

### `src/pipeline`

Orquestador end-to-end del pipeline (ME27): ejecuta las seis etapas
(contenido → imagen → audio → manifest → render → quality) dentro de un run
aislado.

| Componente | Rol |
|---|---|
| `context.py` | `RunContext` (creación/validación de `run_id`, preparación de `output/runs/<run_id>/{input,output}`) |
| `runner.py` | `PipelineRunner`: importa las funciones internas de `scripts/*` y las ejecuta en orden; se detiene ante el primer fallo |
| `models.py` | `PipelineResult` / `StageResult` (timestamps, éxito, error); expone `video_path` del render y `quality_result` |
| `exceptions.py` | Jerarquía de excepciones (p. ej. `PipelineValidationError`) |

- **Estado:** IMPLEMENTADO.
- **Dependencias:** `scripts/*` (funciones `*_to_path`), `quality`, `config`.
- Nota: los scripts conservan su CLI individual; `run_pipeline.py` es el CLI
  de este runner.

### `src/quality`

Quality Gate del pipeline (ME27/ME28): etapa **read-only** que valida el run
tras el render.

| Componente | Rol |
|---|---|
| `gate.py` | `QualityGate`: orquesta los checks (content/manifest, imágenes, audio/video decodificables, duración y sync A/V, provenance, residuos) y emite el veredicto |
| `models.py` | `QualityResult` / `QualityCheck` (severidad `error`/`warning`/`info`, estado PASS/WARN/FAIL) |
| `exceptions.py` | Excepciones de la capa |

- **Estado:** IMPLEMENTADO.
- **Dependencias:** `content`, `project`, `media` (usa ffprobe vía
  `src/renderer`).
- Nota: nunca modifica ni borra artefactos. Un FAIL deja `success=False` y exit
  code 1, pero conserva el run (incluido `video_path`) para diagnóstico.

### `src/renderer`

Render FFmpeg: el manifest es la única fuente de verdad.

| Componente | Rol |
|---|---|
| `ffmpeg.py` | Builder de comandos FFmpeg |
| `commands.py` | Adaptador manifest → comandos/request |
| `executor` | Ejecución vía subprocess (ffmpeg/ffprobe) |

- **Estado:** IMPLEMENTADO.
- **Dependencias:** `project`, `media`.
- Nota: requiere FFmpeg + ffprobe en el PATH.

### `src/video`

Abstracción planeada de composición de video (Timeline, VideoAdapter).

- **Estado:** PENDIENTE — esqueleto planeado, **no cableado al pipeline**
  (nada lo importa).
- **Dependencias:** —.

## Directorios de apoyo

| Directorio | Rol | Estado |
|---|---|---|
| `scripts/` | Etapas del pipeline (`generate_content`, `generate_image`, `generate_audio`, `generate_manifest`, `render_video`, `quality_gate`, `run_pipeline`, `test_gemini`) | IMPLEMENTADO |
| `config/` | Configuración base: `project.json`, `providers.json`, `providers.example.json`, `settings.json`, `youtube.json`, `content-schema.json` | Parcial (algunos archivos desactualizados vs. implementación) |
| `prompts/`, `workflows/` | Scaffolding previsto de contenido LLM y orquestación | VACÍO (sin código) |
| `output/` | Salidas de cada etapa del pipeline | Generado (gitignored) |

Nota: `src/prompt_engine` sustituye funcionalmente al scaffolding de
`prompts/`; la orquestación vive hoy en `src/pipeline` + `scripts/`
(a la espera de `workflows/`).

## Secciones principales

- [Visión general del sistema](SYSTEM_OVERVIEW.md) — contexto del proyecto.
- [Flujo de datos](DATA_FLOW.md) — cómo se relacionan las áreas entre sí.

## Pendientes (TODO)

- [ ] Implementar análisis de Shorts (nuevo dominio + providers).
- [ ] Materializar orquestación en `workflows/` (hoy en `scripts/`).
- [ ] Cablear o descartar `src/video`.
- [ ] Añadir pruebas y tooling de calidad por módulo.
- [ ] Mantener este documento alineado con el código.