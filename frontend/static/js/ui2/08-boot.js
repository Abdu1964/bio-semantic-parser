// UI v2 — start: background, handlers, and re-enhance the results whenever the page renders new ones.

UI2.refreshResults = () => {
  UI2.enhanceReviewCards();
  UI2.updateReviewCount();
  UI2.renderGallery();
  UI2.renderHero();
  UI2.tilt([...document.querySelectorAll('.out-card')]);
  UI2.reveal([...document.querySelectorAll('.out-card')]);
  UI2.syncDock();
  UI2.renderTabs();
};

UI2.boot = () => {
  UI2.background();
  UI2.hookReviewHandlers();
  UI2.figureClicks();
  UI2.hookCardClicks();
  ['out-cards', 'review-cards', 'review-section', 'pln-cards'].forEach(id => UI2.watch(document.getElementById(id), UI2.refreshResults));
  const host = document.getElementById('review-section')?.parentNode;          // the auto-added figure list lands here
  UI2.watch(host, UI2.refreshResults, 300, false);
  new MutationObserver(UI2.refreshResults).observe(document.getElementById('output'), { attributes: true, attributeFilter: ['class'] });
  ['commit-section', 'page-pipeline'].forEach(id => {                           // the dock follows these two
    const node = document.getElementById(id);
    node && new MutationObserver(UI2.syncDock).observe(node, { attributes: true, attributeFilter: ['style'] });
  });
  ['review-section', 'ver-content'].forEach(id => {                             // tabs appear as these get shown
    const node = document.getElementById(id);
    node && new MutationObserver(UI2.refreshResults).observe(node, { attributes: true, attributeFilter: ['style'] });
  });
  window.addEventListener('resize', UI2.moveGlider);
  UI2.refreshResults();
};

document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', UI2.boot) : UI2.boot();
