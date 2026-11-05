-- Datos semilla de demostración (todos ficticios).
-- Cada envío recorre su máquina de estados y deja sus eventos en `outbox`,
-- de modo que el estado actual (envios.estado) y el historial coinciden.
-- Al arrancar, el worker publica estos eventos en MongoDB (eventos_seguimiento).

INSERT INTO zonas (id, nombre) VALUES (1, 'Norte'), (2, 'Sur'), (3, 'Centro'), (4, 'Valles');
INSERT INTO tipos_servicio (id, nombre, tarifa_base, tiempo_estimado_min) VALUES
(1, 'Express', 5.00, 30), (2, 'Estándar', 2.50, 120), (3, 'Programado', 3.00, 240);

INSERT INTO usuarios (id, nombre, rol, email, password_hash) VALUES
('11111111-1111-1111-1111-111111111111', 'Admin Principal',   'admin',      'admin@mensajeria.local',   'hash_ficticio'),
('22222222-2222-2222-2222-222222222221', 'Operador 1',        'operador',   'op1@mensajeria.local',     'hash_ficticio'),
('22222222-2222-2222-2222-222222222222', 'Operador 2',        'operador',   'op2@mensajeria.local',     'hash_ficticio'),
('33333333-3333-3333-3333-333333333331', 'Cliente 1 (Tienda Andina)',  'cliente', 'cliente1@mail.local', 'hash_ficticio'),
('33333333-3333-3333-3333-333333333332', 'Cliente 2 (Librería Sol)',   'cliente', 'cliente2@mail.local', 'hash_ficticio'),
('33333333-3333-3333-3333-333333333333', 'Cliente 3 (Farmacia Luna)',  'cliente', 'cliente3@mail.local', 'hash_ficticio'),
('44444444-4444-4444-4444-444444444441', 'Repartidor Moto',   'repartidor', 'rep1@mensajeria.local',    'hash_ficticio'),
('44444444-4444-4444-4444-444444444442', 'Repartidor Bici',   'repartidor', 'rep2@mensajeria.local',    'hash_ficticio'),
('44444444-4444-4444-4444-444444444443', 'Repartidor Auto',   'repartidor', 'rep3@mensajeria.local',    'hash_ficticio'),
('44444444-4444-4444-4444-444444444444', 'Repartidor Furgón', 'repartidor', 'rep4@mensajeria.local',    'hash_ficticio');

-- Capacidades distintas; la Bici (capacidad 1) queda libre para la demo de capacidad
INSERT INTO repartidores (id, usuario_id, zona_id, capacidad_max, carga_actual) VALUES
('55555555-5555-5555-5555-555555555551', '44444444-4444-4444-4444-444444444441', 1, 3, 0),
('55555555-5555-5555-5555-555555555552', '44444444-4444-4444-4444-444444444442', 2, 1, 0),
('55555555-5555-5555-5555-555555555553', '44444444-4444-4444-4444-444444444443', 3, 2, 0),
('55555555-5555-5555-5555-555555555554', '44444444-4444-4444-4444-444444444444', 4, 5, 0);

