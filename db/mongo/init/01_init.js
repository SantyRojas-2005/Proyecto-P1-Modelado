// Inicialización de MongoDB 7 (se ejecuta solo cuando el volumen está vacío).
// Las fechas se guardan como texto ISO 8601 en UTC, igual que en el outbox de PostgreSQL.
db = db.getSiblingDB('mensajeria');

// 1. Historial de seguimiento: proyección del outbox de PostgreSQL (lo escribe solo el worker)
db.createCollection("eventos_seguimiento", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["evento_id", "envio_codigo", "secuencia", "tipo", "estado_nuevo", "actor", "ocurrido_en"],
      properties: {
        evento_id: { bsonType: "string" },
        envio_codigo: { bsonType: "string" },
        secuencia: { bsonType: ["int", "long"], minimum: 1 },
        tipo: { bsonType: "string" },
        estado_nuevo: { bsonType: "string" },
        actor: { bsonType: "object", required: ["id", "rol"] },
        ocurrido_en: { bsonType: "string" }
      }
    }
  }
});
// Idempotencia del worker: un evento no se inserta dos veces
db.eventos_seguimiento.createIndex({ evento_id: 1 }, { unique: true });
// Orden de la línea de tiempo y unicidad de la secuencia por envío
db.eventos_seguimiento.createIndex({ envio_codigo: 1, secuencia: 1 }, { unique: true });
db.eventos_seguimiento.createIndex({ ocurrido_en: -1 });

// 2. Evidencias de entrega: documentos heterogéneos (los escribe la API)
db.createCollection("evidencias_entrega", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["envio_codigo", "receptor_nombre", "registrado_por", "registrado_en"],
      properties: {
        envio_codigo: { bsonType: "string" },
        receptor_nombre: { bsonType: "string" },
        registrado_por: { bsonType: "string" }
      }
    }
  }
});
// Una sola evidencia por envío: el upsert de la API es idempotente
db.evidencias_entrega.createIndex({ envio_codigo: 1 }, { unique: true });

// 3. Evidencias de los dos envíos semilla entregados (ENV-SEED0017 y ENV-SEED0018).
// Sus eventos NO se insertan aquí: el worker los copia desde el outbox al arrancar.
db.evidencias_entrega.insertMany([
  {
    envio_codigo: "ENV-SEED0017",
    receptor_nombre: "Martina Zambrano",
    observacion: "Recibido en recepción del edificio",
    registrado_por: "44444444-4444-4444-4444-444444444441",
    registrado_en: "2026-10-31T16:30:00+00:00",
    foto: { tipo: "referencia", valor: "evidencias/ENV-SEED0017.jpg" },
    extras: { firma_simulada: true }
  },
  {
    envio_codigo: "ENV-SEED0018",
    receptor_nombre: "Guardia del conjunto (ficticio)",
    observacion: "Destinatario autorizó dejarlo en garita",
    registrado_por: "44444444-4444-4444-4444-444444444443",
    registrado_en: "2026-11-01T16:30:00+00:00",
    extras: { firma_simulada: true, documento_receptor: "ficticio-0018" }
  }
]);
