# Integrante 2 · Reglas acordadas para la API

> Nota de trabajo del equipo. El contrato completo está en `api/README.md` y `api/openapi.yaml`.

La capa de persistencia usa PostgreSQL 16 como fuente de verdad transaccional (estado de envíos, asignaciones, catálogos y outbox), MongoDB 7 para la proyección del historial y las evidencias de entrega, y Redis 7 para el estado efímero.

## Reglas que cumple la API

- **Transacción local con outbox.** Cada cambio en `envios` y su fila en `outbox` se escriben en la misma transacción de PostgreSQL. El worker copia el outbox a MongoDB de forma idempotente.
- **Control de capacidad.** La asignación bloquea la fila del repartidor con `SELECT … FOR UPDATE`, comprueba `carga_actual < capacidad_max` y responde 409 `CAPACIDAD_EXCEDIDA`. El `CHECK` de la tabla es una segunda barrera.
- **Aceptación del repartidor.** Tras la asignación, el repartidor confirma con `POST /envios/{codigo}/aceptar-asignacion`; sin eso no puede retirar ni avanzar.
- **Evidencias.** Se guardan en `evidencias_entrega` (MongoDB) antes de marcar `entregado` en PostgreSQL. Si MongoDB no responde, 503 y el envío no cambia.
- **Estado efímero.** Posición y latido solo en Redis con TTL (120 s y 60 s); nunca sirven como comprobante de entrega.
- **Búsqueda operativa.** Directamente en PostgreSQL con índices GIN (`pg_trgm`) y compuestos; se descartó Elasticsearch para no añadir un cuarto motor sin necesidad.
- **Visibilidad.** El cliente solo ve sus envíos y el repartidor solo los que le fueron asignados.
