// Frontend de Mensajería Urbana. Toda la información pasa por la API REST; el navegador
// nunca se conecta a las bases de datos ni conoce credenciales.
const API_URL = `${window.location.protocol}//${window.location.hostname}:8000`;
const ESTADOS_ACTIVOS = ['asignado', 'retirado', 'en_transito', 'incidencia'];
const ESTADOS_FINALES = ['entregado', 'cancelado'];
const COLOR_ESTADO = {
    creado: 'secondary', aceptado: 'info', asignado: 'primary', retirado: 'primary',
    en_transito: 'warning', incidencia: 'danger', entregado: 'success', cancelado: 'dark'
};
const ETIQUETA_ESTADO = {
    creado: 'Creado', aceptado: 'Aceptado', asignado: 'Asignado', retirado: 'Retirado',
    en_transito: 'En tránsito', incidencia: 'Incidencia', entregado: 'Entregado', cancelado: 'Cancelado'
};

let currentActor = { id: '', rol: '' };
let modalEvidencia = null;
let gpsInterval = null;
let seguimientoInterval = null;
let miRepartidorId = null;
let repartidoresCache = [];
let zonasCache = [];
let serviciosCache = [];

// ========================== UTILIDADES ==========================
function generateUUID() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

function escapeHTML(valor) {
    if (valor === null || valor === undefined) return '';
    return valor.toString().replace(/[&<>'"]/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[c]));
}

function badgeEstado(estado) {
    return `<span class="badge bg-${COLOR_ESTADO[estado] || 'secondary'} text-uppercase">${escapeHTML(ETIQUETA_ESTADO[estado] || estado)}</span>`;
}

function fecha(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return isNaN(d) ? escapeHTML(iso) : d.toLocaleString();
}

function showAlert(message, type = 'danger') {
    const container = document.getElementById('alertContainer');
    container.innerHTML = `<div class="alert alert-${type} alert-dismissible fade show border-0 shadow-sm rounded-3">${escapeHTML(message)}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>`;
    window.scrollTo({ top: 0, behavior: 'smooth' });
    clearTimeout(showAlert._t);
    showAlert._t = setTimeout(() => { container.innerHTML = ''; }, 7000);
}

// Llamada única a la API: añade cabeceras de actor y traduce errores a un mensaje legible
async function api(method, path, body = null, extraHeaders = {}) {
    const headers = { 'X-Actor-Id': currentActor.id, 'X-Actor-Rol': currentActor.rol, ...extraHeaders };
    if (body !== null) headers['Content-Type'] = 'application/json';
    let res;
    try {
        res = await fetch(`${API_URL}${path}`, { method, headers, body: body !== null ? JSON.stringify(body) : undefined });
    } catch (e) {
        return { ok: false, status: 0, data: null, mensaje: `No se pudo conectar con la API en ${API_URL}. Revise "docker compose ps" y "docker compose logs api".` };
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* respuesta sin cuerpo */ }
    let mensaje = '';
    if (!res.ok) {
        const err = data && data.error;
        mensaje = err ? `${err.mensaje} [${err.codigo}]` : `Error HTTP ${res.status}`;
        if (err && err.detalles && err.detalles.campos) {
            mensaje += ': ' + err.detalles.campos.map(c => `${c.campo} → ${c.mensaje}`).join('; ');
        }
    }
    return { ok: res.ok, status: res.status, data, mensaje };
}

// Coordenadas ficticias dentro de la ciudad (no se usa GPS real)
function simularCoordenada() {
    return {
        lat: parseFloat((-0.1807 + (Math.random() - 0.5) * 0.1).toFixed(6)),
        lng: parseFloat((-78.4678 + (Math.random() - 0.5) * 0.1).toFixed(6))
    };
}

// ========================== NAVEGACIÓN Y SESIÓN ==========================
function updateActor() {
    const selector = document.getElementById('actorSelector');
    const opcion = selector.options[selector.selectedIndex];
    currentActor = { id: opcion.getAttribute('data-id'), rol: selector.value };
    miRepartidorId = null;
    if (gpsInterval) { document.getElementById('gpsToggle').checked = false; toggleGPS(); }

    document.querySelectorAll('#mainMenu [data-roles]').forEach(li => {
        li.style.display = li.dataset.roles.split(' ').includes(currentActor.rol) ? '' : 'none';
    });
    resetFormEnvio();
    const inicio = { cliente: 'solicitud', operador: 'despacho', repartidor: 'repartidor', admin: 'despacho' };
    showView(inicio[currentActor.rol]);
}

function showView(viewId) {
    clearInterval(seguimientoInterval);
    document.querySelectorAll('.view-section').forEach(el => el.classList.add('d-none'));
    document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));
    document.getElementById(`view-${viewId}`).classList.remove('d-none');
    const activeBtn = document.getElementById(`nav-${viewId}`);
    if (activeBtn) activeBtn.classList.add('active');

    if (viewId === 'solicitud') cargarCatalogos();
    if (viewId === 'misenvios') cargarMisEnvios();
    if (viewId === 'despacho') { poblarFiltrosDespacho(); cargarDespacho(); }
    if (viewId === 'repartidor') cargarTareasRepartidor();
    if (viewId === 'admin') cargarAdmin();
}

