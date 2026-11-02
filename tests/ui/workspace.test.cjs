/* DOM integration checks with mocked Leaflet and API responses; no upstream downloads. */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const root = path.resolve(__dirname, "../..");
const html = fs.readFileSync(path.join(root, "static/map.html"), "utf8");
const script = fs.readFileSync(path.join(root, "static/map.js"), "utf8");
const point = (properties = {}) => ({
  type: "Feature",
  geometry: { type: "Point", coordinates: [27, -13] },
  properties: {
    instrument: "VIIRS",
    satellite: "SNPP",
    frp: 12.5,
    ...properties,
  },
});
const collection = (...features) => ({ type: "FeatureCollection", features });
const flush = () => new Promise((resolve) => setImmediate(resolve));
const yearMonths = (year) => {
  const now = new Date();
  const last = now.getFullYear() === year ? now.getMonth() + 1 : 12;
  return Array.from(
    { length: last },
    (_, index) => `${year}-${String(index + 1).padStart(2, "0")}`,
  );
};
const lastMonth2026 = () => {
  const months = yearMonths(2026);
  return months[months.length - 1];
};
const monthEnd = (period) => {
  const [year, month] = period.split("-").map(Number);
  const day = String(new Date(year, month, 0).getDate()).padStart(2, "0");
  return `${period}-${day}`;
};
const utcToday = () => new Date().toISOString().slice(0, 10);
const rollingStart = (days) => {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - days + 1);
  return date.toISOString().slice(0, 10);
};

test("district labels are safe and stay separate from fire results", async (t) => {
  const { submit, $, layers, calls } = setup(t, {
    "/api/districts": collection({
      type: "Feature",
      properties: { DISTRICT: "<img src=x>", PROVINCE: "Eastern" },
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [27, -13],
            [28, -13],
            [28, -14],
            [27, -13],
          ],
        ],
      },
    }),
  });
  await submit();
  const district = layers.find((layer) => layer.tooltip);
  assert.equal(
    district.tooltip.querySelector("strong").textContent,
    "<img src=x>",
  );
  assert.equal(district.tooltip.querySelector("small").textContent, "Eastern");
  assert.equal(district.tooltip.querySelector("img"), null);
  // Districts show details on hover only; the click popup is removed.
  assert.equal(district.popup, undefined);
  assert.equal($("detection-count").textContent, "1");
  assert.ok(calls.includes("/api/districts"));
  assert.ok(layers.some((layer) => layer.sentToBack));
});

test("hazard keeps its annual reference and opacity when observation dates change", async (t) => {
  const { submit, change, $, layers, window } = setup(
    t,
    {},
    { scenario: "prevention" },
  );
  change("start", "2026-01-01");
  change("end", "2026-02-01");
  await submit();
  const hazard = layers.find((layer) =>
    layer.url?.includes("/api/hazard/tiles"),
  );
  assert.ok(hazard);
  assert.equal(hazard.url.includes("?"), false);
  assert.match($("legend-content").textContent, /Hazard index · 2025/);
  $("hazard-opacity").value = "35";
  $("hazard-opacity").dispatchEvent(
    new window.Event("input", { bubbles: true }),
  );
  assert.equal(hazard.opacity, 0.35);
  hazard.emit("tileerror");
  hazard.emit("load");
  assert.equal($("status").dataset.state, "error");
});

test("district source failure leaves hazard usable and reports the missing reference", async (t) => {
  const { submit, $, layers } = setup(
    t,
    { "/api/districts": { error: "District source unavailable" } },
    { scenario: "prevention" },
  );
  await submit();
  assert.ok(layers.some((layer) => layer.url?.includes("/api/hazard/tiles")));
  assert.match($("status").textContent, /District source unavailable/);
  assert.equal($("load").disabled, false);
  assert.equal($("export").disabled, true);
});

function setup(
  t,
  responses = {},
  { mobile = false, noLeaflet = false, scenario = "" } = {},
) {
  const dom = new JSDOM(html, {
    url: "http://localhost/map" + (scenario ? "?scenario=" + scenario : ""),
    runScripts: "outside-only",
  });
  const { window } = dom;
  t.after(() => window.close());
  const $ = (id) => window.document.getElementById(id);
  const calls = [],
    layers = [],
    mapCalls = [];
  window.matchMedia = () => ({ matches: mobile, addEventListener() {} });
  window.ResizeObserver = class {
    observe() {}
  };
  const map = {
    createPane() {
      return { style: {} };
    },
    setView(...args) {
      mapCalls.push(["setView", ...args]);
      return this;
    },
    fitBounds(...args) {
      mapCalls.push(["fitBounds", ...args]);
      return this;
    },
    getCenter() {
      return { lat: -13, lng: 27 };
    },
    getZoom() {
      return 6;
    },
    getBounds() {
      return {
        getWest: () => 22,
        getSouth: () => -18,
        getEast: () => 33,
        getNorth: () => -8,
      };
    },
    on() {
      return this;
    },
    invalidateSize() {},
    removeLayer(layer) {
      layer.removed = true;
    },
  };
  function layer(kind, options = {}) {
    const listeners = {};
    const value = {
      kind,
      options,
      on(name, fn) {
        listeners[name] = fn;
        return this;
      },
      addTo() {
        if (kind === "tile" || kind === "image")
          queueMicrotask(() => listeners.load?.());
        return this;
      },
      emit(name) {
        listeners[name]?.();
      },
      setOpacity(opacity) {
        this.opacity = opacity;
      },
      bringToBack() {
        this.sentToBack = true;
        return this;
      },
      getLatLng() {
        return { lat: -13, lng: 27 };
      },
      bindTooltip(panel) {
        this.tooltip = panel;
      },
      bindPopup(panel) {
        this.popup = panel;
      },
      openPopup() {
        this.open = true;
      },
    };
    layers.push(value);
    return value;
  }
  window.L = {
    map: () => map,
    control: { zoom: () => layer("control"), scale: () => layer("control") },
    tileLayer: (url, options) => Object.assign(layer("tile", options), { url }),
    imageOverlay: (url, bounds, options) =>
      Object.assign(layer("image", options), { url, bounds }),
    rectangle: () => layer("rectangle"),
    circleMarker: (latlng, options) =>
      Object.assign(layer("marker", options), { latlng }),
    heatLayer: (points, options) =>
      Object.assign(layer("heat", options), { points }),
    geoJSON(data, options) {
      const value = layer("geojson", options);
      const source = data.type === "FeatureCollection" ? data.features : [data];
      const features = source.map((feature) => {
        if (feature.geometry?.type === "Point") {
          const pointLayer = options.pointToLayer?.(feature, {
            lat: feature.geometry.coordinates[1],
            lng: feature.geometry.coordinates[0],
          });
          const style =
            typeof options.style === "function"
              ? options.style(feature)
              : options.style;
          if (pointLayer?.options && style)
            Object.assign(pointLayer.options, style);
        }
        const child = layer("feature");
        options.onEachFeature?.(feature, child);
        return child;
      });
      value.getLayers = () => features;
      return value;
    },
  };
  if (noLeaflet) delete window.L;
  window.fetch = async (url) => {
    calls.push(String(url));
    const pathname = new URL(url, "http://localhost").pathname;
    const response =
      responses[pathname] ??
      (pathname === "/api/districts"
        ? collection()
        : pathname === "/api/districts/summary"
          ? [
              {
                name: "Chadiza",
                province: "Eastern",
                code: 103001,
                bbox: [31.9, -14.6, 33.3, -13.9],
              },
            ]
          : pathname === "/api/hazard/style"
            ? { year: 2025, min: 0, max: 1, colors: ["#ffffcc", "#800026"] }
            : pathname === "/api/burn/style"
              ? {
                  year: 2025,
                  months: ["2025-01", "2025-06", "2025-07", "2025-12"],
                  color: "#c2410c",
                }
              : pathname === "/api/recovery/style"
                ? { year: 2025, color: "#2f8f5b", label: "SLIM recovery status" }
                : undefined);
    if (response instanceof Error) throw response;
    const data = typeof response === "function" ? await response() : response;
    return {
      ok: !data?.error,
      status: data?.error ? 502 : 200,
      json: async () =>
        data?.error
          ? { detail: data.error }
          : data ||
            (pathname.endsWith("/style")
              ? { entries: [], opacity: 0.8 }
              : collection(point())),
    };
  };
  window.eval(fs.readFileSync(path.join(root, "static/scenarios.js"), "utf8"));
  window.eval(
    fs.readFileSync(path.join(root, "static/scenario-map.js"), "utf8"),
  );
  window.eval(script);
  const change = (id, value) => {
    if (typeof value === "boolean") $(id).checked = value;
    else $(id).value = value;
    $(id).dispatchEvent(new window.Event("change", { bubbles: true }));
  };
  const submit = async () => {
    if (!$("activity-panel").hidden) $("edit-settings").click();
    for (let i = 0; i < 2 && $("layers-step").hidden; i++)
      $("next-step").click();
    if ($("layers-step").hidden) return;
    $("load").click();
    await flush();
    await flush();
  };
  return { window, $, calls, layers, mapCalls, map, change, submit };
}

