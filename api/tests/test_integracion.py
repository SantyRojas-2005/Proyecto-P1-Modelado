"""Pruebas de integración contra las bases levantadas con `docker compose up` y los datos semilla.

Ejecutar dentro del contenedor de la API (ya tiene las dependencias y la red interna):
    docker compose exec -e RUN_INTEGRATION=1 api python -m pytest tests -v

Reiniciar los datos antes, si se quiere partir de cero:
    docker compose down -v && docker compose up --build -d
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.infra import transaccion
from app.main import app
from worker.sync import ciclo

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="requiere PostgreSQL, MongoDB y Redis levantados con docker compose",
)

ADMIN = {"X-Actor-Id": "11111111-1111-1111-1111-111111111111", "X-Actor-Rol": "admin"}
OPERADOR = {"X-Actor-Id": "22222222-2222-2222-2222-222222222221", "X-Actor-Rol": "operador"}
CLIENTE = {"X-Actor-Id": "33333333-3333-3333-3333-333333333331", "X-Actor-Rol": "cliente"}
OTRO_CLIENTE = {"X-Actor-Id": "33333333-3333-3333-3333-333333333332", "X-Actor-Rol": "cliente"}
USUARIO_REP_CAP1 = "44444444-4444-4444-4444-444444444442"   # Repartidor Bici, capacidad 1
REP_CAP1 = "55555555-5555-5555-5555-555555555552"
REP_CAP1_ACTOR = {"X-Actor-Id": USUARIO_REP_CAP1, "X-Actor-Rol": "repartidor"}
REP_FURGON = "55555555-5555-5555-5555-555555555554"         # capacidad 5
REP_FURGON_ACTOR = {"X-Actor-Id": "44444444-4444-4444-4444-444444444444", "X-Actor-Rol": "repartidor"}


def evento() -> str:
    return f"EV-{uuid.uuid4()}"


def nuevo_envio(cliente, clave=None, cabeceras=CLIENTE, **extra):
    cuerpo = {
        "remitente": "Empresa Ficticia SA", "destinatario": "Destinatario Prueba",
        "origen": {"lat": -0.1807, "lng": -78.4678}, "destino": {"lat": -0.21, "lng": -78.49},
        "origen_direccion": "Av. Ficticia 123", "destino_direccion": "Calle Inventada 45",
        "zona_id": 1, "tipo_servicio_id": 1, **extra,
    }
    return cliente.post("/envios", json=cuerpo,
                        headers={**cabeceras, "Idempotency-Key": clave or str(uuid.uuid4())})


def patch(cliente, codigo, estado, cabeceras):
    return cliente.patch(f"/envios/{codigo}/estado",
                         json={"evento_id": evento(), "nuevo_estado": estado}, headers=cabeceras)


def envio_asignado(cliente, repartidor=REP_CAP1, rep_actor=REP_CAP1_ACTOR, aceptar=True):
    codigo = nuevo_envio(cliente).json()["codigo"]
    assert patch(cliente, codigo, "aceptado", OPERADOR).status_code == 200
    asignacion = {"evento_id": evento(), "envio_codigo": codigo, "repartidor_id": repartidor}
    assert cliente.post("/asignaciones", json=asignacion, headers=OPERADOR).status_code == 201
    if aceptar:
        r = cliente.post(f"/envios/{codigo}/aceptar-asignacion", json={"evento_id": evento()}, headers=rep_actor)
        assert r.status_code == 200
    return codigo


def sincronizar():
    """Ejecuta el worker en proceso para no depender de su intervalo."""
    infra = app.state.infra
    with transaccion(infra.pg) as cur:
        while ciclo(cur, infra.mongo_db.eventos_seguimiento):
            pass


@pytest.fixture(scope="module")
def cliente():
    with TestClient(app) as c:
        yield c


def test_salud_reporta_las_tres_bases(cliente):
    respuesta = cliente.get("/health")
    assert respuesta.status_code == 200
    assert respuesta.json()["dependencias"] == {"postgres": "ok", "mongodb": "ok", "redis": "ok"}


# ---------- Datos semilla ----------

def test_semilla_tiene_historial_coherente_con_el_estado(cliente):
    sincronizar()
    resultados = cliente.get("/envios", params={"q": "ENV-SEED", "limite": 100}, headers=ADMIN).json()["resultados"]
    assert len(resultados) == 20
    for envio in resultados:
        seg = cliente.get(f"/envios/{envio['codigo']}/seguimiento", headers=ADMIN).json()
        assert seg["eventos"], f"{envio['codigo']} sin historial"
        assert seg["eventos"][-1]["estado_nuevo"] == seg["estado_actual"]
        assert [e["secuencia"] for e in seg["eventos"]] == list(range(1, len(seg["eventos"]) + 1))


def test_semilla_entregados_tienen_evidencia(cliente):
    for codigo in ("ENV-SEED0017", "ENV-SEED0018"):
        assert cliente.get(f"/envios/{codigo}/evidencia", headers=ADMIN).status_code == 200


# ---------- Flujo 1: registro y comprobante ----------

def test_creacion_devuelve_comprobante_y_es_idempotente(cliente):
    clave = str(uuid.uuid4())
    primera = nuevo_envio(cliente, clave)
    segunda = nuevo_envio(cliente, clave)
    assert primera.status_code == 201
    assert segunda.status_code == 200
    comprobante = primera.json()
    assert comprobante["codigo"] == segunda.json()["codigo"]
    assert comprobante["estado"] == "creado"
    assert comprobante["tarifa_base"] is not None and comprobante["zona"] == "Norte"
    assert comprobante["destino_direccion"] == "Calle Inventada 45"


def test_operador_registra_a_nombre_de_un_cliente(cliente):
    sin_cliente = nuevo_envio(cliente, cabeceras=OPERADOR)
    assert sin_cliente.status_code == 422
    con_cliente = nuevo_envio(cliente, cabeceras=OPERADOR, cliente_id=CLIENTE["X-Actor-Id"])
    assert con_cliente.status_code == 201
    assert str(con_cliente.json()["cliente_id"]) == CLIENTE["X-Actor-Id"]


def test_no_se_registra_en_zona_desactivada(cliente):
    nombre = f"Zona-{uuid.uuid4().hex[:6]}"
    zona = cliente.post("/zonas", json={"nombre": nombre, "activa": False}, headers=ADMIN).json()
    respuesta = nuevo_envio(cliente, zona_id=zona["id"])
    assert respuesta.status_code == 422


# ---------- Flujo 2 y 3: asignación, capacidad y recorrido ----------

def test_asignacion_respeta_capacidad_y_se_libera_al_entregar(cliente):
    primero = envio_asignado(cliente)
    segundo = nuevo_envio(cliente).json()["codigo"]
    abiertos = [primero, segundo]
    try:
        assert patch(cliente, segundo, "aceptado", OPERADOR).status_code == 200
        exceso = {"evento_id": evento(), "envio_codigo": segundo, "repartidor_id": REP_CAP1}
        respuesta = cliente.post("/asignaciones", json=exceso, headers=OPERADOR)
        assert respuesta.status_code == 409
        assert respuesta.json()["error"]["codigo"] == "CAPACIDAD_EXCEDIDA"

        for destino in ("retirado", "en_transito"):
            assert patch(cliente, primero, destino, REP_CAP1_ACTOR).status_code == 200
        entrega = {"evento_id": evento(), "receptor_nombre": "Maria Ficticia", "observacion": "Recepción",
                   "foto_url": "evidencias/prueba.jpg"}
        assert cliente.post(f"/envios/{primero}/entrega", json=entrega, headers=REP_CAP1_ACTOR).status_code == 200
        abiertos.remove(primero)

        reintento = {"evento_id": evento(), "envio_codigo": segundo, "repartidor_id": REP_CAP1}
        assert cliente.post("/asignaciones", json=reintento, headers=OPERADOR).status_code == 201
    finally:
        for codigo in abiertos:
            patch(cliente, codigo, "cancelado", OPERADOR)


def test_retirar_sin_aceptar_la_asignacion_se_rechaza(cliente):
    codigo = envio_asignado(cliente, aceptar=False)
    try:
        respuesta = patch(cliente, codigo, "retirado", REP_CAP1_ACTOR)
        assert respuesta.status_code == 409
        detalle = cliente.get(f"/envios/{codigo}", headers=REP_CAP1_ACTOR).json()
        assert "aceptar_asignacion" in detalle["acciones"]
    finally:
        patch(cliente, codigo, "cancelado", OPERADOR)


def test_incidencia_antes_del_retiro_no_salta_a_transito(cliente):
    codigo = envio_asignado(cliente)
    try:
        assert patch(cliente, codigo, "incidencia", REP_CAP1_ACTOR).status_code == 200
        assert patch(cliente, codigo, "en_transito", REP_CAP1_ACTOR).status_code == 409
        assert patch(cliente, codigo, "retirado", REP_CAP1_ACTOR).status_code == 200
    finally:
        patch(cliente, codigo, "incidencia", REP_CAP1_ACTOR)
        patch(cliente, codigo, "cancelado", OPERADOR)


def test_cliente_no_cancela_un_envio_ya_asignado(cliente):
    codigo = envio_asignado(cliente)
    try:
        assert patch(cliente, codigo, "cancelado", CLIENTE).status_code == 403
    finally:
        patch(cliente, codigo, "cancelado", OPERADOR)


def test_reasignacion_mueve_la_carga(cliente):
    def carga(rep_id):
        return next(r for r in cliente.get("/repartidores", headers=OPERADOR).json()
                    if str(r["id"]) == rep_id)["carga_actual"]

    codigo = envio_asignado(cliente)
    try:
        antes = carga(REP_FURGON)
        cuerpo = {"evento_id": evento(), "envio_codigo": codigo, "repartidor_id": REP_FURGON}
        assert cliente.post("/asignaciones/reasignar", json=cuerpo, headers=OPERADOR).status_code == 200
        assert carga(REP_CAP1) == 0 and carga(REP_FURGON) == antes + 1
        # El repartidor anterior ya no puede mover el envío
        assert patch(cliente, codigo, "retirado", REP_CAP1_ACTOR).status_code == 403
        mismo = {"evento_id": evento(), "envio_codigo": codigo, "repartidor_id": REP_FURGON}
        assert cliente.post("/asignaciones/reasignar", json=mismo, headers=OPERADOR).status_code == 409
    finally:
        patch(cliente, codigo, "cancelado", OPERADOR)


def test_concurrencia_dos_asignaciones_simultaneas(cliente):
    envio1 = nuevo_envio(cliente).json()["codigo"]
    envio2 = nuevo_envio(cliente).json()["codigo"]
    patch(cliente, envio1, "aceptado", OPERADOR)
    patch(cliente, envio2, "aceptado", OPERADOR)

    def hacer_asignacion(codigo):
        cuerpo = {"evento_id": evento(), "envio_codigo": codigo, "repartidor_id": REP_CAP1}
        return cliente.post("/asignaciones", json=cuerpo, headers=OPERADOR).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(hacer_asignacion, (envio1, envio2)))
    try:
        assert sorted(resultados) == [201, 409]
    finally:
        for codigo in (envio1, envio2):
            patch(cliente, codigo, "cancelado", OPERADOR)


# ---------- Idempotencia y reglas ----------

def test_reenviar_el_mismo_evento_no_lo_aplica_dos_veces(cliente):
    codigo = nuevo_envio(cliente).json()["codigo"]
    cuerpo = {"evento_id": evento(), "nuevo_estado": "aceptado"}
    primera = cliente.patch(f"/envios/{codigo}/estado", json=cuerpo, headers=OPERADOR).json()
    segunda = cliente.patch(f"/envios/{codigo}/estado", json=cuerpo, headers=OPERADOR).json()
    assert primera["ya_aplicado"] is False
    assert segunda["ya_aplicado"] is True


def test_transicion_invalida_devuelve_409(cliente):
    codigo = nuevo_envio(cliente).json()["codigo"]
    assert patch(cliente, codigo, "cancelado", CLIENTE).status_code == 200
    respuesta = patch(cliente, codigo, "aceptado", OPERADOR)
    assert respuesta.status_code == 409
    assert respuesta.json()["error"]["codigo"] == "TRANSICION_INVALIDA"


# ---------- Visibilidad ----------

def test_repartidor_solo_ve_sus_envios(cliente):
    assert cliente.get("/envios/ENV-SEED0001", headers=REP_FURGON_ACTOR).status_code == 404
    propios = cliente.get("/envios", params={"limite": 100}, headers=REP_FURGON_ACTOR).json()["resultados"]
    assert propios and all(e["codigo"] != "ENV-SEED0001" for e in propios)
    assert cliente.get("/envios/ENV-SEED0016", headers=REP_FURGON_ACTOR).status_code == 200


def test_cliente_no_ve_envios_ajenos(cliente):
    assert cliente.get("/envios/ENV-SEED0002", headers=CLIENTE).status_code == 404
    assert cliente.get("/envios/ENV-SEED0002", headers=OTRO_CLIENTE).status_code == 200


# ---------- Administración ----------

def test_catalogos_duplicados_e_inexistentes(cliente):
    assert cliente.post("/zonas", json={"nombre": "Norte"}, headers=ADMIN).status_code == 409
    assert cliente.put("/zonas/99999", json={"nombre": "X"}, headers=ADMIN).status_code == 404
    duplicado = {"nombre": "Express", "tarifa_base": 1, "tiempo_estimado_min": 10}
    assert cliente.post("/tipos-servicio", json=duplicado, headers=ADMIN).status_code == 409


def test_admin_ve_zonas_inactivas(cliente):
    zonas = cliente.get("/admin/zonas", headers=ADMIN).json()
    assert all("activa" in z for z in zonas)


# ---------- Seguimiento, sincronización y fallos ----------

def test_historial_tiene_autor_y_coincide_con_el_estado(cliente):
    codigo = envio_asignado(cliente)
    try:
        sincronizar()
        seg = cliente.get(f"/envios/{codigo}/seguimiento", headers=CLIENTE).json()
        assert seg["pendientes_sincronizacion"] == 0
        assert seg["eventos"][-1]["estado_nuevo"] == seg["estado_actual"] == "asignado"
        assert seg["eventos"][-1]["actor"]["nombre"] == "Repartidor Bici"
    finally:
        patch(cliente, codigo, "cancelado", OPERADOR)


def test_seguimiento_usa_el_outbox_si_mongodb_cae(cliente):
    class MongoCaido:
        def __getattr__(self, _):
            raise RuntimeError("MongoDB no disponible")

    infra = app.state.infra
    original = infra.mongo_db
    infra.mongo_db = MongoCaido()
    try:
        seg = cliente.get("/envios/ENV-SEED0017/seguimiento", headers=ADMIN).json()
        assert seg["fuente_historial"] == "postgresql_outbox"
        assert seg["eventos"][-1]["estado_nuevo"] == "entregado"
        assert cliente.get("/sincronizacion", headers=ADMIN).json()["mongodb"] == "caido"
    finally:
        infra.mongo_db = original


def test_estado_temporal_expira_y_no_es_evidencia(cliente):
    cliente.post(f"/repartidores/{REP_CAP1}/posicion", json={"lat": -0.18, "lng": -78.46}, headers=REP_CAP1_ACTOR)
    respuesta = cliente.get(f"/repartidores/{REP_CAP1}/estado-temporal", headers=OPERADOR).json()
    assert respuesta["posicion"]["vigente"] is True
    assert 0 < respuesta["posicion"]["expira_en_segundos"] <= 120
    assert "no es comprobante" in respuesta["nota"]


def test_repartidor_no_reporta_la_posicion_de_otro(cliente):
    respuesta = cliente.post(f"/repartidores/{REP_FURGON}/posicion", json={"lat": 0, "lng": 0},
                             headers=REP_CAP1_ACTOR)
    assert respuesta.status_code == 403
