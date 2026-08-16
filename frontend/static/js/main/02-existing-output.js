// ── Run selector — shown when multiple runs of the same paper exist ──────────
// ── Existing output actions — shown when loading a previously-processed paper ─
function _showExistingOutputActions(d) {
  const old = document.getElementById('existing-actions');
  if (old) old.remove();

  const docId = d.doc_id || '';
  const wrap  = document.createElement('div');
  wrap.id = 'existing-actions';
  wrap.style.cssText = `
    background:var(--bg2);border:1px solid var(--border);border-radius:12px;
    padding:20px 24px;margin-top:16px;margin-bottom:24px;
  `;
  const completedLayers = d.completed_layers || [];
  const nextLayer       = d.next_layer || null;
  const allDone         = completedLayers.length >= 8;

  // Build layer selector — user can resume from any layer 1–8
  const layerNames = ['','Source','Dedup','Fetch','NER','Schema','Extract','Validate','Publish'];
  const layerSelector = `
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px;
      padding:10px 12px;background:var(--bg3);border-radius:8px;border:1px solid var(--border)">
      <span style="font-size:11px;font-weight:700;color:var(--text3);text-transform:uppercase;
        letter-spacing:.05em;white-space:nowrap">Resume from:</span>
      ${[3,4,5,6,7,8].map(n => {
        const done  = completedLayers.includes(n);
        const isNext = n === nextLayer;
        const color  = isNext ? 'var(--blue)' : done ? 'var(--green)' : 'var(--border)';
        const bg     = isNext ? 'var(--bg)' : done ? 'var(--bg2)' : 'var(--bg3)';
        const label  = `L${n} ${layerNames[n]}`;
        return `<button class="fmt-chip"
          style="border-color:${color};color:${isNext?'var(--blue)':done?'var(--green)':'var(--text3)'};
            background:${bg};font-size:10px;padding:3px 9px"
          data-action="resume-layer" data-docid="${esc(docId)}" data-layer="${n}"
          title="Re-run from Layer ${n}: ${layerNames[n]}${done?' (completed)':isNext?' (next)':' (not run)'}">
          ${label}
        </button>`;
      }).join('')}
    </div>`;

  const statusLine = !allDone && nextLayer
    ? `Layers ${completedLayers.join(', ')} completed. Pipeline stopped at Layer ${nextLayer}.`
    : `All layers complete. Open existing outputs above, or re-run from any layer below:`;

  wrap.innerHTML = `
    <div style="font-size:14px;font-weight:700;color:var(--text);margin-bottom:6px">
      Existing outputs loaded
    </div>
    <div style="font-size:12px;color:var(--text3);margin-bottom:12px">
      ${statusLine}
    </div>
    ${layerSelector}
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <button class="fmt-chip" style="background:var(--bg3);border:1px solid var(--green2);color:var(--text)"
        data-action="commit-kg" data-rundir="${esc(d.run_dir||'')}" data-docid="${esc(docId)}"
        title="Commit existing triples to the unified atomspace">
        Commit to Unified KG
      </button>
      ${_PLN_ENABLED ? `<button class="fmt-chip" style="border-color:var(--pln2);color:var(--pln)"
        data-action="commit-pln"
        title="Run PLN reasoning and commit to unified MeTTa PLN">
        🔮 Commit to Unified PLN
      </button>` : ''}
      <button class="fmt-chip" style="border-color:var(--border);color:var(--text2)"
        data-action="reprocess" data-docid="${esc(docId)}"
        title="Discard existing outputs and reprocess from scratch">
        Reprocess from scratch
      </button>
      <button class="fmt-chip" style="border-color:var(--blue);color:var(--blue)"
        data-action="manage-triples" data-docid="${esc(docId)}" data-rundir="${esc(d.run_dir||'')}"
        title="View, edit and delete triples in the staging DB for this paper">
        ✎ Manage Paper Triples
      </button>
      <button class="fmt-chip" style="border-color:var(--border);color:var(--text2)"
        data-action="manage-unified"
        title="View, edit and delete triples in the unified KG">
        ✎ Manage Unified KG Triples
      </button>
      ${_PLN_ENABLED ? `<button class="fmt-chip" style="border-color:var(--pln2);color:var(--pln)"
        data-action="pln-inference" data-docid="${esc(docId)}" data-rundir="${esc(d.run_dir||'')}"
        title="Run PLN forward chaining over this paper and open the inference graph">
        🔮 Run PLN Inference
      </button>` : ''}
      ${_PLN_ENABLED ? `<button class="fmt-chip" style="border-color:var(--pln2);color:var(--pln)"
        data-action="pln-query" data-docid="${esc(docId)}" data-rundir="${esc(d.run_dir||'')}"
        title="Ask PLN a natural language question over this paper's triples">
        🔮 PLN Query
      </button>` : ''}
    </div>
    <div id="existing-action-result" style="margin-top:12px;display:none"></div>
  `;

  const output = document.getElementById('output');
  if (output && output.parentNode) output.parentNode.insertBefore(wrap, output.nextSibling);

  // Attach listeners via JS — no inline onclick, avoids scope issues
  wrap.querySelectorAll('[data-action]').forEach(btn => {
    btn.addEventListener('click', function() {
      const action  = this.dataset.action;
      const runDir  = this.dataset.rundir || '';
      const dId     = this.dataset.docid  || '';
      if (action === 'commit-kg')      _commitExistingOutputs(runDir, dId);
      if (action === 'commit-pln')     _commitExistingPLN();
      if (action === 'reprocess')      _reprocessPaper(dId);
      if (action === 'resume')         _resumePipeline(dId, parseInt(this.dataset.layer||'1'));
      if (action === 'resume-layer')   _resumeFromLayer(dId, parseInt(this.dataset.layer||'1'));
      if (action === 'manage-triples') {
        const sdb = `data/staging/${dId}_both.db`;
        openTriplesPanel(sdb, runDir, `Paper Triples — ${dId}`);
      }
      if (action === 'manage-unified') {
        if (_PLN_ENABLED) openUnifiedKGPanel();
        else openTriplesPanel('data/triple_store_neo4j.db', '', 'Unified KG Triples');
      }
      if (action === 'pln-inference') {
        const sdb = `data/staging/${dId}_both.db`;
        _runPLNInference(runDir, sdb, dId, this);
      }
      if (action === 'pln-query') {
        const sdb = `data/staging/${dId}_both.db`;
        openPLNPanel(sdb, runDir, `PLN Query — ${dId}`);
      }
    });
  });
}