for (const scenario of ["prevention", "response", "restoration"]) {
  test(`${scenario}: clicking Explore map requests districts when they are the only selected layer`, async (t) => {
    const { submit, change, $, calls } = setup(t, {}, { scenario });
    change("show-fire", false);
    for (const id of [
      "show-hazard",
      "show-burn",
      "show-land-cover",
      "show-fuel",
      "show-precip-heatmap",
    ])
      change(id, false);
    change("show-districts", true);
    await submit();
    assert.deepEqual(
      calls.filter((url) => !url.endsWith("/style")),
      ["/api/districts"],
    );
    assert.equal($("load").disabled, false);
    assert.match($("status").textContent, /district boundaries loaded/);
  });
}

test("a map preparation exception is reported and the user can retry", async (t) => {
  const { submit, $, map, calls, window } = setup(
    t,
    {},
    { scenario: "prevention" },
  );
  const fit = map.fitBounds;
  window.console.error = () => {};
  map.fitBounds = () => {
    throw new Error("Cannot position map");
  };
  await submit();
  assert.match($("status").textContent, /Cannot position map/);
  assert.equal($("status").dataset.state, "error");
  assert.equal($("load").disabled, false);
  assert.equal($("load").getAttribute("aria-busy"), "false");
  map.fitBounds = fit;
  await submit();
  assert.ok(calls.includes("/api/districts"));
});

test("HTML has unique IDs and valid label/control references", () => {
  const dom = new JSDOM(html);
  const document = dom.window.document;
  const ids = [...document.querySelectorAll("[id]")].map((node) => node.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const node of document.querySelectorAll(
    "[for],[aria-controls],[aria-labelledby]",
  )) {
    for (const attribute of ["for", "aria-controls", "aria-labelledby"]) {
      for (const id of (node.getAttribute(attribute) || "")
        .split(" ")
        .filter(Boolean))
        assert.ok(document.getElementById(id), `${attribute}=${id}`);
    }
  }
  const header = document.querySelector(".workspace-header");
  assert.equal(document.querySelectorAll(".workspace-header").length, 1);
  assert.equal(header.parentElement, document.body);
  assert.equal(
    document.getElementById("workspace").querySelector(".workspace-header"),
    null,
  );
  dom.window.close();
});

test("first visit makes no observation requests and clearly shows an unloaded state", async (t) => {
  const { calls, $ } = setup(t);
  await flush();
  assert.deepEqual(calls, ["/api/land-cover/style"]);
  assert.equal($("export").disabled, true);
  assert.equal($("detection-count").textContent, "—");
  assert.equal($("activity-panel").hidden, true);
  assert.equal($("area-step").hidden, false);
  assert.equal($("dates-step").hidden, true);
  assert.equal($("layers-step").hidden, true);
  assert.equal($("workspace-tabs").hidden, true);
  assert.equal($("export").hidden, true);
  assert.equal($("map-legend").hidden, true);
  assert.equal($("load").hidden, true);
  assert.equal($("workspace").querySelector(".welcome-panel, .metrics"), null);
});

test("invalid date order and coordinates do not request or clear observations", async (t) => {
  const { change, submit, calls, $ } = setup(
    t,
    {},
    { scenario: "restoration" },
  );
  change("start", "2025-08-01");
  await submit();
  assert.match($("status").textContent, /end date/);
  change("start", "2025-07-01");
  change("bbox", "21,,33,-8");
  await submit();
  assert.match($("status").textContent, /four coordinates/);
  change("bbox", "33,-18,21,-8");
  await submit();
  assert.match($("status").textContent, /west must be less/);
  assert.equal(calls.filter((url) => url.startsWith("/api/fires")).length, 0);
});