-- Especificación de los 20 envíos semilla: estado final y repartidor (si aplica)
CREATE TEMP TABLE semilla (
  n INT, cliente CHAR(1), remitente TEXT, destinatario TEXT,
  origen TEXT, destino TEXT, zona INT, tipo INT,
  final TEXT, rep CHAR(1), aceptada BOOLEAN
);
INSERT INTO semilla VALUES
( 1,'1','Tienda Andina','Lucía Paredes','Av. Amazonas N24-10','Calle Robles E5-22',1,1,'creado',NULL,false),
( 2,'2','Librería Sol','Mateo Cevallos','Av. 10 de Agosto N30-12','Calle Ulloa N21-44',3,2,'creado',NULL,false),
( 3,'3','Farmacia Luna','Valentina Mora','Av. Maldonado S12-40','Calle Ajaví 230',2,1,'creado',NULL,false),
( 4,'1','Tienda Andina','Sebastián Ríos','Av. Amazonas N24-10','Av. Ilaló km 2',4,3,'creado',NULL,false),
( 5,'2','Librería Sol','Camila Andrade','Av. 10 de Agosto N30-12','Av. Eloy Alfaro N40-15',1,2,'creado',NULL,false),
( 6,'3','Farmacia Luna','Daniel Vásconez','Av. Maldonado S12-40','Calle Venezuela 812',3,1,'creado',NULL,false),
( 7,'1','Tienda Andina','Isabella Torres','Av. Amazonas N24-10','Av. de la Prensa N52-30',1,1,'aceptado',NULL,false),
( 8,'2','Librería Sol','Nicolás Jaramillo','Av. 10 de Agosto N30-12','Calle Chimborazo S3-18',2,2,'aceptado',NULL,false),
( 9,'3','Farmacia Luna','Emilia Salazar','Av. Maldonado S12-40','Av. General Rumiñahui 1200',4,2,'aceptado',NULL,false),
(10,'1','Tienda Andina','Martín Guerrero','Av. Amazonas N24-10','Calle Guayaquil N8-20',3,3,'aceptado',NULL,false),
(11,'1','Tienda Andina','Sofía Villacís','Av. Amazonas N24-10','Av. Occidental N60-11',1,1,'asignado','1',false),
(12,'2','Librería Sol','Joaquín Benítez','Av. 10 de Agosto N30-12','Calle Los Shyris 410',4,2,'asignado','4',true),
(13,'3','Farmacia Luna','Antonella Castro','Av. Maldonado S12-40','Av. San Luis 55',4,1,'retirado','4',true),
(14,'1','Tienda Andina','Gabriel Espinosa','Av. Amazonas N24-10','Calle Juan León Mera N23-15',1,1,'en_transito','1',true),
(15,'2','Librería Sol','Renata Ortiz','Av. 10 de Agosto N30-12','Calle Flores 610',3,2,'en_transito','3',true),
(16,'3','Farmacia Luna','Tomás Herrera','Av. Maldonado S12-40','Av. Interoceánica km 12',4,3,'incidencia','4',true),
(17,'1','Tienda Andina','Martina Zambrano','Av. Amazonas N24-10','Av. Naciones Unidas E3-40',1,1,'entregado','1',true),
(18,'2','Librería Sol','Lucas Montalvo','Av. 10 de Agosto N30-12','Calle García Moreno 1050',3,2,'entregado','3',true),
(19,'3','Farmacia Luna','Paula Cárdenas','Av. Maldonado S12-40','Calle Michelena S5-11',2,1,'cancelado_cliente',NULL,false),
(20,'2','Librería Sol','Diego Proaño','Av. 10 de Agosto N30-12','Av. Napo S8-77',2,2,'cancelado_operador',NULL,false);

DO $$
DECLARE
  s RECORD;
  v_cod TEXT;
  pasos TEXT[];
  paso TEXT;
  anterior TEXT;
  nuevo TEXT;
  seq INT;
  ts TIMESTAMPTZ;
  actor_id TEXT;
  actor_rol TEXT;
  tipo_ev TEXT;
  nota TEXT;
  extra JSONB;
  rep_id UUID;
  rep_usuario UUID;
  cliente_uuid UUID;
  iso TEXT;
