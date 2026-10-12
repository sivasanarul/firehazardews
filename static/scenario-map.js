(() => {
  const requested = new URLSearchParams(location.search).get('scenario');
  const scenario = window.SLIM_SCENARIOS.find(item => item.id === requested) || window.SLIM_SCENARIOS[1];
  window.SLIM_ACTIVE_SCENARIO = scenario;
  document.body.dataset.scenario = scenario.id;
  document.title = `SLIM · ${scenario.title}`;
  document.querySelector('.workspace-title h1').textContent = scenario.title;
  document.getElementById('workspace-emblem').innerHTML = `<svg><use href="#i-${scenario.icon}"/></svg>`;
  const context = document.getElementById('scenario-context');
  const shortNames = ['Prepare', 'Respond', 'Restore'];
  context.innerHTML = `<p class="eyebrow">SCENARIO ${scenario.number} · ${scenario.phase}</p><p>${scenario.description}</p><nav class="scenario-navigation" aria-label="Scenario workspaces">${window.SLIM_SCENARIOS.map((item, index) => `<a href="/map?scenario=${item.id}" aria-label="${item.title}" ${item.id === scenario.id ? 'aria-current="page"' : ''}>${item.number} ${shortNames[index]}</a>`).join('')}</nav>`;
  const roadmap = document.createElement('details');
  roadmap.className = 'scenario-roadmap';
  roadmap.innerHTML = `<summary>Future features <span>${scenario.future.length} modules</span></summary><p>${scenario.note} These modules are preview only.</p><ul>${scenario.future.map(([name, detail, status]) => `<li><div><strong>${name}</strong><small>${detail}</small></div><span>${status}</span></li>`).join('')}</ul>`;
  document.getElementById('explore-panel').append(roadmap);
  document.getElementById('fire-display').value = scenario.id === 'response' ? 'fires' : 'none';
  for (const id of scenario.defaults) document.getElementById(id).checked = true;
  if (scenario.id !== 'response') {
    document.querySelector('.context-settings').open = true;
    document.querySelector('#fire-card + .field-note').textContent = 'Fire activity is optional in this scenario. Combine the available layers below to explore the landscape.';
    document.getElementById('loaded-period').textContent = 'Choose an area and period to explore the landscape.';
  }
  if (scenario.id === 'restoration') {
    const note = document.createElement('p');
    note.className = 'field-note';
    note.textContent = 'Burned area shows the existing GWIS layer for your selected dates. The yearly burned-area raster and recovery map are planned features.';
    document.getElementById('show-burned-area').closest('.layer-card').append(note);
  }
})();