function verSeguimiento(codigo) {
    document.getElementById('inputCodigoSeguimiento').value = codigo;
    showView('seguimiento');
    buscarSeguimiento();
}

// ========================== SOLICITUD Y COMPROBANTE ==========================
async function cargarCatalogos() {
    const [zonas, servicios] = await Promise.all([api('GET', '/zonas'), api('GET', '/tipos-servicio')]);
    if (!zonas.ok || !servicios.ok) { showAlert(zonas.mensaje || servicios.mensaje); return; }
    document.getElementById('zona_id').innerHTML = '<option value="">Zona…</option>' +
        zonas.data.map(z => `<option value="${escapeHTML(z.id)}">${escapeHTML(z.nombre)}</option>`).join('');
    document.getElementById('tipo_servicio_id').innerHTML = '<option value="">Tipo de servicio…</option>' +
        servicios.data.map(s => `<option value="${escapeHTML(s.id)}">${escapeHTML(s.nombre)} ($${escapeHTML(s.tarifa_base)} · ${escapeHTML(s.tiempo_estimado_min)} min)</option>`).join('');

    const grupo = document.getElementById('grupoCliente');
    if (currentActor.rol === 'operador' || currentActor.rol === 'admin') {
        const clientes = await api('GET', '/clientes');
        if (clientes.ok) {
            document.getElementById('cliente_id').innerHTML = clientes.data.map(c => `<option value="${escapeHTML(c.id)}">${escapeHTML(c.nombre)}</option>`).join('');
        }
        grupo.classList.remove('d-none');
    } else {
        grupo.classList.add('d-none');
    }
}

async function crearEnvio(event) {
    event.preventDefault();
    const boton = document.getElementById('btnSubmitEnvio');
    const payload = {
        remitente: document.getElementById('remitente').value,
        destinatario: document.getElementById('destinatario').value,
        origen_direccion: document.getElementById('origen_dir').value,
        destino_direccion: document.getElementById('destino_dir').value,
        origen: simularCoordenada(), destino: simularCoordenada(),
        zona_id: parseInt(document.getElementById('zona_id').value),
        tipo_servicio_id: parseInt(document.getElementById('tipo_servicio_id').value)
    };
    if (currentActor.rol !== 'cliente') payload.cliente_id = document.getElementById('cliente_id').value;

    // La misma clave se reutiliza si el usuario pulsa dos veces o se reintenta: no se duplica el envío
    const form = document.getElementById('formCrearEnvio');
    if (!form.dataset.idempotencia) form.dataset.idempotencia = generateUUID();
    boton.disabled = true;
    const r = await api('POST', '/envios', payload, { 'Idempotency-Key': form.dataset.idempotencia });
    boton.disabled = false;
    if (!r.ok) { showAlert(`No se registró el envío: ${r.mensaje}`); return; }

    const e = r.data;
    form.classList.add('d-none');
    document.getElementById('resultadoEnvio').classList.remove('d-none');
    document.getElementById('codigoGenerado').innerText = e.codigo;
    document.getElementById('comprobante').innerHTML = `
        <div class="row g-2 small">
            ${dato('Estado', badgeEstado(e.estado), true)}
            ${dato('Fecha', fecha(e.creado_en))}
            ${dato('Cliente', escapeHTML(e.cliente))}
            ${dato('Remitente', escapeHTML(e.remitente))}
            ${dato('Destinatario', escapeHTML(e.destinatario))}
            ${dato('Zona', escapeHTML(e.zona))}
            ${dato('Recogida', escapeHTML(e.origen_direccion))}
            ${dato('Entrega', escapeHTML(e.destino_direccion))}
            ${dato('Servicio', `${escapeHTML(e.tipo_servicio)} · ${escapeHTML(e.tiempo_estimado_min)} min`)}
            ${dato('Tarifa base', `$${escapeHTML(e.tarifa_base)}`)}
        </div>`;
    showAlert(r.status === 200 ? 'La solicitud ya estaba registrada (reintento): se muestra el mismo comprobante.' : 'Envío registrado correctamente.', 'success');
}

function dato(etiqueta, valorHtml, crudo = false) {
    return `<div class="col-6 col-md-4"><div class="text-muted text-uppercase" style="font-size:.7rem">${escapeHTML(etiqueta)}</div><div class="fw-semibold">${valorHtml || '<span class="text-muted">—</span>'}</div></div>`;
}

function resetFormEnvio() {
    const form = document.getElementById('formCrearEnvio');
    form.reset();
    delete form.dataset.idempotencia;
    form.classList.remove('d-none');
    document.getElementById('resultadoEnvio').classList.add('d-none');
}

