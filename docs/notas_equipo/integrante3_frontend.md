# Integrante 3 · Frontend

> Nota de trabajo del equipo. Las instrucciones de uso están en el `README.md` principal y en `pruebas_defensa.md`.

## Tecnologías

- HTML5, JavaScript puro (sin compilación) y Bootstrap 5 **incluido localmente** en `frontend/vendor/` (la web funciona sin internet).
- Servido por Nginx en su propio contenedor (puerto 8080).
- Toda la información pasa por la API REST (`http://<host>:8000`). El navegador no conoce credenciales ni se conecta a las bases.
- Una sola función `api()` añade las cabeceras `X-Actor-Id`/`X-Actor-Rol`, traduce los errores de la API a mensajes visibles y detecta si la API no responde.
- Todo dato que viene del servidor pasa por `escapeHTML()` antes de insertarse en la página (prevención de XSS).

## Pantallas

| Pantalla | Rol | Endpoints |
| --- | --- | --- |
| Nueva solicitud y comprobante | cliente, operador, admin | `GET /zonas`, `GET /tipos-servicio`, `GET /clientes`, `POST /envios` (con `Idempotency-Key`) |
| Mis envíos | cliente | `GET /envios`, `PATCH /envios/{codigo}/estado` (cancelar) |
| Seguimiento y detalle | todos | `GET /envios/{codigo}`, `GET /envios/{codigo}/seguimiento`, `GET /envios/{codigo}/evidencia` |
| Panel de despacho | operador, admin | `GET /envios` con filtros (texto, estado, zona, fechas), `GET /repartidores`, `GET …/estado-temporal`, `POST /asignaciones`, `POST /asignaciones/reasignar`, `PATCH …/estado` |
| Mi ruta | repartidor | `GET /envios`, `POST …/aceptar-asignacion`, `PATCH …/estado`, `POST …/entrega`, `POST …/posicion`, `POST …/latido` |
| Administración | admin | `GET /admin/zonas`, `POST/PUT /zonas`, `POST/PUT /tipos-servicio`, `PATCH …/capacidad`, `GET /health`, `GET /sincronizacion` |

## Decisiones

- **Botones según reglas del servidor.** Cada envío llega con `acciones` permitidas para el usuario; la web solo muestra esos botones y, si aun así la API rechaza algo, enseña su mensaje.
- **Reintentos seguros.** El formulario de envío reutiliza su `Idempotency-Key` si se pulsa dos veces; cada cambio de estado lleva un `evento_id` nuevo y la entrega reutiliza el suyo si se reenvía el formulario.
- **Coordenadas ficticias.** Origen, destino y posición del repartidor se generan dentro de la ciudad; no se usa el GPS del navegador.
- **Foto de evidencia simulada.** No se sube el archivo: solo se guarda una referencia (`evidencias/<codigo>/<archivo>`).
- **Consistencia visible.** El seguimiento muestra el estado actual (PostgreSQL), la fuente del historial (MongoDB u outbox) y un aviso cuando hay eventos pendientes de sincronizar; se refresca solo cada 8 s mientras el envío esté en curso.
- **Pantalla técnica.** En Administración se ven la salud de las tres bases y el avance del sincronizador, sin credenciales.

## Historias de usuario

- HU-01 (cliente): registrar un envío y obtener un comprobante con código único.
- HU-02 (cliente): consultar el seguimiento con línea de tiempo, autores y prueba de entrega.
- HU-03 (cliente): cancelar mientras no haya repartidor asignado.
- HU-04 (operador): ver pendientes con filtros, validarlos y asignarlos a un repartidor con capacidad.
- HU-05 (sistema): rechazar asignaciones que excedan la capacidad.
- HU-06 (operador): gestionar incidencias reasignando o cancelando.
- HU-07 (repartidor): aceptar la tarea y reportar retiro, tránsito e incidencias.
- HU-08 (repartidor): confirmar la entrega con receptor, observación y foto simulada.
- HU-09 (administrador): mantener zonas, tipos de servicio y capacidades, y revisar el estado técnico.
