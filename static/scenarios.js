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
    title: "Detection",
    short: "Detection",
    phase: "During the fire",
    description:
      "Locate satellite fire detections, explore burned areas and understand the surrounding landscape.",
    question: "Where is fire activity?",
    icon: "beacon",
    available: [
      "FIRMS fire detections",
      "Numerical Fire Weather Index",
      "District boundaries",
      "Burned area (SLIM · monthly & yearly)",
      "Land cover",
      "Vegetation fuel",
    ],
    future: [
      ["Ground sensors", "Complement satellite observations", "Concept"],
    ],
    defaults: ["show-districts", "show-burn"],
    note: "Detect fires using a recent, monthly, or custom period and overlay the burned-area mask preceding the selected period. Add environmental context as needed; ground sensors are a future feature.",
  },
  {
    id: "prediction",
    number: "03",
    title: "Prediction",
    short: "Prediction",
    phase: "Ahead of fire spread",
    description:
      "Explore the landscape context needed to anticipate fire spread and future risk.",
    question: "Where could fire spread next?",
    icon: "forecast",
    available: [
      "District boundaries",
      "Burned area (SLIM · monthly & yearly)",
      ...ENVIRONMENTAL_CONTEXT,
    ],
    future: [
      ["Fire propagation", "Explore how a fire could spread", "Planned"],
      [
        "Risk prediction",
        "Identify landscapes at elevated future risk",
        "Planned",
      ],
    ],
    defaults: ["show-burn", "show-districts"],
    note: "Review burned-area and landscape context for a selected period. Fire propagation and risk prediction are planned modules and do not yet produce results.",
  },
  {
    id: "restoration",
    number: "04",
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
