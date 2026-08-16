// ── Stream terminal ────────────────────────────────────────────────────────────

const LAYER_COLORS = ['','--l1','--l2','--l3','--l4','--l5','--l6','--l7','--l8'];

let _currentStreamLayer = 0;

function streamHeader(layer, message) {
  const ll = document.getElementById('stream-lines');
  if (!ll) return;
  ll.querySelector('.stream-idle')?.remove();
  _currentStreamLayer = layer;

  // Update stream bar indicator
  const term = document.getElementById('stream-terminal');
  const ind  = document.getElementById('stream-layer-indicator');
  if (term) term.classList.add('active');
  if (ind) {
    ind.style.display = 'inline';
    ind.textContent   = `L${layer}`;
    ind.className     = `sl-lh-${layer}`;
  }

  const wrap = document.createElement('div');
  wrap.className  = `sl-layer-header sl-lh-${layer}`;
  wrap.id         = `slh-${layer}`;
  wrap.innerHTML = `
    <div class="lh-num" title="Layer ${layer}">${layer}</div>
    <div class="lh-name">${LAYER_ICONS[layer]} ${LAYER_NAMES[layer]}</div>
    <div class="lh-msg">${esc(message)}</div>`;
  ll.appendChild(wrap);
  ll.scrollTop = ll.scrollHeight;

  // Add/update the layer nav pill in stream bar
  let nav = document.getElementById('snav-' + layer);
  if (!nav) {
    nav = document.createElement('button');
    nav.id        = 'snav-' + layer;
    nav.className = 'stream-nav-pill';
    nav.style.cssText = `color:var(${LAYER_COLORS[layer]});border-color:var(${LAYER_COLORS[layer]});`;
    nav.textContent = layer;
    nav.title = LAYER_NAMES[layer];
    nav.onclick = () => {
      const hdr = document.getElementById('slh-' + layer);
      if (hdr) hdr.scrollIntoView({ behavior:'smooth', block:'start' });
    };
    const bar = document.querySelector('.stream-bar-title');
    if (bar) bar.appendChild(nav);
  }
}

function streamLine(message, kind) {
  const ll = document.getElementById('stream-lines');
  if (!ll) return;

  // Chunk card
  if (kind === 'chunk') {
    const parts = message.split('|');
    if (parts.length >= 4) {
      const [num, section, words, ...textParts] = parts;
      const text = textParts.join('|');
      const card = document.createElement('div');
      card.className = 'sl-chunk-card';
      card.innerHTML = `
        <span class="cc-num">#${esc(num)}</span>
        <span class="cc-section">${esc(section)}</span>
        <span class="cc-words">${esc(words)}w</span>
        <span class="cc-text">${esc(text)}</span>`;
      ll.appendChild(card);
      autoScroll(ll); return;
    }
  }

  // Step divider
  if (kind === 'step' || /^step \d/i.test(message.trim())) {
    const div = document.createElement('div');
    div.className = 'sl-step-div';
    div.textContent = message.trim();
    ll.appendChild(div);
    autoScroll(ll); return;
  }

  // Regular line
  const div = document.createElement('div');
  const cls = kind || streamClass(message);
  div.className = 'sl-' + cls;
  div.textContent = message;
  ll.appendChild(div);
  autoScroll(ll);
}

function autoScroll(el) {
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 80)
    el.scrollTop = el.scrollHeight;
}

function streamClass(msg) {
  if (!msg) return 'meta';
  const m = msg.toLowerCase();
  if (m.includes('✓') || m.includes(' ok') || m.startsWith('ok') || m.includes('done') || m.includes('generated') || m.startsWith('  ✓')) return 'ok';
  if (m.includes('coref') || m.includes('rewrite') || m.includes('before :') || m.includes('after :') || m.includes('chain')) return 'coref';
  if (m.includes('entity') || m.includes('negat') || m.includes('(gene)') || m.includes('(protein)') || m.includes('absent')) return 'entity';
  if (m.includes('→') || m.includes('conf:') || m.includes('↗') || m.includes('relation')) return 'rel';
  if (m.includes('warning') || m.includes('skip') || m.includes('fail') || m.startsWith('  ✗')) return 'warn';
  if (m.startsWith('  ') && m.includes(':')) return 'meta';
  return 'plain';
}

