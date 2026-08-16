// ── Sources slide-over ────────────────────────────────────────────────────────

async function openSources() {
  document.getElementById('overlay').classList.add('open');
  await loadSourcesPanel();
}

function closeSources() {
  document.getElementById('overlay').classList.remove('open');
}

async function loadSources() {
  // Load sources for header chips
  try {
    const res  = await fetch('/api/sources');
    const data = await res.json();
    renderHeaderChips(data);
  } catch(e) {}
}

function renderHeaderChips(sources) {
  const el = document.getElementById('header-sources');
  if (!el) return;
  const main = sources.filter(s => s.name !== 'pdf' && s.name !== 'www.medrxiv.org');
  // Add URL option
  const urlChip = `<button class="sources-btn" id="chip-url" onclick="quickSelectSource('url',this)">URL</button>`;
  el.innerHTML = main.map(s =>
    `<button class="sources-btn" id="chip-${esc(s.name)}" onclick="quickSelectSource('${esc(s.name)}',this)">
       ${esc(s.name)}
     </button>`
  ).join('') + urlChip;
}


function quickSelectSource(name, chipEl) {
  selectedSource = name;
  const idMap = {
    pmc:           'PMC ID — e.g. PMC6746067',
    pubmed:        'PubMed PMID — e.g. 25062748',
    geo:           'GEO accession — e.g. GSE12345678',
    clinicaltrials:'ClinicalTrials NCT ID — e.g. NCT04580043',
    biorxiv:       'bioRxiv DOI — e.g. 10.1101/2024.01.01.000001',
    url:           'Paper URL — https://www.ncbi.nlm.nih.gov/pmc/...',
  };
  const inp = document.getElementById('smart-input');
  inp.value       = '';                           // clear any previous value
  inp.placeholder = idMap[name] || `Enter ID for ${name}`;
  inp.dataset.source = name;
  inp.focus();

  const badge = document.getElementById('detect-badge');
  badge.textContent = name === 'url' ? 'URL' : name.toUpperCase();
  badge.className   = 'detect-badge ' + (name==='pmc'?'pmc':name==='pubmed'?'pubmed':name==='url'?'url':'pmc');

  document.querySelectorAll('#header-sources .sources-btn').forEach(b => b.classList.remove('active'));
  if (chipEl) chipEl.classList.add('active');
}

async function loadSourcesPanel() {
  const body = document.getElementById('so-body');
  body.innerHTML = '<div style="color:var(--text3);font-size:13px">Loading…</div>';

  // Load sources first (fast), then processed papers separately
  const srcRes  = await fetch('/api/sources');
  const sources = await srcRes.json();

  // Render sources immediately, load processed lazily
  let processed = [];
  try {
    const procRes = await fetch('/api/processed');
    processed = await procRes.json();
  } catch(e) { processed = []; }

  const srcCards = sources.map(s => `
    <div class="src-card" onclick="useSource('${esc(s.name)}')">
      <div class="src-info">
        <div class="src-name">${esc(s.name)}</div>
        <div class="src-meta">${esc((s.base_url||s.watch_dir||'file').slice(0,38))}</div>
      </div>
      <div class="src-right">
        <span class="src-fmt fmt-${s.format||'xml'}">${(s.format||'').toUpperCase()}</span>
        <span class="src-count">${s.processed_count||0} papers</span>
      </div>
      <button class="src-del" onclick="delSource(event,'${esc(s.name)}')">✕</button>
    </div>`).join('');

  const procItems = processed.slice(0,20).map(p => `
    <div class="proc-item" onclick="reuseDoc(${JSON.stringify(p.doc_id).replace(/"/g,'&quot;')},${JSON.stringify(p.source_name).replace(/"/g,'&quot;')})">
      <div class="proc-title">${esc(p.title||p.doc_id)}</div>
      <div class="proc-meta">
        <span>${esc(p.source_name)}</span>
        <span>${(p.processed_at||'').slice(0,10)}</span>
        ${p.format?`<span>${esc(p.format)}</span>`:''}
      </div>
    </div>`).join('') || '<div style="color:var(--text3);font-size:12px">No papers processed yet.</div>';

  body.innerHTML = `
    <div class="so-section-title">Registered sources</div>
    ${srcCards}
    <button class="add-btn" onclick="toggleAddForm()">＋ &nbsp; Add new source</button>
    <div class="add-form" id="add-form">
      <div class="field"><label>Name</label><input id="ns-name" placeholder="e.g. medrxiv"/></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <div class="field"><label>Type</label>
          <select id="ns-type"><option value="api">API</option><option value="file">File</option></select>
        </div>
        <div class="field"><label>Format</label>
          <select id="ns-fmt"><option value="xml">XML</option><option value="json">JSON</option><option value="pdf">PDF</option></select>
        </div>
      </div>
      <div class="field"><label>Base URL</label><input id="ns-url" placeholder="https://api.example.org/"/></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <div class="field"><label>ID field</label><input id="ns-id" placeholder="doi"/></div>
        <div class="field"><label>Rate limit</label><input id="ns-rate" type="number" value="5"/></div>
      </div>
      <div class="field"><label>API key env</label><input id="ns-key" placeholder="MY_API_KEY"/></div>
      <div style="display:flex;align-items:center;gap:10px;margin-top:2px">
        <button class="save-btn" onclick="saveSource()">Save source</button>
        <span class="cancel-link" onclick="toggleAddForm()">Cancel</span>
      </div>
    </div>
    <div class="so-section-title" style="margin-top:4px">Processed papers</div>
    ${procItems}
  `;
}

function toggleAddForm() {
  const f = document.getElementById('add-form');
  f.classList.toggle('open');
}

async function saveSource() {
  const fd = new FormData();
  fd.append('name',        document.getElementById('ns-name').value.trim());
  fd.append('source_type', document.getElementById('ns-type').value);
  fd.append('fmt',         document.getElementById('ns-fmt').value);
  fd.append('base_url',    document.getElementById('ns-url').value.trim());
  fd.append('id_field',    document.getElementById('ns-id').value.trim());
  fd.append('rate_limit',  document.getElementById('ns-rate').value);
  fd.append('api_key_env', document.getElementById('ns-key').value.trim());
  if (!fd.get('name')) { alert('Name is required.'); return; }
  const r = await fetch('/api/sources', { method:'POST', body:fd });
  const d = await r.json();
  if (d.error) { alert(d.error); return; }
  await loadSourcesPanel();
}

async function delSource(e, name) {
  e.stopPropagation();
  if (!confirm(`Remove "${name}"?`)) return;
  await fetch(`/api/sources/${name}`, { method:'DELETE' });
  loadSourcesPanel();
}

function useSource(name) {
  const idMap = { pmc:'PMC ID (e.g. PMC3988638)', pubmed:'PMID (e.g. 25062748)',
                  geo:'GSE ID', clinicaltrials:'NCT ID', biorxiv:'DOI' };
  document.getElementById('smart-input').placeholder = idMap[name] || 'Enter paper ID for ' + name;
  document.getElementById('smart-input').dataset.source = name;
  document.getElementById('smart-input').focus();
  closeSources();
}

function reuseDoc(docId, srcName) {
  document.getElementById('smart-input').value = docId;
  document.getElementById('smart-input').dataset.source = srcName;
  detectInput(docId);
  closeSources();
}

