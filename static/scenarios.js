/* Shared scenario catalogue. Future modules never request data or draw results. */
window.SLIM_SCENARIOS = [
  {
    id: "prevention",
    number: "01",
    title: "Prevention & preparedness",
    phase: "Before the fire",
    description:
      "Understand the landscape. Identify hazards and prepare for the next fire season.",
    question: "Where should we prepare?",
    icon: "target",
    available: [
      "Hazard map · 2025",
      "District boundaries",
      "Land cover",
      "Vegetation fuel",
      "Precipitation",
    ],
    future: [
      [
        "Annual impact assessment",
        "Evaluate who is affected, by year",
        "Planned",
      ],
      ["Risk assessment", "Not yet available; scope to be defined", "Planned"],
    ],
    defaults: ["show-hazard", "show-districts"],
    note: "Explore the 2025 hazard index by district. Add land cover, fuel or rainfall for context. Risk and annual impact assessment are planned.",
  },
  {
    id: "response",
    number: "02",
    title: "Detection & response",
    phase: "During the fire",
    description:
      "Locate satellite fire detections, explore burned areas and understand the surrounding landscape.",
    question: "Where is fire activity?",
    icon: "beacon",
    available: [
      "FIRMS fire detections",
      "District boundaries",
      "Burned area (SLIM · monthly & yearly)",
      "Land cover",
    ],
    future: [
      ["Fire propagation", "Explore how a fire could spread", "Planned"],
      [
        "Incident impact assessment",
        "Evaluate who is affected, ad hoc",
        "Planned",
      ],
      ["Ground sensors", "Complement satellite observations", "Concept"],
      ["Risk assessment", "Not yet available; scope to be defined", "Planned"],
    ],
    defaults: ["show-districts", "show-burn"],
    note: "Detect fires in a single 2026 month and overlay the burned-area mask of the previous month. Propagation and impact assessment are future features.",
  },
  {
    id: "restoration",
    number: "03",
    title: "Restoration & adaptation",
    phase: "After the fire",
    description:
      "Explore affected landscapes and build a longer-term view of recovery and adaptation.",
    question: "How does the landscape recover?",
    icon: "leaf",
    available: [
      "Burned area (SLIM · monthly & yearly)",
      "District boundaries",
      "Land cover",
    ],
    future: [
      [
        "Recovery map",
        "Recovery raster layer · integration pending",
        "Planned",
      ],
      [
        "Emissions estimation",
        "“Pseudo emission” in the brief; scope to be clarified",
        "Concept",
      ],
      ["Carbon assessment", "Explore carbon impacts", "Concept"],
    ],
    defaults: ["show-burn", "show-land-cover", "show-districts"],
    note: "Explore the SLIM 2025 burned-area mask and land cover now. The recovery map is a planned feature.",
  },
];
