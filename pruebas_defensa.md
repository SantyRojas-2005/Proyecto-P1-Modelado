# Guía de pruebas para la defensa · Proyecto B

Todas las órdenes se ejecutan desde la carpeta del proyecto (donde está `docker-compose.yml`) y funcionan igual en PowerShell, CMD o Bash. Las credenciales son las ficticias de `.env.example`.

Antes de empezar, partir de datos limpios:

```bash
docker compose down -v
docker compose up --build -d
docker compose ps        # postgres, mongo, redis y api deben estar "healthy"
```

Web: http://localhost:8080 · Documentación interactiva de la API: http://localhost:8000/docs

Atajos usados abajo:

```bash
# Consola SQL
docker compose exec postgres psql -U mensajeria_user -d mensajeria
# Consola de MongoDB
docker compose exec mongo mongosh -u mongo_user -p cambiar_esto --authenticationDatabase admin mensajeria
```

## Datos semilla disponibles

| Código | Estado | Para qué sirve |
| --- | --- | --- |
| ENV-SEED0001 a 0006 | creado | Validar desde el despacho |
| ENV-SEED0007 a 0010 | aceptado | Asignar repartidor |
| ENV-SEED0011 | asignado (sin aceptar) | Mostrar que el repartidor debe aceptar |
| ENV-SEED0012 | asignado (aceptado) | Retiro |
| ENV-SEED0013 | retirado | Tránsito |
| ENV-SEED0014, 0015 | en tránsito | Entrega con evidencia |
| ENV-SEED0016 | incidencia | Reasignar o cancelar |
| ENV-SEED0017, 0018 | entregado | Evidencia guardada en MongoDB |
| ENV-SEED0019, 0020 | cancelado | Historial conservado |

El Repartidor Bici (capacidad 1) empieza libre para la prueba de capacidad.

---

## Prueba 1 · Código único y consulta posterior

En la web, como Cliente 1: Nueva Solicitud → Confirmar. Se muestra el comprobante con el código `ENV-XXXXXXXX`. Pulsar "Ver seguimiento".

Alternativa por consola:

```bash
docker compose exec api python scripts/demo.py codigo
```

Se crean dos envíos con códigos distintos; el dueño los consulta y otro cliente recibe 404.

Comprobar en PostgreSQL:

```sql
SELECT codigo, estado, version, creado_en FROM envios ORDER BY creado_en DESC LIMIT 3;
```

## Prueba 2 · Secuencia válida y transición inválida

```bash
docker compose exec api python scripts/demo.py invalida
```

`creado → entregado` y `creado → retirado` se rechazan con 409 `TRANSICION_INVALIDA`; `creado → aceptado` sí se aplica.

Secuencia válida desde la web:

1. Operador 1 → Despacho → Validar → elegir repartidor → Asignar.
2. Repartidor elegido → Mi Ruta → Aceptar asignación → Paquete retirado → Reportar incidencia → Retomar tránsito → Confirmar entrega.
3. Cliente → Seguimiento: estado actual (PostgreSQL), línea de tiempo con autor (MongoDB) y prueba de entrega.

Reglas que se pueden mostrar en vivo:

- El repartidor no puede retirar sin aceptar la asignación.
- Una incidencia ocurrida antes del retiro solo permite volver a "retirado" o cancelar.
- El cliente solo cancela antes de la asignación; después lo hace operación.

## Prueba 3 · Dos asignaciones incompatibles (capacidad)

```bash
docker compose exec api python scripts/demo.py concurrencia
```

Dos hilos asignan a la vez dos envíos al Repartidor Bici (capacidad 1). Siempre sale un 201 y un 409 `CAPACIDAD_EXCEDIDA`, porque la asignación bloquea la fila del repartidor con `SELECT … FOR UPDATE`.

Desde la web: asignar ENV-SEED0007 a la Bici y después ENV-SEED0008 a la Bici; el segundo muestra el error en rojo.

Comprobar que la carga coincide con las asignaciones activas:

```sql
SELECT r.id, r.capacidad_max, r.carga_actual,
       (SELECT count(*) FROM asignaciones a WHERE a.repartidor_id = r.id AND a.activa) AS activas
FROM repartidores r;
```

Restricciones de respaldo en la base (la API no depende solo de ellas):

