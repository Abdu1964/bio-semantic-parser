// ── Run ───────────────────────────────────────────────────────────────────────

let _activeSocket = null;
let _activeRunId        = null;
let _pipelineComplete   = false;
let _wsReconnectCount   = 0;

function stopPipeline() {
  // 1. Close WebSocket immediately — UI stops receiving events right away
  if (_activeSocket) {
    _activeSocket.close();
    _activeSocket = null;
  }
  // 2. Signal server to stop (fire-and-forget — don't await)
  if (_activeRunId) {
    const fd = new FormData();
    fd.append('run_id', _activeRunId);
    fetch('/api/stop', { method: 'POST', body: fd }).catch(() => {});
  }
  _activeRunId = null;
  setStatus('done', 'Stopped by user');
  setBtn(false);
  _disableStop();
  const tb = document.getElementById('test-btn');
  if (tb) tb.disabled = false;
  exitFullscreen();
  streamLine('  ⬛ Pipeline stopped by user.', 'warn');
  // Update simple view
  const _se = _simpleEls();
  if (_se.title) {
    
    _se.title.textContent = 'Pipeline incomplete';
    _se.sub.textContent   = 'Processing was stopped before completing. Please check the detailed log for the last completed step, then re-run the pipeline.';
    if (_se.steam)  _se.steam.style.display  = 'none';
    if (_se.layMsg) _se.layMsg.style.display = 'none';
    if (_se.eta)    _se.eta.textContent = 'Switch to Detailed view for the log';
    if (_se.stepr)  _se.stepr.textContent = 'Incomplete';
  }
}

function _enableStop() {
  ['stop-btn','stop-btn-main'].forEach(id => {
    const b = document.getElementById(id);
    if (!b) return;
    b.style.opacity       = '1';
    b.style.cursor        = 'pointer';
    b.style.pointerEvents = 'auto';
  });
}
function _disableStop() {
  ['stop-btn','stop-btn-main'].forEach(id => {
    const b = document.getElementById(id);
    if (!b) return;
    b.style.opacity       = '0.35';
    b.style.cursor        = 'not-allowed';
    b.style.pointerEvents = 'none';
  });
}

async function startTest() {
  resetUI();
  _startTime = Date.now(); _totalChunks = 6;
  setBtn(true);
  _enableStop();
  const tb = document.getElementById('test-btn');
  if (tb) tb.disabled = true;
  setStatus('running', 'Running mock pipeline…');
  if (viewMode === 'detail') enterFullscreen();
  const resp = await fetch('/api/run/test', { method:'POST' });
  const { run_id } = await resp.json();
  const proto = location.protocol==='https:'?'wss':'ws';
  const socket = new WebSocket(`${proto}://${location.host}/ws/${run_id}`);
  _activeSocket = socket;
  socket.onmessage = e => handle(JSON.parse(e.data));
  socket.onerror   = () => setStatus('error','Connection error');
  socket.onclose   = () => {
    setBtn(false);
    if (tb) tb.disabled = false;
    _disableStop();
    _activeSocket = null;
  };
}

