// ── PLN Query panel ───────────────────────────────────────────────────────────

let _plnStagingDb = '', _plnRunDir = '';

function openPLNPanel(stagingDb, runDir, label) {
  _plnStagingDb = stagingDb;
  _plnRunDir    = runDir;

  const old = document.getElementById('pln-panel');
  if (old) old.remove();

  const panel = document.createElement('div');
  panel.id = 'pln-panel';
  panel.style.cssText = `
    position:fixed;top:0;right:0;width:580px;max-width:95vw;height:100vh;
    background:var(--bg);border-left:1px solid var(--border);z-index:9999;
    display:flex;flex-direction:column;box-shadow:-4px 0 20px rgba(0,0,0,.18);
  `;
  panel.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;
      padding:16px 20px;border-bottom:1px solid var(--border);flex-shrink:0">
      <div style="font-size:15px;font-weight:700;color:var(--text)">🔮 ${esc(label)}</div>
      <button onclick="document.getElementById('pln-panel').remove()"
        style="background:none;border:none;cursor:pointer;font-size:18px;color:var(--text3)">✕</button>
    </div>
    <div style="padding:16px 20px;border-bottom:1px solid var(--border);flex-shrink:0">
      <div style="display:flex;gap:8px">
        <input id="pln-q-input" type="text" placeholder="e.g. What does HGNC_5358 inhibit?"
          style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:8px;
            background:var(--bg2);color:var(--text);font-size:13px;outline:none"
          onkeydown="if(event.key==='Enter')_runPLNQuery()" />
        <button onclick="_runPLNQuery()"
          style="padding:8px 16px;background:var(--pln);color:#fff;border:none;border-radius:8px;
            cursor:pointer;font-size:13px;font-weight:600">Ask</button>
      </div>
      <div style="font-size:11px;color:var(--text3);margin-top:6px">
        Try: "What activates HGNC_9170?" · "What treats Alzheimer's?" · "What causes MESH_D000544?"
      </div>
    </div>
    <div id="pln-results" style="flex:1;overflow-y:auto;padding:16px 20px">
      <div style="color:var(--text3);font-size:13px">
        Ask a question to run PLN forward + backward chaining over this paper's triples.
      </div>
    </div>
  `;
  document.body.appendChild(panel);
  setTimeout(() => document.getElementById('pln-q-input').focus(), 60);
}

async function _runPLNQuery() {
  const input = document.getElementById('pln-q-input');
  const q = (input && input.value || '').trim();
  if (!q) return;
  const res = document.getElementById('pln-results');
  if (!res) return;
  res.innerHTML = '<div style="color:var(--text3);font-size:13px">🔮 Running PLN reasoning…</div>';
  try {
    const r = await fetch('/api/pln-query', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ question: q, staging_db: _plnStagingDb, run_dir: _plnRunDir }),
    });
    const d = await r.json();
    if (!d.ok) {
      res.innerHTML = `<div style="color:var(--red);font-size:12px">PLN error: ${esc(d.error||'Unknown')}</div>`;
      return;
    }
    if (!d.answers || !d.answers.length) {
      res.innerHTML = `
        <div style="color:var(--text3);font-size:13px">No PLN proof found for this question.</div>
        <div style="color:var(--text3);font-size:11px;margin-top:6px">Query: <code>${esc(d.pln_query||'')}</code></div>`;
      return;
    }
    const rows = d.answers.slice(0,20).map(a => `
      <tr>
        <td style="padding:7px 10px;font-size:12px;color:var(--text)">${esc(a.predicate||'')}</td>
        <td style="padding:7px 10px;font-size:12px;color:var(--text2)">${esc(a.subject||'')}</td>
        <td style="padding:7px 10px;font-size:12px;color:var(--text2)">${esc(a.object||'')}</td>
        <td style="padding:7px 10px;font-size:11px;color:var(--text3);font-variant-numeric:tabular-nums">
          ${(a.strength||0).toFixed(2)} / ${(a.confidence||0).toFixed(2)}
        </td>
      </tr>`).join('');
    res.innerHTML = `
      <div style="font-size:12px;color:var(--text3);margin-bottom:10px">
        ${d.answer_count} result(s) · PLN query: <code style="color:var(--blue)">${esc(d.pln_query||'')}</code>
      </div>
      <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <thead>
            <tr style="border-bottom:1px solid var(--border)">
              <th style="text-align:left;padding:6px 10px;color:var(--text3);font-weight:600;font-size:11px">Relation</th>
              <th style="text-align:left;padding:6px 10px;color:var(--text3);font-weight:600;font-size:11px">Subject</th>
              <th style="text-align:left;padding:6px 10px;color:var(--text3);font-weight:600;font-size:11px">Object</th>
              <th style="text-align:left;padding:6px 10px;color:var(--text3);font-weight:600;font-size:11px">Str / Conf</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      ${d.inferred_count > 0 ? `<div style="font-size:11px;color:var(--text3);margin-top:12px">
        ${d.inferred_count} atoms in atomspace (original + inferred by rules)
      </div>` : ''}
    `;
  } catch(e) {
    res.innerHTML = `<div style="color:var(--red);font-size:12px">Error: ${esc(e.message)}</div>`;
  }
}

