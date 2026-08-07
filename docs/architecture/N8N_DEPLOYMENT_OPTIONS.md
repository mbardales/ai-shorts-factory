# N8N_DEPLOYMENT_OPTIONS.md

## Propósito

Comparar objetivamente **Render, Northflank y Railway** como opciones de despliegue para **n8n Community Edition** (auto-alojado), en el contexto del MVP de AI Shorts Factory: operar un canal de YouTube Shorts con el **menor costo posible**. Incluye una recomendación final argumentada.

## Estado

- **Etapa:** Evaluación de opciones de despliegue. Sin decisión tomada ni recursos provisionados.
- **Datos:** Precios y características verificados a **agosto de 2026**. Sujetos a cambio; validar con las páginas oficiales antes de decidir.
- **Última actualización:** 2026-08-06.

## Descripción

El MVP necesita ejecutar **n8n Community Edition** (edición gratuita, licencia *fair-code*: workflows, ejecuciones y usuarios ilimitados) que orqueste los flujos de análisis y generación de Shorts, conectando con la API de YouTube y los proveedores de IA. Como el software n8n es gratuito, el **único costo es la infraestructura**.

Las tres plataformas soportan la imagen oficial de n8n (`n8nio/n8n`) y Postgres como base de datos. La comparación se centra en el costo real para una carga de trabajo pequeña y siempre activa (n8n procesando tareas programadas en horario continuo).

## Secciones principales

### 1. Render

Plataforma PaaS con precios por **plan plano** (plan del workspace + instancia de cómputo por servicio).

**Ventajas**

- Precio **predecible**: se conoce el tope mensual de antemano.
- Guía oficial y plantilla (Blueprint `render.yaml`) para desplegar n8n con la imagen oficial.
- Servicios de background workers y cron jobs como tipos de servicio de primera clase.
- Postgres gestionado nativo; despliegues zero-downtime (en planes de pago).
- Free tier para desarrollo/experimentación ($0).
- Certificados TLS gratuitos y despliegue desde Git con Docker o buildpacks.

**Desventajas**

- Free tier **no apto para producción**: el servicio web se suspende tras 15 min de inactividad (arranque en frío de 30–60 s; los webhooks externos se agotan) y la base de datos Postgres gratuita se **elimina a los 30 días**.
- El costo mínimo fiable supera a los competidores: ~$7/mes por instancia Starter + ~$6–7/mes por Postgres.
- Un disco persistente impide despliegues zero-downtime y limita a **una sola instancia** (sin escalado horizontal con disco).
- Sin BYOC, sin GPU, sin Kubernetes gestionado.

**Coste**

- Workspace: Hobby $0/mes + cómputo; Pro $25/mes + cómputo.
- Web service: Free $0 (512 MB RAM / 0,1 CPU) · Starter $7/mes (512 MB / 0,5) · Standard $25/mes (2 GB / 1).
- Postgres: desde ~$6–7/mes (free tier expira en 30 días).
- Discos persistentes: $0,25/GB-mes (mínimo práctico 10 GB ≈ $2,50/mes).
- **Estimado MVP siempre activo: ≈ $13–14/mes** (Starter + Postgres), plano y predecible.

**Facilidad de configuración**

Alta. Despliegue con la plantilla oficial de n8n en pasos guiados; se requieren pocas decisiones de infraestructura. La conexión a Postgres está asistida por el Blueprint.

**Persistencia de datos**

- Recomendado: Postgres gestionado (n8n guarda workflows y credenciales).
- Alternativa: disco persistente en `/home/node/.n8n` (SQLite), pero impide escalado horizontal.
- Riesgo: el Postgres gratuito se borra a los 30 días; los datos de workflows no deben vivir en el tier gratuito.

**Compatibilidad con Docker**

Excelente. Soporta imágenes Docker oficiales (incluida `n8nio/n8n`) y buildpacks; los Blueprints permiten versionar el despliegue en Git.

**Escalabilidad**

- Vertical por cambio de instancia (hasta 32 GB / 8 CPU).
- Horizontal limitado: requiere Postgres separado y **sin** disco adjunto (n8n en modo *queue*).
- Sin autoescalado a cero: los planes de pago facturan 24/7 aunque n8n esté inactivo.

**Riesgos**

- Cold starts del free tier → webhooks que agotan el tiempo de espera.
- Pérdida de datos por expiración del Postgres gratuito (borrado duro sin aviso previo).
- Costo mínimo de ~$13/mes para uso fiable, el más alto de las tres opciones.
- Perder la clave de cifrado de n8n (`N8N_ENCRYPTION_KEY`) deja ilegibles workflows y credenciales.

### 2. Northflank

Plataforma basada en Kubernetes con **facturación por uso** (por segundo) y tier gratuito.

**Ventajas**

