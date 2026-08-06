# GIT_WORKFLOW.md

## Propósito

Definir el flujo de trabajo con Git del proyecto **AI Shorts Factory**: estilo de commits, manejo de ramas y convenciones, basado en la historia actual del repositorio.

## Estado

- **Etapa:** Convenciones definidas en base a la historia existente (2 commits en `main`).
- **Última actualización:** 2026-08-06.

## Descripción

### Estado actual del repositorio

- Rama principal: `main`.
- Historial actual:
  - `Initial commit` — `README.md` y `LICENSE`.
  - `chore: initialize project structure` — creación del esqueleto de directorios (archivos `.gitkeep`).
- No existen ramas de trabajo, tags, ni CI configurado.

## Secciones principales

### 1. Estilo de commits

Seguir un estilo **convencional y en minúsculas** (consistente con el historial):

```
<tipo>: <descripción breve>
```

Tipos sugeridos: `chore`, `feat`, `fix`, `docs`, `refactor`, `test`, `style`.

Ejemplos:

- `docs: add architecture documentation`
- `feat: add analyzer module skeleton`
- `fix: correct data flow in generator`

### 2. Ramas

- Trabajar en **`main`** directamente solo para cambios triviales (documentación, mantenimiento).
- Para funcionalidades, usar ramas de trabajo con nombre descriptivo:
  - `feat/<descripcion>`
  - `fix/<descripcion>`
  - `docs/<descripcion>`
- Integrar la rama a `main` mediante revisión cuando el proyecto cuente con más colaboradores.

### 3. Qué incluir en cada commit

- Solo los archivos intencionados para el cambio.
- **Nunca** incluir secretos, claves o credenciales.
- Mantener fuera de commits los artefactos de build y archivos generados (aún no existe `.gitignore`; crear uno antes de que aparezcan estos artefactos).

### 4. Mensajes

- Mensajes breves y descriptivos en inglés o español, consistentes dentro de cada rama.
- No usar mayúsculas iniciales salvo que el tipo lo requiera.

## Pendientes (TODO)

- [ ] Crear `.gitignore` antes de incorporar artefactos de build o secretos.
- [ ] Definir política de revisión de PR cuando exista más de un colaborador.
- [ ] Decidir si se protege `main` (reglas de protección) al habilitar colaboración.
- [ ] Documentar comandos específicos (conventional commits, hooks) si se adoptan herramientas.
