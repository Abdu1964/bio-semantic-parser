// ── Input auto-detection ──────────────────────────────────────────────────────

function detectInput(val) {
  // User typed manually — clear chip selection
  selectedSource = '';
  document.querySelectorAll('#header-sources .sources-btn').forEach(b => b.classList.remove('active'));
  const badge = document.getElementById('detect-badge');
  const v     = val.trim();
  let type = '', label = 'Auto-detect';

  if (!v)                                            { type='';       label='Auto-detect'; }
  else if (/^PMC\d+$/i.test(v) || /^\d{5,8}$/.test(v) && v.length < 9) {
    if (/^PMC/i.test(v))                             { type='pmc';    label='PMC ID'; }
    else                                             { type='pubmed'; label='PubMed ID'; }
  }
  else if (/^10\.\d{4,}\//.test(v))                 { type='doi';    label='DOI'; }
  else if (/^https?:\/\//.test(v))                   { type='url';    label='URL'; }
  else if (v.length > 0)                             { type='pmc';    label='PMC ID'; }

  badge.textContent = label;
  badge.className   = 'detect-badge' + (type ? ' ' + type : '');
}

function showPdf() {
  document.getElementById('pdf-row').style.display = 'block';
  document.getElementById('smart-input').placeholder = 'PDF selected — or type an ID above to switch back';
  isPdf = true;
  const badge = document.getElementById('detect-badge');
  badge.textContent = 'PDF'; badge.className = 'detect-badge pdf';
  const link = document.getElementById('pdf-toggle-link');
  if (link) { link.textContent = '✕ Cancel PDF upload'; link.style.color = 'var(--red)'; }
}

function togglePdfMode() {
  if (isPdf || document.getElementById('pdf-row').style.display !== 'none') {
    clearPdf();
    const link = document.getElementById('pdf-toggle-link');
    if (link) { link.textContent = 'Upload PDF'; link.style.color = ''; }
  } else {
    showPdf();
  }
}

function onFileSelect(input) {
  if (!input.files.length) return;
  pdfFile = input.files[0];
  document.getElementById('pdf-name').textContent = pdfFile.name;
  document.getElementById('pdf-clear').style.display = 'inline';
  isPdf = true;
}

function clearPdf() {
  pdfFile = null;
  isPdf   = false;
  selectedSource = '';
  document.getElementById('pdf-input').value = '';
  document.getElementById('pdf-name').textContent = 'Click to choose PDF or drag & drop';
  document.getElementById('pdf-clear').style.display = 'none';
  document.getElementById('pdf-row').style.display = 'none';   // hide the PDF row
  const badge = document.getElementById('detect-badge');
  badge.textContent = 'Auto-detect'; badge.className = 'detect-badge';
  const inp = document.getElementById('smart-input');
  inp.placeholder = 'PMC6746067 · 25062748 · 10.1016/j.cell.2023... · https://...';
  inp.focus();
  const link = document.getElementById('pdf-toggle-link');
  if (link) { link.textContent = 'Upload PDF'; link.style.color = ''; }
}

function onDrop(e) {
  e.preventDefault();
  const f = e.dataTransfer.files[0];
  if (f && f.name.endsWith('.pdf')) {
    pdfFile = f;
    document.getElementById('pdf-name').textContent = f.name;
    document.getElementById('pdf-clear').style.display = 'inline';
    document.getElementById('drop-zone').style.color = '';
    isPdf = true;
  }
}

function setFmt(f, btn) {
  document.querySelectorAll('.fmt-chip').forEach(b => b.classList.remove('active'));
  btn.classList.add('active'); fmt = f;
  AppState.saveFormat(f);
}

