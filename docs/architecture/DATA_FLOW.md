# DATA_FLOW.md

## Propósito

Describir el **flujo de datos previsto** del sistema **AI Shorts Factory** entre sus áreas. Sirve de referencia para diseñar los pipelines de análisis y generación cuando se implemente el código.

## Estado

- **Etapa:** Diseño conceptual. No existe código que procese datos todavía.
- **Última actualización:** 2026-08-06.

## Descripción

A partir del objetivo del proyecto (`README.md`: analizador y generador de YouTube Shorts) y de la estructura de directorios, se prevén dos flujos principales. Se presentan como **intención de diseño** y deberán validarse al implementar el sistema.

### Flujo de análisis (previsto)

1. **Entrada:** un video corto de YouTube (Short) que se desea analizar.
2. **Extracción y preparación:** datos o contenido extraído del video; activos de entrada podrían ubicarse en `assets/audio/`.
3. **Procesamiento con LLM:** el contenido se procesa siguiendo prompts definidos en `prompts/` (`system/`, `templates/`, `chains/`), organizados en secuencias de `workflows/core/`.
4. **Resultado:** hallazgos o métricas del análisis; documentación o salida almacenable en `logs/` o `examples/`.

### Flujo de generación (previsto)

1. **Entrada:** idea, brief o parámetros definidos en `config/`.
2. **Orquestación:** un workflow de `workflows/` (con pasos compartidos en `workflows/shared/`) produce los elementos del contenido.
3. **Contenido generado por LLM:** resultado de aplicar prompts de `prompts/`.
4. **Activos:** el contenido se combina con activos de `assets/` (`audio/`, `music/`, `branding/`, `fonts/`) para producir el Short final.

### Relaciones entre áreas

```
config/  ──►  prompts/  ──►  workflows/  ──►  assets/  ──►  salida final
                  ▲               │
                  └───────────────┘
```

- `prompts/` define **qué** se pide al modelo.
- `workflows/` define **cuándo y en qué orden** se pide.
- `assets/` aporta el material multimedia para la salida.
- `config/` parametriza todo el proceso.

> Estos flujos son hipótesis de diseño. No hay funcionalidad implementada que los verifique.

## Secciones principales

- [Visión general del sistema](SYSTEM_OVERVIEW.md)
- [Módulos previstos](MODULES.md)

## Pendientes (TODO)

- [ ] Confirmar el orden real de pasos al implementar el primer pipeline.
- [ ] Definir los formatos de datos de entrada y salida de cada etapa.
- [ ] Definir cómo se persiste o registra la salida (`logs/`, `examples/`).
- [ ] Documentar el contrato entre `prompts/` y `workflows/`.
- [ ] Actualizar este documento con los flujos reales una vez implementados.
