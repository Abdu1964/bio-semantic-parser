// ── PLN board: questions over every loaded paper; answers, a live timer and a step-by-step walkthrough ──

let _plnHistory = [];                         // {q, secs}: questions with a saved answer, newest first, each once
const _plnKey = q => String(q || '').toLowerCase().replace(/\s+/g, ' ').replace(/\?+$/, '').trim();
let _plnLast = null;                          // last API reply, for the walkthrough

const _plnName = s => String(s || '').replace(/_/g, ' ');
const _plnPaper = run => String(run || '').replace(/^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_/, '');   // run folder → paper id

// The board asks the atomspace that holds every processed paper, whichever paper it is opened from
function openPLNPanel() {
  document.getElementById('pln-board')?.remove();

  const board = document.createElement('div');
  board.id = 'pln-board';
  board.innerHTML = `
    <div class="pb-panel" role="dialog" aria-label="PLN reasoning">
      <div class="pb-head">
        <div class="pb-orb"></div>
        <div><h2>PLN Reasoning</h2><p id="pln-stats">Loading the atomspace…</p></div>
        <button class="pb-x" title="Close (Esc)" onclick="closePLNBoard()">✕</button>
      </div>
      <div class="pb-body">
        <div class="pb-side">
          <div class="pb-ask">
            <div class="pb-label">Ask the knowledge graph</div>
            <textarea id="pln-q-input" placeholder="e.g. What does sodium regulate?"
              onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();_runPLNQuery()}"></textarea>
            <button class="pb-go" id="pln-go" onclick="_runPLNQuery()">🔮 Reason</button>
          </div>
          <div class="pb-forms" id="pln-forms"><div class="pb-label">Questions from the loaded facts</div></div>
          <div class="pb-hist" id="pln-hist"></div>
        </div>
        <div class="pb-main" id="pln-results">${_plnIdleHtml()}</div>
      </div>
    </div>`;
  board.addEventListener('click', e => { if (e.target === board) closePLNBoard(); });
  document.body.appendChild(board);
  document.addEventListener('keydown', _plnEsc);
  _plnRenderHistory();
  _plnLoadOverview();
  setTimeout(() => document.getElementById('pln-q-input')?.focus(), 80);
}

// What the atomspace holds, and ready questions made from its own facts (each has an answer)
async function _plnLoadOverview() {
  const stats = document.getElementById('pln-stats'), forms = document.getElementById('pln-forms');
  try {
    const d = await (await fetch('/api/pln-overview')).json();
    if (!d.ok) { stats.textContent = `PLN error: ${d.error || 'unknown'}`; return; }
    stats.textContent = `${d.papers} paper${d.papers === 1 ? '' : 's'} · ${d.facts} facts · ${d.entities} entities · ${d.relations} relation types · ${d.sync}`;
    _plnHistory = (d.asked || []).map(a => ({ q: a.question, secs: _plnSecs(a.ms || 0) }));
    _plnRenderHistory();
    forms.innerHTML = `<div class="pb-label">Questions from the loaded facts</div>`
      + (d.questions || []).map(q => `<span title="${esc(q.predicate)} · ${q.facts} fact(s)"
           onclick="_plnAsk(${JSON.stringify(q.question).replace(/"/g, '&quot;')})">${esc(q.question)}</span>`).join('');
  } catch (e) { stats.textContent = `Error: ${e.message}`; }
}

function closePLNBoard() {
  const board = document.getElementById('pln-board');
  if (!board) return;
  clearInterval(window._plnTick);
  document.removeEventListener('keydown', _plnEsc);
  board.classList.add('closing');
  setTimeout(() => board.remove(), 240);
}

function _plnEsc(e) { if (e.key === 'Escape') closePLNBoard(); }

const _PLN_STAGES = ['Facts', 'Question', 'Names', 'Forward chaining', 'Backward chaining', 'Answers'];
const _plnFlowHtml = () => `<div class="pb-flow">${_PLN_STAGES.map((s, i) => (i ? '<i>→</i>' : '') + `<span>${s}</span>`).join('')}</div>`;

function _plnIdleHtml() {
  return `<div class="pb-center">
      <div class="pb-orb" style="width:70px;height:70px"></div>
      <h3>Ask a question about the knowledge graph</h3>
      <p>PLN holds the facts of every processed paper, matches your question to their entities, applies the biology
         rules forward and proves the answer backward. Every step can be opened afterwards.</p>
      ${_plnFlowHtml()}
    </div>`;
}

