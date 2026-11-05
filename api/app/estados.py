"""Máquina de estados del envío. Reglas puras, sin acceso a base de datos."""
from .errors import NoPermitido, TransicionInvalida, Validacion

ESTADOS = (
    "creado", "aceptado", "asignado", "retirado",
    "en_transito", "incidencia", "entregado", "cancelado",
)

TRANSICIONES = {
    "creado": {"aceptado", "cancelado"},
    "aceptado": {"asignado", "cancelado"},
    "asignado": {"retirado", "incidencia", "cancelado"},
    "retirado": {"en_transito", "incidencia"},
    "en_transito": {"entregado", "incidencia"},
    # Desde una incidencia se retoma el paso pendiente o se cancela (ver validar_salida_incidencia)
    "incidencia": {"retirado", "en_transito", "cancelado"},
    "entregado": set(),
    "cancelado": set(),
}

# Quién puede pedir cada estado destino
ROLES_POR_DESTINO = {
    "aceptado": {"operador", "admin"},
    "asignado": {"operador", "admin"},
    "retirado": {"repartidor", "admin"},
    "en_transito": {"repartidor", "admin"},
    "incidencia": {"repartidor", "operador", "admin"},
    "entregado": {"repartidor", "admin"},
    "cancelado": {"cliente", "operador", "admin"},
}

# Estados que solo se alcanzan por su endpoint específico, nunca por PATCH genérico
POR_ENDPOINT = {
    "asignado": "POST /asignaciones",
    "entregado": "POST /envios/{codigo}/entrega",
}

# Al llegar aquí el envío deja de ocupar capacidad del repartidor
LIBERAN_CAPACIDAD = frozenset({"entregado", "cancelado"})

# El cliente solo cancela antes de que exista un repartidor asignado
CLIENTE_PUEDE_CANCELAR_EN = frozenset({"creado", "aceptado"})

# Estos pasos exigen que el repartidor asignado haya aceptado la asignación
REQUIEREN_ACEPTACION = frozenset({"retirado", "en_transito", "entregado"})

# Estados en los que operación puede cambiar de repartidor
REASIGNABLES = frozenset({"asignado", "retirado", "incidencia"})

# Nombre de la acción que ve la interfaz para cada destino
ACCION_POR_DESTINO = {"asignado": "asignar", "entregado": "entregar"}


def validar_pedido_generico(nuevo: str) -> None:
    """Valida un cambio pedido por PATCH /envios/{codigo}/estado antes de tocar la BD."""
    if nuevo not in ESTADOS:
        raise Validacion(f"estado desconocido: '{nuevo}'", {"estados_validos": list(ESTADOS)})
    if nuevo in POR_ENDPOINT:
        raise TransicionInvalida(
            f"'{nuevo}' no se cambia con PATCH; use {POR_ENDPOINT[nuevo]}"
        )


def validar_transicion(actual: str, nuevo: str, rol: str) -> None:
    """Valida que actual -> nuevo sea legal y que el rol pueda pedirlo."""
    permitidas = TRANSICIONES.get(actual, set())
    if nuevo not in permitidas:
        raise TransicionInvalida(
            f"no se puede pasar de '{actual}' a '{nuevo}'",
            {"estado_actual": actual, "permitidas": sorted(permitidas)},
        )
    if rol not in ROLES_POR_DESTINO[nuevo]:
        raise NoPermitido(f"el rol '{rol}' no puede pasar el envío a '{nuevo}'")
    if nuevo == "cancelado" and rol == "cliente" and actual not in CLIENTE_PUEDE_CANCELAR_EN:
        raise NoPermitido(
            "el cliente solo puede cancelar antes de la asignación; solicite la cancelación a operación",
            {"estado_actual": actual},
        )


def validar_salida_incidencia(previo: str | None, nuevo: str) -> None:
    """Al salir de una incidencia solo se retoma el paso que faltaba.

    previo es el estado que tenía el envío justo antes de la incidencia.
    """
    if nuevo == "cancelado" or previo is None:
        return
    if previo == "asignado" and nuevo != "retirado":
        raise TransicionInvalida(
            "el paquete aún no fue retirado: desde esta incidencia solo se puede pasar a 'retirado' o 'cancelado'",
            {"estado_previo": previo},
        )
    if previo in ("retirado", "en_transito") and nuevo != "en_transito":
        raise TransicionInvalida(
            "el paquete ya fue retirado: desde esta incidencia solo se puede pasar a 'en_transito' o 'cancelado'",
            {"estado_previo": previo},
        )


def validar_aceptacion(nuevo: str, hay_asignacion: bool, aceptada: bool) -> None:
    if nuevo in REQUIEREN_ACEPTACION and (not hay_asignacion or not aceptada):
        raise TransicionInvalida(
            "el repartidor asignado debe aceptar la asignación antes de continuar",
            {"accion_requerida": "POST /envios/{codigo}/aceptar-asignacion"},
        )


def acciones_permitidas(
    estado: str,
    rol: str,
    *,
    previo_incidencia: str | None = None,
    hay_asignacion: bool = False,
    aceptada: bool = False,
    es_mi_asignacion: bool = False,
) -> list[str]:
    """Acciones que el actor puede ejecutar ahora sobre el envío (para que la web muestre solo botones válidos)."""
    if rol == "repartidor" and not es_mi_asignacion:
        return []
    acciones = []
    for destino in sorted(TRANSICIONES.get(estado, set())):
        try:
            validar_transicion(estado, destino, rol)
            if estado == "incidencia":
                validar_salida_incidencia(previo_incidencia, destino)
            validar_aceptacion(destino, hay_asignacion, aceptada)
        except (TransicionInvalida, NoPermitido):
            continue
        acciones.append(ACCION_POR_DESTINO.get(destino, destino))
    if hay_asignacion and not aceptada and rol in ("repartidor", "admin") and estado not in LIBERAN_CAPACIDAD:
        acciones.append("aceptar_asignacion")
    if estado in REASIGNABLES and rol in ("operador", "admin"):
        acciones.append("reasignar")
    return acciones