// ========================== MIS ENVÍOS (cliente) ==========================
async function cargarMisEnvios() {
    const r = await api('GET', '/envios?limite=100');
    const tabla = document.getElementById('tablaMisEnvios');
    if (!r.ok) { showAlert(r.mensaje); return; }
    if (r.data.resultados.length === 0) { tabla.innerHTML = '<tr><td colspan="6" class="text-muted">Aún no tiene envíos.</td></tr>'; return; }
    tabla.innerHTML = r.data.resultados.map(e => `
        <tr>
            <td class="fw-bold">${escapeHTML(e.codigo)}</td>
            <td>${escapeHTML(e.destinatario)}</td>
            <td>${escapeHTML(e.zona)}</td>
            <td>${badgeEstado(e.estado)}</td>
            <td class="small text-muted">${fecha(e.creado_en)}</td>
            <td class="text-end">
                <button class="btn btn-sm btn-outline-dark" onclick="verSeguimiento('${escapeHTML(e.codigo)}')">Ver</button>
                ${e.acciones.includes('cancelado') ? `<button class="btn btn-sm btn-outline-danger" onclick="cancelarEnvio('${escapeHTML(e.codigo)}', cargarMisEnvios)">Cancelar</button>` : ''}
            </td>
        </tr>`).join('');
}

async function cancelarEnvio(codigo, alTerminar) {
    if (!confirm(`¿Cancelar el envío ${codigo}? El historial se conserva.`)) return;
    const motivo = currentActor.rol === 'cliente' ? 'Cancelado por el cliente' : (prompt('Motivo de la cancelación:', 'Cancelado por operación') || 'Cancelado por operación');
    await ejecutarCambio(codigo, 'cancelado', motivo, alTerminar);
}

// ========================== SEGUIMIENTO Y DETALLE ==========================
async function buscarSeguimiento(silencioso = false) {
    const codigo = document.getElementById('inputCodigoSeguimiento').value.trim().toUpperCase();
    if (!codigo) return;
    const [detalle, seg] = await Promise.all([
        api('GET', `/envios/${encodeURIComponent(codigo)}`),
        api('GET', `/envios/${encodeURIComponent(codigo)}/seguimiento`)
    ]);
    const contenedor = document.getElementById('resultadoSeguimiento');
    if (!detalle.ok || !seg.ok) {
        clearInterval(seguimientoInterval);
        contenedor.classList.add('d-none');
        if (!silencioso) showAlert(detalle.mensaje || seg.mensaje);
        return;
    }
    const e = detalle.data, s = seg.data;
    contenedor.classList.remove('d-none');
    document.getElementById('segCodigo').innerText = e.codigo;
    const badge = document.getElementById('segEstado');
    badge.className = `badge fs-6 text-uppercase bg-${COLOR_ESTADO[s.estado_actual] || 'secondary'}`;
    badge.innerText = ETIQUETA_ESTADO[s.estado_actual] || s.estado_actual;
    document.getElementById('segActualizado').innerText = `Estado actual desde PostgreSQL · versión ${e.version} · ${new Date().toLocaleTimeString()}`;
    document.getElementById('segDetalle').innerHTML = `
        ${dato('Cliente', escapeHTML(e.cliente))}
        ${dato('Remitente', escapeHTML(e.remitente))}
        ${dato('Destinatario', escapeHTML(e.destinatario))}
        ${dato('Recogida', escapeHTML(e.origen_direccion))}
        ${dato('Entrega', escapeHTML(e.destino_direccion))}
        ${dato('Zona / servicio', `${escapeHTML(e.zona)} · ${escapeHTML(e.tipo_servicio)}`)}
        ${dato('Repartidor', e.repartidor_nombre
            ? `${escapeHTML(e.repartidor_nombre)} ${e.asignacion_aceptada ? '<span class="badge bg-success">aceptó</span>' : '<span class="badge bg-warning text-dark">pendiente de aceptar</span>'}`
            : (e.ultimo_repartidor ? `${escapeHTML(e.ultimo_repartidor)} <span class="badge bg-secondary">finalizado</span>` : ''))}
        ${dato('Registrado', fecha(e.creado_en))}
        ${dato('Última actualización', fecha(e.actualizado_en))}`;

    const aviso = document.getElementById('segAviso');
    if (s.fuente_historial === 'postgresql_outbox') {
        aviso.style.display = 'block';
        aviso.className = 'alert alert-warning small border-0';
        aviso.innerText = 'MongoDB no está disponible: el historial se muestra desde el registro oficial (outbox de PostgreSQL). Las evidencias no se pueden consultar hasta que vuelva.';
    } else if (s.pendientes_sincronizacion > 0) {
        aviso.style.display = 'block';
        aviso.className = 'alert alert-info small border-0';
        aviso.innerText = `⏳ ${s.pendientes_sincronizacion} evento(s) aún no llegan al historial (MongoDB). El estado actual ya es correcto; la línea de tiempo se completará cuando el sincronizador los procese.`;
    } else { aviso.style.display = 'none'; }

    document.getElementById('segFuente').innerText = `(fuente: ${s.fuente_historial === 'mongodb' ? 'MongoDB' : 'outbox PostgreSQL'})`;
    document.getElementById('segTimeline').innerHTML = s.eventos.length === 0
        ? '<li class="text-muted small">Sin eventos sincronizados todavía.</li>'
        : s.eventos.map(ev => itemTimeline(ev)).join('');

    const evDiv = document.getElementById('evidenciaContenedor');
    evDiv.innerHTML = '';
    if (s.estado_actual === 'entregado') {
        const ev = await api('GET', `/envios/${encodeURIComponent(codigo)}/evidencia`);
        evDiv.innerHTML = ev.ok ? `
            <div class="mt-4 p-4 bg-light border rounded-3">
                <h6 class="fw-bold mb-3 text-muted small text-uppercase">Prueba de entrega (MongoDB)</h6>
                <div class="row g-2 small">
                    ${dato('Recibido por', escapeHTML(ev.data.receptor_nombre))}
                    ${dato('Observación', escapeHTML(ev.data.observacion))}
                    ${dato('Registrada', fecha(ev.data.registrado_en))}
                    ${dato('Foto (referencia simulada)', ev.data.foto ? `<code>${escapeHTML(ev.data.foto.valor)}</code>` : '')}
                    ${dato('Firma simulada', ev.data.extras && ev.data.extras.firma_simulada ? 'Sí' : 'No')}
                </div>
            </div>` : `<div class="alert alert-warning small mt-4">No se pudo leer la evidencia: ${escapeHTML(ev.mensaje)}</div>`;
    }

    // Refresco automático mientras el envío siga en curso
    clearInterval(seguimientoInterval);
    if (!ESTADOS_FINALES.includes(s.estado_actual) || s.pendientes_sincronizacion > 0) {
        seguimientoInterval = setInterval(() => buscarSeguimiento(true), 8000);
    }
}

