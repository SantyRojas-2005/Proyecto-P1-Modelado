"""Endpoints REST. Solo traducen HTTP a servicios; las reglas viven en servicios.py y estados.py."""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from . import servicios
from .errors import NoPermitido, Validacion
from .estados import validar_pedido_generico
from .infra import Infraestructura
from .schemas import (AceptacionAsignacion, Asignacion, CambioEstado, CapacidadUpdate, Coordenada,
                      Entrega, NuevoEnvio, TipoServicioIn, ZonaIn)

router = APIRouter()

ROLES = {"cliente", "operador", "repartidor", "admin"}
POSICION_TTL = 120   # segundos; dato efímero de posición
CONEXION_TTL = 60    # segundos; latido de conexión


def _err(descripcion: str) -> dict:
    return {"description": descripcion, "content": {"application/json": {"example": {
        "error": {"codigo": "CODIGO_ESTABLE", "mensaje": "texto legible", "detalles": {}}}}}}


E403 = {403: _err("NO_PERMITIDO: el rol o el usuario no puede realizar la operación")}
E404 = {404: _err("NO_ENCONTRADO: el recurso no existe o no es visible para este usuario")}
E409 = {409: _err("CONFLICTO, TRANSICION_INVALIDA, CAPACIDAD_EXCEDIDA o ENVIO_YA_ASIGNADO")}
E503 = {503: _err("DEPENDENCIA_NO_DISPONIBLE: una base de datos no responde (reintentable)")}
ESCRITURA = {**E403, **E404, **E409, **E503}


class Actor:
    def __init__(self, id: UUID, rol: str):
        self.id = id
        self.rol = rol


def get_infra(request: Request) -> Infraestructura:
    return request.app.state.infra


def get_actor(
    x_actor_id: UUID = Header(..., description="UUID del usuario que realiza la petición"),
    x_actor_rol: str = Header(..., description="cliente | operador | repartidor | admin"),
) -> Actor:
    if x_actor_rol not in ROLES:
        raise Validacion(f"rol inválido: '{x_actor_rol}'", {"roles_validos": sorted(ROLES)})
    return Actor(x_actor_id, x_actor_rol)


def exigir(actor: Actor, *roles: str) -> None:
    if actor.rol not in roles:
        raise NoPermitido(f"el rol '{actor.rol}' no puede realizar esta operación")


def _ahora_iso() -> str:
    return servicios.ahora()


# ---------- Salud y catálogos ----------

@router.get("/health", tags=["Salud"], summary="Estado de la API y sus dependencias",
            responses={503: {"description": "Alguna dependencia caída (estado 'degradado')"}})
def salud(response: Response, infra: Infraestructura = Depends(get_infra)):
    dependencias = infra.salud()
    ok = all(v == "ok" for v in dependencias.values())
    response.status_code = 200 if ok else 503
    return {"estado": "ok" if ok else "degradado", "dependencias": dependencias}


@router.get("/zonas", tags=["Catálogos"], summary="Zonas activas (público)")
def zonas(infra: Infraestructura = Depends(get_infra)):
    return servicios.listar_zonas(infra.pg)


@router.get("/admin/zonas", tags=["Administración"], summary="Todas las zonas, incluidas las inactivas",
            responses=E403)
def zonas_admin(actor: Actor = Depends(get_actor), infra: Infraestructura = Depends(get_infra)):
    exigir(actor, "admin")
    return servicios.listar_zonas(infra.pg, incluir_inactivas=True)


@router.get("/tipos-servicio", tags=["Catálogos"], summary="Tipos de servicio")
def tipos_servicio(infra: Infraestructura = Depends(get_infra)):
    return servicios.listar_tipos_servicio(infra.pg)


@router.get("/repartidores", tags=["Catálogos"], summary="Repartidores con capacidad y carga", responses=E403)
def listar_repartidores(actor: Actor = Depends(get_actor), infra: Infraestructura = Depends(get_infra)):
    exigir(actor, "operador", "repartidor", "admin")
    return servicios.listar_repartidores(infra.pg)


