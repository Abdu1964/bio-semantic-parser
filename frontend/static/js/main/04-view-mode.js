// ── View mode ─────────────────────────────────────────────────────────────────

let viewMode = 'detail';
let _totalChunks = 0;
let _startTime   = 0;
let _layerStart  = 0;
// Track actual elapsed time per completed layer for real ETA
const _layerDurations = {};   // { layerN: seconds }
let _layerStartTimes  = {};   // { layerN: Date.now() }

const LAYER_NAMES = ['','Source Registry','Deduplication','Universal Fetcher',
  'NER + Negation','Taxonomy Schema','LLM Extraction',
  'Post-Extraction Validation','Publish to KG'];

const LAYER_ICONS = ['','›1','›2','›3','›4','›5','›6','›7','›8'];

function setView(mode) {
  viewMode = mode;
  document.getElementById('view-simple').classList.toggle('active', mode==='simple');
  document.getElementById('view-detail').classList.toggle('active', mode==='detail');
  document.getElementById('simple-view').style.display  = mode==='simple' ? 'block' : 'none';
  document.getElementById('stream-view').style.display  = mode==='detail' ? 'block' : 'none';
  if (mode === 'simple') exitFullscreen();
}

function initSimpleSteps() { /* no-op — simple view now uses coffee card */ }

const COFFEE_MSGS = [
  'Analysing data sources…',
  'Checking knowledge graph…',
  'Fetching the paper…',
  'Identifying biological entities…',
  'Loading relationship taxonomy…',
  'Extracting relations with AI…',
  'Validating extracted triples…',
  'Writing to knowledge graph…',
];

// Average layer durations from real runs (seconds) — used for initial ETA before any data comes in
const _LAYER_EST = [0, 8, 25, 12, 10, 30, 15, 20, 15]; // L1–L8 rough estimates
const _TOTAL_EST = _LAYER_EST.slice(1).reduce((a,b)=>a+b,0); // ~135s total

function _simpleEls() {
  return {
    icon:   document.getElementById('coffee-title'),   // title doubles as main status
    title:  document.getElementById('coffee-title'),
    sub:    document.getElementById('coffee-sub'),
    bar:    document.getElementById('coffee-bar'),
    eta:    document.getElementById('coffee-eta'),
    stepr:  document.getElementById('coffee-step-r'),
    layMsg: document.getElementById('coffee-layer-msg'),
    stepEl: document.getElementById('coffee-step'),
    wrap:   document.getElementById('coffee-progress-wrap'),
    steam:  document.getElementById('bio-loader'),
  };
}

function initSimpleProcessing(paperLabel) {
  // Called immediately when Run is clicked — before any layer event arrives
  const e = _simpleEls();
  if (!e.title) return;
  if (e.wrap)   e.wrap.style.display   = 'block';
  if (e.steam)  e.steam.style.display  = 'flex';
  if (e.layMsg) e.layMsg.style.display = 'flex';
  e.title.textContent = 'Starting pipeline…';
  e.sub.textContent   = paperLabel
    ? `Processing "${paperLabel}" — this typically takes 2–4 minutes.`
    : 'Connecting to pipeline — this typically takes 2–4 minutes.';
  if (e.stepEl) e.stepEl.textContent = 'Initialising…';
  if (e.bar)    e.bar.style.width    = '2%';
  if (e.stepr)  e.stepr.textContent  = 'Starting…';
  if (e.eta)    e.eta.textContent    = `~${Math.ceil(_TOTAL_EST/60)}–${Math.ceil(_TOTAL_EST/60)+2} min estimated`;
}

function updateSimpleStep(n, status, message) {
  const e = _simpleEls();
  if (!e.title) return;

  if (status === 'running') {
    _layerStartTimes[n] = Date.now();
    if (e.wrap)   e.wrap.style.display   = 'block';
    if (e.steam)  e.steam.style.display  = 'flex';
    if (e.layMsg) e.layMsg.style.display = 'flex';

    e.title.textContent = _layerTitle(n);
    e.sub.textContent   = _layerHint(n);
    if (e.stepEl) e.stepEl.textContent = `Layer ${n} / 8 — ${LAYER_NAMES[n]}`;

    // Progress bar: layer start fraction
    const pct = Math.round((n - 1) / 8 * 100);
    if (e.bar) e.bar.style.width = Math.max(pct, 3) + '%';
    if (e.stepr) e.stepr.textContent = `Layer ${n} of 8`;

    // ETA: use real durations if available, else fall back to static estimates
    if (e.eta) e.eta.textContent = _calcEta(n);

  } else if (status === 'done') {
    if (_layerStartTimes[n]) _layerDurations[n] = (Date.now() - _layerStartTimes[n]) / 1000;

    const pct = Math.round(n / 8 * 100);
    if (e.bar) e.bar.style.width = pct + '%';
    if (e.stepr) e.stepr.textContent = `Layer ${n} / 8 done`;

    if (n === 8) {
      if (e.steam)  e.steam.style.display  = 'none';
      if (e.layMsg) e.layMsg.style.display = 'none';
      e.title.textContent = 'Knowledge graph ready';
      const elapsed = _startTime ? Math.round((Date.now()-_startTime)/1000) : 0;
      const elStr   = elapsed > 60 ? `${Math.floor(elapsed/60)}m ${elapsed%60}s` : `${elapsed}s`;
      e.sub.textContent   = `All 8 layers complete in ${elStr}. Review your results below.`;
      if (e.eta) e.eta.textContent = '';
    }
  }
}

function _layerTitle(n) {
  return [
    '', 'Loading paper…', 'Chunking text…', 'Resolving references…',
    'Normalising entities…', 'Extracting relations…',
    'Validating semantics…', 'Deduplicating triples…', 'Publishing to knowledge graph…',
  ][n] || 'Processing…';
}

function _layerHint(n) {
  const hints = [
    '',
    'Fetching the paper from the source database.',
    'Splitting the paper into processable chunks — larger papers take longer.',
    'Resolving co-references and pronouns across the text.',
    'Linking biological entities to standard ontologies (MESH, GO, MONDO…).',
    'Asking the LLM to extract biological relations — longest step.',
    'Checking extracted triples against the schema and taxonomy.',
    'Merging duplicate triples across chunks and prior papers.',
    'Writing Neo4j CSVs and MeTTa files to the knowledge graph.',
  ];
  return hints[n] || 'This may take a moment…';
}

function _calcEta(currentLayer) {
  const done = Object.keys(_layerDurations).map(Number);
  let secsLeft = 0;
  for (let l = currentLayer; l <= 8; l++) {
    if (_layerDurations[l]) continue; // already done
    // Use actual avg if we have data, else static estimate
    const avg = done.length > 0
      ? done.reduce((s,k)=>s+_layerDurations[k],0) / done.length
      : null;
    secsLeft += avg !== null ? avg : (_LAYER_EST[l] || 15);
  }
  if (secsLeft < 5) return '';
  return secsLeft > 90
    ? `~${Math.ceil(secsLeft/60)} min remaining`
    : `~${Math.round(secsLeft)}s remaining`;
}