test("fire results update metrics, safe popups, result navigation, and export availability", async (t) => {
  const recent = new Date(Date.now() - 2 * 3_600_000).toISOString();
  const older = new Date(Date.now() - 48 * 3_600_000).toISOString();
  const { submit, $, calls, layers, mapCalls } = setup(t, {
    "/api/fires": collection(
      point({
        satellite: "<img src=x onerror=alert(1)>",
        frp: 10,
        acq_datetime_utc: older,
      }),
      point({ frp: 100, acq_datetime_utc: recent }),
    ),
  });
  await submit();
  assert.equal($("detection-count").textContent, "2");
  assert.equal($("max-frp").textContent, "100");
  assert.equal(
    $("period-days").textContent,
    "7",
  );
  assert.equal($("results-list").children.length, 2);
  assert.equal($("results-list").querySelector("img"), null);
  assert.equal(
    layers.find((layer) => layer.kind === "feature").popup.querySelector("img"),
    null,
  );
  assert.equal($("export").disabled, false);
  const fireMarkers = layers.filter((layer) => layer.kind === "marker");
  assert.equal(fireMarkers.length >= 2, true);
  assert.equal(
    fireMarkers[1].options.radius > fireMarkers[0].options.radius,
    true,
  );
  assert.match(fireMarkers[0].options.fillColor, /^#[0-9a-f]{6}$/i);
  assert.match(fireMarkers[1].options.fillColor, /^#[0-9a-f]{6}$/i);
  assert.notEqual(fireMarkers[0].options.fillColor, fireMarkers[1].options.fillColor);
  assert.match($("legend-content").textContent, /size by FRP/i);
  assert.match($("legend-content").textContent, /colour shows observation date/i);
  assert.ok(calls.some((url) => url.includes(`start=${rollingStart(7)}`)));
  $("results-list").firstChild.click();
  assert.equal(layers.find((layer) => layer.kind === "feature").open, true);
  assert.equal(mapCalls.at(-1)[0], "setView");
});

test("combined satellite timeline selects and highlights each imagery source", async (t) => {
  const observed = "2026-10-20T10:00:00Z";
  const { submit, $, calls, layers, window } = setup(t, {
    "/api/fires": collection(point({ acq_datetime_utc: observed })),
    "/api/cdse/acquisitions": {
      bbox: [26.95, -13.05, 27.05, -12.95],
      reference_datetime: observed,
      window_start: "2026-10-10T10:00:00Z",
      window_end: "2026-11-04T10:00:00Z",
      source: "all",
      acquisitions: [
        {
          id: "S2-test",
          datetime: "2026-10-18T09:00:00Z",
          platform: "sentinel-2c",
          source: "sentinel-2",
          cloud_cover: 4,
        },
        {
          id: "LC09-test",
          datetime: "2026-10-18T08:00:00Z",
          platform: "landsat-9",
          source: "landsat-8-9",
          cloud_cover: 8,
        },
      ],
    },
  });
  await submit();
  layers.find((layer) => layer.kind === "feature")?.emit("click");
  await flush();
  await flush();
  assert.ok(
    calls.some(
      (url) =>
        url.startsWith("/api/cdse/acquisitions?") &&
        new URL(url, "http://localhost").searchParams.get("source") ===
          "all",
    ),
  );
  const timelineButtons = [
    ...$("satellite-timeline").querySelectorAll("button.available"),
  ];
  assert.deepEqual(
    timelineButtons.map((button) => button.textContent),
    ["S2", "L9"],
  );
  assert.equal(timelineButtons[0].closest(".satellite-day"), timelineButtons[1].closest(".satellite-day"));
  const sentinelButton = timelineButtons.find((button) => button.textContent === "S2");
  sentinelButton.click();
  await flush();
  assert.ok(
    window.document
      .querySelector('[data-satellite-source="sentinel-2"]')
      .classList.contains("active"),
  );
  const landsatButton = timelineButtons.find((button) => button.textContent === "L9");
  landsatButton.click();
  await flush();
  const image = layers.filter((layer) => layer.kind === "image").at(-1);
  assert.equal(
    new URL(image.url, "http://localhost").searchParams.get("source"),
    "landsat-8-9",
  );
  assert.ok(
    window.document
      .querySelector('[data-satellite-source="landsat-8-9"]')
      .classList.contains("active"),
  );
  assert.match($("satellite-status").textContent, /Landsat 9/);

  const initialImage = layers.filter((layer) => layer.kind === "image").at(-1);
  const initialParams = new URL(initialImage.url, "http://localhost").searchParams;
  assert.equal(initialParams.get("width"), "768");
  assert.equal($("satellite-radius-value").textContent, "10 × 10 km");
  $("satellite-radius").value = "10";
  $("satellite-radius").dispatchEvent(new window.Event("input", { bubbles: true }));
  $("satellite-radius").dispatchEvent(new window.Event("change", { bubbles: true }));
  const expandedImage = layers.filter((layer) => layer.kind === "image").at(-1);
  const expandedParams = new URL(expandedImage.url, "http://localhost").searchParams;
  const initialBbox = initialParams.get("bbox").split(",").map(Number);
  const expandedBbox = expandedParams.get("bbox").split(",").map(Number);
  assert.equal($("satellite-radius-value").textContent, "20 × 20 km");
  assert.equal(expandedParams.get("datetime"), initialParams.get("datetime"));
  assert.equal(expandedParams.get("source"), initialParams.get("source"));
  assert.ok(expandedBbox[2] - expandedBbox[0] > initialBbox[2] - initialBbox[0]);
});

test("FRP slider filters loaded FIRMS points from highest to lowest", async (t) => {
  const { submit, $, window } = setup(t, {
    "/api/fires": collection(
      point({ frp: 5, acq_datetime_utc: "2026-10-20T08:00:00Z" }),
      point({ frp: 40, acq_datetime_utc: "2026-10-20T09:00:00Z" }),
      point({ frp: 120, acq_datetime_utc: "2026-10-20T10:00:00Z" }),
    ),
  });
  await submit();
  const slider = $("frp-threshold");
  assert.equal($("frp-filter").hidden, false);
  assert.equal(Number(slider.dataset.frpLow), 5);
  assert.equal(Number(slider.dataset.frpHigh), 120);
  assert.equal($("frp-median-label").textContent, "40 MW · median");

  slider.value = "1000";
  slider.dispatchEvent(new window.Event("input", { bubbles: true }));
  assert.equal($("detection-count").textContent, "1");
  assert.match($("frp-filter-count").textContent, /1 of 3/);
  assert.match($("legend-content").textContent, /FRP ≥ 120 MW/);

  slider.value = "0";
  slider.dispatchEvent(new window.Event("input", { bubbles: true }));
  assert.equal($("detection-count").textContent, "3");
  assert.match($("frp-filter-count").textContent, /3 of 3/);
});

test("date slider filters FIRMS points by UTC day and combines with FRP", async (t) => {
  const { submit, $, window } = setup(t, {
    "/api/fires": collection(
      point({ frp: 5, acq_datetime_utc: "2026-10-05T23:30:00Z" }),
      point({ frp: 40, acq_datetime_utc: "2026-10-15T01:00:00Z" }),
      point({ frp: 120, acq_datetime_utc: "2026-10-25T18:00:00Z" }),
    ),
  });
  await submit();
  const dateSlider = $("date-threshold");
  const frpSlider = $("frp-threshold");
  assert.equal($("date-filter").hidden, false);
  assert.match($("date-low-label").textContent, /2026.*earliest/);
  assert.match($("date-high-label").textContent, /2026.*latest/);

  dateSlider.value = "500";
  dateSlider.dispatchEvent(new window.Event("input", { bubbles: true }));
  assert.equal($("detection-count").textContent, "2");
  assert.match($("date-threshold-value").textContent, /^From .*2026$/);
  assert.match($("legend-content").textContent, /date from .*2026/);

  frpSlider.value = "1000";
  frpSlider.dispatchEvent(new window.Event("input", { bubbles: true }));
  assert.equal($("detection-count").textContent, "1");
  assert.match($("legend-content").textContent, /FRP ≥ 120 MW and date from/);

  frpSlider.value = "0";
  frpSlider.dispatchEvent(new window.Event("input", { bubbles: true }));
  assert.equal($("detection-count").textContent, "2");
});

test("event mode uses its own endpoint and FRP fields; empty results show zero", async (t) => {
  const { change, submit, $ } = setup(t, {
    "/api/fire-events": collection(point({ max_frp: 80 })),
    "/api/fires": collection(),
  });
  change("fire-display", "events");
  await submit();
  assert.equal($("count-label").textContent, "Fire events");
  assert.equal($("max-frp").textContent, "80");
  assert.equal($("event-note").hidden, false);
  change("fire-display", "fires");
  await submit();
  assert.equal($("detection-count").textContent, "0");
  assert.equal($("max-frp").textContent, "—");
  assert.match(
    $("results-description").textContent,
    /No observations returned/,
  );
});

test("a failed environmental request does not block successful fire data", async (t) => {
  const { change, submit, $ } = setup(t, {
    "/api/land-cover/selection": { error: "Land-cover source unavailable" },
  });
  change("show-land-cover", true);
  await submit();
  assert.equal($("detection-count").textContent, "1");
  assert.equal($("export").disabled, false);
  assert.equal($("status").dataset.state, "error");
  assert.match($("status").textContent, /Land-cover source unavailable/);
  assert.equal($("load").disabled, false);
});

test("precipitation uses the selected end date, reports status, and changes only heat opacity", async (t) => {
  const { change, submit, $, window, calls } = setup(t, {
    "/api/precipitation/heatmap": collection(point({ intensity: 0.3 })),
  });
  change("show-precip-heatmap", true);
  change("show-fire", false);
  await submit();
  assert.ok(
    calls.some((url) =>
      url.includes(`observation_date=${utcToday()}`),
    ),
  );
  assert.match($("status").textContent, /Precipitation: 1 observations/);
  $("precip-heatmap-opacity").value = "20";
  $("precip-heatmap-opacity").dispatchEvent(
    new window.Event("input", { bubbles: true }),
  );
  assert.equal($("map").style.getPropertyValue("--precip-opacity"), "0.2");
  assert.equal($("precip-heatmap-opacity-value").textContent, "20%");
});

test("failed reload clears stale metrics and disables export", async (t) => {
  let failed = false;
  const { submit, $ } = setup(t, {
    "/api/fires": () =>
      failed ? { error: "FIRMS is unavailable" } : collection(point()),
  });
  await submit();
  assert.equal($("detection-count").textContent, "1");
  failed = true;
  await submit();
  assert.equal($("detection-count").textContent, "—");
  assert.equal($("export").disabled, true);
  assert.match($("status").textContent, /FIRMS is unavailable/);
});

test("turning every layer off removes old overlays and reports the empty selection", async (t) => {
  const { change, submit, $, layers } = setup(t);
  await submit();
  change("show-fire", false);
  change("show-districts", false);
  change("show-burn", false);
  await submit();
  assert.equal(
    layers.filter((layer) => layer.kind === "geojson" && !layer.removed).length,
    0,
  );
  assert.equal($("export").disabled, true);
  assert.equal(layers.find((layer) => layer.kind === "geojson").removed, true);
  assert.match($("status").textContent, /No layers selected/);
});

test("tile errors stay visible after Leaflet emits load", async (t) => {
  const { change, submit, $, layers } = setup(
    t,
    {},
    { scenario: "restoration" },
  );
  change("show-burn", true);
  await submit();
  const layer = layers.find((layer) => layer.url?.includes("/api/burn/"));
  layer.emit("tileerror");
  layer.emit("load");
  assert.equal($("status").dataset.state, "error");
  assert.match($("status").textContent, /some tiles failed/);
});

test("mobile controls are inaccessible when closed and support Escape", (t) => {
  const { $, window } = setup(t, {}, { mobile: true });
  assert.equal($("sidebar").inert, true);
  $("menu-toggle").click();
  assert.equal($("sidebar").inert, false);
  assert.equal($("menu-toggle").getAttribute("aria-expanded"), "true");
  window.document.dispatchEvent(
    new window.KeyboardEvent("keydown", { key: "Escape" }),
  );
  assert.equal($("sidebar").inert, true);
  assert.equal($("sidebar-backdrop").hidden, true);
});

test("floating desktop controls collapse and resume without clearing settings or results", async (t) => {
  const { $, window, submit, change, calls, mapCalls } = setup(t);
  assert.equal($("sidebar").inert, false);
  assert.equal($("menu-toggle").getAttribute("aria-expanded"), "true");
  assert.equal($("sidebar-backdrop").hidden, true);
  const initialFit = mapCalls.find((call) => call[0] === "fitBounds");
  assert.ok(initialFit[2].paddingTopLeft[0] > 360);
  change("start", "2025-07-02");
  $("next-step").click();
  $("menu-toggle").click();
  assert.equal($("sidebar").inert, true);
  assert.equal(window.document.activeElement, $("menu-toggle"));
  assert.equal($("menu-toggle").getAttribute("aria-expanded"), "false");
  $("menu-toggle").click();
  assert.equal($("dates-step").hidden, false);
  assert.equal($("start").value, "2025-07-02");
  await submit();
  assert.equal($("sidebar").inert, false);
  const requestCount = calls.length;
  const resultCount = $("detection-count").textContent;
  $("menu-toggle").click();
  $("menu-toggle").click();
  assert.equal($("activity-panel").hidden, false);
  assert.equal($("detection-count").textContent, resultCount);
  assert.equal(calls.length, requestCount);
});

test("missing Leaflet produces an actionable error", (t) => {
  const { $ } = setup(t, {}, { noLeaflet: true });
  assert.equal($("load").disabled, true);
  assert.equal($("next-step").disabled, true);
  assert.match($("status").textContent, /map library could not load/);
});

test("guided setup reveals one decision at a time without loading data", async (t) => {
  const { $, calls } = setup(t);
  await flush();
  assert.equal($("dates-step-link").disabled, true);
  $("next-step").click();
  assert.equal($("area-step").hidden, true);
  assert.equal($("dates-step").hidden, false);
  assert.equal($("layers-step").hidden, true);
  assert.equal($("dates-step-link").getAttribute("aria-current"), "step");
  $("next-step").click();
  assert.equal($("dates-step").hidden, true);
  assert.equal($("layers-step").hidden, false);
  assert.equal($("load").hidden, false);
  assert.equal($("next-step").hidden, true);
  // The default workspace enables a context layer, so the panel is open.
  assert.equal($("layers-step").querySelector(".context-settings").open, true);
  assert.deepEqual(calls, ["/api/land-cover/style"]);
});

test("going back retains the chosen area, dates, and layers", (t) => {
  const { $, change } = setup(t);
  $("use-map").click();
  const bbox = $("bbox").value;
  $("next-step").click();
  change("start", "2025-06-01");
  change("end", "2025-06-07");
  $("next-step").click();
  change("show-land-cover", true);
  $("previous-step").click();
  assert.equal($("start").value, "2025-06-01");
  assert.equal($("end").value, "2025-06-07");
  $("previous-step").click();
  assert.equal($("bbox").value, bbox);
  assert.equal($("region").value, "custom");
  $("layers-step-link").click();
  assert.equal($("show-land-cover").checked, true);
});

test("Enter on the first step advances the guide instead of fetching observations", async (t) => {
  const { window, $, calls } = setup(t);
  $("controls").dispatchEvent(
    new window.Event("submit", { bubbles: true, cancelable: true }),
  );
  await flush();
  assert.equal($("dates-step").hidden, false);
  assert.equal(calls.filter((url) => url.startsWith("/api/fires")).length, 0);
});

test("results and export appear after loading and settings remain editable", async (t) => {
  const { $, submit } = setup(t);
  await submit();
  assert.equal($("workspace-tabs").hidden, false);
  assert.equal($("activity-panel").hidden, false);
  assert.equal($("explore-panel").hidden, true);
  assert.equal($("export").hidden, false);
  assert.equal($("map-legend").hidden, false);
  assert.equal($("edit-settings").hidden, false);
  $("edit-settings").click();
  assert.equal($("full-settings-heading").hidden, false);
  assert.equal($("setup-intro").hidden, true);
  assert.equal($("setup-steps").hidden, true);
  assert.equal($("area-step").hidden, false);
  assert.equal($("dates-step").hidden, false);
  assert.equal($("layers-step").hidden, false);
  assert.equal($("explore-panel").hidden, false);
  assert.equal($("load-label").textContent, "Update map");
});

test("full map settings remain available through the Map settings tab after loading", async (t) => {
  const { $, submit } = setup(t);
  await submit();
  $("explore-tab").click();
  assert.equal($("area-step").hidden, false);
  assert.equal($("dates-step").hidden, false);
  assert.equal($("layers-step").hidden, false);
  assert.equal($("next-step").hidden, true);
  assert.equal($("previous-step").hidden, true);
  assert.equal($("load").hidden, false);
});

test("exploring without a fire layer still leaves every setting available", async (t) => {
  const { $, change, submit } = setup(t);
  change("show-fire", false);
  await submit();
  assert.equal($("full-settings-heading").hidden, false);
  assert.equal($("area-step").hidden, false);
  assert.equal($("dates-step").hidden, false);
  assert.equal($("layers-step").hidden, false);
  assert.equal($("load").hidden, false);
});

test("the mobile menu toggle remains available and resumes the current setup step", (t) => {
  const { $, window } = setup(t, {}, { mobile: true });
  assert.equal($("menu-window").contains($("menu-toggle")), true);
  assert.equal($("sidebar").contains($("menu-toggle")), false);
  $("menu-toggle").click();
  assert.equal($("sidebar").inert, false);
  assert.equal(window.document.activeElement, $("step-title"));
  $("next-step").click();
  $("sidebar-backdrop").click();
  $("menu-toggle").click();
  assert.equal($("dates-step").hidden, false);
});

test("landing page links to all four workspaces and labels unavailable modules", () => {
  const dom = new JSDOM(
    fs.readFileSync(path.join(root, "static/index.html"), "utf8"),
    { runScripts: "outside-only" },
  );
  dom.window.eval(
    fs.readFileSync(path.join(root, "static/scenarios.js"), "utf8"),
  );
  dom.window.eval(
    fs.readFileSync(path.join(root, "static/landing.js"), "utf8"),
  );
  const cards = [...dom.window.document.querySelectorAll(".scenario-card")];
  assert.equal(cards.length, 4);
  assert.ok(
    cards[1].querySelector('svg path[d*="M12 2v3"]'),
    "detection card uses the response beacon",
  );
  assert.deepEqual(
    cards.map((card) => card.querySelector("a").getAttribute("href")),
    [
      "/map?scenario=prevention",
      "/map?scenario=response",
      "/map?scenario=prediction",
      "/map?scenario=restoration",
    ],
  );
  assert.deepEqual(
    cards.map((card) => card.querySelectorAll(".future-modules .badge").length),
    [1, 1, 2, 1],
    "the roadmap follows the brief's scenario-specific future scope",
  );
  assert.match(cards[0].querySelector(".available").textContent, /Hazard map/);
  assert.doesNotMatch(
    cards[0].querySelector(".future-modules").textContent,
    /Hazard map/,
  );
  assert.equal(
    dom.window.document.querySelectorAll(
      ".future-modules button, .future-modules input",
    ).length,
    0,
  );
  assert.equal(dom.window.document.querySelector(".site-header"), null);
  assert.ok(
    dom.window.document.querySelector('img[src="/static/slim-logo.webp"]'),
  );
  dom.window.close();
});

for (const scenario of ["prevention", "prediction", "restoration"]) {
  test(`${scenario} cannot request FIRMS even if a stale fire selection is present`, async (t) => {
    const { submit, $, calls } = setup(
      t,
      { "/api/land-cover/selection": { year: 2024, resolution_m: 10 } },
      { scenario },
    );
    $("show-fire").checked = true;
    await submit();
    assert.equal(
      calls.some((url) => /\/api\/(fires|fire-events)/.test(url)),
      false,
    );
    assert.equal($("show-fire").checked, false);
    assert.equal($("fire-display").disabled, true);
    assert.equal($("fire-card").hidden, true);
    assert.equal($("export").hidden, true);
  });
  test(`${scenario} loads relevant context without requesting fire detections or future modules`, async (t) => {
    const { window, $, calls, submit, layers } = setup(
      t,
      { "/api/land-cover/selection": { year: 2024, resolution_m: 10 } },
      { scenario },
    );
    assert.equal($("show-fire").checked, false);
    assert.equal($("fire-card").hidden, true);
    assert.equal($("fire-display").disabled, true);
    assert.equal($("show-land-cover").checked, false);
    assert.equal($("show-hazard").checked, scenario === "prevention");
    assert.equal($("show-districts").checked, true);
    assert.equal($("show-fuel").checked, false);
    assert.equal(
      $("show-fuel").closest(".layer-card").hidden,
      !["response", "prediction"].includes(scenario),
    );
    assert.equal(
      $("show-precip-heatmap").closest(".layer-card").hidden,
      !["response", "prediction"].includes(scenario),
    );
    // Scenario 4 makes the SLIM recovery raster its primary layer.
    assert.equal($("show-burn").checked, scenario !== "prevention");
    assert.equal($("show-recovery").checked, scenario === "restoration");
    assert.equal($("show-burned-area"), null);
    assert.equal(
      $("selected-count").textContent,
      scenario === "restoration" ? "3 selected" : "2 selected",
    );
    // The environmental-context panel opens only when a layer inside it is on.
    assert.equal(
      window.document.querySelector(".context-settings").open,
      scenario !== "prevention",
    );
    assert.match(
      window.document.querySelector(".scenario-roadmap").textContent,
      /future features/i,
    );
    const navigation = window.document.querySelector(".scenario-navigation");
    assert.equal(navigation.querySelectorAll("a").length, 4);
    assert.equal(
      navigation.querySelector('[aria-current="page"]').getAttribute("href"),
      `/map?scenario=${scenario}`,
    );
    window.document.querySelector(".scenario-roadmap").open = true;
    await flush();
    assert.deepEqual(calls, ["/api/land-cover/style"]);
    await submit();
    assert.equal($("fire-display").disabled, true);
    assert.ok(calls.includes("/api/districts"));
    assert.equal(
      calls.some((url) => /fires|fire-events|propagation/.test(url)),
      false,
    );
    assert.ok(
      layers.some((layer) =>
        layer.url?.includes(
          scenario === "prevention" ? "/api/hazard/" : "/api/burn/",
        ),
      ),
    );
    assert.equal($("export").hidden, true);
    assert.equal($("area-step").hidden, false);
    // Prevention and restoration use fixed 2025 rasters and skip Dates;
    // prediction retains a selectable context period.
    assert.equal($("dates-step").hidden, scenario !== "prediction");
    assert.equal($("layers-step").hidden, false);
    assert.equal(
      window.document.querySelector(".scenario-home").getAttribute("href"),
      "/",
    );
  });
}

test("scenario 4 leads with recovery, fixes burned area to 2025, and skips Dates", async (t) => {
  const { $, submit, layers, calls, window, change } = setup(
    t,
    { "/api/land-cover/selection": { year: 2025, resolution_m: 30 } },
    { scenario: "restoration" },
  );
  // No period selection: the Dates step is skipped.
  assert.equal($("dates-step-link").hidden, true);
  assert.equal($("step-caption").textContent, "STEP 1 OF 2");
  // Recovery is the primary (first) layer and is on by default.
  assert.equal($("recovery-card").hidden, false);
  assert.equal($("show-recovery").checked, true);
  assert.equal(
    window.document
      .querySelector("#layers-step")
      .querySelector(".layer-card:not([hidden])"),
    $("recovery-card"),
  );
  // Burned area is on but grouped with the environmental context, and the
  // period selector is removed (fixed 2025 yearly) with an explicit year.
  assert.equal($("show-burn").checked, true);
  assert.equal($("burn-period"), null);
  const context = window.document.querySelector(".context-settings");
  assert.equal(context.querySelector(".layer-card"), $("burn-card"));
  assert.ok(context.querySelector("#burn-card"));
  assert.equal(
    $("burn-card").querySelector(".toggle-label span").textContent,
    "Burned area · 2025",
  );
  // Land cover is available but is no longer selected by default.
  assert.equal($("show-land-cover").checked, false);
  assert.equal($("selected-count").textContent, "3 selected");
  // Enable it so the raster stacking order can still be verified.
  change("show-land-cover", true);
  await submit();
  assert.ok(calls.includes("/api/recovery/style"));
  assert.ok(
    layers.some((layer) => layer.url?.includes("/api/recovery/tiles/")),
  );
  assert.ok(
    layers.some(
      (layer) =>
        layer.url?.includes("/api/burn/tiles/") &&
        layer.url.includes("period=yearly"),
    ),
  );
  // Map stacking follows the layers-step order: recovery on top, then burned
  // area, then land cover.
  const z = (token) =>
    layers.find((layer) => layer.url?.includes(token))?.options?.zIndex;
  assert.ok(z("/api/recovery/tiles/") > z("/api/burn/tiles/"));
  assert.ok(z("/api/burn/tiles/") > z("/api/land-cover/tiles/"));
  assert.ok(calls.includes("/api/districts"));
  assert.match($("status").textContent, /Recovery 2025 loaded/);
});

test("Detection offers quick, monthly, and custom periods", async (t) => {
  const { $, submit, layers, calls, window } = setup(
    t,
    {},
    { scenario: "response" },
  );
  assert.equal($("detection-period-options").hidden, false);
  assert.deepEqual(
    [...window.document.querySelectorAll("[data-period]")].map((button) => button.textContent),
    ["Last 24h", "Last 48h", "Last 7 days", "Last 10 days", "Last 30 days", "This month", "Previous month"],
  );
  assert.equal(window.document.querySelector('[data-period="7"]').classList.contains("active"), true);
  assert.equal($("start").value, rollingStart(7));
  assert.equal($("end").value, utcToday());
  assert.match($("period-note").textContent, /UTC calendar dates/);
  assert.equal($("month-field").hidden, true);
  assert.equal($("dates-step").querySelector(".date-fields").hidden, true);
  assert.equal($("show-burn").checked, true);
  assert.equal($("show-districts").checked, true);
  assert.equal($("burn-card").hidden, false);
  assert.ok(window.document.querySelector(".context-settings #burn-card"));
  // Burned area is the first environmental-context layer, and the panel is
  // open because a layer inside it is enabled by default.
  const context = window.document.querySelector(".context-settings");
  assert.equal(context.querySelector(".layer-card"), $("burn-card"));
  assert.equal(context.open, true);
  assert.equal(
    $("burn-card").querySelector(".toggle-label span").textContent,
    "Burned area",
  );
  // Selecting a month reveals the picker and drives the fire query.
  window.document.querySelector('[data-period-mode="month"]').click();
  assert.equal($("month-field").hidden, false);
  const months = [...$("month-select").options].map((option) => option.value);
  assert.ok(months.length >= 1 && months.length <= 12);
  assert.ok(months.every((value) => value.startsWith("2026-")));
  assert.equal($("start").value, `${lastMonth2026()}-01`);
  await submit();
  assert.ok(calls.some((url) => url.includes(`start=${lastMonth2026()}-01`)));
  // The burned-area raster is the month before the selected month.
  let [year, month] = lastMonth2026().split("-").map(Number);
  month -= 1;
  if (month === 0) {
    month = 12;
    year -= 1;
  }
  const previous = `${year}-${String(month).padStart(2, "0")}`;
  assert.ok(
    layers.some(
      (layer) =>
        layer.url?.includes(`/api/burn/tiles/`) &&
        layer.url.includes(`period=${previous}`),
    ),
  );
  window.document.querySelector('[data-period-mode="custom"]').click();
  assert.equal($("month-field").hidden, true);
  assert.equal($("custom-period-fields").hidden, false);
});

test("detection loads numerical FWI at the selected extent centre", async (t) => {
  const result = {
    date: "2026-10-13",
    location: { longitude: 27.85, latitude: -13.15 },
    source: "GWIS numerical FWI",
    product: "fire_weather_index",
    fwi: 12.4,
    unit: "dimensionless",
    retrieval_time: "2026-10-13T12:00:00+00:00",
    status: "available",
    sampling: "point query",
    extent: [21.9, -18.1, 33.8, -8.2],
    coverage: "single provider-rendered WMS image across selected extent",
    raster_url:
      "/api/fire-weather-index/gwis/raster.png?date=2026-10-13&bbox=21.9%2C-18.1%2C33.8%2C-8.2",
  };
  const { $, change, submit, calls, layers } = setup(
    t,
    { "/api/fire-weather-index": result },
    { scenario: "response" },
  );
  assert.equal($("fwi-card").hidden, false);
  change("show-fwi", true);
  change("fwi-date", result.date);
  await submit();
  const request = calls.find((url) => url.startsWith("/api/fire-weather-index?"));
  assert.ok(request);
  const params = new URL(request, "http://localhost").searchParams;
  assert.equal(params.get("date"), result.date);
  assert.equal(Number(params.get("longitude")), 27.85);
  assert.equal(Number(params.get("latitude")), -13.15);
  assert.equal(params.get("bbox"), "21.9,-18.1,33.8,-8.2");
  assert.equal($("fwi-status").textContent, "Available");
  assert.match($("fwi-value").textContent, /12.4.*dimensionless/);
  assert.equal($("fwi-source").textContent, "GWIS numerical FWI");
  assert.equal($("fwi-sampling").textContent, "point query");
  assert.ok(layers.some((layer) => layer.kind === "image" && layer.url === result.raster_url));
  assert.match($("legend-content").textContent, /FWI area layer.*12.4/);
  assert.equal($("export").disabled, false);
});

test("queued EWDS FWI is shown as pending without a point marker", async (t) => {
  const { $, change, submit, layers } = setup(
    t,
    {
      "/api/fire-weather-index": {
        date: "2020-07-01",
        location: { longitude: 27.85, latitude: -13.15 },
        source: "Copernicus EWDS ERA5 historical FWI",
        product: "fire_weather_index",
        fwi: null,
        unit: "dimensionless",
        retrieval_time: "2026-10-13T12:00:00+00:00",
        status: "pending",
        sampling: "nearest grid cell",
        message: "EWDS download is queued or processing.",
      },
    },
    { scenario: "response" },
  );
  change("show-fire", false);
  change("show-burn", false);
  change("show-districts", false);
  change("show-fwi", true);
  change("fwi-date", "2020-07-01");
  await submit();
  assert.equal($("fwi-status").textContent, "Pending");
  assert.match($("fwi-message").textContent, /queued/);
  assert.equal(layers.some((layer) => layer.kind === "marker"), false);
  assert.equal($("activity-panel").hidden, false);
});

test("available EWDS FWI displays the numerical grid over the selected extent", async (t) => {
  const result = {
    date: "2020-07-01",
    location: { longitude: 27.85, latitude: -13.15 },
    sampled_location: { longitude: 27.75, latitude: -13.25 },
    extent: [21.9, -18.1, 33.8, -8.2],
    source: "Copernicus EWDS ERA5 historical FWI",
    fwi: 8.5,
    unit: "dimensionless",
    retrieval_time: "2026-10-14T08:00:00+00:00",
    status: "available",
    sampling: "nearest grid cell",
    coverage: "numerical raster across selected extent",
    raster_url: "/api/fire-weather-index/rasters/abc.png",
    statistics: { min: 1, mean: 9.2, max: 23, cell_count: 1200 },
  };
  const { $, change, submit, layers } = setup(
    t,
    { "/api/fire-weather-index": result },
    { scenario: "response" },
  );
  change("show-fwi", true);
  change("fwi-date", result.date);
  await submit();
  const image = layers.find((layer) => layer.kind === "image");
  assert.equal(image.url, result.raster_url);
  assert.deepEqual(image.bounds, [[-18.1, 21.9], [-8.2, 33.8]]);
  assert.match($("fwi-statistics").textContent, /1,200 cells.*min 1.*mean 9.2.*max 23/);
  assert.equal($("fwi-sampled-location").textContent, "-13.2500, 27.7500");
  assert.match($("legend-content").textContent, /1.*Lower.*23.*Higher/);
});

test("prevention hides the SLIM burn mask", async (t) => {
  const { $ } = setup(t, {}, { scenario: "prevention" });
  assert.equal($("burn-card").hidden, true);
});

test("selecting a district offers the district list, zooms to it, and highlights it", async (t) => {
  const { $, change, calls, layers, mapCalls } = setup(
    t,
    {
      "/api/districts/summary": [
        {
          name: "Chadiza",
          province: "Eastern",
          code: 103001,
          bbox: [31.9, -14.6, 33.3, -13.9],
        },
      ],
      "/api/districts": collection({
        type: "Feature",
        properties: { DISTRICT: "Chadiza", PROVINCE: "Eastern" },
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [31.9, -14.6],
              [33.3, -14.6],
              [33.3, -13.9],
              [31.9, -14.6],
            ],
          ],
        },
      }),
    },
    { scenario: "prevention" },
  );
  assert.equal($("district-field").hidden, true);
  change("region", "district");
  for (let i = 0; i < 8; i++) await flush();
  assert.equal($("district-field").hidden, false);
  assert.equal($("district").options.length, 1);
  assert.equal($("district").value, "Chadiza");
  assert.ok(calls.includes("/api/districts/summary"));
  // The query area follows the selected district's bounding box.
  assert.equal($("bbox").value, "31.9000,-14.6000,33.3000,-13.9000");
  assert.equal(mapCalls.at(-1)[0], "fitBounds");
  // The district boundary is highlighted as its own overlay.
  assert.ok(layers.some((layer) => layer.kind === "geojson" && !layer.removed));
  assert.ok(calls.includes("/api/districts"));
  // Switching back to Zambia clears the highlight and restores the default extent.
  change("region", "zambia");
  for (let i = 0; i < 4; i++) await flush();
  assert.equal($("district-field").hidden, true);
  assert.equal($("bbox").value, "21.9,-18.1,33.8,-8.2");
  assert.equal(
    layers.some((layer) => layer.kind === "geojson" && !layer.removed),
    false,
  );
});