@router.get("/clientes", tags=["Catálogos"], summary="Clientes (para que operación registre a su nombre)",
            responses=E403)
def listar_clientes(actor: Actor = Depends(get_actor), infra: Infraestructura = Depends(get_infra)):
    exigir(actor, "operador", "admin")
    return servicios.listar_clientes(infra.pg)


# ---------- Envíos ----------

@router.post("/envios", status_code=201, tags=["Envíos"], summary="Registrar un envío (devuelve el comprobante)",
             responses={200: {"description": "Reintento con la misma Idempotency-Key: mismo envío"},
                        422: _err("VALIDACION: datos, zona o tipo de servicio inválidos"), **E403, **E409, **E503})
def crear_envio(
    datos: NuevoEnvio,
    response: Response,
    actor: Actor = Depends(get_actor),
    idempotency_key: str | None = Header(default=None, max_length=64,
                                         description="Evita duplicados si se reintenta"),
    infra: Infraestructura = Depends(get_infra),
):
    exigir(actor, "cliente", "operador", "admin")
    codigo, creado = servicios.crear_envio(infra.pg, datos, actor, idempotency_key)
    if not creado:
        response.status_code = 200
    return servicios.obtener_envio(infra.pg, codigo, actor)


@router.get("/envios", tags=["Búsqueda"], summary="Búsqueda operativa con filtros (PostgreSQL, pg_trgm)")
def buscar_envios(
    q: str | None = Query(default=None, max_length=100, description="Código o destinatario"),
    estado: str | None = None,
    zona_id: int | None = Query(default=None, gt=0),
    desde: date | None = None,
    hasta: date | None = None,
    limite: int = Query(default=20, ge=1, le=100),
    desplazamiento: int = Query(default=0, ge=0),
    actor: Actor = Depends(get_actor),
    infra: Infraestructura = Depends(get_infra),
):
    resultados, total = servicios.buscar_envios(
        infra.pg, actor, q, estado, zona_id, desde, hasta, limite, desplazamiento
    )
    return {"resultados": resultados, "total": total, "fuente": "postgresql",
            "limite": limite, "desplazamiento": desplazamiento}


@router.get("/envios/{codigo}", tags=["Envíos"], summary="Detalle y estado actual del envío", responses=E404)
def detalle_envio(codigo: str, actor: Actor = Depends(get_actor), infra: Infraestructura = Depends(get_infra)):
    return servicios.obtener_envio(infra.pg, codigo, actor)


@router.get("/envios/{codigo}/seguimiento", tags=["Seguimiento"],
            summary="Línea de tiempo (MongoDB; respaldo desde el outbox de PostgreSQL)", responses=E404)
def seguimiento(codigo: str, actor: Actor = Depends(get_actor), infra: Infraestructura = Depends(get_infra)):
    return servicios.leer_seguimiento(infra.pg, infra.mongo_db, codigo, actor)


@router.get("/envios/{codigo}/evidencia", tags=["Seguimiento"], summary="Evidencia de entrega (MongoDB)",
            responses={**E404, **E503})
def evidencia(codigo: str, actor: Actor = Depends(get_actor), infra: Infraestructura = Depends(get_infra)):
    return servicios.leer_evidencia(infra.pg, infra.mongo_db, codigo, actor)


@router.patch("/envios/{codigo}/estado", tags=["Envíos"],
              summary="Cambiar estado (aceptado, retirado, en tránsito, incidencia, cancelado)",
              responses={**ESCRITURA, 422: _err("VALIDACION: estado desconocido")})
def cambiar_estado(
    codigo: str,
    datos: CambioEstado,
    actor: Actor = Depends(get_actor),
    infra: Infraestructura = Depends(get_infra),
):
    validar_pedido_generico(datos.nuevo_estado)
    return servicios.cambiar_estado(
        infra.pg, codigo, datos.nuevo_estado, datos.evento_id, actor,
        nota=datos.nota, ubicacion=datos.ubicacion,
    )


