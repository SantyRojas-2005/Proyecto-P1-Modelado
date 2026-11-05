"""Pruebas de la máquina de estados. No requieren base de datos."""
import pytest

from app import estados
from app.errors import NoPermitido, TransicionInvalida, Validacion


def test_camino_feliz_completo():
    validar = estados.validar_transicion
    validar("creado", "aceptado", "operador")
    validar("aceptado", "asignado", "operador")
    validar("asignado", "retirado", "repartidor")
    validar("retirado", "en_transito", "repartidor")
    validar("en_transito", "entregado", "repartidor")


def test_creado_no_puede_saltar_a_entregado():
    with pytest.raises(TransicionInvalida) as info:
        estados.validar_transicion("creado", "entregado", "repartidor")
    assert info.value.codigo == "TRANSICION_INVALIDA"
    assert info.value.detalles["permitidas"] == ["aceptado", "cancelado"]


@pytest.mark.parametrize("terminal", ["entregado", "cancelado"])
def test_estados_terminales_no_tienen_salida(terminal):
    for destino in estados.ESTADOS:
        with pytest.raises(TransicionInvalida):
            estados.validar_transicion(terminal, destino, "admin")


@pytest.mark.parametrize("destino", ["asignado", "entregado"])
def test_patch_generico_no_puede_marcar_asignado_ni_entregado(destino):
    with pytest.raises(TransicionInvalida):
        estados.validar_pedido_generico(destino)


def test_estado_inexistente_es_error_de_validacion():
    with pytest.raises(Validacion):
        estados.validar_pedido_generico("volando")


def test_cliente_no_puede_entregar():
    with pytest.raises(NoPermitido):
        estados.validar_transicion("en_transito", "entregado", "cliente")


def test_cliente_puede_cancelar_su_envio_creado():
    estados.validar_transicion("creado", "cancelado", "cliente")


def test_solo_entregado_y_cancelado_liberan_capacidad():
    assert estados.LIBERAN_CAPACIDAD == {"entregado", "cancelado"}


def test_todas_las_transiciones_apuntan_a_estados_conocidos():
    for origen, destinos in estados.TRANSICIONES.items():
        assert origen in estados.ESTADOS
        assert destinos <= set(estados.ESTADOS)


def test_cliente_no_cancela_despues_de_asignado():
    with pytest.raises(NoPermitido):
        estados.validar_transicion("asignado", "cancelado", "cliente")
    estados.validar_transicion("asignado", "cancelado", "operador")


def test_incidencia_antes_del_retiro_solo_retoma_el_retiro():
    estados.validar_salida_incidencia("asignado", "retirado")
    estados.validar_salida_incidencia("asignado", "cancelado")
    with pytest.raises(TransicionInvalida):
        estados.validar_salida_incidencia("asignado", "en_transito")


def test_incidencia_despues_del_retiro_solo_retoma_el_transito():
    estados.validar_salida_incidencia("en_transito", "en_transito")
    with pytest.raises(TransicionInvalida):
        estados.validar_salida_incidencia("retirado", "retirado")


def test_retirar_exige_asignacion_aceptada():
    with pytest.raises(TransicionInvalida):
        estados.validar_aceptacion("retirado", hay_asignacion=True, aceptada=False)
    estados.validar_aceptacion("retirado", hay_asignacion=True, aceptada=True)
    estados.validar_aceptacion("incidencia", hay_asignacion=True, aceptada=False)


def test_acciones_del_repartidor_segun_aceptacion():
    sin_aceptar = estados.acciones_permitidas("asignado", "repartidor", hay_asignacion=True,
                                              aceptada=False, es_mi_asignacion=True)
    assert "aceptar_asignacion" in sin_aceptar and "retirado" not in sin_aceptar
    aceptada = estados.acciones_permitidas("asignado", "repartidor", hay_asignacion=True,
                                           aceptada=True, es_mi_asignacion=True)
    assert "retirado" in aceptada and "aceptar_asignacion" not in aceptada


def test_repartidor_sin_asignacion_no_tiene_acciones():
    assert estados.acciones_permitidas("en_transito", "repartidor", hay_asignacion=True,
                                       aceptada=True, es_mi_asignacion=False) == []


def test_acciones_del_operador():
    assert estados.acciones_permitidas("creado", "operador") == ["aceptado", "cancelado"]
    assert estados.acciones_permitidas("aceptado", "operador") == ["asignar", "cancelado"]
    incidencia = estados.acciones_permitidas("incidencia", "operador", previo_incidencia="en_transito",
                                             hay_asignacion=True, aceptada=True)
    assert incidencia == ["cancelado", "reasignar"]
