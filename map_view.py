"""Self-contained browser map for the SLIM Fire API."""

MAP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SLIM Fire Map</title>
  <link rel="stylesheet"
        href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
        crossorigin="">
  <style>
    html, body { height: 100%; margin: 0; font: 14px system-ui, sans-serif; }
    body { display: grid; grid-template-rows: auto 1fr; }
    form { display: flex; flex-wrap: wrap; gap: .6rem; align-items: end;
           padding: .7rem; background: #18231d; color: white; }
    label { display: grid; gap: .2rem; font-size: .8rem; }
    input, select, button { min-height: 2rem; box-sizing: border-box; }
    input { width: 12rem; }
    input[type=date] { width: 9rem; }
    input[type=checkbox] { width: auto; min-height: auto; }
    label.toggle { display: flex; flex-direction: row; align-items: center;
                   gap: .4rem; padding-bottom: .45rem; font-size: .9rem; }
    label.opacity { display: flex; flex-direction: row; align-items: center;
                    gap: .4rem; padding-bottom: .25rem; font-size: .9rem; }
    input[type=range] { width: 8rem; min-height: auto; }
    button { padding: 0 1rem; cursor: pointer; }
    button:disabled { cursor: wait; opacity: .65; }
    #status { padding-bottom: .45rem; }
    #map { min-height: 20rem; }
    @media (max-width: 700px) { input { width: 10rem; } }
  </style>
</head>
<body>
  <form id="controls">
    <label>Bounding box
      <input id="bbox" value="21.9,-18.1,33.8,-8.2" required>
    </label>
    <label>Start
      <input id="start" type="date" value="2025-07-01" required>
    </label>
    <label>End
      <input id="end" type="date" value="2025-07-15" required>
    </label>
    <label>Sensor
      <select id="sensor">
        <option value="all">All</option>
        <option value="viirs_snpp">VIIRS S-NPP</option>
        <option value="viirs_noaa20">VIIRS NOAA-20</option>
        <option value="viirs_noaa21">VIIRS NOAA-21</option>
        <option value="modis">MODIS</option>
      </select>
    </label>
    <label>Fire layer
      <select id="fire-display">
        <option value="fires">Individual detections</option>
        <option value="events">15-day event polygons</option>
        <option value="none">None</option>
      </select>
    </label>
    <label class="toggle">
      <input id="show-burned-area" type="checkbox">
      Burned area (GWIS)
    </label>
    <label class="toggle">
      <input id="show-land-cover" type="checkbox">
      Land cover
    </label>
    <label class="opacity">LC opacity
      <input id="land-cover-opacity" type="range" min="0" max="100" value="80">
      <span id="land-cover-opacity-value">80%</span>
    </label>
    <label class="toggle">
      <input id="show-fuel" type="checkbox">
      Fuel data (GWIS)
    </label>
    <label>Fuel layer
      <select id="fuel-layer">
        <option value="fuel_map">Global Fuel Map</option>
      </select>
    </label>
    <label class="opacity">Fuel opacity
      <input id="fuel-opacity" type="range" min="0" max="100" value="70">
      <span id="fuel-opacity-value">70%</span>
    </label>
    <label class="toggle">
      <input id="show-precip-heatmap" type="checkbox">
      Precipitation heatmap
    </label>
    <label>Precip time window
      <select id="precip-heatmap-window">
        <option value="1h">Last 1 hour</option>
        <option value="6h">Last 6 hours</option>
        <option value="24h" selected>Last 24 hours</option>
        <option value="72h">Last 72 hours</option>
        <option value="7d">Last 7 days</option>
        <option value="14d">Last 14 days</option>
      </select>
    </label>
    <label class="opacity">Heatmap opacity
      <input id="precip-heatmap-opacity" type="range" min="0" max="100" value="50">
      <span id="precip-heatmap-opacity-value">50%</span>
    </label>
    <button id="load" type="submit">Load layers</button>
    <span id="status" role="status">Ready</span>
  </form>
  <div id="map"></div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
          integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
          crossorigin=""></script>
  <script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
  <script>
    const map = L.map('map', {preferCanvas: true}).setView([-13.2, 27.8], 6);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);
    let fireLayer = L.geoJSON(null, {
      style: {
        color: '#991b1b', weight: 2, fillColor: '#f97316', fillOpacity: .28
      },
      pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
        radius: 4, color: '#7f1d1d', weight: 1, fillColor: '#ef3b2c', fillOpacity: .75
      }),
      onEachFeature: (feature, layer) => {
        const properties = feature.properties || {};
        const panel = document.createElement('div');
        for (const key of [
          'fire_id', 'interval_start', 'interval_end', 'start_time', 'end_time',
          'detections', 'max_frp', 'cluster_area_ha', 'acq_datetime_utc',
          'satellite', 'instrument', 'confidence', 'frp', 'firms_source'
        ]) {
          if (properties[key] === undefined || properties[key] === null) continue;
          const row = document.createElement('div');
          const label = document.createElement('strong');
          label.textContent = key + ': ';
          row.append(label, document.createTextNode(String(properties[key])));
          panel.appendChild(row);
        }
        layer.bindPopup(panel);
      }
    }).addTo(map);
    let burnedAreaLayer = null;
    let landCoverLayer = null;
    let fuelLayer = null;
    let heatmapLayer = null;
    const layerControl = L.control.layers(null, {
      'Fire detections / events': fireLayer
    }).addTo(map);

    const form = document.querySelector('#controls');
    const button = document.querySelector('#load');
    const status = document.querySelector('#status');
    const landCoverOpacity = document.querySelector('#land-cover-opacity');
    const landCoverOpacityValue = document.querySelector('#land-cover-opacity-value');
    const fuelOpacity = document.querySelector('#fuel-opacity');
    const fuelOpacityValue = document.querySelector('#fuel-opacity-value');
    const heatmapOpacity = document.querySelector('#precip-heatmap-opacity');
    const heatmapOpacityValue = document.querySelector('#precip-heatmap-opacity-value');

    landCoverOpacity.addEventListener('input', () => {
      landCoverOpacityValue.textContent = `${landCoverOpacity.value}%`;
      if (landCoverLayer) landCoverLayer.setOpacity(Number(landCoverOpacity.value) / 100);
    });
    fuelOpacity.addEventListener('input', () => {
      fuelOpacityValue.textContent = `${fuelOpacity.value}%`;
      if (fuelLayer) fuelLayer.setOpacity(Number(fuelOpacity.value) / 100);
    });
    heatmapOpacity.addEventListener('input', () => {
      heatmapOpacityValue.textContent = `${heatmapOpacity.value}%`;
      if (heatmapLayer) heatmapLayer.setOpacity(Number(heatmapOpacity.value) / 100);
    });
    fetch('/api/land-cover/style')
      .then(response => response.ok ? response.json() : null)
      .then(style => {
        if (!style) return;
        landCoverOpacity.value = Math.round(style.opacity * 100);
        landCoverOpacityValue.textContent = `${landCoverOpacity.value}%`;
      });

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      button.disabled = true;
      let fireStatus = '';
      let burnedStatus = '';
      let landCoverStatus = '';
      let fuelStatus = '';
      const updateStatus = () => {
        status.textContent = [fireStatus, burnedStatus, landCoverStatus, fuelStatus]
          .filter(Boolean).join(' · ') || 'No layers selected';
      };
      const params = new URLSearchParams({
        bbox: document.querySelector('#bbox').value,
        start: document.querySelector('#start').value,
        end: document.querySelector('#end').value,
        sensor: document.querySelector('#sensor').value
      });
      const fireDisplay = document.querySelector('#fire-display').value;
      const showBurnedArea = document.querySelector('#show-burned-area').checked;
      const showLandCover = document.querySelector('#show-land-cover').checked;
      const showFuel = document.querySelector('#show-fuel').checked;
      const showHeatmap = document.querySelector('#show-precip-heatmap').checked;
      const heatmapWindow = document.querySelector('#precip-heatmap-window').value;
      const fuelLayerType = document.querySelector('#fuel-layer').value;
      const endpoint = fireDisplay === 'events' ? '/api/fire-events?' : '/api/fires?';
      try {
        const values = document.querySelector('#bbox').value.split(',').map(Number);
        if (values.length !== 4 || values.some(value => !Number.isFinite(value))) {
          throw new Error('Bounding box must contain four numbers');
        }
        if (burnedAreaLayer) {
          layerControl.removeLayer(burnedAreaLayer);
          map.removeLayer(burnedAreaLayer);
          burnedAreaLayer = null;
        }
        if (landCoverLayer) {
          layerControl.removeLayer(landCoverLayer);
          map.removeLayer(landCoverLayer);
          landCoverLayer = null;
        }
        if (fuelLayer) {
          layerControl.removeLayer(fuelLayer);
          map.removeLayer(fuelLayer);
          fuelLayer = null;
        }
        if (heatmapLayer) {
          layerControl.removeLayer(heatmapLayer);
          map.removeLayer(heatmapLayer);
          heatmapLayer = null;
        }
        if (showBurnedArea) {
          burnedStatus = 'GWIS burned area loading';
          updateStatus();
          const tileParams = new URLSearchParams({
            start: document.querySelector('#start').value,
            end: document.querySelector('#end').value
          });
          const newBurnedAreaLayer = L.tileLayer(
            '/api/burned-area/tiles/{z}/{x}/{y}.png?' + tileParams,
            {
              opacity: .7,
              maxZoom: 19,
              attribution: 'Burned area &copy; European Commission JRC / GWIS'
            }
          );
          burnedAreaLayer = newBurnedAreaLayer;
          layerControl.addOverlay(newBurnedAreaLayer, 'GWIS burned area');
          newBurnedAreaLayer.on('load', () => {
            if (burnedAreaLayer !== newBurnedAreaLayer) return;
            burnedStatus = 'GWIS burned area loaded';
            updateStatus();
          });
          newBurnedAreaLayer.on('tileerror', () => {
            if (burnedAreaLayer !== newBurnedAreaLayer) return;
            burnedStatus = 'Some GWIS burned-area tiles failed';
            updateStatus();
          });
          newBurnedAreaLayer.addTo(map);
        }
        if (showLandCover) {
          landCoverStatus = 'Land cover selection loading';
          updateStatus();
          const selectionParams = new URLSearchParams({
            start: document.querySelector('#start').value,
            end: document.querySelector('#end').value
          });
          const selectionResponse = await fetch('/api/land-cover/selection?' + selectionParams);
          const selection = await selectionResponse.json();
          if (!selectionResponse.ok) {
            throw new Error(selection.detail || `HTTP ${selectionResponse.status}`);
          }
          const newLandCoverLayer = L.tileLayer(
            `/api/land-cover/tiles/${selection.year}/{z}/{x}/{y}.png`,
            {
              opacity: Number(landCoverOpacity.value) / 100,
              maxZoom: 19,
              attribution: `SLIM land cover ${selection.year} (${selection.resolution_m} m)`
            }
          );
          landCoverLayer = newLandCoverLayer;
          layerControl.addOverlay(
            newLandCoverLayer,
            `SLIM land cover ${selection.year} (${selection.resolution_m} m)`
          );
          newLandCoverLayer.on('load', () => {
            if (landCoverLayer !== newLandCoverLayer) return;
            landCoverStatus = `Land cover ${selection.year} (${selection.resolution_m} m) loaded`;
            updateStatus();
          });
          newLandCoverLayer.on('tileerror', () => {
            if (landCoverLayer !== newLandCoverLayer) return;
            landCoverStatus = `Land cover ${selection.year} tile failed`;
            updateStatus();
          });
          newLandCoverLayer.addTo(map);
          landCoverStatus = `Land cover ${selection.year} (${selection.resolution_m} m) loading`;
          updateStatus();
        }
        if (showFuel) {
          fuelStatus = 'GWIS fuel data loading';
          updateStatus();
          const newFuelLayer = L.tileLayer(
            `/api/fuel/tiles/{z}/{x}/{y}.png?fuel_layer=${encodeURIComponent(fuelLayerType)}`,
            {
              opacity: Number(fuelOpacity.value) / 100,
              maxZoom: 19,
              attribution: 'Fuel data &copy; European Commission JRC / GWIS'
            }
          );
          fuelLayer = newFuelLayer;
          layerControl.addOverlay(newFuelLayer, `GWIS fuel data (${fuelLayerType})`);
          newFuelLayer.on('load', () => {
            if (fuelLayer !== newFuelLayer) return;
            fuelStatus = `GWIS fuel data (${fuelLayerType}) loaded`;
            updateStatus();
          });
          newFuelLayer.on('tileerror', () => {
            if (fuelLayer !== newFuelLayer) return;
            fuelStatus = `Some GWIS fuel-data tiles failed`;
            updateStatus();
          });
          newFuelLayer.addTo(map);
        }
        
        if (showHeatmap) {
          let heatmapStatus = 'Loading precipitation heatmap...';
          updateStatus();
          
          const heatmapParams = new URLSearchParams({
            bbox: document.querySelector('#bbox').value,
            time_window: heatmapWindow
          });
          
          try {
            const response = await fetch('/api/precipitation/heatmap?' + heatmapParams);
            const geojsonData = await response.json();
            
            if (!response.ok) {
              throw new Error(geojsonData.detail || `HTTP ${response.status}`);
            }
            
            // Extract intensity values for heatmap
            const heatmapPoints = geojsonData.features.map(feature => [
              feature.geometry.coordinates[1],  // latitude
              feature.geometry.coordinates[0],  // longitude
              feature.properties.intensity      // intensity (0-1)
            ]);
            
            if (heatmapPoints.length > 0) {
              const newHeatmapLayer = L.heatLayer(heatmapPoints, {
                radius: 35,
                blur: 35,
                maxZoom: 18,
                gradient: {0.0: '#0000ff', 0.25: '#00ffff', 0.5: '#00ff00', 0.75: '#ffff00', 1.0: '#ff0000'},
                opacity: Number(heatmapOpacity.value) / 100
              });
              
              heatmapLayer = newHeatmapLayer;
              layerControl.addOverlay(newHeatmapLayer, `Precipitation heatmap (${heatmapWindow})`);
              newHeatmapLayer.addTo(map);
              heatmapStatus = `Precipitation heatmap (${heatmapWindow}) loaded with ${heatmapPoints.length} points`;
            } else {
              heatmapStatus = 'No precipitation data available';
            }
          } catch (error) {
            heatmapStatus = 'Heatmap error: ' + error.message;
          }
          updateStatus();
        }

        fireLayer.clearLayers();
        if (fireDisplay === 'none') {
          map.removeLayer(fireLayer);
          map.fitBounds([[values[1], values[0]], [values[3], values[2]]]);
          updateStatus();
          return;
        }
        if (!map.hasLayer(fireLayer)) fireLayer.addTo(map);
        fireStatus = 'FIRMS fire layer loading';
        updateStatus();
        const response = await fetch(endpoint + params);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        fireLayer.clearLayers().addData(data);
        const count = data.features.length;
        const itemName = fireDisplay === 'events' ? 'event polygons' : 'detections';
        fireStatus = `${count.toLocaleString()} ${itemName}`;
        updateStatus();
        if (count) {
          map.fitBounds(fireLayer.getBounds(), {padding: [20, 20]});
        } else {
          map.fitBounds([[values[1], values[0]], [values[3], values[2]]]);
        }
      } catch (error) {
        fireStatus = 'Error: ' + error.message;
        updateStatus();
      } finally {
        button.disabled = false;
      }
    });
  </script>
</body>
</html>
"""
