"""Punto de entrada de la API REST.

Arranque: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import logging
from contextlib import asynccontextmanager

import psycopg2
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from .config import cargar_config
from .errors import AppError, DependenciaCaida
from .infra import Infraestructura
from .routers import router

CFG = cargar_config()
logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.infra = Infraestructura(CFG)
    yield
    app.state.infra.cerrar()


app = FastAPI(
    title="API Mensajería urbana",
    version="1.0.0",
    description=(
        "Persistencia políglota: PostgreSQL (estado y asignaciones), MongoDB (historial y evidencias) "
        "y Redis (estado temporal). Las cabeceras X-Actor-Id y X-Actor-Rol identifican al usuario "
        "(simplificación del laboratorio; no es autenticación real)."
    ),
    lifespan=lifespan,
)



@app.middleware("http")
async def _errores_no_controlados(request: Request, call_next):
    # Se registra ANTES que CORS para quedar por dentro: así un 500 también lleva cabeceras CORS
    # y la web muestra el motivo real en vez de "no se pudo conectar".
    try:
        return await call_next(request)
    except psycopg2.ProgrammingError as exc:
        logger.exception("Error de esquema en PostgreSQL")
        return _error(500, "ESQUEMA_DESACTUALIZADO",
                      "la base de datos tiene un esquema distinto al del código; "
                      "ejecute 'docker compose down -v' y luego 'docker compose up --build -d'",
                      {"detalle": str(exc).splitlines()[0]})
    except Exception as exc:
        logger.exception("Error no controlado")
        return _error(500, "ERROR_INTERNO", "error interno de la API; revise 'docker compose logs api'",
                      {"detalle": type(exc).__name__})


app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CFG.cors_origins),
    allow_methods=["GET", "POST", "PATCH", "PUT", "OPTIONS"],
    allow_headers=["Content-Type", "X-Actor-Id", "X-Actor-Rol", "Idempotency-Key"],
)
app.include_router(router)


def _error(status: int, codigo: str, mensaje: str, detalles: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"codigo": codigo, "mensaje": mensaje, "detalles": detalles or {}}},
    )


@app.exception_handler(AppError)
async def _manejar_error_dominio(request: Request, exc: AppError):
    return _error(exc.status, exc.codigo, exc.mensaje, exc.detalles)


@app.exception_handler(RequestValidationError)
async def _manejar_validacion(request: Request, exc: RequestValidationError):
    campos = [{"campo": ".".join(str(p) for p in e["loc"]), "mensaje": e["msg"]} for e in exc.errors()]
    return _error(422, "VALIDACION", "datos de entrada inválidos", {"campos": campos})


async def _manejar_dependencia(request: Request, exc: Exception):
    # Una base caída no debe tumbar la API: se responde 503 de forma controlada
    return _error(503, DependenciaCaida.codigo, "una dependencia de persistencia no está disponible",
                  {"detalle": type(exc).__name__})


for _excepcion in (psycopg2.OperationalError, psycopg2.InterfaceError, PyMongoError, RedisError):
    app.add_exception_handler(_excepcion, _manejar_dependencia)


@app.exception_handler(psycopg2.IntegrityError)
async def _manejar_integridad(request: Request, exc: psycopg2.IntegrityError):
    # Red de seguridad: una restricción de la BD rechazó la operación (no es un error del servidor)
    restriccion = getattr(getattr(exc, "diag", None), "constraint_name", None)
    return _error(409, "CONFLICTO", "la operación viola una restricción de integridad",
                  {"restriccion": restriccion})


@app.exception_handler(psycopg2.DataError)
async def _manejar_dato(request: Request, exc: psycopg2.DataError):
    return _error(422, "VALIDACION", "dato con formato inválido para la base de datos")
