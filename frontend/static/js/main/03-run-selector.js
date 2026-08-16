// ── Run selector — shown when multiple runs of the same paper exist ──────────
function _showRunSelector(runs, baseData) {
  // Remove any existing selector
  const old = document.getElementById('run-selector');
  if (old) old.remove();

  const wrap = document.createElement('div');
  wrap.id = 'run-selector';
  wrap.style.cssText = `
    background:var(--bg2);border:1px solid var(--border);border-radius:12px;
    padding:16px 20px;margin-bottom:16px;
  `;

  const cards = runs.map((r, i) => {
    const name  = r.run_name || r.run_dir.split('/').pop();
    const date  = name.match(/\d{4}-\d{2}-\d{2}_\d{2}-\d{2}/)?.[0]?.replace('_',' ') || name;
    const isFirst = i === 0;
    return `<div style="
        background:${isFirst?'var(--bg3)':'var(--bg)'};
        border:1px solid ${isFirst?'var(--blue)':'var(--border)'};
        border-radius:8px;padding:10px 14px;cursor:pointer;transition:border-color .15s;
        ${isFirst?'':'opacity:.8'}
      "
      onclick="_switchRun(${JSON.stringify(r).replace(/"/g,'&quot;')}, this)"
      title="Open this run">
      <div style="font-size:12px;font-weight:600;color:var(--text)">${date}</div>
      <div style="font-size:10px;color:var(--text3);margin-top:2px">${r.run_dir.split('/').pop()}</div>
      ${isFirst?'<div style="font-size:10px;color:var(--blue);margin-top:2px">Latest ✓</div>':''}
    </div>`;
  }).join('');

  wrap.innerHTML = `
    <div style="font-size:11px;font-weight:700;color:var(--text3);text-transform:uppercase;
                letter-spacing:.06em;margin-bottom:10px">
      ${runs.length} runs found for this paper — select one:
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px">
      ${cards}
    </div>`;

  const main = document.querySelector('.main');
  const output = document.getElementById('output');
  if (main && output) main.insertBefore(wrap, output);
}

function _switchRun(run, el) {
  // Highlight selected
  document.querySelectorAll('#run-selector [onclick]').forEach(e => {
    e.style.borderColor = 'var(--border)';
    e.style.opacity = '.8';
  });
  el.style.borderColor = 'var(--blue)';
  el.style.opacity = '1';

  // Update output cards to point to the selected run
  const d = window._outputData;
  if (!d) return;
  const updated = { ...d,
    graph_html: run.graph_html || d.graph_html,
    pln_html:   run.pln_html   || d.pln_html,
    ver_html:   run.ver_html   || d.ver_html,
    run_dir:    run.run_dir    || d.run_dir,
  };
  window._outputData = updated;
  renderInspectActions(updated);
}

function discardRun() {
  document.getElementById('commit-section').style.display = 'none';
  const inspectSec = document.getElementById('inspect-section');
  if (inspectSec) inspectSec.style.display = 'none';
  const breakdown = document.getElementById('staging-breakdown');
  if (breakdown) { breakdown.style.display = 'none'; breakdown.innerHTML = ''; }
  _stagingDb = ''; _docId = '';
  AppState.clear();    // clear persisted state on discard
  const banner = document.getElementById('restore-banner');
  if (banner) banner.remove();
}

