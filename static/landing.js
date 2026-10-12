(() => {
  const icons = {
    target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 1v4m0 14v4M1 12h4m14 0h4"/>',
    fire: '<path d="M13 3c1 5-5 6-3 10 1-2 3-3 4-4 0 3 5 4 5 8a7 7 0 0 1-14 0C5 11 12 10 13 3Z"/>',
    beacon: '<circle cx="12" cy="12" r="2.5"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5.2 5.2l2.1 2.1m9.4 9.4 2.1 2.1m0-13.6-2.1 2.1m-9.4 9.4-2.1 2.1"/><circle cx="12" cy="12" r="6.5"/>',
    leaf: '<path d="M20 3C8 2 1 8 5 16s17 3 15-13ZM3 22 16 9"/>'
  };
  for (const scenario of window.SLIM_SCENARIOS) {
    const card = document.createElement('article');
    card.className = `scenario-card ${scenario.id}`;
    card.dataset.scenario = scenario.id;
    card.innerHTML = `<div class="card-top"><span class="scenario-icon"><svg viewBox="0 0 24 24" aria-hidden="true">${icons[scenario.icon]}</svg></span><span>SCENARIO ${scenario.number}</span></div><p class="phase">${scenario.phase}</p><h3>${scenario.title}</h3><p class="card-description">${scenario.description}</p><div class="available"><h4>AVAILABLE IN THE DEMO</h4><ul>${scenario.available.map(label => `<li><span aria-hidden="true">✓</span>${label}</li>`).join('')}</ul></div><details class="future-modules"><summary>Future features <span>${scenario.future.length} modules</span></summary><ul>${scenario.future.map(([name, detail, status]) => `<li><div><strong>${name}</strong><small>${detail}</small></div><span class="badge">${status}</span></li>`).join('')}</ul></details><a class="open-scenario" href="/map?scenario=${scenario.id}" aria-label="Open ${scenario.title}">Explore scenario <span aria-hidden="true">↗</span></a>`;
    document.getElementById('scenario-grid').append(card);
  }
  const cycle = document.querySelector('.cycle');
  const items = [...document.querySelectorAll('.scenario-card, .cycle-label')];
  const phaseNames = {prevention: ['Before the fire', 'Prepare'], response: ['During the fire', 'Respond'], restoration: ['After the fire', 'Restore']};
  let pointerScenario = null;
  let focusScenario = null;
  function updatePreview() {
    const id = focusScenario || pointerScenario;
    cycle.dataset.active = id || '';
    for (const item of items) item.classList.toggle('is-active', item.dataset.scenario === id);
    const [kicker, word] = phaseNames[id] || ['Fire', 'resilience'];
    document.getElementById('cycle-kicker').textContent = kicker;
    document.getElementById('cycle-word').textContent = word;
  }
  for (const item of items) {
    item.addEventListener('pointerenter', () => { pointerScenario = item.dataset.scenario; updatePreview(); });
    item.addEventListener('pointerleave', () => { pointerScenario = null; updatePreview(); });
    item.addEventListener('focusin', () => { focusScenario = item.dataset.scenario; updatePreview(); });
    item.addEventListener('focusout', event => {
      if (item.contains(event.relatedTarget)) return;
      focusScenario = null;
      updatePreview();
    });
  }
})();
