// ── Ask PLN beside the graph: backward-chaining questions over all papers; an answer shows on the graph ──

let _plnSideOpen = false, _plnSideAnswers = [];

(function () {
  const st = document.createElement('style');
  st.textContent = `
  #pln-side { width:400px; flex:none; display:flex; flex-direction:column; border-left:1px solid rgba(168,85,247,.3);
    background:linear-gradient(180deg,#0f1420,#0b0f17); color:#c9d1d9; font:13px Inter,system-ui,sans-serif; min-height:0 }
  .ps-head { display:flex; align-items:center; gap:8px; padding:12px 14px; border-bottom:1px solid rgba(255,255,255,.06) }
  .ps-title { font-weight:700; color:#e9d5ff } .ps-sub { color:#6e7681; font-size:12px }
  .ps-x { margin-left:auto; background:none; border:1px solid rgba(255,255,255,.12); color:#8b949e; border-radius:8px; width:28px; height:26px; cursor:pointer }
  .ps-ask { padding:12px 14px; display:flex; flex-direction:column; gap:8px; border-bottom:1px solid rgba(255,255,255,.06) }
  .ps-q { width:100%; box-sizing:border-box; min-height:60px; resize:vertical; background:#0d1117; color:#e6edf3; border:1px solid #30363d;
    border-radius:10px; padding:9px 11px; font:inherit }
  .ps-q:focus { outline:none; border-color:#a855f7; box-shadow:0 0 0 3px rgba(168,85,247,.18) }
  .ps-go { background:linear-gradient(135deg,#7c3aed,#a855f7); color:#fff; border:none; border-radius:10px; padding:9px; font-weight:700; cursor:pointer }
  .ps-go:disabled { opacity:.6; cursor:default }
  .ps-out { flex:1; overflow:auto; padding:12px 14px; display:flex; flex-direction:column; gap:10px }
  .ps-note { color:#8b949e; font-size:12px; line-height:1.5 }
  .ps-card { background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.07); border-radius:12px; padding:11px 12px; cursor:pointer;
    transition:border-color .15s, background .15s, transform .15s }
  .ps-card:hover, .ps-card.on { border-color:rgba(168,85,247,.55); background:rgba(168,85,247,.08); transform:translateY(-1px) }
  .ps-t { color:#f0f6fc; font-weight:600; line-height:1.45 } .ps-rel { color:#c4b5fd; font-weight:500 }
  .ps-row { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px }
  .ps-b { padding:2px 8px; border-radius:10px; font-size:11px; background:rgba(255,255,255,.06); color:#8b949e }
  .ps-b.inf { background:rgba(168,85,247,.18); color:#d8b4fe } .ps-b.neg { background:rgba(248,113,113,.15); color:#fca5a5 }
  .ps-b.con { background:rgba(251,191,36,.15); color:#fcd34d } .ps-b.st { background:rgba(45,212,191,.12); color:#5eead4 }
  .ps-why { margin-top:8px; color:#8b949e; font-size:12px; line-height:1.45 }
  .ps-src { margin-top:6px; color:#6e7681; font-size:11px }
  .ps-hist { padding:10px 14px; border-bottom:1px solid rgba(255,255,255,.06); max-height:150px; overflow:auto }
  .ps-hh { font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:#8b949e; margin-bottom:6px }
  .ps-chip { display:block; width:100%; text-align:left; background:none; border:1px solid rgba(255,255,255,.07); color:#c9d1d9;
    border-radius:8px; padding:6px 9px; margin-bottom:5px; cursor:pointer; font:12px Inter,system-ui,sans-serif }
  .ps-chip:hover { border-color:rgba(168,85,247,.55); background:rgba(168,85,247,.08) }
  .ps-bar { display:flex; align-items:center; gap:8px }
  .ps-back { margin-left:auto; background:none; border:1px solid rgba(34,211,238,.45); color:#67e8f9; border-radius:8px; padding:3px 9px; cursor:pointer; font-size:12px }`;
  document.head.appendChild(st);
})();

