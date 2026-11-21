// UI v2 — action dock: the commit / PLN steps move out of the page into a side panel that slides in.
// The existing #commit-section node is moved as is, so its ids and handlers keep working.

UI2.WIDE = '(min-width: 1500px)';            // wide screens: the page slides left and the dock stays beside it

UI2.dockAvailable = () => {
  const sec = document.getElementById('commit-section');
  const page = document.getElementById('page-pipeline');
  return !!sec && sec.style.display !== 'none' && (!page || page.style.display !== 'none');
};

UI2.setDock = open => {
  const dock = document.getElementById('ui2-dock');
  if (!dock) return;
  open = open && UI2.dockAvailable();
  dock.classList.toggle('open', open);
  document.body.classList.toggle('ui2-docked', open && window.matchMedia(UI2.WIDE).matches);
  document.getElementById('ui2-dock-tab')?.classList.toggle('show', !open && UI2.dockAvailable());
};

UI2.buildDock = () => {
  const sec = document.getElementById('commit-section');
  if (!sec || document.getElementById('ui2-dock')) return;
  const dock = UI2.el('aside', { id: 'ui2-dock', 'aria-label': 'Next steps' },
    UI2.el('div', { class: 'top' },
      UI2.el('div', {}, UI2.el('h3', {}, 'Next steps'), UI2.el('p', {}, 'Save this paper to the unified graph, then reason over it')),
      UI2.el('button', { class: 'x', title: 'Close (Esc)', onclick: () => UI2.setDock(false) }, '×')),
    UI2.el('div', { class: 'body' }, sec));
  const tab = UI2.el('button', { id: 'ui2-dock-tab', onclick: () => UI2.setDock(true) }, UI2.el('span', {}, '✦ Next steps'));
  document.body.append(dock, tab);
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !document.querySelector('#ui2-lb.open')) UI2.setDock(false); });
  window.matchMedia(UI2.WIDE).addEventListener('change', () => UI2.setDock(dock.classList.contains('open')));
};

// Follow the page: show the tab when the steps become available, open the dock once on wide screens
UI2._dockSeen = false;
UI2.syncDock = () => {
  UI2.buildDock();
  const available = UI2.dockAvailable();
  if (available && !UI2._dockSeen) {
    UI2._dockSeen = true;
    if (window.matchMedia(UI2.WIDE).matches) return UI2.setDock(true);
  }
  if (!available) UI2._dockSeen = false;
  UI2.setDock(available && document.getElementById('ui2-dock')?.classList.contains('open'));
};
