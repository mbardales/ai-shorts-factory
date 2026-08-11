# SYSTEM_OVERVIEW.md

## Propósito

Documentar la visión general del sistema **AI Shorts Factory**: su objetivo,
alcance, componentes implementados y la relación entre los principales paquetes
de `src/`. Sirve como punto de entrada para cualquier persona (o agente) que
necesite comprender el proyecto antes de trabajar en él.

## Estado

- **Etapa:** Implementación — pipeline de generación funcional end-to-end.
- **Código:** pipeline completo implementado en `src/` + `scripts/`.
- **Última actualización:** 2026-08-11.

## Descripción

El proyecto (ver `README.md`) es un **generador de YouTube Shorts asistido por
IA**. El flujo de **generación** está implementado: un tema se convierte en un
Short completo (contenido, imágenes, narración, manifest y video renderizado).
El flujo de **análisis** de Shorts existentes sigue pendiente.

La arquitectura separa **dominio puro** (sin dependencias de IA/infra) de
**infraestructura** reutilizable, y mantiene cada generador de activos como una
capa con sus propios providers y su adaptador.

## Arquitectura en capas

```
          Scripts (orquestación del pipeline)
                       │
   ┌───────────────────┼────────────────────┐
   │  Content Package  │  Project Manifest  │   ← dominio puro
   │    (src/content)  │    (src/project)   │
   └───────┬───────────┴─────────┬──────────┘
           │                     │
   ┌───────┴───────┐   ┌─────────┴─────────┐
   │ prompt_engine │   │      media        │   ← infraestructura compartida
   │   (prompts)   │   │  (storage, paths) │
   └───────┬───────┘   └───────────────────┘
           │
   ┌───────┴───────┐  ┌───────────┐  ┌───────────┐
   │   ai (LLM)    │  │ image     │  │ audio     │   ← proveedores + adaptador
   └───────────────┘  └───────────┘  └───────────┘
                                                      │
                                        ┌─────────────┘
                                        │
                                        └──► renderer (FFmpeg) ──► output/video/
```

## Componentes implementados

| Paquete | Responsabilidad | Depende de |
|---|---|---|
| `src/config` | Carga centralizada del `.env` | — |
| `src/content` | Modelo de dominio Content Package (agregado, validator, schema) | — (dominio puro) |
| `src/prompt_engine` | Composición de prompts para el LLM | `templates` (interno) |
| `src/ai` | Abstracción LLM (Gemini provider + adapter) | `content`, `config` |
| `src/media` | Storage/metadata/paths de activos | `config` |
| `src/image` | Generación de imágenes (providers + adaptador + fallback) | `media`, `config` |
| `src/audio` | Generación de narración (providers + adaptador) | `media`, `config` |
| `src/project` | Project Manifest (agregado, serializer, validator) | `content`, `media` |
| `src/renderer` | Builder y ejecutor de comandos FFmpeg | `project`, `media` |

## Flujo de datos del pipeline

```
generate_content.py
   tema ──► prompt_engine.build_prompt ──► ai (Gemini) ──► output/content.json
generate_image.py
   content.json ──► image providers ──► output/images/scene_*.png + providers.json
generate_audio.py
   content.json ──► audio providers   ──► output/audio/narration.mp3 | narration.wav
generate_manifest.py
   detecta activos ──► output/project.json   (fuente de verdad del render)
render_video.py
   project.json ──► renderer (FFmpeg) ──► output/video/*.mp4
```

Detalle del flujo de datos previsto y ampliado: [DATA_FLOW.md](DATA_FLOW.md).

## Providers

| Área | Providers | Selección |
|---|---|---|
| Texto | `gemini` | `GEMINI_MODEL` |
| Imágenes | `gemini`, `stability`, `synthetic` | `GEMINI_IMAGE_PROVIDER`; fallback `GEMINI_IMAGE_FALLBACK_PROVIDER` |
| Audio | `gemini`, `synthetic` | `GEMINI_AUDIO_PROVIDER` |

El pipeline completo es ejecutable **offline** con los providers `synthetic`
(imagen + audio) + FFmpeg, sin API keys ni HTTP.

## Principios de diseño

- **Dominio puro:** `content` y `project` no conocen proveedores ni formato
  de activos; no dependen de IA/infra.
- **Adaptadores:** cada generador (`ai`, `image`, `audio`) define un contrato
  (protocolo/Adaptador) e implementa uno o más providers.
- **Provenance:** los activos registran el provider/model que los generó
  (`providers.json`).
- **Manifest único de render:** `output/project.json`.
- **Fallback controlado:** máximo un fallback por run; el proveedor
  `synthetic` nunca se activa automáticamente.

## Secciones principales

- [Módulos](MODULES.md) — desglose por paquete de `src/`.
- [Flujo de datos](DATA_FLOW.md) — recorrido de datos.
- [Guía de desarrollo](guides/DEVELOPMENT_GUIDE.md) — convenciones y entorno.
- [Guía de Git](guides/GIT_WORKFLOW.md) — flujo de trabajo con el repositorio.
- [Roadmap](roadmap/MVP.md) y [Backlog](roadmap/BACKLOG.md) — plan de trabajo.
- [ADR-001](decisions/ADR-001-Architecture.md) — decisiones de arquitectura.
- [Estado del proyecto](PROJECT_STATE.md) — estado actual consolidado.

## Pendientes (TODO)

- [ ] Definir el alcance funcional del analizador de Shorts.
- [ ] Confirmar el rol final de `workflows/` en la orquestación.
- [ ] Cablear o descartar `src/video`.
- [ ] Completar herramientas de calidad (lint/typecheck/tests).
- [ ] Mantener este documento alineado con el código.