async function _commitExistingOutputs(runDir, docId) {
  const res = document.getElementById('existing-action-result');
  res.style.display = 'block';
  res.innerHTML = '<span style="color:var(--text3)">Committing…</span>';
  try {
    const fd = new FormData();
    fd.append('run_dir', runDir);
    fd.append('doc_id',  docId);
    fd.append('output_format', _commitFormat || 'both');
    const r = await fetch('/api/commit_existing', { method:'POST', body:fd });
    const d = await r.json();
    if (d.ok) {
      const msg   = d.message || `${d.new||0} new triple(s) committed`;
      const color = (d.new||0) === 0 ? 'var(--text3)' : 'var(--green)';
      // Build view buttons — same as post-commit in normal flow
      let viewBtns = '<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">';
      viewBtns += '<span style="font-size:11px;color:var(--text3);font-weight:600;text-transform:uppercase;letter-spacing:.05em">Open →</span>';
      if (d.neo4j_compare) viewBtns += `<button class="fmt-chip" style="border-color:var(--blue)" data-open="${d.neo4j_compare}">⇄ Neo4j Compare ↗</button>`;
      if (d.metta_compare) viewBtns += `<button class="fmt-chip" style="border-color:var(--purple)" data-open="${d.metta_compare}">⇄ MeTTa Compare ↗</button>`;
      viewBtns += '</div>';
      res.innerHTML = `<div style="color:${color};font-size:13px;font-weight:600;margin-bottom:4px">
        ✓ ${msg} — unified ${d.format||''} atomspace
      </div>${viewBtns}`;
      // Wire open buttons via event listener
      res.querySelectorAll('[data-open]').forEach(btn => {
        btn.addEventListener('click', () => {
          openViewer('/api/file?path=' + encodeURIComponent(btn.dataset.open), btn.textContent.trim().replace(' ↗',''));
        });
      });
      // Show PLN commit step only if PLN is enabled
      if (_PLN_ENABLED) document.getElementById('pln-commit-step').style.display = 'block';
    } else {
      res.innerHTML = `<div style="color:var(--red);font-size:12px">Error: ${d.error||'unknown'}</div>`;
    }
  } catch(e) {
    res.innerHTML = `<div style="color:var(--red);font-size:12px">Network error: ${e.message}</div>`;
  }
}

