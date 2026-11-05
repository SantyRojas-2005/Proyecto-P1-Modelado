# API REST · Mensajería urbana (Proyecto B)

API en **FastAPI** que es el único punto de entrada de la web. El navegador nunca habla con PostgreSQL, MongoDB ni Redis.

| Dominio | Gestor | Qué guarda |
| --- | --- | --- |
| Solicitudes, asignaciones, catálogos, consulta operativa | **PostgreSQL 16** | Estado actual, capacidad, outbox (historial oficial) |
| Seguimiento y evidencias | **MongoDB 7** | Proyección de lectura del historial (la escribe el worker) y pruebas de entrega (las escribe la API) |
| Posición y conexión de repartidores | **Redis 7** | Datos efímeros con TTL, sin persistencia en disco |

## 1. Puesta en marcha

Desde la carpeta raíz del proyecto (donde está `docker-compose.yml`), con los pasos del `README.md` principal:

- Documentación interactiva (Swagger): `http://localhost:8000/docs`
- Especificación OpenAPI: `api/openapi.yaml` (generada desde el código)
- Salud: `GET /health` responde `200` si las tres bases están arriba y `503` ("degradado") si alguna no, sin exponer credenciales.
- Sincronizador: `GET /sincronizacion` (operador o admin) muestra eventos pendientes del outbox y el retraso.

### Variables de entorno

| Variable | Uso |
| --- | --- |
| `PG_DB`, `PG_USER`, `PG_PASSWORD`, `POSTGRES_HOST` | Conexión a PostgreSQL |
| `MONGO_USER`, `MONGO_PASSWORD`, `MONGO_DB`, `MONGO_HOST` | Conexión a MongoDB (`authSource=admin`) |
| `REDIS_PASSWORD`, `REDIS_HOST` | Conexión a Redis |
| `CORS_ORIGINS` | Orígenes permitidos para la web. Por defecto `http://localhost:8080,http://127.0.0.1:8080` |
| `SYNC_INTERVALO_SEGUNDOS` | Cada cuánto revisa el worker el outbox (por defecto 2) |

## 2. Cómo se identifica quien llama

Las rutas de negocio exigen dos cabeceras:

| Cabecera | Valor |
| --- | --- |
| `X-Actor-Id` | UUID de un usuario existente |
| `X-Actor-Rol` | `cliente`, `operador`, `repartidor` o `admin` |

> Es una simplificación del laboratorio: no hay login ni tokens. La API sí aplica autorización por rol, por dueño del envío (cliente) y por asignación (repartidor). En producción se sustituiría por autenticación real (por ejemplo, OAuth2 con JWT).

Usuarios semilla:

| Nombre | `X-Actor-Id` | Rol | Notas |
| --- | --- | --- | --- |
| Admin Principal | `11111111-1111-1111-1111-111111111111` | admin | |
| Operador 1 / 2 | `22222222-2222-2222-2222-22222222222{1,2}` | operador | |
| Cliente 1, 2, 3 | `33333333-3333-3333-3333-33333333333{1,2,3}` | cliente | Dueños de los envíos `ENV-SEED00xx` |
| Repartidor Moto | `44444444-4444-4444-4444-444444444441` | repartidor | Repartidor `5555…5551`, capacidad 3 |
| Repartidor Bici | `44444444-4444-4444-4444-444444444442` | repartidor | Repartidor `5555…5552`, capacidad 1 (libre) |
| Repartidor Auto | `44444444-4444-4444-4444-444444444443` | repartidor | Repartidor `5555…5553`, capacidad 2 |
| Repartidor Furgón | `44444444-4444-4444-4444-444444444444` | repartidor | Repartidor `5555…5554`, capacidad 5 |

Zonas: `1` Norte, `2` Sur, `3` Centro, `4` Valles. Tipos de servicio: `1` Express, `2` Estándar, `3` Programado. Los 20 envíos semilla (`ENV-SEED0001` a `ENV-SEED0020`) cubren todos los estados; el detalle está en `pruebas_defensa.md`.

## 3. Formato de respuestas y errores

Las respuestas son JSON. Las fechas van en ISO 8601 (UTC). Todo error tiene la misma forma:

```json
{ "error": { "codigo": "CAPACIDAD_EXCEDIDA", "mensaje": "el repartidor alcanzó su capacidad máxima",
             "detalles": { "capacidad_max": 1, "carga_actual": 1 } } }
```

