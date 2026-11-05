"""Reglas de negocio y transacciones.

Regla de diseño: cada escritura en PostgreSQL es una transacción local que incluye
la fila del evento en `outbox`. MongoDB y Redis nunca forman parte de esa transacción;
el worker sincroniza el outbox hacia MongoDB de forma idempotente.

El outbox es el historial oficial (no se borra ni se modifica); la colección
`eventos_seguimiento` de MongoDB es su proyección de lectura y se puede reconstruir.
"""
import uuid
from datetime import datetime, timedelta, timezone

from psycopg2 import errors as pgerr
from psycopg2.extras import Json

from . import estados
from .errors import (AsignacionActiva, CapacidadExcedida, Conflicto, NoEncontrado,
                     NoPermitido, TransicionInvalida, Validacion)
from .infra import transaccion

RESTRICCION_EVENTO_DUPLICADO = "outbox_evento_id_key"
RESTRICCION_ASIGNACION_ACTIVA = "uq_asignacion_activa"
RESTRICCION_IDEMPOTENCIA = "envios_idempotency_key_key"


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ubicacion_geojson(coordenada):
    if coordenada is None:
        return None
    return {"type": "Point", "coordinates": [coordenada.lng, coordenada.lat]}


# ---------- Ayudantes de lectura dentro de una transacción ----------

def _bloquear_envio(cur, codigo: str):
    cur.execute(
        "SELECT codigo, cliente_id, estado, version FROM envios WHERE codigo = %s FOR UPDATE",
        (codigo,),
    )
    fila = cur.fetchone()
    if fila is None:
        raise NoEncontrado(f"el envío {codigo} no existe")
    return fila


def _ya_aplicado(cur, evento_id: str) -> bool:
    cur.execute("SELECT 1 FROM outbox WHERE evento_id = %s", (evento_id,))
    return cur.fetchone() is not None


def _asignacion_activa(cur, codigo: str):
    """Asignación activa del envío con el usuario del repartidor, o None."""
    cur.execute(
        """SELECT a.id, a.repartidor_id, a.aceptada_en, r.usuario_id
           FROM asignaciones a JOIN repartidores r ON r.id = a.repartidor_id
           WHERE a.envio_codigo = %s AND a.activa""",
        (codigo,),
    )
    return cur.fetchone()


def _estado_previo_incidencia(cur, codigo: str):
    cur.execute(
        """SELECT payload->>'estado_anterior' AS previo FROM outbox
           WHERE envio_codigo = %s AND payload->>'estado_nuevo' = 'incidencia'
           ORDER BY id DESC LIMIT 1""",
        (codigo,),
    )
    fila = cur.fetchone()
    return fila["previo"] if fila else None


def _verificar_dueño(envio, actor) -> None:
    # Un cliente solo ve y mueve sus propios envíos; para otros no revelamos que existen
    if actor.rol == "cliente" and str(envio["cliente_id"]) != str(actor.id):
        raise NoEncontrado(f"el envío {envio['codigo']} no existe")


def _validar_reglas(cur, envio, nuevo: str, actor):
    """Todas las reglas de un cambio de estado. Devuelve la asignación activa (o None)."""
    _verificar_dueño(envio, actor)
    asignacion = _asignacion_activa(cur, envio["codigo"])
    if actor.rol == "repartidor" and (asignacion is None or str(asignacion["usuario_id"]) != str(actor.id)):
        raise NoPermitido("el envío no está asignado a este repartidor")
    estados.validar_transicion(envio["estado"], nuevo, actor.rol)
    if envio["estado"] == "incidencia":
        estados.validar_salida_incidencia(_estado_previo_incidencia(cur, envio["codigo"]), nuevo)
    estados.validar_aceptacion(nuevo, asignacion is not None,
                               asignacion is not None and asignacion["aceptada_en"] is not None)
    return asignacion


