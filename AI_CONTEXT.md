# AI_CONTEXT — Contexto técnico del proyecto

Contexto operativo para herramientas de IA y personas que trabajen sobre este
repositorio. **Refleja el estado real del código en la rama
`feature/project-manifest`.** Si este documento contradice al código, el código
manda.

## Propósito del proyecto

"AI Shorts Factory": generar YouTube Shorts de forma asistida por IA desde un
solo tema. El pipeline completo (contenido → imágenes → audio → manifest →
render FFmpeg) está **implementado y funcional**.

## Stack

- **Python 3.14**, sin packaging (`src/` no es un paquete instalable).
- Dependencias mínimas: `google-genai`, `python-dotenv`
  (`scripts/requirements.txt`), instaladas en `.venv`.
- **FFmpeg + ffprobe** requeridos en el PATH para la etapa de render.
- No hay framework de tests, ni config de lint/typecheck.

## Estructura de directorios (real)

```
config/    Configuración: project.json, providers.json, providers.example.json,
           settings.json, youtube.json, content-schema.json
docs/      Arquitectura, guías, roadmap, decisiones
prompts/   Scaffolding vacío
workflows/ Scaffolding vacío
scripts/   Etapas del pipeline + helpers
src/       Código (ver módulos abajo)
output/    Salidas de cada etapa (gitignored)
```

No existe `infra/`; `docs/decisions/ADR-001` documenta la arquitectura
modular, y `config/project.json` quedó obsoleto respecto al estado real del
código (ver "Estado").

## Pipeline (implementado)

```
generate_content.py  →  output/content.json
generate_image.py    →  output/images/scene_*.png + providers.json
generate_audio.py    →  output/audio/narration.mp3 | narration.wav
generate_manifest.py →  output/project.json
render_video.py      →  output/video/*.mp4  (FFmpeg)
```

- Etapas en orden estricto; cada una consume la salida de la anterior.
- `run_pipeline.py` orquesta contenido + imagen + audio.
- `test_gemini.py` es un smoke test de API en vivo, no un test unitario.

## Módulos de `src/` (estado real)

| Módulo | Responsabilidad | Estado |
|---|---|---|
| `src/content` | Modelo de dominio Content Package (agregado, validator, `CONTENT_PACKAGE_SCHEMA`) | **Implementado** |
| `src/prompt_engine` | Composición de prompts (`builder.build_prompt`, plantillas) | **Implementado** |
| `src/ai` | Abstracción LLM (Gemini provider + adapter) | **Implementado** |
| `src/media` | Storage/metadata/paths compartidos para activos | **Implementado** |
| `src/image` | Generación de imágenes: providers + adapter + fallback | **Implementado** |
| `src/audio` | Generación de narración: providers + adapter | **Implementado** |
| `src/project` | Project Manifest (agregado, serializer, validator) | **Implementado** |
| `src/renderer` | Builder + executor de comandos FFmpeg | **Implementado** |
| `src/video` | Abstracción planeada de video (Timeline, VideoAdapter). **No cableada**; nada la importa | **Pendiente / no usado** |
| `src/config` | Carga centralizada de `.env` | **Implementado** |

## Providers

### Texto (LLM)
- Proveedor: `gemini` (único). `src/ai/providers/gemini.py` + `adapter.py`.
- `GEMINI_MODEL` (default `gemini-3.6-flash`).

### Imágenes
- `GEMINI_IMAGE_PROVIDER`: `gemini` (default) | `stability` | `synthetic`.
- `SyntheticImageProvider` = opción **determinista y offline** (sin API key,
  sin HTTP). Se selecciona solo de forma explícita; **nunca** se auto-incluye
  como fallback.
- Fallback: `GEMINI_IMAGE_FALLBACK_PROVIDER` permite **un** cambio por run
  (nunca encadena sintético). Máximo un fallback; si también falla, el run
  falla.
- `GEMINI_IMAGE_MODEL` y `STABILITY_API_KEY` para los providers remotos.
- El pipeline con proveedor `synthetic` está validado end-to-end (sin costo,
  offline).

### Audio
- `GEMINI_AUDIO_PROVIDER`: `gemini` (default) | `synthetic`.
- `synthetic` = WAV PCM offline, sin API key, sin HTTP. No hay fallback de
  audio.
- `AudioResult` NO tiene campo `provider` (contrato real: `text`, `content`,
  `metadata`, `model`).

### Config / entorno
- `config.load_project_env()` resuelve el `.env` raíz desde el módulo, no del
  CWD; hay que llamarlo antes de construir cualquier provider.
- `generate_manifest.py` y `render_video.py` NO lo llaman (no tocan providers).

## Variables de entorno

Ver `scripts/.env.example` para la documentación canónica. Principales:

| Variable | Default | Uso |
|---|---|---|
| `GEMINI_API_KEY` | — | Clave para LLM + proveedores remotos |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Modelo LLM de texto |
| `GEMINI_IMAGE_PROVIDER` | `gemini` | `gemini` \| `stability` \| `synthetic` |
| `GEMINI_IMAGE_MODEL` | — | Modelo de Imagen |
| `GEMINI_IMAGE_FALLBACK_PROVIDER` | — | Un fallback por run |
| `STABILITY_API_KEY` | — | Proveedor Stability |
| `GEMINI_AUDIO_PROVIDER` | `gemini` | `gemini` \| `synthetic` |
| `GEMINI_TTS_MODEL` | — | Modelo TTS |
| `GEMINI_TTS_VOICE` | — | Voz TTS |
| `PIPELINE_TOPIC` | — | Tema para `run_pipeline.py` |

## Comandos

- Python del repo: `.venv\Scripts\python.exe`.
- Etapas individuales: `scripts/generate_*.py` y `scripts/render_video.py`
  (este último con docstring que documenta exit codes 0–4).
- Todo junto (etapas 1–3): `python scripts/run_pipeline.py "<tema>"` (o env
  `PIPELINE_TOPIC`).

## Decisiones técnicas relevantes

- **Dominio puro:** `src/content` y `src/project` no dependen de IA/infra.
- **Provenance:** los activos generados guardan el provider/model usado
  (`providers.json` sidecar escrito atómicamente por `generate_image.py`).
- **Fallback controlado:** un único intento de fallback por run; el proveedor
  sintético nunca se activa automáticamente.
- **Manifest como fuente de verdad:** `output/project.json` gobierna el render
  FFmpeg (`src/renderer/ffmpeg.py`, `commands.py`, executor).

## Estado actual

- Pipeline **funcional end-to-end**.
- **Limitación conocida:** la clave Gemini actual tiene **cero cuota de
  generación de imágenes** (`RESOURCE_EXHAUSTED` en Imagen). Se mitiga con
  Stability o el modo sintético. Esta limitación es transitoria (depende de la
  cuenta), no del código.
- En desarrollo: rama `feature/project-manifest`.
- Pendiente de implementar: análisis de Shorts existentes, publicación a
  YouTube, orquestación en `workflows/`, calidad (lint/typecheck/tests),
  packaging.

## Próximas áreas de trabajo

1. Análisis de Shorts (dominio + providers, `src/analyzer`?).
2. Publicación a YouTube (OAuth, API).
3. Orquestación del flujo completo en `workflows/`.
4. Pruebas y calidad (tooling, unit tests del dominio).
5. Cablear o descartar `src/video`.
