/* DOM integration checks with mocked Leaflet and API responses; no upstream downloads. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'static/map.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'static/map.js'), 'utf8');
const point = (properties = {}) => ({type: 'Feature', geometry: {type: 'Point', coordinates: [27, -13]}, properties: {instrument: 'VIIRS', satellite: 'SNPP', frp: 12.5, ...properties}});
const collection = (...features) => ({type: 'FeatureCollection', features});
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup(t, responses = {}, {mobile = false, noLeaflet = false} = {}) {
  const dom = new JSDOM(html, {url: 'http://localhost/map', runScripts: 'outside-only'});
  const {window} = dom;
  t.after(() => window.close());
  const $ = id => window.document.getElementById(id);
  const calls = [], layers = [], mapCalls = [];
  window.matchMedia = () => ({matches: mobile, addEventListener() {}});
  window.ResizeObserver = class {observe() {}};
  const map = {
    setView(...args) {mapCalls.push(['setView', ...args]); return this;},
    fitBounds(...args) {mapCalls.push(['fitBounds', ...args]); return this;},
    getCenter() {return {lat: -13, lng: 27};}, getZoom() {return 6;},
    getBounds() {return {getWest: () => 22, getSouth: () => -18, getEast: () => 33, getNorth: () => -8};},
    on() {return this;}, invalidateSize() {}, removeLayer(layer) {layer.removed = true;}
  };
  function layer(kind, options = {}) {
    const listeners = {};
    const value = {kind, options, on(name, fn) {listeners[name] = fn; return this;},
      addTo() {if (kind === 'tile') queueMicrotask(() => listeners.load?.()); return this;},
      emit(name) {listeners[name]?.();}, setOpacity(opacity) {this.opacity = opacity;},
      getLatLng() {return {lat: -13, lng: 27};}, bindPopup(panel) {this.popup = panel;}, openPopup() {this.open = true;}
    };
    layers.push(value); return value;
  }
  window.L = {
    map: () => map, control: {zoom: () => layer('control'), scale: () => layer('control')},
    tileLayer: (url, options) => Object.assign(layer('tile', options), {url}),
    rectangle: () => layer('rectangle'), circleMarker: () => layer('marker'),
    heatLayer: (points, options) => Object.assign(layer('heat', options), {points}),
    geoJSON(data, options) {const value = layer('geojson', options); const features = data.features.map(feature => {const child = layer('feature'); options.onEachFeature(feature, child); return child;}); value.getLayers = () => features; return value;}
  };
  if (noLeaflet) delete window.L;
  window.fetch = async url => {
    calls.push(String(url)); const pathname = new URL(url, 'http://localhost').pathname;
    const response = responses[pathname];
    if (response instanceof Error) throw response;
    const data = typeof response === 'function' ? await response() : response;
    return {ok: !data?.error, status: data?.error ? 502 : 200, json: async () => data?.error ? {detail: data.error} : data || (pathname.endsWith('/style') ? {entries: [], opacity: .8} : collection(point()))};
  };
  window.eval(script);
  const change = (id, value) => {if (typeof value === 'boolean') $(id).checked = value; else $(id).value = value; $(id).dispatchEvent(new window.Event('change', {bubbles: true}));};
  const submit = async () => {
    if (!$('activity-panel').hidden) $('edit-settings').click();
    for (let i = 0; i < 2 && $('layers-step').hidden; i++) $('next-step').click();
    if ($('layers-step').hidden) return;
    $('controls').dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
    await flush(); await flush();
  };
  return {window, $, calls, layers, mapCalls, change, submit};
}

test('HTML has unique IDs and valid label/control references', () => {
  const dom = new JSDOM(html); const document = dom.window.document;
  const ids = [...document.querySelectorAll('[id]')].map(node => node.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const node of document.querySelectorAll('[for],[aria-controls],[aria-labelledby]')) {
    for (const attribute of ['for', 'aria-controls', 'aria-labelledby']) {
      for (const id of (node.getAttribute(attribute) || '').split(' ').filter(Boolean)) assert.ok(document.getElementById(id), `${attribute}=${id}`);
    }
  }
  const header = document.querySelector('.workspace-header');
  assert.equal(document.querySelectorAll('.workspace-header').length, 1);
  assert.equal(header.parentElement, document.body);
  assert.equal(document.getElementById('workspace').querySelector('.workspace-header'), null);
  dom.window.close();
});

test('first visit makes no observation requests and clearly shows an unloaded state', async t => {
  const {calls, $} = setup(t); await flush();
  assert.deepEqual(calls, ['/api/land-cover/style']);
  assert.equal($('export').disabled, true);
  assert.equal($('detection-count').textContent, '—');
  assert.equal($('activity-panel').hidden, true);
  assert.equal($('area-step').hidden, false);
  assert.equal($('dates-step').hidden, true);
  assert.equal($('layers-step').hidden, true);
  assert.equal($('workspace-tabs').hidden, true);
  assert.equal($('export').hidden, true);
  assert.equal($('map-legend').hidden, true);
  assert.equal($('load').hidden, true);
  assert.equal($('workspace').querySelector('.welcome-panel, .metrics'), null);
});

test('invalid date order and coordinates do not request or clear observations', async t => {
  const {change, submit, calls, $} = setup(t);
  change('start', '2025-08-01'); await submit();
  assert.match($('status').textContent, /end date/);
  change('start', '2025-07-01'); change('bbox', '21,,33,-8'); await submit();
  assert.match($('status').textContent, /four coordinates/);
  change('bbox', '33,-18,21,-8'); await submit();
  assert.match($('status').textContent, /west must be less/);
  assert.equal(calls.filter(url => url.startsWith('/api/fires')).length, 0);
});

test('fire results update metrics, safe popups, result navigation, and export availability', async t => {
  const {submit, $, calls, layers, mapCalls} = setup(t, {'/api/fires': collection(point({satellite: '<img src=x onerror=alert(1)>'}), point({frp: 38}))});
  await submit();
  assert.equal($('detection-count').textContent, '2');
  assert.equal($('max-frp').textContent, '38');
  assert.equal($('period-days').textContent, '15');
  assert.equal($('results-list').children.length, 2);
  assert.equal($('results-list').querySelector('img'), null);
  assert.equal(layers.find(layer => layer.kind === 'feature').popup.querySelector('img'), null);
  assert.equal($('export').disabled, false);
  assert.ok(calls.some(url => url.includes('start=2025-07-01')));
  $('results-list').firstChild.click();
  assert.equal(layers.find(layer => layer.kind === 'feature').open, true);
  assert.equal(mapCalls.at(-1)[0], 'setView');
});

test('event mode uses its own endpoint and FRP fields; empty results show zero', async t => {
  const {change, submit, $} = setup(t, {'/api/fire-events': collection(point({max_frp: 80})), '/api/fires': collection()});
  change('fire-display', 'events'); await submit();
  assert.equal($('count-label').textContent, 'Fire events');
  assert.equal($('max-frp').textContent, '80');
  assert.equal($('event-note').hidden, false);
  change('fire-display', 'fires'); await submit();
  assert.equal($('detection-count').textContent, '0');
  assert.equal($('max-frp').textContent, '—');
  assert.match($('results-description').textContent, /No observations returned/);
});

test('a failed environmental request does not block successful fire data', async t => {
  const {change, submit, $} = setup(t, {'/api/land-cover/selection': {error: 'Land-cover source unavailable'}});
  change('show-land-cover', true); await submit();
  assert.equal($('detection-count').textContent, '1');
  assert.equal($('export').disabled, false);
  assert.equal($('status').dataset.state, 'error');
  assert.match($('status').textContent, /Land-cover source unavailable/);
  assert.equal($('load').disabled, false);
});

test('precipitation uses the selected end date, reports status, and changes only heat opacity', async t => {
  const {change, submit, $, window, calls} = setup(t, {'/api/precipitation/heatmap': collection(point({intensity: .3}))});
  change('show-precip-heatmap', true); change('fire-display', 'none'); await submit();
  assert.ok(calls.some(url => url.includes('observation_date=2025-07-15')));
  assert.match($('status').textContent, /Precipitation: 1 observations/);
  $('precip-heatmap-opacity').value = '20';
  $('precip-heatmap-opacity').dispatchEvent(new window.Event('input', {bubbles: true}));
  assert.equal($('map').style.getPropertyValue('--precip-opacity'), '0.2');
  assert.equal($('precip-heatmap-opacity-value').textContent, '20%');
});

test('failed reload clears stale metrics and disables export', async t => {
  let failed = false;
  const {submit, $} = setup(t, {'/api/fires': () => failed ? {error: 'FIRMS is unavailable'} : collection(point())});
  await submit(); assert.equal($('detection-count').textContent, '1');
  failed = true; await submit();
  assert.equal($('detection-count').textContent, '—');
  assert.equal($('export').disabled, true);
  assert.match($('status').textContent, /FIRMS is unavailable/);
});

test('turning every layer off removes old overlays and reports the empty selection', async t => {
  const {change, submit, $, layers} = setup(t);
  await submit(); change('fire-display', 'none'); await submit();
  assert.equal(layers.filter(layer => layer.kind === 'geojson' && !layer.removed).length, 0);
  assert.equal($('export').disabled, true);
  assert.equal(layers.find(layer => layer.kind === 'geojson').removed, true);
  assert.match($('status').textContent, /No layers selected/);
});

test('tile errors stay visible after Leaflet emits load', async t => {
  const {change, submit, $, layers} = setup(t);
  change('show-burned-area', true); await submit();
  const layer = layers.find(layer => layer.url?.includes('/api/burned-area'));
  layer.emit('tileerror'); layer.emit('load');
  assert.equal($('status').dataset.state, 'error');
  assert.match($('status').textContent, /some tiles failed/);
});

test('mobile controls are inaccessible when closed and support Escape', t => {
  const {$, window} = setup(t, {}, {mobile: true});
  assert.equal($('sidebar').inert, true);
  $('menu-toggle').click();
  assert.equal($('sidebar').inert, false);
  assert.equal($('menu-toggle').getAttribute('aria-expanded'), 'true');
  window.document.dispatchEvent(new window.KeyboardEvent('keydown', {key: 'Escape'}));
  assert.equal($('sidebar').inert, true);
  assert.equal($('sidebar-backdrop').hidden, true);
});

test('missing Leaflet produces an actionable error', t => {
  const {$} = setup(t, {}, {noLeaflet: true});
  assert.equal($('load').disabled, true);
  assert.equal($('next-step').disabled, true);
  assert.match($('status').textContent, /map library could not load/);
});

test('guided setup reveals one decision at a time without loading data', async t => {
  const {$, calls} = setup(t); await flush();
  assert.equal($('dates-step-link').disabled, true);
  $('next-step').click();
  assert.equal($('area-step').hidden, true);
  assert.equal($('dates-step').hidden, false);
  assert.equal($('layers-step').hidden, true);
  assert.equal($('dates-step-link').getAttribute('aria-current'), 'step');
  $('next-step').click();
  assert.equal($('dates-step').hidden, true);
  assert.equal($('layers-step').hidden, false);
  assert.equal($('load').hidden, false);
  assert.equal($('next-step').hidden, true);
  assert.equal($('layers-step').querySelector('.context-settings').open, false);
  assert.deepEqual(calls, ['/api/land-cover/style']);
});

test('going back retains the chosen area, dates, and layers', t => {
  const {$, change} = setup(t);
  $('use-map').click();
  const bbox = $('bbox').value;
  $('next-step').click();
  change('start', '2025-06-01');
  change('end', '2025-06-07');
  $('next-step').click();
  change('show-land-cover', true);
  $('previous-step').click();
  assert.equal($('start').value, '2025-06-01');
  assert.equal($('end').value, '2025-06-07');
  $('previous-step').click();
  assert.equal($('bbox').value, bbox);
  assert.equal($('region').value, 'custom');
  $('layers-step-link').click();
  assert.equal($('show-land-cover').checked, true);
});

test('Enter on the first step advances the guide instead of fetching observations', async t => {
  const {window, $, calls} = setup(t);
  $('controls').dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
  await flush();
  assert.equal($('dates-step').hidden, false);
  assert.equal(calls.filter(url => url.startsWith('/api/fires')).length, 0);
});

test('results and export appear after loading and settings remain editable', async t => {
  const {$, submit} = setup(t);
  await submit();
  assert.equal($('workspace-tabs').hidden, false);
  assert.equal($('activity-panel').hidden, false);
  assert.equal($('explore-panel').hidden, true);
  assert.equal($('export').hidden, false);
  assert.equal($('map-legend').hidden, false);
  assert.equal($('edit-settings').hidden, false);
  $('edit-settings').click();
  assert.equal($('full-settings-heading').hidden, false);
  assert.equal($('setup-intro').hidden, true);
  assert.equal($('setup-steps').hidden, true);
  assert.equal($('area-step').hidden, false);
  assert.equal($('dates-step').hidden, false);
  assert.equal($('layers-step').hidden, false);
  assert.equal($('explore-panel').hidden, false);
  assert.equal($('load-label').textContent, 'Update map');
});

test('full map settings remain available through the Map settings tab after loading', async t => {
  const {$, submit} = setup(t);
  await submit();
  $('explore-tab').click();
  assert.equal($('area-step').hidden, false);
  assert.equal($('dates-step').hidden, false);
  assert.equal($('layers-step').hidden, false);
  assert.equal($('next-step').hidden, true);
  assert.equal($('previous-step').hidden, true);
  assert.equal($('load').hidden, false);
});

test('exploring without a fire layer still leaves every setting available', async t => {
  const {$, change, submit} = setup(t);
  change('fire-display', 'none');
  await submit();
  assert.equal($('full-settings-heading').hidden, false);
  assert.equal($('area-step').hidden, false);
  assert.equal($('dates-step').hidden, false);
  assert.equal($('layers-step').hidden, false);
  assert.equal($('load').hidden, false);
});

test('the mobile Get started action opens the current setup step', t => {
  const {$, window} = setup(t, {}, {mobile: true});
  $('start-setup').click();
  assert.equal($('sidebar').inert, false);
  assert.equal(window.document.activeElement, $('step-title'));
  $('next-step').click();
  $('sidebar-backdrop').click();
  $('start-setup').click();
  assert.equal($('dates-step').hidden, false);
});