function _plnRenderHistory() {
  const el = document.getElementById('pln-hist');
  if (!el) return;
  el.innerHTML = _plnHistory.length ? `<div class="pb-label">Asked</div>` + _plnHistory.map(h =>
    `<div onclick="_plnAsk(${JSON.stringify(h.q).replace(/"/g, '&quot;')})"><span>${esc(h.q)}</span><b>${h.secs}</b></div>`).join('') : '';
}

function _plnAsk(q) {
  document.getElementById('pln-q-input').value = q;
  _runPLNQuery();
}

const _plnSecs = ms => ms < 60000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.floor(ms / 60000)} min ${Math.round(ms % 60000 / 1000)} s`;

async function _runPLNQuery(fresh = false) {
  const input = document.getElementById('pln-q-input');
  const q = (input ? input.value : (_plnLast && _plnLast.question) || '').trim();
  const res = document.getElementById('pln-results');
  if (!q || !res) return;
  const go = document.getElementById('pln-go');
  if (go) go.disabled = true;

  // live timer while PLN reasons
  const t0 = performance.now();
  res.innerHTML = `<div class="pb-center pb-running">
      <div class="pb-ring"></div>
      <div class="pb-clock" id="pln-clock">0.0 s</div>
      <h3>PLN is reasoning…</h3>
      <p>${esc(q)}</p>
      ${_plnFlowHtml()}
    </div>`;
  clearInterval(window._plnTick);
  window._plnTick = setInterval(() => {
    const c = document.getElementById('pln-clock');
    if (c) c.textContent = _plnSecs(performance.now() - t0);
  }, 100);

  try {
    const r = await fetch('/api/pln-unified-query', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ question: q, fresh }),
    });
    const d = await r.json();
    const took = _plnSecs(performance.now() - t0);
    clearInterval(window._plnTick);
    if (!d.ok) {
      res.innerHTML = `<div class="pb-err">PLN error: ${esc(d.error || 'Unknown')}</div>`;
      return;
    }
    _plnLast = { ...d, took };
    _plnHistory = _plnHistory.filter(h => _plnKey(h.q) !== _plnKey(q));
    _plnHistory.unshift({ q, secs: d.cached ? _plnSecs(d.reasoned_ms || 0) : took });
    _plnRenderHistory();
    _plnShowResult();
  } catch (e) {
    clearInterval(window._plnTick);
    res.innerHTML = `<div class="pb-err">Error: ${esc(e.message)}</div>`;
  } finally {
    if (go) go.disabled = false;
  }
}

// The answer, as it is: one card per relation found
function _plnShowResult() {
  const d = _plnLast, res = document.getElementById('pln-results');
  if (!d || !res) return;
  const answers = d.answers || [];
  const pct = v => Math.round((v || 0) * 100);
  const cards = answers.slice(0, 30).map((a, i) => `
    <div class="pb-answer" style="animation-delay:${i * 70}ms">
      <div class="pb-ent"><small>${esc(_plnName(a.subject_type))}</small>${esc(_plnName(a.subject))}</div>
      <div class="pb-rel">${esc(a.predicate)}</div>
      <div class="pb-ent"><small>${esc(_plnName(a.object_type))}</small>${esc(_plnName(a.object))}</div>
      <div class="pb-bars">
        <div>strength <u><i style="width:${pct(a.strength)}%"></i></u> ${(a.strength || 0).toFixed(2)}</div>
        <div>confidence <u><i style="width:${pct(a.confidence)}%"></i></u> ${(a.confidence || 0).toFixed(2)}</div>
      </div>
      <div class="pb-kind">${a.inferred ? '<b class="inf">inferred by a rule</b>'
        : a.negated ? '<b style="color:#f87171">negative finding: stated as NOT holding</b>'
        : a.contested ? '<b style="color:#fbbf24">contested: papers disagree</b>' : '<b>stated in the paper</b>'}
        ${(a.papers || []).map(p => `<span class="pb-paper">📄 ${esc(_plnPaper(p))}</span>`).join('')}</div>
    </div>`).join('');
  res.innerHTML = `
    <div class="pb-res-head">
      <h3>${esc(d.question)}</h3>
      <span class="pb-chip pb-time">⏱ ${esc(d.took)}</span>
      ${d.cached ? `<span class="pb-chip pb-saved" title="Reasoned on ${esc((d.saved_at || '').replace('T', ' '))}">⚡ saved answer · reasoned in ${_plnSecs(d.reasoned_ms || 0)}</span>
        <button class="pb-again" onclick="_runPLNQuery(true)">↻ Run again</button>` : ''}
      <span class="pb-chip" style="color:#c4b5fd;background:rgba(139,92,246,.12)">${answers.length} answer${answers.length === 1 ? '' : 's'}</span>
      <button class="pb-again" onclick="window.open('/static/pln-answer.html?q=' + encodeURIComponent(_plnLast.question), '_blank')"
        title="Open this answer on its own page">↗ Open in new tab</button>
      <button class="pb-learn" onclick="_plnShowWalkthrough()">🧠 How PLN answered this</button>
    </div>
    <div class="pb-q">${esc(d.pln_query || '')}</div>
    ${cards || `<div class="pb-none">No PLN proof found for this question. Open “How PLN answered this” to see each step it tried.</div>`}`;
}

// Every step PLN took for this question, with what it means and the data behind it
function _plnShowWalkthrough() {
  const d = _plnLast, res = document.getElementById('pln-results');
  if (!d || !res) return;
  const title = { 'Knowledge': 'Facts enter the atomspace', 'Sync': 'Only new papers are loaded', 'Question': 'The question is read', 'Name': 'A name is matched to an entity',
                  'PeTTa query': 'The question becomes a PeTTa query', 'Forward chaining': 'Forward chaining applies the rules',
                  'Backward chaining': 'Backward chaining searches for proofs', 'Filter': 'Answers are kept for the matched entities',
                  'Answer': 'An answer and its proof' };
  const steps = (d.trace || []).map((t, i) => `
    <div class="pb-step" style="animation-delay:${i * 90}ms">
      <div class="num">${i + 1}</div>
      <h4>${esc(title[t.step] || t.step)}${t.ms != null ? `<span class="pb-chip pb-time">${_plnSecs(t.ms)}</span>` : ''}</h4>
      <div class="what">${esc(t.detail)}</div>
      ${t.explain ? `<div class="why">${esc(t.explain)}</div>` : ''}
      ${_plnDataHtml(t)}
    </div>`).join('');
  res.innerHTML = `
    <button class="pb-back" onclick="_plnShowResult()">← Back to the answer</button>
    <div class="pb-res-head"><h3>How PLN answered: ${esc(d.question)}</h3><span class="pb-chip pb-time">⏱ ${esc(d.took)} in total</span></div>
    <div class="pb-steps">${steps}</div>`;
  res.scrollTop = 0;
}

function _plnDataHtml(t) {
  const d = t.data;
  if (!d) return '';
  const box = (label, rows) => rows.length
    ? `<details><summary>${esc(label)}</summary><div class="pb-data">${rows.join('')}</div></details>` : '';
  if (t.step === 'Knowledge') {
    const papers = d.sources || [];
    return box(`Where the facts came from (${papers.length} paper${papers.length === 1 ? '' : 's'})`, papers.map(s =>
        `<div>📄 <b>${esc(_plnPaper(s.paper))}</b> — ${(s.files || []).length} edge file(s) in ${esc(s.run_dir)}`
        + `${s.truth_values_from_db ? ` · truth values from ${esc(s.staging_db)}` : ''}<br><em>${(s.files || []).map(esc).join(' · ')}</em></div>`))
      + box(`The atoms${d.atoms_total > (d.atoms || []).length ? ` (first ${d.atoms.length} of ${d.atoms_total})` : ` (${(d.atoms || []).length})`}`,
            (d.atoms || []).map(a => `<div>${esc(a)}</div>`));
  }
  if (t.step === 'Forward chaining') {
    return box(`The ${(d.rules || []).length} rules`, (d.rules || []).map(r =>
        `<div class="pb-rule"><b>${esc(r.name)}</b> (STV ${r.stv.map(x => (+x).toFixed(2)).join(' ')})<br>`
        + `${esc(r.meaning)}<br><em>${esc(r.text)}</em></div>`))
      + box(`Derived relations (${(d.derived || []).length})`, (d.derived || []).map(x =>
        `<div>${esc(x.atom)}${x.rule ? `<br><em>by ${esc(x.rule)}: ${esc(x.premise1)} + ${esc(x.premise2)}</em>` : ''}</div>`));
  }
  if (t.step === 'Backward chaining') return box(`The ${(d.proofs || []).length} proofs`, (d.proofs || []).map(p => `<div>${esc(p)}</div>`));
  if (t.step === 'Answer') return box('The proof', [`<div>${esc(d.proof)}</div>`]);
  return '';
}