| HTTP | `codigo` | Cuándo ocurre | ¿Reintentar? |
| --- | --- | --- | --- |
| 422 | `VALIDACION` | Datos mal formados, fuera de rango, zona desactivada o cliente inexistente (`detalles.campos`) | No, corregir datos |
| 403 | `NO_PERMITIDO` | El rol o el usuario no puede hacer esa operación | No |
| 404 | `NO_ENCONTRADO` | No existe o no es visible para ese usuario | No |
| 409 | `TRANSICION_INVALIDA` | La máquina de estados no lo permite (`detalles.permitidas`) | No |
| 409 | `CAPACIDAD_EXCEDIDA` | El repartidor ya tiene su carga completa | Con otro repartidor |
| 409 | `ENVIO_YA_ASIGNADO` | El envío ya tiene una asignación activa | No |
| 409 | `CONFLICTO` | Nombre duplicado, capacidad menor que la carga, asignación ya aceptada u otra restricción | No |
| 503 | `DEPENDENCIA_NO_DISPONIBLE` | PostgreSQL, MongoDB o Redis no responde | Sí, con el mismo `evento_id` |

## 4. Endpoints

| Método y ruta | Rol | Qué hace | Gestor |
| --- | --- | --- | --- |
| `GET /health` | Público | Estado de las dependencias | Las tres |
| `GET /sincronizacion` | operador, admin | Pendientes y retraso del outbox | PostgreSQL y MongoDB |
| `GET /zonas`, `GET /tipos-servicio` | Público | Catálogos activos para formularios | PostgreSQL |
| `GET /clientes` | operador, admin | Clientes para registrar a su nombre | PostgreSQL |
| `GET /repartidores` | operador, repartidor, admin | Capacidad y carga | PostgreSQL |
| `POST /envios` | cliente, operador, admin | Registra un envío y devuelve el comprobante. `Idempotency-Key` opcional. Operación debe enviar `cliente_id` | PostgreSQL |
| `GET /envios?q=&estado=&zona_id=&desde=&hasta=&limite=&desplazamiento=` | Todos (cliente: sus envíos; repartidor: los asignados a él) | Búsqueda operativa; cada fila trae `acciones` permitidas | PostgreSQL (`pg_trgm`) |
| `GET /envios/{codigo}` | Quien puede verlo | Detalle, repartidor, `acciones` permitidas | PostgreSQL |
| `GET /envios/{codigo}/seguimiento` | Quien puede verlo | Línea de tiempo con autor; respaldo desde el outbox si MongoDB cae | MongoDB + PostgreSQL |
| `GET /envios/{codigo}/evidencia` | Quien puede verlo | Prueba de entrega | MongoDB |
| `PATCH /envios/{codigo}/estado` | Según el destino | `aceptado`, `retirado`, `en_transito`, `incidencia`, `cancelado` | PostgreSQL |
| `POST /asignaciones` | operador, admin | Asigna repartidor a un envío `aceptado` | PostgreSQL |
| `POST /asignaciones/reasignar` | operador, admin | Cambia de repartidor (asignado, retirado o incidencia) | PostgreSQL |
| `POST /envios/{codigo}/aceptar-asignacion` | repartidor asignado, admin | El repartidor acepta la tarea | PostgreSQL |
| `POST /envios/{codigo}/entrega` | repartidor asignado, admin | Guarda evidencia y marca `entregado` | MongoDB y luego PostgreSQL |
| `POST /repartidores/{id}/posicion` | el propio repartidor, admin | Posición simulada (expira a los 120 s) | Redis |
| `POST /repartidores/{id}/latido` | el propio repartidor, admin | Conexión (expira a los 60 s) | Redis |
| `GET /repartidores/{id}/estado-temporal` | operador, repartidor, admin | Posición y conexión, con `vigente` y TTL | Redis |
| `POST /zonas`, `PUT /zonas/{id}`, `GET /admin/zonas` | admin | Alta, edición y activación de zonas | PostgreSQL |
| `POST /tipos-servicio`, `PUT /tipos-servicio/{id}` | admin | Alta y edición de servicios | PostgreSQL |
| `PATCH /repartidores/{id}/capacidad` | admin | Capacidad (no menor que la carga actual) | PostgreSQL |

### 4.1 Máquina de estados

Camino feliz: `creado → aceptado → asignado → (aceptación del repartidor) → retirado → en_transito → entregado`.

| Destino | Desde | Quién lo pide | Regla adicional |
| --- | --- | --- | --- |
| `aceptado` | `creado` | operador, admin | |
| `asignado` | `aceptado` | operador, admin | Solo por `POST /asignaciones`; respeta la capacidad |
| `retirado` | `asignado`, `incidencia` | repartidor asignado, admin | Exige asignación aceptada. Desde incidencia, solo si ocurrió antes del retiro |
| `en_transito` | `retirado`, `incidencia` | repartidor asignado, admin | Exige asignación aceptada. Desde incidencia, solo si ocurrió después del retiro |
| `incidencia` | `asignado`, `retirado`, `en_transito` | repartidor asignado, operador, admin | |
| `entregado` | `en_transito` | repartidor asignado, admin | Solo por `POST /entrega`, con evidencia |
| `cancelado` | `creado`, `aceptado` | cliente dueño | |
| `cancelado` | `creado`, `aceptado`, `asignado`, `incidencia` | operador, admin | |

`entregado` y `cancelado` son finales y liberan la capacidad del repartidor. Aceptar y reasignar no cambian el estado, pero sí la versión y el historial.

