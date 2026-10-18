'use strict';
/* Vigía de Incendios Perú — interfaz del mapa (Leaflet, sin compilación). */
(() => {
  // ------------------------------------------------------------------------------------
  // Configuración
  // ------------------------------------------------------------------------------------
  const CONFIG = {
    centro: [-9.3, -75.0],
    zoom: 5,
    autoMin: 15,
    gibs: {
      wms: 'https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi',
      wmts: 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best',
      anomalias: [
        'VIIRS_NOAA20_Thermal_Anomalies_375m_All',
        'VIIRS_NOAA21_Thermal_Anomalies_375m_All',
        'VIIRS_SNPP_Thermal_Anomalies_375m_All',
      ],
      colorReal: 'VIIRS_NOAA20_CorrectedReflectance_TrueColor',
      quemas: 'VIIRS_SNPP_CorrectedReflectance_BandsM11-I2-I1',
    },
    categorias: {
      bomberos: { letra: 'B', color: '#c62828', titulo: 'Compañías de bomberos' },
      salud: { letra: 'S', color: '#1565c0', titulo: 'Salud' },
      policia: { letra: 'P', color: '#37474f', titulo: 'Comisarías' },
      poblados: { letra: 'C', color: '#6d4c41', titulo: 'Centros poblados' },
      agua: { letra: 'A', color: '#0277bd', titulo: 'Agua (ríos, lagunas)' },
      aereo: { letra: 'H', color: '#5e35b1', titulo: 'Aeródromos y helipuertos' },
      vias: { letra: 'V', color: '#546e7a', titulo: 'Acceso vial más cercano' },
    },
  };

  const state = {
    rango: '24h',
    fuentes: new Set(),
    confianza: 'baja',
    soloPeru: true,
    demo: false,
    datos: null,
    porId: new Map(),
    sel: null,
    cacheDetalle: new Map(),
    filtros: { dep: '', nivelMin: 1, estado: 'todos', orden: 'prioridad', q: '' },
    base: 'mapa',
    cartoKey: '',
    horizonteProp: 6,
    fechaNasa: hoyLima(),
    tema: leerLocal('vigia-tema') || 'sistema',
    hashPendiente: decodeURIComponent((location.hash || '').slice(1)),
    timerAuto: null,
  };

  // ------------------------------------------------------------------------------------
  // Utilidades
  // ------------------------------------------------------------------------------------
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const nf0 = new Intl.NumberFormat('es-PE', { maximumFractionDigits: 0 });
  const nf1 = new Intl.NumberFormat('es-PE', { maximumFractionDigits: 1 });
  const nf2 = new Intl.NumberFormat('es-PE', { maximumFractionDigits: 2 });
  const fmtFecha = new Intl.DateTimeFormat('es-PE', { timeZone: 'America/Lima', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  const fmtHora = new Intl.DateTimeFormat('es-PE', { timeZone: 'America/Lima', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

  function leerLocal(k) { try { return localStorage.getItem(k); } catch { return null; } }
  function guardarLocal(k, v) { try { localStorage.setItem(k, v); } catch { /* sin almacenamiento */ } }
  function hoyLima() { return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Lima' }).format(new Date()); }
  function fechaLima(iso) { return iso ? fmtFecha.format(new Date(iso)) : '—'; }
  function hace(iso) {
    if (!iso) return '—';
    const min = Math.max(0, (Date.now() - new Date(iso).getTime()) / 60000);
    if (min < 60) return `hace ${Math.round(min)} min`;
    if (min < 48 * 60) return `hace ${nf0.format(min / 60)} h`;
    return `hace ${nf0.format(min / 1440)} d`;
  }
  function horasDesde(iso) { return (Date.now() - new Date(iso).getTime()) / 3.6e6; }
  function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function colorNivel(n) { return cssVar(`--lv${Math.min(5, Math.max(1, n || 1))}`); }
  function colorEdad(h) { return cssVar(h <= 6 ? '--edad1' : h <= 12 ? '--edad2' : h <= 24 ? '--edad3' : '--edad4'); }
  const CARDINALES = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSO', 'SO', 'OSO', 'O', 'ONO', 'NO', 'NNO'];
  const cardinal = (g) => (g == null ? '—' : CARDINALES[Math.floor(((g % 360) + 11.25) / 22.5) % 16]);
  function destino(lat, lon, rumbo, km) {
    const R = 6371.0088, d = km / R, b = rumbo * Math.PI / 180, p1 = lat * Math.PI / 180, l1 = lon * Math.PI / 180;
    const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
    const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
    return [p2 * 180 / Math.PI, l2 * 180 / Math.PI];
  }
  function toast(msg) {
    const t = document.createElement('div');
    t.className = 'toast'; t.textContent = msg; t.setAttribute('role', 'status');
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2600);
  }
  async function copiar(texto, etiqueta = 'Copiado') {
    try {
      await navigator.clipboard.writeText(texto);
      toast(etiqueta);
    } catch {
      $('#modal-texto').value = texto;
      $('#modal').hidden = false;
      $('#modal-texto').select();
    }
  }
  async function getJSON(url) {
    const r = await fetch(url, { headers: { Accept: 'application/json' } });
    let cuerpo = null;
    try { cuerpo = await r.json(); } catch { /* respuesta no JSON */ }
    if (!r.ok) throw new Error((cuerpo && cuerpo.error) || `El servidor respondió ${r.status}`);
    return cuerpo;
  }

  // ------------------------------------------------------------------------------------
  // Tema
  // ------------------------------------------------------------------------------------
  const consultaOscuro = window.matchMedia('(prefers-color-scheme: dark)');
  function esOscuro() {
    return state.tema === 'oscuro' || (state.tema === 'sistema' && consultaOscuro.matches);
  }
  function aplicarTema() {
    const root = document.documentElement;
    if (state.tema === 'sistema') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', state.tema === 'oscuro' ? 'dark' : 'light');
    $('#btn-tema').title = `Tema: ${state.tema}`;
  }
  $('#btn-tema').addEventListener('click', () => {
    state.tema = { sistema: 'claro', claro: 'oscuro', oscuro: 'sistema' }[state.tema];
    guardarLocal('vigia-tema', state.tema);
    aplicarTema();
    cambiarBase(state.base);
    dibujarTodo();
    toast(`Tema: ${state.tema}`);
  });
  consultaOscuro.addEventListener?.('change', () => { if (state.tema === 'sistema') { cambiarBase(state.base); dibujarTodo(); } });
  aplicarTema();

  // ------------------------------------------------------------------------------------
  // Mapa
  // ------------------------------------------------------------------------------------
  const mapa = L.map('mapa', { preferCanvas: true, minZoom: 4, zoomSnap: 0.5 }).setView(CONFIG.centro, CONFIG.zoom);
  mapa.fitBounds([[-18.4, -81.3], [-0.05, -68.7]], { padding: [10, 10] });
  L.control.scale({ imperial: false, position: 'bottomright' }).addTo(mapa);
  [['limites', 350], ['huellas', 390], ['cono', 405], ['incendios', 420], ['flechas', 620], ['recursos', 640]]
    .forEach(([n, z]) => { mapa.createPane(n).style.zIndex = z; });
  mapa.getPane('flechas').style.pointerEvents = 'none';
  const rHuellas = L.canvas({ pane: 'huellas', padding: 0.3 });
  // Un solo lienzo para todo lo que se puede tocar: un <canvas> encima de otro se queda con todos los
  // clics, así que focos, incendios, zonas y capas de la selección comparten este y se ordenan con
  // bringToBack (el último dibujado es el que recibe el clic).
  const rVectores = L.canvas({ pane: 'incendios', padding: 0.3 });

  const capas = {
    limites: L.layerGroup().addTo(mapa),
    huellas: L.layerGroup().addTo(mapa),
    envolventes: L.layerGroup().addTo(mapa),
    focos: L.layerGroup().addTo(mapa),
    incendios: L.layerGroup().addTo(mapa),
    viento: L.layerGroup().addTo(mapa),
    seleccion: L.layerGroup().addTo(mapa),
    eonet: L.layerGroup(),
    gibs: null,
  };
  let capaBase = null;
  let capaEtiquetas = null;

  const ATR_OSM = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
  const ATR_NASA = 'Imágenes: <a href="https://earthdata.nasa.gov/gibs">NASA GIBS</a>';
  function crearBase(nombre) {
    const fecha = state.fechaNasa;
    switch (nombre) {
      case 'relieve':
        return L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', { maxZoom: 17, attribution: `${ATR_OSM}, SRTM | &copy; OpenTopoMap (CC-BY-SA)` });
      case 'satelite':
        return L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Imágenes &copy; Esri, Maxar, Earthstar Geographics' });
      case 'nasa_color':
        return L.tileLayer(`${CONFIG.gibs.wmts}/${CONFIG.gibs.colorReal}/default/${fecha}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`, { maxNativeZoom: 9, maxZoom: 14, attribution: `${ATR_NASA} (VIIRS NOAA-20, ${fecha})` });
      case 'nasa_quemas':
        return L.tileLayer(`${CONFIG.gibs.wmts}/${CONFIG.gibs.quemas}/default/${fecha}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`, { maxNativeZoom: 9, maxZoom: 14, attribution: `${ATR_NASA} (VIIRS bandas M11-I2-I1, ${fecha})` });
      default: {
        // CARTO exige clave (CARTO_API_KEY en .env); sin ella las teselas salen con la marca "API KEY REQUIRED".
        if (!state.cartoKey) {
          return L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: ATR_OSM });
        }
        const estilo = esOscuro() ? 'dark_all' : 'light_all';
        return L.tileLayer(`https://{s}.basemaps.cartocdn.com/${estilo}/{z}/{x}/{y}{r}.png?key=${encodeURIComponent(state.cartoKey)}`, { maxZoom: 19, subdomains: 'abcd', attribution: `${ATR_OSM} &copy; <a href="https://carto.com/attributions">CARTO</a>` });
      }
    }
  }
  function crearEtiquetas() {
    if (!state.cartoKey) {
      return L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, pane: 'limites', opacity: 0.9, attribution: 'Etiquetas &copy; Esri' });
    }
    return L.tileLayer(`https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png?key=${encodeURIComponent(state.cartoKey)}`, { subdomains: 'abcd', maxZoom: 19, pane: 'limites', opacity: 0.9 });
  }
  function cambiarBase(nombre) {
    state.base = nombre;
    if (capaBase) mapa.removeLayer(capaBase);
    if (capaEtiquetas) { mapa.removeLayer(capaEtiquetas); capaEtiquetas = null; }
    capaBase = crearBase(nombre).addTo(mapa);
    capaBase.bringToBack();
    if (nombre !== 'mapa' && nombre !== 'relieve') {
      capaEtiquetas = crearEtiquetas().addTo(mapa);
    }
  }
  function crearGibs() {
    return L.tileLayer.wms(CONFIG.gibs.wms, {
      layers: CONFIG.gibs.anomalias.join(','), format: 'image/png', transparent: true, version: '1.3.0',
      TIME: state.fechaNasa, pane: 'huellas', opacity: 0.9, attribution: `${ATR_NASA} (anomalías térmicas, ${state.fechaNasa})`,
    });
  }
  cambiarBase('mapa');

  // Límites departamentales
  let capaDeps = null;
  const estiloDeps = () => ({ color: cssVar('--muted'), weight: 1, opacity: 0.55, fill: false, dashArray: '4 3' });
  getJSON('/api/limites').then((gj) => {
    capaDeps = L.geoJSON(gj, { pane: 'limites', interactive: false, style: estiloDeps }).addTo(capas.limites);
  }).catch(() => { /* el mapa funciona sin límites */ });

  // ------------------------------------------------------------------------------------
  // Carga de datos
  // ------------------------------------------------------------------------------------
  function parametros() {
    const p = new URLSearchParams({
      rango: state.rango,
      fuentes: Array.from(state.fuentes).join(','),
      confianza: state.confianza,
      solo_peru: state.soloPeru ? '1' : '0',
    });
    if (state.demo) p.set('demo', '1');
    return p.toString();
  }

  async function cargar() {
    const boton = $('#btn-actualizar');
    boton.setAttribute('aria-busy', 'true');
    $('#cargando').hidden = false;
    $('#cargando-texto').textContent = state.demo ? 'Generando datos de demostración…' : 'Descargando focos de NASA FIRMS…';
    try {
      const datos = await getJSON(`/api/incendios?${parametros()}`);
      state.datos = datos;
      state.porId = new Map(datos.incendios.map((i) => [i.id, i]));
      state.cacheDetalle.clear();
      actualizarResumen();
      llenarDepartamentos();
      dibujarTodo();
      renderLista();
      renderRegiones();
      $('#dl-csv').href = `/api/exportar.csv?${parametros()}`;
      $('#dl-geojson').href = `/api/exportar.geojson?${parametros()}`;
      if (state.hashPendiente && state.porId.has(state.hashPendiente)) {
        seleccionar(state.hashPendiente);
      } else if (state.sel && state.porId.has(state.sel)) {
        seleccionar(state.sel, { zoom: false });
      } else if (state.sel) {
        cerrarDetalle();
      }
      state.hashPendiente = '';
    } catch (err) {
      mostrarAvisos([`No se pudieron cargar los datos: ${err.message}`], true);
      $('#estado-datos').textContent = 'Sin conexión con el servidor';
    } finally {
      boton.removeAttribute('aria-busy');
      $('#cargando').hidden = true;
    }
  }

  function mostrarAvisos(lista, error = false, extraHTML = '') {
    const el = $('#avisos');
    if (!lista.length && !extraHTML) { el.hidden = true; el.innerHTML = ''; return; }
    el.hidden = false;
    el.classList.toggle('error', error);
    el.innerHTML = `<strong>${error ? 'Problema con los datos' : 'Avisos'}</strong><ul>${lista.map((a) => `<li>${esc(a)}</li>`).join('')}</ul>${extraHTML}`;
    const demo = $('#btn-demo', el);
    if (demo) demo.addEventListener('click', () => { state.demo = true; cargar(); });
  }

  function actualizarResumen() {
    const d = state.datos;
    const t = d.totales;
    $('#k-focos').textContent = nf0.format(t.focos);
    $('#k-incendios').textContent = nf0.format(t.incendios);
    $('#k-activos').textContent = nf0.format(t.activos);
    $('#k-frp').textContent = nf0.format(t.frp_total);
    $('#k-area').textContent = nf0.format(t.area_ha);

    const okFuentes = d.fuentes.filter((f) => f.ok);
    const modo = d.modo === 'demo' ? '<span class="demo">DATOS DE DEMOSTRACIÓN</span>'
      : d.modo === 'api' ? 'NASA FIRMS (API)' : 'NASA FIRMS (CSV público)';
    $('#estado-datos').innerHTML = `${modo} · ${okFuentes.length}/${d.fuentes.length} satélites<br>`
      + `Consulta <b>${fmtHora.format(new Date(d.descargado))}</b> · último foco <b>${t.ultima_deteccion ? hace(t.ultima_deteccion) : '—'}</b>`;

    const todasFallan = d.fuentes.length && !okFuentes.length;
    const extra = todasFallan && d.modo !== 'demo'
      ? `<p>Revisa la conexión a internet del servidor. Si persiste, configura <code>FIRMS_MAP_KEY</code> en <code>.env</code>. Mientras tanto puedes <button type="button" class="mini-boton" id="btn-demo">ver datos de demostración</button>.</p>`
      : '';
    const avisos = [...d.avisos];
    if (d.modo === 'demo') avisos.unshift('Estás viendo focos simulados para probar la interfaz. No representan incendios reales.');
    mostrarAvisos(avisos, todasFallan, extra);
  }

  function llenarDepartamentos() {
    const sel = $('#f-dep');
    const actual = state.filtros.dep;
    const deps = state.datos.departamentos.map((r) => r.departamento).sort((a, b) => a.localeCompare(b, 'es'));
    sel.innerHTML = '<option value="">Todo el Perú</option>' + deps.map((d) => `<option ${d === actual ? 'selected' : ''}>${esc(d)}</option>`).join('');
  }

  // ------------------------------------------------------------------------------------
  // Filtros
  // ------------------------------------------------------------------------------------
  function incendiosFiltrados() {
    if (!state.datos) return [];
    const f = state.filtros;
    const q = f.q.trim().toLowerCase();
    let lista = state.datos.incendios.filter((i) => {
      if (f.dep && i.departamento !== f.dep) return false;
      if (i.nivel < f.nivelMin) return false;
      if (f.estado === 'activo' && i.estado !== 'activo') return false;
      if (f.estado === '24' && i.horas_desde_ultima > 24) return false;
      if (q && !(`${i.departamento} ${i.provincia || ''}`.toLowerCase().includes(q))) return false;
      return true;
    });
    const orden = {
      prioridad: (a, b) => b.prioridad - a.prioridad,
      reciente: (a, b) => a.horas_desde_ultima - b.horas_desde_ultima,
      frp: (a, b) => b.frp_total - a.frp_total,
      area: (a, b) => b.area_pixeles_ha - a.area_pixeles_ha,
    }[f.orden];
    lista = lista.slice().sort(orden);
    return lista;
  }

  // ------------------------------------------------------------------------------------
  // Dibujo del mapa
  // ------------------------------------------------------------------------------------
  function dibujarTodo() {
    if (capaDeps) capaDeps.setStyle(estiloDeps());
    if (!state.datos) return;
    const visibles = incendiosFiltrados();
    const ids = new Set(visibles.map((i) => i.id));
    dibujarFocos(ids);
    dibujarIncendios(visibles);
    dibujarViento(visibles);
    dibujarHuellas();
    renderLeyenda();
    if (state.sel) resaltarSeleccion();
  }

  function radioFoco(frp) { return Math.max(2.5, Math.min(7, 2 + Math.sqrt(frp) * 0.7)); }

  function dibujarFocos(ids) {
    capas.focos.clearLayers();
    if (!$('#c-focos').checked) return;
    const borde = esOscuro() ? '#000' : '#3a1600';
    for (const f of state.datos.focos) {
      if (f.incendio && !ids.has(f.incendio)) continue;
      const m = L.circleMarker([f.lat, f.lon], {
        renderer: rVectores, radius: radioFoco(f.frp), weight: 0.6, color: borde, opacity: 0.6,
        fillColor: colorEdad(horasDesde(f.fecha)), fillOpacity: 0.9,
      });
      m.on('click', (e) => {
        L.DomEvent.stopPropagation(e);
        L.popup({ maxWidth: 260 }).setLatLng([f.lat, f.lon]).setContent(popupFoco(f)).openOn(mapa);
        if (f.incendio) seleccionar(f.incendio, { zoom: false });
      });
      m.addTo(capas.focos);
    }
  }

  function popupFoco(f) {
    return `<div class="popup-foco">
      <span>Satélite</span><span>${esc(f.sensor)} ${esc(f.satelite)}</span>
      <span>Detección</span><span>${esc(fechaLima(f.fecha))} (${esc(hace(f.fecha))})</span>
      <span>FRP</span><span>${nf1.format(f.frp)} MW</span>
      <span>Temp. brillo</span><span>${nf1.format(f.brillo)} K</span>
      <span>Píxel</span><span>${nf2.format(f.scan)} × ${nf2.format(f.track)} km</span>
      <span>Confianza</span><span>${esc(f.confianza)}</span>
      <span>Pasada</span><span>${f.dia_noche === 'N' ? 'Nocturna' : 'Diurna'}</span>
      <span>Coordenadas</span><span>${f.lat.toFixed(5)}, ${f.lon.toFixed(5)}</span>
    </div>`;
  }

  function popupIncendio(i) {
    return `<div class="popup-foco">
      <strong class="popup-titulo" style="--nivel-color:var(--lv${i.nivel})">Incendio ${esc(i.nivel_texto)}</strong>
      <span>Ubicación</span><span>${esc(i.provincia || 'Provincia no identificada')}, ${esc(i.departamento)}</span>
      <span>Estado</span><span>${esc(etiquetaEstado(i.estado))}</span>
      <span>Área con calor</span><span>${nf0.format(i.area_pixeles_ha)} ha</span>
      <span>Envolvente</span><span>${nf0.format(i.area_envolvente_ha)} ha</span>
      <span>FRP total</span><span>${nf0.format(i.frp_total)} MW (máx. ${nf1.format(i.frp_max)} MW)</span>
      <span>Detecciones</span><span>${nf0.format(i.detecciones)} · ${i.nocturnas} nocturnas</span>
      <span>Primera</span><span>${esc(fechaLima(i.primera))}</span>
      <span>Última</span><span>${esc(fechaLima(i.ultima))} (${esc(hace(i.ultima))})</span>
      <span>Coordenadas</span><span>${i.lat.toFixed(5)}, ${i.lon.toFixed(5)}</span>
    </div>`;
  }

  function abrirIncendio(i) {
    seleccionar(i.id);
    L.popup({ maxWidth: 300, autoPan: false }).setLatLng([i.lat, i.lon]).setContent(popupIncendio(i)).openOn(mapa);
  }

  function radioIncendio(i) { return Math.max(7, Math.min(28, 6 + Math.sqrt(i.area_pixeles_ha) * 0.55)); }

  function dibujarIncendios(lista) {
    capas.incendios.clearLayers();
    capas.envolventes.clearLayers();
    if (!$('#c-incendios').checked) return;
    const opac = { activo: 0.5, reciente: 0.32, anterior: 0.14 };
    const verEnvolventes = mapa.getZoom() >= 9;
    for (const i of lista) {
      const color = colorNivel(i.nivel);
      if (verEnvolventes && i.envolvente.length >= 3) {
        L.polygon(i.envolvente, { renderer: rVectores, color, weight: 1.5, fillColor: color, fillOpacity: 0.08 })
          .on('click', (e) => {
            L.DomEvent.stopPropagation(e);
            // dentro del incendio ya seleccionado manda la probabilidad de llegada, si está a la vista
            if (i.id === state.sel && popupProp(e.latlng)) return;
            abrirIncendio(i);
          })
          .addTo(capas.envolventes)
          .bringToBack();
      }
      const m = L.circleMarker([i.lat, i.lon], {
        renderer: rVectores, radius: radioIncendio(i), color, weight: i.estado === 'activo' ? 2.5 : 1.5,
        dashArray: i.estado === 'anterior' ? '3 3' : null, fillColor: color, fillOpacity: opac[i.estado],
      });
      m.bindTooltip(`<b>${esc(i.nivel_texto)}</b> · ${esc(i.provincia || i.departamento)}<br>${nf0.format(i.frp_total)} MW · ${nf0.format(i.area_pixeles_ha)} ha · ${esc(hace(i.ultima))}`, { direction: 'top', offset: [0, -6] });
      m.on('click', (e) => { L.DomEvent.stopPropagation(e); abrirIncendio(i); });
      m.addTo(capas.incendios);
    }
  }

  function svgFlecha(grados, color, largo = 26) {
    return `<svg width="64" height="64" viewBox="-32 -32 64 64" aria-hidden="true">
      <g transform="rotate(${grados})">
        <line x1="0" y1="-4" x2="0" y2="${-largo}" stroke="#fff" stroke-width="5" stroke-linecap="round" opacity=".85"/>
        <line x1="0" y1="-4" x2="0" y2="${-largo}" stroke="${color}" stroke-width="2.4" stroke-linecap="round"/>
        <path d="M0 ${-largo - 6} L5 ${-largo + 2} L-5 ${-largo + 2} Z" fill="${color}" stroke="#fff" stroke-width="1"/>
      </g></svg>`;
  }

  function dibujarViento(lista) {
    capas.viento.clearLayers();
    if (!$('#c-viento').checked || mapa.getZoom() < 5) return;
    const color = cssVar('--accent');
    for (const i of lista) {
      const m = i.meteo;
      if (!m || m.avance == null) continue;
      const escala = mapa.getZoom() < 7 ? 0.7 : 1;
      const largo = (Math.max(14, Math.min(30, 12 + (m.viento || 0) * 0.6)) + radioIncendio(i) * 0.6) * escala;
      L.marker([i.lat, i.lon], {
        pane: 'flechas', interactive: false, keyboard: false,
        icon: L.divIcon({ className: 'flecha-viento', html: svgFlecha(m.avance, color, largo), iconSize: [64, 64], iconAnchor: [32, 32] }),
      }).addTo(capas.viento);
    }
  }

  function dibujarHuellas() {
    capas.huellas.clearLayers();
    if (!state.datos || !$('#c-focos').checked || mapa.getZoom() < 11) return;
    const b = mapa.getBounds().pad(0.2);
    let n = 0;
    for (const f of state.datos.focos) {
      if (!b.contains([f.lat, f.lon])) continue;
      if (++n > 4000) break;
      const dx = (f.scan / 2) / (111.32 * Math.cos(f.lat * Math.PI / 180));
      const dy = (f.track / 2) / 110.574;
      L.rectangle([[f.lat - dy, f.lon - dx], [f.lat + dy, f.lon + dx]], {
        renderer: rHuellas, weight: 1, color: colorEdad(horasDesde(f.fecha)), fillOpacity: 0.25, interactive: false,
      }).addTo(capas.huellas);
    }
  }

  mapa.on('zoomend', () => { if (state.datos) { dibujarIncendios(incendiosFiltrados()); dibujarViento(incendiosFiltrados()); dibujarHuellas(); if (state.sel) resaltarSeleccion(); } });
  mapa.on('moveend', () => { if (mapa.getZoom() >= 11) dibujarHuellas(); });

  function renderLeyenda() {
    const niveles = ['Bajo', 'Moderado', 'Alto', 'Muy alto', 'Extremo'];
    const abierta = $('#leyenda details') ? $('#leyenda details').open : !window.matchMedia('(max-width: 860px)').matches;
    $('#leyenda').innerHTML = `<details ${abierta ? 'open' : ''}><summary>Leyenda</summary><div class="leyenda-cuerpo">
      <div class="leyenda-titulo">Nivel del incendio (FRP)</div>
      <div class="leyenda-fila">${niveles.map((n, k) => `<span><i style="background:var(--lv${k + 1})"></i>${n}</span>`).join('')}</div>
      <div class="leyenda-titulo">Edad del foco</div>
      <div class="leyenda-fila"><span><i style="background:var(--edad1)"></i>&lt;6 h</span><span><i style="background:var(--edad2)"></i>6–12 h</span><span><i style="background:var(--edad3)"></i>12–24 h</span><span><i style="background:var(--edad4)"></i>&gt;24 h</span></div>
      <div>Flecha: hacia dónde empuja el viento el fuego y el humo</div>
      <div class="leyenda-titulo">Incendio seleccionado</div>
      <div>Mancha: probabilidad de llegada del fuego · líneas: avance probable a 3, 6, 12 y 24 h</div>
      <div class="leyenda-titulo">Calidad del aire (INCA)</div>
      <div class="leyenda-fila">${Object.values(INCA).map((c) => `<span><i style="background:${c.color}"></i>${c.nombre}</span>`).join('')}</div></div></details>`;
  }

  // ------------------------------------------------------------------------------------
  // Lista y regiones
  // ------------------------------------------------------------------------------------
  function svgFlechaMini(grados) {
    return `<svg class="flecha-mini" viewBox="-12 -12 24 24" aria-hidden="true"><g transform="rotate(${grados})"><line x1="0" y1="7" x2="0" y2="-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M0 -10 L4.5 -3.5 L-4.5 -3.5Z" fill="currentColor"/></g></svg>`;
  }

  function renderLista() {
    const lista = incendiosFiltrados();
    const total = state.datos ? state.datos.incendios.length : 0;
    $('#conteo-lista').textContent = total
      ? `${nf0.format(lista.length)} de ${nf0.format(total)} incendios · ${state.rango === '7d' ? 'últimos 7 días' : `últimas ${state.rango.replace('h', ' h')}`}`
      : 'No hay focos de calor en el periodo y filtros elegidos.';
    const ol = $('#lista-incendios');
    ol.innerHTML = lista.slice(0, 300).map((i) => {
      const m = i.meteo || {};
      const viento = m.viento != null
        ? `<span title="Viento ${nf0.format(m.viento)} km/h del ${esc(cardinal(m.dir))}; avance hacia ${esc(m.avance_txt)}">${svgFlechaMini(m.avance)}</span>`
        : '';
      return `<li><button type="button" class="tarjeta ${i.id === state.sel ? 'sel' : ''}" data-id="${esc(i.id)}" style="--nivel-color:var(--lv${i.nivel})">
        <span class="tarjeta-franja"></span>
        <span class="tarjeta-cuerpo">
          <span class="tarjeta-titulo">${esc(i.provincia || 'Sin provincia')} · ${esc(i.departamento)}</span>
          <span class="tarjeta-sub"><span class="estado estado-${i.estado}">${esc(etiquetaEstado(i.estado))}</span><span>${esc(hace(i.ultima))}</span><span>${nf0.format(i.detecciones)} ${i.detecciones === 1 ? 'foco' : 'focos'}</span></span>
          <span class="tarjeta-datos"><span><b>${nf0.format(i.frp_total)}</b> MW</span><span><b>${nf0.format(i.area_pixeles_ha)}</b> ha</span>${m.viento != null ? `<span><b>${nf0.format(m.viento)}</b> km/h</span>` : ''}${m.temp != null ? `<span><b>${nf0.format(m.temp)}</b> °C · ${nf0.format(m.hr)} %</span>` : ''}</span>
        </span>
        <span class="tarjeta-lado"><span class="chip">${esc(i.nivel_texto)}</span>${viento}</span>
      </button></li>`;
    }).join('') + (lista.length > 300 ? `<li class="nota">Se muestran los 300 primeros. Usa los filtros para acotar.</li>` : '');
  }
  function etiquetaEstado(e) { return { activo: 'Activo', reciente: 'Reciente', anterior: 'Anterior' }[e] || e; }

  $('#lista-incendios').addEventListener('click', (e) => {
    const b = e.target.closest('.tarjeta');
    if (b) seleccionar(b.dataset.id);
  });

  function renderRegiones() {
    const filas = state.datos.departamentos;
    const max = Math.max(1, ...filas.map((r) => r.frp_total));
    $('#tabla-regiones tbody').innerHTML = filas.map((r) => `
      <tr data-dep="${esc(r.departamento)}">
        <td><span class="punto-nivel" style="background:var(--lv${Math.max(1, r.nivel_max)})"></span>${esc(r.departamento)}<div class="barra-frp" style="width:${Math.max(2, (r.frp_total / max) * 100)}%"></div></td>
        <td class="num">${nf0.format(r.focos)}</td><td class="num">${nf0.format(r.incendios)}</td>
        <td class="num">${nf0.format(r.activos)}</td><td class="num">${nf0.format(r.frp_total)}</td>
      </tr>`).join('') || '<tr><td colspan="5">Sin focos en el periodo.</td></tr>';
  }
  $('#tabla-regiones').addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-dep]');
    if (!tr) return;
    state.filtros.dep = tr.dataset.dep;
    $('#f-dep').value = tr.dataset.dep;
    aplicarFiltros();
    mostrarVista('lista');
    const pts = incendiosFiltrados().map((i) => [i.lat, i.lon]);
    if (pts.length) mapa.fitBounds(L.latLngBounds(pts).pad(0.25), { maxZoom: 10 });
  });

  // ------------------------------------------------------------------------------------
  // Selección y detalle
  // ------------------------------------------------------------------------------------
  function mostrarVista(nombre) {
    $$('.vista').forEach((v) => { v.hidden = v.dataset.vista !== nombre; });
    $$('.pestanas button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === nombre || (nombre === 'detalle' && b.dataset.tab === 'lista'))));
  }
  $$('.pestanas button').forEach((b) => b.addEventListener('click', () => {
    if (b.dataset.tab === 'lista' && state.sel) mostrarVista('detalle');
    else mostrarVista(b.dataset.tab);
  }));

  function cerrarDetalle() {
    state.sel = null;
    capas.seleccion.clearLayers();
    history.replaceState(null, '', location.pathname + location.search);
    mostrarVista('lista');
    renderLista();
  }
  $('#btn-volver').addEventListener('click', cerrarDetalle);

  function resaltarSeleccion() {
    capas.seleccion.clearLayers();
    const i = state.porId.get(state.sel);
    if (!i) return;
    const color = colorNivel(i.nivel);
    if (i.envolvente.length >= 3) {
      L.polygon(i.envolvente, { renderer: rVectores, color, weight: 2.5, fillOpacity: 0.12, fillColor: color, interactive: false }).addTo(capas.seleccion).bringToBack();
    }
    L.circleMarker([i.lat, i.lon], { renderer: rVectores, radius: radioIncendio(i) + 6, color: cssVar('--accent'), weight: 2.5, fill: false, interactive: false }).addTo(capas.seleccion);
    const det = state.cacheDetalle.get(i.id) || {};
    if (det.prop && det.prop.disponible) dibujarPropagacion(det.prop);
    else if (det.meteo && (det.propErr || det.prop)) dibujarCono(i, det.meteo);  // respaldo si el modelo no corre
    if (det.aire) dibujarAire(det.aire);
    if (det.ctx) dibujarRecursos(det.ctx);
  }

  function dibujarCono(i, meteo) {
    const p = meteo.propagacion || {};
    if (p.rumbo == null || !$('#c-viento').checked) return;
    const radioKm = Math.sqrt(Math.max(i.area_envolvente_ha, i.area_pixeles_ha) / 100 / Math.PI);
    const largo = Math.max(1.5, (p.proyeccion_3h_km || 0) + radioKm);
    const pts = [[i.lat, i.lon]];
    for (let a = -25; a <= 25; a += 5) pts.push(destino(i.lat, i.lon, p.rumbo + a, largo));
    L.polygon(pts, { renderer: rVectores, color: colorNivel(i.nivel), weight: 1.5, dashArray: '6 4', fillOpacity: 0.07, interactive: true })
      .bindTooltip(`Avance probable hacia el ${esc(p.rumbo_txt)} · ~${nf1.format(p.proyeccion_3h_km || 0)} km en 3 h (regla del 10 %, sin modelo)`, { sticky: true })
      .addTo(capas.seleccion)
      .bringToBack();
  }

  // ------------------------------------------------------------------------------------
  // Pronóstico de propagación (modelo de Rothermel + conjunto, calculado en el servidor)
  // ------------------------------------------------------------------------------------
  const RAMPA_PROB = [[10, [240, 200, 60]], [30, [234, 150, 32]], [50, [222, 100, 22]], [75, [192, 50, 24]], [100, [140, 14, 40]]];
  const VAR_HORIZONTE = { 0: '--ink', 3: '--lv1', 6: '--lv2', 12: '--lv3', 24: '--lv4' };

  function colorProb(p) {
    for (let k = 1; k < RAMPA_PROB.length; k++) {
      const [p1, c1] = RAMPA_PROB[k];
      if (p <= p1) {
        const [p0, c0] = RAMPA_PROB[k - 1];
        const f = Math.max(0, (p - p0) / (p1 - p0));
        return c0.map((v, j) => Math.round(v + (c1[j] - v) * f));
      }
    }
    return RAMPA_PROB[RAMPA_PROB.length - 1][1];
  }
  function horizonteVisible(prop) {
    return prop.horizontes.includes(state.horizonteProp) ? state.horizonteProp : prop.horizonte_h;
  }
  function etiquetaHorizonte(h) { return h === 0 ? 'ahora' : `${h} h`; }

  function imagenProb(prop, h) {
    const n = prop.malla.n;
    const p = prop.prob[String(h)];
    const lienzo = document.createElement('canvas');
    lienzo.width = n; lienzo.height = n;
    const g = lienzo.getContext('2d');
    const img = g.createImageData(n, n);
    for (let k = 0; k < n * n; k++) {
      if (p[k] < 10) continue;
      const [r, v, b] = colorProb(p[k]);
      img.data.set([r, v, b, Math.round(255 * (0.22 + 0.5 * p[k] / 100))], k * 4);
    }
    g.putImageData(img, 0, 0);
    return lienzo.toDataURL();
  }

  function dibujarPropagacion(prop) {
    if (!$('#c-prop').checked) return;
    const m = prop.malla;
    const h = horizonteVisible(prop);
    L.imageOverlay(imagenProb(prop, h), [[m.sur, m.oeste], [m.norte, m.este]], { pane: 'cono', interactive: false, className: 'capa-prob' })
      .addTo(capas.seleccion);
    for (const iso of prop.isolineas) {
      const actual = iso.nivel === 50 && iso.h === h;
      const posible = iso.nivel === 10;
      const texto = posible ? `Alcance posible en ${iso.h} h (≥10 % de los escenarios)`
        : iso.h === 0 ? 'Extensión estimada en este momento' : `Llegada probable del fuego (≥50 %) en ${iso.h} h`;
      for (const linea of iso.lineas) {
        L.polyline(linea, {
          renderer: rVectores, color: cssVar(posible ? '--muted' : VAR_HORIZONTE[iso.h] || '--lv4'),
          weight: actual ? 3 : posible ? 1.4 : 1.3, opacity: actual ? 1 : 0.8,
          dashArray: posible ? '2 5' : iso.h === 0 ? '6 4' : null,
        }).bindTooltip(texto, { sticky: true }).addTo(capas.seleccion).bringToBack();
      }
    }
  }

  function celdaProp(prop, lat, lon) {
    const m = prop.malla;
    const j = Math.floor((m.norte - lat) / ((m.norte - m.sur) / m.n));
    const i = Math.floor((lon - m.oeste) / ((m.este - m.oeste) / m.n));
    return i >= 0 && i < m.n && j >= 0 && j < m.n ? j * m.n + i : null;
  }
  function textoEta(eta) {
    if (eta == null) return 'no se espera en el horizonte (escenario mediano)';
    if (eta <= 0) return 'ya alcanzado (estimado)';
    return eta < 1 ? `en ~${nf0.format(eta * 60)} min` : `en ~${nf1.format(eta)} h`;
  }

  function amenazasProp(prop, ctx) {
    if (!ctx || !ctx.recursos) return [];
    const p = prop.prob[String(prop.horizonte_h)];
    const salida = [];
    for (const cat of ['poblados', 'vias', 'salud', 'bomberos', 'policia', 'aereo']) {
      for (const r of ctx.recursos[cat] || []) {
        const c = celdaProp(prop, r.lat, r.lon);
        if (c == null || p[c] < 10) continue;
        salida.push({ ...r, cat, prob: p[c], eta: prop.eta[c] });
      }
    }
    return salida.sort((a, b) => (a.eta ?? 1e9) - (b.eta ?? 1e9) || b.prob - a.prob);
  }

  function graficoRos(serie) {
    if (!serie.length) return '';
    const W = 360, H = 118, ml = 30, mr = 6, mt = 8, mb = 20;
    const vmax = Math.max(0.2, ...serie.map((s) => s.ros_m_min)) * 1.1;
    const paso = [10, 5, 2, 1, 0.5, 0.2, 0.1].find((x) => vmax > 4 * x) || 0.05;
    const top = Math.ceil(vmax / paso) * paso;
    const ancho = (W - ml - mr) / serie.length;
    const y = (v) => mt + (1 - v / top) * (H - mt - mb);
    let rejilla = '';
    for (let v = 0; v <= top + 1e-9; v += paso) {
      rejilla += `<line x1="${ml}" x2="${W - mr}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${ml - 4}" y="${y(v) + 3.5}" text-anchor="end">${nf1.format(v)}</text>`;
    }
    const barras = serie.map((s, k) => {
      const x = ml + k * ancho + 0.75;
      const titulo = `${fmtHora.format(new Date(s.t))} · ${nf1.format(s.ros_m_min)} m/min · viento ${nf0.format(s.viento)} km/h del ${cardinal(s.dir)} · HR ${nf0.format(s.hr)} % · combustible fino ${nf1.format(s.m1_pct)} %`;
      return `<rect x="${x.toFixed(1)}" y="${y(s.ros_m_min).toFixed(1)}" width="${Math.max(1, ancho - 1.5).toFixed(1)}" height="${(H - mb - y(s.ros_m_min)).toFixed(1)}" fill="var(--lv3)" opacity="${(0.45 + 0.55 * s.ros_m_min / top).toFixed(2)}"><title>${esc(titulo)}</title></rect>`;
    }).join('');
    let etiquetas = '';
    for (let k = 0; k < serie.length; k += 6) etiquetas += `<text x="${ml + (k + 0.5) * ancho}" y="${H - 6}" text-anchor="middle">${esc(fmtHora.format(new Date(serie[k].t)))}</text>`;
    return `<svg class="grafico" viewBox="0 0 ${W} ${H}" role="img" aria-label="Velocidad potencial del frente por hora">${rejilla}${barras}${etiquetas}</svg>
      <div class="leyenda-grafico"><span>Velocidad potencial del frente en terreno plano (m/min), hora Perú</span></div>`;
  }

  function renderPropagacion(i, det) {
    const caja = $('#det-propagacion');
    if (!caja) return;
    const titulo = '<h3>Pronóstico de propagación</h3>';
    if (det.propErr) { caja.innerHTML = `${titulo}<p class="error-txt">${esc(det.propErr)}</p>`; return; }
    const p = det.prop;
    if (!p) return;
    if (!p.disponible) { caja.innerHTML = `${titulo}<p class="nota">${esc(p.motivo)}</p>`; return; }
    const h = horizonteVisible(p);
    const ph = p.por_horizonte.find((x) => x.h === h);
    const r = p.resumen;
    const cal = p.calibracion;
    const obs = p.observado;
    const botones = p.horizontes.map((x) => `<button type="button" role="radio" data-h="${x}" aria-checked="${x === h}">${x === 0 ? 'Ahora' : `${x} h`}</button>`).join('');
    const filas = p.por_horizonte.map((x) => `<tr${x.h === h ? ' class="sel"' : ''}><td>${x.h === 0 ? 'Ahora' : `${x.h} h`}</td><td class="num">${nf0.format(x.area_ha)}</td><td class="num">${nf0.format(x.area_posible_ha)}</td><td class="num">${nf1.format(x.alcance_km)}</td><td>${esc(x.rumbo_txt || '—')}</td></tr>`).join('');
    const amenazas = amenazasProp(p, det.ctx);
    const etiquetaCat = { poblados: 'Centro poblado', vias: 'Vía', salud: 'Salud', bomberos: 'Bomberos', policia: 'Comisaría', aereo: 'Pista / helipuerto' };
    const listaAmenazas = det.ctxErr ? avisoOsm(det, true)
      : !det.ctx ? '<p class="cargando-txt">Esperando los recursos de OpenStreetMap…</p>'
      : amenazas.length ? `<ul class="lista-simple">${amenazas.slice(0, 8).map((a) => `<li><span>${esc(etiquetaCat[a.cat])}: ${esc(a.nombre)}</span><span><b>${nf0.format(a.prob)} %</b> · ${esc(textoEta(a.eta))}</span></li>`).join('')}</ul>`
        : `<p class="nota">Ningún poblado, vía o servicio registrado en OpenStreetMap queda dentro del área posible a ${p.horizonte_h} h.</p>`;
    const textoCal = cal.aplicada
      ? `Velocidad del modelo ajustada ×${nf2.format(cal.factor)} con ${cal.n} píxeles nuevos entre pasadas (medición ×${nf2.format(cal.factor_crudo)}, peso ${nf0.format(cal.peso * 100)} %).`
      : obs.pasadas < 2 ? 'Sin calibrar: hay una sola pasada satelital; se usa el modelo sin ajuste.'
        : `Sin calibrar: en ${obs.pasadas} pasadas hubo ${obs.n} ${obs.n === 1 ? 'píxel nuevo útil' : 'píxeles nuevos útiles'} (se necesitan 3); se usa el modelo sin ajuste.`;
    const textoObs = obs.n ? ` Avance observado entre pasadas: mediana ${nf1.format(obs.ros_mediana_m_min)} m/min hacia el ${esc(obs.rumbo_txt)}.` : '';
    caja.innerHTML = `${titulo}
      <div class="segmentado segmentado-mini" role="radiogroup" aria-label="Horizonte del pronóstico">${botones}</div>
      <div class="peligro" style="--nivel-color:var(--lv3)"><span class="chip">Hacia el ${esc(r.rumbo_txt || '—')}</span>
        <span class="nota">Escenario mediano: ${nf0.format(r.crecimiento_ha)} ha nuevas y hasta ${nf1.format(r.alcance_km)} km del centro en ${p.horizonte_h} h.</span></div>
      <div class="rampa"><span class="rampa-barra"></span><span class="rampa-etq"><span>10 %</span><span>50 %</span><span>100 %</span></span>
        <span class="nota">Probabilidad de que el fuego llegue en ${h === 0 ? 'este momento' : `las próximas ${h} h`}. Toca el mapa para consultar un punto.</span></div>
      <div class="rejilla-datos">
        <div class="dato"><div class="dato-v">${nf0.format(ph.area_ha)}<small>ha</small></div><div class="dato-e">Área probable (≥50 %) · ${esc(etiquetaHorizonte(h))}${ph.crecimiento_ha ? ` · +${nf0.format(ph.crecimiento_ha)} ha` : ''}</div></div>
        <div class="dato"><div class="dato-v">${nf1.format(ph.alcance_km)}<small>km</small></div><div class="dato-e">Alcance desde el centro${ph.rumbo_txt ? ` · hacia el ${esc(ph.rumbo_txt)}` : ''}</div></div>
        <div class="dato"><div class="dato-v">${r.ros_pico_m_min != null ? nf1.format(r.ros_pico_m_min) : '—'}<small>m/min</small></div><div class="dato-e">Frente más rápido${r.ros_pico_t ? ` a las ${esc(fmtHora.format(new Date(r.ros_pico_t)))}` : ''} · ${esc(r.modelo_frente_nombre)}</div></div>
        <div class="dato"><div class="dato-v">${nf1.format(r.humedad_1h_pct)}<small>%</small></div><div class="dato-e">Humedad del combustible fino · pasto ${nf0.format(r.pasto_curado_pct)} % seco</div></div>
      </div>
      ${graficoRos(p.serie)}
      <div class="tabla-envoltura"><table class="tabla-prop">
        <thead><tr><th>Horizonte</th><th class="num">Probable ha</th><th class="num">Posible ha</th><th class="num">Alcance km</th><th>Rumbo</th></tr></thead>
        <tbody>${filas}</tbody></table></div>
      <h4 class="subtitulo">Puede alcanzar en ${p.horizonte_h} h</h4>
      ${listaAmenazas}
      <p class="nota"><b>Calibración con satélites.</b> ${esc(textoCal)}${textoObs}</p>
      ${p.avisos.length ? `<ul class="avisos-prop">${p.avisos.map((a) => `<li>${esc(a)}</li>`).join('')}</ul>` : ''}
      <details class="metodo"><summary>Cómo se calcula</summary>
        <p>Modelo de Rothermel (núcleo de BehavePlus y FARSITE) con modelos de combustible de Scott y Burgan asignados según la cobertura del suelo (Esri Land Cover 10 m${p.cobertura_anio ? `, ${p.cobertura_anio}` : ''}) y la altitud; pendiente del relieve Terrarium (AWS/Mapzen); viento, temperatura, humedad y lluvia horarios de Open-Meteo. El fuego avanza de celda en celda por el camino más rápido (tiempo mínimo de recorrido) desde cada píxel detectado y a su hora de detección.</p>
        <p>Se simulan ${p.miembros} escenarios que varían viento, dirección, humedad y velocidad; la probabilidad es la fracción de escenarios en que el fuego llega. Celdas de ${nf0.format(p.malla.celda_m)} m; calculado a las ${esc(fmtHora.format(new Date(p.generado)))}.</p>
        <p>No considera vientos de ladera locales, saltos de pavesas, fuego de copas ni el trabajo de las brigadas. Es orientativo: confirma en campo.</p>
        <p>Combustibles en el área: ${p.combustibles.slice(0, 4).map((c) => `${esc(c.codigo)} ${nf0.format(c.pct)} %`).join(' · ')}. Pendiente cerca del fuego: ${r.pendiente_media_pct != null ? `${nf0.format(r.pendiente_media_pct)} % media, ${nf0.format(r.pendiente_max_pct)} % máx.` : '—'}</p>
      </details>`;
    actualizarCompartir(i);
  }

  $('#detalle').addEventListener('click', (e) => {
    const b = e.target.closest('#det-propagacion .segmentado button');
    if (!b) return;
    state.horizonteProp = Number(b.dataset.h);
    const i = state.porId.get(state.sel);
    if (!i) return;
    renderPropagacion(i, state.cacheDetalle.get(i.id) || {});
    resaltarSeleccion();
  });

  function popupProp(latlng) {
    const i = state.porId.get(state.sel);
    const prop = i && (state.cacheDetalle.get(i.id) || {}).prop;
    if (!prop || !prop.disponible || !$('#c-prop').checked) return false;
    const c = celdaProp(prop, latlng.lat, latlng.lng);
    if (c == null) return false;
    const filas = prop.horizontes.filter((h) => h > 0).map((h) => `<span>≤ ${h} h</span><span>${nf0.format(prop.prob[String(h)][c])} %</span>`).join('');
    L.popup({ maxWidth: 260 }).setLatLng(latlng).setContent(`<div class="popup-foco"><span>Llegada del fuego</span><span>${esc(textoEta(prop.eta[c]))}</span>${filas}</div>`).openOn(mapa);
    return true;
  }
  mapa.on('click', (e) => { popupProp(e.latlng); });

  function dibujarRecursos(ctx) {
    for (const [cat, lista] of Object.entries(ctx.recursos || {})) {
      const c = CONFIG.categorias[cat];
      if (!c) continue;
      for (const r of lista) {
        L.marker([r.lat, r.lon], {
          pane: 'recursos',
          icon: L.divIcon({ className: '', html: `<span class="marcador-recurso" style="background:${c.color}">${c.letra}</span>`, iconSize: [22, 22], iconAnchor: [11, 11] }),
        }).bindTooltip(`<b>${esc(r.nombre)}</b><br>${esc(r.detalle || c.titulo)} · ${nf1.format(r.dist_km)} km al ${esc(r.rumbo_txt)}`).addTo(capas.seleccion);
      }
    }
  }

  function seleccionar(id, { zoom = true } = {}) {
    const i = state.porId.get(id);
    if (!i) return;
    state.sel = id;
    history.replaceState(null, '', `#${encodeURIComponent(id)}`);
    mostrarVista('detalle');
    renderDetalle(i);
    $$('.tarjeta').forEach((t) => t.classList.toggle('sel', t.dataset.id === id));
    resaltarSeleccion();
    if (zoom) {
      if (i.envolvente.length >= 3) mapa.fitBounds(L.latLngBounds(i.envolvente).pad(0.8), { maxZoom: 13 });
      else mapa.setView([i.lat, i.lon], Math.max(mapa.getZoom(), 12));
    }
    if (window.matchMedia('(max-width: 860px)').matches) $('#panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
    cargarDetalle(i);
  }

  async function cargarDetalle(i) {
    let det = state.cacheDetalle.get(i.id);
    if (!det) { det = {}; state.cacheDetalle.set(i.id, det); }
    const q = `lat=${i.lat}&lon=${i.lon}`;
    const tareas = [];
    if (!det.meteo && !det.meteoErr) {
      tareas.push(getJSON(`/api/meteo?${q}`).then((m) => { det.meteo = m; }).catch((e) => { det.meteoErr = e.message; }).finally(() => {
        if (state.sel === i.id) { renderMeteo(i, det); resaltarSeleccion(); }
      }));
    } else renderMeteo(i, det);
    if (!det.ctx && !det.ctxErr) {
      tareas.push(getJSON(`/api/contexto?${q}`).then((c) => { det.ctx = c; }).catch((e) => {
        det.ctxErr = e.message;
        // un reintento automático pasado el minuto (el servidor no repite la consulta antes de 45 s)
        if (!det.ctxReintentado) {
          det.ctxReintentado = true;
          det.ctxReintento = setTimeout(() => { det.ctxReintento = null; if (state.sel === i.id && det.ctxErr) reintentarOsm(); }, 60000);
        }
      }).finally(() => {
        if (state.sel === i.id) { renderContexto(i, det); renderPropagacion(i, det); resaltarSeleccion(); cargarAire(i, det); }
      }));
    } else renderContexto(i, det);
    tareas.push(cargarAire(i, det));  // sin lugares al inicio; se repite con los poblados cuando llega OpenStreetMap
    if (!det.prop && !det.propErr) {
      tareas.push(getJSON(`/api/propagacion?id=${encodeURIComponent(i.id)}&${parametros()}`).then((p) => { det.prop = p; }).catch((e) => { det.propErr = e.message; }).finally(() => {
        if (state.sel === i.id) { renderPropagacion(i, det); resaltarSeleccion(); }
      }));
    } else renderPropagacion(i, det);
    await Promise.allSettled(tareas);
  }

  function enlacesExternos(i) {
    const fecha = hoyLima();
    const v = [i.lon - 0.25, i.lat - 0.18, i.lon + 0.25, i.lat + 0.18].map((x) => x.toFixed(4)).join(',');
    const capasWv = `${CONFIG.gibs.colorReal},${CONFIG.gibs.anomalias[0]},${CONFIG.gibs.anomalias[1]},Coastlines_15m`;
    const rangoFirms = { '24h': '24hrs', '48h': '48hrs', '7d': '7days' }[state.rango];
    return {
      gmaps: `https://www.google.com/maps/search/?api=1&query=${i.lat},${i.lon}`,
      ruta: `https://www.google.com/maps/dir/?api=1&destination=${i.lat},${i.lon}`,
      osm: `https://www.openstreetmap.org/?mlat=${i.lat}&mlon=${i.lon}#map=13/${i.lat}/${i.lon}`,
      firms: `https://firms.modaps.eosdis.nasa.gov/map/#d:${rangoFirms};@${i.lon.toFixed(3)},${i.lat.toFixed(3)},12.0z`,
      worldview: `https://worldview.earthdata.nasa.gov/?v=${v}&l=${capasWv}&t=${fecha}`,
    };
  }

  function renderDetalle(i) {
    const e = enlacesExternos(i);
    const sats = Object.entries(i.satelites).map(([s, n]) => `<li><span>${esc(s)}</span><span>${n}</span></li>`).join('');
    const c = i.confianza;
    $('#detalle').innerHTML = `
      <div class="det-cabecera" style="--nivel-color:var(--lv${i.nivel})">
        <div class="peligro"><span class="chip">Nivel ${esc(i.nivel_texto)}</span><span class="estado estado-${i.estado}">${esc(etiquetaEstado(i.estado))} · última detección ${esc(hace(i.ultima))}</span></div>
        <h2 id="det-titulo">${esc(i.provincia || 'Provincia no identificada')}, ${esc(i.departamento)}</h2>
        <div class="sub" id="det-sub">Distrito: consultando OpenStreetMap…</div>
        <div id="det-anp"></div>
      </div>

      <div class="bloque">
        <h3>Ubicación</h3>
        <div class="coords">
          <span>Lat, lon</span><span>${i.lat.toFixed(5)}, ${i.lon.toFixed(5)}</span><button type="button" class="mini-boton" data-copiar="${i.lat.toFixed(5)}, ${i.lon.toFixed(5)}">Copiar</button>
          <span>UTM WGS84</span><span>${esc(i.utm)}</span><button type="button" class="mini-boton" data-copiar="${esc(i.utm)}">Copiar</button>
          <span>Altitud</span><span id="det-altitud">—</span><span></span>
        </div>
        <div class="enlaces">
          <a href="${e.ruta}" target="_blank" rel="noopener">Cómo llegar</a>
          <a href="${e.gmaps}" target="_blank" rel="noopener">Google Maps</a>
          <a href="${e.osm}" target="_blank" rel="noopener">OpenStreetMap</a>
          <a href="${e.firms}" target="_blank" rel="noopener">NASA FIRMS</a>
          <a href="${e.worldview}" target="_blank" rel="noopener">Imagen NASA Worldview</a>
        </div>
      </div>

      <div class="bloque">
        <h3>Magnitud del fuego</h3>
        <div class="rejilla-datos">
          <div class="dato"><div class="dato-v">${nf0.format(i.area_pixeles_ha)}<small>ha</small></div><div class="dato-e">Área con calor detectado</div></div>
          <div class="dato"><div class="dato-v">${nf0.format(i.area_envolvente_ha)}<small>ha</small></div><div class="dato-e">Envolvente de las detecciones</div></div>
          <div class="dato"><div class="dato-v">${nf0.format(i.frp_total)}<small>MW</small></div><div class="dato-e">FRP total · máx. ${nf1.format(i.frp_max)} MW</div></div>
          <div class="dato"><div class="dato-v">${nf0.format(i.detecciones)}</div><div class="dato-e">Detecciones · ${i.nocturnas} nocturnas</div></div>
        </div>
        <ul class="lista-simple">
          <li><span>Primera detección</span><span>${esc(fechaLima(i.primera))}</span></li>
          <li><span>Última detección</span><span>${esc(fechaLima(i.ultima))} (hora Perú)</span></li>
          <li><span>Temperatura de brillo máx.</span><span>${nf0.format(i.brillo_max)} K</span></li>
          <li><span>Confianza alta / nominal / baja</span><span>${c.alta} / ${c.nominal} / ${c.baja}</span></li>
          ${sats}
        </ul>
      </div>

      <div class="bloque" id="det-meteo"><h3>Condiciones meteorológicas</h3><p class="cargando-txt">Consultando Open-Meteo…</p></div>
      <div class="bloque" id="det-propagacion"><h3>Pronóstico de propagación</h3><p class="cargando-txt">Simulando el avance con relieve, vegetación y pronóstico del tiempo…</p></div>
      <div class="bloque" id="det-aire"><h3>Calidad del aire (INCA)</h3><p class="cargando-txt">Consultando el modelo CAMS…</p></div>
      <div class="bloque" id="det-recursos"><h3>Recursos cercanos</h3><p class="cargando-txt">Buscando bomberos, salud, agua y accesos en OpenStreetMap…</p></div>

      <div class="bloque">
        <h3>Compartir</h3>
        <div class="acciones">
          <button type="button" class="boton boton-primario" id="btn-reporte">Copiar reporte</button>
          <a class="boton" id="btn-whatsapp" href="#" target="_blank" rel="noopener">Enviar por WhatsApp</a>
          <button type="button" class="boton" id="btn-enlace">Copiar enlace</button>
        </div>
        <p class="nota">Llama al <b>116</b> (bomberos) o al <b>115</b> (Defensa Civil) si el incendio amenaza viviendas o personas. Los focos satelitales deben confirmarse en campo.</p>
      </div>`;
    actualizarCompartir(i);
    $('#btn-reporte').addEventListener('click', () => copiar(textoReporte(i), 'Reporte copiado'));
    $('#btn-enlace').addEventListener('click', () => copiar(location.href, 'Enlace copiado'));
  }

  $('#detalle').addEventListener('click', (e) => {
    const b = e.target.closest('[data-copiar]');
    if (b) copiar(b.dataset.copiar, 'Coordenadas copiadas');
  });

  function actualizarCompartir(i) {
    const a = $('#btn-whatsapp');
    if (a) a.href = `https://wa.me/?text=${encodeURIComponent(textoReporte(i))}`;
  }

  function textoReporte(i) {
    const det = state.cacheDetalle.get(i.id) || {};
    const m = det.meteo;
    const ctx = det.ctx;
    const adm = (ctx && ctx.admin) || {};
    const lugar = [adm.distrito, i.provincia || adm.provincia, i.departamento].filter(Boolean).join(', ');
    const lineas = [
      `INCENDIO (foco satelital) - Nivel ${i.nivel_texto} - ${etiquetaEstado(i.estado)}`,
      `Lugar: ${lugar}`,
      `Coordenadas: ${i.lat.toFixed(5)}, ${i.lon.toFixed(5)} | UTM ${i.utm}`,
      `Área con calor: ~${nf0.format(i.area_pixeles_ha)} ha | Envolvente: ~${nf0.format(i.area_envolvente_ha)} ha`,
      `FRP total: ${nf0.format(i.frp_total)} MW (${i.detecciones} detecciones)`,
      `Última detección: ${fechaLima(i.ultima)} hora Perú (${hace(i.ultima)})`,
    ];
    if (ctx && ctx.protegidas && ctx.protegidas.length) lineas.push(`Dentro de: ${ctx.protegidas.map((p) => p.nombre).join('; ')}`);
    if (m && m.actual) {
      const a = m.actual;
      lineas.push(`Viento: ${nf0.format(a.viento)} km/h del ${a.dir_txt} (ráfagas ${nf0.format(a.rafagas)}) -> avance probable hacia el ${m.propagacion.rumbo_txt}`);
      lineas.push(`Temp ${nf0.format(a.temp)} °C | HR ${nf0.format(a.hr)} % | ${m.dias_sin_lluvia} días sin lluvia | Peligro meteorológico: ${m.peligro_txt}`);
    }
    const ai = det.aire;
    if (ai) {
      const fa = ai.foco.actual;
      lineas.push(`Calidad del aire (INCA, modelo CAMS): ${fa.inca} ${INCA[fa.categoria].nombre} por ${fa.contaminante} en el incendio${ai.humo_rumbo_txt ? `; humo hacia el ${ai.humo_rumbo_txt}` : ''}`);
      if (INCA[ai.alerta.categoria].orden >= 2) lineas.push(`Aire ${INCA[ai.alerta.categoria].nombre} (${ai.alerta.motivo}): ${ai.recomendaciones.oficial} Usar mascarilla N95/KN95 si se sale.`);
    }
    const p = det.prop;
    if (p && p.disponible) {
      const corto = p.por_horizonte.find((x) => x.h === 6) || p.por_horizonte[p.por_horizonte.length - 1];
      lineas.push(`Propagación (modelo, orientativo): hacia el ${p.resumen.rumbo_txt || '—'}; en ${corto.h} h ~${nf0.format(corto.area_ha)} ha, en ${p.horizonte_h} h ~${nf0.format(p.resumen.area_ha)} ha y hasta ${nf1.format(p.resumen.alcance_km)} km del centro`);
      const amen = amenazasProp(p, ctx).filter((x) => x.cat === 'poblados' || x.cat === 'vias').slice(0, 3);
      if (amen.length) lineas.push(`Podría alcanzar: ${amen.map((x) => `${x.nombre} (${nf0.format(x.prob)} %, ${textoEta(x.eta)})`).join('; ')}`);
    }
    if (ctx && ctx.recursos) {
      const b = ctx.recursos.bomberos && ctx.recursos.bomberos[0];
      if (b) lineas.push(`Bomberos más cercanos: ${b.nombre} (${nf1.format(b.dist_km)} km al ${b.rumbo_txt})`);
      const v = ctx.recursos.vias && ctx.recursos.vias[0];
      if (v) lineas.push(`Acceso vial: ${v.detalle} a ${nf1.format(v.dist_km)} km`);
      const ag = ctx.recursos.agua && ctx.recursos.agua[0];
      if (ag) lineas.push(`Agua: ${ag.nombre} a ${nf1.format(ag.dist_km)} km`);
    }
    lineas.push(`Mapa: https://www.google.com/maps/search/?api=1&query=${i.lat},${i.lon}`);
    lineas.push('Fuente: NASA FIRMS (VIIRS/MODIS). Requiere confirmación en campo. Emergencias: 116 bomberos, 115 Defensa Civil.');
    return lineas.join('\n');
  }

  function brujula(dirViento, avance) {
    const ticks = Array.from({ length: 16 }, (_, k) => {
      const a = k * 22.5, largo = k % 4 === 0 ? 7 : 3.5;
      return `<line x1="0" y1="-44" x2="0" y2="${-44 + largo}" transform="rotate(${a})" stroke="var(--line)" stroke-width="1.4"/>`;
    }).join('');
    return `<svg class="brujula" viewBox="-52 -52 104 104" role="img" aria-label="Viento del ${esc(cardinal(dirViento))}, avance hacia el ${esc(cardinal(avance))}">
      <circle r="44" fill="var(--surface-2)" stroke="var(--line)"/>
      ${ticks}
      <text x="0" y="-30" text-anchor="middle">N</text><text x="32" y="4" text-anchor="middle">E</text>
      <text x="0" y="38" text-anchor="middle">S</text><text x="-32" y="4" text-anchor="middle">O</text>
      <g transform="rotate(${avance})">
        <line x1="0" y1="18" x2="0" y2="-26" stroke="var(--lv3)" stroke-width="3.2" stroke-linecap="round"/>
        <path d="M0 -38 L7 -24 L-7 -24Z" fill="var(--lv3)"/>
        <circle cx="0" cy="22" r="4" fill="var(--accent)"/>
      </g>
    </svg>`;
  }

  function grafico(p) {
    const W = 360, H = 162, ml = 30, mr = 32, mt = 34, mb = 22;
    const n = p.hora.length;
    if (n < 2) return '';
    const vmax = Math.max(20, ...p.viento.filter((v) => v != null), ...p.rafagas.filter((v) => v != null)) * 1.1;
    const paso = vmax > 60 ? 20 : vmax > 30 ? 10 : 5;
    const top = Math.ceil(vmax / paso) * paso;
    const x = (k) => ml + (k / (n - 1)) * (W - ml - mr);
    const yv = (v) => mt + (1 - v / top) * (H - mt - mb);
    const yh = (h) => mt + (1 - h / 100) * (H - mt - mb);
    const linea = (arr, fy) => arr.map((v, k) => (v == null ? null : `${x(k).toFixed(1)},${fy(v).toFixed(1)}`)).filter(Boolean).join(' ');
    let rejilla = '';
    for (let v = 0; v <= top; v += paso) {
      rejilla += `<line x1="${ml}" x2="${W - mr}" y1="${yv(v)}" y2="${yv(v)}" stroke="var(--line)" stroke-width="1"/>`
        + `<text x="${ml - 5}" y="${yv(v) + 3.5}" text-anchor="end">${v}</text>`;
    }
    let ejeHr = '';
    for (const h of [0, 50, 100]) ejeHr += `<text x="${W - mr + 5}" y="${yh(h) + 3.5}">${h}%</text>`;
    let etiquetas = '';
    for (let k = 0; k < n; k += 6) etiquetas += `<text x="${x(k)}" y="${H - 6}" text-anchor="middle">${esc(p.hora[k])}</text>`;
    let flechas = '';
    for (let k = 0; k < n; k += 3) {
      if (p.dir[k] == null) continue;
      const a = (p.dir[k] + 180) % 360;
      flechas += `<g transform="translate(${x(k)},11) rotate(${a})"><line x1="0" y1="5" x2="0" y2="-4" stroke="var(--lv3)" stroke-width="1.6"/><path d="M0 -7 L3 -2.5 L-3 -2.5Z" fill="var(--lv3)"/></g>`;
    }
    const area = `${x(0)},${yv(0)} ${linea(p.viento, yv)} ${x(n - 1)},${yv(0)}`;
    return `<svg class="grafico" viewBox="0 0 ${W} ${H}" role="img" aria-label="Pronóstico de viento y humedad para las próximas 24 horas">
      ${rejilla}${ejeHr}${etiquetas}${flechas}
      <polygon points="${area}" fill="var(--accent)" opacity=".10"/>
      <polyline points="${linea(p.hr, yh)}" fill="none" stroke="var(--muted)" stroke-width="1.6" stroke-dasharray="2 3"/>
      <polyline points="${linea(p.rafagas, yv)}" fill="none" stroke="var(--accent)" stroke-width="1.3" stroke-dasharray="5 3"/>
      <polyline points="${linea(p.viento, yv)}" fill="none" stroke="var(--accent)" stroke-width="2.2"/>
      <text x="${ml - 5}" y="${mt - 9}" text-anchor="end">km/h</text>
    </svg>
    <div class="leyenda-grafico"><span><i style="border-color:var(--accent)"></i>Viento</span><span><i style="border-color:var(--accent);border-top-style:dashed"></i>Ráfagas</span><span><i style="border-color:var(--muted);border-top-style:dotted"></i>Humedad relativa</span><span>Flechas: avance cada 3 h</span></div>`;
  }

  function colorPeligro(n) { return n >= 5 ? 5 : n >= 4 ? 4 : n >= 3 ? 3 : n >= 2 ? 2 : 1; }

  function renderMeteo(i, det) {
    const caja = $('#det-meteo');
    if (!caja) return;
    if (det.meteoErr) {
      caja.innerHTML = `<h3>Condiciones meteorológicas</h3><p class="error-txt">No se pudo consultar Open-Meteo: ${esc(det.meteoErr)}</p>`;
      return;
    }
    const m = det.meteo;
    if (!m) return;
    const a = m.actual;
    const p = m.propagacion;
    $('#det-altitud').textContent = m.elevacion_m != null ? `${nf0.format(m.elevacion_m)} m s. n. m. (celda del modelo)` : '—';
    caja.innerHTML = `<h3>Condiciones meteorológicas · ${esc(a.hora ? a.hora.slice(11, 16) : '')} h</h3>
      <div class="meteo-cabecera">
        ${brujula(a.dir, p.rumbo)}
        <div class="lista-simple">
          <div class="dato-v">${nf0.format(a.viento)}<small>km/h del ${esc(a.dir_txt)}</small></div>
          <div class="dato-e">Ráfagas ${nf0.format(a.rafagas)} km/h · el viento empuja el fuego y el humo hacia el <b>${esc(p.rumbo_txt)}</b></div>
          <div class="dato-e">El avance según relieve y vegetación está en «Pronóstico de propagación».</div>
        </div>
      </div>
      <div class="rejilla-datos">
        <div class="dato"><div class="dato-v">${nf0.format(a.temp)}<small>°C</small></div><div class="dato-e">Temperatura · ${esc(a.cielo)}</div></div>
        <div class="dato"><div class="dato-v">${nf0.format(a.hr)}<small>%</small></div><div class="dato-e">Humedad relativa</div></div>
        <div class="dato"><div class="dato-v">${m.dias_sin_lluvia}${m.dias_sin_lluvia_mas ? '+' : ''}<small>días</small></div><div class="dato-e">Sin lluvia · ${nf1.format(m.lluvia_7d_mm)} mm en 7 días</div></div>
        <div class="dato"><div class="dato-v">${m.angstrom != null ? nf1.format(m.angstrom) : '—'}</div><div class="dato-e">Índice de Angström · ${esc(m.angstrom_txt || '')}</div></div>
      </div>
      <div class="peligro" style="--nivel-color:var(--lv${colorPeligro(m.peligro)})"><span class="chip">Peligro meteorológico ${esc(m.peligro_txt)}</span>
        <span class="nota">Viento máx. 24 h: ${m.viento_max_24h.valor != null ? `${nf0.format(m.viento_max_24h.valor)} km/h a las ${esc(m.viento_max_24h.hora)}` : '—'} · lluvia hasta ${m.prob_lluvia_max_24h ?? '—'} %</span></div>
      ${grafico(m.pronostico)}`;
    actualizarCompartir(i);
  }

  // ------------------------------------------------------------------------------------
  // Calidad del aire: Índice de Calidad del Aire (INCA, R. M. N.° 181-2016-MINAM)
  // ------------------------------------------------------------------------------------
  const INCA = {
    buena: { nombre: 'Buena', color: '#2e9e44', tinta: '#ffffff', orden: 0 },
    moderada: { nombre: 'Moderada', color: '#f2d600', tinta: '#1f1f1f', orden: 1 },
    mala: { nombre: 'Mala', color: '#f28c28', tinta: '#1f1f1f', orden: 2 },
    umbral: { nombre: 'Umbral de cuidado', color: '#d7191c', tinta: '#ffffff', orden: 3 },
  };
  function estiloInca(cat) { const c = INCA[cat] || INCA.buena; return `--inca:${c.color};--inca-tinta:${c.tinta}`; }
  function gravedadInca(e) { return e ? INCA[e.categoria].orden * 10000 + e.inca : -1; }
  function textoPromedio(e) { return `${esc(e.contaminante)} ${nf1.format(e.conc)} µg/m³ (promedio ${e.horas} h)`; }

  function cargarAire(i, det) {
    const pobl = (((det.ctx || {}).recursos || {}).poblados || []).slice(0, 6);
    const lugares = pobl.map((p) => `${p.nombre.replace(/[;|]/g, ' ')}|${p.lat}|${p.lon}`).join(';');
    if (det.aireLugares === lugares && (det.aire || det.aireErr || det.aireCargando)) {
      if (!det.aireCargando) renderAire(i, det);
      return Promise.resolve();
    }
    det.aireLugares = lugares;
    det.aireCargando = true;
    const turno = (det.aireTurno || 0) + 1;
    det.aireTurno = turno;
    const q = `lat=${i.lat}&lon=${i.lon}${lugares ? `&lugares=${encodeURIComponent(lugares)}` : ''}`;
    return getJSON(`/api/aire?${q}`).then((a) => { if (det.aireTurno === turno) { det.aire = a; det.aireErr = null; } })
      .catch((e) => { if (det.aireTurno === turno && !det.aire) det.aireErr = e.message; })
      .finally(() => {
        if (det.aireTurno !== turno) return;  // llegó una consulta más nueva
        det.aireCargando = false;
        if (state.sel === i.id) { renderAire(i, det); resaltarSeleccion(); }
      });
  }

  function graficoInca(serie) {
    if (serie.length < 2) return '';
    const W = 360, H = 112, ml = 30, mr = 6, mt = 8, mb = 20;
    const top = Math.max(120, ...serie.map((s) => s.inca)) * 1.08;
    const ancho = (W - ml - mr) / serie.length;
    const y = (v) => mt + (1 - Math.min(v, top) / top) * (H - mt - mb);
    let rejilla = '';
    for (const v of [50, 100, 500].filter((x) => x < top)) {
      rejilla += `<line x1="${ml}" x2="${W - mr}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${ml - 4}" y="${y(v) + 3.5}" text-anchor="end">${v}</text>`;
    }
    const barras = serie.map((s, k) => `<rect x="${(ml + k * ancho + 0.5).toFixed(1)}" y="${y(s.inca).toFixed(1)}" width="${Math.max(1, ancho - 1).toFixed(1)}" height="${(H - mb - y(s.inca)).toFixed(1)}" fill="${INCA[s.categoria].color}" opacity="${s.pronostico ? 0.55 : 1}"><title>${esc(fmtFecha.format(new Date(s.t)))} · INCA ${s.inca} ${esc(INCA[s.categoria].nombre)} (${esc(s.contaminante)})${s.pronostico ? ' · pronóstico' : ''}</title></rect>`).join('');
    const kAhora = serie.findIndex((s) => s.pronostico);
    const ahora = kAhora > 0 ? `<line x1="${ml + kAhora * ancho}" x2="${ml + kAhora * ancho}" y1="${mt}" y2="${H - mb}" stroke="var(--ink)" stroke-dasharray="3 3"/><text x="${ml + kAhora * ancho + 3}" y="${mt + 8}" text-anchor="start">ahora</text>` : '';
    let etiquetas = '';
    for (let k = 0; k < serie.length; k += 12) etiquetas += `<text x="${ml + (k + 0.5) * ancho}" y="${H - 6}" text-anchor="middle">${esc(fmtHora.format(new Date(serie[k].t)))}</text>`;
    return `<svg class="grafico" viewBox="0 0 ${W} ${H}" role="img" aria-label="INCA en el incendio: últimas 24 h y pronóstico de 24 h">${rejilla}${barras}${ahora}${etiquetas}</svg>
      <div class="leyenda-grafico"><span>INCA horario en el incendio: últimas 24 h (sólido) y pronóstico (claro)</span></div>`;
  }

  function renderAire(i, det) {
    const caja = $('#det-aire');
    if (!caja) return;
    const titulo = '<h3>Calidad del aire (INCA)</h3>';
    if (!det.aire) {
      caja.innerHTML = det.aireErr ? `${titulo}<p class="error-txt">${esc(det.aireErr)}</p>` : `${titulo}<p class="cargando-txt">Consultando el modelo CAMS…</p>`;
      return;
    }
    const a = det.aire;
    const f = a.foco.actual;
    const pf = a.foco.pronostico;
    const r = a.recomendaciones;
    const al = a.alerta;
    const peorQueAhora = INCA[al.categoria].orden > INCA[f.categoria].orden;
    const cercanos = a.puntos.filter((p) => p.tipo !== 'foco' && p.actual)
      .sort((x, y) => (y.tipo === 'lugar') - (x.tipo === 'lugar') || gravedadInca(y.actual) - gravedadInca(x.actual));
    const filas = cercanos.slice(0, 9).map((p) => `<li style="${estiloInca(p.actual.categoria)}"><span><i class="punto-inca"></i>${esc(p.nombre)}${p.tipo === 'lugar' ? ` <small>${nf1.format(p.dist_km)} km al ${esc(p.rumbo_txt)}</small>` : ''}${p.a_sotavento ? ' <span class="chip chip-humo">humo</span>' : ''}</span>
        <span><b>${p.actual.inca}</b> ${esc(INCA[p.actual.categoria].nombre)}${p.pronostico && INCA[p.pronostico.categoria].orden > INCA[p.actual.categoria].orden ? ` → ${esc(INCA[p.pronostico.categoria].nombre)}` : ''}</span></li>`).join('');
    const tabla = Object.values(f.detalle).map((c) => `<tr><td>${esc(c.nombre)}</td><td class="num">${c.horas} h</td><td class="num">${nf1.format(c.conc)}</td><td class="num">${c.inca}</td><td><i class="punto-inca" style="${estiloInca(c.categoria)}"></i>${esc(INCA[c.categoria].nombre)}</td></tr>`).join('');
    caja.innerHTML = `${titulo}
      <div class="inca-cabecera" style="${estiloInca(f.categoria)}">
        <div class="inca-valor">${f.inca}</div>
        <div><div class="inca-cat">${esc(INCA[f.categoria].nombre)}</div>
          <div class="dato-e">En el incendio ahora · ${textoPromedio(f)}</div></div>
      </div>
      ${peorQueAhora ? `<div class="alerta-aire" style="${estiloInca(al.categoria)}"><b>Atención: calidad ${esc(INCA[al.categoria].nombre)}</b> (INCA ${al.inca}, ${esc(al.contaminante)}) · ${esc(al.motivo || '')}.</div>` : ''}
      <div class="rejilla-datos">
        <div class="dato"><div class="dato-v">${a.peor.inca}<small>${esc(INCA[a.peor.categoria].nombre)}</small></div><div class="dato-e">Peor valor a 50 km · ${esc(a.peor.nombre)} (${esc(a.peor.contaminante)})</div></div>
        <div class="dato"><div class="dato-v">${pf ? pf.inca : '—'}<small>${pf ? esc(INCA[pf.categoria].nombre) : ''}</small></div><div class="dato-e">Máximo en el incendio en 24 h${pf ? ` · ${esc(fmtFecha.format(new Date(pf.t)))}` : ''}</div></div>
      </div>
      <p class="nota">${a.humo_rumbo_txt ? `Con el viento de las próximas 6 h el humo va hacia el <b>${esc(a.humo_rumbo_txt)}</b>; las zonas marcadas «humo» están en esa dirección.` : 'Viento en calma o variable: el humo puede acumularse alrededor del incendio.'}</p>
      ${graficoInca(a.serie_foco)}
      <h4 class="subtitulo">Zonas cercanas</h4>
      <ul class="lista-simple lista-aire">${filas}</ul>
      <div class="fila-botones"><button type="button" class="mini-boton" id="btn-aire-mapa">Ver zonas en el mapa</button></div>
      <div class="recomendaciones-aire" style="${estiloInca(r.categoria)}">
        <h4>Recomendaciones · calidad ${esc(INCA[r.categoria].nombre)}</h4>
        <p><b>Cuidados:</b> ${esc(r.cuidados)}</p>
        <p><b>Recomendación (MINAM):</b> ${esc(r.oficial)}</p>
        ${r.acciones.length ? `<ul>${r.acciones.map((x) => `<li>${esc(x)}</li>`).join('')}</ul><p class="nota"><b>Síntomas de alerta:</b> ${esc(r.sintomas)}</p>` : ''}
      </div>
      <div class="cerca-fuego"><b>Cerca del incendio</b><ul>${r.humo_cercano.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>
      <details class="metodo"><summary>INCA por contaminante en el incendio</summary>
        <div class="tabla-envoltura"><table class="tabla-prop"><thead><tr><th>Contaminante</th><th class="num">Promedio</th><th class="num">µg/m³</th><th class="num">INCA</th><th>Calificación</th></tr></thead><tbody>${tabla}</tbody></table></div>
        <p>El INCA general es el del contaminante en peor categoría. Escala oficial: Buena 0–50, Moderada 51–100, Mala 101 hasta el valor umbral de cuidado (VUEC) de cada contaminante y Umbral de cuidado por encima.</p>
      </details>
      <p class="nota">Concentraciones del modelo CAMS (Copernicus), que incluye el humo de incendios con celdas de unos 40 km; no son mediciones de estaciones. Junto al fuego el humo puede ser mucho más denso. Mediciones oficiales: <a href="https://sinia.minam.gob.pe/novedades/conoce-calidad-aire-tiempo-real-traves-infoaire-peru" target="_blank" rel="noopener">INFOAIRE Perú</a>.</p>`;
    actualizarCompartir(i);
  }

  function dibujarAire(a) {
    if (!$('#c-aire').checked) return;
    const f = a.foco;
    if (a.humo_rumbo != null) {
      const pts = [[f.lat, f.lon]];
      for (let d = -a.sector_humo.grados; d <= a.sector_humo.grados; d += 5) pts.push(destino(f.lat, f.lon, a.humo_rumbo + d, a.sector_humo.alcance_km));
      L.polygon(pts, { renderer: rVectores, color: cssVar('--muted'), weight: 1, dashArray: '4 4', fillColor: cssVar('--muted'), fillOpacity: 0.12 })
        .bindTooltip(`Hacia aquí va el humo en las próximas 6 h (hacia el ${esc(a.humo_rumbo_txt)})`, { sticky: true })
        .addTo(capas.seleccion)
        .bringToBack();
    }
    for (const p of a.puntos) {
      if (!p.actual) continue;
      const c = INCA[p.actual.categoria];
      const futuro = p.pronostico && INCA[p.pronostico.categoria].orden > c.orden ? `<br>En 24 h: ${p.pronostico.inca} ${esc(INCA[p.pronostico.categoria].nombre)} (${esc(fmtFecha.format(new Date(p.pronostico.t)))})` : '';
      // los poblados ya tienen su marcador «C»: el INCA va como anillo alrededor para no taparlo
      const estilo = p.tipo === 'lugar' ? { radius: 15, color: c.color, weight: 4, opacity: 0.95, fill: false }
        : { radius: p.tipo === 'foco' ? 11 : 7, color: '#ffffff', weight: 1.5, fillColor: c.color, fillOpacity: 0.95 };
      L.circleMarker([p.lat, p.lon], { renderer: rVectores, ...estilo })
        .bindTooltip(`<b>${esc(p.nombre)}</b> · INCA ${p.actual.inca} ${esc(c.nombre)}<br>${textoPromedio(p.actual)}${futuro}${p.a_sotavento ? '<br>En la dirección del humo' : ''}`)
        .addTo(capas.seleccion);
    }
  }

  $('#detalle').addEventListener('click', (e) => {
    if (!e.target.closest('#btn-aire-mapa')) return;
    const i = state.porId.get(state.sel);
    const a = i && (state.cacheDetalle.get(i.id) || {}).aire;
    if (!a) return;
    if (!$('#c-aire').checked) { $('#c-aire').checked = true; resaltarSeleccion(); }
    mapa.fitBounds(L.latLngBounds(a.puntos.map((p) => [p.lat, p.lon])).pad(0.1));
  });

  // Overpass (OpenStreetMap) es un servicio público que a veces se satura: se explica y se reintenta
  function avisoOsm(det, corto = false) {
    const auto = det.ctxReintento ? ' Se volverá a intentar en un minuto.' : '';
    return `<div class="aviso-osm"><p><b>OpenStreetMap no responde.</b> ${corto ? 'Sin la lista de poblados y vías no se puede calcular qué lugares alcanzaría el fuego.' : 'Los servidores públicos de consulta (Overpass) están saturados o caídos; no es un problema de tu conexión.'}${auto}</p>
      <button type="button" class="mini-boton" data-reintentar-osm>Reintentar ahora</button>
      ${corto ? '' : `<details><summary>Detalle técnico</summary><p class="nota">${esc(det.ctxErr)}</p></details>`}</div>`;
  }

  function reintentarOsm() {
    const i = state.porId.get(state.sel);
    if (!i) return;
    const det = state.cacheDetalle.get(i.id) || {};
    clearTimeout(det.ctxReintento);
    det.ctxReintento = null;
    det.ctxErr = null;
    if (det.ctx && det.ctx.desactualizado) det.ctx = null;
    const caja = $('#det-recursos');
    if (caja) caja.innerHTML = '<h3>Recursos cercanos</h3><p class="cargando-txt">Consultando OpenStreetMap de nuevo…</p>';
    cargarDetalle(i);
  }
  $('#detalle').addEventListener('click', (e) => { if (e.target.closest('[data-reintentar-osm]')) reintentarOsm(); });

  function renderContexto(i, det) {
    const caja = $('#det-recursos');
    if (!caja) return;
    if (det.ctxErr) {
      caja.innerHTML = `<h3>Recursos cercanos</h3>${avisoOsm(det)}`;
      $('#det-sub').textContent = 'Distrito no disponible';
      return;
    }
    const c = det.ctx;
    if (!c) return;
    const notaOsm = c.desactualizado
      ? `<p class="aviso-osm">Datos guardados de OpenStreetMap del ${esc(fechaLima(c.consultado))}: los servidores no responden ahora. <button type="button" class="mini-boton" data-reintentar-osm>Reintentar</button></p>`
      : (c.avisos || []).map((a) => `<p class="nota">${esc(a)}</p>`).join('');
    const adm = c.admin || {};
    $('#det-sub').textContent = adm.distrito ? `Distrito de ${adm.distrito}${adm.provincia ? ` · Provincia de ${adm.provincia}` : ''}` : 'Distrito no identificado en OpenStreetMap';
    const normal = (t) => (t || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
    if (adm.provincia && normal(adm.provincia) === normal(i.provincia)) {
      $('#det-titulo').textContent = `${adm.provincia}, ${i.departamento}`;  // OSM trae tildes
    }
    if (c.protegidas && c.protegidas.length) {
      $('#det-anp').innerHTML = `<div class="alerta-anp">Dentro de área protegida: ${c.protegidas.map((p) => esc(p.nombre)).join('; ')}</div>`;
    }
    const orden = ['bomberos', 'salud', 'vias', 'agua', 'poblados', 'aereo', 'policia'];
    const grupos = orden.map((cat) => {
      const lista = (c.recursos || {})[cat] || [];
      const conf = CONFIG.categorias[cat];
      const filas = lista.length
        ? lista.map((r) => `<li><span>${esc(r.nombre)}${r.telefono ? ` · <span style="user-select:all">${esc(r.telefono)}</span>` : ''}</span><span class="r-dist">${nf1.format(r.dist_km)} km ${esc(r.rumbo_txt)}</span>
            <span class="r-det">${esc(r.detalle || '')}</span>
            <a href="https://www.google.com/maps/dir/?api=1&origin=${r.lat},${r.lon}&destination=${i.lat},${i.lon}" target="_blank" rel="noopener">Ruta al fuego</a></li>`).join('')
        : `<li><span class="r-det">Ninguno registrado en OpenStreetMap ${cat === 'agua' ? 'a 10 km' : cat === 'vias' ? 'a 5 km' : `a ${nf0.format(c.radio_km)} km`}</span></li>`;
      return `<div class="recurso-grupo"><h4><span class="punto-cat" style="background:${conf.color}">${conf.letra}</span>${esc(conf.titulo)}</h4><ul>${filas}</ul></div>`;
    }).join('');
    caja.innerHTML = `<h3>Recursos cercanos</h3>${notaOsm}<div class="recursos">${grupos}</div>
      <p class="nota">Datos de OpenStreetMap: la cobertura rural puede ser incompleta. Verifica con el COER o la municipalidad.</p>`;
    actualizarCompartir(i);
  }

  // ------------------------------------------------------------------------------------
  // EONET y GIBS
  // ------------------------------------------------------------------------------------
  async function cargarEonet() {
    capas.eonet.clearLayers();
    try {
      const r = await getJSON('/api/eonet');
      for (const ev of r.eventos) {
        L.marker([ev.lat, ev.lon], { pane: 'recursos', icon: L.divIcon({ className: '', html: '<span class="marcador-eonet"></span>', iconSize: [16, 16], iconAnchor: [8, 8] }) })
          .bindPopup(`<b>${esc(ev.titulo)}</b><br>${esc(fechaLima(ev.fecha))}${ev.magnitud ? `<br>${esc(ev.magnitud)} ${esc(ev.unidad || '')}` : ''}${ev.fuentes.length ? `<br><a href="${esc(ev.fuentes[0])}" target="_blank" rel="noopener">Fuente</a>` : ''}`)
          .addTo(capas.eonet);
      }
      toast(r.eventos.length ? `${r.eventos.length} eventos EONET en la zona` : 'EONET no reporta eventos en la zona');
    } catch (e) { toast(`EONET no disponible: ${e.message}`); }
  }

  // ------------------------------------------------------------------------------------
  // Controles
  // ------------------------------------------------------------------------------------
  function aplicarFiltros() { renderLista(); dibujarTodo(); }

  $$('.segmentado button').forEach((b) => b.addEventListener('click', () => {
    state.rango = b.dataset.rango;
    $$('.segmentado button').forEach((x) => x.setAttribute('aria-checked', String(x === b)));
    cargar();
  }));
  $('#btn-actualizar').addEventListener('click', () => cargar());
  $('#f-buscar').addEventListener('input', (e) => { state.filtros.q = e.target.value; aplicarFiltros(); });
  $('#f-dep').addEventListener('change', (e) => {
    state.filtros.dep = e.target.value; aplicarFiltros();
    const pts = incendiosFiltrados().map((i) => [i.lat, i.lon]);
    if (e.target.value && pts.length) mapa.fitBounds(L.latLngBounds(pts).pad(0.25), { maxZoom: 10 });
  });
  $('#f-nivel').addEventListener('change', (e) => { state.filtros.nivelMin = Number(e.target.value); aplicarFiltros(); });
  $('#f-estado').addEventListener('change', (e) => { state.filtros.estado = e.target.value; aplicarFiltros(); });
  $('#f-orden').addEventListener('change', (e) => { state.filtros.orden = e.target.value; renderLista(); });
  $('#f-confianza').addEventListener('change', (e) => { state.confianza = e.target.value; cargar(); });
  $('#f-solo-peru').addEventListener('change', (e) => { state.soloPeru = e.target.checked; cargar(); });

  $('#opciones-base').addEventListener('change', (e) => { if (e.target.name === 'base') cambiarBase(e.target.value); });
  $('#fecha-nasa').value = state.fechaNasa;
  $('#fecha-nasa').max = state.fechaNasa;
  $('#fecha-nasa').addEventListener('change', (e) => {
    state.fechaNasa = e.target.value || hoyLima();
    if (state.base.startsWith('nasa')) cambiarBase(state.base);
    if (capas.gibs) { mapa.removeLayer(capas.gibs); capas.gibs = crearGibs().addTo(mapa); }
  });
  ['c-incendios', 'c-focos', 'c-viento', 'c-prop', 'c-aire'].forEach((id) => $(`#${id}`).addEventListener('change', () => { dibujarTodo(); }));
  $('#c-limites').addEventListener('change', (e) => { if (e.target.checked) capas.limites.addTo(mapa); else mapa.removeLayer(capas.limites); });
  $('#c-gibs').addEventListener('change', (e) => {
    if (e.target.checked) { capas.gibs = crearGibs().addTo(mapa); toast(`Anomalías térmicas NASA del ${state.fechaNasa}`); }
    else if (capas.gibs) { mapa.removeLayer(capas.gibs); capas.gibs = null; }
  });
  $('#c-eonet').addEventListener('change', (e) => {
    if (e.target.checked) { capas.eonet.addTo(mapa); cargarEonet(); } else mapa.removeLayer(capas.eonet);
  });

  const auto = $('#auto-actualizar');
  auto.checked = leerLocal('vigia-auto') === '1';
  function programarAuto() {
    clearInterval(state.timerAuto);
    if (auto.checked) state.timerAuto = setInterval(cargar, CONFIG.autoMin * 60000);
  }
  auto.addEventListener('change', () => { guardarLocal('vigia-auto', auto.checked ? '1' : '0'); programarAuto(); });

  $('#modal-cerrar').addEventListener('click', () => { $('#modal').hidden = true; });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!$('#modal').hidden) $('#modal').hidden = true;
    else if (state.sel) cerrarDetalle();
  });

  // ------------------------------------------------------------------------------------
  // Contáctenos (enlaces configurados con CONTACTO_* en .env)
  // ------------------------------------------------------------------------------------
  const ICONOS_CONTACTO = {
    red: '<path d="M8.5 11.5a3.5 3.5 0 0 0 5 0l2.5-2.5a3.5 3.5 0 0 0-5-5l-1 1M11.5 8.5a3.5 3.5 0 0 0-5 0L4 11a3.5 3.5 0 0 0 5 5l1-1"/>',
    whatsapp: '<path d="M10 17a7 7 0 1 0-6-3.4L3 17l3.5-1A7 7 0 0 0 10 17z"/><path d="M7.5 7.5c0 2.8 2.2 5 5 5l.8-1.4-1.6-.8-.8.8a3.5 3.5 0 0 1-2-2l.8-.8-.8-1.6z" fill="currentColor"/>',
    correo: '<rect x="3" y="5" width="14" height="10" rx="1.5"/><path d="M3.5 6l6.5 5 6.5-5"/>',
    web: '<circle cx="10" cy="10" r="7"/><path d="M3 10h14M10 3c2 2 2.8 4.5 2.8 7S12 15 10 17c-2-2-2.8-4.5-2.8-7S8 5 10 3z"/>',
  };
  function renderContacto(c) {
    if (!c || !c.enlaces || !c.enlaces.length) return;
    $('#contacto-texto').textContent = c.texto || 'Escríbenos o síguenos para enviar comentarios, reportar un problema o recibir novedades.';
    $('#contacto-enlaces').innerHTML = c.enlaces.map((e) => `<a href="${esc(e.url)}" target="_blank" rel="noopener">
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONOS_CONTACTO[e.tipo] || ICONOS_CONTACTO.red}</svg>
      <span><b>${esc(e.nombre)}</b><small>${esc(e.detalle)}</small></span></a>`).join('');
    $('#contacto').hidden = false;
  }

  // ------------------------------------------------------------------------------------
  // Arranque
  // ------------------------------------------------------------------------------------
  async function iniciar() {
    try {
      const est = await getJSON('/api/estado');
      Object.keys(est.fuentes).forEach((k) => state.fuentes.add(k));
      $('#opciones-fuentes').innerHTML = Object.entries(est.fuentes)
        .map(([k, n]) => `<label><input type="checkbox" value="${esc(k)}" checked> ${esc(n)}</label>`).join('');
      $('#opciones-fuentes').addEventListener('change', (e) => {
        if (e.target.checked) state.fuentes.add(e.target.value); else state.fuentes.delete(e.target.value);
        if (!state.fuentes.size) { e.target.checked = true; state.fuentes.add(e.target.value); toast('Deja al menos un satélite activo'); return; }
        cargar();
      });
      $('#glosario-niveles').textContent = est.niveles
        .map((n) => `${n.texto}: ${n.frp_desde > 0 ? `desde ${nf0.format(n.frp_desde)} MW` : 'menos de 10 MW'}`).join(' · ')
        + '. Umbrales de referencia de esta herramienta, no oficiales.';
      state.demo = est.demo;
      renderContacto(est.contacto);
      if (est.carto_key) { state.cartoKey = est.carto_key; cambiarBase(state.base); }
    } catch (e) {
      mostrarAvisos([`No se pudo contactar al servidor: ${e.message}`], true);
    }
    programarAuto();
    renderLeyenda();
    await cargar();
  }
  iniciar();
})();
