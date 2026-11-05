"""Sincronizador outbox (PostgreSQL) -> eventos_seguimiento (MongoDB).

- Procesa los eventos en el orden en que se escribieron (outbox.id).
- Es idempotente: si el evento ya está en MongoDB (mismo evento_id) solo lo marca como publicado.
- Si MongoDB o PostgreSQL caen, espera y reintenta; nada se pierde porque el outbox no se borra.

Arranque: python -m worker.sync
"""
import os
import time

import psycopg2
import psycopg2.extras
from psycopg2 import InterfaceError, OperationalError
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

INTERVALO = float(os.getenv("SYNC_INTERVALO_SEGUNDOS", "2"))
LOTE = 50


def pg_dsn():
    return {
        "dbname": os.getenv("PG_DB"), "user": os.getenv("PG_USER"), "password": os.getenv("PG_PASSWORD"),
        "host": os.getenv("POSTGRES_HOST", "postgres"), "port": 5432, "connect_timeout": 3,
    }


def mongo_coleccion():
    uri = (f"mongodb://{os.getenv('MONGO_USER')}:{os.getenv('MONGO_PASSWORD')}"
           f"@{os.getenv('MONGO_HOST', 'mongo')}:27017/?authSource=admin")
    cliente = MongoClient(uri, serverSelectionTimeoutMS=3000)
    return cliente[os.getenv("MONGO_DB", "mensajeria")]["eventos_seguimiento"]


def conectar_pg():
    """Reintenta hasta que PostgreSQL responda."""
    while True:
        try:
            conn = psycopg2.connect(**pg_dsn())
            conn.autocommit = True
            print("Conexión a PostgreSQL establecida.", flush=True)
            return conn
        except OperationalError:
            print("Esperando a PostgreSQL...", flush=True)
            time.sleep(3)


def publicar(cur, eventos_col, fila) -> bool:
    """Copia un evento a MongoDB. Devuelve True si quedó publicado."""
    evento = dict(fila["payload"])
    try:
        eventos_col.insert_one(evento)
    except DuplicateKeyError:
        existente = eventos_col.find_one({"evento_id": evento["evento_id"]}, {"_id": 1})
        if existente is None:
            # Misma secuencia con otro evento_id: conflicto real. No se marca como publicado;
            # tras 3 intentos queda apartado (visible en GET /sincronizacion) para revisión manual.
            cur.execute("UPDATE outbox SET intentos = intentos + 1 WHERE id = %s", (fila["id"],))
            print(f"CONFLICTO: secuencia repetida para {evento.get('envio_codigo')} "
                  f"(evento {fila['evento_id']}); requiere revisión", flush=True)
            return False
    cur.execute("UPDATE outbox SET publicado_en = now() WHERE id = %s", (fila["id"],))
    return True


def ciclo(cur, eventos_col) -> int:
    cur.execute(
        """SELECT id, evento_id, payload FROM outbox
           WHERE publicado_en IS NULL AND intentos < 3
           ORDER BY id LIMIT %s""",
        (LOTE,),
    )
    publicados = 0
    for fila in cur.fetchall():
        try:
            if publicar(cur, eventos_col, fila):
                publicados += 1
                print(f"Evento {fila['evento_id']} sincronizado.", flush=True)
        except PyMongoError as exc:
            # MongoDB no disponible: se deja pendiente y se reintenta en el próximo ciclo
            print(f"MongoDB no disponible ({type(exc).__name__}); se reintentará.", flush=True)
            break
    return publicados


def main():
    print("Iniciando worker sincronizador (PostgreSQL outbox -> MongoDB)...", flush=True)
    eventos_col = mongo_coleccion()
    conn = conectar_pg()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    while True:
        try:
            ciclo(cur, eventos_col)
        except (OperationalError, InterfaceError):
            print("Conexión perdida con PostgreSQL; reconectando...", flush=True)
            time.sleep(3)
            conn = conectar_pg()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        except PyMongoError:
            pass
        except Exception as exc:  # no detener el worker por un error inesperado
            print(f"Error inesperado durante la sincronización: {exc}", flush=True)
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
