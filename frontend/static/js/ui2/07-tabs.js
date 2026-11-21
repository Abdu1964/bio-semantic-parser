// UI v2 — results workspace: the long sections (review, figures, verification) become tabs, one shown at a time.
// Sections stay where they are in the page; a tab only hides the others (class ui2-off).

UI2.TABS = [
  { key: 'review',  label: 'Human review', icon: '⚠', color: '#fbbf24', ids: ['review-section'],
    count: () => UI2.reviewCards().length },
  { key: 'figures', label: 'Figures',      icon: '🖼', color: '#a855f7', ids: ['ui2-figs', 'figure-auto-section'],
    count: () => UI2.collectFigures().length },
  { key: 'verify',  label: 'Verification', icon: '✓', color: '#4ade80', ids: ['ver-content'], count: () => null },
];

UI2._tab = null;

UI2._tabNodes = tab => tab.ids.map(id => document.getElementById(id)).filter(Boolean);
UI2._tabReady = tab => UI2._tabNodes(tab).some(n => n.style.display !== 'none');

UI2.tabOf = id => UI2.TABS.find(t => t.ids.includes(id));

// Show one tab; the others' sections get hidden. The incoming section rises in.
UI2.showTab = key => {
  UI2._tab = key;
  UI2.TABS.forEach(t => UI2._tabNodes(t).forEach(n => {
    const off = t.key !== key;
    if (!off && n.classList.contains('ui2-off') && !UI2.reducedMotion())
      n.animate([{ opacity: 0, transform: 'perspective(900px) rotateX(7deg) translateY(16px)' }, { opacity: 1, transform: 'none' }],
                { duration: 420, easing: 'cubic-bezier(.2,.8,.2,1)' });
    n.classList.toggle('ui2-off', off);
  }));
  const bar = document.getElementById('ui2-tabs');
  bar?.querySelectorAll('.tab').forEach(b => b.classList.toggle('on', b.dataset.key === key));
  UI2.moveGlider();
};

UI2.moveGlider = () => {
  const on = document.querySelector('#ui2-tabs .tab.on'), glide = document.querySelector('#ui2-tabs .glide');
  if (!on || !glide) return;
  glide.style.width = on.offsetWidth + 'px';
  glide.style.transform = `translateX(${on.offsetLeft}px)`;
  glide.style.setProperty('--ui2-c', on.style.getPropertyValue('--ui2-c'));
};

UI2.renderTabs = () => {
  const out = document.getElementById('output');
  const ready = out && out.classList.contains('show') ? UI2.TABS.filter(UI2._tabReady) : [];
  const counts = ready.map(t => t.count());
  if (!UI2.changed('tabs', [ready.map(t => t.key), counts, UI2.dockAvailable()])) return;
  document.getElementById('ui2-tabs')?.remove();
  document.body.classList.toggle('ui2-tabbed', ready.length > 0);
  if (!ready.length) {
    UI2.TABS.forEach(t => UI2._tabNodes(t).forEach(n => n.classList.remove('ui2-off')));
    return;
  }
  const bar = UI2.el('div', { id: 'ui2-tabs' }, UI2.el('span', { class: 'glide' }),
    ready.map((t, i) => UI2.el('button', { class: 'tab', 'data-key': t.key, style: { '--ui2-c': t.color }, onclick: () => UI2.showTab(t.key) },
      UI2.el('span', { class: 'ic' }, t.icon), t.label,
      counts[i] != null ? UI2.el('b', {}, String(counts[i])) : null)),
    UI2.dockAvailable() ? UI2.el('button', { class: 'next', onclick: () => UI2.setDock(true) }, '✦ Next steps') : null);
  document.getElementById('ver-content').before(bar);
  if (!ready.some(t => t.key === UI2._tab)) UI2._tab = ready[0].key;
  UI2.showTab(UI2._tab);
};

// The Verification result card used to scroll down to the summary; now it opens its tab
UI2.hookCardClicks = () => {
  const original = window.onCardClick;
  if (typeof original !== 'function' || original.ui2) return;
  const wrapped = i => {
    original(i);
    if (window._outCards?.[i]?.inline) UI2.scrollTo('ver-content');
  };
  wrapped.ui2 = true;
  window.onCardClick = wrapped;
};