test("prevention removes the custom-area controls and keeps a light district highlight", async (t) => {
  const { window, $, change, layers } = setup(
    t,
    {
      "/api/districts/summary": [
        {
          name: "Chadiza",
          province: "Eastern",
          code: 103001,
          bbox: [31.9, -14.6, 33.3, -13.9],
        },
      ],
      "/api/districts": collection({
        type: "Feature",
        properties: { DISTRICT: "Chadiza", PROVINCE: "Eastern" },
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [31.9, -14.6],
              [33.3, -14.6],
              [33.3, -13.9],
              [31.9, -14.6],
            ],
          ],
        },
      }),
    },
    { scenario: "prevention" },
  );
  // The Custom map area option, its extent button, and the coordinates input are gone.
  assert.equal($("region").querySelector('option[value="custom"]'), null);
  assert.equal($("use-map").hidden, true);
  assert.equal(
    window.document.querySelector("#area-step .coordinates").hidden,
    true,
  );
  // Selecting a district draws a prominent border with a near-transparent fill.
  change("region", "district");
  for (let i = 0; i < 8; i++) await flush();
  const highlight = layers.find(
    (layer) => layer.kind === "geojson" && !layer.removed,
  );
  assert.ok(highlight);
  assert.ok(highlight.options.style.weight >= 2.5);
  assert.ok(highlight.options.style.fillOpacity <= 0.06);
});