def _registrar_evento(cur, codigo, evento_id, tipo, secuencia, anterior, nuevo, actor, nota, extra=None):
    marca = ahora()
    payload = {
        "evento_id": evento_id,
        "envio_codigo": codigo,
        "secuencia": secuencia,
        "tipo": tipo,
        "estado_anterior": anterior,
        "estado_nuevo": nuevo,
        "actor": {"id": str(actor.id), "rol": actor.rol},
        "nota": nota,
        "ocurrido_en": marca,
        "registrado_en": marca,
    }
    if extra:
        payload.update(extra)
    cur.execute(
        "INSERT INTO outbox (evento_id, envio_codigo, tipo, payload) VALUES (%s, %s, %s, %s)",
        (evento_id, codigo, tipo, Json(payload)),
    )


def _liberar_asignacion(cur, codigo: str) -> None:
    cur.execute(
        """UPDATE asignaciones SET activa = false, liberado_en = now()
           WHERE envio_codigo = %s AND activa RETURNING repartidor_id""",
        (codigo,),
    )
    for fila in cur.fetchall():
        cur.execute(
            "UPDATE repartidores SET carga_actual = carga_actual - 1 WHERE id = %s",
            (fila["repartidor_id"],),
        )


def _subir_version(cur, codigo, version_actual, nuevo_estado=None):
    version = version_actual + 1
    if nuevo_estado:
        cur.execute(
            """UPDATE envios SET estado = %s, version = %s, actualizado_en = now()
               WHERE codigo = %s RETURNING *""",
            (nuevo_estado, version, codigo),
        )
    else:
        cur.execute(
            "UPDATE envios SET version = %s, actualizado_en = now() WHERE codigo = %s RETURNING *",
            (version, codigo),
        )
    return version, cur.fetchone()


def _acciones(fila, actor) -> list[str]:
    hay = fila.get("repartidor_id") is not None
    return estados.acciones_permitidas(
        fila["estado"], actor.rol,
        previo_incidencia=fila.get("previo_incidencia"),
        hay_asignacion=hay,
        aceptada=bool(fila.get("asignacion_aceptada")),
        es_mi_asignacion=hay and str(fila.get("repartidor_usuario_id")) == str(actor.id),
    )


# ---------- Catálogos ----------

def listar_zonas(pool, incluir_inactivas: bool = False):
    with transaccion(pool) as cur:
        filtro = "" if incluir_inactivas else "WHERE activa"
        cur.execute(f"SELECT id, nombre, activa FROM zonas {filtro} ORDER BY nombre")
        return cur.fetchall()


def listar_tipos_servicio(pool):
    with transaccion(pool) as cur:
        cur.execute("SELECT id, nombre, tarifa_base, tiempo_estimado_min FROM tipos_servicio ORDER BY id")
        return cur.fetchall()


def listar_clientes(pool):
    with transaccion(pool) as cur:
        cur.execute("SELECT id, nombre FROM usuarios WHERE rol = 'cliente' ORDER BY nombre")
        return cur.fetchall()


def listar_repartidores(pool):
    with transaccion(pool) as cur:
        cur.execute("""
            SELECT r.id, u.nombre, u.id AS usuario_id, r.zona_id, z.nombre AS zona,
                   r.capacidad_max, r.carga_actual, r.activo
            FROM repartidores r
            JOIN usuarios u ON u.id = r.usuario_id
            JOIN zonas z ON z.id = r.zona_id
            ORDER BY u.nombre
        """)
        return cur.fetchall()


def actualizar_capacidad(pool, repartidor_id, nueva_capacidad):
    with transaccion(pool) as cur:
        cur.execute("SELECT carga_actual FROM repartidores WHERE id = %s FOR UPDATE", (str(repartidor_id),))
        fila = cur.fetchone()
        if fila is None:
            raise NoEncontrado("repartidor no encontrado")
        if nueva_capacidad < fila["carga_actual"]:
            raise Conflicto("la capacidad no puede ser menor que la carga actual",
                            {"carga_actual": fila["carga_actual"]})
        cur.execute(
            "UPDATE repartidores SET capacidad_max = %s WHERE id = %s RETURNING *",
            (nueva_capacidad, str(repartidor_id)),
        )
        return cur.fetchone()