@router.post("/envios/{codigo}/aceptar-asignacion", tags=["Despacho"],
             summary="El repartidor acepta la asignación", responses=ESCRITURA)
def aceptar_asignacion(
    codigo: str,
    datos: AceptacionAsignacion,
    actor: Actor = Depends(get_actor),
    infra: Infraestructura = Depends(get_infra),
):
    exigir(actor, "repartidor", "admin")
    return servicios.aceptar_asignacion(infra.pg, codigo, datos.evento_id, actor)


@router.post("/envios/{codigo}/entrega", tags=["Envíos"], summary="Confirmar entrega con evidencia",
             responses=ESCRITURA)
def registrar_entrega(
    codigo: str,
    datos: Entrega,
    actor: Actor = Depends(get_actor),
    infra: Infraestructura = Depends(get_infra),
):
    exigir(actor, "repartidor", "admin")
    return servicios.registrar_entrega(infra.pg, infra.mongo_db, codigo, datos, actor)


# ---------- Asignaciones ----------

@router.post("/asignaciones", status_code=201, tags=["Despacho"], summary="Asignar un repartidor a un envío",
             responses={200: {"description": "Reintento con el mismo evento_id"}, **ESCRITURA})
def asignar(
    datos: Asignacion,
    response: Response,
    actor: Actor = Depends(get_actor),
    infra: Infraestructura = Depends(get_infra),
):
    exigir(actor, "operador", "admin")
    resultado = servicios.asignar(infra.pg, datos.envio_codigo, datos.repartidor_id, datos.evento_id, actor)
    if resultado.get("ya_aplicado"):
        response.status_code = 200
    return resultado


@router.post("/asignaciones/reasignar", tags=["Despacho"], summary="Reasignar un envío a otro repartidor",
             responses=ESCRITURA)
def reasignar_envio(
    datos: Asignacion, actor: Actor = Depends(get_actor), infra: Infraestructura = Depends(get_infra)
):
    exigir(actor, "operador", "admin")
    return servicios.reasignar(infra.pg, datos.envio_codigo, datos.repartidor_id, datos.evento_id, actor)


# ---------- Estado temporal (Redis) ----------

def _verificar_repartidor_propio(infra, actor, repartidor_id):
    # Un repartidor solo reporta su propia posición
    if actor.rol != "repartidor":
        return
    for r in servicios.listar_repartidores(infra.pg):
        if str(r["id"]) == str(repartidor_id):
            if str(r["usuario_id"]) != str(actor.id):
                raise NoPermitido("solo puede reportar su propia posición")
            return
    raise NoPermitido("repartidor desconocido")


@router.post("/repartidores/{repartidor_id}/posicion", tags=["Estado temporal"],
             summary="Reportar posición simulada (Redis, expira)", responses={**E403, **E503})
def reportar_posicion(
    repartidor_id: UUID,
    datos: Coordenada,
    actor: Actor = Depends(get_actor),
    infra: Infraestructura = Depends(get_infra),
):
    exigir(actor, "repartidor", "admin")
    _verificar_repartidor_propio(infra, actor, repartidor_id)
    clave = f"repartidor:{repartidor_id}:posicion"
    with infra.redis.pipeline() as pipe:
        pipe.hset(clave, mapping={"lat": datos.lat, "lng": datos.lng, "ts": _ahora_iso()})
        pipe.expire(clave, POSICION_TTL)
        pipe.execute()
    return {"vigente": True, "expira_en_segundos": POSICION_TTL}


@router.post("/repartidores/{repartidor_id}/latido", tags=["Estado temporal"],
             summary="Latido de conexión (Redis, expira)", responses={**E403, **E503})