function togglePlnSide() {
  const side = document.getElementById('pln-side');
  _plnSideOpen = !_plnSideOpen;
  side.style.display = _plnSideOpen ? 'flex' : 'none';
  if (_plnSideOpen && !side.dataset.ready) {
    side.dataset.ready = '1';
    side.innerHTML = `
      <div class="ps-head"><span class="ps-title">💬 Ask PLN</span><span class="ps-sub">backward chaining · all papers</span>
        <button class="ps-x" title="Close" onclick="togglePlnSide()">✕</button></div>
      <div class="ps-ask">
        <textarea class="ps-q" id="ps-q" placeholder="e.g. What does sodium regulate?  ·  What acts upstream of reproductive process?"></textarea>
        <button class="ps-go" id="ps-go" onclick="plnSideAsk()">🔮 Reason</button></div>
      <div class="ps-hist" id="ps-hist"><div class="ps-hh">Recent questions</div><div class="ps-note">loading…</div></div>
      <div class="ps-out" id="ps-out"><div class="ps-note">Ask in the form <b>“What does X <i>relation</i>?”</b> or
        <b>“What <i>relation</i> X?”</b>. Each answer shows how PLN proved it; click one to see it on the graph.</div></div>`;
    document.getElementById('ps-q').addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); plnSideAsk(); }
    });
    plnSideHistory();
  }
}

// Questions with a saved answer (the same list as the PLN board); a click asks again, answered from the saved copy
let _plnSideAsked = [];
async function plnSideHistory(add) {
  if (add) _plnSideAsked = [add, ..._plnSideAsked.filter(q => q.toLowerCase() !== add.toLowerCase())];
  else {
    try { const d = await (await fetch('/api/pln-overview')).json(); _plnSideAsked = (d.asked || []).map(a => a.question); }
    catch (e) { _plnSideAsked = []; }
  }
  const box = document.getElementById('ps-hist');
  if (!box) return;
  box.innerHTML = '<div class="ps-hh">Recent questions</div>' + (_plnSideAsked.length
    ? _plnSideAsked.slice(0, 30).map((q, i) => `<button class="ps-chip" onclick="plnSideReuse(${i})">${esc(q)}</button>`).join('')
    : '<div class="ps-note">None yet: your questions are saved here.</div>');
}
function plnSideReuse(i) { document.getElementById('ps-q').value = _plnSideAsked[i]; plnSideAsk(); }

// Back to the PLN view of the graph (answers stay coloured)
function plnSideBack() {
  const w = document.getElementById('pln-tab-frame').contentWindow;
  document.querySelectorAll('.ps-card').forEach(c => c.classList.remove('on'));
  _plnSideRun([]);                                    // restore the answer styling
  try { w.eval(`_infVia = ''; _infSel = null; document.getElementById('inf-drawer').style.display = 'none'; showReasoned();`); } catch (e) {}
}

