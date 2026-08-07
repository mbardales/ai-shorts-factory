# AI Shorts Factory

> Documento de contexto técnico para asistentes de IA (OpenCode, ChatGPT, etc.).
> **Leer antes de iniciar cualquier tarea.** Describe visión, arquitectura,
> convenciones y estado real del proyecto. No describe funcionalidades
> inexistentes; solo lo que existe en el repositorio.

## 1. Visión del proyecto

AI Shorts Factory es un **analizador y generador de YouTube Shorts** asistido por
inteligencia artificial.

- **Análisis (flujo previsto):** estudiar un Short de YouTube y producir un
  resultado estructurado (métricas, descripción, tags, contenido del video).
- **Generación (flujo implementado):** a partir de un tema o idea simple,
  generar el contenido completo de un Short: identidad, investigación, SEO,
  guion, escenas, narración y estado del ciclo de vida.

El sistema se construye en Python y delega la generación de texto a un LLM
(actualmente Google Gemini vía el SDK oficial `google-genai`). El código se
organiza en módulos puros de dominio (sin acoplar a IA o infraestructura) y
capas de infraestructura reutilizables.

## 2. Objetivos del MVP

Según `config/project.json` y `docs/roadmap/MVP.md`:

1. **Análisis:** analizar un Short de YouTube y producir un resultado
   estructurado (pendiente de implementar).
2. **Generación:** generar un Short a partir de una idea simple. **Implementado**
   en `scripts/generate_content.py` (tema → `output/content.json`).
3. **Reproducibilidad:** proceso reproducible con stack definido y comandos
   claros.

**Alcance de la generación hoy:** el pipeline produce el *Content Package*
completo (guion, escenas, narración, SEO). No se genera todavía imagen, audio,
video renderizado ni publicación en YouTube.

## 3. Arquitectura general

Código fuente en `src/`, organizado en paquetes independientes entre sí.

### AI Core — `src/ai/`

Abstracción de acceso a proveedores LLM.

- `base.py`: `BaseAIProvider` (contrato `generate()`), `GenerationOptions`,
  `GenerationResult` (incluye `parsed` para salida estructurada).
- `exceptions.py`: jerarquía `AIError` → `ConfigurationError`, `ProviderError`
  (`APIError`, `AuthenticationError`, `RateLimitError`, `ContentBlockedError`).
- `adapter.py`: `AIAdapter` (orquesta `generate_text` y `generate_structured`),
  `ProviderSettings`, `from_config()`.
- `providers/gemini.py`: `GeminiProvider` sobre `google-genai`. Lee
  `GEMINI_API_KEY` (entorno o `.env`) y `GEMINI_MODEL` (default
  `gemini-3.6-flash`). Import diferido del SDK. Soporta Structured Output
  (`response_schema` + `response_mime_type=application/json`) y expone
  `response.parsed`.

### Prompt Engine — `src/prompt_engine/`

Composición de prompts, desacoplado de IA y dominio.

- `templates.py`: `JSON_SCHEMA` (estructura JSON de ejemplo), `PROMPT_TEMPLATE`,
  `RULES`.
- `builder.py`: `build_prompt(topic, *, schema=None, rules=None)` (serializa con
  `ensure_ascii=False`).

### Content Package — `src/content/`

Modelo de dominio puro del contenido (sin IA ni infraestructura).

- `models.py`: agregado `ContentPackage` con 10 objetos de valor: `Identity`,
  `Research`, `SEO`, `Script`, `Scene`, `Visuals`, `Narration`, `Assets`,
  `Publication`, `Analytics`, `Status`. Enum `ContentStage`:
  `generated → research → seo → script → scenes → narration → assets → video → published`.
- `validator.py`: `find_errors`, `validate_content_package`, `transition` y
  `coerce_content_package` (reconstrucción robusta que rellena defaults).
  Límites: título ≤ 100, descripción ≤ 5000, tags ≤ 30.
- `schema.py`: serialización a/desde dict y JSON, y `CONTENT_PACKAGE_SCHEMA`
  (JSON Schema para Structured Output). El esquema incluye solo
  identity/research/seo/script/visuals/narration/status; `assets`,
  `publication` y `analytics` se inicializan en `null` porque en la generación
  aún no representan información real.

### Media Core — `src/media/`

Infraestructura compartida para activos multimedia (imagen, audio, video).
Los módulos `image/`, `audio/` y `video/` **aún no existen**.

