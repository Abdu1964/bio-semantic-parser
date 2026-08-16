// ── Event handler ─────────────────────────────────────────────────────────────

function handle(ev) {
  try { _handle(ev); } catch(e) { console.error('[handle] uncaught:', e, ev); }
}

function _handle(ev) {
  const { layer, status, message, data } = ev;

  if (layer === 0) {
    if (status === 'ping') return;  // keepalive — ignore
    const _se = _simpleEls();
    if (status === 'complete' || status === 'fatal') {
      _pipelineComplete = true;  // stop reconnect attempts
    }
    if (status === 'complete') {
      setStatus('done','Complete ✓'); exitFullscreen();
      // Simple view — complete state (layer 8 done handler also fires, this is a fallback)
      if (_se.title && _se.title.textContent !== "Knowledge graph ready") {
        const elapsed = _startTime ? Math.round((Date.now()-_startTime)/1000) : 0;
        const elStr   = elapsed > 60 ? `${Math.floor(elapsed/60)}m ${elapsed%60}s` : `${elapsed}s`;
        
        _se.title.textContent = 'Knowledge graph ready';
        _se.sub.textContent   = `Pipeline completed in ${elStr}. Review your results below.`;
        if (_se.steam)  _se.steam.style.display  = 'none';
        if (_se.layMsg) _se.layMsg.style.display = 'none';
        if (_se.bar)    _se.bar.style.width = '100%';
        if (_se.eta)    _se.eta.textContent = '';
        if (_se.stepr)  _se.stepr.textContent = '8 / 8 done';
      }
    } else {
      setStatus('error', message); exitFullscreen();
      if (_se.title) {
        
        _se.title.textContent = 'Pipeline finished with an error';
        const _errDetail = message ? message.slice(0, 120) + (message.length > 120 ? '…' : '') : '';
        _se.sub.textContent = _errDetail
          ? `Error: ${_errDetail} — Switch to Detailed view to see the full log, fix the issue, then re-run.`
          : 'An error occurred during processing. Switch to Detailed view to see what went wrong, then re-run the pipeline.';
        if (_se.steam)  _se.steam.style.display  = 'none';
        if (_se.layMsg) _se.layMsg.style.display = 'none';
        if (_se.eta)    _se.eta.textContent = '';
        if (_se.stepr)  _se.stepr.textContent = 'Failed';
      }
    }
    setBtn(false);
    _disableStop();
    _activeSocket = null;
    return;
  }

  // Bio entity picker — pipeline paused waiting for publication selection
  if (status === 'picker_required') {
    setBtn(false);
    _disableStop();
    _activeSocket = null;
    _showPublicationPicker(data.entity_id, data.entity_db, data.publications || []);
    return;
  }

  const card  = document.getElementById('lc-'    + layer);
  const msg   = document.getElementById('lm-'    + layer);
  const stat  = document.getElementById('ls-'    + layer);
  const bar   = document.getElementById('lb-'    + layer);
  const body  = document.getElementById('lbody-' + layer);
  if (!card && !msg) return;

  card?.classList.remove('pending','running','done','error');

  if (status === 'running') {
    updateSimpleStep(layer, 'running', message);
    streamHeader(layer, message);
    if (card) {
      card.classList.add('running','open');
      if (stat) stat.textContent = 'Running';
      if (msg)  msg.innerHTML = `<span class="spinner"></span>${message}`;
      if (bar)  bar.style.width = '55%';
    }

  } else if (status === 'progress') {
    const p = data.total ? Math.round(data.done*100/data.total) : 0;
    if (bar) bar.style.width = p + '%';
    if (stat) stat.textContent = `${data.done}/${data.total}`;
    if (msg)  msg.innerHTML = `<span class="spinner"></span>${message}`;
    streamLine(`  ${message}`);
    // Capture chunk count from Layer 6 progress for time estimate
    if (layer === 6 && data.total) _totalChunks = data.total;

  } else if (status === 'log') {
    if (data.kind === 'html') {
      streamHtml(message);
    } else {
      streamLine(message, data.kind || '');
    }

  } else if (status === 'done') {
    updateSimpleStep(layer, 'done', message);
    streamLine(`  ✓ ${message}`);
    if (card) {
      card.classList.add('done');
      if (stat) stat.textContent = '✓ Done';
      if (msg)  msg.textContent  = message;
      const summary = render(layer, data);
      if (body) body.innerHTML = summary;
    }
    // Capture chunk count from Layer 3
    if (layer === 3 && data.chunks) _totalChunks = data.chunks;
    if (layer === 8) renderOutput(data);

  } else if (status === 'error') {
    updateSimpleStep(layer, 'error', message);
    streamLine(`  ✗ ERROR: ${message}`);
    if (card) {
      card.classList.add('error','open');
      if (stat) stat.textContent = 'Error';
      if (msg)  msg.textContent  = message;
      if (body) body.innerHTML   = `<div class="err-box">${esc(message)}</div>`;
    }
  }
}

