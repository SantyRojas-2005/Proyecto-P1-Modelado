"""Conexiones a PostgreSQL, MongoDB y Redis, y el manejo de transacciones."""
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pymongo
import redis

from .config import Config


@contextmanager
def transaccion(pool):
    """Entrega un cursor dentro de una transacción local. Commit al salir; rollback ante error."""
    conn = pool.getconn()
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        conn.commit()
    except BaseException:
        if not conn.closed:
            try:
                conn.rollback()
            except psycopg2.Error:
                pass
        raise
    finally:
        # Si la conexión murió (reinicio de PostgreSQL), se descarta en vez de devolverla al pool
        pool.putconn(conn, close=bool(conn.closed))


class EsquemaDesactualizado(Exception):
    """El volumen de PostgreSQL se creó con una versión anterior de 01_schema.sql."""


class Infraestructura:
    def __init__(self, cfg: Config):
        self.pg = psycopg2.pool.ThreadedConnectionPool(1, 10, **cfg.pg)
        self.mongo = pymongo.MongoClient(cfg.mongo_uri, serverSelectionTimeoutMS=3000)
        self.mongo_db = self.mongo[cfg.mongo_db]
        self.redis = redis.Redis(
            host=cfg.redis_host,
            port=6379,
            password=cfg.redis_password,
            socket_timeout=2,
            decode_responses=True,
        )

    def _probar_pg(self):
        with transaccion(self.pg) as cur:
            # Además de conectar, comprueba que el volumen tenga el esquema actual
            cur.execute("""SELECT count(*) AS n FROM information_schema.columns
                           WHERE table_name = 'asignaciones' AND column_name = 'aceptada_en'""")
            if cur.fetchone()["n"] == 0:
                raise EsquemaDesactualizado()

    def _probar_mongo(self):
        self.mongo.admin.command("ping")

    def _probar_redis(self):
        self.redis.ping()

    def salud(self) -> dict:
        resultado = {}
        pruebas = (("postgres", self._probar_pg), ("mongodb", self._probar_mongo), ("redis", self._probar_redis))
        for nombre, prueba in pruebas:
            try:
                prueba()
                resultado[nombre] = "ok"
            except EsquemaDesactualizado:
                resultado[nombre] = "esquema_desactualizado (ejecute docker compose down -v)"
            except Exception:
                resultado[nombre] = "caido"
        return resultado

    def cerrar(self):
        self.pg.closeall()
        self.mongo.close()
        self.redis.close()
