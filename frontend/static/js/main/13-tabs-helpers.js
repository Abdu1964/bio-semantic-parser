// ── Tabs + helpers ────────────────────────────────────────────────────────────

function switchOut(pane, btn) {
  document.querySelectorAll('.otab').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.opane').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('op-'+pane).classList.add('active');
}

function toggleL(n) {
  const c = document.getElementById('lc-'+n);
  c.classList.toggle('open');
}

function resetUI() {
  // Reset stream terminal
  const ll = document.getElementById('stream-lines');
  if (ll) ll.innerHTML = '<div class="stream-idle">Waiting for pipeline to start…</div>';
  // Remove layer nav pills
  for (let n=1;n<=8;n++) { document.getElementById('snav-'+n)?.remove(); }
  exitFullscreen();
  // Reset simple view card
  const _se = _simpleEls();
  
  if (_se.title)  { _se.title.textContent = 'Ready'; }
  if (_se.sub)    { _se.sub.textContent   = 'Enter a paper ID or upload a PDF'; }
  if (_se.bar)    { _se.bar.style.width   = '0%'; }
  if (_se.eta)    { _se.eta.textContent   = ''; }
  if (_se.stepr)  { _se.stepr.textContent = ''; }
  if (_se.stepEl) { _se.stepEl.textContent = ''; }
  if (_se.wrap)   { _se.wrap.style.display   = 'none'; }
  if (_se.steam)  { _se.steam.style.display  = 'none'; }
  if (_se.layMsg) { _se.layMsg.style.display = 'none'; }
  for (let n=1;n<=8;n++) {
    const c = document.getElementById('lc-'+n); if(!c)continue;
    c.className = 'lcard pending';
    const lm = document.getElementById('lm-'+n); if(lm) lm.textContent = defaultMsg(n);
    const ls = document.getElementById('ls-'+n); if(ls) ls.textContent = '—';
    const lb = document.getElementById('lbody-'+n); if(lb) lb.innerHTML = '';
    const b  = document.getElementById('lb-'+n); if(b) b.style.width='0%';
  }
  const out = document.getElementById('output');
  if (out) out.classList.remove('show');
  const commitSec = document.getElementById('commit-section');
  if (commitSec) commitSec.style.display = 'none';
  const inspectSec = document.getElementById('inspect-section');
  if (inspectSec) inspectSec.style.display = 'none';
  const reviewSec = document.getElementById('review-section');
  if (reviewSec) { reviewSec.style.display = 'none'; const rc = document.getElementById('review-cards'); if(rc) rc.innerHTML=''; }
  const breakdown = document.getElementById('staging-breakdown');
  if (breakdown) { breakdown.style.display = 'none'; breakdown.innerHTML = ''; }
  _stagingDb = ''; _docId = '';
  Object.keys(_layerDurations).forEach(k => delete _layerDurations[k]);
  Object.keys(_layerStartTimes).forEach(k => delete _layerStartTimes[k]);
  const oc = document.getElementById('out-cards');
  if (oc) oc.innerHTML = '';
  const vc = document.getElementById('ver-content');
  if (vc) { vc.style.display = 'none'; vc.innerHTML = ''; }
  window._outCards = [];
  closeModal();
}

function defaultMsg(n) {
  return ['','Resolves input and selects the correct data source',
    'Checks if this paper is already in the knowledge graph',
    'Fetches and segments the paper into text chunks',
    'Tags biological entities and detects negation',
    'Loads 87 relation types and 39 entity types',
    'Extracts structured biological relations with Pydantic enforcement',
    'Validates, deduplicates, and detects contradictions',
    'Writes triples to Neo4j CSV and MeTTa atomspace'][n];
}

function setStatus(state, text) {
  const dot  = document.getElementById('dot');
  const span = document.getElementById('status-txt');
  span.textContent  = text;
  dot.className     = 'dot' + (state==='running'?' running':state==='error'?' error':'');
}

function setBtn(disabled) { document.getElementById('run-btn').disabled = disabled; }
function toggleLogLines(n) {
  const ll  = document.getElementById('ll-' + n);
  const btn = document.querySelector(`#lt-${n} .log-toggle-btn`);
  if (!ll) return;
  const collapsed = ll.classList.toggle('collapsed');
  if (btn) btn.textContent = collapsed ? '▸ expand' : '▾ collapse';
}

