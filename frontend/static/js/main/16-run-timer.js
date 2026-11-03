// ── Run timer ─────────────────────────────────────────────────────────────────
// Live pipeline time since Run was pressed; freezes when the run completes, fails or is stopped.

let _timerStart = 0, _timerEnd = 0, _timerFrozen = false, _timerSawRun = false;

function _fmtElapsed(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  const pad = n => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function _timerEl() {
  let el = document.getElementById('run-timer');
  if (!el) {
    el = document.createElement('div');
    el.id = 'run-timer';
    // Bottom-right, above the full-screen log (z-index 500), clear of the header buttons
    el.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:600;padding:4px 12px;border-radius:999px;'
      + 'font:600 13px var(--font, system-ui);background:var(--bg2, #1e1e2e);color:var(--text, #eee);'
      + 'border:1px solid var(--border, #444);box-shadow:0 2px 8px rgba(0,0,0,.25);display:none';
    document.body.appendChild(el);
  }
  return el;
}

function _runTimerTick() {
  const el = _timerEl();
  if (!_startTime) { el.style.display = 'none'; return; }
  if (_startTime !== _timerStart) {                    // a new run was started
    _timerStart = _startTime; _timerFrozen = false; _timerSawRun = false;
  }
  if (_activeRunId) _timerSawRun = true;
  const running = !_pipelineComplete && (_activeRunId || !_timerSawRun);
  if (!running && !_timerFrozen && _timerSawRun) { _timerFrozen = true; _timerEnd = Date.now(); }
  const sec = Math.max(0, Math.round(((_timerFrozen ? _timerEnd : Date.now()) - _timerStart) / 1000));
  const label = !_timerFrozen ? '⏱ Running' : (_pipelineComplete ? '✓ Pipeline time' : '⬛ Stopped after');
  el.textContent = `${label} ${_fmtElapsed(sec)}`;
  el.style.display = 'block';
}

setInterval(_runTimerTick, 1000);
