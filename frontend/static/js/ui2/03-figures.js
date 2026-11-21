// UI v2 — figures: a gallery of every figure the results mention, and figure images in cards open the lightbox.

// Each figure image on the page → {src, label, facts[]}, grouped by image
UI2.collectFigures = () => {
  const figs = new Map();
  document.querySelectorAll('#review-cards img, #figure-auto-section img').forEach(img => {
    const block = img.closest('div');                                   // the "Read from figure — …" box
    const head = block && block.firstElementChild ? block.firstElementChild.textContent : '';
    const label = head.replace(/^\s*Read from figure\s*[—-]\s*/i, '').trim() || 'Figure';
    const card = img.closest('[id^="rc-"]') || img.closest('details');
    const fact = card ? (card.querySelector('summary, [onclick^="toggleReviewCard"]') || card).textContent.replace(/\s+/g, ' ').trim() : '';
    const entry = figs.get(img.src) || { src: img.src, label, facts: [] };
    if (fact && !entry.facts.includes(fact)) entry.facts.push(fact.slice(0, 220));
    figs.set(img.src, entry);
  });
  return [...figs.values()];
};

// Gallery section above the human review, collapsible
UI2.renderGallery = () => {
  const figs = UI2.collectFigures();
  if (!UI2.changed('gallery', figs.map(f => [f.src, f.facts.length]))) return;
  let sec = document.getElementById('ui2-figs');
  if (!figs.length) { sec && sec.remove(); return; }
  if (!sec) {
    sec = UI2.el('div', { id: 'ui2-figs' });
    const anchor = document.getElementById('review-section');
    anchor ? anchor.before(sec) : document.getElementById('output')?.after(sec);
  }
  const grid = UI2.el('div', { id: 'ui2-fig-grid' }, figs.map(f =>
    UI2.el('div', { class: 'ui2-fig', title: f.label, onclick: () => UI2.lightbox.open(f.src, f.label, f.facts) },
      UI2.el('img', { src: f.src, loading: 'lazy', alt: f.label }),
      UI2.el('div', { class: 'cap' }, UI2.el('b', {}, f.label),
        UI2.el('span', { class: 'cnt' }, `${f.facts.length} fact${f.facts.length === 1 ? '' : 's'}`)))));
  sec.replaceChildren(UI2.sectionHeader('🖼 Figures', figs.length, grid, '#a855f7'), grid);
  UI2.reveal([...grid.children]);
  UI2.tilt([...grid.children]);
};

// Figure images inside review cards and the auto-added list open the lightbox instead of a new tab
UI2.figureClicks = () => {
  document.addEventListener('click', e => {
    const img = e.target.closest('#review-cards img, #figure-auto-section img');
    if (!img) return;
    e.preventDefault();
    e.stopPropagation();
    const fig = UI2.collectFigures().find(f => f.src === img.src);
    UI2.lightbox.open(img.src, fig ? fig.label : 'Figure', fig ? fig.facts : []);
  }, true);
};

// Collapsible header: title, count badge, chevron; toggles `body` with a height animation
UI2.sectionHeader = (title, count, body, color, startOpen = true) => {
  const head = UI2.el('div', { class: 'ui2-head' + (startOpen ? '' : ' closed') },
    UI2.el('span', { class: 'chev' }, '▾'),
    UI2.el('span', { style: { fontSize: '12px', fontWeight: '700', letterSpacing: '.07em', textTransform: 'uppercase', color } }, title),
    UI2.el('span', { class: 'ui2-badge', style: { color, borderColor: color + '66', background: color + '1f' } }, String(count)));
  if (!startOpen) body.style.display = 'none';
  head.addEventListener('click', () => {
    const open = head.classList.toggle('closed') === false;
    UI2.animateOpen(body, open);
  });
  return head;
};
