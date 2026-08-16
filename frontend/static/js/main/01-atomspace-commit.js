// ── Atomspace commit ─────────────────────────────────────────────────────────

let _stagingDb    = '';
let _commitFormat = 'both';
let _docId        = '';

function setCommitFmt(f, btn) {
  document.querySelectorAll('#commit-fmt-group .fmt-chip').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  _commitFormat = f;
  // Refresh breakdown when format changes — n_already depends on which DB is checked
  if (_stagingDb) fetchStagingBreakdown(_stagingDb, _commitFormat);
}

async function commitToAtomspace() {
  if (!_stagingDb) { alert('No staging data to commit.'); return; }
  const btn = document.getElementById('commit-btn');
  btn.disabled = true; btn.textContent = '⟳ Committing…';
  const res = document.getElementById('commit-result');

  try {
    const fd = new FormData();
    fd.append('staging_db',    _stagingDb);
    fd.append('output_format', _commitFormat);
    fd.append('doc_id',        _docId);
    const resp = await fetch('/api/commit', { method:'POST', body:fd });
    const d    = await resp.json();

    res.style.display = 'block';
    if (d.ok) {
      const newN = d.new || 0;
      const updN = d.updated || 0;
      // The DB commit (what actually matters for correctness) is done at
      // this point — the unified graph.html/verification rebuild is slow
      // (scales with EVERY triple ever committed, not just this one) and
      // keeps running in the background; poll for it instead of blocking
      // the button on it.
      res.innerHTML = `<div style="color:var(--green);font-size:13px;font-weight:600;margin-bottom:10px">
        ✓ ${newN} new triple(s) committed${updN > 0 ? ` · ${updN} updated` : ''} — unified ${d.format} atomspace
      </div>
      <div id="commit-rebuild-status" style="color:var(--text3);font-size:12px">⟳ Rebuilding unified graph + verification report…</div>`;
      btn.textContent = '✓ Committed';

      if (_PLN_ENABLED) document.getElementById('pln-commit-step').style.display = 'block';
      AppState.saveCommit(_commitFormat, d, '');

      if (d.job_id) _pollCommitRebuild(d.job_id);
      else if (_stagingDb) rebuildComparePages(d);   // no job_id — fall back to old synchronous path
    } else {
      res.innerHTML = `<div style="color:var(--red);font-size:12px">Error: ${d.error||'Unknown error'}</div>`;
      btn.disabled = false; btn.textContent = '✓ Commit to Unified KG';
    }
  } catch (e) {
    // Without this, a network error or unparseable response left the button
    // stuck on "⟳ Committing…" forever — the exact symptom reported live.
    if (res) {
      res.style.display = 'block';
      res.innerHTML = `<div style="color:var(--red);font-size:12px">Error: ${e.message||'commit request failed'}</div>`;
    }
    btn.disabled = false; btn.textContent = '✓ Commit to Unified KG';
  }
}

async function _pollCommitRebuild(jobId, attempt = 0) {
  const statusEl = document.getElementById('commit-rebuild-status');
  try {
    const r = await fetch(`/api/commit-status?job_id=${encodeURIComponent(jobId)}`);
    const d = await r.json();
    if (d.status === 'done') {
      if (d.error) {
        if (statusEl) { statusEl.style.color = 'var(--red)'; statusEl.textContent = `⚠ Rebuild failed: ${esc(d.error)}`; }
        return;
      }
      if (statusEl) statusEl.remove();
      rebuildComparePages(d);
      return;
    }
  } catch (_) { /* transient — keep polling */ }
  // Backs off from 2s up to 10s so a multi-minute rebuild (large unified KG)
  // doesn't hammer the server with fast polls the whole time.
  const delay = Math.min(2000 + attempt * 1000, 10000);
  setTimeout(() => _pollCommitRebuild(jobId, attempt + 1), delay);
}

