/* SLIM monitoring workspace. Data remains on the existing same-origin API. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const form = $('controls');
  const defaultBbox = '21.9,-18.1,33.8,-8.2';
  let busy = false;
  let loaded = null;
  let generation = 0;
  let palette = [];
  let setupStep = 0;
  let unlockedStep = 0;
  let currentPanel = 'explore';
  let explored = false;
  let fullControls = false;
  const guidance = [
    {title: 'Where would you like to explore?', description: 'Start with Zambia, or use the map to choose your own area.', next: 'Continue to dates', hint: 'Next, choose when to look for fire activity.'},
    {title: 'When would you like to look?', description: 'Choose a period to see the fire observations recorded during that time.', next: 'Continue to layers', hint: 'Next, choose what to show on the map.'},
    {title: 'What would you like to see?', description: 'Begin with fire detections. Add landscape context whenever you need it.', hint: 'Your map is ready. Load your selected observations.'}
  ];
  const overlays = new Map();
  const layerStates = new Map();
  const number = value => Number(value).toLocaleString(undefined, {maximumFractionDigits: 1});
  const setText = (id, text) => { $(id).textContent = text; };
  const dateLabel = value => new Date(value + 'T00:00:00Z').toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC'});

  function setStatus(message, state = 'ready') {
    setText('status', message);
    $('map-status').hidden = false;
    $('map-status').dataset.state = state;
  }
  function refreshActions() {
    const settings = currentPanel === 'explore';
    $('next-step').hidden = !settings || fullControls || setupStep === 2;
    $('load').hidden = !settings || (!fullControls && setupStep !== 2);
    $('edit-settings').hidden = settings;
    $('previous-step').hidden = !settings || fullControls || setupStep === 0;
    setText('load-label', busy ? 'Loading observations…' : explored ? 'Update map' : 'Explore map');
  }
  function enableFullControls(focus = false) {
    fullControls = true;
    unlockedStep = 2;
    $('setup-intro').hidden = true;
    $('setup-steps').hidden = true;
    $('setup-summary').hidden = true;
    $('full-settings-heading').hidden = false;
    for (const section of document.querySelectorAll('[data-step]')) section.hidden = false;
    refreshActions();
    if (focus) $('full-settings-heading').querySelector('h2').focus();
  }
  function showStep(index, focus = false) {
    if (fullControls) {
      enableFullControls(focus);
      return;
    }
    setupStep = index;
    unlockedStep = Math.max(unlockedStep, index);
    for (const section of document.querySelectorAll('[data-step]')) section.hidden = Number(section.dataset.step) !== index;
    for (const button of document.querySelectorAll('[data-go-step]')) {
      const step = Number(button.dataset.goStep);
      button.disabled = busy || step > unlockedStep;
      if (step === index) button.setAttribute('aria-current', 'step');
      else button.removeAttribute('aria-current');
      button.classList.toggle('complete', step < index);
    }
    const guide = guidance[index];
    setText('step-caption', `STEP ${index + 1} OF 3`);
    setText('step-title', guide.title);
    setText('step-description', guide.description);
    setText('form-hint', guide.hint);
    if (index < 2) setText('next-step-label', guide.next);
    if (index > 0) setText('previous-step', index === 1 ? 'Back to area' : 'Back to dates');
    if (index === 2) setText('setup-summary', `${$('region').value === 'zambia' ? 'Zambia' : 'Custom area'} · ${dateLabel($('start').value)} – ${dateLabel($('end').value)}`);
    refreshActions();
    $('explore-panel').scrollTop = 0;
    if (focus) $('step-title').focus();
  }
  function validateDates() {
    if (!$('start').value || !$('end').value || $('start').value > $('end').value) throw new Error('Choose an end date on or after the start date.');
  }
  function advanceStep() {
    if (busy) return;
    try {
      if (setupStep === 0) readBounds();
      else validateDates();
      showStep(Math.min(2, setupStep + 1), true);
    } catch (error) {
      setText('form-hint', error.message);
      setStatus(error.message, 'error');
      if (setupStep === 0) {
        $('bbox').closest('details').open = true;
        $('bbox').focus();
      } else $('end').focus();
    }
  }
  $('next-step').addEventListener('click', advanceStep);
  $('previous-step').addEventListener('click', () => showStep(Math.max(0, setupStep - 1), true));
  for (const button of document.querySelectorAll('[data-go-step]')) button.addEventListener('click', () => showStep(Number(button.dataset.goStep), true));
  $('edit-settings').addEventListener('click', () => { enableFullControls(); setPanel('explore'); $('full-settings-heading').querySelector('h2').focus(); });
  showStep(0);
  function setPanel(name) {
    currentPanel = name;
    for (const panel of ['explore', 'activity']) {
      const active = panel === name;
      $(panel + '-panel').hidden = !active;
      $(panel + '-tab').classList.toggle('active', active);
      $(panel + '-tab').setAttribute('aria-selected', String(active));
      $(panel + '-tab').tabIndex = active ? 0 : -1;
    }
    refreshActions();
    if (name === 'activity') setText('form-hint', 'Select an observation to find it on the map.');
    else setText('form-hint', fullControls ? 'Change any setting, then update the map.' : guidance[setupStep].hint);
  }
  for (const name of ['explore', 'activity']) {
    $(name + '-tab').addEventListener('click', () => setPanel(name));
    $(name + '-tab').addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 'explore' : event.key === 'End' ? 'activity' : name === 'explore' ? 'activity' : 'explore';
      setPanel(next);
      $(next + '-tab').focus();
    });
  }
  function sidebar(open, returnFocus = false) {
    $('sidebar').classList.toggle('open', open);
    $('sidebar-backdrop').hidden = !open;
    $('menu-toggle').setAttribute('aria-expanded', String(open));
    $('sidebar').inert = matchMedia('(max-width: 900px)').matches && !open;
    if (open) {
      if ($('workspace-tabs').hidden) $('step-title').focus();
      else $(currentPanel + '-tab').focus();
    }
    else if (returnFocus) $('menu-toggle').focus();
  }
  $('menu-toggle').addEventListener('click', () => sidebar(!$('sidebar').classList.contains('open')));
  $('start-setup').addEventListener('click', () => { setPanel('explore'); sidebar(true); });
  $('sidebar-backdrop').addEventListener('click', () => sidebar(false, true));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && $('sidebar').classList.contains('open')) sidebar(false, true);
    if (event.key === 'Tab' && $('sidebar').classList.contains('open') && matchMedia('(max-width: 900px)').matches) {
      const focusable = [...$('sidebar').querySelectorAll('a,button,input,select,summary')].filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  matchMedia('(max-width: 900px)').addEventListener('change', () => sidebar(false));
  sidebar(false);
  $('help').addEventListener('click', () => $('help-dialog').showModal());
  $('close-help').addEventListener('click', () => $('help-dialog').close());
  $('help-dialog').addEventListener('click', event => { if (event.target === $('help-dialog')) { const r = event.target.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) event.target.close(); } });

  if (!window.L) {
    setStatus('The map library could not load. Check your connection and refresh the page.', 'error');
    for (const id of ['load', 'next-step', 'fit-area', 'use-map']) $(id).disabled = true;
    return;
  }
  const map = L.map('map', {preferCanvas: true, zoomControl: false, minZoom: 2, worldCopyJump: true}).setView([-13.2, 27.8], 6);
  L.control.zoom({position: 'topright'}).addTo(map);
  L.control.scale({position: 'bottomleft', imperial: false}).addTo(map);
  const base = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
  base.on('tileerror', () => { if (!busy && !loaded) setStatus('Some basemap tiles could not load. Check your connection or try zooming out.', 'error'); });
  // Leaflet.heat 0.2.0 has no opacity setter or custom-pane support.
  // Control only its canvas, leaving the fire renderer fully opaque.
  $('map').style.setProperty('--precip-opacity', '.5');
  let areaOutline;
  let selectedBounds;
  function readBounds() {
    const parts = $('bbox').value.split(',');
    const values = parts.map(value => value.trim() === '' ? NaN : Number(value));
    if (values.length !== 4 || values.some(value => !Number.isFinite(value))) throw new Error('Enter four coordinates: west, south, east, north.');
    const [w, s, e, n] = values;
    if (w < -180 || e > 180 || s < -90 || n > 90 || w >= e || s >= n) throw new Error('Use valid coordinates: west must be less than east, and south less than north.');
    return [[s, w], [n, e]];
  }
  function updateArea(fit = false) {
    selectedBounds = readBounds();
    if (areaOutline) map.removeLayer(areaOutline);
    areaOutline = L.rectangle(selectedBounds, {color: '#536f55', weight: 1, dashArray: '5 6', fill: false, interactive: false}).addTo(map);
    setText('area-label', $('region').value === 'zambia' ? 'Zambia' : 'Custom area');
    if (fit) map.fitBounds(selectedBounds, {padding: [40, 40]});
  }
  updateArea(true);
  new ResizeObserver(() => map.invalidateSize()).observe($('map'));
  function dirty() { if (!busy) setText('form-hint', fullControls ? 'Settings changed. Update the map to apply.' : setupStep === 2 ? 'Settings changed. Load your map to apply.' : guidance[setupStep].hint); }
  form.addEventListener('input', dirty);
  form.addEventListener('change', dirty);
  $('region').addEventListener('change', () => {
    if ($('region').value === 'zambia') { $('bbox').value = defaultBbox; updateArea(true); }
    else $('use-map').click();
  });
  $('use-map').addEventListener('click', () => {
    const bounds = map.getBounds();
    const values = [Math.max(-180, bounds.getWest()), Math.max(-90, bounds.getSouth()), Math.min(180, bounds.getEast()), Math.min(90, bounds.getNorth())];
    $('bbox').value = values.map(value => value.toFixed(4)).join(',');
    $('region').value = 'custom';
    try { updateArea(); dirty(); } catch (error) { setStatus(error.message, 'error'); }
  });
  $('bbox').addEventListener('change', () => { $('region').value = $('bbox').value === defaultBbox ? 'zambia' : 'custom'; try { updateArea(true); } catch (error) { setStatus(error.message, 'error'); } });
  $('fit-area').addEventListener('click', () => { try { updateArea(true); } catch (error) { setStatus(error.message, 'error'); } });
  for (const button of document.querySelectorAll('[data-days]')) {
    button.addEventListener('click', () => {
      const end = new Date();
      const start = new Date(end);
      start.setDate(start.getDate() - Number(button.dataset.days) + 1);
      const localDate = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
      $('start').value = localDate(start); $('end').value = localDate(end);
      for (const preset of document.querySelectorAll('[data-days]')) preset.classList.toggle('active', preset === button);
      dirty();
    });
  }
  for (const id of ['start', 'end']) $(id).addEventListener('input', () => { for (const preset of document.querySelectorAll('[data-days]')) preset.classList.remove('active'); });
  const toggles = ['show-burned-area', 'show-land-cover', 'show-fuel', 'show-precip-heatmap'];
  function selectionChanged() {
    const fire = $('fire-display').value !== 'none';
    $('fire-card').classList.toggle('selected', fire);
    $('fire-dot').hidden = !fire;
    $('sensor').disabled = !fire || busy;
    $('event-note').hidden = $('fire-display').value !== 'events';
    for (const id of toggles) {
      const input = $(id);
      input.closest('.layer-card').classList.toggle('selected', input.checked);
      const panel = input.getAttribute('aria-controls');
      if (panel) { $(panel).hidden = !input.checked; input.setAttribute('aria-expanded', String(input.checked)); }
    }
    setText('selected-count', `${Number(fire) + toggles.filter(id => $(id).checked).length} selected`);
  }
  for (const id of [...toggles, 'fire-display']) $(id).addEventListener('change', selectionChanged);
  for (const [id, key] of [['land-cover-opacity', 'land'], ['fuel-opacity', 'fuel'], ['precip-heatmap-opacity', 'rain']]) {
    $(id).addEventListener('input', event => {
      event.stopPropagation();
      setText(id + '-value', $(id).value + '%');
      if (key === 'rain') $('map').style.setProperty('--precip-opacity', Number($(id).value) / 100);
      else overlays.get(key)?.setOpacity(Number($(id).value) / 100);
    });
  }
  async function json(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 180000);
    try {
      const response = await fetch(url, {signal: controller.signal});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (HTTP ${response.status}). Check your area and dates.`);
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The data request timed out. Try a smaller area or shorter period.');
      if (error instanceof SyntaxError) throw new Error('The server returned an unreadable response. Try again.');
      throw error;
    } finally { clearTimeout(timer); }
  }
  json('/api/land-cover/style').then(style => { palette = style.entries || []; renderLegend(); }).catch(() => { /* The palette is optional; map requests report their own errors. */ });

  function refreshStatus() {
    const states = [...layerStates.values()];
    const failures = states.some(state => state.error);
    const pending = states.some(state => state.pending);
    setStatus(states.map(state => state.message).join(' · ') || 'No layers selected. Choose a data layer to begin.', failures ? 'error' : pending ? 'loading' : 'ready');
  }
  function state(key, message, options = {}) { layerStates.set(key, {message, ...options}); refreshStatus(); }
  function tiles(key, name, url, options, run) {
    const layer = L.tileLayer(url, {maxZoom: 19, zIndex: {land: 210, fuel: 220, burned: 230}[key], ...options});
    let failed = false;
    overlays.set(key, layer);
    state(key, `${name} loading`, {pending: true});
    layer.on('loading', () => { if (run === generation) { failed = false; state(key, `${name} loading`, {pending: true}); } });
    layer.on('tileerror', () => { if (run === generation) { failed = true; state(key, `${name}: some tiles failed`, {error: true}); } });
    layer.on('load', () => { if (run === generation && !failed) state(key, `${name} loaded`); });
    layer.addTo(map);
    return layer;
  }
  const popupLabels = {fire_id: 'Event ID', interval_start: 'Interval start', interval_end: 'Interval end', start_time: 'First observation', end_time: 'Last observation', detections: 'Detections', max_frp: 'Maximum FRP (MW)', cluster_area_ha: 'Cluster area (ha)', acq_datetime_utc: 'Observed (UTC)', satellite: 'Satellite', instrument: 'Instrument', confidence: 'Confidence', frp: 'FRP (MW)', firms_source: 'Source'};
  function popup(feature, layer) {
    const panel = document.createElement('div');
    const title = document.createElement('h3'); title.className = 'popup-title'; title.textContent = feature.geometry.type === 'Point' ? 'Fire observation' : 'Fire event'; panel.append(title);
    for (const [key, name] of Object.entries(popupLabels)) {
      const value = feature.properties?.[key]; if (value === undefined || value === null) continue;
      const row = document.createElement('div'); row.className = 'popup-row';
      const label = document.createElement('strong'); label.textContent = name;
      const text = document.createElement('span'); text.textContent = String(value);
      row.append(label, text); panel.append(row);
    }
    layer.bindPopup(panel);
  }
  function renderResults(data, fireLayer, mode) {
    const features = data.features;
    const isEvents = mode === 'events';
    setText('detection-count', number(features.length));
    setText('results-badge', number(features.length));
    setText('count-label', isEvents ? 'Fire events' : 'Fire detections');
    const powers = features.map(feature => feature.properties?.[isEvents ? 'max_frp' : 'frp']).filter(value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value))).map(Number);
    setText('max-frp', powers.length ? number(powers.reduce((a, b) => Math.max(a, b), -Infinity)) : '—');
    setText('results-description', features.length ? `${number(features.length)} ${isEvents ? 'event polygons' : 'detections'}. Select an observation to locate it on the map.${features.length > 100 ? ' Showing the first 100; export for all records.' : ''}` : 'No observations returned for this area, period, and sensor. Try a different selection.');
    $('results-list').replaceChildren();
    const layers = fireLayer.getLayers();
    features.slice(0, 100).forEach((feature, index) => {
      const p = feature.properties || {};
      const button = document.createElement('button'); button.type = 'button'; button.className = 'result-item';
      const description = document.createElement('span');
      const title = document.createElement('strong'); title.textContent = isEvents ? `Event ${p.fire_id ?? index + 1}` : `${p.instrument || 'Fire'} · ${p.satellite || 'Satellite observation'}`;
      const date = document.createElement('small'); date.textContent = String(p.acq_datetime_utc || p.start_time || p.interval_start || 'Time unavailable');
      const power = document.createElement('span'); const value = p[isEvents ? 'max_frp' : 'frp']; power.textContent = value != null && Number.isFinite(Number(value)) ? `${number(value)} MW` : 'View ↗';
      description.append(title, date); button.append(description, power);
      button.addEventListener('click', () => { const layer = layers[index]; if (layer.getLatLng) map.setView(layer.getLatLng(), Math.max(map.getZoom(), 10)); else map.fitBounds(layer.getBounds(), {padding: [50, 50], maxZoom: 12}); layer.openPopup(); sidebar(false); });
      $('results-list').append(button);
    });
  }
  function renderLegend() {
    $('map-legend').hidden = overlays.size === 0;
    const container = $('legend-content'); container.replaceChildren();
    function row(color, label) { const line = document.createElement('div'); line.className = 'legend-row'; const swatch = document.createElement('span'); swatch.className = 'legend-swatch'; swatch.style.backgroundColor = color; const text = document.createElement('span'); text.textContent = label; line.append(swatch, text); container.append(line); }
    if (overlays.has('fire')) row('#e58149', loaded?.mode === 'events' ? 'Fire event polygons' : 'Fire detections');
    if (overlays.has('burned')) { const p = document.createElement('p'); p.textContent = 'Burned area · GWIS source colors'; container.append(p); }
    if (overlays.has('fuel')) { const p = document.createElement('p'); p.textContent = 'Vegetation fuel · GWIS source colors'; container.append(p); }
    if (overlays.has('land')) {
      const title = document.createElement('p'); title.textContent = 'SLIM land-cover classes'; container.append(title);
      palette.filter(entry => entry.alpha > 0).forEach(entry => row(entry.color, entry.label));
      if (!palette.length) { const p = document.createElement('p'); p.textContent = 'Class legend unavailable.'; container.append(p); }
    }
    if (overlays.has('rain')) { const p = document.createElement('p'); p.textContent = 'Rainfall intensity'; const gradient = document.createElement('div'); gradient.className = 'legend-gradient'; const labels = document.createElement('p'); labels.textContent = '0 mm → 200+ mm'; container.append(p, gradient, labels); }
    if (!overlays.size) container.textContent = 'Load layers to see their legend.';
  }
  function resetResults() {
    loaded = null; $('export').disabled = true; $('export').hidden = true;
    setText('detection-count', '—'); setText('max-frp', '—'); setText('results-badge', '0');
    setText('results-description', 'No fire observations loaded for this selection.'); $('results-list').replaceChildren();
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return;
    if (setupStep < 2) { advanceStep(); return; }
    let bounds;
    try {
      bounds = readBounds();
    } catch (error) {
      setPanel('explore'); showStep(0);
      setStatus(error.message, 'error'); setText('form-hint', error.message);
      $('bbox').closest('details').open = true;
      if (matchMedia('(max-width: 900px)').matches) sidebar(true);
      $('bbox').focus();
      return;
    }
    try {
      validateDates();
    } catch (error) {
      setPanel('explore'); showStep(1);
      setStatus(error.message, 'error'); setText('form-hint', error.message);
      if (matchMedia('(max-width: 900px)').matches) sidebar(true);
      $('end').focus();
      return;
    }
    enableFullControls();
    busy = true;
    const run = ++generation;
    const query = {bbox: $('bbox').value, start: $('start').value, end: $('end').value, sensor: $('sensor').value};
    const mode = $('fire-display').value;
    for (const el of form.querySelectorAll('input,select,button')) el.disabled = true;
    $('load').setAttribute('aria-busy', 'true');
    setText('load-label', 'Loading observations…'); setText('form-hint', 'Connecting to your selected data sources…');
    sidebar(false);
    for (const layer of overlays.values()) map.removeLayer(layer);
    overlays.clear(); layerStates.clear(); resetResults(); renderLegend();
    map.fitBounds(bounds, {padding: [40, 40]});
    setText('period-days', Math.round((Date.parse(query.end) - Date.parse(query.start)) / 86400000) + 1);
    setText('loaded-period', `${dateLabel(query.start)} – ${dateLabel(query.end)}`);
    const tasks = [];
    const task = (key, name, action) => {
      state(key, `${name} loading`, {pending: true});
      tasks.push(Promise.resolve().then(action).catch(error => { state(key, `${name}: ${error.message}`, {error: true}); }).finally(() => { renderLegend(); refreshStatus(); }));
    };
    if (mode !== 'none') task('fire', 'Fire activity', async () => {
      const data = await json((mode === 'events' ? '/api/fire-events?' : '/api/fires?') + new URLSearchParams(query));
      if (!Array.isArray(data.features)) throw new Error('No valid observation collection was returned.');
      const layer = L.geoJSON(data, {style: {color: '#b96539', weight: 1.5, fillColor: '#ec965e', fillOpacity: .28}, pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {radius: 4, color: '#a55730', weight: .8, fillColor: '#ed9453', fillOpacity: .85}), onEachFeature: popup}).addTo(map);
      overlays.set('fire', layer); loaded = {data, ...query, mode};
      renderResults(data, layer, mode); $('export').disabled = false; $('export').hidden = false;
      explored = true;
      $('workspace-tabs').hidden = false;
      enableFullControls();
      state('fire', `${number(data.features.length)} ${mode === 'events' ? 'event polygons' : 'fire detections'}`);
    });
    if ($('show-burned-area').checked) task('burned', 'Burned area', () => tiles('burned', 'Burned area', '/api/burned-area/tiles/{z}/{x}/{y}.png?' + new URLSearchParams({start: query.start, end: query.end}), {opacity: .7, attribution: 'Burned area © European Commission JRC / GWIS'}, run));
    if ($('show-land-cover').checked) task('land', 'Land cover', async () => {
      const selection = await json('/api/land-cover/selection?' + new URLSearchParams({start: query.start, end: query.end}));
      tiles('land', `Land cover ${selection.year} (${selection.resolution_m} m)`, `/api/land-cover/tiles/${selection.year}/{z}/{x}/{y}.png`, {opacity: Number($('land-cover-opacity').value) / 100, attribution: `SLIM land cover ${selection.year}`}, run);
    });
    if ($('show-fuel').checked) task('fuel', 'Vegetation fuel', () => tiles('fuel', 'Vegetation fuel', '/api/fuel/tiles/{z}/{x}/{y}.png?fuel_layer=fuel_map', {opacity: Number($('fuel-opacity').value) / 100, attribution: 'Fuel data © European Commission JRC / GWIS'}, run));
    if ($('show-precip-heatmap').checked) task('rain', 'Precipitation', async () => {
      if (!L.heatLayer) throw new Error('The heatmap library could not load. Refresh the page to retry.');
      const data = await json('/api/precipitation/heatmap?' + new URLSearchParams({bbox: query.bbox, time_window: $('precip-heatmap-window').value, observation_date: query.end}));
      const points = data.features.map(feature => [feature.geometry.coordinates[1], feature.geometry.coordinates[0], feature.properties.intensity]);
      if (points.length) {
        $('map').style.setProperty('--precip-opacity', Number($('precip-heatmap-opacity').value) / 100);
        const layer = L.heatLayer(points, {radius: 35, blur: 35, maxZoom: 18, gradient: {0: '#60a5fa', .25: '#22d3ee', .5: '#4ade80', .75: '#fde047', 1: '#f97316'}}).addTo(map);
        overlays.set('rain', layer);
      }
      state('rain', points.length ? `Precipitation: ${number(points.length)} observations` : 'No precipitation observations returned');
    });
    refreshStatus();
    try { await Promise.allSettled(tasks); }
    finally {
      busy = false;
      if (overlays.size || loaded) $('start-setup').hidden = true;
      for (const el of form.querySelectorAll('input,select,button')) el.disabled = false;
      $('load').setAttribute('aria-busy', 'false');
      showStep(setupStep);
      if (loaded) setPanel('activity');
      else setPanel('explore');
      const errors = [...layerStates.values()].some(item => item.error);
      setText('form-hint', errors ? 'Some data could not load. See the map status.' : loaded ? loaded.data.features.length ? 'Select an observation to find it on the map.' : 'No observations returned. Adjust your area or dates.' : tasks.length ? 'Map updated. Adjust your selection to explore.' : 'Select at least one layer to explore.');
      selectionChanged(); renderLegend(); refreshStatus();
    }
  });
  form.addEventListener('invalid', event => { setPanel('explore'); showStep(event.target.id === 'bbox' ? 0 : 1); if (event.target.id === 'bbox') event.target.closest('details').open = true; if (matchMedia('(max-width: 900px)').matches) sidebar(true); }, true);
  $('export').addEventListener('click', () => {
    if (!loaded) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(loaded.data, null, 2)], {type: 'application/geo+json'}));
    const link = document.createElement('a'); link.href = url; link.download = `slim-${loaded.mode}-${loaded.start}-${loaded.end}.geojson`;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
