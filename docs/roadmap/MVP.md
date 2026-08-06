# MVP.md

## Propósito

Definir el alcance mínimo viable (MVP) del proyecto **AI Shorts Factory**: qué debe lograr la primera versión utilizable y cuál es la ruta para alcanzarla.

## Estado

- **Etapa:** Definición de alcance en borrador. Sin código implementado.
- **Última actualización:** 2026-08-06.

## Descripción

El MVP debe entregar el valor central del proyecto según el `README.md`: **analizar y generar YouTube Shorts** de forma asistida. En esta fase aún no se han tomado decisiones técnicas; por lo tanto, el alcance del MVP se presenta como una **propuesta preliminar** que debe validarse antes de comenzar a implementar.

### Alcance propuesto del MVP

1. **Analizador de Shorts (mínimo):**
   - Entrada de un Short de YouTube (identificador o URL).
   - Procesamiento con un modelo de lenguaje utilizando prompts definidos en `prompts/`.
   - Salida del análisis en un formato estructurado y reproducible.

2. **Generador de Shorts (mínimo):**
   - Entrada de una idea o brief simple.
   - Generación del contenido (guion/texto) mediante workflows de `workflows/`.
   - Producción de una salida que combine el contenido con activos de `assets/`.

3. **Base estructural:**
   - Stack tecnológico definido y documentado.
   - `.gitignore` creado.
   - Comandos de build, ejecución y prueba funcionando.

### Fuera del MVP

- Automatización avanzada o procesos de escala.
- Integraciones con servicios externos no esenciales.
- Optimizaciones de rendimiento y contenido.

## Secciones principales

- [Criterios de éxito](#criterios-de-exito)
- [Entregables](#entregables)
- [Dependencias previas](#dependencias-previas)

### Criterios de éxito

- Es posible analizar un Short y obtener un resultado estructurado.
- Es posible generar un Short a partir de una idea simple.
- El proceso es reproducible con documentación y comandos claros.

### Entregables

- Código fuente funcional de los dos flujos centrales.
- Prompt básico de referencia por flujo en `prompts/`.
- Workflow básico de referencia por flujo en `workflows/`.
- Documentación de uso.

### Dependencias previas

- Decisión de stack tecnológico (ver [DEVELOPMENT_GUIDE.md](../guides/DEVELOPMENT_GUIDE.md)).
- Decisiones de arquitectura registradas en `docs/decisions/`.

## Pendientes (TODO)

- [ ] Validar y fijar el alcance del MVP con el equipo.
- [ ] Elegir el stack tecnológico.
- [ ] Definir el primer pipeline mínimo (análisis o generación) a construir.
- [ ] Definir los criterios de aceptación concretos y medibles.
- [ ] Crear tareas derivadas en [BACKLOG.md](BACKLOG.md).
- [ ] Actualizar este documento cuando el MVP esté definido formalmente.
