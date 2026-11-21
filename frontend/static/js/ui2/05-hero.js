// UI v2 — results hero: one tile per headline number; numbers count up, a tile opens what it counts.

// Click the existing result card whose label contains `text` (Paper Graph, Neo4j Verification…)
UI2.openCard = text => {
  const card = [...document.querySelectorAll('.out-card')].find(c => c.textContent.includes(text));
  card && card.click();
};

// Go to a section: open its tab when it has one, then bring it into view
UI2.scrollTo = id => {
  const tab = UI2.tabOf?.(id);
  if (tab && document.getElementById('ui2-tabs')) {
    UI2.showTab(tab.key);
    id = 'ui2-tabs';
  }
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
};

// Open the verification report on one metric's own detail: formula, inputs and every item behind it
UI2.openMetric = (key, title) => {
  const d = UI2.output();
  if (!d.ver_html || typeof openViewer !== 'function') return UI2.openCard('Verification');
  const frame = document.getElementById('modal-frame');
  frame.addEventListener('load', () => {
    try { frame.contentWindow.showDetail?.(key); } catch (e) { /* report from another origin */ }
  }, { once: true });
  openViewer(fileUrl(d.ver_html), title);
};

UI2.heroTiles = () => {
  const d = UI2.output();
  const pct = v => (v == null ? null : Math.round(v * 100));
  const tiles = [
    { label: 'Facts in the KG', value: d.neo4j_edges ?? d.metta_edges, sub: `${d.neo4j_nodes ?? d.metta_nodes ?? 0} entities`,
      color: '#00c9b1', open: () => UI2.openCard('Paper Graph') },
    { label: 'Waiting for review', value: UI2.reviewCards().length, sub: 'click to review',
      color: '#fbbf24', open: () => UI2.scrollTo('review-section') },
    { label: 'Precision', value: pct(d.paper_precision), suffix: '%', sub: 'facts the paper states', color: '#38bdf8',
      open: () => UI2.openMetric('precision', 'Precision — how it is calculated') },
    { label: 'Recall', value: pct(d.paper_recall), suffix: '%', sub: 'paper findings found', color: '#818cf8',
      open: () => UI2.openMetric('recall', 'Recall — how it is calculated') },
    { label: 'F1', value: pct(d.paper_f1), suffix: '%', sub: 'balance of both', color: '#c084fc',
      open: () => UI2.openMetric('f1', 'F1 — how it is calculated') },
    { label: 'Figures', value: UI2.collectFigures().length, sub: 'read by the vision model', color: '#a855f7',
      open: () => UI2.scrollTo('ui2-figs') },
  ];
  return tiles.filter(t => t.value != null && !(t.label === 'Figures' && !t.value));
};

UI2.renderHero = () => {
  const out = document.getElementById('output');
  if (!out || !out.classList.contains('show')) return;
  const tiles = UI2.heroTiles();
  if (!UI2.changed('hero', tiles.map(t => [t.label, t.value]))) return;
  const hero = UI2.el('div', { id: 'ui2-hero' }, tiles.map(t => {
    const n = UI2.el('div', { class: 'n' }, '0');
    const tile = UI2.el('div', { class: 'ui2-tile', style: { '--ui2-c': t.color }, onclick: t.open, title: t.sub },
      UI2.el('div', { class: 'glow' }), n, UI2.el('div', { class: 'l' }, t.label), UI2.el('div', { class: 's' }, t.sub));
    UI2.countUp(n, t.value, t.suffix || '');
    return tile;
  }));
  const old = document.getElementById('ui2-hero');
  old ? old.replaceWith(hero) : out.prepend(hero);
  UI2.tilt([...hero.children]);
  UI2.reveal([...hero.children]);
};
