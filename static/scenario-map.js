(() => {
  const requested = new URLSearchParams(location.search).get("scenario");
  const scenario =
    window.SLIM_SCENARIOS.find((item) => item.id === requested) ||
    window.SLIM_SCENARIOS[1];
  window.SLIM_ACTIVE_SCENARIO = scenario;
  document.body.dataset.scenario = scenario.id;
  document.title = `SLIM · ${scenario.title}`;
  document.querySelector(".workspace-title h1").textContent = scenario.title;
  document.getElementById("workspace-emblem").innerHTML =
    `<svg><use href="#i-${scenario.icon}"/></svg>`;
  const context = document.getElementById("scenario-context");
  const shortNames = ["Prepare", "Respond", "Restore"];
  // The header keeps only the scenario switcher; the descriptive copy lives on
  // the landing page, so it is not repeated in the compact workspace header.
  context.innerHTML = `<nav class="scenario-navigation" aria-label="Scenario workspaces">${window.SLIM_SCENARIOS.map((item, index) => `<a href="/map?scenario=${item.id}" aria-label="${item.title}" ${item.id === scenario.id ? 'aria-current="page"' : ""}>${item.number} ${shortNames[index]}</a>`).join("")}</nav>`;
  const roadmap = document.createElement("details");
  roadmap.className = "scenario-roadmap";
  roadmap.innerHTML = `<summary>Future features <span>${scenario.future.length} modules</span></summary><p>${scenario.note} These modules are preview only.</p><ul>${scenario.future.map(([name, detail, status]) => `<li><div><strong>${name}</strong><small>${detail}</small></div><span>${status}</span></li>`).join("")}</ul>`;
  document.getElementById("explore-panel").append(roadmap);
  document.getElementById("fire-display").value = "fires";
  document.getElementById("show-fire").checked = scenario.id === "response";
  for (const id of scenario.defaults)
    document.getElementById(id).checked = true;
  document.getElementById("hazard-card").hidden = scenario.id !== "prevention";
  // SLIM recovery status is the primary (top) layer in restoration.
  const recoveryCard = document.getElementById("recovery-card");
  recoveryCard.hidden = scenario.id !== "restoration";
  // SLIM burned area: not part of prevention. Detection & response and
  // restoration both group it with the optional environmental context rather
  // than the primary layers.
  const contextSettings = document.querySelector(".context-settings");
  const burnCard = document.getElementById("burn-card");
  if (scenario.id === "prevention") {
    burnCard.hidden = true;
  } else {
    burnCard.hidden = false;
    if (contextSettings) {
      const firstCard = contextSettings.querySelector(".layer-card");
      contextSettings.insertBefore(burnCard, firstCard);
    }
  }
  // Keep the optional environmental-context panel open when a layer inside it
  // is enabled by default.
  if (
    contextSettings &&
    contextSettings.querySelector('input[type="checkbox"]:checked')
  )
    contextSettings.open = true;
  if (scenario.id === "prevention") {
    // Area is Zambia or a specific district only: no manual extent or coordinates.
    document.querySelector('#region option[value="custom"]')?.remove();
    const useMap = document.getElementById("use-map");
    if (useMap) useMap.hidden = true;
    const coordinates = document.querySelector("#area-step .coordinates");
    if (coordinates) coordinates.hidden = true;
  }
  const referenceNote = document.createElement("p");
  referenceNote.className = "field-note";
  if (scenario.id === "prevention") {
    referenceNote.textContent =
      "District boundaries and the 2025 hazard index are fixed reference layers; they do not depend on an observation period.";
    document.getElementById("layers-step").append(referenceNote);
  } else if (scenario.id === "restoration") {
    referenceNote.textContent =
      "District boundaries, the 2025 burned area and the 2025 recovery status are fixed reference layers; they do not depend on an observation period.";
    document.getElementById("layers-step").append(referenceNote);
  } else {
    referenceNote.textContent =
      "District boundaries are independent of the selected dates.";
    document.getElementById("dates-step").append(referenceNote);
  }
  if (scenario.id === "response") {
    // Detection & response reviews a single 2026 month: swap the date range for
    // a month picker and fix the burned-area layer to the previous month.
    const monthField = document.getElementById("month-field");
    if (monthField) monthField.hidden = false;
    const dateFields = document.querySelector("#dates-step .date-fields");
    if (dateFields) dateFields.hidden = true;
    const presets = document.querySelector("#dates-step .presets");
    if (presets) presets.hidden = true;
    const periodNote = document.getElementById("period-note");
    if (periodNote) periodNote.hidden = true;
    document.getElementById("period-heading").textContent = "Month";
    document.getElementById("period-subheading").textContent =
      "Choose a month to inspect.";
    const burnPeriod = document.getElementById("burn-period");
    if (burnPeriod) {
      burnPeriod.closest("label")?.remove();
      burnPeriod.remove();
    }
  } else {
    const monthField = document.getElementById("month-field");
    if (monthField) monthField.hidden = true;
  }
  if (scenario.id === "restoration") {
    // Restoration is a fixed 2025 view: no period selection and the burned-area
    // layer is always the 2025 yearly composite.
    const burnLabel = document.querySelector("#burn-card .toggle-label span");
    if (burnLabel) burnLabel.textContent = `Burned area · ${2025}`;
    const burnPeriod = document.getElementById("burn-period");
    if (burnPeriod) {
      burnPeriod.closest("label")?.remove();
      burnPeriod.remove();
    }
  }
  if (scenario.id !== "response") {
    document.getElementById("fire-card").hidden = true;
    document.getElementById("fire-display").disabled = true;
    document.querySelector(".context-settings > .field-note").textContent =
      "Add landscape and rainfall layers for context.";
    document.getElementById("loaded-period").textContent =
      scenario.id === "prevention"
        ? "Choose an area to explore the landscape."
        : "Choose an area to explore the 2025 recovery.";
  }
})();