async function startRun() {
  const val  = document.getElementById('smart-input').value.trim();
  const src  = document.getElementById('smart-input').dataset.source || '';

  if (!isPdf && !val) { alert('Enter a paper ID, URL, or upload a PDF.'); return; }
  if (isPdf && !pdfFile) { alert('Select a PDF file.'); return; }

  // ── Pre-run checkpoint check ────────────────────────────────────────────────
  // Works for both PMC/PubMed IDs and PDFs.
  if (!_skipCheckpointCheck) {
    try {
      let ckUrl;
      if (isPdf && pdfFile) {
        ckUrl = `/api/checkpoint-status?filename=${encodeURIComponent(pdfFile.name)}`;
      } else if (val) {
        ckUrl = `/api/checkpoint-status?doc_id=${encodeURIComponent(val)}`;
      }
      if (ckUrl) {
        const ck = await fetch(ckUrl).then(r => r.json());
        if (ck.has_checkpoint && (ck.completed_layers || []).length > 0) {
          const displayId = isPdf ? (pdfFile?.name || val) : val;
          _showPreRunLayerSelector(ck.doc_id || val, ck.completed_layers || [], ck.next_layer, displayId, ck.substeps || {});
          return;
        }
      }
    } catch(e) { /* no checkpoint — proceed normally */ }
  }
  _skipCheckpointCheck = false;

  AppState.clear();                    // clear previous run state on new run
  const banner = document.getElementById('restore-banner');
  if (banner) banner.remove();
  resetUI();
  _startTime = Date.now(); _totalChunks = 0;
  setBtn(true);
  setStatus('running','Running…');
  // Immediately show "Starting…" in simple view before first layer event arrives
  const _paperLabel = isPdf ? (pdfFile?.name || 'PDF') : val;
  initSimpleProcessing(_paperLabel);
  if (viewMode === 'detail') enterFullscreen();

  const fd = new FormData();
  fd.append('output_format', fmt);
  fd.append('source_name',   src);

  if (isPdf && pdfFile) {
    fd.append('input_type',  'pdf');
    fd.append('input_value', '');
    fd.append('pdf_file',    pdfFile);
  } else {
    // If user explicitly selected a source via chip — trust that
    let type = selectedSource || src || '';
    if (!type) {
      // Auto-detect only when no source was explicitly selected
      const v = val.toUpperCase();
      type = /^PMC\d+$/.test(v)              ? 'pmc'    :
             /^\d{5,9}$/.test(val)            ? 'pubmed' :
             /^10\./.test(val)                ? 'url'    :
             /^https?:\/\//.test(val)         ? 'url'    :
             /^GSE\d+/i.test(val)             ? 'geo'    :
             /^NCT\d+/i.test(val)             ? 'clinicaltrials' :
             /^FBgn\d{7}$/.test(val)          ? 'auto'   :
             /^WBGene\d{8}$/.test(val)        ? 'auto'   :
             /^MGI:\d+$/.test(val)            ? 'auto'   :
             /^ZDB-GENE-\d{6}-\d+$/.test(val) ? 'auto'   :
             /^S\d{9}$/.test(val)             ? 'auto'   :
             /^RGD:\d+$/.test(val)            ? 'auto'   :
             'pmc';
    }
    fd.append('input_type',  type);
    fd.append('input_value', val);
  }

  const resp  = await fetch('/api/run', { method:'POST', body:fd });
  const { run_id } = await resp.json();
  _activeRunId      = run_id;
  _pipelineComplete = false;
  _wsReconnectCount = 0;
  _connectWs(run_id);
}

function _connectWs(run_id) {
  const proto  = location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${proto}://${location.host}/ws/${run_id}`);
  _activeSocket = socket;
  _enableStop();

  let _lastMsgTime = Date.now();

  let _pingInterval = setInterval(() => {
    if (socket.readyState === WebSocket.OPEN) socket.send('ping');
  }, 30000);

  // Watchdog: the server sends a keepalive event at least every ~15s while a
  // run is active, but a dead connection (idle NAT/proxy drop, laptop sleep)
  // can leave `onclose` never firing on either side — the socket just stops
  // receiving anything. If nothing arrives for way longer than the server's
  // own keepalive interval, treat the connection as dead and force a close
  // ourselves so the reconnect logic below actually gets a chance to run.
  const STALE_MS = 90000;
  let _staleCheck = setInterval(() => {
    if (_pipelineComplete || _activeRunId !== run_id) return;
    if (Date.now() - _lastMsgTime > STALE_MS) {
      console.warn(`[ws] no message in ${STALE_MS}ms — assuming dead connection, forcing reconnect`);
      socket.close();
    }
  }, 10000);

  socket.onmessage = e => { _lastMsgTime = Date.now(); if (_wsReconnectCount > 0) _wsReconnectCount = 0; handle(JSON.parse(e.data)); };
  socket.onerror   = () => {};   // onclose fires too — let that handle UI
  socket.onclose   = () => {
    clearInterval(_pingInterval);
    clearInterval(_staleCheck);
    _activeSocket = null;
    if (_pipelineComplete || _activeRunId !== run_id) {
      // Normal close after completion or a new run started — clean up
      setBtn(false);
      _disableStop();
      return;
    }
    // Unexpected disconnect while pipeline is still running — reconnect
    if (_wsReconnectCount < 8) {
      _wsReconnectCount++;
      const delay = Math.min(1000 * _wsReconnectCount, 8000);
      setTimeout(() => {
        if (_activeRunId === run_id && !_pipelineComplete) {
          console.log(`[ws] reconnecting (attempt ${_wsReconnectCount})…`);
          _connectWs(run_id);
        }
      }, delay);
    } else {
      setBtn(false);
      _disableStop();
      setStatus('error', 'Connection lost — reload to check results');
    }
  };
}

