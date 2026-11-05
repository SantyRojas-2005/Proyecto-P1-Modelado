"""Cuerpos de petición validados por Pydantic."""
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints


def Texto(minimo: int, maximo: int):
    # Recorta espacios: "   " no cuenta como nombre válido
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=minimo, max_length=maximo)]


class Coordenada(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class NuevoEnvio(BaseModel):
    remitente: Texto(1, 100)
    destinatario: Texto(1, 100)
    origen: Coordenada
    destino: Coordenada
    origen_direccion: Texto(0, 150) | None = None
    destino_direccion: Texto(0, 150) | None = None
    zona_id: int = Field(gt=0)
    tipo_servicio_id: int = Field(gt=0)
    # Solo para operador/admin que registran a nombre de un cliente; el cliente no lo envía
    cliente_id: UUID | None = None


class CambioEstado(BaseModel):
    # evento_id es la clave de idempotencia: reenviar el mismo evento no lo aplica dos veces
    evento_id: Texto(3, 60)
    nuevo_estado: str = Field(max_length=20)
    nota: Texto(0, 200) | None = None
    ubicacion: Coordenada | None = None


class Asignacion(BaseModel):
    evento_id: Texto(3, 60)
    envio_codigo: Texto(3, 20)
    repartidor_id: UUID


class AceptacionAsignacion(BaseModel):
    evento_id: Texto(3, 60)


class Entrega(BaseModel):
    evento_id: Texto(3, 60)
    receptor_nombre: Texto(1, 100)
    observacion: Texto(0, 200) | None = None
    # Referencia simulada a la foto (URL o nombre de archivo); no se sube ningún binario
    foto_url: Texto(0, 300) | None = None
    firma_simulada: bool = True


class CapacidadUpdate(BaseModel):
    capacidad_max: int = Field(ge=1, le=20)


class ZonaIn(BaseModel):
    nombre: Texto(1, 80)
    activa: bool = True


class TipoServicioIn(BaseModel):
    nombre: Texto(1, 60)
    tarifa_base: float = Field(ge=0, le=999999)
    tiempo_estimado_min: int = Field(gt=0, le=10000)
