"""Reconstruye la proyección de MongoDB (eventos_seguimiento) desde el outbox de PostgreSQL.

El outbox es el historial oficial; MongoDB es una vista de lectura. Si la colección se
pierde o queda inconsistente, este comando la vacía y marca todo el outbox como pendiente:
el worker vuelve a publicarlo en orden.

Uso (con el stack levantado):
    docker compose exec worker python -m worker.reconstruir
"""

from .sync import conectar_pg, mongo_coleccion


def main():
    eventos_col = mongo_coleccion()
    borrados = eventos_col.delete_many({}).deleted_count
    conn = conectar_pg()
    with conn.cursor() as cur:
        cur.execute("UPDATE outbox SET publicado_en = NULL, intentos = 0")
        pendientes = cur.rowcount
    conn.close()
    print(f"MongoDB: {borrados} eventos eliminados. Outbox: {pendientes} eventos marcados como pendientes.")
    print("El worker los volverá a publicar en orden en los próximos segundos.")


if __name__ == "__main__":
    main()