BEGIN
  FOR s IN SELECT * FROM semilla ORDER BY n LOOP
    v_cod := 'ENV-SEED' || lpad(s.n::text, 4, '0');
    cliente_uuid := ('33333333-3333-3333-3333-33333333333' || s.cliente)::uuid;
    ts := date_trunc('day', now()) - ((21 - s.n) || ' days')::interval + interval '8 hours';

    pasos := CASE s.final
      WHEN 'creado'             THEN ARRAY[]::TEXT[]
      WHEN 'aceptado'           THEN ARRAY['aceptado']
      WHEN 'asignado'           THEN ARRAY['aceptado','asignado']
      WHEN 'retirado'           THEN ARRAY['aceptado','asignado','retirado']
      WHEN 'en_transito'        THEN ARRAY['aceptado','asignado','retirado','en_transito']
      WHEN 'incidencia'         THEN ARRAY['aceptado','asignado','retirado','en_transito','incidencia']
      WHEN 'entregado'          THEN ARRAY['aceptado','asignado','retirado','en_transito','entregado']
      WHEN 'cancelado_cliente'  THEN ARRAY['cancelado']
      WHEN 'cancelado_operador' THEN ARRAY['aceptado','cancelado']
    END;
    -- La aceptación del repartidor va justo después de la asignación
    IF s.aceptada THEN
      pasos := array_replace(pasos, 'asignado', 'asignado|aceptacion');
      pasos := string_to_array(array_to_string(pasos, '|'), '|');
    END IF;

    IF s.rep IS NOT NULL THEN
      rep_id := ('55555555-5555-5555-5555-55555555555' || s.rep)::uuid;
      rep_usuario := ('44444444-4444-4444-4444-44444444444' || s.rep)::uuid;
    ELSE
      rep_id := NULL; rep_usuario := NULL;
    END IF;

    INSERT INTO envios (codigo, cliente_id, remitente, destinatario,
                        origen_lat, origen_lng, destino_lat, destino_lng,
                        origen_direccion, destino_direccion,
                        zona_id, tipo_servicio_id, estado, version, creado_en, actualizado_en)
    VALUES (v_cod, cliente_uuid, s.remitente, s.destinatario,
            -0.180000 - s.n * 0.001, -78.470000 + s.n * 0.001,
            -0.210000 + s.n * 0.002, -78.490000 + s.n * 0.001,
            s.origen, s.destino, s.zona, s.tipo, 'creado', 1, ts, ts);

    -- Evento 1: creación (mismo formato que genera la API)
    seq := 1;
    iso := to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"');
    INSERT INTO outbox (evento_id, envio_codigo, tipo, payload, creado_en) VALUES (
      v_cod || '-0001', v_cod, 'creacion',
      jsonb_build_object('evento_id', v_cod || '-0001', 'envio_codigo', v_cod, 'secuencia', 1,
        'tipo', 'creacion', 'estado_anterior', NULL, 'estado_nuevo', 'creado',
        'actor', jsonb_build_object('id', cliente_uuid::text, 'rol', 'cliente'),
        'nota', 'Envío registrado', 'ocurrido_en', iso, 'registrado_en', iso),
      ts);

    anterior := 'creado';
    FOREACH paso IN ARRAY pasos LOOP
      seq := seq + 1;
      ts := ts + interval '35 minutes';
      iso := to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"');
      extra := '{}'::jsonb;
      tipo_ev := 'cambio_estado';
      nuevo := paso;

      IF paso = 'aceptado' THEN
        actor_id := '22222222-2222-2222-2222-222222222221'; actor_rol := 'operador';
        nota := 'Solicitud validada por operación';
      ELSIF paso = 'asignado' THEN
        actor_id := '22222222-2222-2222-2222-222222222221'; actor_rol := 'operador';
        tipo_ev := 'asignacion';
        nota := 'Asignado al repartidor ' || rep_id;
        extra := jsonb_build_object('repartidor_id', rep_id::text);
        INSERT INTO asignaciones (envio_codigo, repartidor_id, asignado_en) VALUES (v_cod, rep_id, ts);
      ELSIF paso = 'aceptacion' THEN
        actor_id := rep_usuario::text; actor_rol := 'repartidor';
        tipo_ev := 'aceptacion_asignacion';
        nuevo := anterior;
        nota := 'El repartidor aceptó la asignación';
        extra := jsonb_build_object('repartidor_id', rep_id::text);
        UPDATE asignaciones SET aceptada_en = ts WHERE envio_codigo = v_cod AND activa;
      ELSIF paso = 'cancelado' THEN
        IF s.final = 'cancelado_cliente' THEN
          actor_id := cliente_uuid::text; actor_rol := 'cliente'; nota := 'Cancelado por el cliente';
        ELSE
          actor_id := '22222222-2222-2222-2222-222222222222'; actor_rol := 'operador'; nota := 'Cancelado por operación: dirección incompleta';
        END IF;
      ELSE
        actor_id := rep_usuario::text; actor_rol := 'repartidor';
        nota := CASE paso
          WHEN 'retirado' THEN 'Paquete retirado en origen'
          WHEN 'en_transito' THEN 'En camino al destino'
          WHEN 'incidencia' THEN 'Destinatario ausente; se reintentará'
          WHEN 'entregado' THEN 'Entrega confirmada con evidencia'
        END;
        extra := jsonb_build_object('ubicacion', jsonb_build_object('type', 'Point',
                   'coordinates', jsonb_build_array(-78.48 + seq * 0.001, -0.19 - seq * 0.001)));
        IF paso = 'entregado' THEN
          extra := extra || jsonb_build_object('evidencia_ref', v_cod);
        END IF;
      END IF;

      IF paso IN ('entregado', 'cancelado') THEN
        UPDATE asignaciones SET activa = false, liberado_en = ts WHERE envio_codigo = v_cod AND activa;
      END IF;

      INSERT INTO outbox (evento_id, envio_codigo, tipo, payload, creado_en) VALUES (
        v_cod || '-' || lpad(seq::text, 4, '0'), v_cod, tipo_ev,
        jsonb_build_object('evento_id', v_cod || '-' || lpad(seq::text, 4, '0'), 'envio_codigo', v_cod,
          'secuencia', seq, 'tipo', tipo_ev, 'estado_anterior', anterior, 'estado_nuevo', nuevo,
          'actor', jsonb_build_object('id', actor_id, 'rol', actor_rol),
          'nota', nota, 'ocurrido_en', iso, 'registrado_en', iso) || extra,
        ts);
      anterior := nuevo;
    END LOOP;

    UPDATE envios SET estado = anterior::envio_estado, version = seq, actualizado_en = ts WHERE codigo = v_cod;
  END LOOP;

  -- La carga actual se deriva de las asignaciones activas (mismo invariante que mantiene la API)
  UPDATE repartidores r SET carga_actual =
    (SELECT count(*) FROM asignaciones a WHERE a.repartidor_id = r.id AND a.activa);
END $$;

DROP TABLE semilla;

SELECT setval(pg_get_serial_sequence('zonas', 'id'), 5, false);
SELECT setval(pg_get_serial_sequence('tipos_servicio', 'id'), 4, false);
