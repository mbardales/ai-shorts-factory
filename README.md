# AI Shorts Factory

Generador de YouTube Shorts asistido por IA. A partir de un tema, el proyecto
produce un Short completo de forma reproducible: guion, escenas, narración y
video renderizado, usando proveedores de IA para texto, imagen y audio.

## Propósito

Convertir una idea simple en un Short de YouTube listo para publicar:

- **Generación de contenido:** tema → guion, investigación, SEO, escenas y
  narración (Google Gemini).
- **Generación de imágenes:** una imagen por escena (Gemini, Stability o modo
  sintético offline).
- **Generación de audio:** narración en voz (Gemini TTS o modo sintético
  offline).
- **Manifest de proyecto:** detección automática de activos y construcción de
  `output/project.json`, única fuente de verdad del renderizado.
- **Render:** composición de escenas + audio y codificación final con FFmpeg.

## Arquitectura resumida

El código vive en `src/`, organizado en paquetes independientes con dominio
puro (sin acoplar a IA o infraestructura) y capas de infraestructura
reutilizables:

| Paquete | Responsabilidad |
|---|---|
| `src/content` | Modelo de dominio "Content Package" (guion, escenas, narración, estado). |
| `src/prompt_engine` | Composición de prompts para el LLM. |
| `src/ai` | Abstracción de proveedores LLM (Gemini) con adaptador. |
| `src/media` | Infraestructura compartida de activos (metadatos, rutas, storage). |
| `src/image` | Generación de imágenes (providers + adaptador + fallback). |
| `src/audio` | Generación de narración (providers + adaptador). |
| `src/project` | Project Manifest (agregado, serializer, validator). |
| `src/renderer` | Construcción y ejecución de comandos FFmpeg. |
| `src/video` | Abstracción planeada de composición de video (no cableada al pipeline). |
| `src/config` | Carga centralizada del archivo `.env`. |

## Pipeline

```
generate_content.py  →  output/content.json
generate_image.py    →  output/images/scene_*.png + providers.json
generate_audio.py    →  output/audio/narration.mp3 | narration.wav
generate_manifest.py →  output/project.json
render_video.py      →  output/video/*.mp4  (FFmpeg)
```

Los scripts se ejecutan en orden estricto; cada etapa consume la salida de la
anterior. `run_pipeline.py` orquesta las etapas 1–3.

## Providers

| Área | Providers | Selección |
|---|---|---|
| Texto | `gemini` | `GEMINI_MODEL` (default `gemini-3.6-flash`) |
| Imágenes | `gemini`, `stability`, `synthetic` | `GEMINI_IMAGE_PROVIDER` (default `gemini`); fallback opcional `GEMINI_IMAGE_FALLBACK_PROVIDER` |
| Audio | `gemini`, `synthetic` | `GEMINI_AUDIO_PROVIDER` (default `gemini`) |

## Ejecución rápida

Requisitos: Python 3.14 (`scripts/requirements.txt`), un `.env` raíz con
`GEMINI_API_KEY`, y FFmpeg + ffprobe en el PATH.

```powershell
.venv\Scripts\python.exe scripts/generate_content.py   # pedirá un tema
.venv\Scripts\python.exe scripts/generate_image.py
.venv\Scripts\python.exe scripts/generate_audio.py
.venv\Scripts\python.exe scripts/generate_manifest.py
.venv\Scripts\python.exe scripts/render_video.py
```

O bien, etapas 1–3 juntas:

```powershell
.venv\Scripts\python.exe scripts/run_pipeline.py "Un eclipse solar total"
```

## Modo offline

Todo el pipeline puede ejecutarse sin APIs externas usando los providers
sintéticos:

```powershell
$env:GEMINI_IMAGE_PROVIDER = "synthetic"
$env:GEMINI_AUDIO_PROVIDER = "synthetic"
# + un content.json existente (o un LLM disponible)
```

`SyntheticImageProvider` genera PNG reales sin red ni API key;
`SyntheticAudioProvider` genera un WAV PCM determinista. El render final con
FFmpeg no depende de ningún proveedor externo. Este camino está validado
end-to-end.

## Estructura principal

```
config/    Configuración base (project.json, providers.json, settings.json, ...)
docs/      Documentación (arquitectura, guías, roadmap, decisiones)
prompts/   Scaffolding de contenido para LLM (vacío)
workflows/ Scaffolding de orquestación (vacío)
scripts/   Etapas del pipeline + helpers (generate_*, render_video, run_pipeline)
src/       Paquetes de código (ai, content, image, audio, media, project, renderer, video, config)
output/    Salidas generadas (gitignored)
```

## Estado del proyecto

- Pipeline de generación **funcional end-to-end** (contenido → imágenes →
  audio → manifest → render).
- Providers de texto, imagen y audio implementados, con modo sintético offline.
- En desarrollo sobre la rama `feature/project-manifest`.
- Pendiente: flujo de análisis de Shorts, publicación en YouTube,
  orquestación con `workflows/`, y herramientas de calidad (lint/tests).

Más detalle en `docs/PROJECT_STATE.md` y `AI_CONTEXT.md`.