function itemTimeline(ev) {
    const titulos = {
        creacion: 'Envío creado', asignacion: 'Repartidor asignado',
        aceptacion_asignacion: 'Asignación aceptada por el repartidor', reasignacion: 'Repartidor reasignado'
    };
    const titulo = titulos[ev.tipo] || `Cambio a ${ETIQUETA_ESTADO[ev.estado_nuevo] || ev.estado_nuevo}`;
    const autor = ev.actor ? `${escapeHTML(ev.actor.nombre || ev.actor.id)} (${escapeHTML(ev.actor.rol)})` : '';
    const ubic = ev.ubicacion && ev.ubicacion.coordinates
        ? `<div class="small text-muted">Ubicación simulada: ${ev.ubicacion.coordinates[1].toFixed(4)}, ${ev.ubicacion.coordinates[0].toFixed(4)}</div>` : '';
    return `
        <li class="timeline-item">
            <div class="d-flex justify-content-between flex-wrap gap-2">
                <div><span class="text-muted small me-2">#${escapeHTML(ev.secuencia)}</span><b>${escapeHTML(titulo)}</b> ${badgeEstado(ev.estado_nuevo)}</div>
                <small class="text-muted">${fecha(ev.ocurrido_en)}</small>
            </div>
            <div class="small">Por ${autor}${ev.nota ? ` · ${escapeHTML(ev.nota)}` : ''}</div>
            ${ubic}
        </li>`;
}

// ========================== DESPACHO ==========================
async function poblarFiltrosDespacho() {
    const select = document.getElementById('filtroZona');
    if (select.options.length > 1) return;
    const r = await api('GET', '/zonas');
    if (r.ok) select.innerHTML = '<option value="">Todas las zonas</option>' + r.data.map(z => `<option value="${escapeHTML(z.id)}">${escapeHTML(z.nombre)}</option>`).join('');
}

