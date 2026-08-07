# GEMINI_SETUP.md

## Propósito

Preparar la integración de **AI Shorts Factory** con **Google AI Studio (Gemini)**: qué es, cómo obtener y proteger la API Key, qué variables de entorno usará el proyecto, cómo validar la clave y cuáles son los errores comunes.

## Estado

- **Etapa:** Preparación de integración. El proveedor de IA aún no está confirmado ni habilitado (ver `config/providers.json`, `enabled: false`).
- **Última actualización:** 2026-08-06.

## Descripción

Google AI Studio es la plataforma de Google para construir aplicaciones con el **Gemini API**. Para usar la API se necesita una **API Key** (clave de API). En este proyecto, la clave **nunca** se guarda en el repositorio: se carga desde una variable de entorno (ver `.gitignore`).

## Secciones principales

### 1. Requisitos previos

- Una cuenta de Google (Gmail o cuenta de Google Workspace).
- Acceso a Google AI Studio desde un navegador web.
- Conocimiento básico de variables de entorno.
- (Opcional) Tener definido dónde guardar el archivo de entorno local (`.env`), ya excluido del repositorio por `.gitignore`.

### 2. Qué es Google AI Studio

- **Google AI Studio** es la herramienta web de Google para experimentar con modelos Gemini y generar claves de API.
- Se accede en: `https://aistudio.google.com/`
- Permite:
  - Probar modelos Gemini con texto, imágenes, audio y video.
  - Crear y administrar **API Keys**.
  - Exportar código para usar el Gemini API desde distintos lenguajes.
- El **Gemini API** se consume a través del endpoint REST: `https://generativelanguage.googleapis.com/v1beta`.

### 3. Cómo obtener una API Key

1. Ingresar a Google AI Studio con la cuenta de Google: `https://aistudio.google.com/`.
2. Ir a la sección de claves de API: `https://aistudio.google.com/apikey`.
3. Si es la primera vez, Google AI Studio crea automáticamente un proyecto y una clave.
4. Si se necesita una clave nueva, hacer clic en **Crear clave de API** (Create API key).
5. Copiar la clave. **Guardarla en un lugar seguro**; solo se muestra en el momento de la creación (y puede consultarse en la misma página).

> **Nota (2026):** las claves nuevas se crean como **auth keys**. Las claves "estándar" sin restricciones están siendo eliminadas por Google; migrar a auth keys o a claves con restricciones explícitas para evitar interrupciones (la migración es obligatoria para septiembre de 2026).

### 4. Buenas prácticas para proteger la clave

- **Nunca** versionar la clave en el repositorio (`.gitignore` excluye `.env` y `.env.*`).
- Guardar la clave en una variable de entorno local (archivo `.env` o configurada en el sistema).
- No pegar la clave en mensajes de chat, código, issues o documentación.
- Usar claves con **restricciones** (limitadas a la API de Gemini) en lugar de claves sin restricciones.
- Rotar la clave si se sospecha filtración; revocar la clave comprometida en Google AI Studio.
- No incluir la clave en URLs, logs ni capturas de pantalla compartidas.
- Considerar cuotas y límites de uso: una clave comprometida o expuesta puede generar consumo no autorizado.

### 5. Variables de entorno que utilizará el proyecto

| Variable | Secreta | Descripción |
|---|---|---|
| `GEMINI_API_KEY` | Sí | Clave de API de Google AI Studio. Es la variable principal del proyecto para el proveedor Gemini. |
| `GEMINI_MODEL` | No | Modelo de Gemini por defecto (ejemplo: `gemini-3.6-flash`). Opcional; si no se define, se usa el valor de `config/providers.json`. |
| `GEMINI_BASE_URL` | No | URL base de la API. Opcional; por defecto `https://generativelanguage.googleapis.com/v1beta`. |

Ejemplo del archivo local `.env` (nunca se versiona):

```
GEMINI_API_KEY=TU_API_KEY_AQUI
GEMINI_MODEL=gemini-3.6-flash
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
```

> El SDK oficial de Gemini detecta automáticamente `GEMINI_API_KEY` (y también `GOOGLE_API_KEY`, que tiene prioridad si ambas están definidas).

### 6. Cómo validar que la clave fue creada correctamente

**Opción A — Desde Google AI Studio:** ingresar a `https://aistudio.google.com/apikey` y verificar que la clave figura en la lista sin etiquetas de bloqueo.

**Opción B — Con una petición a la API (REST):** hacer una solicitud de generación de contenido al endpoint `generateContent`:

```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H 'Content-Type: application/json' \
  -X POST \
  -d '{
    "contents": [ { "parts": [ { "text": "Hola" } ] } ]
  }'
```

- Si la clave es válida, la respuesta es HTTP `200` con un JSON que contiene el texto generado.
- Si la clave es inválida o falta, la respuesta es un error `4xx` (ver sección siguiente).

> En Windows (PowerShell), la variable se referencia como `$env:GEMINI_API_KEY` y las comillas se escapan de forma distinta; consultar la sintaxis de la terminal antes de ejecutar.

### 7. Errores comunes

| Código / Error | Causa probable | Solución |
|---|---|---|
| `400 API key not valid` | La clave es inválida o fue revocada. | Verificar/rotar la clave en Google AI Studio. |
| `403 Permission denied` | La clave no tiene permisos para la API o el proyecto no está habilitado. | Usar una clave con restricciones adecuadas; habilitar el proyecto y la API. |
| `429 RESOURCE_EXHAUSTED` | Se superó la cuota o el límite de solicitudes. | Esperar y reintentar; revisar la cuota de la cuenta. |
| Clave con etiqueta "Bloqueada" (Blocked) | Clave estándar sin restricciones, inactiva por largo tiempo. | Generar una nueva auth key o aplicar restricciones. |
| La clave no se reconoce en la aplicación | La variable de entorno no está definida o lleva un nombre distinto. | Verificar `GEMINI_API_KEY` en el entorno y que `.env` se cargue correctamente. |
| `404` en el modelo | El nombre del modelo no existe o no está disponible para esa clave/región. | Confirmar el nombre del modelo vigente en la documentación oficial. |

## Pendientes (TODO)

- [ ] Confirmar con el equipo el uso de Google AI Studio como proveedor de IA.
- [ ] Obtener la API Key y configurarla solo en el entorno local (`.env`).
- [ ] Habilitar el proveedor en `config/providers.json` (`enabled: true`) cuando se confirme.
- [ ] Definir el modelo de Gemini por defecto en `config/providers.json` y/o `GEMINI_MODEL`.
- [ ] Actualizar este documento si cambian los endpoints o requisitos de Google.
- [ ] Registrar la decisión de proveedor como ADR en `docs/decisions/` cuando se confirme.
