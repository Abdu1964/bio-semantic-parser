// UI v2 — lightbox: a figure full screen with zoom (wheel / double-click) and pan (drag), and its facts beside it.

UI2.lightbox = (() => {
  let box, img, scale = 1, x = 0, y = 0, drag = null;

  const apply = () => { img.style.transform = `translate(${x}px, ${y}px) scale(${scale})`; };
  const reset = () => { scale = 1; x = 0; y = 0; apply(); };

  function build() {
    img = UI2.el('img', { alt: '' });
    const stage = UI2.el('div', { class: 'stage' }, img);
    stage.addEventListener('wheel', e => {
      e.preventDefault();
      scale = Math.min(6, Math.max(1, scale * (e.deltaY < 0 ? 1.12 : 0.89)));
      if (scale === 1) { x = 0; y = 0; }
      apply();
    }, { passive: false });
    stage.addEventListener('dblclick', () => { scale = scale > 1 ? 1 : 2.5; if (scale === 1) { x = 0; y = 0; } apply(); });
    stage.addEventListener('pointerdown', e => { drag = { sx: e.clientX - x, sy: e.clientY - y }; });
    window.addEventListener('pointermove', e => { if (drag && scale > 1) { x = e.clientX - drag.sx; y = e.clientY - drag.sy; apply(); } });
    window.addEventListener('pointerup', () => { drag = null; });
    box = UI2.el('div', { id: 'ui2-lb', role: 'dialog', 'aria-modal': 'true' },
      UI2.el('button', { class: 'x', 'aria-label': 'Close', onclick: close }, '✕'),
      stage, UI2.el('div', { class: 'side' }));
    box.addEventListener('click', e => { if (e.target === box) close(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
    document.body.append(box);
  }

  function open(src, title, facts = []) {
    if (!box) build();
    img.src = src;
    reset();
    const side = box.querySelector('.side');
    side.replaceChildren(
      UI2.el('h3', {}, title || 'Figure'),
      UI2.el('div', { class: 'hint' }, 'Scroll or double-click to zoom · drag to move · Esc to close'),
      ...(facts.length ? facts.map(f => UI2.el('div', { class: 'fact' }, f))
                       : [UI2.el('div', { class: 'hint' }, 'No facts from this figure are listed on this page.')]));
    box.classList.add('open');
  }

  function close() { box && box.classList.remove('open'); }

  return { open, close };
})();
