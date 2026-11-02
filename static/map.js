/* SLIM monitoring workspace. Data remains on the existing same-origin API. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const form = $("controls");
  const scenario = window.SLIM_ACTIVE_SCENARIO || null;
  const supportsFire = scenario?.id === "response";
  // Prevention observes a fixed 2025 hazard index and restoration a fixed 2025
  // recovery/burn status, so their wizards skip the Dates step entirely.
  const skipsDates =
    scenario?.id === "prevention" || scenario?.id === "restoration";
  const stepSequence = skipsDates ? [0, 2] : [0, 1, 2];
  const defaultBbox = "21.9,-18.1,33.8,-8.2";
  let busy = false;
  let settingsDirty = false;
  let loaded = null;
  let generation = 0;
  let palette = [];
  let hazardStyle = null;
  let burnStyle = null;
  let burnPeriod = null;
  let recoveryStyle = null;
  let fwiResult = null;
  let fireDateScale = null;
  let setupStep = 0;
  let unlockedStep = 0;
  let currentPanel = "explore";
  let explored = false;
  let fullControls = false;
  const guidance = [
    {
      title: "Where would you like to explore?",
      description: "Start with Zambia, or use the map to choose your own area.",
      next: "Continue to dates",
      hint: "Next, choose when to look for fire activity.",
    },
    {
      title: "When would you like to look?",
      description:
        "Choose a period to see the fire observations recorded during that time.",
      next: "Continue to layers",
      hint: "Next, choose what to show on the map.",
    },
    {
      title: "What would you like to see?",
      description:
        "Begin with fire detections. Add landscape context whenever you need it.",
      hint: "Your map is ready. Load your selected observations.",
    },
  ];
  if (scenario && scenario.id !== "response") {
    guidance[0].hint = skipsDates
      ? "Next, choose what to show on the map."
      : "Next, choose a period for the landscape layers.";
    guidance[1].description =
      "Choose the observation period for the available landscape layers.";
    guidance[2].description = scenario.note;
    guidance[2].hint =
      "Load the selected landscape layers. Future modules are preview only.";
  }
  if (skipsDates) {
    guidance[0].next = "Continue to layers";
    const layersLink = $("layers-step-link");
    if (layersLink) layersLink.querySelector("span").textContent = "2";
    const datesLink = $("dates-step-link");
    if (datesLink) {
      datesLink.hidden = true;
      datesLink.disabled = true;
    }
  }
  const overlays = new Map();
  const layerStates = new Map();
  let satelliteAcquisitions = [];
  let selectedAcquisition = null;
  let satelliteBbox = null;
  let satelliteWindow = null;
  let satelliteStyle = "true-color";
  const satelliteSource = "all";
  let satelliteFeature = null;
  let satelliteSearch = 0;
  const number = (value) =>
    Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
  const setText = (id, text) => {
    $(id).textContent = text;
  };
  const dateLabel = (value) =>
    new Date(value + "T00:00:00Z").toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    });
  function refreshHeaderContext() {
    $("header-context").hidden = !fullControls;
    if (!fullControls) return;
    setText(
      "header-area",
      $("region").value === "zambia"
        ? "Zambia"
        : $("region").value === "district"
          ? $("district").value || "District"
          : "Custom area",
    );
    const start = $("start").value;
    const end = $("end").value;
    setText(
      "header-dates",
      start && end
        ? `${dateLabel(start)} – ${dateLabel(end)}`
        : "Select a period",
    );
    $("header-dates").parentElement.hidden = skipsDates;
    const selected = [
      "show-fire",
      "show-fwi",
      "show-hazard",
      "show-burn",
      "show-recovery",
      "show-districts",
      "show-land-cover",
      "show-fuel",
      "show-precip-heatmap",
    ].filter((id) => $(id).checked).length;
    setText(
      "header-layers",
      `${selected} ${selected === 1 ? "layer" : "layers"}`,
    );
  }

  function setStatus(message, state = "ready") {
    setText("status", message);
    $("status").dataset.state = state;
    const loading =
      state === "loading" ||
      busy ||
      [...layerStates.values()].some((item) => item.pending);
    const displayState =
      state === "error"
        ? "error"
        : loading
          ? "loading"
          : settingsDirty
            ? "dirty"
            : state;
    document.body.dataset.mapState = displayState;
    document.body.classList.toggle("map-loading", loading);
    setText(
      "workspace-state",
      displayState === "error"
        ? "Needs attention"
        : loading
          ? "Loading map"
          : settingsDirty
            ? "Changes not applied"
            : loaded || overlays.size
              ? "Map ready"
              : "No layers loaded",
    );
    $("workspace-state").title = message;
  }
  function refreshActions() {
    const settings = currentPanel === "explore";
    $("next-step").hidden = !settings || fullControls || setupStep === 2;
    $("load").hidden = !settings || (!fullControls && setupStep !== 2);
    $("edit-settings").hidden = settings;
    $("previous-step").hidden = !settings || fullControls || setupStep === 0;
    setText(
      "load-label",
      busy ? "Loading observations…" : explored ? "Update map" : "Explore map",
    );
  }
  function enableFullControls(focus = false) {
    fullControls = true;
    unlockedStep = 2;
    $("setup-intro").hidden = true;
    $("setup-steps").hidden = true;
    $("setup-summary").hidden = true;
    $("full-settings-heading").hidden = false;
    for (const section of document.querySelectorAll("[data-step]"))
      section.hidden = skipsDates && Number(section.dataset.step) === 1;
    $("explore-panel").classList.add("full-settings");
    refreshHeaderContext();
    refreshActions();
    if (focus) $("full-settings-heading").querySelector("h2").focus();
  }
  function showStep(index, focus = false) {
    if (fullControls) {
      enableFullControls(focus);
      return;
    }
    setupStep = index;
    unlockedStep = Math.max(unlockedStep, index);
    for (const section of document.querySelectorAll("[data-step]"))
      section.hidden = Number(section.dataset.step) !== index;
    const position = stepSequence.indexOf(index) + 1;
    for (const button of document.querySelectorAll("[data-go-step]")) {
      const step = Number(button.dataset.goStep);
      if (step === 1 && skipsDates) {
        button.hidden = true;
        button.disabled = true;
        continue;
      }
      button.disabled = busy || step > unlockedStep;
      if (step === index) button.setAttribute("aria-current", "step");
      else button.removeAttribute("aria-current");
      button.classList.toggle(
        "complete",
        stepSequence.indexOf(step) < stepSequence.indexOf(index),
      );
    }
    const guide = guidance[index];
    setText("step-caption", `STEP ${position} OF ${stepSequence.length}`);
    setText("step-title", guide.title);
    setText("step-description", guide.description);
    setText("status", guide.hint);
    if (index !== stepSequence[stepSequence.length - 1])
      setText("next-step-label", guide.next);
    if (position > 1)
      setText(
        "previous-step",
        `Back to ${stepSequence[position - 2] === 0 ? "area" : "dates"}`,
      );
    if (index === 2) {
      const area =
        $("region").value === "zambia"
          ? "Zambia"
          : $("region").value === "district"
            ? $("district").value || "District"
            : "Custom area";
      setText(
        "setup-summary",
        skipsDates
          ? area
          : `${area} · ${dateLabel($("start").value)} – ${dateLabel($("end").value)}`,
      );
    }
    refreshActions();
    $("explore-panel").scrollTop = 0;
    if (focus) $("step-title").focus();
  }
  function validateDates() {
    if (
      !$("start").value ||
      !$("end").value ||
      $("start").value > $("end").value
    )
      throw new Error("Choose an end date on or after the start date.");
    if (supportsFire && $("end").value > new Date().toISOString().slice(0, 10))
      throw new Error("Choose today or an earlier end date.");
  }
  function advanceStep() {
    if (busy) return;
    try {
      if (setupStep === 0) readBounds();
      else validateDates();
      const position = stepSequence.indexOf(setupStep);
      showStep(
        stepSequence[Math.min(stepSequence.length - 1, position + 1)],
        true,
      );
    } catch (error) {
      setText("status", error.message);
      setStatus(error.message, "error");
      if (setupStep === 0) {
        $("bbox").closest("details").open = true;
        $("bbox").focus();
      } else $("end").focus();
    }
  }
  $("next-step").addEventListener("click", advanceStep);
  $("previous-step").addEventListener("click", () => {
    const position = stepSequence.indexOf(setupStep);
    showStep(stepSequence[Math.max(0, position - 1)], true);
  });
  for (const button of document.querySelectorAll("[data-go-step]"))
    button.addEventListener("click", () =>
      showStep(Number(button.dataset.goStep), true),
    );
  $("edit-settings").addEventListener("click", () => {
    enableFullControls();
    setPanel("explore");
    $("full-settings-heading").querySelector("h2").focus();
  });
  showStep(0);
  function setPanel(name) {
    currentPanel = name;
    for (const panel of ["explore", "activity"]) {
      const active = panel === name;
      $(panel + "-panel").hidden = !active;
      $(panel + "-tab").classList.toggle("active", active);
      $(panel + "-tab").setAttribute("aria-selected", String(active));
      $(panel + "-tab").tabIndex = active ? 0 : -1;
    }
    refreshActions();
    if (name === "activity")
      setText("status", "Select an observation to find it on the map.");
    else
      setText(
        "status",
        fullControls
          ? "Change any setting, then update the map."
          : guidance[setupStep].hint,
      );
  }
  for (const name of ["explore", "activity"]) {
    $(name + "-tab").addEventListener("click", () => setPanel(name));
    $(name + "-tab").addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
        return;
      event.preventDefault();
      const next =
        event.key === "Home"
          ? "explore"
          : event.key === "End"
            ? "activity"
            : name === "explore"
              ? "activity"
              : "explore";
      setPanel(next);
      $(next + "-tab").focus();
    });
  }
  function sidebar(open, returnFocus = false, focus = true) {
    const mobile = matchMedia("(max-width: 900px)").matches;
    $("sidebar").classList.toggle("open", open);
    $("menu-window").classList.toggle("open", open);
    document.body.classList.toggle("controls-open", open);
    $("sidebar-backdrop").hidden = !open || !mobile;
    $("menu-toggle").setAttribute("aria-expanded", String(open));
    $("menu-toggle").setAttribute(
      "aria-label",
      open ? "Collapse map controls" : "Open map controls",
    );
    $("menu-toggle")
      .querySelector("use")
      .setAttribute("href", open ? "#i-close" : "#i-menu");
    $("sidebar").inert = !open;
    if (open && focus) {
      if ($("workspace-tabs").hidden) $("step-title").focus();
      else $(currentPanel + "-tab").focus();
    } else if (returnFocus) $("menu-toggle").focus();
  }
  $("menu-toggle").addEventListener("click", () =>
    sidebar(!$("sidebar").classList.contains("open"), true),
  );
  $("sidebar-backdrop").addEventListener("click", () => sidebar(false, true));
  document.addEventListener("keydown", (event) => {
    if ($("help-dialog").open) return;
    if (event.key === "Escape" && $("sidebar").classList.contains("open"))
      sidebar(false, true);
    if (
      event.key === "Tab" &&
      $("sidebar").classList.contains("open") &&
      matchMedia("(max-width: 900px)").matches
    ) {
      const focusable = [
        ...$("menu-window").querySelectorAll("a,button,input,select,summary"),
      ].filter(
        (el) => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length,
      );
      const first = focusable[0],
        last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      }
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
  matchMedia("(max-width: 900px)").addEventListener("change", (event) =>
    sidebar(
      !event.matches,
      event.matches && $("sidebar").contains(document.activeElement),
      false,
    ),
  );
  sidebar(!matchMedia("(max-width: 900px)").matches, false, false);
  const workspaceHeader = document.querySelector(".workspace-header");
  function positionMenu() {
    const bottom = workspaceHeader.getBoundingClientRect().bottom;
    if (bottom)
      document.body.style.setProperty(
        "--header-bottom",
        `${Math.ceil(bottom)}px`,
      );
  }
  new ResizeObserver(positionMenu).observe(workspaceHeader);
  positionMenu();
  $("help").addEventListener("click", () => $("help-dialog").showModal());
  $("close-help").addEventListener("click", () => $("help-dialog").close());
  $("help-dialog").addEventListener("click", (event) => {
    if (event.target === $("help-dialog")) {
      const r = event.target.getBoundingClientRect();
      if (
        event.clientX < r.left ||
        event.clientX > r.right ||
        event.clientY < r.top ||
        event.clientY > r.bottom
      )
        event.target.close();
    }
  });

  if (!window.L) {
    setStatus(
      "The map library could not load. Check your connection and refresh the page.",
      "error",
    );
    for (const id of ["load", "next-step", "fit-area", "use-map"]) {
      const element = $(id);
      if (element) element.disabled = true;
    }
    return;
  }
  const map = L.map("map", {
    preferCanvas: true,
    zoomControl: false,
    minZoom: 2,
    worldCopyJump: true,
  }).setView([-13.2, 27.8], 6);
  L.control.zoom({ position: "topright" }).addTo(map);
  L.control.scale({ position: "bottomright", imperial: false }).addTo(map);
  const base = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  base.on("tileerror", () => {
    if (!busy && !loaded)
      setStatus(
        "Some basemap tiles could not load. Check your connection or try zooming out.",
        "error",
      );
  });
  // Leaflet.heat 0.2.0 has no opacity setter or custom-pane support.
  // Control only its canvas, leaving the fire renderer fully opaque.
  $("map").style.setProperty("--precip-opacity", ".5");
  let areaOutline;
  let selectedBounds;
  let districtHighlight;
  let districtList = null;
  let districtsGeo = null;
  function fitMapBounds(bounds, options = {}) {
    const mobile = matchMedia("(max-width: 900px)").matches;
    const panelWidth = $("sidebar").getBoundingClientRect().width || 360;
    const left =
      !mobile && $("sidebar").classList.contains("open") ? panelWidth + 42 : 30;
    const top = (workspaceHeader.getBoundingClientRect().bottom || 190) + 20;
    map.fitBounds(bounds, {
      paddingTopLeft: [left, top],
      paddingBottomRight: [70, 60],
      ...options,
    });
  }
  function readBounds() {
    const parts = $("bbox").value.split(",");
    const values = parts.map((value) =>
      value.trim() === "" ? NaN : Number(value),
    );
    if (values.length !== 4 || values.some((value) => !Number.isFinite(value)))
      throw new Error("Enter four coordinates: west, south, east, north.");
    const [w, s, e, n] = values;
    if (w < -180 || e > 180 || s < -90 || n > 90 || w >= e || s >= n)
      throw new Error(
        "Use valid coordinates: west must be less than east, and south less than north.",
      );
    return [
      [s, w],
      [n, e],
    ];
  }
  function clearAreaOutline() {
    if (areaOutline) {
      map.removeLayer(areaOutline);
      areaOutline = null;
    }
  }
  function clearDistrictHighlight() {
    if (districtHighlight) {
      map.removeLayer(districtHighlight);
      districtHighlight = null;
    }
  }
  function updateArea(fit = false) {
    selectedBounds = readBounds();
    clearAreaOutline();
    // A highlighted district replaces the dashed query rectangle.
    if ($("region").value !== "district") {
      areaOutline = L.rectangle(selectedBounds, {
        color: "#536f55",
        weight: 1,
        dashArray: "5 6",
        fill: false,
        interactive: false,
      }).addTo(map);
    }
    refreshHeaderContext();
    if (fit) fitMapBounds(selectedBounds);
  }
  async function loadDistrictList() {
    if (districtList) return districtList;
    districtList = await json("/api/districts/summary");
    const select = $("district");
    select.replaceChildren();
    for (const item of districtList) {
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent = item.province
        ? `${item.name} · ${item.province}`
        : item.name;
      option.dataset.bbox = (item.bbox || []).join(",");
      select.append(option);
    }
    return districtList;
  }
  async function loadDistrictGeometry() {
    if (!districtsGeo) districtsGeo = await json("/api/districts");
    return districtsGeo;
  }
  async function selectDistrict(name) {
    const entry =
      districtList?.find((item) => item.name === name) || districtList?.[0];
    if (!entry) return;
    $("district").value = entry.name;
    $("bbox").value = entry.bbox.map((value) => value.toFixed(4)).join(",");
    updateArea();
    clearDistrictHighlight();
    try {
      const geometry = await loadDistrictGeometry();
      const feature = geometry.features.find(
        (item) => item.properties?.DISTRICT === entry.name,
      );
      if (feature) {
        districtHighlight = L.geoJSON(feature, {
          style: {
            color: "#0b5cab",
            weight: 2.8,
            opacity: 1,
            fillColor: "#2f80ed",
            fillOpacity: 0.05,
          },
          interactive: false,
        }).addTo(map);
        districtHighlight.bringToFront?.();
      }
    } catch (error) {
      setStatus(error.message, "error");
    }
    fitMapBounds(selectedBounds);
    dirty();
  }
  updateArea(true);
  new ResizeObserver(() => map.invalidateSize()).observe($("map"));
  function dirty() {
    if (busy) return;
    settingsDirty = Boolean(loaded || fwiResult || overlays.size);
    setStatus(
      fullControls
        ? "Settings changed. Update the map to apply."
        : setupStep === 2
          ? "Settings changed. Load your map to apply."
          : guidance[setupStep].hint,
    );
  }
  form.addEventListener("input", dirty);
  form.addEventListener("change", dirty);
  $("region").addEventListener("change", async () => {
    const value = $("region").value;
    $("district-field").hidden = value !== "district";
    if (value === "zambia") {
      $("bbox").value = defaultBbox;
      clearDistrictHighlight();
      updateArea(true);
    } else if (value === "district") {
      try {
        await loadDistrictList();
        await selectDistrict($("district").value);
      } catch (error) {
        setStatus(error.message, "error");
      }
    } else {
      $("use-map")?.click();
    }
  });
  $("district").addEventListener("change", () => {
    selectDistrict($("district").value);
  });
  $("use-map")?.addEventListener("click", () => {
    const bounds = map.getBounds();
    const values = [
      Math.max(-180, bounds.getWest()),
      Math.max(-90, bounds.getSouth()),
      Math.min(180, bounds.getEast()),
      Math.min(90, bounds.getNorth()),
    ];
    $("bbox").value = values.map((value) => value.toFixed(4)).join(",");
    $("region").value = "custom";
    $("district-field").hidden = true;
    clearDistrictHighlight();
    try {
      updateArea();
      dirty();
    } catch (error) {
      setStatus(error.message, "error");
    }
  });
  $("bbox").addEventListener("change", () => {
    const region = $("region");
    if ($("bbox").value === defaultBbox) region.value = "zambia";
    else if (region.querySelector('option[value="custom"]'))
      region.value = "custom";
    $("district-field").hidden = true;
    clearDistrictHighlight();
    try {
      updateArea(true);
    } catch (error) {
      setStatus(error.message, "error");
    }
  });
  $("fit-area").addEventListener("click", () => {
    try {
      updateArea(true);
    } catch (error) {
      setStatus(error.message, "error");
    }
  });
  for (const button of document.querySelectorAll("[data-days]")) {
    button.addEventListener("click", () => {
      const end = new Date();
      const start = new Date(end);
      start.setDate(start.getDate() - Number(button.dataset.days) + 1);
      const localDate = (date) =>
        `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
      $("start").value = localDate(start);
      $("end").value = localDate(end);
      for (const preset of document.querySelectorAll("[data-days]"))
        preset.classList.toggle("active", preset === button);
      dirty();
    });
  }
  for (const id of ["start", "end"])
    $(id).addEventListener("input", () => {
      for (const preset of document.querySelectorAll("[data-days]"))
        preset.classList.remove("active");
      if (monthMode) updatePeriodUi("custom");
      refreshHeaderContext();
    });
  // Detection period choices all write the equivalent start/end dates so every
  // downstream layer continues to use one consistent query contract.
  const monthMode = scenario?.id === "response";
  const MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
  ];
  const yearMonths = (year) => {
    const now = new Date();
    const last = now.getFullYear() === year ? now.getMonth() + 1 : 12;
    return Array.from(
      { length: last },
      (_, index) => `${year}-${String(index + 1).padStart(2, "0")}`,
    );
  };
  const monthBounds = (period) => {
    const [year, month] = period.split("-").map(Number);
    const lastDay = new Date(year, month, 0).getDate();
    const day = (value) => String(value).padStart(2, "0");
    return {
      start: `${period}-01`,
      end: `${period}-${day(lastDay)}`,
    };
  };
  const utcDateValue = (value) => value.toISOString().slice(0, 10);
  const todayValue = () => utcDateValue(new Date());
  const previousMonth = (period) => {
    let [year, month] = period.split("-").map(Number);
    month -= 1;
    if (month === 0) {
      month = 12;
      year -= 1;
    }
    return `${year}-${String(month).padStart(2, "0")}`;
  };
  function applyMonth(period) {
    const bounds = monthBounds(period);
    const today = todayValue();
    const start = bounds.start;
    const end = bounds.start <= today && today <= bounds.end ? today : bounds.end;
    $("start").value = start;
    $("end").value = end;
    if ($("fwi-date")) {
      $("fwi-date").value = today.startsWith(period) ? today : end;
    }
    updatePeriodUi("month");
    refreshHeaderContext();
  }
  function updatePeriodNote() {
    if (!$("start").value || !$("end").value) return;
    setText(
      "period-note",
      `Selected period: ${dateLabel($("start").value)} – ${dateLabel($("end").value)}. FIRMS requests use UTC calendar dates.`,
    );
  }
  function updatePeriodUi(active) {
    for (const button of document.querySelectorAll("[data-period]")) {
      const selected = button.dataset.period === active;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
    }
    for (const button of document.querySelectorAll("[data-period-mode]")) {
      const selected = button.dataset.periodMode === active;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
    }
    if ($("month-field")) $("month-field").hidden = active !== "month";
    if ($("custom-period-fields"))
      $("custom-period-fields").hidden = active !== "custom";
    updatePeriodNote();
  }
  function applyRollingPeriod(days, active = String(days)) {
    const end = new Date();
    const start = new Date(end);
    start.setUTCDate(start.getUTCDate() - days + 1);
    $("start").value = utcDateValue(start);
    $("end").value = utcDateValue(end);
    if ($("fwi-date")) $("fwi-date").value = $("end").value;
    updatePeriodUi(active);
    refreshHeaderContext();
  }
  function applyNamedPeriod(period) {
    const today = new Date();
    const current = `${today.getUTCFullYear()}-${String(today.getUTCMonth() + 1).padStart(2, "0")}`;
    if (period === "this-month") {
      applyMonth(current);
      updatePeriodUi(period);
      return;
    }
    const bounds = monthBounds(previousMonth(current));
    $("start").value = bounds.start;
    $("end").value = bounds.end;
    if ($("fwi-date")) $("fwi-date").value = bounds.end;
    updatePeriodUi(period);
    refreshHeaderContext();
  }
  function populateMonthSelect() {
    const select = $("month-select");
    if (!select) return;
    const months = yearMonths(2026);
    select.replaceChildren();
    for (const period of months) {
      const option = document.createElement("option");
      option.value = period;
      const [year, month] = period.split("-").map(Number);
      option.textContent = `${MONTH_NAMES[month - 1]} ${year}`;
      select.append(option);
    }
    select.value = months[months.length - 1] || "";
    if (select.value) applyMonth(select.value);
  }
  if (monthMode) {
    populateMonthSelect();
    for (const id of ["start", "end"]) $(id).max = todayValue();
    for (const button of document.querySelectorAll("[data-period]"))
      button.addEventListener("click", () => {
        const period = button.dataset.period;
        if (/^\d+$/.test(period)) applyRollingPeriod(Number(period), period);
        else applyNamedPeriod(period);
        dirty();
      });
    for (const button of document.querySelectorAll("[data-period-mode]"))
      button.addEventListener("click", () => {
        const mode = button.dataset.periodMode;
        updatePeriodUi(mode);
        if (mode === "month") applyMonth($("month-select").value);
        dirty();
      });
    $("month-select").addEventListener("change", () => {
      applyMonth($("month-select").value);
      dirty();
    });
    applyRollingPeriod(7, "7");
  }
  const toggles = [
    "show-fire",
    "show-fwi",
    "show-hazard",
    "show-burn",
    "show-recovery",
    "show-districts",
    "show-land-cover",
    "show-fuel",
    "show-precip-heatmap",
  ];
  function selectionChanged() {
    if (!supportsFire) $("show-fire").checked = false;
    $("show-fire").disabled = !supportsFire || busy;
    const fire = supportsFire && $("show-fire").checked;
    $("fire-display").disabled = !fire || busy;
    $("sensor").disabled = !fire || busy;
    $("show-fwi").disabled = !supportsFire || busy;
    $("fwi-date").disabled =
      !supportsFire || !$("show-fwi").checked || busy;
    $("fwi-date").required = supportsFire && $("show-fwi").checked;
    $("event-note").hidden = $("fire-display").value !== "events";
    for (const id of toggles) {
      const input = $(id);
      input.closest(".layer-card").classList.toggle("selected", input.checked);
      const panel = input.getAttribute("aria-controls");
      if (panel) {
        $(panel).hidden = !input.checked;
        input.setAttribute("aria-expanded", String(input.checked));
      }
    }
    setText(
      "selected-count",
      `${toggles.filter((id) => $(id).checked).length} selected`,
    );
    refreshHeaderContext();
  }
  for (const id of [...toggles, "fire-display"])
    $(id).addEventListener("change", selectionChanged);
  selectionChanged();
  for (const [id, key] of [
    ["fwi-opacity", "fwi"],
    ["hazard-opacity", "hazard"],
    ["burn-opacity", "burn"],
    ["recovery-opacity", "recovery"],
    ["land-cover-opacity", "land"],
    ["fuel-opacity", "fuel"],
    ["precip-heatmap-opacity", "rain"],
  ]) {
    $(id).addEventListener("input", (event) => {
      event.stopPropagation();
      setText(id + "-value", $(id).value + "%");
      if (key === "rain")
        $("map").style.setProperty(
          "--precip-opacity",
          Number($(id).value) / 100,
        );
      else overlays.get(key)?.setOpacity(Number($(id).value) / 100);
    });
  }
  async function json(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 180000);
    try {
      const response = await fetch(url, { signal: controller.signal });
      const data = await response.json();
      if (!response.ok) {
        const requestError = new Error(
          typeof data.detail === "string"
            ? data.detail
            : typeof data.message === "string"
              ? data.message
              : `Request failed (HTTP ${response.status}). Check your area and dates.`,
        );
        requestError.data = data;
        throw requestError;
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError")
        throw new Error(
          "The data request timed out. Try a smaller area or shorter period.",
        );
      if (error instanceof SyntaxError)
        throw new Error(
          "The server returned an unreadable response. Try again.",
        );
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }
  json("/api/land-cover/style")
    .then((style) => {
      palette = style.entries || [];
      renderLegend();
    })
    .catch(() => {
      /* The palette is optional; map requests report their own errors. */
    });

  function refreshStatus() {
    const states = [...layerStates.values()];
    const failures = states.some((state) => state.error);
    const pending = states.some((state) => state.pending);
    setStatus(
      states.map((state) => state.message).join(" · ") ||
        "No layers selected. Choose a data layer to begin.",
      failures ? "error" : pending ? "loading" : "ready",
    );
  }
  function state(key, message, options = {}) {
    layerStates.set(key, { message, ...options });
    refreshStatus();
  }
  // Raster layers are stacked so the map follows the on-screen order in the
  // layers step: the first listed layer sits on top of the ones below it.
  const TILE_KEYS = {
    "show-fwi": "fwi",
    "show-hazard": "hazard",
    "show-recovery": "recovery",
    "show-burn": "burn",
    "show-land-cover": "land",
    "show-fuel": "fuel",
  };
  function tileZIndex(key) {
    const order = [...document.querySelectorAll("#layers-step input[type=checkbox]")]
      .map((input) => TILE_KEYS[input.id])
      .filter(Boolean);
    const index = order.indexOf(key);
    return index < 0 ? 200 : 300 - index * 10;
  }
  function tiles(key, name, url, options, run) {
    const layer = L.tileLayer(url, {
      maxZoom: 19,
      zIndex: tileZIndex(key),
      ...options,
    });
    let failed = false;
    overlays.set(key, layer);
    state(key, `${name} loading`, { pending: true });
    layer.on("loading", () => {
      if (run === generation) {
        failed = false;
        state(key, `${name} loading`, { pending: true });
      }
    });
    layer.on("tileerror", () => {
      if (run === generation) {
        failed = true;
        state(key, `${name}: some tiles failed`, { error: true });
      }
    });
    layer.on("load", () => {
      if (run === generation && !failed) state(key, `${name} loaded`);
    });
    layer.addTo(map);
    return layer;
  }
  // The monthly burn mask follows the month of the selected end date; when that
  // month is not published, the nearest available month is used (and shown).
  function pickBurnMonth(months, endDate) {
    if (!Array.isArray(months) || !months.length) return "yearly";
    const target = String(endDate || "").slice(0, 7);
    if (months.includes(target)) return target;
    const index = (value) => {
      const [year, month] = String(value).split("-").map(Number);
      return Number.isFinite(year) && Number.isFinite(month)
        ? year * 12 + month
        : NaN;
    };
    const wanted = index(target);
    if (!Number.isFinite(wanted)) return months[months.length - 1];
    return months
      .map((month) => [month, Math.abs(index(month) - wanted)])
      .sort((a, b) => a[1] - b[1])[0][0];
  }
  const popupLabels = {
    fire_id: "Event ID",
    interval_start: "Interval start",
    interval_end: "Interval end",
    start_time: "First observation",
    end_time: "Last observation",
    detections: "Detections",
    max_frp: "Maximum FRP (MW)",
    cluster_area_ha: "Cluster area (ha)",
    acq_datetime_utc: "Observed (UTC)",
    satellite: "Satellite",
    instrument: "Instrument",
    confidence: "Confidence",
    frp: "FRP (MW)",
    firms_source: "Source",
  };
  const TURBO_COLORS = [
    "#30123b",
    "#4146a6",
    "#4776ee",
    "#36aaf9",
    "#1ae4b6",
    "#72fe5e",
    "#c8ef34",
    "#faba39",
    "#f66b19",
    "#c92903",
    "#7a0403",
  ];
  function firePointRadius(properties = {}) {
    const value = Number(properties.frp ?? properties.max_frp);
    if (!Number.isFinite(value) || value <= 0) return 4;
    // Radius grows with sqrt(FRP), making the visible symbol area proportional
    // to radiant power while clamps keep extreme readings usable on the map.
    return Math.min(18, Math.max(4, 1.2 * Math.sqrt(value)));
  }
  function observationTime(properties = {}) {
    const value =
      properties.acq_datetime_utc ||
      properties.end_time ||
      properties.start_time ||
      properties.interval_end;
    const timestamp = Date.parse(value);
    return Number.isFinite(timestamp) ? timestamp : null;
  }
  function observationDay(properties = {}) {
    const timestamp = observationTime(properties);
    if (timestamp === null) return null;
    const date = new Date(timestamp);
    return Date.UTC(
      date.getUTCFullYear(),
      date.getUTCMonth(),
      date.getUTCDate(),
    );
  }
  function interpolateHex(start, end, amount) {
    const channels = (color) => [1, 3, 5].map((index) =>
      parseInt(color.slice(index, index + 2), 16),
    );
    const from = channels(start);
    const to = channels(end);
    return `#${from.map((value, index) =>
      Math.round(value + (to[index] - value) * amount)
        .toString(16)
        .padStart(2, "0"),
    ).join("")}`;
  }
  function turboColor(amount) {
    const position = Math.max(0, Math.min(1, amount)) * (TURBO_COLORS.length - 1);
    const index = Math.min(TURBO_COLORS.length - 2, Math.floor(position));
    return interpolateHex(TURBO_COLORS[index], TURBO_COLORS[index + 1], position - index);
  }
  function configureFireDateScale(start, end) {
    const startTime = Date.parse(`${start}T00:00:00Z`);
    const selectedEnd = Date.parse(`${end}T23:59:59.999Z`);
    const now = Date.now();
    const endTime = startTime <= now && now < selectedEnd ? now : selectedEnd;
    const days = Math.max(1, Math.ceil((endTime - startTime) / 86_400_000));
    fireDateScale = {
      start: startTime,
      end: Math.max(startTime, endTime),
      intervals: Math.min(6, days),
    };
  }
  function fireDateColor(properties = {}) {
    const timestamp = observationTime(properties);
    if (timestamp === null) return "#6b7280";
    if (!fireDateScale || fireDateScale.end <= fireDateScale.start)
      return turboColor(1);
    return turboColor(
      (timestamp - fireDateScale.start) /
        (fireDateScale.end - fireDateScale.start),
    );
  }
  function firePointStyle(feature) {
    const properties = feature?.properties || {};
    return {
      radius: firePointRadius(properties),
      color: "#5f4b3f",
      weight: 1,
      fillColor: fireDateColor(properties),
      fillOpacity: 0.9,
    };
  }
  function popup(feature, layer) {
    const isObservation = feature.geometry.type === "Point";
    const panel = document.createElement("div");
    panel.className = "popup-card";
    const heading = document.createElement("div");
    heading.className = "popup-heading";
    const eyebrow = document.createElement("span");
    eyebrow.className = "popup-eyebrow";
    eyebrow.textContent = isObservation ? "FIRE DETECTION" : "FIRE ACTIVITY";
    const title = document.createElement("h3");
    title.className = "popup-title";
    title.textContent = isObservation ? "Fire observation" : "Fire event";
    heading.append(eyebrow, title);
    panel.append(heading);
    for (const [key, name] of Object.entries(popupLabels)) {
      const value = feature.properties?.[key];
      if (value === undefined || value === null) continue;
      const row = document.createElement("div");
      row.className = "popup-row";
      const label = document.createElement("strong");
      label.textContent = name;
      const text = document.createElement("span");
      text.textContent = String(value);
      row.append(label, text);
      panel.append(row);
    }
    layer.bindPopup(panel);
    if (isObservation && supportsFire)
      layer.on("click", () => loadSatelliteAcquisitions(feature));
  }

  function createFireLayer(data) {
    return L.geoJSON(data, {
      // Leaflet applies GeoJSON's `style` after `pointToLayer`. Return the
      // point style here as well so the polygon fallback cannot overwrite the
      // Turbo fill colour assigned to FIRMS circle markers.
      style: (feature) =>
        feature?.geometry?.type === "Point"
          ? firePointStyle(feature)
          : {
              color: "#b96539",
              weight: 1.5,
              fillColor: "#ec965e",
              fillOpacity: 0.28,
            },
      pointToLayer: (feature, latlng) =>
        L.circleMarker(latlng, firePointStyle(feature)),
      onEachFeature: popup,
    }).addTo(map);
  }
  function fireDateLegendIntervals() {
    if (!fireDateScale) return [];
    const { start, end, intervals } = fireDateScale;
    const duration = Math.max(1, end - start);
    const format = new Intl.DateTimeFormat(undefined, {
      day: "numeric",
      month: "short",
      timeZone: "UTC",
    });
    return Array.from({ length: intervals }, (_, index) => {
      const from = start + (duration * index) / intervals;
      const to = start + (duration * (index + 1)) / intervals;
      return {
        color: turboColor((index + 0.5) / intervals),
        label: `${format.format(new Date(from))} – ${format.format(new Date(to))}`,
      };
    });
  }

  function satelliteDate(value) {
    return new Date(value).toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    });
  }
  function satelliteSourceLabel(source) {
    return source === "landsat-8-9"
      ? "Landsat 8/9"
      : source === "all"
        ? "Sentinel-2 + Landsat 8/9"
        : "Sentinel-2";
  }
  function acquisitionLabel(acquisition) {
    const platform = String(acquisition.platform || "").toLowerCase();
    if (platform === "landsat-8") return "Landsat 8";
    if (platform === "landsat-9") return "Landsat 9";
    return satelliteSourceLabel(acquisition.source || satelliteSource);
  }
  function acquisitionKey(acquisition) {
    return `${acquisition.source || ""}:${acquisition.id || acquisition.datetime}`;
  }
  function satelliteRadius() {
    return Number($("satellite-radius").value);
  }
  function satelliteCoverageLabel(radius = satelliteRadius()) {
    const span = radius * 2;
    return `${number(span)} × ${number(span)} km`;
  }
  function satelliteBounds(longitude, latitude, radius) {
    const latitudeDelta = radius / 111.32;
    const longitudeDelta =
      radius /
      Math.max(1, 111.32 * Math.cos((latitude * Math.PI) / 180));
    return [
      Math.max(-180, longitude - longitudeDelta),
      Math.max(-90, latitude - latitudeDelta),
      Math.min(180, longitude + longitudeDelta),
      Math.min(90, latitude + latitudeDelta),
    ];
  }
  function highlightSatelliteSource(source) {
    for (const option of document.querySelectorAll("[data-satellite-source]"))
      option.classList.toggle("active", option.dataset.satelliteSource === source);
  }
  function clearSatelliteImagery(hidePanel = true) {
    satelliteSearch += 1;
    const overlay = overlays.get("satellite");
    if (overlay) map.removeLayer(overlay);
    overlays.delete("satellite");
    satelliteAcquisitions = [];
    selectedAcquisition = null;
    satelliteBbox = null;
    satelliteWindow = null;
    highlightSatelliteSource(null);
    $("satellite-controls").hidden = true;
    $("satellite-timeline").replaceChildren();
    $("satellite-map-bar").hidden = true;
    if (hidePanel) {
      satelliteFeature = null;
      $("satellite-result").hidden = true;
    }
    renderLegend();
  }
  function renderSatelliteTimeline() {
    const timeline = $("satellite-timeline");
    timeline.replaceChildren();
    if (!satelliteWindow?.reference_datetime) return;
    const dayKey = (value) => new Date(value).toISOString().slice(0, 10);
    const byDate = new Map();
    for (const acquisition of satelliteAcquisitions) {
      const key = dayKey(acquisition.datetime);
      if (!byDate.has(key)) byDate.set(key, []);
      byDate.get(key).push(acquisition);
    }
    const referenceKey = dayKey(satelliteWindow.reference_datetime);
    const start = new Date(satelliteWindow.window_start);
    for (let offset = 0; offset <= 25; offset += 1) {
      const current = new Date(start);
      current.setUTCDate(start.getUTCDate() + offset);
      const key = dayKey(current);
      const acquisitions = byDate.get(key) || [];
      const day = document.createElement("div");
      day.className = "satellite-day";
      day.classList.toggle("reference", key === referenceKey);
      const date = document.createElement("small");
      date.textContent = `${current.getUTCDate()} ${current.toLocaleDateString(undefined, { month: "short", timeZone: "UTC" })}`;
      day.append(date);
      const actions = document.createElement("div");
      actions.className = "satellite-day-actions";
      if (!acquisitions.length) {
        day.title = `${satelliteDate(current.toISOString())} — no acquisition`;
      }
      for (const acquisition of acquisitions) {
        const button = document.createElement("button");
        const label = acquisitionLabel(acquisition);
        button.type = "button";
        button.className = `available ${acquisition.source}`;
        button.classList.toggle(
          "active",
          acquisitionKey(acquisition) === acquisitionKey(selectedAcquisition || {}),
        );
        button.textContent = label === "Sentinel-2" ? "S2" : label.replace("Landsat ", "L");
        button.title = `${satelliteDate(acquisition.datetime)} — ${label} RGB available${acquisition.cloud_cover == null ? "" : ` — ${Math.round(acquisition.cloud_cover)}% scene cloud`}`;
        button.setAttribute("aria-label", button.title);
        button.addEventListener("click", () => showSatelliteAcquisition(acquisition));
        actions.append(button);
      }
      day.append(actions);
      timeline.append(day);
    }
  }
  function showSatelliteAcquisition(acquisition) {
    if (!satelliteBbox) return;
    selectedAcquisition = acquisition;
    highlightSatelliteSource(acquisition.source);
    setText("satellite-map-date", satelliteDate(acquisition.datetime));
    renderSatelliteTimeline();
    const existing = overlays.get("satellite");
    if (existing) map.removeLayer(existing);
    const params = new URLSearchParams({
      datetime: acquisition.datetime,
      bbox: satelliteBbox.join(","),
      style: satelliteStyle,
      source: acquisition.source || satelliteSource,
      width: "768",
      height: "768",
    });
    const bounds = [
      [satelliteBbox[1], satelliteBbox[0]],
      [satelliteBbox[3], satelliteBbox[2]],
    ];
    const overlay = L.imageOverlay(`/api/cdse/image?${params}`, bounds, {
      opacity: Number($("satellite-opacity").value) / 100,
      interactive: false,
      zIndex: 190,
    });
    overlay.on("load", () => {
      setText(
        "satellite-status",
        `${acquisitionLabel(acquisition)} · ${satelliteDate(acquisition.datetime)} · ${satelliteStyle === "swir" ? "SWIR" : "True colour"}${acquisition.cloud_cover == null ? "" : ` · ${Math.round(acquisition.cloud_cover)}% scene cloud`}`,
      );
    });
    overlay.on("error", () => {
      setText(
        "satellite-status",
        "The satellite image could not be rendered. Check CDSE authentication and try again.",
      );
    });
    overlay.addTo(map);
    overlays.set("satellite", overlay);
    overlays.get("fire")?.bringToFront?.();
    setText("satellite-status", "Rendering the selected acquisition…");
    renderLegend();
  }
  async function loadSatelliteAcquisitions(feature) {
    if (!supportsFire || feature?.geometry?.type !== "Point") return;
    satelliteFeature = feature;
    const [longitude, latitude] = feature.geometry.coordinates;
    $("satellite-radius").value = "5";
    setText("satellite-radius-value", satelliteCoverageLabel());
    const search = ++satelliteSearch;
    const existing = overlays.get("satellite");
    if (existing) map.removeLayer(existing);
    overlays.delete("satellite");
    $("satellite-result").hidden = false;
    $("satellite-controls").hidden = true;
    $("satellite-map-bar").hidden = true;
    highlightSatelliteSource(null);
    setText(
      "satellite-status",
      `Searching recent ${satelliteSourceLabel(satelliteSource)} acquisitions…`,
    );
    renderLegend();
    try {
      const timestamp = observationTime(feature.properties || {});
      if (timestamp === null)
        throw new Error("This FIRMS point has no usable observation time.");
      const result = await json(
        "/api/cdse/acquisitions?" +
          new URLSearchParams({
            longitude: String(longitude),
            latitude: String(latitude),
            reference_datetime: new Date(timestamp).toISOString(),
            before_days: "10",
            after_days: "15",
            limit: "20",
            radius_km: String(satelliteRadius()),
            source: satelliteSource,
          }),
      );
      if (search !== satelliteSearch) return;
      satelliteAcquisitions = result.acquisitions || [];
      satelliteBbox = result.bbox;
      satelliteWindow = result;
      $("satellite-map-bar").hidden = false;
      setText(
        "satellite-reference-label",
        `FIRMS · ${satelliteDate(result.reference_datetime)}`,
      );
      setText("satellite-map-date", "No acquisition selected");
      renderSatelliteTimeline();
      if (!satelliteAcquisitions.length) {
        setText(
          "satellite-status",
          `No ${satelliteSourceLabel(satelliteSource)} RGB acquisitions were found from 10 days before to 15 days after this observation.`,
        );
        return;
      }
      $("satellite-controls").hidden = false;
      const closest = satelliteAcquisitions.reduce((best, item) =>
        Math.abs(Date.parse(item.datetime) - timestamp) <
        Math.abs(Date.parse(best.datetime) - timestamp)
          ? item
          : best,
      );
      showSatelliteAcquisition(closest);
    } catch (error) {
      if (search !== satelliteSearch) return;
      const missingCredentials = /CDSE_CLIENT_ID|not configured/i.test(error.message);
      setText(
        "satellite-status",
        missingCredentials
          ? "Satellite imagery is ready but CDSE credentials are not configured on this server."
          : `Satellite search failed: ${error.message}`,
      );
    }
  }
  $("satellite-clear").addEventListener("click", () =>
    clearSatelliteImagery(),
  );
  $("satellite-map-close").addEventListener("click", () =>
    clearSatelliteImagery(),
  );
  for (const button of document.querySelectorAll("[data-satellite-style]")) {
    button.addEventListener("click", () => {
      satelliteStyle = button.dataset.satelliteStyle;
      for (const option of document.querySelectorAll("[data-satellite-style]"))
        option.classList.toggle(
          "active",
          option.dataset.satelliteStyle === satelliteStyle,
        );
      if (selectedAcquisition) showSatelliteAcquisition(selectedAcquisition);
    });
  }
  for (const button of document.querySelectorAll("[data-satellite-source]")) {
    button.addEventListener("click", () => {
      const source = button.dataset.satelliteSource;
      const choices = satelliteAcquisitions.filter(
        (acquisition) => acquisition.source === source,
      );
      const reference = Date.parse(satelliteWindow?.reference_datetime || "");
      if (!choices.length || !Number.isFinite(reference)) return;
      const closest = choices.reduce((best, item) =>
        Math.abs(Date.parse(item.datetime) - reference) <
        Math.abs(Date.parse(best.datetime) - reference)
          ? item
          : best,
      );
      showSatelliteAcquisition(closest);
    });
  }
  $("satellite-opacity").addEventListener("input", () => {
    setText("satellite-opacity-value", `${$("satellite-opacity").value}%`);
    overlays
      .get("satellite")
      ?.setOpacity(Number($("satellite-opacity").value) / 100);
    renderLegend();
  });
  $("satellite-radius").addEventListener("input", () => {
    setText("satellite-radius-value", satelliteCoverageLabel());
  });
  $("satellite-radius").addEventListener("change", () => {
    if (!satelliteFeature || !selectedAcquisition) return;
    const [longitude, latitude] = satelliteFeature.geometry.coordinates;
    satelliteBbox = satelliteBounds(
      longitude,
      latitude,
      satelliteRadius(),
    );
    showSatelliteAcquisition(selectedAcquisition);
  });
  function renderResults(data, fireLayer, mode) {
    const features = data.features;
    const isEvents = mode === "events";
    setText("detection-count", number(features.length));
    setText("results-badge", number(features.length));
    setText("count-label", isEvents ? "Fire events" : "Fire detections");
    const powers = features
      .map((feature) => feature.properties?.[isEvents ? "max_frp" : "frp"])
      .filter(
        (value) =>
          value !== null &&
          value !== undefined &&
          value !== "" &&
          Number.isFinite(Number(value)),
      )
      .map(Number);
    setText(
      "max-frp",
      powers.length
        ? number(powers.reduce((a, b) => Math.max(a, b), -Infinity))
        : "—",
    );
    setText(
      "results-description",
      features.length
        ? `${number(features.length)} ${isEvents ? "event polygons" : "detections"}. Select an observation to locate it on the map.${features.length > 100 ? " Showing the first 100; export for all records." : ""}`
        : "No observations returned for this area, period, and sensor. Try a different selection.",
    );
    $("results-list").replaceChildren();
    const layers = fireLayer.getLayers();
    features.slice(0, 100).forEach((feature, index) => {
      const p = feature.properties || {};
      const button = document.createElement("button");
      button.type = "button";
      button.className = "result-item";
      const description = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = isEvents
        ? `Event ${p.fire_id ?? index + 1}`
        : `${p.instrument || "Fire"} · ${p.satellite || "Satellite observation"}`;
      const date = document.createElement("small");
      date.textContent = String(
        p.acq_datetime_utc ||
          p.start_time ||
          p.interval_start ||
          "Time unavailable",
      );
      const power = document.createElement("span");
      const value = p[isEvents ? "max_frp" : "frp"];
      power.textContent =
        value != null && Number.isFinite(Number(value))
          ? `${number(value)} MW`
          : "View ↗";
      description.append(title, date);
      button.append(description, power);
      button.addEventListener("click", () => {
        const layer = layers[index];
        if (layer.getLatLng)
          map.setView(layer.getLatLng(), Math.max(map.getZoom(), 10));
        else
          map.fitBounds(layer.getBounds(), { padding: [50, 50], maxZoom: 12 });
        layer.openPopup();
        if (!isEvents) loadSatelliteAcquisitions(feature);
        sidebar(false, true);
      });
      $("results-list").append(button);
    });
  }
  function configureFrpFilter(data, mode) {
    const panel = $("frp-filter");
    const powers = data.features
      .map((feature) => {
        const value = feature.properties?.frp;
        return value === null || value === undefined || value === ""
          ? NaN
          : Number(value);
      })
      .filter((value) => Number.isFinite(value) && value >= 0)
      .sort((a, b) => a - b);
    panel.hidden = mode !== "fires" || !powers.length;
    if (panel.hidden) return;
    const [low, high] = powers.reduce(
      ([minimum, maximum], value) => [
        Math.min(minimum, value),
        Math.max(maximum, value),
      ],
      [Infinity, -Infinity],
    );
    const slider = $("frp-threshold");
    const middle = Math.floor(powers.length / 2);
    const median =
      powers.length % 2
        ? powers[middle]
        : (powers[middle - 1] + powers[middle]) / 2;
    slider.min = "0";
    slider.max = "1000";
    slider.value = "0";
    slider.dataset.frpLow = String(low);
    slider.dataset.frpHigh = String(high);
    slider.disabled = low === high;
    setText("frp-low-label", `${number(low)} MW · lowest`);
    setText("frp-median-label", `${number(median)} MW · median`);
    setText("frp-high-label", `${number(high)} MW · highest`);
    setText("frp-threshold-value", `≥ ${number(low)} MW`);
    setText(
      "frp-filter-count",
      `${number(data.features.length)} of ${number(data.features.length)} detections shown`,
    );
  }
  function filterDateLabel(timestamp) {
    return new Intl.DateTimeFormat(undefined, {
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    }).format(new Date(timestamp));
  }
  function configureDateFilter(data, mode) {
    const panel = $("date-filter");
    const times = data.features
      .map((feature) => observationDay(feature.properties || {}))
      .filter(Number.isFinite)
      .sort((a, b) => a - b);
    panel.hidden = mode !== "fires" || !times.length;
    if (panel.hidden) return;
    const earliest = times[0];
    const latest = times[times.length - 1];
    const slider = $("date-threshold");
    slider.value = "0";
    slider.dataset.dateLow = String(earliest);
    slider.dataset.dateHigh = String(latest);
    slider.disabled = earliest === latest;
    setText("date-low-label", `${filterDateLabel(earliest)} · earliest`);
    setText("date-high-label", `${filterDateLabel(latest)} · latest`);
    setText("date-threshold-value", `From ${filterDateLabel(earliest)}`);
    setText(
      "date-filter-count",
      `${number(data.features.length)} of ${number(data.features.length)} detections shown`,
    );
  }
  function frpThresholdFromSlider() {
    const slider = $("frp-threshold");
    const low = Number(slider.dataset.frpLow);
    const high = Number(slider.dataset.frpHigh);
    const position = Number(slider.value);
    if (![low, high, position].every(Number.isFinite)) return NaN;
    if (high <= low) return low;
    if (position <= 0) return low;
    if (position >= 1000) return high;
    const ratio = Math.max(0, Math.min(1, position / 1000));
    return Math.expm1(
      Math.log1p(low) + ratio * (Math.log1p(high) - Math.log1p(low)),
    );
  }
  function dateThresholdFromSlider() {
    const slider = $("date-threshold");
    const earliest = Number(slider.dataset.dateLow);
    const latest = Number(slider.dataset.dateHigh);
    const position = Number(slider.value);
    if (![earliest, latest, position].every(Number.isFinite)) return NaN;
    if (latest <= earliest || position <= 0) return earliest;
    if (position >= 1000) return latest;
    const day = 86_400_000;
    const dayOffset = Math.round(
      ((latest - earliest) / day) * (position / 1000),
    );
    return earliest + dayOffset * day;
  }
  function applyFireFilters() {
    if (!loaded || loaded.mode !== "fires") return;
    const frpThreshold = frpThresholdFromSlider();
    const dateThreshold = dateThresholdFromSlider();
    if (![frpThreshold, dateThreshold].every(Number.isFinite)) return;
    const features = loaded.data.features.filter((feature) => {
      const value = feature.properties?.frp;
      const frp = value === null || value === undefined || value === ""
        ? NaN
        : Number(value);
      const matchesFrp = Number.isFinite(frp)
        ? frp >= frpThreshold
        : Number($("frp-threshold").value) === 0;
      const observed = observationDay(feature.properties || {});
      const matchesDate = observed === null
        ? Number($("date-threshold").value) === 0
        : observed >= dateThreshold;
      return matchesFrp && matchesDate;
    });
    const current = overlays.get("fire");
    if (current) map.removeLayer(current);
    const filtered = { ...loaded.data, features };
    const layer = createFireLayer(filtered);
    overlays.set("fire", layer);
    loaded.filteredData = filtered;
    setText("frp-threshold-value", `≥ ${number(frpThreshold)} MW`);
    setText("date-threshold-value", `From ${filterDateLabel(dateThreshold)}`);
    setText(
      "frp-filter-count",
      `${number(features.length)} of ${number(loaded.data.features.length)} detections shown`,
    );
    setText(
      "date-filter-count",
      `${number(features.length)} of ${number(loaded.data.features.length)} detections shown`,
    );
    renderResults(filtered, layer, loaded.mode);
    if (satelliteFeature && !features.includes(satelliteFeature))
      clearSatelliteImagery();
    state(
      "fire",
      `${number(features.length)} of ${number(loaded.data.features.length)} fire detections shown`,
    );
    renderLegend();
  }
  $("frp-threshold").addEventListener("input", (event) => {
    event.stopPropagation();
    applyFireFilters();
  });
  $("date-threshold").addEventListener("input", (event) => {
    event.stopPropagation();
    applyFireFilters();
  });
  function renderFwi(result) {
    fwiResult = result;
    $("fwi-result").hidden = false;
    $("fwi-status").textContent =
      result.status.charAt(0).toUpperCase() + result.status.slice(1);
    $("fwi-status").dataset.status = result.status;
    setText(
      "fwi-value",
      result.fwi == null ? "—" : `${number(result.fwi)} (dimensionless)`,
    );
    setText("fwi-result-date", dateLabel(result.date));
    setText(
      "fwi-location",
      result.location
        ? `${Number(result.location.latitude).toFixed(4)}, ${Number(result.location.longitude).toFixed(4)}`
        : "—",
    );
    $("fwi-sampled-row").hidden = !result.sampled_location;
    setText(
      "fwi-sampled-location",
      result.sampled_location
        ? `${Number(result.sampled_location.latitude).toFixed(4)}, ${Number(result.sampled_location.longitude).toFixed(4)}`
        : "—",
    );
    setText("fwi-source", result.source || "—");
    setText("fwi-sampling", result.sampling || "point query");
    setText("fwi-coverage", result.coverage || "No area layer available");
    $("fwi-statistics-row").hidden = !result.statistics;
    setText(
      "fwi-statistics",
      result.statistics
        ? `${number(result.statistics.cell_count)} cells · min ${number(result.statistics.min)} · mean ${number(result.statistics.mean)} · max ${number(result.statistics.max)}`
        : "—",
    );
    setText(
      "fwi-retrieved",
      result.retrieval_time
        ? new Date(result.retrieval_time).toLocaleString()
        : "—",
    );
    setText("fwi-message", result.message || "");
    if (!loaded)
      setText(
        "results-description",
        "FWI area layer with a numerical value reported at its centre.",
      );
    $("export").disabled = false;
    $("export").hidden = false;
  }
  function renderLegend() {
    $("map-legend").hidden = overlays.size === 0;
    const container = $("legend-content");
    container.replaceChildren();
    function row(color, label) {
      const line = document.createElement("div");
      line.className = "legend-row";
      const swatch = document.createElement("span");
      swatch.className = "legend-swatch";
      swatch.style.backgroundColor = color;
      const text = document.createElement("span");
      text.textContent = label;
      line.append(swatch, text);
      container.append(line);
    }
    if (overlays.has("fire")) {
      if (loaded?.mode === "events") {
        row("#e58149", "Fire event polygons");
      } else {
        const title = document.createElement("p");
        title.textContent = "FIRMS detections · size by FRP";
        const sizeScale = document.createElement("div");
        sizeScale.className = "legend-size-scale";
        for (const value of [10, 50, 100]) {
          const item = document.createElement("span");
          item.className = "legend-size-item";
          const dot = document.createElement("i");
          dot.className = "legend-size-dot";
          const diameter = firePointRadius({ frp: value }) * 2;
          dot.style.width = `${diameter}px`;
          dot.style.height = `${diameter}px`;
          const label = document.createElement("small");
          label.textContent = `${value} MW`;
          item.append(dot, label);
          sizeScale.append(item);
        }
        const note = document.createElement("p");
        note.className = "legend-note";
        const filters = [];
        if (!$("frp-filter").hidden && Number($("frp-threshold").value) > 0)
          filters.push(`FRP ≥ ${number(frpThresholdFromSlider())} MW`);
        if (!$("date-filter").hidden && Number($("date-threshold").value) > 0)
          filters.push(`date from ${filterDateLabel(dateThresholdFromSlider())}`);
        note.textContent = filters.length
          ? `Showing ${filters.join(" and ")}; point area scales with FRP and colour shows observation date.`
          : "Point area scales with FRP; Turbo colour shows observation date.";
        container.append(title, sizeScale, note);
        for (const item of fireDateLegendIntervals()) row(item.color, item.label);
      }
    }
    if (overlays.has("satellite")) {
      const note = document.createElement("p");
      note.textContent = `${acquisitionLabel(selectedAcquisition || {})} · ${satelliteStyle === "swir" ? "SWIR" : "True colour"} · ${satelliteCoverageLabel()} · ${Math.round(Number($("satellite-opacity").value))}% opacity`;
      container.append(note);
    }
    if (overlays.has("fwi") && fwiResult) {
      const title = document.createElement("p");
      title.textContent = `FWI area layer · centre ${number(fwiResult.fwi)}`;
      container.append(title);
      if (fwiResult.statistics) {
        const gradient = document.createElement("div");
        gradient.className = "legend-gradient fwi-gradient";
        const labels = document.createElement("p");
        labels.textContent = `${number(fwiResult.statistics.min)} · Lower → ${number(fwiResult.statistics.max)} · Higher`;
        container.append(gradient, labels);
      } else {
        const note = document.createElement("p");
        note.textContent = "Provider-rendered GWIS WMS · colours are not sampled values";
        container.append(note);
      }
    }
    if (overlays.has("districts"))
      row("#425951", "District boundaries · hover for name");
    if (overlays.has("hazard") && hazardStyle) {
      const title = document.createElement("p");
      title.textContent = `Hazard index · ${hazardStyle.year}`;
      const gradient = document.createElement("div");
      gradient.className = "legend-gradient";
      gradient.style.background = `linear-gradient(to right, ${hazardStyle.colors.join(",")})`;
      const labels = document.createElement("p");
      labels.textContent = `${hazardStyle.min} · Lower → ${hazardStyle.max} · Higher`;
      container.append(title, gradient, labels);
    }
    if (overlays.has("recovery"))
      row(
        recoveryStyle?.color || "#2f8f5b",
        `Recovery status${recoveryStyle ? ` · ${recoveryStyle.year}` : ""}`,
      );
    if (overlays.has("burn"))
      row(
        burnStyle?.color || "#c2410c",
        `Burned area${burnPeriod ? ` · ${burnPeriod === "yearly" && burnStyle ? burnStyle.year : burnPeriod}` : ""}`,
      );
    if (overlays.has("fuel")) {
      const p = document.createElement("p");
      p.textContent = "Vegetation fuel · GWIS source colors";
      container.append(p);
    }
    if (overlays.has("land")) {
      const title = document.createElement("p");
      title.textContent = "SLIM land-cover classes";
      container.append(title);
      palette
        .filter((entry) => entry.alpha > 0)
        .forEach((entry) => row(entry.color, entry.label));
      if (!palette.length) {
        const p = document.createElement("p");
        p.textContent = "Class legend unavailable.";
        container.append(p);
      }
    }
    if (overlays.has("rain")) {
      const p = document.createElement("p");
      p.textContent = "Rainfall intensity";
      const gradient = document.createElement("div");
      gradient.className = "legend-gradient";
      const labels = document.createElement("p");
      labels.textContent = "0 mm → 200+ mm";
      container.append(p, gradient, labels);
    }
    if (!overlays.size)
      container.textContent = "Load layers to see their legend.";
  }
  function resetResults() {
    clearSatelliteImagery();
    loaded = null;
    fireDateScale = null;
    fwiResult = null;
    $("export").disabled = true;
    $("export").hidden = true;
    setText("detection-count", "—");
    setText("max-frp", "—");
    setText("results-badge", "0");
    $("frp-filter").hidden = true;
    $("frp-threshold").disabled = false;
    $("date-filter").hidden = true;
    $("date-threshold").disabled = false;
    setText(
      "results-description",
      "No fire observations loaded for this selection.",
    );
    $("fwi-result").hidden = true;
    $("fwi-sampled-row").hidden = true;
    $("fwi-statistics-row").hidden = true;
    $("fwi-status").removeAttribute("data-status");
    $("results-list").replaceChildren();
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    if (setupStep < 2) {
      advanceStep();
      return;
    }
    let bounds;
    try {
      bounds = readBounds();
    } catch (error) {
      setPanel("explore");
      showStep(0);
      setStatus(error.message, "error");
      setText("status", error.message);
      $("bbox").closest("details").open = true;
      if (matchMedia("(max-width: 900px)").matches) sidebar(true);
      $("bbox").focus();
      return;
    }
    try {
      validateDates();
    } catch (error) {
      setPanel("explore");
      showStep(1);
      setStatus(error.message, "error");
      setText("status", error.message);
      if (matchMedia("(max-width: 900px)").matches) sidebar(true);
      $("end").focus();
      return;
    }
    enableFullControls();
    busy = true;
    settingsDirty = false;
    const run = ++generation;
    const query = {
      bbox: $("bbox").value,
      start: $("start").value,
      end: $("end").value,
      sensor: $("sensor").value,
    };
    const mode =
      supportsFire && $("show-fire").checked ? $("fire-display").value : "none";
    const tasks = [];
    try {
      for (const el of form.querySelectorAll("input,select,button"))
        el.disabled = true;
      $("load").setAttribute("aria-busy", "true");
      setText("load-label", "Loading observations…");
      setStatus("Connecting to your selected data sources…", "loading");
      if (matchMedia("(max-width: 900px)").matches) sidebar(false, true);
      for (const layer of overlays.values()) map.removeLayer(layer);
      overlays.clear();
      layerStates.clear();
      resetResults();
      renderLegend();
      fitMapBounds(bounds);
      setText(
        "period-days",
        Math.round(
          (Date.parse(query.end) - Date.parse(query.start)) / 86400000,
        ) + 1,
      );
      setText(
        "loaded-period",
        `${dateLabel(query.start)} – ${dateLabel(query.end)}`,
      );
      const task = (key, name, action) => {
        state(key, `${name} loading`, { pending: true });
        tasks.push(
          Promise.resolve()
            .then(action)
            .catch((error) => {
              state(key, `${name}: ${error.message}`, { error: true });
            })
            .finally(() => {
              renderLegend();
              refreshStatus();
            }),
        );
      };
      if (mode !== "none")
        task("fire", "Fire activity", async () => {
          const data = await json(
            (mode === "events" ? "/api/fire-events?" : "/api/fires?") +
              new URLSearchParams(query),
          );
          if (!Array.isArray(data.features))
            throw new Error("No valid observation collection was returned.");
          configureFireDateScale(query.start, query.end);
          const layer = createFireLayer(data);
          overlays.set("fire", layer);
          loaded = { data, ...query, mode };
          renderResults(data, layer, mode);
          configureFrpFilter(data, mode);
          configureDateFilter(data, mode);
          $("export").disabled = false;
          $("export").hidden = false;
          explored = true;
          $("workspace-tabs").hidden = false;
          enableFullControls();
          state(
            "fire",
            `${number(data.features.length)} ${mode === "events" ? "event polygons" : "fire detections"}`,
          );
        });
      if ($("show-fwi").checked)
        task("fwi", "Fire Weather Index", async () => {
          const latitude = (bounds[0][0] + bounds[1][0]) / 2;
          const longitude = (bounds[0][1] + bounds[1][1]) / 2;
          const url =
            "/api/fire-weather-index?" +
            new URLSearchParams({
              date: $("fwi-date").value,
              longitude: String(longitude),
              latitude: String(latitude),
              bbox: $("bbox").value,
            });
          let result;
          try {
            result = await json(url);
          } catch (error) {
            if (error.data?.status) renderFwi(error.data);
            throw error;
          }
          renderFwi(result);
          if (result.status === "available" || result.raster_url) {
            if (result.raster_url?.includes("{z}")) {
              tiles(
                "fwi",
                "FWI area layer",
                result.raster_url,
                {
                  opacity: Number($("fwi-opacity").value) / 100,
                  attribution: "GWIS Fire Weather Index",
                },
                run,
              );
            } else if (result.raster_url && result.extent) {
              const [west, south, east, north] = result.extent;
              const layer = L.imageOverlay(
                result.raster_url,
                [[south, west], [north, east]],
                {
                  opacity: Number($("fwi-opacity").value) / 100,
                  zIndex: tileZIndex("fwi"),
                },
              );
              overlays.set("fwi", layer);
              state("fwi", "FWI numerical area layer loading", {
                pending: true,
              });
              layer.on("load", () => {
                if (run === generation)
                  state("fwi", `FWI numerical grid loaded from ${result.source}`);
              });
              layer.on("error", () => {
                if (run === generation)
                  state("fwi", "FWI numerical area layer failed", {
                    error: true,
                  });
              });
              layer.addTo(map);
            } else {
              const markerLocation = result.sampled_location || result.location;
              const marker = L.circleMarker(
                [markerLocation.latitude, markerLocation.longitude],
                {
                  radius: 7,
                  color: "#8f432c",
                  weight: 2,
                  fillColor: "#ed9453",
                  fillOpacity: 0.9,
                },
              ).addTo(map);
              overlays.set("fwi", marker);
              state("fwi", `FWI ${number(result.fwi)} loaded from ${result.source}`);
            }
          } else if (result.status === "pending") {
            state("fwi", "FWI pending in the EWDS download queue");
          } else {
            state("fwi", `FWI unavailable: ${result.message || "no numerical value"}`);
          }
          $("workspace-tabs").hidden = false;
          enableFullControls();
        });
      if ($("show-hazard").checked)
        task("hazard", "Hazard 2025", async () => {
          hazardStyle = await json("/api/hazard/style");
          tiles(
            "hazard",
            "Hazard 2025",
            "/api/hazard/tiles/{z}/{x}/{y}.png",
            {
              opacity: Number($("hazard-opacity").value) / 100,
              attribution: "SLIM hazard index 2025",
            },
            run,
          );
        });
      if ($("show-recovery").checked)
        task("recovery", "Recovery 2025", async () => {
          recoveryStyle = await json("/api/recovery/style");
          tiles(
            "recovery",
            "Recovery 2025",
            "/api/recovery/tiles/{z}/{x}/{y}.png",
            {
              opacity: Number($("recovery-opacity").value) / 100,
              attribution: "Recovery status © SLIM",
            },
            run,
          );
        });
      if ($("show-burn").checked)
        task("burn", "Burned area", async () => {
          burnStyle = await json("/api/burn/style");
          if (monthMode) {
            // Use the monthly burned-area layer before the active period's end.
            burnPeriod = previousMonth(query.end.slice(0, 7));
          } else if ($("burn-period")) {
            burnPeriod =
              $("burn-period").value === "monthly"
                ? pickBurnMonth(burnStyle.months, query.end)
                : "yearly";
          } else {
            // Restoration keeps a fixed 2025 yearly composite.
            burnPeriod = "yearly";
          }
          const name =
            burnPeriod === "yearly"
              ? `Burned area ${burnStyle.year}`
              : `Burned area ${burnPeriod}`;
          tiles(
            "burn",
            name,
            `/api/burn/tiles/{z}/{x}/{y}.png?period=${encodeURIComponent(burnPeriod)}`,
            {
              opacity: Number($("burn-opacity").value) / 100,
              attribution: "Burned area © SLIM",
            },
            run,
          );
        });
      if ($("show-districts").checked)
        task("districts", "District boundaries", async () => {
          const data = await loadDistrictGeometry();
          if (!Array.isArray(data.features))
            throw new Error("No valid district collection was returned.");
          const layer = L.geoJSON(data, {
            style: {
              color: "#425951",
              weight: 1.2,
              opacity: 0.8,
              fillOpacity: 0.01,
            },
            onEachFeature: (feature, item) => {
              const tip = document.createElement("span");
              tip.className = "district-tooltip";
              const name = document.createElement("strong");
              name.textContent =
                feature.properties?.DISTRICT || "Unnamed district";
              tip.append(name);
              const province = feature.properties?.PROVINCE;
              if (province) {
                const sub = document.createElement("small");
                sub.textContent = province;
                tip.append(sub);
              }
              item.bindTooltip(tip, {
                sticky: true,
                direction: "top",
                className: "district-tip",
                opacity: 1,
              });
            },
          }).addTo(map);
          // Share Leaflet's canvas with fire markers so an upper canvas cannot
          // swallow district clicks. Keep district fills behind observations.
          layer.bringToBack();
          overlays.set("districts", layer);
          state(
            "districts",
            `${data.features.length} district boundaries loaded`,
          );
        });
      if ($("show-land-cover").checked)
        task("land", "Land cover", async () => {
          const selection = await json(
            "/api/land-cover/selection?" +
              new URLSearchParams({ start: query.start, end: query.end }),
          );
          tiles(
            "land",
            `Land cover ${selection.year} (${selection.resolution_m} m)`,
            `/api/land-cover/tiles/${selection.year}/{z}/{x}/{y}.png`,
            {
              opacity: Number($("land-cover-opacity").value) / 100,
              attribution: `SLIM land cover ${selection.year}`,
            },
            run,
          );
        });
      if ($("show-fuel").checked)
        task("fuel", "Vegetation fuel", () =>
          tiles(
            "fuel",
            "Vegetation fuel",
            "/api/fuel/tiles/{z}/{x}/{y}.png?fuel_layer=fuel_map",
            {
              opacity: Number($("fuel-opacity").value) / 100,
              attribution: "Fuel data © European Commission JRC / GWIS",
            },
            run,
          ),
        );
      if ($("show-precip-heatmap").checked)
        task("rain", "Precipitation", async () => {
          if (!L.heatLayer)
            throw new Error(
              "The heatmap library could not load. Refresh the page to retry.",
            );
          const data = await json(
            "/api/precipitation/heatmap?" +
              new URLSearchParams({
                bbox: query.bbox,
                time_window: $("precip-heatmap-window").value,
                observation_date: query.end,
              }),
          );
          const points = data.features.map((feature) => [
            feature.geometry.coordinates[1],
            feature.geometry.coordinates[0],
            feature.properties.intensity,
          ]);
          if (points.length) {
            $("map").style.setProperty(
              "--precip-opacity",
              Number($("precip-heatmap-opacity").value) / 100,
            );
            const layer = L.heatLayer(points, {
              radius: 35,
              blur: 35,
              maxZoom: 18,
              gradient: {
                0: "#60a5fa",
                0.25: "#22d3ee",
                0.5: "#4ade80",
                0.75: "#fde047",
                1: "#f97316",
              },
            }).addTo(map);
            overlays.set("rain", layer);
          }
          state(
            "rain",
            points.length
              ? `Precipitation: ${number(points.length)} observations`
              : "No precipitation observations returned",
          );
        });
      refreshStatus();
      await Promise.allSettled(tasks);
    } catch (error) {
      console.error("Map loading failed before completion", error);
      state("workspace", `Could not prepare the map: ${error.message}`, {
        error: true,
      });
      await Promise.allSettled(tasks);
    } finally {
      busy = false;
      explored = overlays.size > 0 || Boolean(fwiResult);
      for (const el of form.querySelectorAll("input,select,button"))
        el.disabled = false;
      $("load").setAttribute("aria-busy", "false");
      showStep(setupStep);
      if (loaded || fwiResult) setPanel("activity");
      else setPanel("explore");
      const errors = [...layerStates.values()].some((item) => item.error);
      setText(
        "status",
        errors
          ? "Some data could not load. Review the settings and try again."
          : loaded
            ? loaded.data.features.length
              ? "Select an observation to find it on the map."
              : "No observations returned. Adjust your area or dates."
            : tasks.length
              ? "Map updated. Adjust your selection to explore."
              : "Select at least one layer to explore.",
      );
      selectionChanged();
      renderLegend();
      refreshStatus();
    }
  });
  form.addEventListener(
    "invalid",
    (event) => {
      setPanel("explore");
      showStep(event.target.id === "bbox" ? 0 : 1);
      if (event.target.id === "bbox")
        event.target.closest("details").open = true;
      if (matchMedia("(max-width: 900px)").matches) sidebar(true);
    },
    true,
  );
  $("export").addEventListener("click", () => {
    if (!loaded && !fwiResult) return;
    const data = loaded
      ? JSON.parse(JSON.stringify(loaded.data))
      : { type: "FeatureCollection", features: [] };
    if (fwiResult) {
      const resultLocation = fwiResult.sampled_location || fwiResult.location;
      data.features.push({
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: [
            resultLocation.longitude,
            resultLocation.latitude,
          ],
        },
        properties: {
          feature_type: "fire_weather_index",
          ...fwiResult,
        },
      });
    }
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], {
        type: "application/geo+json",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = loaded
      ? `slim-${loaded.mode}-${loaded.start}-${loaded.end}.geojson`
      : `slim-fwi-${fwiResult.date}.geojson`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
