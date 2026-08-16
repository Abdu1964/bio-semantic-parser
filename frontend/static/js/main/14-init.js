// ── Init ──────────────────────────────────────────────────────────────────────
// ── Expose functions globally for inline onclick attrs (dynamically injected HTML) ──
window._commitExistingOutputs = _commitExistingOutputs;
window._commitExistingPLN     = _commitExistingPLN;
window._runPLNInference       = _runPLNInference;
window.commitPLNInference     = commitPLNInference;
window.commitPLNQuery         = commitPLNQuery;
window._reprocessPaper        = _reprocessPaper;
window._switchRun             = _switchRun;
window._startFromLayer        = _startFromLayer;
window._startFromSubstep      = _startFromSubstep;
window._dismissPreRun         = _dismissPreRun;
window.openViewer             = openViewer;
window.discardRun             = discardRun;
window.commitPLN              = commitPLN;
window.clearAndReset          = AppState.clearAndReset;
window.exportDownload         = exportDownload;
window.toggleQbExportMenu     = toggleQbExportMenu;
window.toggleUnifiedExportMenu = toggleUnifiedExportMenu;
window.exportUnifiedKG        = exportUnifiedKG;

// ── Feature flag: PLN optional sidecar ──────────────────────────────────────
// Loaded once at page init from /api/config. Hides all PLN UI when false.
let _PLN_ENABLED = false;

function _applyPlnVisibility() {
  // Step 2 PLN commit section
  const plnStep = document.getElementById('pln-commit-step');
  if (plnStep) plnStep.style.display = _PLN_ENABLED ? 'block' : 'none';
  // Any other PLN-specific elements with data-pln-feature attribute
  document.querySelectorAll('[data-pln-feature]').forEach(el => {
    if (!_PLN_ENABLED) {
      el.style.display = 'none';
    } else {
      // Restore to each element's natural display value
      el.style.display = el.id === 'pln-cards' ? 'contents' : '';
    }
  });
  // Step 1 label — hide "Step 1" badge and "(without PLN reasoning)" when PLN is off
  const step1Badge  = document.getElementById('commit-step1-badge');
  const step1Suffix = document.getElementById('commit-step1-pln-suffix');
  if (step1Badge)  step1Badge.style.display  = _PLN_ENABLED ? '' : 'none';
  if (step1Suffix) step1Suffix.style.display = _PLN_ENABLED ? '' : 'none';
}

window.addEventListener('load', async () => {
  // Load feature flags first
  try {
    const cfg = await fetch('/api/config').then(r => r.json());
    _PLN_ENABLED = cfg.enable_pln === true;
  } catch(e) { _PLN_ENABLED = false; }
  _applyPlnVisibility();

  loadSources();
  setView('simple');   // default to simple view — detailed on demand
  const inp = document.getElementById('smart-input');
  if (inp && inp.value) detectInput(inp.value);

  // ── Restore previous session from localStorage ───────────────────────────
  if (AppState.has()) {
    const saved = AppState.load();
    AppState.showBanner();
    AppState.restore(saved);
    _applyPlnVisibility();  // re-apply after restore so PLN stays hidden when disabled
    // Refresh graph stats in case human-review approvals happened since last save
    if (saved && saved.run_dir) {
      fetch(`/api/run-graph-stats?run_dir=${encodeURIComponent(saved.run_dir)}`)
        .then(r => r.json())
        .then(sd => {
          if (sd.edges !== undefined && _outputData) {
            const ts = Date.now();
            const bust = p => p ? p.split('?')[0] + '?t=' + ts : p;
            renderOutput({
              ..._outputData,
              neo4j_edges: sd.edges,
              neo4j_nodes: sd.nodes,
              graph_html:  bust(_outputData.graph_html),
              ver_html:    bust(_outputData.ver_html),
            });
          }
        }).catch(() => {});
    }
  }
});

window.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
  // Close any open export menus on Escape
  document.querySelectorAll('.qb-export-menu').forEach(m => m.classList.remove('open'));
});

