# Dockerfile para PRODUCCIÓN de n8n Community Edition (despliegue en Northflank)
#
# Uso:
#   docker build -t <registro>/n8n:latest -f infra/Dockerfile .
#   docker push <registro>/n8n:latest
#
# La imagen NO incluye código de automatización ni workflows: solo extiende la
# imagen oficial de n8n. Toda la configuración se inyecta mediante variables
# de entorno en la plataforma (ver .env.example y README.md).

# Imagen base oficial de n8n Community Edition.
FROM n8nio/n8n:latest

# Puerto interno en el que n8n escucha (N8N_PORT).
EXPOSE 5678

# La imagen oficial ya ejecuta como usuario 'node' (no privilegiado).
USER node

# Healthcheck: Northflank y otros orquestadores lo usan para detectar que n8n
# está listo. n8n expone el endpoint /healthz en el puerto 5678.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD node -e "fetch('http://localhost:5678/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

# Valores por defecto de producción. Son sobrescribibles por las variables de
# entorno definidas en la plataforma. NO definir aquí secretos (N8N_ENCRYPTION_KEY,
# credenciales de base de datos): deben venir del entorno de Northflank.
ENV NODE_ENV=production \
    N8N_PORT=5678 \
    N8N_PROTOCOL=https \
    N8N_SECURE_COOKIE=true \
    N8N_DIAGNOSTICS_ENABLED=false \
    N8N_PERSONALIZATION_ENABLED=false \
    N8N_VERSION_NOTIFICATIONS_ENABLED=false \
    GENERIC_TIMEZONE=UTC \
    N8N_DEFAULT_LOCALE=es \
    EXECUTIONS_DATA_PRUNE=true \
    EXECUTIONS_DATA_MAX_AGE=336 \
    EXECUTIONS_DATA_PRUNE_MAX_COUNT=50000 \
    N8N_RUNNERS_ENABLED=true