async function plnSideAsk() {
  const q = document.getElementById('ps-q').value.trim(), out = document.getElementById('ps-out'), go = document.getElementById('ps-go');
  if (!q) return;
  go.disabled = true;
  const t0 = Date.now();
  out.innerHTML = '<div class="ps-note" id="ps-wait">PLN is reasoning… 0 s</div>';
  const tick = setInterval(() => { const w = document.getElementById('ps-wait'); if (w) w.textContent = `PLN is reasoning… ${Math.round((Date.now() - t0) / 1000)} s`; }, 500);
  try {
    const d = await (await fetch('/api/pln-unified-query', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                           body: JSON.stringify({ question: q }) })).json();
    if (!d.ok) { out.innerHTML = `<div class="ps-note" style="color:#f87171">PLN error: ${esc(d.error || 'unknown')}</div>`; return; }
    _plnSideAnswers = d.answers || [];
    const why = (d.trace || []).filter(t => t.step === 'Answer').map(t => t.detail);
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    const head = `<div class="ps-bar"><span class="ps-note">${_plnSideAnswers.length} answer${_plnSideAnswers.length === 1 ? '' : 's'} · ${secs} s${d.cached ? ' · saved answer' : ''}</span>
      <button class="ps-back" onclick="plnSideBack()">↺ Back to the full graph</button></div>`;
    plnSideHistory(q);
    if (!_plnSideAnswers.length) {
      const step = (d.trace || []).find(t => t.step === 'Question');
      out.innerHTML = head + `<div class="ps-note">No proof found. ${esc(step ? step.detail : '')}</div>`;
      return;
    }
    out.innerHTML = head + _plnSideAnswers.map((a, i) => {
      const kind = a.inferred ? '<span class="ps-b inf">inferred by a rule</span>'
        : a.negated ? '<span class="ps-b neg">negative finding</span>'
        : a.contested ? '<span class="ps-b con">contested</span>' : '<span class="ps-b st">stated in a paper</span>';
      const rel = String(a.predicate || '').replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase();
      return `<div class="ps-card" id="ps-card-${i}" onclick="plnSideShow(${i})">
        <div class="ps-t">${esc(a.subject)} <span class="ps-rel">—[${esc(rel)}]→</span> ${esc(a.object)}</div>
        <div class="ps-row">${kind}<span class="ps-b">stv ${a.strength.toFixed(2)} ${a.confidence.toFixed(2)}</span></div>
        ${a.inferred && why[i] ? `<div class="ps-why">${esc(why[i].replace(/^.*? — /, ''))}</div>` : ''}
        ${(a.papers || []).length ? `<div class="ps-src">from ${esc(a.papers.map(p => String(p).replace(/^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_/, '')).join(', '))}</div>` : ''}</div>`;
    }).join('');
    plnSideShowAll();
  } catch (e) {
    out.innerHTML = `<div class="ps-note" style="color:#f87171">Network error: ${esc(e.message)}</div>`;
  } finally {
    clearInterval(tick);
    go.disabled = false;
  }
}

