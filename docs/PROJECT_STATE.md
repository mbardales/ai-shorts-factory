# PROJECT_STATE.md

## Propósito

Consolidar el estado actual del proyecto **AI Shorts Factory** en un único
documento de referencia, para orientar cualquier sesión de trabajo o agente que
ingrese al repositorio. **Refleja el estado real del código** en la rama
`feature/project-manifest`.

## Estado

- **Etapa del proyecto:** Implementación — pipeline funcional end-to-end.
- **Última actualización:** 2026-08-13.
- **Histórico de cambios:** ver `git log`.

## Descripción

### Qué es el proyecto

Generador de YouTube Shorts asistido por IA ("AI Shorts Factory"). El flujo de
**generación** está implementado y funcional: un tema entra y se produce un
Short completo (guion, imágenes, narración, manifest y video renderizado). El
flujo de **análisis** de Shorts existentes sigue pendiente.

### Estado del código

- **Implementado y funcional:** pipeline de generación completo
  (`src/` + `scripts/`), desde contenido hasta render FFmpeg.
- **Módulos implementados:** `content`, `prompt_engine`, `ai`, `media`,
  `image`, `audio`, `project`, `renderer`, `quality`, `config`.
- **Pendiente:** módulo `video` (abstracción planeada, no cableada),
  análisis de Shorts, publicación a YouTube, orquestación en `workflows/`,
  herramientas de calidad (lint/typecheck/tests).
- **Rama de desarrollo:** `feature/project-manifest` (adelantada a la rama
  principal). `main` ya no refleja el estado real del código.
- **Sin packaging** (`pyproject.toml`/`setup.py`), sin framework de tests, sin
  config de lint/typecheck — por decisión, no por omisión.

### Estructura vigente

```
config/    Configuración base (project.json, providers.json, settings.json, youtube.json, content-schema.json)
docs/      Arquitectura, guías, roadmap, decisiones
prompts/   Scaffolding vacío
workflows/ Scaffolding vacío
scripts/   Etapas del pipeline (generate_*, render_video, run_pipeline, test_gemini)
src/       Paquetes de código (ai, audio, config, content, image, media, project, prompt_engine, renderer, video)
output/    Salidas de cada etapa (gitignored)
```

Nota: el antiguo área `assets/` se materializó como `src/media`; `infra/` no
existe en el repositorio. La estructura inicial por áreas funcionales quedó
reemplazada por la estructura modular de `src/`.

### Pipeline implementado

```
generate_content.py  →  output/content.json
generate_image.py    →  output/images/scene_*.png + providers.json
generate_audio.py    →  output/audio/narration.mp3 | narration.wav
generate_manifest.py →  output/project.json
render_video.py      →  output/video/*.mp4  (FFmpeg)
quality_gate.py      →  veredicto PASS / WARN / FAIL (read-only)
```

`run_pipeline.py` orquesta las seis etapas (contenido → imagen → audio →
manifest → render → quality) dentro de un run aislado `output/runs/<run_id>/`
mediante `src/pipeline` (PipelineRunner + RunContext), deteniéndose ante el
primer fallo.

Tras el render, el **Quality Gate** (`src/quality`, read-only) inspecciona el
run: content/manifest válidos, una imagen por escena, audio y video
decodificables, duración y sync A/V, provenance y residuos. Un check fallido
de severidad `error` produce **FAIL** (pipeline `success=False`, exit 1);
desviaciones no críticas producen **WARN** y el run sigue siendo **PASS**. El
gate no modifica ni borra nada: ante un FAIL se conservan todos los artefactos
del run (incluido el video renderizado) para diagnóstico.

### Providers

| Área | Providers | Selección |
|---|---|---|
| Texto | `gemini` | `GEMINI_CONTENT_PROVIDER` (default `gemini`); `GEMINI_MODEL` (default `gemini-3.6-flash`) |
| Imágenes | `gemini` \| `stability` \| `synthetic` | `GEMINI_IMAGE_PROVIDER`; fallback opcional `GEMINI_IMAGE_FALLBACK_PROVIDER` |
| Audio | `gemini` \| `synthetic` | `GEMINI_AUDIO_PROVIDER` |

