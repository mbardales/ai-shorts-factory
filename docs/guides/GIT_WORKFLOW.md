# GIT_WORKFLOW.md

## Propósito

Definir el flujo de trabajo con Git del proyecto **AI Shorts Factory**: estilo
de commits, manejo de ramas y convenciones, basado en la historia real del
repositorio (rama `feature/project-manifest`).

## Estado

- **Etapa:** Convenciones definidas en base al historial real.
- **Última actualización:** 2026-08-11.

## Descripción

### Estado actual del repositorio

- Rama de desarrollo activa: **`feature/project-manifest`** (adelantada a la
  rama principal; contiene el pipeline implementado, con commits convencionales
  de `feat:`/`fix:`).
- `main` conserva la estructura inicial y ya **no refleja** el estado real del
  código.
- No hay CI configurado ni política de protección de ramas.

## Secciones principales

### 1. Estilo de commits

Convencional y en minúsculas, consistente con el historial:

```
<tipo>: <descripción breve>
```

Tipos usados/ sugeridos: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`,
`style`.

Ejemplos del historial real:

- `feat(image): add synthetic image provider`
- `feat(audio): add synthetic audio provider`
- `fix(renderer): integrate audio into final video`
- `feat(config): centralize environment loading`

Se permite el alcance entre paréntesis (`feat(audio):`).

### 2. Ramas

- Trabajar en ramas de trabajo con el prefijo correspondiente:
  - `feature/<descripcion>` — funcionalidades
  - `fix/<descripcion>` — correcciones
  - `docs/<descripcion>` — documentación
- La rama de integración principal es `feature/project-manifest`.
- Integrar mediante revisión cuando el proyecto tenga más colaboradores.
- **No hacer push ni crear PR a menos que se solicite explícitamente.**

### 3. Qué incluir en cada commit

- Solo los archivos intencionados para el cambio.
- **Nunca** incluir secretos, claves ni credenciales.
- Mantener fuera de los commits lo generado: `output/`, `.venv/`, `.env`
  (ya cubiertos por `.gitignore`).
- Verificar antes de commitear: `git status`, `git diff`, y `git diff --check`
  (sin espacios en blanco al final ni conflictos de marcado).

### 4. Mensajes

- Breves y descriptivos en inglés (o español), consistentes dentro de cada rama.
- Sin mayúscula inicial salvo que el tipo lo requiera.
- Un commit por intención/cambio lógico.

## Pendientes (TODO)

- [ ] Definir política de revisión de PR cuando exista más de un colaborador.
- [ ] Decidir si se protege la rama de integración (reglas de protección) al habilitar colaboración.
- [ ] Documentar hooks/tooling de commits si se adoptan herramientas.