def guardar_zona(pool, datos, zona_id=None):
    try:
        with transaccion(pool) as cur:
            if zona_id:
                cur.execute("UPDATE zonas SET nombre = %s, activa = %s WHERE id = %s RETURNING *",
                            (datos.nombre, datos.activa, zona_id))
            else:
                cur.execute("INSERT INTO zonas (nombre, activa) VALUES (%s, %s) RETURNING *",
                            (datos.nombre, datos.activa))
            fila = cur.fetchone()
            if fila is None:
                raise NoEncontrado(f"la zona {zona_id} no existe")
            return fila
    except pgerr.UniqueViolation:
        raise Conflicto(f"ya existe una zona llamada '{datos.nombre}'")


def guardar_tipo_servicio(pool, datos, ts_id=None):
    try:
        with transaccion(pool) as cur:
            if ts_id:
                cur.execute(
                    """UPDATE tipos_servicio SET nombre = %s, tarifa_base = %s, tiempo_estimado_min = %s
                       WHERE id = %s RETURNING *""",
                    (datos.nombre, datos.tarifa_base, datos.tiempo_estimado_min, ts_id))
            else:
                cur.execute(
                    """INSERT INTO tipos_servicio (nombre, tarifa_base, tiempo_estimado_min)
                       VALUES (%s, %s, %s) RETURNING *""",
                    (datos.nombre, datos.tarifa_base, datos.tiempo_estimado_min))
            fila = cur.fetchone()
            if fila is None:
                raise NoEncontrado(f"el tipo de servicio {ts_id} no existe")
            return fila
    except pgerr.UniqueViolation:
        raise Conflicto(f"ya existe un tipo de servicio llamado '{datos.nombre}'")


# ---------- Envíos ----------

def _resolver_cliente(cur, datos, actor):
    """El cliente registra a su nombre; operador/admin deben indicar para qué cliente."""
    if actor.rol == "cliente":
        if datos.cliente_id is not None and str(datos.cliente_id) != str(actor.id):
            raise NoPermitido("un cliente solo puede registrar envíos a su propio nombre")
        cliente_id = str(actor.id)
    else:
        if datos.cliente_id is None:
            raise Validacion("indique cliente_id: el operador registra el envío a nombre de un cliente")
        cliente_id = str(datos.cliente_id)
    cur.execute("SELECT 1 FROM usuarios WHERE id = %s AND rol = 'cliente'", (cliente_id,))
    if cur.fetchone() is None:
        if actor.rol == "cliente":
            raise NoPermitido("el usuario no está registrado como cliente")
        raise Validacion("el cliente indicado no existe")
    return cliente_id