test("prevention skips the Dates step and still loads the hazard and district layers", async (t) => {
  const { window, $, calls, submit, layers } = setup(
    t,
    {},
    { scenario: "prevention" },
  );
  // The Dates step, its nav button, and the header period are removed.
  assert.equal($("dates-step-link").hidden, true);
  assert.equal($("dates-step-link").disabled, true);
  assert.equal($("layers-step-link").querySelector("span").textContent, "2");
  assert.equal($("step-caption").textContent, "STEP 1 OF 2");
  assert.equal($("next-step-label").textContent, "Continue to layers");
  // Advancing from Area goes straight to Layers.
  $("next-step").click();
  assert.equal($("area-step").hidden, true);
  assert.equal($("dates-step").hidden, true);
  assert.equal($("layers-step").hidden, false);
  assert.equal($("step-caption").textContent, "STEP 2 OF 2");
  assert.equal($("setup-summary").textContent, "Zambia");
  assert.equal($("previous-step").textContent, "Back to area");
  await submit();
  assert.deepEqual(
    calls.filter((url) => !url.endsWith("/style")),
    ["/api/districts"],
  );
  assert.ok(calls.includes("/api/hazard/style"));
  assert.ok(layers.some((layer) => layer.url?.includes("/api/hazard/")));
  assert.ok(layers.some((layer) => layer.kind === "geojson"));
});