- Los providers se seleccionan **mediante variables de entorno** (documentación
  canónica en `scripts/.env.example`); un valor vacío usa el predeterminado del
  código.
- `synthetic` es el provider **offline** (determinista, sin API key ni HTTP);
  `stability` es la **alternativa real** de imagen (requiere `STABILITY_API_KEY`).
- `config/providers.json` es **legacy**: contiene placeholders y el pipeline
  actual no lo utiliza para seleccionar providers.
- El pipeline completo funciona **offline y determinista** con
  `synthetic` (imagen + audio) + FFmpeg. Validado end-to-end.
- `SyntheticImageProvider` nunca se activa automáticamente como fallback; el
  fallback permite un único cambio por run.
- El Quality Gate solo valida un run existente; no genera ni modifica activos.

### Documentación

| Área | Documentos |
|---|---|
| Arquitectura | [SYSTEM_OVERVIEW.md](architecture/SYSTEM_OVERVIEW.md), [MODULES.md](architecture/MODULES.md), [DATA_FLOW.md](architecture/DATA_FLOW.md) |
| Guías | [DEVELOPMENT_GUIDE.md](guides/DEVELOPMENT_GUIDE.md), [GIT_WORKFLOW.md](guides/GIT_WORKFLOW.md) |
| Roadmap | [MVP.md](roadmap/MVP.md), [BACKLOG.md](roadmap/BACKLOG.md) |
| Decisiones | [ADR-001-Architecture.md](decisions/ADR-001-Architecture.md) |
| Estado | Este documento |

### Decisiones tomadas

- **ADR-001:** organizar el repositorio por áreas funcionales (prompts,
  workflows, assets, configuración, documentación). Con la llegada de código
  real, la estructura efectiva evolucionó a paquetes modulares en `src/`; el
  espíritu del ADR (separar áreas y responsabilidades) se mantiene.
- **Dominio puro:** `src/content` y `src/project` no dependen de IA/infra.
- **Provenance de activos:** los activos generados registran el provider/model
  usado (`providers.json`, escrito atómicamente por `generate_image.py`).
- **Manifest como fuente de verdad:** `output/project.json` gobierna el render
  FFmpeg.
- **Quality Gate read-only:** `src/quality` inspecciona el run tras el render
  sin modificarlo; ante un FAIL se conservan los artefactos para diagnóstico.
- **Offline-first:** providers sintéticos para imagen y audio, sin API keys.

### Limitaciones conocidas

- La clave Gemini actual tiene **cero cuota de generación de imágenes**
  (`RESOURCE_EXHAUSTED` en todos los modelos Imagen). Se mitiga con Stability
  o el modo sintético. Transitoria (depende de la cuenta, no del código).
- FFmpeg + ffprobe deben estar en el PATH para la etapa de render.
- `config/project.json` (campo `phase`) quedó desactualizado respecto a la
  implementación real.

### Trabajo en curso / próximos pasos

1. Análisis de Shorts existentes (dominio + providers).
2. Publicación a YouTube (OAuth, API).
3. Orquestación del flujo completo en `workflows/`.
4. Pruebas y calidad (tooling, unit tests del dominio puro).
5. Cablear o descartar `src/video`.

## Secciones principales

- [Qué es el proyecto](#que-es-el-proyecto)
- [Estado del código](#estado-del-codigo)
- [Estructura vigente](#estructura-vigente)
- [Pipeline implementado](#pipeline-implementado)
- [Providers](#providers)
- [Documentación](#documentacion)
- [Decisiones tomadas](#decisiones-tomadas)
- [Limitaciones conocidas](#limitaciones-conocidas)
- [Trabajo en curso / próximos pasos](#trabajo-en-curso--proximos-pasos)

## Pendientes (TODO)

- [ ] Mantener sincronizado este documento con [BACKLOG.md](roadmap/BACKLOG.md) y [MVP.md](roadmap/MVP.md).
- [ ] Actualizar este documento al avanzar cada área pendiente (análisis, publicación, orquestación).
- [ ] Actualizar `config/project.json` (campo `phase`) cuando se retome la configuración del proyecto.
- [ ] Revisar periódicamente el estado de las limitaciones conocidas (cuota de imagen).