async function _commitExistingPLN() {
  const res = document.getElementById('existing-action-result');
  if (res) { res.style.display = 'block'; res.innerHTML = '<span style="color:var(--text3)">🔮 Running PLN reasoning…</span>'; }
  try {
    const resp = await fetch('/api/run_pln', { method: 'POST' });
    const d    = await resp.json();
    if (d.ok) {
      if (res) res.innerHTML = `<div style="color:var(--pln);font-size:13px;font-weight:600">
        🔮 PLN committed — ${d.revised||0} revised · ${d.inferred||0} inferred · ${d.contradictions||0} contradictions
        ${d.pln_html ? `<span style="margin-left:10px"><button class="fmt-chip" style="border-color:var(--pln2);color:var(--pln)"
          data-open="${d.pln_html}">🔮 Open PLN graph ↗</button></span>` : ''}
      </div>`;
      // Wire the open button
      res && res.querySelectorAll('[data-open]').forEach(btn => {
        btn.addEventListener('click', () => openViewer('/api/file?path=' + encodeURIComponent(btn.dataset.open), 'Unified PLN AtomSpace'));
      });
    } else {
      if (res) res.innerHTML = `<div style="color:var(--red);font-size:12px">PLN error: ${d.error||d.trace||'Unknown'}</div>`;
    }
  } catch(e) {
    if (res) res.innerHTML = `<div style="color:var(--red);font-size:12px">Network error: ${e.message}</div>`;
  }
}