async function cargarDespacho() {
    const params = new URLSearchParams({ limite: 100 });
    const filtros = { q: 'filtroQ', estado: 'filtroEstado', zona_id: 'filtroZona', desde: 'filtroDesde', hasta: 'filtroHasta' };
    for (const [clave, id] of Object.entries(filtros)) {
        const valor = document.getElementById(id).value;
        if (valor) params.set(clave, valor);
    }
    const [envios, reps] = await Promise.all([api('GET', `/envios?${params}`), api('GET', '/repartidores')]);
    if (!envios.ok || !reps.ok) { showAlert(envios.mensaje || reps.mensaje); return; }
    repartidoresCache = reps.data;

    // Conexión (Redis) consultada una sola vez por repartidor
    const conexion = {};
    await Promise.all(repartidoresCache.map(async rep => {
        const t = await api('GET', `/repartidores/${rep.id}/estado-temporal`);
        conexion[rep.id] = t.ok ? t.data.conectado : null;
    }));

    document.getElementById('despachoTotal').innerText = `${envios.data.resultados.length} de ${envios.data.total} envíos · fuente: ${envios.data.fuente}`;
    const tabla = document.getElementById('tablaDespacho');
    if (envios.data.resultados.length === 0) { tabla.innerHTML = '<tr><td colspan="6" class="text-muted">Sin resultados para los filtros.</td></tr>'; return; }
    tabla.innerHTML = envios.data.resultados.map(e => {
        const c = escapeHTML(e.codigo);
        let repartidor = '<span class="text-muted small">—</span>';
        if (e.repartidor_id) {
            const online = conexion[e.repartidor_id];
            repartidor = `${escapeHTML(e.repartidor_nombre)}<br><small class="${online ? 'text-success' : 'text-muted'}">${online ? '🟢 conectado' : '⚪ sin conexión'}</small>
                ${e.asignacion_aceptada ? '' : '<br><span class="badge bg-warning text-dark">sin aceptar</span>'}`;
        }
        const botones = [];
        if (e.acciones.includes('aceptado')) botones.push(`<button class="btn btn-sm btn-dark" onclick="ejecutarCambio('${c}', 'aceptado', 'Solicitud validada', cargarDespacho)">Validar</button>`);
        if (e.acciones.includes('asignar') || e.acciones.includes('reasignar')) {
            const accion = e.acciones.includes('asignar') ? 'asignar' : 'reasignar';
            botones.push(`
                <div class="input-group input-group-sm mt-1">
                    <select class="form-select" id="rep_${c}">
                        <option value="">Repartidor…</option>
                        ${repartidoresCache.filter(r => r.id !== e.repartidor_id).map(r => `<option value="${escapeHTML(r.id)}">${escapeHTML(r.nombre)} · ${escapeHTML(r.zona)} (${r.carga_actual}/${r.capacidad_max})${r.carga_actual >= r.capacidad_max ? ' lleno' : ''}</option>`).join('')}
                    </select>
                    <button class="btn ${accion === 'asignar' ? 'btn-dark' : 'btn-warning'}" onclick="asignarRepartidor('${c}', '${accion}')">${accion === 'asignar' ? 'Asignar' : 'Reasignar'}</button>
                </div>`);
        }
        if (e.acciones.includes('incidencia')) botones.push(`<button class="btn btn-sm btn-outline-danger" onclick="reportarIncidencia('${c}', cargarDespacho)">Incidencia</button>`);
        if (e.acciones.includes('cancelado')) botones.push(`<button class="btn btn-sm btn-outline-secondary" onclick="cancelarEnvio('${c}', cargarDespacho)">Cancelar</button>`);
        botones.push(`<button class="btn btn-sm btn-link" onclick="verSeguimiento('${c}')">Detalle</button>`);
        return `<tr>
            <td class="fw-bold text-nowrap">${c}<br><small class="text-muted fw-normal">${fecha(e.creado_en)}</small></td>
            <td>${escapeHTML(e.destinatario)}<br><small class="text-muted">${escapeHTML(e.cliente)}</small></td>
            <td>${escapeHTML(e.zona)}<br><small class="text-muted">${escapeHTML(e.tipo_servicio)}</small></td>
            <td>${badgeEstado(e.estado)}</td>
            <td>${repartidor}</td>
            <td><div class="d-flex flex-wrap gap-1 align-items-center">${botones.join('')}</div></td>
        </tr>`;
    }).join('');
}

async function asignarRepartidor(codigo, accion) {
    const repartidorId = document.getElementById(`rep_${codigo}`).value;
    if (!repartidorId) { showAlert('Seleccione un repartidor.', 'warning'); return; }
    const ruta = accion === 'asignar' ? '/asignaciones' : '/asignaciones/reasignar';
    const r = await api('POST', ruta, { evento_id: generateUUID(), envio_codigo: codigo, repartidor_id: repartidorId });
    if (r.ok) { showAlert(accion === 'asignar' ? 'Asignado. El repartidor debe aceptar la asignación.' : 'Reasignado. El nuevo repartidor debe aceptar la asignación.', 'success'); }
    else { showAlert(`No se pudo ${accion}: ${r.mensaje}`); }
    cargarDespacho();
}

// Cambio de estado genérico (PATCH). Un evento_id nuevo por intención del usuario.
async function ejecutarCambio(codigo, nuevoEstado, nota, alTerminar, ubicacion = null) {
    const cuerpo = { evento_id: generateUUID(), nuevo_estado: nuevoEstado, nota };
    if (ubicacion) cuerpo.ubicacion = ubicacion;
    const r = await api('PATCH', `/envios/${encodeURIComponent(codigo)}/estado`, cuerpo);
    if (r.ok) showAlert(`${codigo}: ${ETIQUETA_ESTADO[nuevoEstado] || nuevoEstado}.`, 'success');
    else showAlert(`No se pudo cambiar el estado: ${r.mensaje}`);
    if (alTerminar) alTerminar();
}