// The answers on the graph: cyan = a stated fact that answers the question, pink dashed = proved by a rule for it
const _plnSideMark = function (list) {            // runs inside the graph page; [] only restores
  const key = s => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
  if (!document.getElementById('ask-legend')) {
    const lg = document.getElementById('legend');
    if (lg) lg.insertAdjacentHTML('beforeend', '<div id="ask-legend"><h4 style="color:#22d3ee">★ Cyan = answer to your question</h4>' +
      '<h4 style="color:#f472b6">★ Pink = proved by backward chaining</h4></div>');
  }
  // restore the previous question's styling
  edgesDS.getIds().filter(id => String(id).startsWith('ask-')).forEach(id => edgesDS.remove(id));
  if (window._askTouched) {
    nodesDS.update(NODES_DATA.map(n => ({ id: n.id, opacity: 1, color: n.color, size: n.size, font: n.font,
                                         borderWidth: n.borderWidth, shadow: n.shadow })));
    edgesDS.update(EDGES_DATA.map(e => ({ id: e.id, color: e.color, width: e.width, label: e.label, shadow: e.shadow, font: e.font })));
    window._askTouched = false;
  }
  if (!list.length) return;
  const answer = new Set(), ends = new Map(), papers = new Set();
  list.forEach((a, i) => {
    const rel = String(a.predicate).replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase();
    const from = NODES_DATA.find(n => key(n.id) === a.subject_id), to = NODES_DATA.find(n => key(n.id) === a.object_id);
    if (!from || !to) return;
    const color = a.inferred ? '#f472b6' : '#22d3ee';
    const style = { color: { color, highlight: color, hover: color, opacity: 1 }, width: 9,
                    shadow: { enabled: true, color, size: 22, x: 0, y: 0 },
                    font: { color: '#ffffff', size: 18, bold: true, strokeWidth: 5, strokeColor: '#000000' } };
    const e = EDGES_DATA.find(x => key(x.from) === a.subject_id && key(x.to) === a.object_id && x._relation === rel);
    if (e) {
      edgesDS.update(Object.assign({ id: e.id, label: '★ ' + rel.replace(/_/g, ' ') }, style));
      answer.add(e.id);
      if (e._paper) papers.add(e._paper);
      if (e._inferred) _premiseEdges(e).forEach(x => { answer.add(x.id); if (x._paper) papers.add(x._paper); });
    } else {
      edgesDS.add(Object.assign({ id: 'ask-' + i, from: from.id, to: to.id, label: '★ ' + rel.replace(/_/g, ' '),
        dashes: a.inferred ? [8, 5] : false, arrows: { to: { enabled: true, scaleFactor: 0.9 } },
        font: { color: '#ffffff', size: 15, bold: true, strokeWidth: 4, strokeColor: '#000000' } }, style));
      answer.add('ask-' + i);
      EDGES_DATA.forEach(x => { if ((x.from === from.id || x.to === from.id) && x._paper) papers.add(x._paper); });
    }
    ends.set(from.id, color); ends.set(to.id, color);
  });
  // the paper(s) around the answer, dimmed; the answer in full colour
  const shown = new Set(answer), ids = new Set(ends.keys());
  EDGES_DATA.forEach(x => { if (papers.has(x._paper)) shown.add(x.id); });
  EDGES_DATA.forEach(x => { if (shown.has(x.id)) { ids.add(x.from); ids.add(x.to); } });
  nodesDS.update(NODES_DATA.map(n => ends.has(n.id)
    ? { id: n.id, hidden: false, opacity: 1, size: 44, borderWidth: 5,
        color: { background: ends.get(n.id), border: '#ffffff', highlight: { background: ends.get(n.id), border: '#ffffff' } },
        font: { color: '#ffffff', size: 24, bold: true, strokeWidth: 5, strokeColor: '#000000' },
        shadow: { enabled: true, color: ends.get(n.id), size: 28, x: 0, y: 0 } }
    : { id: n.id, hidden: !ids.has(n.id), opacity: 0.3, size: 7, font: { size: 9, color: '#6b7280' } }));
  edgesDS.update(EDGES_DATA.filter(x => !answer.has(x.id)).map(x => ({ id: x.id, hidden: !shown.has(x.id),
    color: { color: '#4b5563', highlight: '#9ca3af', hover: '#9ca3af', opacity: 0.25 }, width: 1, shadow: false,
    font: { size: 0 } })));                                   // context edges without labels
  EDGES_DATA.filter(x => answer.has(x.id)).forEach(x => edgesDS.update({ id: x.id, hidden: false }));
  window._askTouched = true;
  network.unselectAll();
  _fitAbove([...ends.keys()], false);                 // centred on the answer, its paper around it
  network.moveTo({ scale: Math.min(network.getScale() * 0.45, 1.1), animation: { duration: 450, easingFunction: 'easeInOutQuad' } });
};
function _plnSideRun(list, tries = 0) {
  const w = document.getElementById('pln-tab-frame').contentWindow;
  if (!w || typeof w.eval !== 'function') return;
  const net = w.document && w.document.getElementById('network');
  if (!net || net.style.visibility !== 'visible') {          // graph still loading or laying out: wait for it
    if (tries < 120) setTimeout(() => _plnSideRun(list, tries + 1), 500);
    return;
  }
  try { w.eval(`(${_plnSideMark.toString()})(${JSON.stringify(list)})`); } catch (e) { /* not a PLN graph page */ }
}
function plnSideShowAll() {
  document.querySelectorAll('.ps-card').forEach(c => c.classList.remove('on'));
  _plnSideRun(_plnSideAnswers);
}
function plnSideShow(i) {
  document.querySelectorAll('.ps-card').forEach((c, k) => c.classList.toggle('on', k === i));
  if (_plnSideAnswers[i]) _plnSideRun([_plnSideAnswers[i]]);
}