function logLineClass(msg) {
  if (!msg) return 'll-meta';
  const m = msg.toLowerCase();
  if (m.startsWith('step ') || m.includes('— step'))   return 'll-step';
  if (m.includes('✓') || m.includes('ok') || m.includes('done') || m.includes('generated')) return 'll-ok';
  if (m.includes('coref') || m.includes('rewrite') || m.includes('before') || m.includes('after :')) return 'll-coref';
  if (m.includes('chunk') && (m.includes('[') || m.includes('words'))) return 'll-chunk';
  if (m.includes('entity') || m.includes('negat') || m.includes('label')) return 'll-entity';
  if (m.includes('→') || m.includes('relation') || m.includes('conf:') || m.includes('valid') || m.includes('→')) return 'll-rel';
  if (m.includes('warning') || m.includes('skip') || m.includes('error') || m.includes('fail')) return 'll-warn';
  if (m.startsWith('  ') || m.startsWith('#') || m.startsWith('─')) return 'll-meta';
  return '';
}

function esc(s) { if(!s)return''; return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// Parses the LLM's free-text suggested_correction ("Subject: X; Relation: Y;
// Object: Z" — any subset, any order) into individual fields. The validator
// doesn't always suggest all three, so each field is optional.
function _parseSuggestionFields(text) {
  const out = {};
  const grab = (label) => {
    // LLM suggestions use comma OR semicolon as field separator — handle both
    const m = text.match(new RegExp(label + '\\s*:\\s*([^;,]+?)(?=[,;]\\s*(?:Subject|Relation|Object)\\s*:|$)', 'i'));
    return m ? m[1].trim() : '';
  };
  const subj = grab('Subject');
  let rel    = grab('Relation');
  const obj  = grab('Object');
  if (subj) out.subject = subj;
  if (obj)  out.object  = obj;

  // Fallback: "Change RelationType.X to RelationType.Y" — extract the NEW relation (after "to")
  if (!rel) {
    const changeM = text.match(/\bto\s+RelationType\.([A-Z][A-Z0-9_]+)/i)
                 || text.match(/\bto\s+([A-Z][A-Z0-9_]{2,})\s*[.,]?\s*$/i);
    if (changeM) rel = changeM[1];
  }

  if (rel) {
    const relMatch = rel.match(/([A-Z][A-Z0-9_]{2,})/);
    // Lowercase to match the taxonomy's canonical RelationType.value form
    // ("regulates", not "REGULATES") — every other code path (extraction,
    // dedup's exact-match SQL constraint, concept alignment's clustering)
    // stores/compares relation strings in that lowercase form, so leaving
    // this uppercase silently breaks case-sensitive matches downstream.
    out.relation = relMatch ? relMatch[1].toLowerCase() : '';
  }
  return out;
}

// ── Export helpers ─────────────────────────────────────────────────────────────

async function exportDownload(url, body, filename) {
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: 'Export failed' }));
      alert(err.error || `Export failed (${resp.status})`);
      return;
    }
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  } catch (e) {
    alert('Export failed: ' + e.message);
  }
}

function toggleQbExportMenu(e) {
  e.stopPropagation();
  const menu = document.getElementById('qb-export-menu');
  if (menu) menu.classList.toggle('open');
}

function toggleUnifiedExportMenu(e) {
  e.stopPropagation();
  const menu = document.getElementById('unified-export-menu');
  if (menu) menu.classList.toggle('open');
}

// Close export menus on outside click
document.addEventListener('click', (e) => {
  if (!e.target.closest('.qb-export-dropdown')) {
    document.querySelectorAll('.qb-export-menu').forEach(m => m.classList.remove('open'));
  }
});

function exportUnifiedKG(fmt) {
  const dbSel = document.getElementById('unified-db-sel');
  const db = dbSel ? dbSel.value : 'neo4j';
  const ext = fmt === 'json' ? 'json' : fmt === 'metta' ? 'metta' : 'zip';

  let paper = '';
  try {
    const frame = document.getElementById('unified-frame');
    if (frame && frame.contentWindow) {
      const doc = frame.contentDocument || frame.contentWindow.document;
      const filter = doc.getElementById('paper-filter');
      if (filter && filter.value && filter.value !== 'all') {
        paper = filter.value;
      }
    }
  } catch (e) {
    console.error('Could not read paper filter from iframe:', e);
  }

  exportDownload('/api/export/unified', { db, format: fmt, paper: paper }, `unified_kg_${db}.${ext}`);
  // Close the menu
  document.querySelectorAll('#unified-export-menu').forEach(m => m.classList.remove('open'));
}

