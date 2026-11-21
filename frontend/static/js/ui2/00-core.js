// UI v2 — shared helpers. Every UI v2 module adds its functions to the UI2 namespace.
window.UI2 = window.UI2 || {};

UI2.reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

UI2.esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// el('div', {class: 'x', onclick: fn}, child, 'text', …) → element
UI2.el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k === 'style' && typeof v === 'object')
      for (const [p, val] of Object.entries(v)) p.startsWith('--') ? node.style.setProperty(p, val) : (node.style[p] = val);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) node.append(c.nodeType ? c : document.createTextNode(c));
  return node;
};

// The results data the page already holds (10-renderers.js keeps it in _outputData)
UI2.output = () => (typeof _outputData !== 'undefined' && _outputData) || window._outputData || {};

// Run fn after DOM changes inside target settle (one call per burst of changes)
UI2.watch = (target, fn, wait = 120, subtree = true) => {
  if (!target) return;
  let timer;
  new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(fn, wait); })
    .observe(target, { childList: true, subtree });
};

// True when `key` differs from the last one stored under `name` (so a part rebuilds only on real change)
UI2._last = {};
UI2.changed = (name, key) => {
  const k = JSON.stringify(key);
  if (UI2._last[name] === k) return false;
  UI2._last[name] = k;
  return true;
};
