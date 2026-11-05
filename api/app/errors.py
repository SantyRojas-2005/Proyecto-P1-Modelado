"""Errores de dominio. Cada uno se traduce a un código HTTP y a un código de error estable."""


class AppError(Exception):
    status = 400
    codigo = "ERROR"

    def __init__(self, mensaje: str, detalles: dict | None = None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.detalles = detalles or {}


class Validacion(AppError):
    status = 422
    codigo = "VALIDACION"


class NoPermitido(AppError):
    status = 403
    codigo = "NO_PERMITIDO"


class NoEncontrado(AppError):
    status = 404
    codigo = "NO_ENCONTRADO"


class Conflicto(AppError):
    status = 409
    codigo = "CONFLICTO"


class TransicionInvalida(Conflicto):
    codigo = "TRANSICION_INVALIDA"


class CapacidadExcedida(Conflicto):
    codigo = "CAPACIDAD_EXCEDIDA"


class AsignacionActiva(Conflicto):
    codigo = "ENVIO_YA_ASIGNADO"


class DependenciaCaida(AppError):
    status = 503
    codigo = "DEPENDENCIA_NO_DISPONIBLE"
