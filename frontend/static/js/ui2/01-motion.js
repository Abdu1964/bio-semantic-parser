// UI v2 — motion: depth background, reveal on scroll, 3D tilt, count-up numbers, height animation.

UI2.background = () => {
  if (document.getElementById('ui2-bg')) return;
  document.body.prepend(UI2.el('div', { id: 'ui2-bg', 'aria-hidden': 'true' },
    UI2.el('i'), UI2.el('i'), UI2.el('i')));
};

// Elements fade and rise into view the first time they scroll in
UI2._revealer = null;
UI2.reveal = nodes => {
  if (UI2.reducedMotion()) return;
  UI2._revealer = UI2._revealer || new IntersectionObserver(entries => entries.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add('ui2-in'); UI2._revealer.unobserve(e.target); }
  }), { threshold: 0.08 });
  nodes.forEach((n, i) => {
    if (n.classList.contains('ui2-reveal')) return;
    n.classList.add('ui2-reveal');
    n.style.transitionDelay = `${Math.min(i, 8) * 45}ms`;
    UI2._revealer.observe(n);
  });
};

// Cards lean toward the pointer (a few degrees)
UI2.tilt = nodes => {
  if (UI2.reducedMotion()) return;
  nodes.forEach(n => {
    if (n.dataset.ui2Tilt) return;
    n.dataset.ui2Tilt = '1';
    n.addEventListener('pointermove', e => {
      const r = n.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
      n.style.transform = `perspective(700px) rotateX(${-y * 7}deg) rotateY(${x * 9}deg) translateZ(6px)`;
    });
    n.addEventListener('pointerleave', () => { n.style.transform = ''; });
  });
};

// Numbers count up from 0; text after the number (e.g. '%') is kept
UI2.countUp = (node, target, suffix = '', ms = 900) => {
  if (UI2.reducedMotion() || !isFinite(target)) { node.textContent = target + suffix; return; }
  const t0 = performance.now();
  const step = t => {
    const p = Math.min(1, (t - t0) / ms), eased = 1 - Math.pow(1 - p, 3);
    node.textContent = Math.round(target * eased) + suffix;
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
};

// Open or close an element by animating its height (display toggles at the ends; open restores the
// element's own display, so grids stay grids)
UI2.animateOpen = (node, open) => {
  if (!node) return;
  const shown = node.dataset.ui2Display || '';
  if (UI2.reducedMotion()) { node.style.display = open ? shown : 'none'; return; }
  const ease = 'cubic-bezier(.2,.8,.2,1)';
  if (open) {
    node.style.display = shown;
    node.animate([{ height: '0px', opacity: 0 }, { height: node.scrollHeight + 'px', opacity: 1 }], { duration: 380, easing: ease });
  } else {
    const a = node.animate([{ height: node.scrollHeight + 'px', opacity: 1 }, { height: '0px', opacity: 0 }], { duration: 300, easing: ease });
    a.onfinish = () => { node.style.display = 'none'; };
  }
};