async function _runPLNInference(runDir, stagingDb, docId, btn) {
  // Find a result container — works in both pipeline view and existing-outputs panel
  const res = document.getElementById('pln-commit-result')
           || document.getElementById('existing-action-result');
  if (res) { res.style.display = 'block'; res.innerHTML = '<span style="color:var(--text3)">🔮 Loading atoms + running PLN forward chaining…</span>'; }
  if (btn) { btn.disabled = true; btn.textContent = '🔮 Running…'; }

  if (!runDir) {
    if (res) res.innerHTML = '<div style="color:var(--red);font-size:12px">PLN error: no run_dir — try re-running the paper first</div>';
    if (btn) { btn.disabled = false; btn.textContent = '🔮 Run PLN Inference'; }
    return;
  }

  try {
    const resp = await fetch('/api/pln-run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_dir: runDir, staging_db: stagingDb, paper_id: docId }),
    });
    const d = await resp.json();
    if (d.ok) {
      const htmlPath = d.pln_html || '';
      const htmlUrl  = htmlPath ? fileUrl(htmlPath) : '';
      if (res) res.innerHTML = `
        <div style="color:var(--pln);font-size:13px;font-weight:600;margin-top:4px">
          🔮 PLN done — ${d.atoms_loaded||0} atoms loaded · ${d.inferred||0} inferred
          ${htmlUrl ? `<button class="fmt-chip" style="margin-left:10px;border-color:var(--pln2);color:var(--pln)"
            onclick="openViewer('${htmlUrl}','PLN AtomSpace')">Open PLN graph ↗</button>` : '(no atoms found — check run_dir has MeTTa output)'}
        </div>`;
      // Update PLN AtomSpace card in the output card row
      if (_PLN_ENABLED) {
        const card = document.getElementById('pln-cards');
        if (card) card.innerHTML = htmlUrl ? `
          <div class="out-card pln-card" onclick="openViewer('${htmlUrl}','PLN AtomSpace')"
               title="Opens PLN graph — original + inferred triples ↗">
            <div class="oc-icon">🔮</div>
            <div class="oc-label">PLN AtomSpace</div>
            <div class="oc-meta">${d.atoms_loaded||0} atoms · ${d.inferred||0} inferred</div>
            <span class="oc-badge oc-pln">MeTTa + stv ↗</span>
          </div>` : '';
      }
    } else {
      if (res) res.innerHTML = `<div style="color:var(--red);font-size:12px">PLN error: ${esc(d.error||d.trace||'Unknown')}</div>`;
    }
  } catch(e) {
    if (res) res.innerHTML = `<div style="color:var(--red);font-size:12px">Network error: ${e.message}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔮 Run PLN Inference'; }
  }
}

function _showPreRunLayerSelector(docId, completedLayers, nextLayer, displayName, substeps) {
  displayName = displayName || docId;
  substeps = substeps || {};
  document.getElementById('pre-run-prompt')?.remove();

  const layerNames = ['','Source','Dedup','Fetch','NER','Schema','Extract','Validate','Publish'];
  const wrap = document.createElement('div');
  wrap.id = 'pre-run-prompt';
  wrap.style.cssText = `
    background:var(--bg2);border:1px solid var(--blue);border-radius:12px;
    padding:20px 24px;margin-top:16px;margin-bottom:16px;
  `;

  const dId = JSON.stringify(docId).replace(/"/g, '&quot;');

  const layerBtns = [3,4,5,6,7,8].map(n => {
    const done   = completedLayers.includes(n);
    const isNext = n === nextLayer;
    const clr    = isNext ? 'var(--blue)' : done ? 'var(--green)' : 'var(--border)';
    const txtClr = isNext ? 'var(--blue)' : done ? 'var(--green)' : 'var(--text3)';
    return `<button class="fmt-chip"
      style="border-color:${clr};color:${txtClr};font-size:10px;padding:3px 9px"
      onclick="_startFromLayer(${dId},${n})"
      title="${done?'Completed':'Not run yet'} — click to start from Layer ${n}">
      L${n} ${layerNames[n]}
    </button>`;
  }).join('');

  // Sub-step buttons for Layer 7
  const l7ss = substeps['7'] || [];
  const substepBtns = [
    l7ss.includes(6) ? `<button class="fmt-chip"
      style="border-color:var(--purple,#a78bfa);color:var(--purple,#a78bfa);font-size:10px;padding:3px 9px"
      onclick="_startFromSubstep(${dId},7,7)"
      title="Skip entity normalization AND semantic validation — resume from atomspace alignment">
      L7.7 skip norm+validation
    </button>` : '',
    l7ss.includes(1) ? `<button class="fmt-chip"
      style="border-color:var(--purple,#a78bfa);color:var(--purple,#a78bfa);font-size:10px;padding:3px 9px"
      onclick="_startFromSubstep(${dId},7,2)"
      title="Skip entity normalization — resume from deduplication">
      L7.2 skip normalization
    </button>` : '',
  ].filter(Boolean).join('');

  const substepRow = substepBtns ? `
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px;
      padding:10px 12px;background:var(--bg3);border-radius:8px">
      <span style="font-size:11px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.05em">
        Layer 7 sub-steps:
      </span>
      ${substepBtns}
    </div>` : '';

  const allDone = !nextLayer;
  wrap.style.borderColor = allDone ? 'var(--green)' : 'var(--blue)';

  wrap.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
      ${allDone
        ? `<span style="font-size:18px;line-height:1;color:var(--green)">◉</span>
           <div>
             <div style="font-size:14px;font-weight:700;color:var(--text)">
               <span style="color:var(--green)">${esc(displayName)}</span> — all 8 layers complete
             </div>
             <div style="font-size:11px;color:var(--text3);margin-top:2px" id="pre-run-result-status">
               Loading results…
             </div>
           </div>`
        : `<span style="font-size:16px;line-height:1;color:var(--blue)">◎</span>
           <div style="font-size:14px;font-weight:700;color:var(--text)">
             Checkpoint found for <span style="color:var(--blue)">${esc(displayName)}</span>
           </div>`
      }
    </div>
    ${!allDone ? `<div style="font-size:12px;color:var(--text3);margin-bottom:12px">
      Layers ${completedLayers.join(', ')} completed — pipeline stopped at Layer ${nextLayer} (${layerNames[nextLayer]}).
      Choose which layer to re-run from, or run fresh.
    </div>` : ''}
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px;
      padding:10px 12px;background:var(--bg3);border-radius:8px">
      <span style="font-size:11px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.05em">
        ${allDone ? 'Re-run from:' : 'Start from:'}
      </span>
      ${layerBtns}
    </div>
    ${substepRow}
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      ${nextLayer ? `<button class="fmt-chip" style="border-color:var(--green);color:var(--green)"
        onclick="_startFromLayer(${dId},${nextLayer})">
        Continue from Layer ${nextLayer}
      </button>` : ''}
      <button class="fmt-chip" style="border-color:var(--border);color:var(--text2)"
        onclick="_dismissPreRun(${dId})">
        Run completely fresh
      </button>
    </div>
  `;

  const hero = document.querySelector('.hero');
  if (hero) hero.after(wrap);
  else document.getElementById('output')?.before(wrap);

  // Auto-load results from disk when all layers are done
  if (allDone) {
    fetch(`/api/results?doc_id=${encodeURIComponent(docId)}`)
      .then(r => r.json())
      .then(d => {
        if (d.error) {
          const st = document.getElementById('pre-run-result-status');
          if (st) st.textContent = 'Results not found on disk — re-run Layer 8 to generate.';
          return;
        }
        const st = document.getElementById('pre-run-result-status');
        if (st) {
          const edges = d.neo4j_edges || d.metta_edges || 0;
          const pct   = d.total > 0 ? Math.round(d.verified / d.total * 100) : 0;
          st.innerHTML = `<span style="color:var(--green)">✓</span> ${edges} edges · ${pct}% confirmed — results loaded below`;
        }
        renderOutput(d);
      })
      .catch(() => {
        const st = document.getElementById('pre-run-result-status');
        if (st) st.textContent = 'Could not load results — re-run Layer 8 to regenerate.';
      });
  }
}