- `base.py`: enum `MediaKind` (IMAGE/AUDIO/VIDEO) y `MEDIA_EXTENSIONS`.
- `metadata.py`: `MediaMetadata` (base) + `ImageMetadata`, `AudioMetadata`,
  `VideoMetadata` y factory `metadata_from_dict()`.
- `paths.py`: nomenclatura `<content_id>-<nombre>.<ext>`, rutas
  `assets/<kind>/<content_id>/…`, `PROJECT_ROOT`, `ASSETS_ROOT`,
  `ensure_safe_relative` (anti path-traversal), validación de extensiones.
- `storage.py`: `Storage` (ABC) y `LocalStorage`.
- `exceptions.py`: jerarquía `MediaError` (Configuration, NotFound, Validation,
  Unsupported) y `StorageError` (Read, Write, Exists, NotFound).

### Scripts — `scripts/`

- `generate_content.py`: flujo del MVP (ver sección 4). Pide un tema por
  consola y escribe `output/content.json` en UTF-8.
- `test_gemini.py`: cliente mínimo de prueba del Gemini API (usa
  `python-dotenv`).
- `.env.example`, `requirements.txt` (`google-genai`, `python-dotenv`).

## 4. Flujo del sistema

Flujo real del generador y flujo previsto del pipeline completo:

```
Tema
 ↓
Prompt (Prompt Engine → build_prompt)
 ↓
Gemini (AI Core → GeminiProvider)
 ↓
Structured Output (response_schema = CONTENT_PACKAGE_SCHEMA; fallback: texto + extract_json)
 ↓
Content Package (validator → coerce_content_package; assets/publication/analytics en null)
 ↓
output/content.json (UTF-8)
 ↓
Media (Media Core — por construir)
 ↓
Video (render — por construir)
 ↓
Publicación (YouTube — por construir)
```

Notas del flujo actual (`scripts/generate_content.py`):

1. Tema por consola (no vacío).
2. `build_prompt(topic)` + `AIAdapter.generate_structured(...,
   CONTENT_PACKAGE_SCHEMA)`. Si el proveedor no devuelve `parsed`, se cae a
   `generate_text` + `extract_json`.
3. Se descartan `assets`, `publication` y `analytics` de la respuesta y se
   reconstruye el `ContentPackage` con `coerce_content_package` (los campos
   quedan en `null`).
4. Persistencia en `output/content.json`.

## 5. Estructura del repositorio

| Directorio | Contenido |
|---|---|
| `src/` | Código fuente: `ai/`, `content/`, `prompt_engine/`, `media/` |
| `scripts/` | Utilidades y automatización (`generate_content.py`, `test_gemini.py`) |
| `prompts/` | Contenido para LLM: `chains/`, `system/`, `templates/`, `user/` (vacío) |
| `workflows/` | Orquestación: `core/`, `shared/`, `templates/`, `testing/` (vacío) |
| `assets/` | Multimedia: `audio/`, `music/`, `branding/`, `fonts/` (solo `.gitkeep`) |
| `config/` | Configuración: `project.json`, `settings.json`, `content-schema.json`, `providers.json`, `youtube.json` |
| `docs/` | `PROJECT_STATE.md`, `architecture/`, `decisions/`, `guides/`, `roadmap/` |
| `infra/` | n8n + PostgreSQL (Docker Compose local, Dockerfile para Northflank) |
| `examples/`, `backups/`, `logs/` | Soporte (vacíos, con `.gitkeep`) |

Archivos raíz: `README.md`, `AGENTS.md`, `LICENSE`, `.gitignore`, `.env`
(no versionado), `AI_CONTEXT.md` (este documento).

## 6. Convenciones de código

- **PEP 8**; docstrings y nombres descriptivos en español; `from __future__ import annotations`.
- **Type hints** completos en firmas públicas (`Optional`, `Union`, `Any`).
- **Dataclasses `frozen=True`** para objetos de valor y metadatos.
- **Logging** con `logging.getLogger(__name__)`; **prohibido `print()`** en
  módulos de `src/` y scripts de producción.
- **Errores tipados**: jerarquías de excepciones propias por paquete
  (`AIError`, `MediaError`, `ContentValidationError`); nunca capturas silenciosas.
- **Import diferido** de dependencias opcionales (SDK `google-genai`) para no
  acoplar imports.
- **Serialización UTF-8** (acentos/emojis intactos; `ensure_ascii=False`).
- **Modelo de dominio puro**: `content/` no conoce IA ni infraestructura.
- Nuevos archivos se ubican en el área correspondiente (ver `AGENTS.md`).

## 7. Git Workflow

Flujo vigente (según `docs/guides/GIT_WORKFLOW.md` y la historia real):