- **Tier gratuito (Developer Sandbox):** 2 servicios + 2 jobs + 1 addon — alcanza para n8n + Postgres de desarrollo a **$0**.
- **Plantilla oficial de n8n** (stack template) que aprovisiona el contenedor n8n + addon PostgreSQL + variables de entorno (clave de cifrado y conexión) en minutos.
- Facturación por segundo: solo se paga lo que se usa; una carga pequeña cuesta pocos dólares al mes.
- Volúmenes persistentes desde 4 GB hasta 64 TB, con soporte **RWX** (multi-lectura/escritura) para workloads stateful.
- Addons gestionados: PostgreSQL desde ~$2,70/mes.
- Autoescalado y escalado a cero; opción BYOC (traer tu propia nube), GPUs y Kubernetes gestionado para el futuro.
- Uptime histórico 99,99%; SLA en acuerdos enterprise.

**Desventajas**

- Modelo de pago por uso: un workflow fuera de control puede **aumentar la factura** sin un tope claro (hay que configurar alertas/límites).
- El tier Sandbox no debe usarse en producción.
- Ecosistema y comunidad de integraciones más pequeña que Render en el nicho PaaS general.
- Más opciones de configuración que Render: puede resultar abrumador si solo se quiere "subir y listo".

**Coste**

- Sandbox: $0/mes (límites de servicios).
- Planes desde ~$2,70/mes; cómputo por segundo (~$0,01667/vCPU-h ≈ $12/vCPU-mes; ~$0,00833/GB-h ≈ $6/GB-mes).
- PostgreSQL addon desde ~$2,70/mes; volúmenes con costo por GB prorrateado.
- **Estimado MVP pequeño: ≈ $5–10/mes** según recursos reales; posible $0 en fase de pruebas con el Sandbox.

**Facilidad de configuración**

Alta. La plantilla oficial de n8n (con Postgres preconfigurado) reduce el despliegue a unos clics y a ajustar variables de entorno. Permite tanto el asistente como control fino (volúmenes, dominios, redes privadas).

**Persistencia de datos**

- Addon PostgreSQL gestionado como almacenamiento de workflows/credenciales.
- Volúmenes persistentes (incluido RWX) para datos binarios de n8n y activos.
- Los volúmenes persisten entre despliegues y pueden escalarse de tamaño.

**Compatibilidad con Docker**

Excelente. Despliegue de imágenes Docker/OCI desde registros públicos o privados, además de builds desde Git (GitHub, GitLab, Bitbucket).

**Escalabilidad**

- Escalado horizontal y vertical nativo; autoescalado y **escalado a cero** para cargas intermitentes.
- Arquitectura Kubernetes: la mejor ruta de crecimiento de las tres (GPU, BYOC, clusters).
- N8n en modo *queue* (múltiples instancias) soportado.

**Riesgos**

- Facturación variable: conviene fijar alertas y topes de gasto.
- Data residency: datos en la nube gestionada de Northflank (o en la propia nube vía BYOC).
- El Sandbox gratuito no es adecuado para producción; es una fase de pruebas.
- Términos y precios de la nube gestionada pueden variar; revisar la calculadora de precios.

### 3. Railway

Plataforma PaaS con **plan mínimo + consumo por uso** (facturación por segundo).

**Ventajas**

- **Costo de entrada bajo:** Hobby $5/mes (incluye $5 de uso de recursos).
- Despliegues muy rápidos desde Git (Railpack) o imagen Docker; sin configuración de infraestructura.
- Bases de datos como plugins gestionados (PostgreSQL, MySQL, Redis, MongoDB) facturados bajo el mismo medidor.
- Volúmenes persistentes (~$0,15/GB-mes) que **sobreviven** aunque se elimine el despliegue.
- Cron jobs nativos; redes privadas; escalado horizontal por réplicas; opción *Serverless* para servicios inactivos.
- Modelo de consumo: un n8n pequeño y eficiente puede costar menos de $5/mes de recursos.

**Desventajas**

- No existe tipo "background worker" dedicado: los procesos sin endpoint se configuran como servicios separados.
- Los servicios **siempre activos se facturan 24/7** aunque estén ociosos (CPU y RAM asignadas).
- El exceso sobre el mínimo incluido ($5 o $20) se cobra aparte; el plan Hobby requiere monitorear para no pasarse.
- Free trial limitado ($5 de créditos) y límites de uso en planes básicos.
- Egreso de red cobrado ($0,05/GB); servicios olvidados suman al costo.

**Coste**

- Plan Hobby: $5/mes (incluye $5 de uso). Plan Pro: $20/mes (incluye $20 de uso). Trial: $5 de créditos.
- Tasas: memoria ~$10/GB-mes; CPU ~$20/vCPU-mes; volúmenes ~$0,15/GB-mes; egreso $0,05/GB.
- **Estimado MVP pequeño: ≈ $5–7/mes** (con el mínimo de $5 y uso contenido; sobrecosto solo si se excede).

**Facilidad de configuración**

Muy alta. Es la ruta más corta de "repo/imagen a n8n corriendo": se crea el servicio con la imagen `n8nio/n8n`, se añade el plugin Postgres y un volumen, y se configuran variables de entorno. Requiere ajustar manualmente n8n (sin plantilla oficial dedicada, aunque la comunidad publica guías).

**Persistencia de datos**

- Plugin PostgreSQL gestionado para workflows/credenciales.
- Volúmenes (~$0,15/GB-mes) para datos binarios y directorio `.n8n`.
- Los volúmenes no se eliminan aunque se borre el despliegue (pero se siguen facturando).