async function reportarIncidencia(codigo, alTerminar) {
    const nota = prompt('Describa la incidencia:', 'Destinatario ausente');
    if (nota === null) return;
    const ubicacion = currentActor.rol === 'repartidor' ? simularCoordenada() : null;
    await ejecutarCambio(codigo, 'incidencia', nota || 'Incidencia', alTerminar, ubicacion);
}

// ========================== REPARTIDOR ==========================
async function obtenerMiRepartidor() {
    if (miRepartidorId) return miRepartidorId;
    const r = await api('GET', '/repartidores');
    if (!r.ok) { showAlert(r.mensaje); return null; }
    const yo = r.data.find(x => x.usuario_id === currentActor.id);
    miRepartidorId = yo ? yo.id : null;
    return miRepartidorId;
}

async function cargarTareasRepartidor() {
    const contenedor = document.getElementById('listaTareasRepartidor');
    const miId = await obtenerMiRepartidor();
    if (!miId) { contenedor.innerHTML = '<p class="text-muted">Este usuario no tiene perfil de repartidor.</p>'; return; }
    actualizarEstadoRedis();

    // La API ya filtra: un repartidor solo recibe envíos que le fueron asignados
    const r = await api('GET', '/envios?limite=100');
    if (!r.ok) { showAlert(r.mensaje); return; }
    const tareas = r.data.resultados.filter(e => ESTADOS_ACTIVOS.includes(e.estado) && e.repartidor_id === miId);
    if (tareas.length === 0) { contenedor.innerHTML = '<p class="text-muted">No tiene tareas activas.</p>'; return; }

    contenedor.innerHTML = tareas.map(e => {
        const c = escapeHTML(e.codigo);
        const a = e.acciones;
        const botones = [];
        if (a.includes('aceptar_asignacion')) botones.push(`<button class="btn btn-sm btn-primary fw-bold" onclick="aceptarAsignacion('${c}')">Aceptar asignación</button>`);
        if (a.includes('retirado')) botones.push(`<button class="btn btn-sm btn-dark" onclick="ejecutarCambio('${c}', 'retirado', 'Paquete retirado en origen', cargarTareasRepartidor, simularCoordenada())">${e.estado === 'incidencia' ? 'Retomar: paquete retirado' : 'Paquete retirado'}</button>`);
        if (a.includes('en_transito')) botones.push(`<button class="btn btn-sm btn-dark" onclick="ejecutarCambio('${c}', 'en_transito', 'En camino al destino', cargarTareasRepartidor, simularCoordenada())">${e.estado === 'incidencia' ? 'Retomar tránsito' : 'Iniciar tránsito'}</button>`);
        if (a.includes('entregar')) botones.push(`<button class="btn btn-sm btn-success fw-bold" onclick="abrirModalEntrega('${c}')">Confirmar entrega</button>`);
        if (a.includes('incidencia')) botones.push(`<button class="btn btn-sm btn-outline-danger" onclick="reportarIncidencia('${c}', cargarTareasRepartidor)">Reportar incidencia</button>`);
        return `
            <div class="border rounded-3 p-3 bg-light d-flex justify-content-between align-items-center flex-wrap gap-2">
                <div>
                    <h5 class="fw-bold mb-1">${c}</h5>
                    <p class="mb-0 small text-muted">Para: ${escapeHTML(e.destinatario)} · ${escapeHTML(e.zona)} · ${escapeHTML(e.tipo_servicio)}</p>
                    <div class="mt-2">${badgeEstado(e.estado)} ${e.asignacion_aceptada ? '' : '<span class="badge bg-warning text-dark">pendiente de aceptar</span>'}</div>
                </div>
                <div class="d-flex flex-column gap-2">
                    ${botones.join('')}
                    <button class="btn btn-sm btn-link" onclick="verSeguimiento('${c}')">Ver detalle</button>
                </div>
            </div>`;
    }).join('');
}

async function aceptarAsignacion(codigo) {
    const r = await api('POST', `/envios/${encodeURIComponent(codigo)}/aceptar-asignacion`, { evento_id: generateUUID() });
    if (r.ok) showAlert(`Asignación de ${codigo} aceptada.`, 'success');
    else showAlert(`No se pudo aceptar: ${r.mensaje}`);
    cargarTareasRepartidor();
}

function toggleGPS() {
    clearInterval(gpsInterval);
    gpsInterval = null;
    if (document.getElementById('gpsToggle').checked) {
        enviarPosicion();
        gpsInterval = setInterval(enviarPosicion, 30000);
    }
}

// Solo coordenadas simuladas: el enunciado exige datos de ubicación ficticios
async function enviarPosicion() {
    const miId = await obtenerMiRepartidor();
    if (!miId) return;
    const pos = await api('POST', `/repartidores/${miId}/posicion`, simularCoordenada());
    const lat = await api('POST', `/repartidores/${miId}/latido`);
    if (!pos.ok || !lat.ok) showAlert(`No se pudo registrar la posición: ${pos.mensaje || lat.mensaje}`, 'warning');
    actualizarEstadoRedis();
}

