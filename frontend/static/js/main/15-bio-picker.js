// ── Bio entity publication picker ─────────────────────────────────────────────

function _showPublicationPicker(entityId, entityDb, publications) {
  // Remove any existing picker
  const old = document.getElementById('bio-picker');
  if (old) old.remove();

  const panel = document.createElement('div');
  panel.id = 'bio-picker';
  panel.style.cssText = `
    position:fixed;top:0;left:0;right:0;bottom:0;z-index:9000;
    background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;
  `;

  const openAccess = publications.filter(p => p.open_access);
  const rows = publications.map((p, i) => {
    const oa   = p.open_access
      ? `<span style="font-size:10px;color:var(--green);font-weight:700;margin-left:6px">OPEN</span>`
      : `<span style="font-size:10px;color:var(--text3);margin-left:6px">PMID only</span>`;
    const id   = p.pmcid || p.pmid || p.doi || p.fbrf || '';
    const year = p.year  ? `<span style="color:var(--text3)">${p.year}</span> ` : '';
    const title = p.title
      ? `<div style="font-size:12px;color:var(--text2);margin-top:2px;line-height:1.4">${esc(p.title.slice(0, 110))}${p.title.length > 110 ? '…' : ''}</div>`
      : '';
    return `
      <div class="fmt-chip" data-idx="${i}" onclick="_resumeWithPub('${esc(entityId)}', ${i})"
           style="display:block;text-align:left;padding:10px 14px;margin-bottom:6px;cursor:pointer;
                  border-color:var(--border);background:var(--bg2)">
        <div style="display:flex;align-items:center;gap:6px">
          <span style="font-size:12px;font-weight:700;color:var(--blue)">${esc(id)}</span>
          ${year}${oa}
        </div>
        ${title}
      </div>`;
  }).join('');

  panel.innerHTML = `
    <div style="background:var(--bg);border:1px solid var(--border);border-radius:14px;
         padding:24px 28px;width:560px;max-width:95vw;max-height:80vh;
         display:flex;flex-direction:column;gap:16px;box-shadow:0 8px 40px rgba(0,0,0,0.4)">
      <div style="display:flex;align-items:flex-start;justify-content:space-between">
        <div>
          <div style="font-size:15px;font-weight:700;color:var(--text)">
            Select a publication
          </div>
          <div style="font-size:12px;color:var(--text3);margin-top:4px">
            ${esc(entityDb)} · <span style="color:var(--blue);font-weight:600">${esc(entityId)}</span>
            · ${publications.length} publication${publications.length !== 1 ? 's' : ''}
            ${openAccess.length ? `· <span style="color:var(--green)">${openAccess.length} open access</span>` : ''}
          </div>
        </div>
        <button onclick="document.getElementById('bio-picker').remove(); resetUI();"
          style="background:none;border:none;color:var(--text3);font-size:20px;cursor:pointer;
                 line-height:1;padding:0 0 0 12px">✕</button>
      </div>
      <div style="overflow-y:auto;flex:1">
        ${rows || '<div style="color:var(--text3);font-size:13px">No publications found.</div>'}
      </div>
    </div>
  `;

  document.body.appendChild(panel);

  // Store publications for the resume callback
  window._bioPickerPubs = publications;
}

function _resumeWithPub(entityId, pubIndex) {
  const pub = (window._bioPickerPubs || [])[pubIndex];
  if (!pub) return;

  // Close picker
  const picker = document.getElementById('bio-picker');
  if (picker) picker.remove();

  // Pre-fill input for display
  const inp = document.getElementById('smart-input');
  if (inp) inp.value = pub.input_value || pub.pmcid || pub.pmid;

  // Reset UI and start a fresh pipeline run with the selected paper
  AppState.clear();
  resetUI();
  _startTime = Date.now(); _totalChunks = 0;
  setBtn(true);
  setStatus('running', 'Running…');
  initSimpleProcessing(pub.input_value || entityId);
  if (viewMode === 'detail') enterFullscreen();

  const fd = new FormData();
  fd.append('output_format', fmt);
  fd.append('input_type',    pub.input_type  || 'pmid');
  fd.append('input_value',   pub.input_value || pub.pmid);
  fd.append('source_entity', entityId);

  fetch('/api/run', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(({ run_id }) => {
      _activeRunId      = run_id;
      _pipelineComplete = false;
      _wsReconnectCount = 0;
      _connectWs(run_id);
    });
}
