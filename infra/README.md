# README.md

## Propósito

Documentar la **infraestructura mínima** para ejecutar **n8n Community Edition** en dos modalidades:

1. **Desarrollo local** — Docker Compose (`docker-compose.yml`).
2. **Producción** — contenedor Docker para **Northflank** (`Dockerfile`).

Incluye n8n con PostgreSQL como base de datos, volúmenes persistentes y configuración por variables de entorno.

## Estado

- **Etapa:** Infraestructura base definida (local + preparación para Northflank).
- **Última actualización:** 2026-08-06.
- **Nota:** la comparación de plataformas de despliegue gestionado (Render, Northflank, Railway) está en `docs/architecture/N8N_DEPLOYMENT_OPTIONS.md`.

## Descripción

- **n8n** (`n8nio/n8n`) — servidor de automatización Community Edition.
- **postgres** (`postgres:16-alpine`) — base de datos de n8n (workflows, credenciales y datos de ejecución). Solo la levanta el Compose local; en Northflank se usa el addon PostgreSQL gestionado.

Toda la configuración sensible se maneja con variables de entorno (ver `.env.example`). No hay credenciales reales en el repositorio.

## Secciones principales

### 1. Archivos

| Archivo | Descripción |
|---|---|
| `docker-compose.yml` | Compose para **desarrollo local** (n8n + PostgreSQL). |
| `Dockerfile` | Imagen de **producción** para Northflank (extiende la imagen oficial). |
| `.dockerignore` | Excluye secretos y contenido innecesario del contexto de build. |
| `.env.example` | Plantilla con todas las variables recomendadas por n8n Community. |
| `README.md` | Este documento. |

### 2. Desarrollo local (Docker Compose)

**Requisitos previos**

- Docker Engine y Docker Compose (plugin `compose` o `docker-compose`).
- (Opcional) `openssl` para generar valores seguros.

**Configuración inicial**

1. Copiar la plantilla a `.env`:

   ```bash
   cp .env.example .env
   ```

2. Completar los valores en `.env`:
   - `POSTGRES_PASSWORD` (generar con `openssl rand -base64 24`).
   - `N8N_ENCRYPTION_KEY` (generar con `openssl rand -hex 32`).
   - En local: `N8N_HOST=localhost`, `N8N_PROTOCOL=http`, `WEBHOOK_URL=http://localhost:5678/`, `N8N_SECURE_COOKIE=false`.

3. **Nunca versionar `.env`** (está excluido por `.gitignore`).

**Comandos**

```bash
# Levantar los servicios en segundo plano
docker compose up -d

# Ver los logs
docker compose logs -f

# Detener los servicios (los datos persisten)
docker compose down

# Detener y ELIMINAR los volúmenes (¡borra todos los datos!)
docker compose down -v
```

- Acceso a n8n: `http://localhost:${N8N_PORT}` (por defecto `http://localhost:5678`).
- Configurar el usuario administrador en el primer arranque.

### 3. Producción (Northflank)

**Flujo de despliegue**

1. Construir y publicar la imagen en un registro (Docker Hub, GHCR, etc.):

   ```bash
   docker build -t <registro>/n8n:latest -f infra/Dockerfile .
   docker push <registro>/n8n:latest
   ```

2. En Northflank, crear un **servicio de despliegue** usando esa imagen (o apuntar a la carpeta `infra/` con el `Dockerfile`).

3. Añadir el **addon PostgreSQL** gestionado de Northflank y conectar el servicio.

4. Configurar las **variables de entorno** (valores de producción):
   - Conexión a la base de datos: `DB_TYPE=postgresdb`, `DB_POSTGRESDB_HOST=<host del addon>`, `DB_POSTGRESDB_PORT`, `DB_POSTGRESDB_DATABASE`, `DB_POSTGRESDB_USER`, `DB_POSTGRESDB_PASSWORD`.
   - Acceso público: `N8N_HOST=<dominio>`, `N8N_PROTOCOL=https`, `WEBHOOK_URL=https://<dominio>/`, `N8N_SECURE_COOKIE=true`, `N8N_PROXY_HOPS=1` (si hay proxy/CDN).
   - Seguridad: `N8N_ENCRYPTION_KEY` (en el gestor de secretos de Northflank).
   - `GENERIC_TIMEZONE`, `N8N_DEFAULT_LOCALE`, retención de ejecuciones y opciones de privacidad (ver `.env.example`).

5. Adjuntar un **volumen persistente** montado en `/home/node/.n8n` para los datos binarios de n8n.

6. Configurar el **healthcheck** en `/healthz` (el `Dockerfile` ya incluye `HEALTHCHECK`).

**Consideraciones Northflank**

- Northflank gestiona el dominio y el TLS de forma nativa.
- Usar el gestor de secretos de Northflank para `N8N_ENCRYPTION_KEY` y credenciales de la base de datos.
- El addon PostgreSQL debe incluir backups automáticos o configurar uno manual.
- Ver `docs/architecture/N8N_DEPLOYMENT_OPTIONS.md` para la justificación de la plataforma.

### 4. Persistencia de datos (local)

Los datos sobreviven a reinicios y a `docker compose down` gracias a dos volúmenes nombrados:

| Volumen | Contenido |
|---|---|
| `n8n-data` | Directorio de datos de n8n (`/home/node/.n8n`): archivos, activos, backups. |
| `n8n-postgres-data` | Base de datos PostgreSQL (`/var/lib/postgresql/data`). |

> `docker compose down -v` elimina los volúmenes y **pierde todos los datos**. Usar solo si se quiere reiniciar desde cero.

### 5. Variables de entorno

La referencia completa y comentada está en `.env.example`. Variables clave:

| Variable | Obligatoria | Descripción |
|---|---|---|
| `N8N_ENCRYPTION_KEY` | Sí | Clave de cifrado de credenciales. Única e irreemplazable. |
| `POSTGRES_PASSWORD` | Sí (local) | Contraseña de PostgreSQL. |
| `DB_POSTGRESDB_HOST` | Sí | `postgres` (local) o el host del addon (Northflank). |
| `N8N_HOST`, `WEBHOOK_URL`, `N8N_PROTOCOL` | Sí | Acceso público y webhooks. |
| `GENERIC_TIMEZONE` | No | Zona horaria de ejecuciones programadas. |

### 6. Seguridad y buenas prácticas

- La **clave de cifrado** (`N8N_ENCRYPTION_KEY`) es crítica: si se pierde, n8n no puede descifrar las credenciales guardadas. Guardarla de forma segura (gestor de secretos o backup cifrado).
- Nunca exponer secretos en el repositorio ni en el `Dockerfile`.
- En producción, usar HTTPS (Northflank lo provee) y `N8N_SECURE_COOKIE=true`.
- Realizar backups periódicos de PostgreSQL (por ejemplo `pg_dump`) y del directorio `.n8n`.
- El modo *queue* (varias instancias con Redis) no está cubierto por esta infraestructura mínima.

## Pendientes (TODO)

- [ ] Generar y resguardar `N8N_ENCRYPTION_KEY` en un gestor de secretos.
- [ ] Provisionar el addon PostgreSQL de Northflank y probar la conexión.
- [ ] Definir la política de backups de PostgreSQL y del directorio `.n8n`.
- [ ] Definir el dominio y verificar el healthcheck `/healthz` en Northflank.
- [ ] Registrar la decisión de despliegue como ADR en `docs/decisions/`.
- [ ] Documentar los comandos de puesta en marcha en `docs/guides/DEVELOPMENT_GUIDE.md` cuando el stack se consolide.