## 5. Ejemplos de uso

Crear un envío (cliente):

```bash
curl -X POST http://localhost:8000/envios \
  -H "Content-Type: application/json" \
  -H "X-Actor-Id: 33333333-3333-3333-3333-333333333331" -H "X-Actor-Rol: cliente" \
  -H "Idempotency-Key: 7f1c2b9e-0001" \
  -d '{"remitente":"Empresa Ficticia SA","destinatario":"Ana Ficticia",
       "origen_direccion":"Av. Ficticia 1","destino_direccion":"Calle Inventada 2",
       "origen":{"lat":-0.1807,"lng":-78.4678},"destino":{"lat":-0.21,"lng":-78.49},
       "zona_id":1,"tipo_servicio_id":1}'
```

Asignar (operador) y aceptar (repartidor):

```bash
curl -X POST http://localhost:8000/asignaciones -H "Content-Type: application/json" \
  -H "X-Actor-Id: 22222222-2222-2222-2222-222222222221" -H "X-Actor-Rol: operador" \
  -d '{"evento_id":"EV-2026-0001","envio_codigo":"ENV-SEED0007","repartidor_id":"55555555-5555-5555-5555-555555555552"}'

curl -X POST http://localhost:8000/envios/ENV-SEED0007/aceptar-asignacion -H "Content-Type: application/json" \
  -H "X-Actor-Id: 44444444-4444-4444-4444-444444444442" -H "X-Actor-Rol: repartidor" \
  -d '{"evento_id":"EV-2026-0002"}'
```

Confirmar entrega (repartidor; el envío debe estar `en_transito`, por ejemplo `ENV-SEED0014` con el Repartidor Moto):

```bash
curl -X POST http://localhost:8000/envios/ENV-SEED0014/entrega -H "Content-Type: application/json" \
  -H "X-Actor-Id: 44444444-4444-4444-4444-444444444441" -H "X-Actor-Rol: repartidor" \
  -d '{"evento_id":"EV-2026-0003","receptor_nombre":"Gabriel Espinosa",
       "observacion":"Entregado en recepción","foto_url":"evidencias/ENV-SEED0014.jpg"}'
```

## 6. Consistencia

- **El estado actual siempre viene de PostgreSQL.** La línea de tiempo viene de MongoDB y puede ir unos segundos atrasada: `pendientes_sincronizacion` indica cuántos eventos faltan. Si MongoDB no responde, se lee del outbox (`fuente_historial: "postgresql_outbox"`).
- **El outbox es el historial oficial.** Se escribe en la misma transacción que el cambio de estado, por eso estado e historial nunca se contradicen. MongoDB se reconstruye con `docker compose exec worker python -m worker.reconstruir`.
- **Idempotencia.** Cada cambio exige `evento_id`; reenviarlo devuelve `"ya_aplicado": true` sin repetir el efecto. `POST /envios` con la misma `Idempotency-Key` devuelve `200` y el mismo envío.
- **Estado temporal.** Posición y conexión caducan; nunca son prueba de entrega.
- **Entrega (saga en dos pasos).** Primero la evidencia en MongoDB (upsert por código, idempotente) y después el cambio a `entregado` en PostgreSQL. Si MongoDB cae, responde 503 y el envío no se marca entregado. Si falla el segundo paso, el reintento con el mismo `evento_id` es seguro.

## 7. Pruebas

Dentro del contenedor (recomendado, sin instalar nada):

```bash
docker compose exec -e RUN_INTEGRATION=1 api python -m pytest -v
```

En local, desde `api/`, con Python 3.11+:

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest                                     # unitarias; las de integración se omiten sin RUN_INTEGRATION=1
```

| Archivo | Demuestra |
| --- | --- |
| `test_estados.py` | Transiciones, roles, cancelación del cliente, salida de incidencia, aceptación, acciones por rol |
| `test_http.py` | Errores 422 y 403 sin tocar la base |
| `test_integracion.py` | Semilla coherente, comprobante, idempotencia, capacidad y concurrencia, aceptación, incidencia, reasignación, visibilidad por rol, catálogos duplicados, respaldo del historial con MongoDB caído, TTL en Redis |

Demostraciones para la defensa: `docker compose exec api python scripts/demo.py todo`.

## 8. Límites conocidos

- **Autenticación simulada** con cabeceras.
- **Respuestas sin esquema tipado** en OpenAPI (los cuerpos de entrada sí están tipados).
- **Evidencia huérfana posible.** Si falla el cambio de estado después de guardar la evidencia, la evidencia queda en MongoDB sin cambio de estado. No afecta al estado actual y el reintento la sobrescribe.
- **Búsqueda sin índice separado.** Se resuelve en PostgreSQL con `pg_trgm`; a gran volumen convendría una proyección en un motor de búsqueda alimentada por el mismo outbox.
- **Altas de repartidores y usuarios** solo por datos semilla.
