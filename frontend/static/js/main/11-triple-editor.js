// ── Triple Editor Panel ───────────────────────────────────────────────────────

let _triplesRelations = null;
let _tripleSearchTimer = null;

async function _loadRelations() {
  if (_triplesRelations) return _triplesRelations;
  try {
    const r = await fetch('/api/kg/relations');
    const d = await r.json();
    _triplesRelations = d.relations || [];
  } catch (_) { _triplesRelations = []; }
  return _triplesRelations;
}

async function openUnifiedKGPanel() {
  // Remove any existing panel
  const existing = document.getElementById('triples-panel');
  if (existing) existing.remove();

  const panel = document.createElement('div');
  panel.id = 'triples-panel';
  panel.dataset.dbPath = 'data/triple_store_neo4j.db';
  panel.style.cssText = `margin-top:16px;background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:20px 24px;`;

  const plnTab = _PLN_ENABLED ? `
    <div class="utab" id="utab-pln" onclick="switchUnifiedTab('pln',this)"
         style="padding:5px 16px;border-radius:8px;font-size:11px;font-weight:600;cursor:pointer;
                border:1px solid var(--border);color:var(--text3);background:var(--bg3)">
      🔮 PLN AtomSpace
    </div>` : '';

  panel.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap">
      <span style="font-size:14px;font-weight:700;color:var(--text)">✎ Unified KG</span>
      <div style="display:flex;gap:6px;align-items:center">
        <div class="utab active-utab" id="utab-triples" onclick="switchUnifiedTab('triples',this)"
             style="padding:5px 16px;border-radius:8px;font-size:11px;font-weight:600;cursor:pointer;
                    border:1px solid var(--pln);color:var(--pln);background:var(--bg2)">
          ✎ MeTTa Triples
        </div>
        ${plnTab}
      </div>
      <button onclick="document.getElementById('triples-panel').remove()"
        style="margin-left:auto;background:none;border:none;color:var(--text3);cursor:pointer;font-size:18px;line-height:1">✕</button>
    </div>

    <!-- Triples tab -->
    <div id="unified-tab-triples">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
        <input id="triples-search" type="text" placeholder="Search triples…"
          style="flex:1;max-width:280px;padding:5px 10px;border-radius:6px;
            border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:12px" />
        <span id="triples-total" style="font-size:11px;color:var(--text3)"></span>
      </div>
      <div id="triples-table-wrap" style="overflow-x:auto"></div>
      <div id="triples-pager" style="margin-top:10px;display:flex;gap:8px;align-items:center;font-size:12px;color:var(--text3)"></div>
    </div>

    <!-- PLN tab (hidden by default) -->
    <div id="unified-tab-pln" style="display:none">
      <div id="unified-pln-content" style="min-height:120px;display:flex;align-items:center;justify-content:center;
           color:var(--text3);font-size:12px">Checking PLN status…</div>
    </div>
  `;

  document.getElementById('triples-search')?.addEventListener('input', () => {
    clearTimeout(_tripleSearchTimer);
    _tripleSearchTimer = setTimeout(() => {
      const p = document.getElementById('triples-panel');
      if (p) { p._offset = 0; p._q = document.getElementById('triples-search').value; _loadTriples(); }
    }, 350);
  });

  const anchor = document.getElementById('out-cards') || document.getElementById('output');
  anchor.after ? anchor.after(panel) : anchor.appendChild(panel);

  panel._dbPath = 'data/triple_store_neo4j.db';
  panel._runDir = '';
  panel._offset = 0;
  panel._limit  = 20;
  panel._q      = '';

  await _loadRelations();
  await _loadTriples();
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function switchUnifiedTab(name, el) {
  // Update tab styles
  document.querySelectorAll('.utab').forEach(t => {
    t.style.borderColor  = 'var(--border)';
    t.style.color        = 'var(--text3)';
    t.style.background   = 'var(--bg3)';
  });
  el.style.borderColor = 'var(--pln)';
  el.style.color       = 'var(--pln)';
  el.style.background  = 'var(--bg2)';

  // Show/hide panes
  const triplesPane = document.getElementById('unified-tab-triples');
  const plnPane     = document.getElementById('unified-tab-pln');
  if (triplesPane) triplesPane.style.display = name === 'triples' ? '' : 'none';
  if (plnPane)     plnPane.style.display     = name === 'pln'     ? '' : 'none';

  if (name === 'pln') _loadUnifiedPLNTab();
}

async function _loadUnifiedPLNTab() {
  const wrap = document.getElementById('unified-pln-content');
  if (!wrap) return;
  wrap.innerHTML = '<span style="color:var(--text3)">Checking PLN atomspace…</span>';
  try {
    const st = await (await fetch('/api/pln-unified-status')).json();
    if (st.exists && st.path) {
      const src = '/api/file?path=' + encodeURIComponent(st.path);
      wrap.innerHTML = `
        <div style="width:100%;display:flex;flex-direction:column;gap:10px">
          <div style="display:flex;align-items:center;gap:10px">
            <span style="color:var(--pln);font-size:12px;font-weight:600">🔮 Unified PLN AtomSpace</span>
            <button class="fmt-chip" style="border-color:var(--pln2);color:var(--pln);font-size:10px"
              onclick="_runUnifiedPLN(this)">↺ Regenerate</button>
            <a class="fmt-chip" style="border-color:var(--border);color:var(--text2);font-size:10px;text-decoration:none"
              href="${src}" target="_blank">↗ Open full screen</a>
          </div>
          <iframe src="${src}" style="width:100%;height:600px;border:1px solid var(--border);border-radius:8px;background:#0d1117"></iframe>
        </div>`;
    } else {
      wrap.innerHTML = `
        <div style="text-align:center;padding:32px 0">
          <div style="color:var(--text3);font-size:13px;margin-bottom:12px">
            No unified PLN graph yet. Load all committed papers into PeTTaChainer atomspace.
          </div>
          <button class="fmt-chip" style="border-color:var(--pln);color:var(--pln);font-size:12px;padding:6px 18px"
            onclick="_runUnifiedPLN(this)">🔮 Generate Unified PLN</button>
        </div>`;
    }
  } catch(e) {
    wrap.innerHTML = `<span style="color:var(--red);font-size:12px">Error: ${esc(e.message)}</span>`;
  }
}

async function _runUnifiedPLN(btn) {
  const wrap = document.getElementById('unified-pln-content');
  if (btn) { btn.disabled = true; btn.textContent = '⟳ Running…'; }
  if (wrap) wrap.innerHTML = '<span style="color:var(--pln);font-size:12px">🔮 Loading all papers into PeTTaChainer atomspace… this may take a moment.</span>';
  try {
    const d = await (await fetch('/api/pln-unified-run', {method:'POST'})).json();
    if (d.ok && d.pln_html) {
      const src = '/api/file?path=' + encodeURIComponent(d.pln_html);
      if (wrap) wrap.innerHTML = `
        <div style="width:100%;display:flex;flex-direction:column;gap:10px">
          <div style="display:flex;align-items:center;gap:10px">
            <span style="color:var(--pln);font-size:12px;font-weight:600">
              🔮 Unified PLN — ${d.papers||0} papers · ${d.atoms_loaded||0} atoms · ${d.inferred||0} inferred
            </span>
            <button class="fmt-chip" style="border-color:var(--pln2);color:var(--pln);font-size:10px"
              onclick="_runUnifiedPLN(this)">↺ Regenerate</button>
            <a class="fmt-chip" style="border-color:var(--border);color:var(--text2);font-size:10px;text-decoration:none"
              href="${src}" target="_blank">↗ Open full screen</a>
          </div>
          <iframe src="${src}" style="width:100%;height:600px;border:1px solid var(--border);border-radius:8px;background:#0d1117"></iframe>
        </div>`;
    } else {
      if (wrap) wrap.innerHTML = `<span style="color:var(--red);font-size:12px">PLN error: ${esc(d.error||'Unknown')}</span>`;
      if (btn) { btn.disabled = false; btn.textContent = '🔮 Generate Unified PLN'; }
    }
  } catch(e) {
    if (wrap) wrap.innerHTML = `<span style="color:var(--red);font-size:12px">Network error: ${esc(e.message)}</span>`;
    if (btn) { btn.disabled = false; btn.textContent = '🔮 Generate Unified PLN'; }
  }
}

async function openTriplesPanel(dbPath, runDir, label) {
  const existing = document.getElementById('triples-panel');
  // Toggle off if clicking the same panel
  if (existing && existing.dataset.dbPath === dbPath) { existing.remove(); return; }
  if (existing) existing.remove();

  const panel = document.createElement('div');
  panel.id = 'triples-panel';
  panel.dataset.dbPath = dbPath;
  panel.style.cssText = `margin-top:16px;background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:20px 24px;`;
  panel.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap">
      <span style="font-size:14px;font-weight:700;color:var(--text)">✎ ${esc(label)}</span>
      <input id="triples-search" type="text" placeholder="Search triples…"
        style="flex:1;max-width:280px;padding:5px 10px;border-radius:6px;
          border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:12px" />
      <span id="triples-total" style="font-size:11px;color:var(--text3)"></span>
      <button onclick="document.getElementById('triples-panel').remove()"
        style="margin-left:auto;background:none;border:none;color:var(--text3);cursor:pointer;font-size:18px;line-height:1">✕</button>
    </div>
    <div id="triples-table-wrap" style="overflow-x:auto"></div>
    <div id="triples-pager" style="margin-top:10px;display:flex;gap:8px;align-items:center;font-size:12px;color:var(--text3)"></div>
  `;

  document.getElementById('triples-search').addEventListener('input', () => {
    clearTimeout(_tripleSearchTimer);
    _tripleSearchTimer = setTimeout(() => {
      const p = document.getElementById('triples-panel');
      if (p) { p._offset = 0; p._q = document.getElementById('triples-search').value; _loadTriples(); }
    }, 350);
  });

  const anchor = document.getElementById('out-cards') || document.getElementById('output');
  anchor.after ? anchor.after(panel) : anchor.appendChild(panel);

  panel._dbPath = dbPath;
  panel._runDir = runDir;
  panel._offset = 0;
  panel._limit  = 20;
  panel._q      = '';

  await _loadRelations();
  await _loadTriples();
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function _loadTriples() {
  const panel = document.getElementById('triples-panel');
  if (!panel) return;
  const wrap = document.getElementById('triples-table-wrap');
  wrap.innerHTML = '<span style="color:var(--text3);font-size:12px">Loading…</span>';
  try {
    const { _dbPath, _runDir, _offset, _limit, _q } = panel;
    const url = `/api/kg/triples?db_path=${encodeURIComponent(_dbPath)}&q=${encodeURIComponent(_q||'')}&limit=${_limit}&offset=${_offset}`;
    const d = await (await fetch(url)).json();
    const tot = document.getElementById('triples-total');
    if (tot) tot.textContent = `${d.total || 0} triples`;
    if (!d.triples || !d.triples.length) {
      wrap.innerHTML = '<span style="color:var(--text3);font-size:12px">No triples found.</span>';
      const pg = document.getElementById('triples-pager'); if (pg) pg.innerHTML = '';
      return;
    }
    panel._triples = d.triples;
    _renderTriplesTable(d.triples);
    _renderTriplesPager(d.total, _offset, _limit);
  } catch(e) {
    wrap.innerHTML = `<span style="color:var(--red);font-size:12px">Error: ${esc(e.message)}</span>`;
  }
}

function _renderTriplesTable(triples) {
  const panel = document.getElementById('triples-panel');
  const rows = triples.map(t => `
    <tr id="trow-${t.id}" style="border-bottom:1px solid var(--border)">
      <td style="padding:6px 8px;font-size:12px;color:var(--text);max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(t.subject_name)}">${esc(t.subject_name)}</td>
      <td style="padding:6px 8px">
        <span style="font-size:10px;background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:2px 6px;color:var(--text2);white-space:nowrap">${esc(t.relation)}</span>
      </td>
      <td style="padding:6px 8px;font-size:12px;color:var(--text);max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(t.object_name)}">${esc(t.object_name)}</td>
      <td style="padding:6px 8px;font-size:10px;color:var(--text3)">${esc(t.section||'')}</td>
      <td style="padding:6px 8px;white-space:nowrap">
        <button onclick="_editTriplesRow(${t.id})"
          style="background:none;border:1px solid var(--border);border-radius:4px;color:var(--text2);cursor:pointer;padding:2px 7px;font-size:11px">✎</button>
        <button onclick="_deleteTriple(${t.id})"
          style="background:none;border:1px solid var(--red);border-radius:4px;color:var(--red);cursor:pointer;padding:2px 7px;font-size:11px;margin-left:4px">✗</button>
      </td>
    </tr>`).join('');

  document.getElementById('triples-table-wrap').innerHTML = `
    <table style="width:100%;border-collapse:collapse;table-layout:fixed">
      <colgroup>
        <col style="width:28%"><col style="width:20%"><col style="width:28%"><col style="width:12%"><col style="width:12%">
      </colgroup>
      <thead>
        <tr style="border-bottom:2px solid var(--border)">
          <th style="text-align:left;padding:5px 8px;font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;font-weight:600">Subject</th>
          <th style="text-align:left;padding:5px 8px;font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;font-weight:600">Relation</th>
          <th style="text-align:left;padding:5px 8px;font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;font-weight:600">Object</th>
          <th style="text-align:left;padding:5px 8px;font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;font-weight:600">Section</th>
          <th></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function _renderTriplesPager(total, offset, limit) {
  const pager = document.getElementById('triples-pager');
  if (!pager) return;
  const page  = Math.floor(offset / limit) + 1;
  const pages = Math.ceil(total / limit);
  if (pages <= 1) { pager.innerHTML = ''; return; }
  pager.innerHTML = `
    <button onclick="_tripleChangePage(-1)" ${page<=1?'disabled':''}
      style="background:none;border:1px solid var(--border);border-radius:4px;padding:2px 9px;cursor:pointer;color:var(--text2)">←</button>
    <span>Page ${page} / ${pages}</span>
    <button onclick="_tripleChangePage(1)" ${page>=pages?'disabled':''}
      style="background:none;border:1px solid var(--border);border-radius:4px;padding:2px 9px;cursor:pointer;color:var(--text2)">→</button>`;
}

function _tripleChangePage(dir) {
  const p = document.getElementById('triples-panel');
  if (!p) return;
  p._offset = Math.max(0, p._offset + dir * p._limit);
  _loadTriples();
}

function _editTriplesRow(id) {
  const panel  = document.getElementById('triples-panel');
  const triple = (panel._triples || []).find(t => t.id === id);
  if (!triple) return;
  const rels = (_triplesRelations || []);
  const opts = rels.map(r => `<option value="${esc(r)}"${r===triple.relation?' selected':''}>${esc(r)}</option>`).join('');
  const inpStyle = `width:100%;padding:3px 6px;border-radius:4px;border:1px solid var(--blue);background:var(--bg3);color:var(--text);font-size:12px;box-sizing:border-box`;
  document.getElementById(`trow-${id}`).innerHTML = `
    <td style="padding:4px 8px"><input id="es-${id}" value="${esc(triple.subject_name)}" style="${inpStyle}" /></td>
    <td style="padding:4px 8px">
      <select id="er-${id}" style="width:100%;padding:3px 4px;border-radius:4px;border:1px solid var(--blue);background:var(--bg3);color:var(--text);font-size:11px">${opts}</select>
    </td>
    <td style="padding:4px 8px"><input id="eo-${id}" value="${esc(triple.object_name)}" style="${inpStyle}" /></td>
    <td style="padding:4px 8px;font-size:10px;color:var(--text3)">${esc(triple.section||'')}</td>
    <td style="padding:4px 8px;white-space:nowrap">
      <button onclick="_saveTriplesEdit(${id})"
        style="background:none;border:1px solid var(--green);border-radius:4px;color:var(--green);cursor:pointer;padding:2px 8px;font-size:11px">✓ Save</button>
      <button onclick="_loadTriples()"
        style="background:none;border:1px solid var(--border);border-radius:4px;color:var(--text3);cursor:pointer;padding:2px 7px;font-size:11px;margin-left:4px">✗</button>
    </td>`;
}

async function _saveTriplesEdit(id) {
  const panel  = document.getElementById('triples-panel');
  const triple = (panel._triples || []).find(t => t.id === id);
  if (!triple) return;
  const subj = document.getElementById(`es-${id}`).value.trim();
  const rel  = document.getElementById(`er-${id}`).value;
  const obj  = document.getElementById(`eo-${id}`).value.trim();
  if (!subj || !rel || !obj) { alert('Subject, Relation, and Object cannot be empty.'); return; }
  const row = document.getElementById(`trow-${id}`);
  row.style.opacity = '0.5';
  const fd = new FormData();
  fd.append('db_path',      panel._dbPath);
  fd.append('run_dir',      panel._runDir || '');
  fd.append('subject_name', subj);
  fd.append('subject_id',   triple.subject_id || `TEXT:${subj}`);
  fd.append('relation',     rel);
  fd.append('object_name',  obj);
  fd.append('object_id',    triple.object_id  || `TEXT:${obj}`);
  try {
    const d = await (await fetch(`/api/kg/triples/${id}`, { method:'PATCH', body:fd })).json();
    if (d.ok) { await _loadTriples(); }
    else { alert(`Save failed: ${d.error}`); row.style.opacity = '1'; }
  } catch(e) { alert(`Error: ${e.message}`); row.style.opacity = '1'; }
}

async function _deleteTriple(id) {
  if (!confirm('Delete this triple? The graph and verification report will rebuild in the background.')) return;
  const panel = document.getElementById('triples-panel');
  const row   = document.getElementById(`trow-${id}`);
  if (row) row.style.opacity = '0.4';
  try {
    const url = `/api/kg/triples/${id}?db_path=${encodeURIComponent(panel._dbPath)}&run_dir=${encodeURIComponent(panel._runDir||'')}`;
    const d = await (await fetch(url, { method:'DELETE' })).json();
    if (d.ok) { await _loadTriples(); }
    else { alert(`Delete failed: ${d.error}`); if (row) row.style.opacity = '1'; }
  } catch(e) { alert(`Error: ${e.message}`); if (row) row.style.opacity = '1'; }
}