test("an unknown scenario safely falls back to the original detection workflow", (t) => {
  const { window, $ } = setup(t, {}, { scenario: "unknown" });
  assert.equal(window.document.body.dataset.scenario, "response");
  assert.equal($("show-fire").checked, true);
  assert.equal($("selected-count").textContent, "3 selected");
});

test("cycle and cards respond to pointer and keyboard focus without changing navigation", (t) => {
  const dom = new JSDOM(
    fs.readFileSync(path.join(root, "static/index.html"), "utf8"),
    { url: "http://localhost/", runScripts: "outside-only" },
  );
  t.after(() => dom.window.close());
  const { window } = dom;
  window.eval(fs.readFileSync(path.join(root, "static/scenarios.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(root, "static/landing.js"), "utf8"));
  const doc = window.document;
  const response = doc.querySelector('.cycle-label[data-scenario="response"]');
  const card = doc.querySelector(".scenario-card.response");
  response.dispatchEvent(new window.Event("pointerenter"));
  assert.equal(card.classList.contains("is-active"), true);
  assert.equal(doc.getElementById("cycle-word").textContent, "Respond");
  assert.equal(response.getAttribute("href"), "/map?scenario=response");
  response.dispatchEvent(new window.Event("pointerleave"));
  assert.equal(card.classList.contains("is-active"), false);
  const restoration = doc.querySelector(
    ".scenario-card.restoration .open-scenario",
  );
  restoration.focus();
  assert.equal(doc.querySelector(".cycle").dataset.active, "restoration");
  assert.equal(doc.getElementById("cycle-word").textContent, "Restore");
  restoration.blur();
  assert.equal(doc.getElementById("cycle-word").textContent, "resilience");
  assert.equal(window.location.pathname, "/");
});

test("header feedback follows actual pending requests, unsaved settings, and failures", async (t) => {
  let release;
  let fail = false;
  const { $, window, submit, change } = setup(t, {
    "/api/fires": () =>
      fail
        ? { error: "Source unavailable" }
        : new Promise((resolve) => {
            release = resolve;
          }),
  });
  assert.equal($("workspace-state").textContent, "No layers loaded");
  const pending = submit();
  await flush();
  assert.equal($("workspace-state").textContent, "Loading map");
  assert.equal(window.document.body.classList.contains("map-loading"), true);
  release(collection(point()));
  await pending;
  assert.equal($("workspace-state").textContent, "Map ready");
  assert.equal(window.document.body.classList.contains("map-loading"), false);
  change("end", "2025-07-16");
  assert.equal($("workspace-state").textContent, "Changes not applied");
  fail = true;
  await submit();
  assert.equal($("workspace-state").textContent, "Needs attention");
  assert.equal(window.document.body.classList.contains("map-loading"), false);
});
