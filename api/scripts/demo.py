"""Demostraciones para la defensa. Solo usa la biblioteca estándar (no requiere instalar nada).

Ejecutar dentro del contenedor de la API (llama a la API por HTTP, igual que la web):
    docker compose exec api python scripts/demo.py todo
    docker compose exec api python scripts/demo.py codigo        # prueba 1: código único y consulta
    docker compose exec api python scripts/demo.py invalida      # prueba 2: transición inválida
    docker compose exec api python scripts/demo.py concurrencia  # prueba 3: dos asignaciones a la vez
    docker compose exec api python scripts/demo.py idempotencia  # reintentos sin duplicar

También funciona desde el equipo anfitrión con Python 3:  python api/scripts/demo.py todo
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
import uuid

API = os.getenv("API_URL", "http://localhost:8000")
CLIENTE = {"X-Actor-Id": "33333333-3333-3333-3333-333333333331", "X-Actor-Rol": "cliente"}
OPERADOR = {"X-Actor-Id": "22222222-2222-2222-2222-222222222221", "X-Actor-Rol": "operador"}
BICI = "55555555-5555-5555-5555-555555555552"   # Repartidor Bici: capacidad 1


def llamar(metodo, ruta, cuerpo=None, cabeceras=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(f"{API}{ruta}", data=datos, method=metodo,
                                 headers={"Content-Type": "application/json", **(cabeceras or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def mostrar(titulo, status, cuerpo):
    if isinstance(cuerpo, dict) and "error" in cuerpo:
        detalle = f"{cuerpo['error']['codigo']}: {cuerpo['error']['mensaje']}"
    elif isinstance(cuerpo, dict):
        detalle = cuerpo.get("codigo") or cuerpo.get("estado") or (cuerpo.get("envio") or {}).get("estado", "")
    else:
        detalle = ""
    print(f"  {titulo:<55} HTTP {status}  {detalle}")


def nuevo_envio(clave=None):
    cuerpo = {"remitente": "Demo Defensa", "destinatario": "Destinatario Demo",
              "origen_direccion": "Av. Ficticia 1", "destino_direccion": "Calle Inventada 2",
              "origen": {"lat": -0.18, "lng": -78.47}, "destino": {"lat": -0.21, "lng": -78.49},
              "zona_id": 1, "tipo_servicio_id": 1}
    return llamar("POST", "/envios", cuerpo, {**CLIENTE, "Idempotency-Key": clave or str(uuid.uuid4())})


def cambiar(codigo, estado, actor=OPERADOR, evento=None):
    return llamar("PATCH", f"/envios/{codigo}/estado",
                  {"evento_id": evento or str(uuid.uuid4()), "nuevo_estado": estado}, actor)


def demo_codigo():
    print("\n[1] Código único y consulta posterior")
    s1, e1 = nuevo_envio(); mostrar("Crear envío A", s1, e1)
    s2, e2 = nuevo_envio(); mostrar("Crear envío B", s2, e2)
    print(f"  Códigos distintos: {e1['codigo'] != e2['codigo']}")
    s, d = llamar("GET", f"/envios/{e1['codigo']}", cabeceras=CLIENTE)
    mostrar(f"Consultar {e1['codigo']}", s, d)
    s, d = llamar("GET", f"/envios/{e1['codigo']}", cabeceras={**CLIENTE, "X-Actor-Id": "33333333-3333-3333-3333-333333333332"})
    mostrar("Otro cliente intenta consultarlo (debe ser 404)", s, d)


def demo_invalida():
    print("\n[2] Transiciones válidas e inválidas")
    _, e = nuevo_envio(); codigo = e["codigo"]
    print(f"  Envío {codigo} en estado 'creado'")
    mostrar("creado → entregado por PATCH (409)", *cambiar(codigo, "entregado"))
    mostrar("creado → entregado por POST /entrega (409)", *llamar(
        "POST", f"/envios/{codigo}/entrega", {"evento_id": str(uuid.uuid4()), "receptor_nombre": "X"},
        {**OPERADOR, "X-Actor-Rol": "admin"}))
    mostrar("creado → retirado (409)", *cambiar(codigo, "retirado", {**OPERADOR, "X-Actor-Rol": "admin"}))
    mostrar("creado → aceptado (válida, 200)", *cambiar(codigo, "aceptado"))
    s, d = llamar("GET", f"/envios/{codigo}/seguimiento", cabeceras=CLIENTE)
    print(f"  Estado actual: {d['estado_actual']} · eventos en historial: {len(d['eventos'])} "
          f"· pendientes de sincronizar: {d['pendientes_sincronizacion']}")


def demo_concurrencia():
    print("\n[3] Dos asignaciones simultáneas al Repartidor Bici (capacidad 1)")
    codigos = []
    for _ in range(2):
        _, e = nuevo_envio(); codigos.append(e["codigo"]); cambiar(e["codigo"], "aceptado")
    resultados = {}

    def asignar(codigo):
        resultados[codigo] = llamar("POST", "/asignaciones",
                                    {"evento_id": str(uuid.uuid4()), "envio_codigo": codigo, "repartidor_id": BICI},
                                    OPERADOR)

    hilos = [threading.Thread(target=asignar, args=(c,)) for c in codigos]
    for h in hilos: h.start()
    for h in hilos: h.join()
    for codigo in codigos:
        mostrar(f"Asignar {codigo}", *resultados[codigo])
    estados = sorted(r[0] for r in resultados.values())
    print(f"  Resultado esperado [201, 409]: {estados} → {'CORRECTO' if estados == [201, 409] else 'REVISAR'}")
    for codigo in codigos:   # dejar la Bici libre para la siguiente demostración
        cambiar(codigo, "cancelado")
    print("  (Los dos envíos se cancelaron para liberar la capacidad)")


def demo_idempotencia():
    print("\n[4] Reintentos seguros")
    clave = str(uuid.uuid4())
    s1, e1 = nuevo_envio(clave); mostrar("POST /envios con Idempotency-Key", s1, e1)
    s2, e2 = nuevo_envio(clave); mostrar("Mismo POST repetido (200, mismo código)", s2, e2)
    evento = str(uuid.uuid4())
    s, d = cambiar(e1["codigo"], "aceptado", evento=evento); print(f"  PATCH aceptado                → HTTP {s} ya_aplicado={d['ya_aplicado']}")
    s, d = cambiar(e1["codigo"], "aceptado", evento=evento); print(f"  Mismo PATCH (mismo evento_id) → HTTP {s} ya_aplicado={d['ya_aplicado']}")


DEMOS = {"codigo": demo_codigo, "invalida": demo_invalida,
         "concurrencia": demo_concurrencia, "idempotencia": demo_idempotencia}

if __name__ == "__main__":
    elegida = sys.argv[1] if len(sys.argv) > 1 else "todo"
    if elegida == "todo":
        for demo in DEMOS.values():
            demo()
    elif elegida in DEMOS:
        DEMOS[elegida]()
    else:
        print(f"Uso: python scripts/demo.py [{'|'.join(DEMOS)}|todo]")
        sys.exit(1)