async function actualizarEstadoRedis() {
    const miId = await obtenerMiRepartidor();
    if (!miId) return;
    const r = await api('GET', `/repartidores/${miId}/estado-temporal`);
    const p = document.getElementById('redisStatus');
    if (!r.ok) { p.className = 'small mt-3 mb-0 text-warning'; p.innerText = `Redis no disponible: ${r.mensaje}`; return; }
    const d = r.data;
    if (d.posicion.vigente) {
        p.className = 'small mt-3 mb-0 text-success';
        p.innerText = `✔ Posición vigente (${d.posicion.lat}, ${d.posicion.lng}) · expira en ${d.posicion.expira_en_segundos} s · ${d.conectado ? 'conectado' : 'sin latido'}`;
    } else {
        p.className = 'small mt-3 mb-0 text-secondary';
        p.innerText = `Posición no vigente: ${d.posicion.motivo}.`;
    }
}

function abrirModalEntrega(codigo) {
    document.getElementById('modalEnvioCodigo').innerText = codigo;
    const form = document.getElementById('formEntrega');
    form.reset();
    form.dataset.evento = generateUUID();   // mismo evento si se reintenta el envío del formulario
    if (!modalEvidencia) modalEvidencia = new bootstrap.Modal(document.getElementById('modalEntrega'));
    modalEvidencia.show();
}

async function registrarEntregaFinal(event) {
    event.preventDefault();
    const codigo = document.getElementById('modalEnvioCodigo').innerText;
    const form = document.getElementById('formEntrega');
    const btn = document.getElementById('btnSubmitEntrega');
    const foto = document.getElementById('evidenciaFoto').files[0];
    const payload = {
        evento_id: form.dataset.evento,
        receptor_nombre: document.getElementById('evidenciaReceptor').value,
        observacion: document.getElementById('evidenciaObs').value || null,
        foto_url: foto ? `evidencias/${codigo}/${foto.name}` : null,
        firma_simulada: true
    };
    btn.disabled = true;
    const r = await api('POST', `/envios/${encodeURIComponent(codigo)}/entrega`, payload);
    btn.disabled = false;
    if (r.ok) { showAlert(`Entrega de ${codigo} registrada con evidencia.`, 'success'); modalEvidencia.hide(); cargarTareasRepartidor(); }
    else showAlert(`No se registró la entrega: ${r.mensaje}`);
}

// ========================== ADMINISTRACIÓN ==========================
async function cargarAdmin() {
    const [reps, zonas, servicios] = await Promise.all([api('GET', '/repartidores'), api('GET', '/admin/zonas'), api('GET', '/tipos-servicio')]);
    if (!reps.ok || !zonas.ok || !servicios.ok) { showAlert(reps.mensaje || zonas.mensaje || servicios.mensaje); return; }

    const conexion = {};
    await Promise.all(reps.data.map(async rep => {
        const t = await api('GET', `/repartidores/${rep.id}/estado-temporal`);
        conexion[rep.id] = t.ok && t.data.conectado;
    }));
    document.getElementById('tablaAdmin').innerHTML = reps.data.map(r => `<tr>
        <td>${escapeHTML(r.nombre)}</td><td>${escapeHTML(r.zona)}</td><td>${escapeHTML(r.carga_actual)}</td>
        <td><input type="number" min="1" max="20" class="form-control form-control-sm" style="width:90px" id="cap_${escapeHTML(r.id)}" value="${escapeHTML(r.capacidad_max)}"></td>
        <td>${conexion[r.id] ? '🟢 conectado' : '⚪ sin conexión'}</td>
        <td><button class="btn btn-sm btn-dark" onclick="guardarCapacidad('${escapeHTML(r.id)}')">Guardar</button></td>
    </tr>`).join('');

    zonasCache = zonas.data;
    serviciosCache = servicios.data;
    document.getElementById('listaAdminZonas').innerHTML = zonas.data.map(z => `
        <li class="list-group-item d-flex justify-content-between align-items-center bg-transparent px-0">
            <span>${escapeHTML(z.nombre)} <span class="badge ${z.activa ? 'bg-success' : 'bg-secondary'}">${z.activa ? 'Activa' : 'Inactiva'}</span></span>
            <button class="btn btn-sm btn-outline-${z.activa ? 'secondary' : 'success'}" onclick="cambiarZona(${Number(z.id)}, ${!z.activa})">${z.activa ? 'Desactivar' : 'Activar'}</button>
        </li>`).join('');

    document.getElementById('listaAdminServicios').innerHTML = servicios.data.map(s => `
        <li class="list-group-item d-flex justify-content-between align-items-center bg-transparent px-0">
            ${escapeHTML(s.nombre)} <span>$${escapeHTML(s.tarifa_base)} <small class="text-muted">(${escapeHTML(s.tiempo_estimado_min)} min)</small>
            <button class="btn btn-sm btn-link" onclick="editarServicio(${Number(s.id)})">Editar</button></span>
        </li>`).join('');

    cargarEstadoTecnico();
}

