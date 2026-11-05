"""Pruebas HTTP que fallan antes de tocar la base de datos (validación, roles, reglas de estado)."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


class InfraSinBaseDeDatos:
    """Sustituto que no conecta a nada: cualquier acceso real provocaría un error de prueba."""

    def __getattr__(self, nombre):
        raise AssertionError(f"la prueba no debía tocar la infraestructura ({nombre})")


@pytest.fixture
def cliente():
    app.state.infra = InfraSinBaseDeDatos()
    return TestClient(app)


def cabeceras(rol: str) -> dict:
    return {"X-Actor-Id": str(uuid.uuid4()), "X-Actor-Rol": rol}


def test_faltan_cabeceras_de_actor(cliente):
    respuesta = cliente.get("/envios")
    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["codigo"] == "VALIDACION"


def test_rol_invalido(cliente):
    respuesta = cliente.get("/envios", headers=cabeceras("pirata"))
    assert respuesta.status_code == 422


def test_coordenadas_fuera_de_rango(cliente):
    cuerpo = {
        "remitente": "A", "destinatario": "B", "zona_id": 1, "tipo_servicio_id": 1,
        "origen": {"lat": 200, "lng": -78.4}, "destino": {"lat": -0.2, "lng": -78.4},
    }
    respuesta = cliente.post("/envios", json=cuerpo, headers=cabeceras("cliente"))
    assert respuesta.status_code == 422


def test_repartidor_no_puede_asignar(cliente):
    cuerpo = {"evento_id": "EV-TEST-1", "envio_codigo": "ENV-000001",
              "repartidor_id": str(uuid.uuid4())}
    respuesta = cliente.post("/asignaciones", json=cuerpo, headers=cabeceras("repartidor"))
    assert respuesta.status_code == 403
    assert respuesta.json()["error"]["codigo"] == "NO_PERMITIDO"


def test_patch_a_entregado_se_rechaza_sin_bd(cliente):
    cuerpo = {"evento_id": "EV-TEST-2", "nuevo_estado": "entregado"}
    respuesta = cliente.patch("/envios/ENV-000001/estado", json=cuerpo, headers=cabeceras("repartidor"))
    assert respuesta.status_code == 409
    assert respuesta.json()["error"]["codigo"] == "TRANSICION_INVALIDA"


def test_entrega_solo_para_repartidor(cliente):
    cuerpo = {"evento_id": "EV-TEST-3", "receptor_nombre": "Maria Ficticia"}
    respuesta = cliente.post("/envios/ENV-000001/entrega", json=cuerpo, headers=cabeceras("cliente"))
    assert respuesta.status_code == 403

def test_get_repartidores_cliente_falla(cliente):
    respuesta = cliente.get("/repartidores", headers=cabeceras("cliente"))
    assert respuesta.status_code == 403


def test_capacidad_fuera_de_rango_es_422(cliente):
    respuesta = cliente.patch(f"/repartidores/{uuid.uuid4()}/capacidad",
                              json={"capacidad_max": 50}, headers=cabeceras("admin"))
    assert respuesta.status_code == 422


def test_solo_admin_cambia_capacidad(cliente):
    respuesta = cliente.patch(f"/repartidores/{uuid.uuid4()}/capacidad",
                              json={"capacidad_max": 3}, headers=cabeceras("operador"))
    assert respuesta.status_code == 403


def test_zona_con_nombre_vacio_es_422(cliente):
    respuesta = cliente.post("/zonas", json={"nombre": "   "}, headers=cabeceras("admin"))
    assert respuesta.status_code == 422


def test_cliente_no_crea_zonas(cliente):
    respuesta = cliente.post("/zonas", json={"nombre": "Nueva"}, headers=cabeceras("cliente"))
    assert respuesta.status_code == 403


def test_repartidor_no_registra_envios(cliente):
    cuerpo = {"remitente": "A", "destinatario": "B", "zona_id": 1, "tipo_servicio_id": 1,
              "origen": {"lat": 0, "lng": 0}, "destino": {"lat": 0, "lng": 0}}
    respuesta = cliente.post("/envios", json=cuerpo, headers=cabeceras("repartidor"))
    assert respuesta.status_code == 403


def test_cliente_no_ve_el_estado_del_sincronizador(cliente):
    respuesta = cliente.get("/sincronizacion", headers=cabeceras("cliente"))
    assert respuesta.status_code == 403
