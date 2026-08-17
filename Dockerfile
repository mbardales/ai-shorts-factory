# =============================================================================
# Dockerfile (ME40.7) — Control plane: SOLO la API FastAPI.
#
# Este contenedor ejecuta únicamente la API (uvicorn api.app:app). El pipeline
# lo ejecuta un worker outbound (GPU local) que NO forma parte de esta imagen:
# no se instalan librerías de generación local (GPU), no se descargan modelos
# ni cachés de Hugging Face, y no se copian entornos ni artefactos locales
# (ver .dockerignore). Es el artefacto preparado para Northflank.
#
# Configuración por entorno (se inyecta en runtime; los secretos nunca se
# imprimen ni se fijan en la imagen):
#   PORT               -> puerto HTTP (por defecto 8000).
#   DATABASE_URL       -> opcional; vacío = LocalPersistenceRepository
#                         (fallback; no se activa PostgreSQL automáticamente).
#   PIPELINE_RUNS_ROOT -> raíz de ejecuciones (volumen opcional).
#   WORKER_TOKEN       -> secreto del protocolo worker (os.environ).
# =============================================================================

# Python estable acorde con el runtime del proyecto (Python 3.14).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=8000

WORKDIR /app

# Dependencias mínimas de la API (ver requirements-api.txt).
COPY requirements-api.txt requirements-api.txt
RUN pip install --no-cache-dir -r requirements-api.txt

# Código de la API (src/). No se copia scripts/ (worker) ni web/.
COPY src/ /app/src/

# Healthcheck sin dependencia adicional (imagen slim: sin curl/wget).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; p=os.environ.get('PORT','8000'); urllib.request.urlopen('http://127.0.0.1:'+p+'/api/v1/health', timeout=5);"]

# Ejecuta SOLO la API en 0.0.0.0 usando PORT (o 8000). El worker no corre aquí.
CMD ["sh", "-c", "exec uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]