async function _startFromLayer(docId, fromLayer) {
  document.getElementById('pre-run-prompt')?.remove();
  await _resumeFromLayer(docId, fromLayer, true); // skipConfirm — user already chose the layer
}

async function _startFromSubstep(docId, layer, substep) {
  document.getElementById('pre-run-prompt')?.remove();
  const stepLabels = {2: 'deduplication', 7: 'atomspace alignment'};
  const label = stepLabels[substep] || `step ${substep}`;
  if (!confirm(`Resume Layer ${layer} from ${label}?\n\nSaved sub-steps before this will be reused.`)) return;

  const res = document.getElementById('existing-action-result');

  const fd = new FormData();
  fd.append('doc_id',       docId);
  fd.append('from_layer',   String(layer));
  fd.append('from_substep', String(substep));
  try {
    const r = await fetch('/api/resume-from-layer', { method:'POST', body:fd });
    const d = await r.json();
    if (!d.ok) { alert(`Sub-step reset failed: ${d.error}`); return; }
  } catch(e) { alert(`Server error: ${e.message}`); return; }

  _skipCheckpointCheck = true;
  AppState.clear();
  document.getElementById('existing-actions')?.remove();
  const inp = document.getElementById('smart-input');
  if (inp) { inp.value = docId; detectInput(docId); }
  startRun();
}

let _skipCheckpointCheck = false;  // set true to bypass pre-run check once

