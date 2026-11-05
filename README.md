# Proyecto B · Empresa de mensajería urbana

Sistema web de registro de envíos, despacho y seguimiento con **persistencia políglota**: PostgreSQL 16, MongoDB 7 y Redis 7, una API REST en FastAPI, un sincronizador (outbox → MongoDB) y una web en HTML, CSS y JavaScript. Todo corre en contenedores con Docker Compose. Todos los datos son ficticios.

```
Navegador ──HTTP──► Frontend (Nginx :8080)
    │
    └──REST──► API FastAPI (:8000) ──► PostgreSQL 16 (envíos, asignaciones, catálogos, outbox)
                    │            ├──► MongoDB 7 (evidencias de entrega)
                    │            └──► Redis 7 (posición y conexión con TTL)
                    │
               Worker sync ── lee outbox de PostgreSQL ──► MongoDB 7 (historial de seguimiento)
```

## 1. Requisitos previos

- Docker Desktop (o Docker Engine con el plugin Compose v2) en ejecución.
- Puertos libres en el equipo: **8080** (web) y **8000** (API). Las bases de datos no publican puertos: solo se accede a ellas por la red interna o con `docker compose exec`.
- No hace falta internet para usar la web: Bootstrap está incluido en `frontend/vendor/`. Solo se necesita para descargar las imágenes la primera vez.

## 2. Ejecución limpia

```bash
cp .env.example .env            # Windows (PowerShell): Copy-Item .env.example .env
docker compose up --build -d
docker compose ps               # postgres, mongo, redis y api deben estar "healthy"
```

La primera vez, PostgreSQL y MongoDB cargan el esquema, los índices y los datos semilla (`db/postgres/init`, `db/mongo/init`). En pocos segundos el worker copia a MongoDB el historial de los envíos semilla.

## 3. URLs

| Qué | URL |
| --- | --- |
| Web | http://localhost:8080 |
| API (Swagger interactivo) | http://localhost:8000/docs |
| Salud de la API y dependencias | http://localhost:8000/health |
| Especificación OpenAPI | `api/openapi.yaml` |

## 4. Credenciales y usuarios ficticios

Las contraseñas de las bases están en `.env` (copiado de `.env.example`) y son solo para el laboratorio: `cambiar_esto`. `.env` está en `.gitignore` y no debe subirse al repositorio. El navegador nunca recibe estas credenciales.

La web no tiene login: el selector "Sesión simulada" elige el usuario y la API recibe su identidad en las cabeceras `X-Actor-Id` y `X-Actor-Rol`.

| Usuario | Rol | Qué puede hacer en la web |
| --- | --- | --- |
| Cliente 1, 2, 3 | cliente | Registrar envíos, ver los suyos, seguimiento, cancelar antes de la asignación |
| Operador 1, 2 | operador | Despacho: validar, asignar, reasignar, incidencias, cancelar; registrar envíos a nombre de un cliente |
| Repartidor Moto, Bici, Auto, Furgón | repartidor | Mi Ruta: aceptar asignación, retiro, tránsito, incidencia, entrega con evidencia, posición simulada |
| Administrador | admin | Zonas, tipos de servicio, capacidades y estado técnico (salud y sincronizador) |

## 5. Estructura

| Ruta | Contenido |
| --- | --- |
| `docker-compose.yml`, `.env.example` | Orquestación, volúmenes, red interna y variables |
| `db/postgres/init/` | `01_schema.sql` (tablas, restricciones, índices) y `02_seed.sql` (datos semilla con historial) |
| `db/mongo/init/01_init.js` | Colecciones con validación, índices y evidencias semilla |
| `api/app/` | API REST: `routers.py` (HTTP), `servicios.py` (reglas y transacciones), `estados.py` (máquina de estados) |
| `api/worker/` | `sync.py` (outbox → MongoDB) y `reconstruir.py` (regenera MongoDB desde el outbox) |
| `api/tests/`, `api/scripts/demo.py` | Pruebas automatizadas y demostraciones de la defensa |
| `api/openapi.yaml`, `api/README.md` | Contrato de la API |
| `frontend/` | Web (Nginx) |
| `docs/informe_tecnico.md` | Informe técnico consolidado con diagramas |
| `pruebas_defensa.md` | Guion paso a paso de las pruebas de la defensa |

## 6. Pruebas

```bash
docker compose exec -e RUN_INTEGRATION=1 api python -m pytest -v    # pruebas automatizadas
docker compose exec api python scripts/demo.py todo                   # demostraciones (código único, transición inválida, concurrencia, idempotencia)
```

El paso a paso de las cinco pruebas obligatorias está en `pruebas_defensa.md`.

## 7. Reinicio y limpieza

| Orden | Efecto en los datos |
| --- | --- |
| `docker compose stop` y `docker compose start` | Se conservan |
| `docker compose restart` | PostgreSQL y MongoDB se conservan (volúmenes). Redis se vacía por diseño (estado efímero) |
| `docker compose down` | Se eliminan los contenedores; **los volúmenes y los datos se conservan** |
| `docker compose up --build -d` tras cambiar código | Reconstruye imágenes sin tocar los datos |
| `docker compose down -v` | **Borra los volúmenes**: se pierden los datos y se vuelve a la semilla en el siguiente `up`. Usar solo cuando se quiera empezar de cero |

Para regenerar solo el historial de MongoDB a partir del outbox (sin perder nada):

```bash
docker compose exec worker python -m worker.reconstruir
```

## 8. Solución de problemas

| Síntoma | Causa probable y solución |
| --- | --- |
| La web muestra "No se pudo conectar con la API" | La API no está arriba: `docker compose ps` y `docker compose logs api` |
| Error `ESQUEMA_DESACTUALIZADO` o `/health` con `esquema_desactualizado` | El volumen de PostgreSQL es de una versión anterior y los scripts de `db/` no se volvieron a ejecutar. Solución: `docker compose down -v` y `docker compose up --build -d` |
| Errores de CORS en la consola del navegador | Abrir la web en `http://localhost:8080` o `http://127.0.0.1:8080` (los orígenes de `CORS_ORIGINS`) |
| La línea de tiempo dice "eventos pendientes" | El worker está detenido o atrasado: `docker compose logs worker` |
| Los cambios en `db/*/init` no se aplican | Los scripts solo corren con volúmenes vacíos: `docker compose down -v` y `up` |
| Puerto 8080 u 8000 ocupado | Cambiar el puerto publicado en `docker-compose.yml` (y `CORS_ORIGINS` si cambia el de la web) |