// PLN commit — triggers Layer 9 PLN reasoning and writes to unified_metta/pln/
async function commitPLN() {
  const btn = document.getElementById('pln-commit-btn');
  btn.disabled = true; btn.textContent = '🔮 Running PLN…';
  const res = document.getElementById('pln-commit-result');
  res.style.display = 'block';
  res.innerHTML = '<span style="color:var(--text3)">Running PLN reasoning (revision · inference · contradictions)…</span>';
  try {
    const resp = await fetch('/api/run_pln', { method:'POST' });
    const d    = await resp.json();
    if (d.ok) {
      res.innerHTML = `<div style="color:var(--pln);font-size:13px;font-weight:600">
        🔮 PLN committed — ${d.revised||0} revised · ${d.inferred||0} inferred · ${d.contradictions||0} contradictions
      </div>
      <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
        ${d.pln_html ? `<button class="fmt-chip" style="border-color:var(--pln2);color:var(--pln)" onclick="openViewer('${fileUrl(d.pln_html)}','Unified PLN AtomSpace')">🔮 Unified PLN ↗</button>` : ''}
      </div>`;
      btn.textContent = '🔮 PLN Committed';
      AppState.savePLN(d);              // persist PLN result
    } else {
      res.innerHTML = `<div style="color:var(--red);font-size:12px">PLN error: ${d.error||d.trace||'Server returned ok=false — restart the frontend server'}</div>`;
      btn.disabled = false; btn.textContent = '🔮 Commit to Unified PLN';
    }
  } catch(e) {
    res.innerHTML = `<div style="color:var(--red);font-size:12px">
      Network error — likely the /api/run_pln endpoint is not registered.<br>
      Restart the server: <code>python3 -m uvicorn frontend.app:app --reload</code><br>
      Error: ${e.message}
    </div>`;
    btn.disabled = false; btn.textContent = '🔮 Commit to Unified PLN';
  }
}

function commitPLNInference() {
  const d   = _outputData || {};
  const btn = document.getElementById('pln-inference-btn');
  _runPLNInference(d.run_dir || '', d.staging_db || '', d.doc_id || '', btn);
}

function commitPLNQuery() {
  const d = _outputData || {};
  openPLNPanel(d.staging_db || '', d.run_dir || '', `PLN Query — ${d.doc_id || ''}`);
}

async function rebuildComparePages(commitData) {
  if (!_outputData || !_outputData.run_dir) return;
  try {
    const fd = new FormData();
    fd.append('run_dir', _outputData.run_dir);
    const r = await fetch('/api/rebuild-compare', { method:'POST', body:fd });
    const d = await r.json();
    const res = document.getElementById('commit-result');
    if (!res) return;
    // Show only Compare buttons — they contain everything inside tabs
    let btns = '<div style="margin-top:12px;display:flex;gap:10px;flex-wrap:wrap;align-items:center">';
    btns += '<span style="font-size:11px;color:var(--text3);font-weight:600;text-transform:uppercase;letter-spacing:.05em">Open in browser →</span>';
    if (d.ok && d.neo4j_compare)
      btns += `<button class="fmt-chip" style="border-color:var(--blue)" onclick="openViewer('${fileUrl(d.neo4j_compare)}','Neo4j Compare')">⇄ Neo4j Compare ↗</button>`;
    if (d.ok && d.metta_compare)
      btns += `<button class="fmt-chip" style="border-color:var(--purple)" onclick="openViewer('${fileUrl(d.metta_compare)}','MeTTa Compare')">⇄ MeTTa Compare ↗</button>`;
    btns += `<button class="fmt-chip" style="border-color:var(--border);color:var(--text2)"
      onclick="_PLN_ENABLED ? openUnifiedKGPanel() : openTriplesPanel('data/triple_store_neo4j.db','','Unified KG Triples')">✎ Manage Unified KG</button>`;
    btns += '</div>';
    res.innerHTML += btns;
  } catch(e) {}
}

function showRunDirButtons(runDir) {
  const res = document.getElementById('commit-result');
  if (!res || !runDir) return;
  // Only show Compare buttons — Paper Graph and Verification Report removed
  const files = [
    ['⇄ Neo4j Compare',  runDir + '/compare_neo4j.html'],
    ['⇄ MeTTa Compare',  runDir + '/compare_metta.html'],
  ];
  let links = '<div style="margin-top:10px"><div style="font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px">Run outputs</div><div style="display:flex;gap:6px;flex-wrap:wrap">';
  for (const [label, path] of files) {
    const apiUrl = `/api/file?path=${encodeURIComponent(path)}`;
    links += `<button class="fmt-chip" onclick="openOrCheck('${apiUrl}','${label}')">${label} ↗</button>`;
  }
  links += '</div></div>';
  res.innerHTML += links;
}

function openOrCheck(src, title) {
  if (!src || src === 'undefined' || src === '') {
    alert(`${title} not generated yet — run the pipeline first.`);
    return;
  }
  openViewer(src, title);
}

