"""Configuración leída desde variables de entorno (docker-compose las inyecta desde .env)."""
import os
from dataclasses import dataclass
from urllib.parse import quote_plus


@dataclass(frozen=True)
class Config:
    pg: dict
    mongo_uri: str
    mongo_db: str
    redis_host: str
    redis_password: str | None
    cors_origins: tuple[str, ...]


def cargar_config() -> Config:
    usuario = quote_plus(os.getenv("MONGO_USER", ""))
    clave = quote_plus(os.getenv("MONGO_PASSWORD", ""))
    host_mongo = os.getenv("MONGO_HOST", "localhost")
    origenes = os.getenv("CORS_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080")
    return Config(
        pg={
            "dbname": os.getenv("PG_DB", "mensajeria"),
            "user": os.getenv("PG_USER", ""),
            "password": os.getenv("PG_PASSWORD", ""),
            "host": os.getenv("POSTGRES_HOST", "localhost"),
            "port": 5432,
            "connect_timeout": 3,
        },
        mongo_uri=f"mongodb://{usuario}:{clave}@{host_mongo}:27017/?authSource=admin",
        mongo_db=os.getenv("MONGO_DB", "mensajeria"),
        redis_host=os.getenv("REDIS_HOST", "localhost"),
        redis_password=os.getenv("REDIS_PASSWORD") or None,
        cors_origins=tuple(o.strip() for o in origenes.split(",") if o.strip()),
    )
