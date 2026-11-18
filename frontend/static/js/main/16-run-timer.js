// ── Run timer ─────────────────────────────────────────────────────────────────
// Live pipeline time since Run was pressed; freezes when the run completes, fails or is stopped.
// Run code calls runTimerStart() / runTimerStop(outcome); outcome is 'done', 'failed' or 'stopped'.

let _timerStart = 0, _timerEnd = 0, _timerOutcome = '';

const _TIMER_LABELS = { '': '⏱ Running', done: '✓ Pipeline time', failed: '✗ Failed after', stopped: '⬛ Stopped after' };

function runTimerStart() {
  _timerStart = Date.now(); _timerEnd = 0; _timerOutcome = '';
  _runTimerTick();
}

function runTimerStop(outcome) {
  if (!_timerStart || _timerOutcome) return;           // no run, or already frozen
  _timerEnd = Date.now(); _timerOutcome = outcome;
  _runTimerTick();
}

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
    _timerHost().appendChild(el);
  }
  return el;
}

// Native fullscreen shows only the fullscreen element's subtree, so the timer moves into it
function _timerHost() {
  return document.fullscreenElement || document.webkitFullscreenElement || document.body;
}

function _timerFollowFullscreen() {
  const el = document.getElementById('run-timer');
  if (el && el.parentNode !== _timerHost()) _timerHost().appendChild(el);
}

function _runTimerTick() {
  const el = _timerEl();
  if (!_timerStart) { el.style.display = 'none'; return; }
  const sec = Math.max(0, Math.round(((_timerOutcome ? _timerEnd : Date.now()) - _timerStart) / 1000));
  el.textContent = `${_TIMER_LABELS[_timerOutcome]} ${_fmtElapsed(sec)}`;
  el.style.display = 'block';
}

document.addEventListener('fullscreenchange',       _timerFollowFullscreen);
document.addEventListener('webkitfullscreenchange', _timerFollowFullscreen);
setInterval(_runTimerTick, 1000);