async function guardarCapacidad(id) {
    const capacidad = parseInt(document.getElementById(`cap_${id}`).value);
    const r = await api('PATCH', `/repartidores/${id}/capacidad`, { capacidad_max: capacidad });
    if (r.ok) showAlert('Capacidad actualizada.', 'success'); else showAlert(`No se actualizó la capacidad: ${r.mensaje}`);
    cargarAdmin();
}

async function guardarZona(e) {
    e.preventDefault();
    const nombre = document.getElementById('adminZonaNombre').value;
    const r = await api('POST', '/zonas', { nombre, activa: true });
    if (r.ok) { showAlert('Zona creada.', 'success'); document.getElementById('adminZonaNombre').value = ''; }
    else showAlert(`No se creó la zona: ${r.mensaje}`);
    cargarAdmin();
}

async function cambiarZona(id, activa) {
    const nombre = zonasCache.find(z => z.id === id).nombre;
    const r = await api('PUT', `/zonas/${id}`, { nombre, activa });
    if (r.ok) showAlert(`Zona ${nombre} ${activa ? 'activada' : 'desactivada'}.`, 'success');
    else showAlert(`No se actualizó la zona: ${r.mensaje}`);
    cargarAdmin();
}

async function guardarServicio(e) {
    e.preventDefault();
    const cuerpo = {
        nombre: document.getElementById('adminServNombre').value,
        tarifa_base: parseFloat(document.getElementById('adminServTarifa').value),
        tiempo_estimado_min: parseInt(document.getElementById('adminServMin').value)
    };
    const r = await api('POST', '/tipos-servicio', cuerpo);
    if (r.ok) { showAlert('Servicio creado.', 'success'); e.target.reset(); }
    else showAlert(`No se creó el servicio: ${r.mensaje}`);
    cargarAdmin();
}

async function editarServicio(id) {
    const { nombre, tarifa_base: tarifa, tiempo_estimado_min: minutos } = serviciosCache.find(s => s.id === id);
    const nuevaTarifa = prompt(`Tarifa base de ${nombre}:`, tarifa);
    if (nuevaTarifa === null) return;
    const nuevosMin = prompt(`Tiempo estimado (min) de ${nombre}:`, minutos);
    if (nuevosMin === null) return;
    const r = await api('PUT', `/tipos-servicio/${id}`, { nombre, tarifa_base: parseFloat(nuevaTarifa), tiempo_estimado_min: parseInt(nuevosMin) });
    if (r.ok) showAlert('Servicio actualizado.', 'success'); else showAlert(`No se actualizó: ${r.mensaje}`);
    cargarAdmin();
}

async function cargarEstadoTecnico() {
    const [salud, sync] = await Promise.all([api('GET', '/health'), api('GET', '/sincronizacion')]);
    const caja = document.getElementById('estadoTecnico');
    const deps = salud.data && salud.data.dependencias ? salud.data.dependencias : {};
    const tarjeta = (titulo, valor, ok) => `<div class="col-6 col-md-3"><div class="p-3 rounded-3 border ${ok === false ? 'border-danger' : ''}"><div class="small text-muted text-uppercase">${escapeHTML(titulo)}</div><div class="fw-bold">${valor}</div></div></div>`;
    let html = ['postgres', 'mongodb', 'redis'].map(d => tarjeta(d, deps[d] === 'ok' ? '🟢 ok' : '🔴 caído', deps[d] === 'ok')).join('');
    if (sync.ok) {
        const s = sync.data;
        html += tarjeta('Outbox pendientes', escapeHTML(s.outbox_pendientes), s.outbox_pendientes === 0);
        html += tarjeta('Retraso del más antiguo', s.retraso_segundos === null ? '—' : `${escapeHTML(s.retraso_segundos)} s`);
        html += tarjeta('Eventos en outbox / MongoDB', `${escapeHTML(s.outbox_total)} / ${s.eventos_en_mongodb === null ? '—' : escapeHTML(s.eventos_en_mongodb)}`);
        html += tarjeta('Último publicado', fecha(s.ultimo_publicado_en) || '—');
    } else if (sync.status === 0) {
        html = `<div class="col-12 text-danger">${escapeHTML(sync.mensaje)}</div>`;
    }
    caja.innerHTML = html;
}

// Al abrir la web se comprueba la salud: avisa si una base está caída o con un esquema viejo
async function verificarSalud() {
    const r = await api('GET', '/health');
    if (r.status === 0) { showAlert(r.mensaje); return; }
    const deps = (r.data && r.data.dependencias) || {};
    const problemas = Object.entries(deps).filter(([, v]) => v !== 'ok').map(([k, v]) => `${k}: ${v}`);
    if (problemas.length) showAlert(`Sistema degradado → ${problemas.join(' · ')}`, 'warning');
}

document.addEventListener('DOMContentLoaded', () => { updateActor(); verificarSalud(); });
