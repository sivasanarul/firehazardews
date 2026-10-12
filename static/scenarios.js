/* Shared scenario catalogue. Future modules never request data or draw results. */
window.SLIM_SCENARIOS = [
  {
    id: 'prevention', number: '01', title: 'Prevention & preparedness', phase: 'Before the fire',
    description: 'Understand the landscape. Identify hazards and prepare for the next fire season.',
    question: 'Where should we prepare?', icon: 'target',
    available: ['Land cover', 'Vegetation fuel', 'Precipitation'],
    future: [
      ['Hazard map', 'Raster layer · integration pending', 'Planned'],
      ['Annual impact assessment', 'Evaluate who is affected, by year', 'Planned'],
      ['Risk assessment', 'Not yet available; scope to be defined', 'Planned']
    ],
    defaults: ['show-land-cover', 'show-fuel'],
    note: 'Explore land cover and fuel now. Hazard mapping and annual impact assessment are future features.'
  },
  {
    id: 'response', number: '02', title: 'Detection & response', phase: 'During the fire',
    description: 'Locate satellite fire detections, explore burned areas and understand the surrounding landscape.',
    question: 'Where is fire activity?', icon: 'beacon',
    available: ['FIRMS fire detections', 'Burned area', 'Land cover'],
    future: [
      ['Monthly burned-area analysis', 'Monthly raster summaries', 'Planned'],
      ['Fire propagation', 'Explore how a fire could spread', 'Planned'],
      ['Incident impact assessment', 'Evaluate who is affected, ad hoc', 'Planned'],
      ['Ground sensors', 'Complement satellite observations', 'Concept'],
      ['Risk assessment', 'Not yet available; scope to be defined', 'Planned']
    ],
    defaults: [],
    note: 'The existing fire explorer. Monthly analysis, propagation and impact assessment are future features.'
  },
  {
    id: 'restoration', number: '03', title: 'Restoration & adaptation', phase: 'After the fire',
    description: 'Explore affected landscapes and build a longer-term view of recovery and adaptation.',
    question: 'How does the landscape recover?', icon: 'leaf',
    available: ['Burned area', 'Land cover'],
    future: [
      ['Recovery map', 'Recovery raster layer · integration pending', 'Planned'],
      ['Burned area yearly (raster)', 'Yearly raster summaries', 'Planned'],
      ['Emissions estimation', '“Pseudo emission” in the brief; scope to be clarified', 'Concept'],
      ['Carbon assessment', 'Explore carbon impacts', 'Concept']
    ],
    defaults: ['show-burned-area', 'show-land-cover'],
    note: 'Explore burned area and land cover now. The recovery map and yearly burned-area raster are planned features.'
  }
];
