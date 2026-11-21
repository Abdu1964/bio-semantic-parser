// UI v2 — human review: compact rows (all collapsed), a sticky toolbar with reason filters, search and
// expand / collapse all, animated open/close, and feedback when a card is approved or rejected.

// Why a card is in review, from the gate's own wording and the card's verdict
UI2.REASONS = {
  contradiction: { label: 'Contradiction', color: '#f87171' },
  doubtful:      { label: 'Doubtful',      color: '#fb923c' },
  figure:        { label: 'Figure',        color: '#a855f7' },
  missing_id:    { label: 'Missing ID',    color: '#f59e0b' },
  rejected:      { label: 'Rejected',      color: '#f87171' },
  needs_review:  { label: 'Needs review',  color: '#fbbf24' },
  not_validated: { label: 'Not validated', color: '#94a3b8' },
  other:         { label: 'Other',         color: '#38bdf8' },
};

UI2.reasonOf = card => {
  const text = card.textContent.toLowerCase();
  if (text.includes('contradiction')) return 'contradiction';
  if (text.includes('unlikely or disputed')) return 'doubtful';
  if (card.querySelector('img') || text.includes('read from figure')) return 'figure';
  return { 'ID?': 'missing_id', REJECT: 'rejected', REVIEW: 'needs_review', SKIPPED: 'not_validated' }[card.dataset.verdict] || 'other';
};

UI2.reviewCards = () => [...document.querySelectorAll('#review-cards > div[id^="rc-"]')];

UI2._filter = { reason: 'all', query: '' };

UI2.applyReviewFilter = () => {
  const { reason, query } = UI2._filter;
  UI2.reviewCards().forEach(card => {
    const show = (reason === 'all' || card.dataset.ui2Reason === reason)
              && (!query || card.textContent.toLowerCase().includes(query));
    card.classList.toggle('ui2-hidden', !show);
  });
};

// Toolbar: reason chips with counts, search, expand / collapse all
UI2.renderReviewBar = () => {
  const cards = UI2.reviewCards();
  const counts = {};
  cards.forEach(c => { counts[c.dataset.ui2Reason] = (counts[c.dataset.ui2Reason] || 0) + 1; });
  const chip = (key, label, n, color) => UI2.el('span', {
    class: 'ui2-chip' + (UI2._filter.reason === key ? ' on' : ''), style: { '--ui2-c': color },
    onclick: () => { UI2._filter.reason = key; UI2.renderReviewBar(); UI2.applyReviewFilter(); },
  }, label, UI2.el('b', {}, String(n)));
  const search = UI2.el('input', { id: 'ui2-review-search', type: 'search', placeholder: 'Search facts, entities, reasons…',
    value: UI2._filter.query, oninput: e => { UI2._filter.query = e.target.value.toLowerCase().trim(); UI2.applyReviewFilter(); } });
  const bar = UI2.el('div', { id: 'ui2-review-bar' },
    chip('all', 'All', cards.length, '#00c9b1'),
    Object.entries(UI2.REASONS).filter(([k]) => counts[k]).map(([k, r]) => chip(k, r.label, counts[k], r.color)),
    search,
    UI2.el('button', { class: 'ui2-btn', onclick: () => UI2.setAllCards(true) }, '⤢ Expand all'),
    UI2.el('button', { class: 'ui2-btn', onclick: () => UI2.setAllCards(false) }, '⤡ Collapse all'));
  const old = document.getElementById('ui2-review-bar');
  old ? old.replaceWith(bar) : document.getElementById('review-cards').before(bar);
  if (document.activeElement?.id !== 'ui2-review-search' && UI2._filter.query) bar.querySelector('input').value = UI2._filter.query;
};

// Open or close one card with animation (replaces the instant toggle of 10-renderers.js)
UI2.setCard = (i, open) => {
  const body = document.getElementById(`rc-body-${i}`), chev = document.getElementById(`rc-chevron-${i}`);
  if (!body || (body.style.display !== 'none') === open) return;
  UI2.animateOpen(body, open);
  if (chev) chev.textContent = open ? '▲' : '▼';
};

UI2.setAllCards = open => UI2.reviewCards().filter(c => !c.classList.contains('ui2-hidden')).forEach((c, k) =>
  setTimeout(() => UI2.setCard(c.id.replace('rc-', ''), open), UI2.reducedMotion() ? 0 : k * 40));

// Each new card: a reason chip in its header, collapsed, rises into view
UI2.enhanceReviewCards = () => {
  const fresh = UI2.reviewCards().filter(c => !c.dataset.ui2Reason);
  fresh.forEach(card => {
    const key = UI2.reasonOf(card), r = UI2.REASONS[key];
    card.dataset.ui2Reason = key;
    const head = card.firstElementChild;
    head && head.prepend(UI2.el('span', { class: 'ui2-reason', style: { '--ui2-c': r.color } }, r.label));
    const i = card.id.replace('rc-', '');
    const body = document.getElementById(`rc-body-${i}`);
    if (body) { body.style.display = 'none'; const ch = document.getElementById(`rc-chevron-${i}`); if (ch) ch.textContent = '▼'; }
  });
  if (!fresh.length) return;
  UI2.wrapReviewSection();
  UI2.renderReviewBar();
  UI2.applyReviewFilter();
  UI2.reveal(fresh);
};

// The review title becomes a collapsible header with a live count
UI2.wrapReviewSection = () => {
  const sec = document.getElementById('review-section');
  if (!sec || document.getElementById('ui2-review-body')) return UI2.updateReviewCount();
  const title = sec.firstElementChild;
  const body = UI2.el('div', { id: 'ui2-review-body' });
  body.append(document.getElementById('review-cards'));
  const head = UI2.sectionHeader('⚠ Human review', UI2.reviewCards().length, body, '#fbbf24');
  head.id = 'ui2-review-head';
  title.replaceWith(head);
  head.after(body);
};

UI2.updateReviewCount = () => {
  const badge = document.querySelector('#ui2-review-head .ui2-badge');
  if (badge) badge.textContent = String(UI2.reviewCards().length);
};

// Animated toggle and approve / reject feedback, on top of the existing handlers
UI2.hookReviewHandlers = () => {
  window.toggleReviewCard = i => {
    const body = document.getElementById(`rc-body-${i}`);
    if (body) UI2.setCard(i, body.style.display === 'none');
  };
  const original = window.reviewAction;
  if (typeof original !== 'function' || original.ui2) return;
  const wrapped = async (idx, btn, action) => {
    const card = document.getElementById(`rc-${idx}`);
    card && card.classList.add('ui2-acting');
    try { return await original(idx, btn, action); }
    finally {
      if (card) { card.classList.remove('ui2-acting'); card.classList.add(action === 'approve' ? 'ui2-done-ok' : 'ui2-done-bad'); }
      UI2.updateReviewCount();
    }
  };
  wrapped.ui2 = true;
  window.reviewAction = wrapped;
};