def crear_envio(pool, datos, actor, clave_idempotencia):
    """Devuelve (codigo, creado). Un reintento con la misma Idempotency-Key devuelve el mismo envío."""
    try:
        with transaccion(pool) as cur:
            cliente_id = _resolver_cliente(cur, datos, actor)
            if clave_idempotencia:
                cur.execute("SELECT codigo, cliente_id FROM envios WHERE idempotency_key = %s",
                            (clave_idempotencia,))
                previo = cur.fetchone()
                if previo is not None:
                    if str(previo["cliente_id"]) != cliente_id:
                        raise Conflicto("la Idempotency-Key ya se usó para otro envío")
                    return previo["codigo"], False

            cur.execute("SELECT activa FROM zonas WHERE id = %s", (datos.zona_id,))
            zona = cur.fetchone()
            if zona is None or not zona["activa"]:
                raise Validacion("la zona no existe o está desactivada", {"zona_id": datos.zona_id})
            cur.execute("SELECT 1 FROM tipos_servicio WHERE id = %s", (datos.tipo_servicio_id,))
            if cur.fetchone() is None:
                raise Validacion("el tipo de servicio no existe", {"tipo_servicio_id": datos.tipo_servicio_id})

            codigo = "ENV-" + uuid.uuid4().hex[:8].upper()
            cur.execute(
                """INSERT INTO envios (codigo, cliente_id, remitente, destinatario,
                       origen_lat, origen_lng, destino_lat, destino_lng,
                       origen_direccion, destino_direccion,
                       zona_id, tipo_servicio_id, idempotency_key)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    codigo, cliente_id, datos.remitente, datos.destinatario,
                    datos.origen.lat, datos.origen.lng, datos.destino.lat, datos.destino.lng,
                    datos.origen_direccion or None, datos.destino_direccion or None,
                    datos.zona_id, datos.tipo_servicio_id, clave_idempotencia,
                ),
            )
            nota = "Envío registrado" if actor.rol == "cliente" else f"Envío registrado por {actor.rol}"
            _registrar_evento(cur, codigo, f"{codigo}-0001", "creacion", 1, None, "creado", actor, nota)
            return codigo, True
    except pgerr.UniqueViolation as exc:
        # Dos peticiones simultáneas con la misma clave: gana una y la otra devuelve el mismo envío
        if exc.diag.constraint_name == RESTRICCION_IDEMPOTENCIA:
            with transaccion(pool) as cur:
                cur.execute("SELECT codigo FROM envios WHERE idempotency_key = %s", (clave_idempotencia,))
                return cur.fetchone()["codigo"], False
        raise


# Consulta base de envío con su asignación activa y el estado previo a una incidencia
_SELECT_ENVIO = """
    SELECT e.codigo, e.cliente_id, uc.nombre AS cliente, e.remitente, e.destinatario,
           e.origen_direccion, e.destino_direccion,
           e.origen_lat, e.origen_lng, e.destino_lat, e.destino_lng,
           e.estado, e.version, e.creado_en, e.actualizado_en,
           e.zona_id, z.nombre AS zona, e.tipo_servicio_id, t.nombre AS tipo_servicio,
           t.tarifa_base, t.tiempo_estimado_min,
           a.repartidor_id, ur.nombre AS repartidor_nombre, r.usuario_id AS repartidor_usuario_id,
           (a.aceptada_en IS NOT NULL) AS asignacion_aceptada,
           inc.previo AS previo_incidencia, ult.nombre AS ultimo_repartidor
    FROM envios e
    JOIN zonas z ON z.id = e.zona_id
    JOIN tipos_servicio t ON t.id = e.tipo_servicio_id
    JOIN usuarios uc ON uc.id = e.cliente_id
    LEFT JOIN asignaciones a ON a.envio_codigo = e.codigo AND a.activa
    LEFT JOIN repartidores r ON r.id = a.repartidor_id
    LEFT JOIN usuarios ur ON ur.id = r.usuario_id
    LEFT JOIN LATERAL (
        SELECT o.payload->>'estado_anterior' AS previo FROM outbox o
        WHERE e.estado = 'incidencia' AND o.envio_codigo = e.codigo
          AND o.payload->>'estado_nuevo' = 'incidencia'
        ORDER BY o.id DESC LIMIT 1
    ) inc ON true
    LEFT JOIN LATERAL (
        SELECT u3.nombre FROM asignaciones a3
        JOIN repartidores r3 ON r3.id = a3.repartidor_id JOIN usuarios u3 ON u3.id = r3.usuario_id
        WHERE a3.envio_codigo = e.codigo ORDER BY a3.asignado_en DESC LIMIT 1
    ) ult ON true
"""

# El repartidor solo ve envíos que le fueron asignados alguna vez
_FILTRO_REPARTIDOR = """EXISTS (SELECT 1 FROM asignaciones a2 JOIN repartidores r2 ON r2.id = a2.repartidor_id
                                WHERE a2.envio_codigo = e.codigo AND r2.usuario_id = %s)"""


def _filtro_visibilidad(actor):
    if actor.rol == "cliente":
        return "e.cliente_id = %s", [str(actor.id)]
    if actor.rol == "repartidor":
        return _FILTRO_REPARTIDOR, [str(actor.id)]
    return None, []


def verificar_acceso(pool, codigo: str, actor) -> None:
    condicion, params = _filtro_visibilidad(actor)
    sql = "SELECT 1 FROM envios e WHERE e.codigo = %s" + (f" AND {condicion}" if condicion else "")
    with transaccion(pool) as cur:
        cur.execute(sql, [codigo] + params)
        visible = cur.fetchone() is not None
    if not visible:
        raise NoEncontrado(f"el envío {codigo} no existe")


def obtener_envio(pool, codigo: str, actor):
    verificar_acceso(pool, codigo, actor)
    with transaccion(pool) as cur:
        cur.execute(_SELECT_ENVIO + " WHERE e.codigo = %s", (codigo,))
        fila = cur.fetchone()
    fila["acciones"] = _acciones(fila, actor)
    fila.pop("repartidor_usuario_id", None)
    return fila


def buscar_envios(pool, actor, q, estado, zona_id, desde, hasta, limite, desplazamiento):
    condiciones, params = [], []
    condicion, extra = _filtro_visibilidad(actor)
    if condicion:
        condiciones.append(condicion)
        params += extra
    if q:
        condiciones.append("(e.destinatario ILIKE %s OR e.codigo ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    if estado:
        if estado not in estados.ESTADOS:
            raise Validacion(f"estado desconocido: '{estado}'", {"estados_validos": list(estados.ESTADOS)})
        condiciones.append("e.estado = %s")
        params.append(estado)
    if zona_id:
        condiciones.append("e.zona_id = %s")
        params.append(zona_id)
    if desde:
        condiciones.append("e.creado_en >= %s")
        params.append(desde)
    if hasta:
        condiciones.append("e.creado_en < %s")
        params.append(hasta + timedelta(days=1))

    where = " AND ".join(condiciones) or "TRUE"
    # Solo se interpolan fragmentos fijos; los valores van como parámetros
    with transaccion(pool) as cur:
        cur.execute(f"SELECT count(*) AS n FROM envios e WHERE {where}", params)
        total = cur.fetchone()["n"]
        cur.execute(_SELECT_ENVIO + f" WHERE {where} ORDER BY e.creado_en DESC LIMIT %s OFFSET %s",
                    params + [limite, desplazamiento])
        filas = cur.fetchall()
    resultados = []
    for fila in filas:
        resultados.append({
            "codigo": fila["codigo"], "cliente": fila["cliente"], "destinatario": fila["destinatario"],
            "estado": fila["estado"], "creado_en": fila["creado_en"],
            "zona_id": fila["zona_id"], "zona": fila["zona"], "tipo_servicio": fila["tipo_servicio"],
            "repartidor_id": fila["repartidor_id"], "repartidor_nombre": fila["repartidor_nombre"],
            "asignacion_aceptada": fila["asignacion_aceptada"],
            "acciones": _acciones(fila, actor),
        })
    return resultados, total


def cambiar_estado(pool, codigo, nuevo, evento_id, actor, nota=None, ubicacion=None, extra=None):
    """Transición genérica. Devuelve {'ya_aplicado': bool, ...}; ya_aplicado=True si el evento ya existía."""
    try:
        with transaccion(pool) as cur:
            if _ya_aplicado(cur, evento_id):
                return {"ya_aplicado": True, "envio_codigo": codigo}
            envio = _bloquear_envio(cur, codigo)
            _validar_reglas(cur, envio, nuevo, actor)

            version, actualizado = _subir_version(cur, codigo, envio["version"], nuevo)
            if nuevo in estados.LIBERAN_CAPACIDAD:
                _liberar_asignacion(cur, codigo)

            payload_extra = dict(extra or {})
            geo = _ubicacion_geojson(ubicacion)
            if geo:
                payload_extra["ubicacion"] = geo
            _registrar_evento(
                cur, codigo, evento_id, "cambio_estado", version,
                envio["estado"], nuevo, actor, nota, payload_extra,
            )
            return {"ya_aplicado": False, "envio": actualizado}
    except pgerr.UniqueViolation as exc:
        # Otra petición con el mismo evento_id llegó primero: no se aplica dos veces
        if exc.diag.constraint_name == RESTRICCION_EVENTO_DUPLICADO:
            return {"ya_aplicado": True, "envio_codigo": codigo}
        raise


def registrar_entrega(pool, mongo_db, codigo, datos, actor):
    """Saga simple: evidencia en MongoDB primero (idempotente), luego entregado en PostgreSQL."""
    with transaccion(pool) as cur:
        if _ya_aplicado(cur, datos.evento_id):
            return {"ya_aplicado": True, "envio_codigo": codigo}
        cur.execute("SELECT codigo, cliente_id, estado, version FROM envios WHERE codigo = %s", (codigo,))
        envio = cur.fetchone()
        if envio is None:
            raise NoEncontrado(f"el envío {codigo} no existe")
        # Validar antes de escribir evidencia: no dejamos pruebas de una entrega inválida
        _validar_reglas(cur, envio, "entregado", actor)

    evidencia = {
        "envio_codigo": codigo,
        "receptor_nombre": datos.receptor_nombre,
        "observacion": datos.observacion,
        "registrado_por": str(actor.id),
        "registrado_en": ahora(),
        "extras": {"firma_simulada": datos.firma_simulada},
    }
    if datos.foto_url:
        evidencia["foto"] = {"tipo": "referencia", "valor": datos.foto_url}
    mongo_db.evidencias_entrega.update_one(
        {"envio_codigo": codigo}, {"$set": evidencia}, upsert=True
    )

    return cambiar_estado(
        pool, codigo, "entregado", datos.evento_id, actor,
        nota="Entrega confirmada con evidencia",
        extra={"evidencia_ref": codigo},
    )


def _bloquear_repartidores(cur, ids):
    """Bloquea filas de repartidores siempre en el mismo orden para evitar interbloqueos."""
    ids = sorted({str(i) for i in ids})
    cur.execute(
        """SELECT r.id, r.capacidad_max, r.carga_actual, r.activo, u.nombre
           FROM repartidores r JOIN usuarios u ON u.id = r.usuario_id
           WHERE r.id = ANY(%s::uuid[]) ORDER BY r.id FOR UPDATE OF r""",
        (ids,),
    )
    return {str(f["id"]): f for f in cur.fetchall()}


def _verificar_capacidad(rep):
    if rep is None:
        raise NoEncontrado("el repartidor no existe")
    if not rep["activo"]:
        raise Conflicto("el repartidor está inactivo")
    if rep["carga_actual"] >= rep["capacidad_max"]:
        raise CapacidadExcedida(
            "el repartidor alcanzó su capacidad máxima",
            {"capacidad_max": rep["capacidad_max"], "carga_actual": rep["carga_actual"]},
        )


def asignar(pool, codigo, repartidor_id, evento_id, actor):
    try:
        with transaccion(pool) as cur:
            if _ya_aplicado(cur, evento_id):
                return {"ya_aplicado": True, "envio_codigo": codigo}
            envio = _bloquear_envio(cur, codigo)
            estados.validar_transicion(envio["estado"], "asignado", actor.rol)

            # Bloquear la fila del repartidor serializa asignaciones concurrentes sobre su capacidad
            rep = _bloquear_repartidores(cur, [repartidor_id]).get(str(repartidor_id))
            _verificar_capacidad(rep)

            cur.execute(
                "INSERT INTO asignaciones (envio_codigo, repartidor_id) VALUES (%s, %s) RETURNING id",
                (codigo, str(repartidor_id)),
            )
            asignacion_id = cur.fetchone()["id"]
            cur.execute("UPDATE repartidores SET carga_actual = carga_actual + 1 WHERE id = %s",
                        (str(repartidor_id),))
            version, actualizado = _subir_version(cur, codigo, envio["version"], "asignado")
            _registrar_evento(
                cur, codigo, evento_id, "asignacion", version,
                envio["estado"], "asignado", actor,
                f"Asignado a {rep['nombre']}",
                {"repartidor_id": str(repartidor_id)},
            )
            return {"ya_aplicado": False, "asignacion_id": str(asignacion_id), "envio": actualizado}
    except pgerr.UniqueViolation as exc:
        if exc.diag.constraint_name == RESTRICCION_ASIGNACION_ACTIVA:
            raise AsignacionActiva("el envío ya tiene una asignación activa")
        if exc.diag.constraint_name == RESTRICCION_EVENTO_DUPLICADO:
            return {"ya_aplicado": True, "envio_codigo": codigo}
        raise


def aceptar_asignacion(pool, codigo, evento_id, actor):
    """El repartidor confirma que toma el envío; sin esto no puede retirarlo."""
    try:
        with transaccion(pool) as cur:
            if _ya_aplicado(cur, evento_id):
                return {"ya_aplicado": True, "envio_codigo": codigo}
            envio = _bloquear_envio(cur, codigo)
            asignacion = _asignacion_activa(cur, codigo)
            if asignacion is None:
                raise TransicionInvalida("el envío no tiene una asignación activa")
            if actor.rol == "repartidor" and str(asignacion["usuario_id"]) != str(actor.id):
                raise NoPermitido("el envío no está asignado a este repartidor")
            if asignacion["aceptada_en"] is not None:
                raise Conflicto("la asignación ya fue aceptada")
            cur.execute("UPDATE asignaciones SET aceptada_en = now() WHERE id = %s", (asignacion["id"],))
            version, actualizado = _subir_version(cur, codigo, envio["version"])
            _registrar_evento(
                cur, codigo, evento_id, "aceptacion_asignacion", version,
                envio["estado"], envio["estado"], actor,
                "El repartidor aceptó la asignación",
                {"repartidor_id": str(asignacion["repartidor_id"])},
            )
            return {"ya_aplicado": False, "envio": actualizado}
    except pgerr.UniqueViolation as exc:
        if exc.diag.constraint_name == RESTRICCION_EVENTO_DUPLICADO:
            return {"ya_aplicado": True, "envio_codigo": codigo}
        raise


def reasignar(pool, codigo, nuevo_repartidor_id, evento_id, actor):
    try:
        with transaccion(pool) as cur:
            if _ya_aplicado(cur, evento_id):
                return {"ya_aplicado": True, "envio_codigo": codigo}
            envio = _bloquear_envio(cur, codigo)
            if envio["estado"] not in estados.REASIGNABLES:
                raise TransicionInvalida(
                    f"un envío en '{envio['estado']}' no se puede reasignar",
                    {"estados_reasignables": sorted(estados.REASIGNABLES)},
                )
            anterior = _asignacion_activa(cur, codigo)
            if anterior is None:
                raise TransicionInvalida("el envío no tiene una asignación activa")
            if str(anterior["repartidor_id"]) == str(nuevo_repartidor_id):
                raise Conflicto("el envío ya está asignado a ese repartidor")

            reps = _bloquear_repartidores(cur, [anterior["repartidor_id"], nuevo_repartidor_id])
            nuevo = reps.get(str(nuevo_repartidor_id))
            _verificar_capacidad(nuevo)

            _liberar_asignacion(cur, codigo)
            cur.execute("INSERT INTO asignaciones (envio_codigo, repartidor_id) VALUES (%s, %s)",
                        (codigo, str(nuevo_repartidor_id)))
            cur.execute("UPDATE repartidores SET carga_actual = carga_actual + 1 WHERE id = %s",
                        (str(nuevo_repartidor_id),))
            version, actualizado = _subir_version(cur, codigo, envio["version"])
            _registrar_evento(
                cur, codigo, evento_id, "reasignacion", version, envio["estado"], envio["estado"], actor,
                f"Reasignado a {nuevo['nombre']}",
                {"repartidor_id": str(nuevo_repartidor_id), "repartidor_anterior": str(anterior["repartidor_id"])},
            )
            return {"ya_aplicado": False, "envio": actualizado}
    except pgerr.UniqueViolation as exc:
        if exc.diag.constraint_name == RESTRICCION_EVENTO_DUPLICADO:
            return {"ya_aplicado": True, "envio_codigo": codigo}
        raise


# ---------- Lectura desde MongoDB (con respaldo en el outbox) ----------

def _nombres_actores(cur, eventos):
    ids = {e.get("actor", {}).get("id") for e in eventos if e.get("actor")}
    if not ids:
        return eventos
    cur.execute("SELECT id::text AS id, nombre FROM usuarios WHERE id::text = ANY(%s)", (list(ids),))
    nombres = {f["id"]: f["nombre"] for f in cur.fetchall()}
    for e in eventos:
        if e.get("actor"):
            e["actor"]["nombre"] = nombres.get(e["actor"]["id"], "desconocido")
    return eventos


def leer_seguimiento(pool, mongo_db, codigo, actor):
    verificar_acceso(pool, codigo, actor)
    with transaccion(pool) as cur:
        cur.execute("SELECT estado FROM envios WHERE codigo = %s", (codigo,))
        estado_actual = cur.fetchone()["estado"]
        cur.execute(
            "SELECT count(*) AS n FROM outbox WHERE envio_codigo = %s AND publicado_en IS NULL",
            (codigo,),
        )
        pendientes = cur.fetchone()["n"]

    try:
        eventos = list(
            mongo_db.eventos_seguimiento.find({"envio_codigo": codigo}, {"_id": 0}).sort("secuencia", 1)
        )
        fuente_historial = "mongodb"
    except Exception:
        # MongoDB caído: el outbox es el historial oficial y sirve de respaldo de lectura
        with transaccion(pool) as cur:
            cur.execute("SELECT payload FROM outbox WHERE envio_codigo = %s ORDER BY id", (codigo,))
            eventos = [f["payload"] for f in cur.fetchall()]
        fuente_historial = "postgresql_outbox"
        pendientes = 0

    with transaccion(pool) as cur:
        _nombres_actores(cur, eventos)

    return {
        "envio_codigo": codigo,
        "estado_actual": estado_actual,
        "fuente_estado_actual": "postgresql",
        "fuente_historial": fuente_historial,
        "pendientes_sincronizacion": pendientes,
        "eventos": eventos,
    }


def leer_evidencia(pool, mongo_db, codigo, actor):
    verificar_acceso(pool, codigo, actor)
    evidencia = mongo_db.evidencias_entrega.find_one({"envio_codigo": codigo}, {"_id": 0})
    if evidencia is None:
        raise NoEncontrado(f"el envío {codigo} no tiene evidencia de entrega")
    return evidencia


def estado_sincronizacion(pool, mongo_db):
    """Pantalla técnica: avance del outbox hacia MongoDB, sin exponer credenciales."""
    with transaccion(pool) as cur:
        cur.execute("""
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE publicado_en IS NULL) AS pendientes,
                   max(publicado_en) AS ultimo_publicado,
                   min(creado_en) FILTER (WHERE publicado_en IS NULL) AS pendiente_mas_antiguo,
                   coalesce(max(intentos) FILTER (WHERE publicado_en IS NULL), 0) AS max_intentos
            FROM outbox
        """)
        outbox = cur.fetchone()
    try:
        en_mongo = mongo_db.eventos_seguimiento.count_documents({})
        mongo = "ok"
    except Exception:
        en_mongo = None
        mongo = "caido"
    retraso = None
    if outbox["pendiente_mas_antiguo"] is not None:
        retraso = round((datetime.now(timezone.utc) - outbox["pendiente_mas_antiguo"]).total_seconds(), 1)
    return {
        "outbox_total": outbox["total"],
        "outbox_pendientes": outbox["pendientes"],
        "ultimo_publicado_en": outbox["ultimo_publicado"],
        "retraso_segundos": retraso,
        "max_intentos_pendientes": outbox["max_intentos"],
        "eventos_en_mongodb": en_mongo,
        "mongodb": mongo,
    }
