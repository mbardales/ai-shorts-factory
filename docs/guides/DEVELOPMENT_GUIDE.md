# DEVELOPMENT_GUIDE.md

## Propósito

Establecer la guía de desarrollo del proyecto **AI Shorts Factory**: estado del
entorno, comandos disponibles, convenciones a seguir y cómo ejecutar el
pipeline completo. Refleja el estado real del código en la rama
`feature/project-manifest`.

## Estado

- **Etapa:** Implementación — pipeline de generación funcional end-to-end.
- **Stack:** Python 3.14; dependencias en `scripts/requirements.txt`
  (`google-genai`, `python-dotenv`), instaladas en el `.venv` del repo.
- **Requisitos externos:** FFmpeg + ffprobe en el PATH para la etapa de render.
- **Última actualización:** 2026-08-11.

## Entorno

- Python del repo: `.venv\Scripts\python.exe`.
- Se requiere un `.env` raíz con `GEMINI_API_KEY` (ver
  `scripts/.env.example`). `config.load_project_env()` resuelve el `.env`
  desde la ruta del módulo (no desde CWD); llámalo antes de construir cualquier
  provider.
- `src/` no es un paquete instalable; los scripts se auto-bootstrapan con
  `sys.path.insert(0, src)`. No usar imports desde `src/` directamente fuera de
  ese mecanismo.
- `.env`, `.venv/` y `output/` están en `.gitignore`; nunca versionarlos.

## Comandos

No existen comandos de build/lint/typecheck/tests (no hay `pyproject.toml` ni
framework de tests). No inventarlos ni ejecutarlos. `scripts/test_gemini.py` es
un smoke test de API en vivo, no un test unitario.

### Etapas del pipeline (en orden estricto)

```powershell
.venv\Scripts\python.exe scripts/generate_content.py   # pide un tema → output/content.json
.venv\Scripts\python.exe scripts/generate_image.py     # → output/images/scene_*.png + providers.json
.venv\Scripts\python.exe scripts/generate_audio.py     # → output/audio/narration.mp3 | narration.wav
.venv\Scripts\python.exe scripts/generate_manifest.py  # → output/project.json
.venv\Scripts\python.exe scripts/render_video.py       # → output/video/*.mp4  (exit codes 0–4 en su docstring)
```

Cada etapa consume la salida de la anterior; no invertir el orden.

### Etapas 1–3 juntas

```powershell
.venv\Scripts\python.exe scripts/run_pipeline.py "<tema>"
# o con la variable PIPELINE_TOPIC
```

### Pipeline offline (sin APIs)

Usa los providers sintéticos para imagen y audio, más FFmpeg para el render:

```powershell
$env:GEMINI_IMAGE_PROVIDER = "synthetic"
$env:GEMINI_AUDIO_PROVIDER = "synthetic"
.venv\Scripts\python.exe scripts/generate_image.py
.venv\Scripts\python.exe scripts/generate_audio.py
.venv\Scripts\python.exe scripts/generate_manifest.py
.venv\Scripts\python.exe scripts/render_video.py
```

No requiere API key ni HTTP; el camino offline está validado end-to-end.
Nota: `GEMINI_IMAGE_PROVIDER=synthetic` debe fijarse explícitamente; el fallback
nunca incluye al provider sintético automáticamente.

## Providers y configuración

| Área | Providers | Selección |
|---|---|---|
| Texto | `gemini` | `GEMINI_MODEL` (default `gemini-3.6-flash`) |
| Imágenes | `gemini`, `stability`, `synthetic` | `GEMINI_IMAGE_PROVIDER`; fallback `GEMINI_IMAGE_FALLBACK_PROVIDER` (máx. uno por run); `GEMINI_IMAGE_MODEL`, `STABILITY_API_KEY` |
| Audio | `gemini`, `synthetic` | `GEMINI_AUDIO_PROVIDER`; `GEMINI_TTS_MODEL`, `GEMINI_TTS_VOICE` |
| Tema | — | `PIPELINE_TOPIC` (para `run_pipeline.py`) |

Ver `scripts/.env.example` para la documentación canónica de variables.

## Convenciones

- Código y docstrings en **español**; PEP 8.
- `from __future__ import annotations` en módulos.
- Value objects como dataclasses frozen.
- `logging.getLogger(__name__)` en `src/` (nunca `print()`).
- Jerarquías de excepciones tipadas por paquete.
- Imports lazy de SDKs opcionales.
- UTF-8 en todo (`ensure_ascii=False`).
- El dominio puro (`src/content`, `src/project`) no debe depender de AI/infra.
- Commits convencionales en minúscula (ver [GIT_WORKFLOW.md](GIT_WORKFLOW.md)).

## Estructura

- `src/` — paquetes de código (ver [MODULES.md](../architecture/MODULES.md)).
- `scripts/` — etapas del pipeline.
- `config/` — configuración base.
- `prompts/`, `workflows/` — scaffolding aún vacío; `src/prompt_engine`
  sustituye funcionalmente a `prompts/`.
- `output/` — salidas generadas (gitignored).

## Documentación

- `docs/PROJECT_STATE.md` — estado del proyecto; mantener al día.
- La planificación vive en `docs/roadmap/` (`MVP.md`, `BACKLOG.md`).
- Las decisiones de arquitectura se registran en `docs/decisions/` como ADR.
- `AI_CONTEXT.md` (raíz) — contexto técnico operativo para agentes.

## Pendientes (TODO)

- [ ] Implementar análisis de Shorts y publicación a YouTube.
- [ ] Orquestación completa en `workflows/`.
- [ ] Definir tooling de calidad (lint/typecheck/tests) y registrar aquí los comandos.
- [ ] Cablear o descartar `src/video`.
- [ ] Actualizar esta guía conforme el proyecto evolucione.