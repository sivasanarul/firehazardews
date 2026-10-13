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
  document.getElementById("fire-display").value =
    scenario.id === "response" ? "fires" : "none";
  for (const id of scenario.defaults)
    document.getElementById(id).checked = true;
  document.getElementById("hazard-card").hidden = scenario.id !== "prevention";
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
  } else {
    referenceNote.textContent =
      "District boundaries are independent of the selected dates.";
    document.getElementById("dates-step").append(referenceNote);
  }
  if (scenario.id !== "response") {
    document.getElementById("fire-card").hidden = true;
    document.getElementById("fire-display").disabled = true;
    document.querySelector("#fire-card + .field-note").hidden = true;
    document.querySelector(".context-settings").open = true;
    document.querySelector(".context-settings > .field-note").textContent =
      "Add landscape and rainfall layers for context.";
    document.getElementById("loaded-period").textContent =
      scenario.id === "prevention"
        ? "Choose an area to explore the landscape."
        : "Choose an area and period to explore the landscape.";
  }
  if (scenario.id === "restoration") {
    const note = document.createElement("p");
    note.className = "field-note";
    note.textContent =
      "Burned area shows the existing GWIS layer for your selected dates. The yearly burned-area raster and recovery map are planned features.";
    document
      .getElementById("show-burned-area")
      .closest(".layer-card")
      .append(note);
  }
})();
