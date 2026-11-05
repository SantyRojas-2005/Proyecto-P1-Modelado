-- 1. Habilitar extensión para búsqueda de texto
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 2. Tipos y Tablas base
CREATE TYPE envio_estado AS ENUM (
  'creado','aceptado','asignado','retirado',
  'en_transito','incidencia','entregado','cancelado'
);

CREATE TABLE zonas (
  id SERIAL PRIMARY KEY,
  nombre VARCHAR(80) UNIQUE NOT NULL,
  activa BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE tipos_servicio (
  id SERIAL PRIMARY KEY,
  nombre VARCHAR(60) UNIQUE NOT NULL,
  tarifa_base NUMERIC(8,2) NOT NULL CHECK (tarifa_base >= 0),
  tiempo_estimado_min INT NOT NULL CHECK (tiempo_estimado_min > 0)
);

CREATE TABLE usuarios (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre VARCHAR(100) NOT NULL,
  rol VARCHAR(20) NOT NULL CHECK (rol IN ('cliente','operador','repartidor','admin')),
  email VARCHAR(120) UNIQUE NOT NULL,
  password_hash TEXT NOT NULL
);

CREATE TABLE repartidores (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario_id UUID UNIQUE REFERENCES usuarios(id),
  zona_id INT NOT NULL REFERENCES zonas(id),
  capacidad_max INT NOT NULL CHECK (capacidad_max BETWEEN 1 AND 20),
  carga_actual INT NOT NULL DEFAULT 0,
  activo BOOLEAN NOT NULL DEFAULT true,
  CHECK (carga_actual >= 0 AND carga_actual <= capacidad_max)
);

CREATE TABLE envios (
  codigo VARCHAR(20) PRIMARY KEY,
  cliente_id UUID NOT NULL REFERENCES usuarios(id),
  remitente VARCHAR(100) NOT NULL,
  destinatario VARCHAR(100) NOT NULL,
  origen_lat NUMERIC(9,6) NOT NULL,
  origen_lng NUMERIC(9,6) NOT NULL,
  destino_lat NUMERIC(9,6) NOT NULL,
  destino_lng NUMERIC(9,6) NOT NULL,
  origen_direccion VARCHAR(150),
  destino_direccion VARCHAR(150),
  zona_id INT NOT NULL REFERENCES zonas(id),
  tipo_servicio_id INT NOT NULL REFERENCES tipos_servicio(id),
  estado envio_estado NOT NULL DEFAULT 'creado',
  version INT NOT NULL DEFAULT 1,
  idempotency_key VARCHAR(64) UNIQUE,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE asignaciones (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  envio_codigo VARCHAR(20) NOT NULL REFERENCES envios(codigo),
  repartidor_id UUID NOT NULL REFERENCES repartidores(id),
  activa BOOLEAN NOT NULL DEFAULT true,
  asignado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  aceptada_en TIMESTAMPTZ,
  liberado_en TIMESTAMPTZ
);

CREATE TABLE outbox (
  id BIGSERIAL PRIMARY KEY,
  evento_id VARCHAR(60) UNIQUE NOT NULL,
  envio_codigo VARCHAR(20) NOT NULL,
  tipo VARCHAR(40) NOT NULL,
  payload JSONB NOT NULL,
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  publicado_en TIMESTAMPTZ,
  intentos INT NOT NULL DEFAULT 0
);

-- El historial oficial es el outbox: nunca se borran ni modifican sus filas (solo publicado_en/intentos)
CREATE INDEX idx_outbox_envio ON outbox(envio_codigo, id);

-- 3. Índices Transaccionales y de Restricción
CREATE INDEX idx_envios_estado_zona ON envios(estado, zona_id);
CREATE INDEX idx_envios_creado ON envios(creado_en DESC);
CREATE INDEX idx_envios_cliente ON envios(cliente_id);
CREATE INDEX idx_asignaciones_repartidor ON asignaciones(repartidor_id) WHERE activa;
CREATE INDEX idx_outbox_pendientes ON outbox(id) WHERE publicado_en IS NULL;

-- Restricción fundamental: Un envío solo puede tener UNA asignación activa a la vez
CREATE UNIQUE INDEX uq_asignacion_activa ON asignaciones(envio_codigo) WHERE activa;

-- 4. Índices para Búsqueda Operativa (Reemplazo de Elasticsearch)
CREATE INDEX idx_envios_destinatario_trgm ON envios USING GIN (destinatario gin_trgm_ops);
CREATE INDEX idx_envios_codigo_trgm ON envios USING GIN (codigo gin_trgm_ops);