**Compatibilidad con Docker**

Excelente. Admite cualquier imagen Docker/OCI y builds automáticos desde Git (Railpack), además de la CLI.

**Escalabilidad**

- Escalado horizontal por réplicas; opción *Serverless* para reducir costo de servicios inactivos.
- Los límites de plataforma dependen del plan; sin BYOC ni GPU (solo en plan Enterprise).
- n8n en modo *queue* factible con Postgres y réplicas.

**Riesgos**

- Cobros por uso superpuestos: un n8n siempre activo o workflows costosos pueden exceder el mínimo mensual.
- Servicios olvidados (entornos efímeros, PR deploys) generan gasto sin uso real.
- Datos de egreso facturados; conviene usar red privada para las conexiones internas.
- Sin plantilla oficial de n8n: más margen para error de configuración manual (clave de cifrado, webhook URL).

### 4. Resumen comparativo

| Criterio | Render | Northflank | Railway |
|---|---|---|---|
| Modelo de precio | Plano (predecible) | Por uso / por segundo | Mínimo + por uso |
| Free tier útil para n8n | No apto (spin-down, DB 30 días) | Sí (Sandbox: 2 servicios + 1 addon) | Trial con créditos limitados |
| Costo MVP estimado | ~$13–14/mes | ~$5–10/mes (o $0 en pruebas) | ~$5–7/mes |
| Facilidad de configuración | Alta (plantilla oficial) | Alta (plantilla oficial + flexibilidad) | Muy alta (más manual) |
| Persistencia de datos | Postgres o disco ($0,25/GB) | Postgres addon + volúmenes RWX | Postgres plugin + volúmenes ($0,15/GB) |
| Compatibilidad Docker | Excelente | Excelente (OCI) | Excelente |
| Escalabilidad | Vertical; horizontal limitada con disco | Horizontal + escala a cero + BYOC/GPU | Réplicas + Serverless |
| Riesgo principal | Costo mínimo alto y free tier frágil | Factura variable sin tope por defecto | Sobrecostos por uso y servicios ociosos |

### 5. Recomendación para el MVP (canal de Shorts con el menor costo)

**Opción recomendada: Northflank.**

Justificación:

1. **Menor costo real:** con el plan **Developer Sandbox** ($0) se puede levantar n8n + PostgreSQL (2 servicios + 1 addon) para desarrollar y validar el MVP completo; al pasar a producción, la facturación por segundo de una carga pequeña se mantiene en el rango de **$5–10/mes**. Render exige ~$13–14/mes para el mismo escenario y Railway parte de un mínimo de $5 con riesgo de sobrecosto.
2. **Configuración guiada para n8n:** la plantilla oficial de n8n (contenedor + addon Postgres + variables de entorno y clave de cifrado preconfiguradas) reduce los errores que en Railway se resuelven a mano, y da control fino (volúmenes RWX, redes privadas) sin llegar a la complejidad de gestionar Kubernetes.
3. **Adecuación al ciclo de vida del MVP:** permite empezar gratis (fase de pruebas), escalar horizontalmente y reducir a cero cargas intermitentes, y migrar a BYOC (traer la propia nube) cuando el canal genere tráfico — sin re-arquitectar.
4. **Riesgos controlables:** el único riesgo relevante (factura variable) se mitiga con alertas y topes de gasto, algo habitual y configurable.

**Alternativa a considerar (Railway):** si la prioridad es la simplicidad absoluta y aceptar el piso de $5/mes, Railway es una alternativa sólida y de despliegue muy rápido, siempre que se monitoree el uso para no superar el crédito incluido y se eviten servicios ociosos.

**Cuándo preferir Render:** si se valora la **predictibilidad del gasto** por encima del costo mínimo, o se quiere la plantilla oficial y el soporte de background workers/cron como tipo de servicio nativo. En ese caso, presupuestar ≈ $13–14/mes y evitar el free tier para producción.

**Recomendación operativa transversal (independiente de la plataforma):**

- Usar **PostgreSQL gestionado** (no SQLite en disco) para workflows/credenciales y persistir el directorio de datos de n8n en un volumen.
- Guardar y versionar de forma segura `N8N_ENCRYPTION_KEY`; si se pierde, no se pueden leer workflows ni credenciales cifradas.
- Configurar la URL pública del webhook (`WEBHOOK_URL`) para el trigger de YouTube.
- Fijar alertas/topes de gasto desde el primer mes.
- Registrar la decisión final como ADR en `docs/decisions/`.

## Pendientes (TODO)

- [ ] Validar precios vigentes en las páginas oficiales antes de provisionar.
- [ ] Decidir la plataforma (propuesta: Northflank) y registrar como ADR.
- [ ] Crear las cuentas y verificar los tiers gratuitos de la plataforma elegida.
- [ ] Definir el plan de backup de PostgreSQL y de la clave de cifrado de n8n.
- [ ] Estimar ejecuciones mensuales de n8n para dimensionar recursos y presupuesto.
- [ ] Actualizar este documento si cambian precios o condiciones.