function _applyFullscreenSize() {
  const t  = document.getElementById('stream-terminal');
  const ll = document.getElementById('stream-lines');
  if (!t || !ll) return;
  const barH = t.querySelector('.stream-bar')?.offsetHeight || 52;
  const h    = window.screen.height || window.innerHeight;
  ll.style.height = (h - barH) + 'px';
  ll.style.maxHeight = 'none';
}

function enterFullscreen() {
  const t = document.getElementById('stream-terminal');
  if (!t) return;
  const req = t.requestFullscreen || t.webkitRequestFullscreen || t.mozRequestFullScreen || t.msRequestFullscreen;
  if (req) {
    req.call(t).then(() => {
      setTimeout(_applyFullscreenSize, 100);  // after browser applies fullscreen
    }).catch(() => {
      t.classList.add('fullscreen');
      document.body.style.overflow = 'hidden';
      setTimeout(_applyFullscreenSize, 50);
    });
  } else {
    t.classList.add('fullscreen');
    document.body.style.overflow = 'hidden';
    setTimeout(_applyFullscreenSize, 50);
  }
}

function exitFullscreen() {
  const t  = document.getElementById('stream-terminal');
  const ll = document.getElementById('stream-lines');
  if (!t) return;
  const exit = document.exitFullscreen || document.webkitExitFullscreen || document.mozCancelFullScreen || document.msExitFullscreen;
  if (document.fullscreenElement || document.webkitFullscreenElement) {
    if (exit) exit.call(document).catch(() => {});
  }
  t.classList.remove('fullscreen');
  document.body.style.overflow = '';
  // Reset inline height set by _applyFullscreenSize
  if (ll) { ll.style.height = ''; ll.style.maxHeight = ''; }
  if (viewMode === 'detail') setView('simple');
}

function toggleFullscreen() {
  const t = document.getElementById('stream-terminal');
  if (!t) return;
  if (document.fullscreenElement || document.webkitFullscreenElement) {
    exitFullscreen();
  } else {
    enterFullscreen();
  }
}

function _onFullscreenChange() {
  const t  = document.getElementById('stream-terminal');
  const ll = document.getElementById('stream-lines');
  if (!t || !ll) return;
  if (document.fullscreenElement || document.webkitFullscreenElement) {
    // Entered fullscreen — apply exact height
    setTimeout(_applyFullscreenSize, 80);
  } else {
    // Exited fullscreen — restore normal height
    t.classList.remove('fullscreen');
    document.body.style.overflow = '';
    ll.style.height   = '';
    ll.style.maxHeight = '';
  }
}
document.addEventListener('fullscreenchange',       _onFullscreenChange);
document.addEventListener('webkitfullscreenchange', _onFullscreenChange);

function streamHtml(html) {
  const ll = document.getElementById('stream-lines');
  if (!ll) return;
  ll.querySelector('.stream-idle')?.remove();
  const wrap = document.createElement('div');
  wrap.className = 'sl-rich-block';
  // Rich exports use inline styles — we just inject the pre block
  wrap.innerHTML = html;
  // Fix background for dark mode
  wrap.querySelectorAll('[style]').forEach(el => {
    const s = el.style;
    if (s.backgroundColor && (s.backgroundColor.includes('rgb(12') || s.backgroundColor.includes('#0c')))
      s.backgroundColor = 'transparent';
  });
  ll.appendChild(wrap);
  autoScroll(ll);
}

function clearStream() {
  const ll = document.getElementById('stream-lines');
  if (ll) ll.innerHTML = '<div class="stream-idle">Stream cleared.</div>';
}

function scrollStreamBottom() {
  const ll = document.getElementById('stream-lines');
  if (ll) ll.scrollTop = ll.scrollHeight;
}

