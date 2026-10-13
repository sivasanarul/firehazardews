/* Shared scenario catalogue. Future modules never request data or draw results. */
const ENVIRONMENTAL_CONTEXT = [
  "Land cover",
  "Vegetation fuel",
  "Precipitation",
];

window.SLIM_SCENARIOS = [
  {
    id: "prevention",
    number: "01",
    title: "Prevention & preparedness",
    short: "Prevention",
    phase: "Before the fire",
    description:
      "Understand the landscape. Identify hazards and prepare for the next fire season.",
    question: "Where should we prepare?",
    icon: "target",
    available: [
      "Hazard map · 2025",
      "District boundaries",
      "Land cover",
    ],
    future: [
      [
        "Risk assessment",
        "Identify areas requiring preparedness action",
        "Planned",
      ],
    ],
    defaults: ["show-hazard", "show-districts"],
    note: "Explore the 2025 hazard index by district and add land cover for context. Risk assessment is planned.",
  },
  {
    id: "response",
    number: "02",
    title: "Detection & response",
    short: "Detection",
    phase: "During the fire",
    description:
      "Locate satellite fire detections, explore burned areas and understand the surrounding landscape.",
    question: "Where is fire activity?",
    icon: "beacon",
    available: [
      "FIRMS fire detections",
      "District boundaries",
      "Burned area (SLIM · monthly & yearly)",
      ...ENVIRONMENTAL_CONTEXT,
    ],
    future: [
      ["Fire propagation", "Explore how a fire could spread", "Planned"],
      [
        "Risk assessment",
        "Identify areas requiring preparedness action",
        "Planned",
      ],
      ["Ground sensors", "Complement satellite observations", "Concept"],
    ],
    defaults: ["show-districts", "show-burn"],
    note: "Detect fires in a single 2026 month and overlay the burned-area mask of the previous month. Add environmental context as needed; propagation, risk assessment, and ground sensors are future features.",
  },
  {
    id: "restoration",
    number: "03",
    title: "Restoration & adaptation",
    short: "Restoration",
    phase: "After the fire",
    description:
      "Explore affected landscapes and build a longer-term view of recovery and adaptation.",
    question: "How does the landscape recover?",
    icon: "leaf",
    available: [
      "Recovery status · 2025",
      "Burned area · 2025",
      "District boundaries",
      "Land cover",
    ],
    future: [
      [
        "Pseudo emission",
        "“Pseudo emission” in the brief; scope to be clarified",
        "Concept",
      ],
    ],
    defaults: ["show-recovery", "show-burn", "show-districts"],
    note: "Review the 2025 recovery status, burned area, and land cover by district. Pseudo emission is a future concept.",
  },
];