```
feature/* (rama de trabajo)
    ↓
commit (estilo conventional, minúsculas)
    ↓
push (origin/feature/*)
    ↓
merge (a main, mediante merge commit o revisión)
    ↓
main
```

- **Commits:** `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `style:`,
  `test:` — mensajes breves, en minúsculas.
- **Ramas por funcionalidad:** `feature/<descripcion>` (ej. `feature/media-core`,
  `feature/structured-output`). Trabajar en `main` solo para cambios triviales.
- **Qué incluir:** solo archivos intencionados; **nunca secretos** (`.env` está
  en `.gitignore`); fuera de commits los artefactos de build y `output/`.
- Historia actual: `main` con merges de `feature/gemini-client`,
  `feature/structured-output`, etc.

## 8. Estado actual del proyecto

### Módulos terminados

- **AI Core** (`src/ai/`): contrato base, adaptador, excepciones y proveedor
  Gemini con Structured Output (`parsed`). Validado con ejecuciones reales.
- **Prompt Engine** (`src/prompt_engine/`): `build_prompt` con plantillas y
  reglas.
- **Content Package** (`src/content/`): modelos, serialización, validator y
  `CONTENT_PACKAGE_SCHEMA`. Validado (roundtrips, transiciones, coacción).
- **Media Core** (`src/media/`): infraestructura (kinds, metadatos, rutas,
  nomenclatura, storage, excepciones). Validado con 32 checks.
- **Generador MVP** (`scripts/generate_content.py`): tema →
  `output/content.json` con los 10 objetos de valor (Structured Output).
- **Config y docs**: `config/*.json`, `infra/` (n8n local + Northflank),
  `docs/architecture`, `docs/roadmap`, ADR-001.
- `.gitignore`, `.env` local (no versionado), entorno `.venv` (Python 3.14,
  `google-genai` instalado).

### Módulos pendientes

- `src/media/image/`, `audio/`, `video/` (consumir Media Core).
- Render del video (composición de escenas, narración, música).
- Flujo de **análisis** de Shorts (YouTube Data API v3; config en
  `youtube.json`).
- Publicación en YouTube (OAuth, subida; config prevista en `youtube.json`).
- Orquestación con `workflows/` y `prompts/` reales.
- Herramientas de calidad: lint, formatter, typecheck y tests (sin configurar).
- `docs/PROJECT_STATE.md` está creado pero vacío.

## 9. Roadmap

Próximos EPICs (derivados de `docs/roadmap/MVP.md` y `BACKLOG.md`):

1. **Media builders**: módulos `image/`, `audio/`, `video/` sobre Media Core
   (metadatos + storage + naming) sin duplicar código.
2. **Pipeline de generación media**: guion → escenas → narración → activos →
   video renderizado (Short ≤ 60 s, 1080×1920, H.264/AAC según `youtube.json`).
3. **Analizador de Shorts**: entrada de URL/video_id → resultado estructurado
   (YouTube Data API v3).
4. **Publicación**: subida a YouTube con OAuth y estados de privacidad.
5. **Orquestación y calidad**: workflows con `workflows/`, prompts en `prompts/`,
   lint/typecheck/tests, política de PR y protección de `main`.

## 10. Definition of Done

Una tarea se considera **terminada** cuando:

1. Solo toca los archivos del alcance indicado (no modifica módulos fuera de él;
   no crea archivos/carpetas no solicitados).
2. No inventa funcionalidad inexistente ni dependencias nuevas no justificadas.
3. El código sigue las convenciones de la sección 6 (type hints, dataclasses,
   logging, excepciones tipadas, UTF-8).
4. Compila y se valida con el entorno real `.venv`
   (`D:\Proyectos\ai-shorts-factory\.venv\Scripts\python.exe`); cuando aplica,
   se ejecuta el flujo real y se verifica su salida.
5. No deja secretos en el repositorio; `git status --short` solo muestra los
   cambios esperados.
6. Se verifica que la integración con Gemini devuelve un objeto estructurado
   (`response.parsed`) siempre que el SDK lo permita.


## 11. Principios de arquitectura

- Mantener bajo acoplamiento entre módulos.
- Cada módulo debe tener una única responsabilidad.
- El dominio nunca depende de infraestructura.
- Toda integración externa debe implementarse mediante adaptadores.
- Evitar lógica de negocio dentro de scripts.
- Todo flujo debe ser reutilizable desde otros módulos.
- Preferir composición sobre herencia.
- El sistema debe poder sustituir Gemini por otro proveedor sin modificar el dominio.