def latido(
    repartidor_id: UUID,
    actor: Actor = Depends(get_actor),
    infra: Infraestructura = Depends(get_infra),
):
    exigir(actor, "repartidor", "admin")
    _verificar_repartidor_propio(infra, actor, repartidor_id)
    infra.redis.set(f"repartidor:{repartidor_id}:conexion", "online", ex=CONEXION_TTL)
    return {"conectado": True, "expira_en_segundos": CONEXION_TTL}


@router.get("/repartidores/{repartidor_id}/estado-temporal", tags=["Estado temporal"],
            summary="Posición y conexión vigentes", responses={**E403, **E503})
def estado_temporal(
    repartidor_id: UUID,
    actor: Actor = Depends(get_actor),
    infra: Infraestructura = Depends(get_infra),
):
    exigir(actor, "operador", "repartidor", "admin")
    pos = infra.redis.hgetall(f"repartidor:{repartidor_id}:posicion")
    conectado = infra.redis.exists(f"repartidor:{repartidor_id}:conexion") == 1
    if pos:
        posicion = {"lat": float(pos["lat"]), "lng": float(pos["lng"]), "ts": pos["ts"], "vigente": True,
                    "expira_en_segundos": infra.redis.ttl(f"repartidor:{repartidor_id}:posicion")}
    else:
        posicion = {"vigente": False, "motivo": "posición expirada o nunca reportada"}
    return {
        "posicion": posicion,
        "conectado": conectado,
        "nota": "Dato efímero: no es comprobante de entrega",
    }


# ---------- Administración ----------

@router.patch("/repartidores/{id}/capacidad", tags=["Administración"], summary="Cambiar capacidad máxima",
              responses=ESCRITURA)
def actualizar_capacidad(
    id: UUID, datos: CapacidadUpdate, actor: Actor = Depends(get_actor), infra: Infraestructura = Depends(get_infra)
):
    exigir(actor, "admin")
    return servicios.actualizar_capacidad(infra.pg, id, datos.capacidad_max)


@router.post("/zonas", status_code=201, tags=["Administración"], summary="Crear zona", responses=ESCRITURA)
def crear_zona(datos: ZonaIn, actor: Actor = Depends(get_actor), infra: Infraestructura = Depends(get_infra)):
    exigir(actor, "admin")
    return servicios.guardar_zona(infra.pg, datos)


@router.put("/zonas/{id}", tags=["Administración"], summary="Editar o activar/desactivar zona",
            responses=ESCRITURA)
def editar_zona(id: int, datos: ZonaIn, actor: Actor = Depends(get_actor), infra: Infraestructura = Depends(get_infra)):
    exigir(actor, "admin")
    return servicios.guardar_zona(infra.pg, datos, id)


@router.post("/tipos-servicio", status_code=201, tags=["Administración"], summary="Crear tipo de servicio",
             responses=ESCRITURA)
def crear_tipo_servicio(datos: TipoServicioIn, actor: Actor = Depends(get_actor),
                        infra: Infraestructura = Depends(get_infra)):
    exigir(actor, "admin")
    return servicios.guardar_tipo_servicio(infra.pg, datos)


@router.put("/tipos-servicio/{id}", tags=["Administración"], summary="Editar tipo de servicio",
            responses=ESCRITURA)
def editar_tipo_servicio(id: int, datos: TipoServicioIn, actor: Actor = Depends(get_actor),
                         infra: Infraestructura = Depends(get_infra)):
    exigir(actor, "admin")
    return servicios.guardar_tipo_servicio(infra.pg, datos, id)


@router.get("/sincronizacion", tags=["Salud"], summary="Estado del sincronizador outbox → MongoDB",
            responses=E403)
def sincronizacion(actor: Actor = Depends(get_actor), infra: Infraestructura = Depends(get_infra)):
    exigir(actor, "operador", "admin")
    return servicios.estado_sincronizacion(infra.pg, infra.mongo_db)