```sql
-- Falla por el índice único parcial uq_asignacion_activa (ENV-SEED0012 ya tiene asignación activa)
INSERT INTO asignaciones (envio_codigo, repartidor_id) VALUES ('ENV-SEED0012', '55555555-5555-5555-5555-555555555551');
-- Falla por el CHECK carga_actual <= capacidad_max (transacción revertida, no deja datos alterados)
BEGIN; UPDATE repartidores SET carga_actual = 99 WHERE id = '55555555-5555-5555-5555-555555555552'; ROLLBACK;
```

## Prueba 4 · Reiniciar el estado temporal (Redis)

1. Repartidor Moto → Mi Ruta → "Enviar ahora". Se muestra "posición vigente · expira en 120 s".
2. Ver las claves y su TTL:

   ```bash
   docker compose exec redis redis-cli -a cambiar_esto --no-auth-warning KEYS "repartidor:*"
   docker compose exec redis redis-cli -a cambiar_esto --no-auth-warning TTL repartidor:55555555-5555-5555-5555-555555555551:posicion
   ```

3. Reiniciar Redis: `docker compose restart redis`
4. Repetir el paso 2: no hay claves. En la web la posición aparece "no vigente".
5. Consultar ENV-SEED0017 en Seguimiento: historial y evidencia siguen intactos. La posición es efímera y no es prueba de entrega.

## Prueba 5 · Persistencia tras reinicio y caída de la sincronización

### 5a. La entrega registrada permanece

```bash
docker compose restart
```

En la web, Seguimiento de un envío entregado (por ejemplo ENV-SEED0017 o el entregado en la prueba 2): estado, historial y evidencia siguen ahí.

```javascript
// En la consola de MongoDB
db.evidencias_entrega.find({}, {_id: 0, envio_codigo: 1, receptor_nombre: 1})
db.eventos_seguimiento.countDocuments()
```

### 5b. Caída del sincronizador (worker)

1. `docker compose stop worker`
2. En la web, avanzar un envío (por ejemplo validar ENV-SEED0001).
3. Seguimiento de ENV-SEED0001: el estado ya es "aceptado" (PostgreSQL), pero aparece el aviso "1 evento aún no llega al historial". En Admin → Estado técnico, "Outbox pendientes" es mayor que 0.
4. El negocio sigue operando: se pueden crear envíos y asignar.
5. `docker compose start worker` → en unos segundos el aviso desaparece y la línea de tiempo se completa, en orden.

```sql
SELECT evento_id, publicado_en FROM outbox ORDER BY id DESC LIMIT 5;
```

### 5c. Caída de MongoDB

1. `docker compose stop mongo`
2. `GET /health` responde 503 "degradado" con `mongodb: caido` (sin exponer credenciales).
3. Seguimiento: la línea de tiempo se lee del outbox de PostgreSQL (aviso amarillo). Crear y asignar envíos sigue funcionando.
4. Confirmar entrega devuelve 503: no se marca "entregado" sin evidencia. La operación se puede reintentar con seguridad.
5. `docker compose start mongo` → el worker publica lo pendiente y la entrega ya se puede registrar.

### 5d. Reconstruir la proyección de MongoDB

El outbox es el historial oficial; MongoDB se puede regenerar desde él:

```bash
docker compose exec worker python -m worker.reconstruir
```

## Pruebas automatizadas

```bash
# Unitarias (sin bases) + integración contra los contenedores levantados
docker compose exec -e RUN_INTEGRATION=1 api python -m pytest -v
```

Cubren: código único e idempotencia, capacidad y concurrencia, máquina de estados, aceptación de asignación, salida de incidencia, reasignación, visibilidad por rol, catálogos duplicados, coherencia de la semilla entre estado e historial, respaldo del historial con MongoDB caído y expiración en Redis.

## Reinicio y limpieza

| Orden | Efecto |
| --- | --- |
| `docker compose stop` / `start` | Detiene o arranca sin perder datos |
| `docker compose restart` | Reinicia; PostgreSQL y MongoDB conservan todo y Redis se vacía |
| `docker compose down` | Elimina contenedores; los volúmenes y los datos se conservan |
| `docker compose down -v` | Borra también los volúmenes: vuelve a los datos semilla (pérdida intencional) |