function _dismissPreRun(resolvedDocId) {
  document.getElementById('pre-run-prompt')?.remove();
  // Discard checkpoint — use resolved doc_id from checkpoint-status, fall back to input value
  const val = resolvedDocId || document.getElementById('smart-input').value.trim();
  const fd  = new FormData();
  fd.append('doc_id', val);
  fetch('/api/discard-paper', {method:'POST', body:fd})
    .catch(()=>{})
    .finally(() => {
      _skipCheckpointCheck = true;
      startRun();
    });
}

function _resumePipeline(docId, fromLayer) {
  // Kept for backward compatibility — calls the full resume-layer flow
  _resumeFromLayer(docId, fromLayer);
}

async function _resumeFromLayer(docId, fromLayer, skipConfirm) {
  const layerNames = ['','Source','Dedup','Fetch','NER','Schema','Extract','Validate','Publish'];
  const name = layerNames[fromLayer] || `Layer ${fromLayer}`;

  const res = document.getElementById('existing-action-result');
  if (res) { res.style.display = 'block'; res.innerHTML = `<span style="color:var(--text3)">Preparing to resume from Layer ${fromLayer} (${name})…</span>`; }

  if (!docId) {
    if (res) res.innerHTML = `<span style="color:var(--red)">Error: no paper ID found. Please re-enter the paper ID above and try again.</span>`;
    return;
  }

  if (!skipConfirm && !confirm(`Resume "${docId}" from Layer ${fromLayer} — ${name}?\n\nLayers ${fromLayer}–8 will re-run.\nLayers 1–${fromLayer > 3 ? fromLayer-1 : 2} will be skipped.`)) {
    if (res) res.style.display = 'none';
    return;
  }

  if (res) res.innerHTML = `<span style="color:var(--text3)">Resetting checkpoint for ${docId}…</span>`;

  // 1. Reset checkpoint
  const fd = new FormData();
  fd.append('doc_id',     docId);
  fd.append('from_layer', String(fromLayer));
  try {
    const r = await fetch('/api/resume-from-layer', { method:'POST', body:fd });
    const d = await r.json();
    if (!d.ok) {
      if (res) res.innerHTML = `<span style="color:var(--red)">Checkpoint reset failed: ${d.error}</span>`;
      return;
    }
    if (res) res.innerHTML = `<span style="color:var(--green)">Checkpoint reset — kept layers ${JSON.stringify(d.kept_layers)}. Starting pipeline…</span>`;
  } catch(e) {
    if (res) res.innerHTML = `<span style="color:var(--red)">Server error: ${e.message}</span>`;
    return;
  }

  // 2. Start pipeline — skip checkpoint check so we don't loop back to selector
  _skipCheckpointCheck = true;
  document.getElementById('pre-run-prompt')?.remove();
  AppState.clear();
  document.getElementById('existing-actions')?.remove();
  const inp = document.getElementById('smart-input');
  if (inp) { inp.value = docId; detectInput(docId); }
  // If docId is a PDF filename, detectInput sets isPdf=true but there's no pdfFile.
  // We're resuming from checkpoint — no re-upload needed — so force text mode.
  if (isPdf && !pdfFile) { isPdf = false; }
  startRun();
}

async function _reprocessPaper(docId) {
  if (!confirm(`Reprocess "${docId}" from scratch?\n\nThis will delete all existing outputs and run all 8 layers again.`)) return;

  // 1. Tell server to delete all existing outputs for this paper
  const fd = new FormData();
  fd.append('doc_id', docId);
  try {
    const r = await fetch('/api/discard-paper', { method:'POST', body:fd });
    const d = await r.json();
    if (!d.ok) {
      alert(`Could not clear existing data: ${d.error}`);
      return;
    }
  } catch(e) {
    alert(`Server error while clearing data: ${e.message}`);
    return;
  }

  // 2. Reset UI and start fresh pipeline run
  AppState.clear();
  const inp = document.getElementById('smart-input');
  if (inp) { inp.value = docId; detectInput(docId); }
  // Remove existing-actions card so it doesn't linger
  document.getElementById('existing-actions')?.remove();
  _skipCheckpointCheck = true;
  startRun();
}

