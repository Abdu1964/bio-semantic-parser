// ── Renderers ─────────────────────────────────────────────────────────────────

function render(n, d) {
  return [,r1,r2,r3,r4,r5,r6,r7,r8][n]?.(d) ?? '';
}

function r1(d) {
  const chips = (d.all_sources||[]).map(s =>
    `<span class="chip"><span class="type fmt-${s.format||'xml'}">${(s.format||'').toUpperCase()}</span>${esc(s.name)}</span>`
  ).join('');
  return `
    <div class="stats">
      <div class="stat"><span class="n n-blue">${(d.all_sources||[]).length}</span> sources</div>
      <div class="stat"><span class="n n-green">${esc(d.source_name)}</span> selected</div>
    </div>
    <div class="chips" style="margin-top:10px">${chips}</div>
    <div class="info-kv"><span class="k">Doc ID:</span><code>${esc(d.doc_id)}</code></div>
    ${d.paper_url?`<div class="info-kv"><span class="k">URL:</span><a href="${esc(d.paper_url)}" target="_blank">${esc(d.paper_url.slice(0,55))}…</a></div>`:''}`;
}

function r2(d) {
  return d.already_processed
    ? `<div style="display:flex;align-items:center;gap:12px;padding:4px 0">
        <span class="dedup-warn">⚠</span>
        <div><div style="font-weight:700;color:var(--yellow)">${d.existing_triples} triples already in KG</div>
        <div style="font-size:11px;color:var(--text3);margin-top:2px">Will re-extract and update confidence scores.</div></div>
       </div>`
    : `<div style="display:flex;align-items:center;gap:12px;padding:4px 0">
        <span class="dedup-ok">✓</span>
        <div><div style="font-weight:700;color:var(--green)">New paper — not yet in KG</div>
        <div style="font-size:11px;color:var(--text3);margin-top:2px">All extracted triples will be fresh.</div></div>
       </div>`;
}

function r3(d) {
  return `<div class="stats">
    <div class="stat"><span class="n n-blue">${d.chunks}</span> chunks</div>
    <div class="stat"><span class="n n-grey">${(d.sections||[]).length}</span> sections</div>
  </div>
  <div class="info-kv" style="margin-top:8px"><span class="k">Title:</span><span class="v">${esc(d.title)}</span></div>`;
}

function r4(d) {
  const pills = (d.entities||[]).slice(0,36).map(e => {
    const cls = 't' + (e.label||'default');
    return `<span class="chip"><span class="type ${cls}">${esc(e.label||'?')}</span>${esc(e.text)}${e.negated?' <span style="color:var(--red);font-size:8px">✗</span>':''}</span>`;
  }).join('');
  return `<div class="stats">
    <div class="stat"><span class="n n-blue">${d.entity_count}</span> entities</div>
    <div class="stat"><span class="n n-red">${d.negated_count}</span> negated</div>
    <div class="stat"><span class="n n-grey">${(d.entity_types||[]).length}</span> types</div>
  </div>
  <div class="chips" style="margin-top:10px">${pills}</div>`;
}

function r5(d) {
  return `<div class="stats">
    <div class="stat"><span class="n n-blue">${d.relation_types}</span> relations</div>
    <div class="stat"><span class="n n-purple">${d.entity_types}</span> entity types</div>
  </div>
  <div class="chips" style="margin-top:10px">
    ${['BioNLP','Hetionet','OpenBioLink','BioCypher','Biolink v4.4.2'].map(s=>`<span class="chip">${s}</span>`).join('')}
  </div>`;
}

function r6(d) {
  return `<div class="stats">
    <div class="stat"><span class="n n-green">${d.viable}</span> extracted</div>
    <div class="stat"><span class="n n-grey">${d.chunks}</span> chunks</div>
    <div class="stat"><span class="n n-red">${d.rejected}</span> → human review</div>
  </div>`;
}

function r7(d) {
  const rows = (d.relations||[]).slice(0,25).map(r => {
    const vc = r.verdict==='VALID'?'valid-v':r.verdict==='DUPLICATE'?'dup-v':'flag-v';
    const cw = Math.round((r.confidence||0)*100);
    return `<tr>
      <td><span class="sub">${esc(r.subject)}</span></td>
      <td><span class="pred">${esc(r.relation)}</span></td>
      <td><span class="obj">${esc(r.object)}</span></td>
      <td><div class="cbar"><div class="ctrack"><div class="cfill" style="width:${cw}%"></div></div>${r.confidence?.toFixed(2)||'—'}</div></td>
      <td><span class="${vc}">${r.verdict||'—'}</span></td>
    </tr>`;
  }).join('');
  return `<div class="stats">
    <div class="stat"><span class="n n-green">${d.valid}</span> valid</div>
    <div class="stat"><span class="n n-grey">${d.duplicates}</span> dup</div>
    <div class="stat"><span class="n n-yellow">${d.flagged}</span> flagged</div>
    <div class="stat"><span class="n n-red">${d.contradictions}</span> conflict</div>
  </div>
  ${rows?`<div style="overflow-x:auto;margin-top:12px"><table class="rtable">
    <thead><tr><th>Subject</th><th>Relation</th><th>Object</th><th>Conf</th><th>Status</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`:''}`;
}

function r8(d) {
  const pct   = d.paper_precision != null
    ? Math.round(d.paper_precision * 100)
    : (d.ver_pct || (d.total > 0 ? Math.round(d.verified / d.total * 100) : 0));
  const color = pct>=90?'var(--green)':pct>=70?'var(--yellow)':'var(--red)';
  return `<div class="stats">
    <div class="stat"><span class="n n-green">${d.auto_insert}</span> inserted</div>
    <div class="stat"><span class="n n-yellow">${d.human_review}</span> review</div>
    <div class="stat"><span class="n" style="color:${color}">${pct}%</span> verified</div>
    <div class="stat"><span class="n n-blue">${d.neo4j_edges}</span> Neo4j edges</div>
    <div class="stat"><span class="n n-purple">${d.metta_edges}</span> MeTTa edges</div>
  </div>`;
}

// Store output data for card clicks
let _outputData = {};

function fileUrl(path) {
  if (!path) return '';
  return path.startsWith('/api/') ? path : `/api/file?path=${encodeURIComponent(path)}`;
}

let _modalSrc = '';

function openViewer(src, title) {
  _modalSrc = src;
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-frame').src = src;
  document.getElementById('file-modal').classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeModal() {
  document.getElementById('file-modal').classList.remove('open');
  document.getElementById('modal-frame').src = 'about:blank';
  document.body.style.overflow = '';
  document.querySelectorAll('.out-card').forEach(c => c.classList.remove('active'));
}

function openModalInTab() {
  if (_modalSrc) window.open(_modalSrc, '_blank');
}

function closeViewer() { closeModal(); }

async function loadHumanReview(runDir, stagingDb) {
  const sec = document.getElementById('review-section');
  const cards = document.getElementById('review-cards');
  if (!sec || !cards || !runDir) return;

  const resp = await fetch(`/api/human-review?run_dir=${encodeURIComponent(runDir)}`);
  const pending = await resp.json();
  if (!Array.isArray(pending) || pending.length === 0) return;

  sec.style.display = 'block';
  cards.innerHTML = pending.map((r, i) => {
    const hasTextSubj   = (r.subject_id || '').startsWith('TEXT:');
    const hasTextObj    = (r.object_id  || '').startsWith('TEXT:');
    const isTextEntity  = hasTextSubj || hasTextObj;
    const verdict = r.validation_verdict || 'REVIEW';
    const displayVerdict = (isTextEntity && verdict === 'VALID') ? 'ID?' : verdict;
    const vcolor  = displayVerdict === 'ID?' ? '#f59e0b'
                  : verdict === 'REVIEW'     ? 'var(--yellow)'
                  : verdict === 'VALID'      ? 'var(--green)' : 'var(--red)';
    const vNote   = displayVerdict === 'ID?'
      ? 'Entity ID not resolved — LLM is looking up the canonical ID below'
      : verdict === 'REVIEW'
      ? 'Uncertain — relation label may be imprecise for what the text states'
      : verdict === 'VALID'
      ? 'Relation is semantically valid — flagged for a different reason, see below'
      : verdict === 'SKIPPED'
      ? 'Validation was skipped — LLM is generating a correction suggestion below'
      : 'Semantic validator found a clear error — see correction suggestion below';
    const rawReason = r.review_reason || r.reasoning || '';
    const parts = rawReason.replace(/SEMANTIC_REVIEW:|SEMANTIC_REJECT:/g,'').split('|').map(s=>s.trim()).filter(Boolean);
    const whyFlagged  = parts[0] || '';
    const issuePart   = parts.find(p=>p.startsWith('Issue')) || '';
    const suggestPart = parts.find(p=>p.startsWith('Suggest')) || '';
    const fullReason  = r.reasoning || '';
    const suggestText = suggestPart.replace(/^Suggested?:\s*/i,'');
    // The LLM's suggestion is free text but consistently uses "Subject: X;
    // Relation: Y; Object: Z" — parse all three fields, not just the
    // relation, so "Apply suggestion" can actually rename a misidentified
    // subject/object (e.g. "low-sodium" → "sodium intake"), not just relabel
    // the edge.
    const suggFields = _parseSuggestionFields(suggestText);
    const suggestedRelation = suggFields.relation || '';
    const suggDataAttr = JSON.stringify(suggFields).replace(/'/g,"&#39;");
    const recJson = JSON.stringify(r).replace(/'/g,"&#39;");
    // First card open, rest collapsed
    const bodyDisplay = i === 0 ? 'block' : 'none';
    const chevron     = i === 0 ? '▲' : '▼';
    return `<div style="background:var(--bg2);border:1px solid ${vcolor}55;border-radius:12px;margin-bottom:8px;overflow:hidden" id="rc-${i}" data-orig-rel="${esc(r.relation||'')}" data-verdict="${displayVerdict}" data-had-text-subj="${hasTextSubj?'1':'0'}" data-had-text-obj="${hasTextObj?'1':'0'}">
      <!-- Collapsible header — always visible -->
      <div onclick="toggleReviewCard(${i})" style="display:flex;align-items:center;gap:10px;padding:12px 16px;cursor:pointer;user-select:none">
        <span style="color:${vcolor};font-size:10px;font-weight:700;border:1px solid ${vcolor};padding:1px 8px;border-radius:8px;flex-shrink:0">${displayVerdict}</span>
        <span style="font-size:13px;font-weight:600;color:var(--text);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
          <span id="subj-name-${i}">${esc(r.subject_name||'')}</span> <span id="rel-display-${i}" style="color:var(--blue)">→${esc(r.relation||'')}→</span> <span id="obj-name-${i}">${esc(r.object_name||'')}</span>
        </span>
        <span id="rc-chevron-${i}" style="color:var(--text3);font-size:11px;flex-shrink:0">${chevron}</span>
      </div>
      <!-- Collapsible body -->
      <div id="rc-body-${i}" style="display:${bodyDisplay};padding:0 16px 16px">
        <div style="font-size:11px;color:var(--text3);margin-bottom:8px">${esc(vNote)}</div>
        ${whyFlagged ? `<div style="margin-bottom:4px"><span style="color:var(--text3);font-size:10px;font-weight:600;text-transform:uppercase">Why flagged</span><div style="color:var(--text2);font-size:12px;margin-top:2px">${esc(whyFlagged)}</div></div>` : ''}
        ${issuePart  ? `<div style="margin-bottom:4px"><span style="color:var(--text3);font-size:10px;font-weight:600;text-transform:uppercase">Issue</span><div style="color:var(--text2);font-size:12px;margin-top:2px">${esc(issuePart.replace(/^Issues?:\s*/i,''))}</div></div>` : ''}
        ${suggestPart ? `<div style="margin-bottom:6px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <div><span style="color:var(--blue);font-size:10px;font-weight:600;text-transform:uppercase">Suggestion</span><div style="color:var(--blue);font-size:12px;margin-top:2px">${esc(suggestText)}</div></div>
          ${(suggFields.relation || suggFields.subject || suggFields.object) ? `<button id="sugg-btn-${i}" data-sugg='${suggDataAttr}' onclick="event.stopPropagation();applySuggestion(${i})"
            style="background:rgba(56,189,248,.15);border:1px solid var(--blue);border-radius:6px;padding:3px 12px;color:var(--blue);font-size:11px;cursor:pointer;font-family:var(--font);font-weight:600;white-space:nowrap;align-self:flex-end">
            ↕ Apply suggestion</button>` : ''}
        </div>` : ''}
        <div id="llm-correction-${i}" style="display:none;margin-bottom:6px;padding:8px 10px;background:rgba(56,189,248,.07);border:1px solid rgba(56,189,248,.3);border-radius:8px">
          <span style="color:var(--blue);font-size:10px;font-weight:700;text-transform:uppercase">LLM Correction</span>
          <div id="llm-correction-text-${i}" style="color:var(--blue);font-size:12px;margin-top:4px"></div>
        </div>
        <div id="id-correction-${i}" style="margin-top:6px;padding:8px 10px;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.3);border-radius:8px;display:${isTextEntity ? 'block' : 'none'}">
          <div style="color:#f59e0b;font-size:10px;font-weight:700;text-transform:uppercase;margin-bottom:6px">Canonical ID Correction</div>
          <div id="subj-id-row-${i}" style="display:${hasTextSubj ? 'flex' : 'none'};align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap">
            <span style="font-size:11px;color:var(--text3);min-width:52px">Subject:</span>
            <span id="subj-id-name-${i}" style="font-size:12px;color:var(--text2)">${esc(r.subject_name||'')}</span>
            <div style="display:flex;flex-direction:column;gap:2px">
              <input id="subj-id-${i}" type="text" value="" placeholder="Looking up…"
                style="background:var(--bg3);border:1px solid #f59e0b88;border-radius:4px;padding:3px 8px;
                       color:var(--text);font-size:11px;font-family:var(--font);width:200px;outline:none"/>
              <span id="subj-id-hint-${i}" style="font-size:10px;color:var(--text3);padding-left:2px"></span>
            </div>
          </div>
          <div id="obj-id-row-${i}" style="display:${hasTextObj ? 'flex' : 'none'};align-items:center;gap:8px;flex-wrap:wrap">
            <span style="font-size:11px;color:var(--text3);min-width:52px">Object:</span>
            <span id="obj-id-name-${i}" style="font-size:12px;color:var(--text2)">${esc(r.object_name||'')}</span>
            <div style="display:flex;flex-direction:column;gap:2px">
              <input id="obj-id-${i}" type="text" value="" placeholder="Looking up…"
                style="background:var(--bg3);border:1px solid #f59e0b88;border-radius:4px;padding:3px 8px;
                       color:var(--text);font-size:11px;font-family:var(--font);width:200px;outline:none"/>
              <span id="obj-id-hint-${i}" style="font-size:10px;color:var(--text3);padding-left:2px"></span>
            </div>
          </div>
        </div>
        ${fullReason ? `<details style="margin-top:6px"><summary style="color:var(--text3);font-size:11px;cursor:pointer">▸ Full LLM reasoning</summary><div style="color:var(--text2);font-size:12px;font-style:italic;margin-top:4px;padding:8px;background:var(--bg3);border-radius:6px">"${esc(fullReason)}"</div></details>` : ''}
        <div style="display:flex;gap:8px;margin-top:10px">
          <button id="approve-btn-${i}" onclick="reviewAction(${i},this,'approve')" data-rec='${recJson}' data-rundir="${esc(runDir)}" data-sdb="${esc(stagingDb)}"
            style="background:rgba(34,197,94,.15);border:1px solid #22c55e;border-radius:8px;padding:6px 16px;color:#22c55e;font-size:12px;cursor:pointer;font-family:var(--font);font-weight:600">✓ Approve — add to KG</button>
          <button onclick="reviewAction(${i},this,'reject')" data-rec='${recJson}' data-rundir="${esc(runDir)}" data-sdb="${esc(stagingDb)}"
            style="background:rgba(248,113,113,.12);border:1px solid var(--red);border-radius:8px;padding:6px 16px;color:var(--red);font-size:12px;cursor:pointer;font-family:var(--font);font-weight:600">✗ Reject — discard</button>
        </div>
      </div>
    </div>`;
  }).join('');

  // Async: fetch canonical ID suggestions for TEXT: entities; auto-approve when ID? verdict + all verified
  window._cardFetchState = {};
  pending.forEach((r, i) => {
    const hasTextSubj = (r.subject_id || '').startsWith('TEXT:');
    const hasTextObj  = (r.object_id  || '').startsWith('TEXT:');
    const needed = (hasTextSubj ? 1 : 0) + (hasTextObj ? 1 : 0);
    const context = r.reasoning || r.review_reason || '';
    if (needed > 0) {
      window._cardFetchState[i] = { needed, resolved: 0, allVerified: true };
      if (hasTextSubj) _fetchCanonicalId(r.subject_name || '', r.subject_type || '', i, 'subj', context);
      if (hasTextObj)  _fetchCanonicalId(r.object_name  || '', r.object_type  || '', i, 'obj', context);
    }
    // For SKIPPED/REJECTED cards with no existing suggestion, auto-fetch a correction
    const verdict = r.validation_verdict || '';
    const hasSuggestion = (r.review_reason || '').toLowerCase().includes('suggest');
    if ((verdict === 'SKIPPED' || verdict === 'REJECT') && !hasSuggestion) {
      _fetchSuggestedCorrection(r, i);
    }
  });
}

async function _fetchCanonicalId(entityName, entityType, cardIdx, role, context) {
  const inputEl = document.getElementById(`${role}-id-${cardIdx}`);
  const hintEl  = document.getElementById(`${role}-id-hint-${cardIdx}`);
  if (!inputEl) return;
  let verified = false;
  try {
    const ctx = context ? `&context=${encodeURIComponent((context || '').slice(0, 400))}` : '';
    const resp = await fetch(`/api/suggest-id?name=${encodeURIComponent(entityName)}&entity_type=${encodeURIComponent(entityType)}${ctx}`);
    const data = await resp.json();
    if (data.id) {
      if (!inputEl.value || inputEl.value === '') {
        inputEl.value = data.id;
        inputEl.style.borderColor = data.verified ? '#22c55e88' : '#f59e0b88';
      }
      verified = !!data.verified;
      if (hintEl) {
        const src  = data.source ? ` · ${data.source}` : '';
        const name = data.official_name ? ` "${data.official_name}"` : '';
        if (data.verified) {
          hintEl.innerHTML = `<span style="color:#22c55e">✓ Verified${src}${name}</span>`;
        } else if (data.confidence === 'low') {
          hintEl.innerHTML = `<span style="color:var(--yellow)">⚠ LLM suggestion — verify before approving${src}</span>`;
        } else {
          hintEl.innerHTML = `<span style="color:var(--yellow)">⚠ Unverified${src}</span>`;
        }
      }
      // Stash so reviewAction() can refresh canonical_name/id_source on
      // approve — without this, approving keeps whatever stale enrichment
      // was in the record (or none), even though a fresh ID was just found.
      window._suggResolved = window._suggResolved || {};
      window._suggResolved[cardIdx] = window._suggResolved[cardIdx] || {};
      window._suggResolved[cardIdx][role] = {
        id: data.id, name: data.official_name || '', source: data.source || '',
        source_url: data.source_url || '', synonyms: data.synonyms || '',
      };
    } else {
      if (inputEl.placeholder === 'Looking up…') inputEl.placeholder = 'e.g. hgnc:1234';
      if (hintEl) hintEl.textContent = 'No suggestion — enter manually';
    }
  } catch(_) {
    if (inputEl.placeholder === 'Looking up…') inputEl.placeholder = 'e.g. hgnc:1234';
  }

  // Track completion for auto-approve
  const state = window._cardFetchState && window._cardFetchState[cardIdx];
  if (state) {
    if (!verified) state.allVerified = false;
    state.resolved++;
    const card = document.getElementById(`rc-${cardIdx}`);
    const verdict = card ? card.dataset.verdict : '';
    if (state.resolved >= state.needed) {
      if (state.allVerified && verdict === 'ID?') {
        // All canonical IDs verified, only reason was missing ID → auto-approve
        const approveBtn = document.getElementById(`approve-btn-${cardIdx}`);
        if (approveBtn) {
          approveBtn.textContent = '⟳ Auto-approving verified IDs…';
          approveBtn.style.opacity = '0.7';
          setTimeout(() => approveBtn.click(), 800);
        }
      } else if (state.allVerified) {
        // Semantic REVIEW/REJECT but IDs are now verified — update button label
        const approveBtn = document.getElementById(`approve-btn-${cardIdx}`);
        if (approveBtn) approveBtn.textContent = '✓ Approve — use suggested IDs';
      }
    }
  }
}

async function _fetchSuggestedCorrection(record, cardIdx) {
  const correctionBox  = document.getElementById(`llm-correction-${cardIdx}`);
  const correctionText = document.getElementById(`llm-correction-text-${cardIdx}`);
  if (!correctionBox || !correctionText) return;

  const params = new URLSearchParams({
    subject:      record.subject_name || '',
    relation:     record.relation     || '',
    object:       record.object_name  || '',
    subject_type: record.subject_type || '',
    object_type:  record.object_type  || '',
    source_text:  (record.reasoning   || '').slice(0, 400),
    review_reason:(record.review_reason || '').slice(0, 300),
  });

  try {
    const resp = await fetch(`/api/suggest-correction?${params}`);
    const data = await resp.json();
    const c    = data.correction;
    if (!c || !c.relation) return;

    const confColor = c.confidence === 'high' ? '#22c55e' : c.confidence === 'medium' ? '#f59e0b' : 'var(--text3)';
    correctionText.innerHTML =
      `<span style="color:var(--text2)">${esc(c.subject)} <span style="color:var(--blue)">→${esc(c.relation)}→</span> ${esc(c.object)}</span>` +
      (c.explanation ? `<div style="color:var(--text3);font-size:11px;margin-top:3px">${esc(c.explanation)}</div>` : '') +
      `<div style="margin-top:6px"><button onclick="event.stopPropagation();_applyLlmCorrection(${cardIdx},${JSON.stringify(c).replace(/"/g,'&quot;')})"
        style="background:rgba(56,189,248,.15);border:1px solid var(--blue);border-radius:6px;padding:3px 12px;color:var(--blue);font-size:11px;cursor:pointer;font-family:var(--font);font-weight:600">
        ↕ Apply correction</button>
        <span style="font-size:10px;color:${confColor};margin-left:8px">${c.confidence} confidence</span></div>`;
    correctionBox.style.display = 'block';
  } catch(_) {}
}

function _applyLlmCorrection(cardIdx, correction) {
  const subjEl = document.getElementById(`subj-name-${cardIdx}`);
  const objEl  = document.getElementById(`obj-name-${cardIdx}`);
  const relEl  = document.getElementById(`rel-display-${cardIdx}`);
  if (subjEl && correction.subject) subjEl.textContent = correction.subject;
  if (objEl  && correction.object)  objEl.textContent  = correction.object;
  if (relEl  && correction.relation) relEl.textContent = `→${correction.relation}→`;

  // If entities changed, trigger a fresh ID lookup for the corrected names
  const card = document.getElementById(`rc-${cardIdx}`);
  if (!card) return;
  const hadTextSubj = card.dataset.hadTextSubj === '1';
  const hadTextObj  = card.dataset.hadTextObj  === '1';
  const corrBox     = document.getElementById(`id-correction-${cardIdx}`);

  if (correction.subject && hadTextSubj) {
    const subjectType = document.getElementById(`subj-id-name-${cardIdx}`)?.dataset?.type || '';
    document.getElementById(`subj-id-name-${cardIdx}`).textContent = correction.subject;
    document.getElementById(`subj-id-${cardIdx}`).value = '';
    document.getElementById(`subj-id-${cardIdx}`).placeholder = 'Looking up…';
    if (corrBox) corrBox.style.display = 'block';
    _fetchCanonicalId(correction.subject, subjectType, cardIdx, 'subj', correction.explanation || '');
  }
  if (correction.object && hadTextObj) {
    const objectType = document.getElementById(`obj-id-name-${cardIdx}`)?.dataset?.type || '';
    document.getElementById(`obj-id-name-${cardIdx}`).textContent = correction.object;
    document.getElementById(`obj-id-${cardIdx}`).value = '';
    document.getElementById(`obj-id-${cardIdx}`).placeholder = 'Looking up…';
    if (corrBox) corrBox.style.display = 'block';
    _fetchCanonicalId(correction.object, objectType, cardIdx, 'obj', correction.explanation || '');
  }
}

function _refreshVerificationCounter(verified, total) {
  if (!total) return;
  const pct = Math.round(verified / total * 100);
  const clr = pct >= 90 ? '#22c55e' : pct >= 70 ? '#eab308' : '#ef4444';
  const arc  = document.getElementById('ver-arc');
  const ring = document.getElementById('ver-pct-ring');
  const cnt  = document.getElementById('ver-count');
  const tot  = document.getElementById('ver-total');
  const bar  = document.getElementById('ver-bar');
  if (arc)  { arc.setAttribute('stroke', clr); arc.setAttribute('stroke-dasharray', `${Math.round(pct/100*138.2)} 138.2`); }
  if (ring) { ring.textContent = pct + '%'; ring.style.color = clr; }
  if (cnt)  { cnt.textContent = verified; cnt.style.color = clr; }
  if (tot)  { tot.textContent = ' / ' + total; }
  if (bar)  { bar.style.width = pct + '%'; bar.style.background = clr; }
}

function toggleReviewCard(i) {
  const body    = document.getElementById(`rc-body-${i}`);
  const chevron = document.getElementById(`rc-chevron-${i}`);
  if (!body) return;
  const open = body.style.display !== 'none';
  body.style.display    = open ? 'none' : 'block';
  if (chevron) chevron.textContent = open ? '▼' : '▲';
}

function applySuggestion(idx) {
  const card    = document.getElementById(`rc-${idx}`);
  const suggBtn = document.getElementById(`sugg-btn-${idx}`);
  const relSpan = document.getElementById(`rel-display-${idx}`);
  if (!card || !suggBtn || !relSpan) return;

  const sugg = JSON.parse((suggBtn.dataset.sugg || '{}').replace(/&#39;/g,"'"));
  const isApplied = card.dataset.suggApplied === '1';
  const origRel   = card.dataset.origRel || '';
  const newRel    = isApplied ? origRel : (sugg.relation || origRel);

  const origSubject = card.dataset.origSubjectData ? JSON.parse(card.dataset.origSubjectData) : null;
  const origObject  = card.dataset.origObjectData  ? JSON.parse(card.dataset.origObjectData)  : null;

  // The LLM's "suggested correction" often only actually changes ONE side —
  // the other is echoed back verbatim (e.g. correcting "low-sodium" while
  // repeating the already-correct object "BP" unchanged). Only treat a side
  // as needing correction if the suggested text actually DIFFERS from what
  // was there originally — otherwise this would needlessly clear an
  // already-correct resolution and re-fetch a fresh (possibly worse) one.
  // Computed ONCE on the first apply (when rec still holds true originals)
  // and persisted, since on undo the current rec no longer reflects them.
  let subjChanged, objChanged;
  if (!isApplied) {
    const firstRec = JSON.parse(card.querySelector('[data-rec]').dataset.rec.replace(/&#39;/g,"'"));
    subjChanged = !!sugg.subject && sugg.subject.trim().toLowerCase() !== (firstRec.subject_name || '').trim().toLowerCase();
    objChanged  = !!sugg.object  && sugg.object.trim().toLowerCase()  !== (firstRec.object_name  || '').trim().toLowerCase();
    card.dataset.subjSuggChanged = subjChanged ? '1' : '0';
    card.dataset.objSuggChanged  = objChanged  ? '1' : '0';
  } else {
    subjChanged = card.dataset.subjSuggChanged === '1';
    objChanged  = card.dataset.objSuggChanged  === '1';
  }

  // Patch data-rec on both Approve and Reject buttons
  card.querySelectorAll('[data-rec]').forEach(b => {
    const rec = JSON.parse(b.dataset.rec.replace(/&#39;/g,"'"));
    rec.relation = newRel;

    if (subjChanged) {
      if (!isApplied) {
        // Stash the full original entity (name + resolved ID/enrichment) so
        // "Undo suggestion" can restore it exactly, not just the name.
        card.dataset.origSubjectData = JSON.stringify({
          subject_name: rec.subject_name, subject_id: rec.subject_id,
          subject_canonical_name: rec.subject_canonical_name, subject_source_url: rec.subject_source_url,
          subject_synonyms: rec.subject_synonyms, subject_id_source: rec.subject_id_source,
          subject_needs_review: rec.subject_needs_review,
        });
        rec.subject_name = sugg.subject;
        // The old subject_id/canonical_name/etc. were resolved against the
        // OLD (wrong) subject text — they no longer describe this entity,
        // so clear them rather than silently publishing a mismatched ID.
        rec.subject_id             = '';
        rec.subject_canonical_name = '';
        rec.subject_source_url     = '';
        rec.subject_synonyms       = '';
        rec.subject_id_source      = '';
        rec.subject_needs_review   = true;
      } else if (origSubject) {
        Object.assign(rec, origSubject);
      }
    }
    if (objChanged) {
      if (!isApplied) {
        card.dataset.origObjectData = JSON.stringify({
          object_name: rec.object_name, object_id: rec.object_id,
          object_canonical_name: rec.object_canonical_name, object_source_url: rec.object_source_url,
          object_synonyms: rec.object_synonyms, object_id_source: rec.object_id_source,
          object_needs_review: rec.object_needs_review,
        });
        rec.object_name = sugg.object;
        rec.object_id             = '';
        rec.object_canonical_name = '';
        rec.object_source_url     = '';
        rec.object_synonyms       = '';
        rec.object_id_source      = '';
        rec.object_needs_review   = true;
      } else if (origObject) {
        Object.assign(rec, origObject);
      }
    }
    b.dataset.rec = JSON.stringify(rec).replace(/'/g,"&#39;");
  });

  // Update the relation display in the triple header
  relSpan.textContent = `→${newRel}→`;

  // Update subject/object name display + trigger a fresh canonical-ID lookup
  // for whichever side was renamed (reusing the same /api/suggest-id flow
  // already built for TEXT: entities — a renamed entity needs the identical
  // treatment even if it wasn't originally unresolved).
  const applySide = (role, origData, nameKey, idKey, typeKey) => {
    const nameSpan = document.getElementById(`${role}-name-${idx}`);
    const rowEl    = document.getElementById(`${role}-id-row-${idx}`);
    const wrapEl   = document.getElementById(`id-correction-${idx}`);
    const lblEl    = document.getElementById(`${role}-id-name-${idx}`);
    const inputEl  = document.getElementById(`${role}-id-${idx}`);
    const hintEl   = document.getElementById(`${role}-id-hint-${idx}`);
    const hadText  = card.dataset[`hadText${role === 'subj' ? 'Subj' : 'Obj'}`] === '1';

    if (!isApplied) {
      const newName = sugg[role === 'subj' ? 'subject' : 'object'];
      if (nameSpan) nameSpan.textContent = newName;
      if (wrapEl) wrapEl.style.display = 'block';
      if (rowEl)  rowEl.style.display  = 'flex';
      if (lblEl)  lblEl.textContent    = newName;
      if (inputEl) { inputEl.value = ''; inputEl.placeholder = 'Looking up…'; inputEl.style.borderColor = '#f59e0b88'; }
      if (hintEl)  hintEl.textContent  = '';
      const anyRec = JSON.parse(card.querySelector('[data-rec]').dataset.rec.replace(/&#39;/g,"'"));
      _fetchCanonicalId(newName, anyRec[typeKey] || '', idx, role);
    } else if (origData) {
      const origName = origData[nameKey] || '';
      if (nameSpan) nameSpan.textContent = origName;
      if (lblEl)    lblEl.textContent    = origName;
      if (inputEl)  inputEl.value        = (origData[idKey] || '').startsWith('TEXT:') ? '' : (origData[idKey] || '');
      if (hintEl)   hintEl.textContent   = '';
      if (!hadText) {
        if (rowEl) rowEl.style.display = 'none';
        const bothHidden = card.dataset.hadTextSubj !== '1' && card.dataset.hadTextObj !== '1';
        if (wrapEl && bothHidden) wrapEl.style.display = 'none';
      }
    }
  };
  if (subjChanged) applySide('subj', origSubject, 'subject_name', 'subject_id', 'subject_type');
  if (objChanged)  applySide('obj',  origObject,  'object_name',  'object_id',  'object_type');

  // Toggle button label and applied state
  card.dataset.suggApplied = isApplied ? '0' : '1';
  suggBtn.textContent = isApplied ? '↕ Apply suggestion' : '↩ Undo suggestion';
  suggBtn.style.background = isApplied ? 'rgba(56,189,248,.15)' : 'rgba(34,197,94,.15)';
  suggBtn.style.borderColor = isApplied ? 'var(--blue)' : '#22c55e';
  suggBtn.style.color       = isApplied ? 'var(--blue)' : '#22c55e';
}

async function reviewAction(idx, btn, action) {
  const runDir = btn.dataset.rundir;
  const sdb    = btn.dataset.sdb;
  const rec    = JSON.parse(btn.dataset.rec.replace(/&#39;/g,"'"));
  const card   = document.getElementById(`rc-${idx}`);

  // Apply any corrected canonical IDs from the TEXT: entity edit inputs
  if (action === 'approve') {
    const subjIn = document.getElementById(`subj-id-${idx}`);
    const objIn  = document.getElementById(`obj-id-${idx}`);
    const resolved = window._suggResolved && window._suggResolved[idx];
    if (subjIn && subjIn.value.trim()) {
      rec.subject_id = subjIn.value.trim();
      // Only trust the auto-resolved canonical_name/id_source if the ID
      // wasn't hand-edited to something else after the lookup ran.
      if (resolved && resolved.subj && resolved.subj.id === rec.subject_id) {
        rec.subject_canonical_name = resolved.subj.name;
        rec.subject_id_source      = resolved.subj.source;
        rec.subject_source_url     = resolved.subj.source_url || '';
        rec.subject_synonyms       = resolved.subj.synonyms   || '';
      }
    }
    if (objIn && objIn.value.trim()) {
      rec.object_id = objIn.value.trim();
      if (resolved && resolved.obj && resolved.obj.id === rec.object_id) {
        rec.object_canonical_name = resolved.obj.name;
        rec.object_id_source      = resolved.obj.source;
        rec.object_source_url     = resolved.obj.source_url || '';
        rec.object_synonyms       = resolved.obj.synonyms   || '';
      }
    }
  }

  const fd = new FormData();
  fd.append('run_dir',    runDir);
  fd.append('staging_db', sdb);
  fd.append('records',    JSON.stringify([rec]));
  fd.append('action',     action);

  const resp = await fetch('/api/human-review/action', {method:'POST', body:fd});
  const d    = await resp.json();
  if (d.ok && card) {
    card.style.opacity = '0.4';
    card.style.pointerEvents = 'none';
    card.querySelector('div').insertAdjacentHTML('afterend',
      `<div style="color:${action==='approve'?'var(--green)':'var(--red)'};font-size:12px;font-weight:600;margin-top:6px">${action==='approve'?'✓ Approved — added to graph':'✗ Rejected'}</div>`);
    // Refresh staging breakdown
    if (_stagingDb) fetchStagingBreakdown(_stagingDb, _commitFormat);

    // Refresh Paper Graph and Verification cards after approve/reject.
    // Poll for real stats from the rebuilt CSV files (background rebuild takes a few seconds).
    if (_outputData && _outputData.run_dir) {
      const runDir = _outputData.run_dir;
      const bust = p => p ? p + (p.includes('?') ? '&' : '?') + 't=' + Date.now() : p;
      // Poll up to 10 times (every 2s) until edge count changes
      const origEdges = _outputData.neo4j_edges || 0;
      let attempts = 0;
      const pollStats = async () => {
        attempts++;
        try {
          const sr = await fetch(`/api/run-graph-stats?run_dir=${encodeURIComponent(runDir)}`);
          const sd = await sr.json();
          if (sd.edges !== origEdges || attempts >= 10) {
            const updatedVerified = sd.total > 0 ? sd.verified : (_outputData.verified || 0);
            const updatedTotal    = sd.total > 0 ? sd.total    : (_outputData.total    || 0);
            _outputData = { ..._outputData, neo4j_edges: sd.edges || origEdges,
              neo4j_nodes: sd.nodes || _outputData.neo4j_nodes || 0,
              verified: updatedVerified, total: updatedTotal };
            // Refresh just the counter + iframes (avoid full re-render flash)
            _refreshVerificationCounter(updatedVerified, updatedTotal);
            const ts = Date.now();
            const graphFrame = document.querySelector('iframe[src*="graph"]');
            if (graphFrame && _outputData.graph_html) graphFrame.src = bust(_outputData.graph_html);
            const verFrame = document.querySelector('iframe[src*="verification"]');
            if (verFrame && _outputData.ver_html) verFrame.src = bust(_outputData.ver_html);
            return;
          }
        } catch(e) {}
        if (attempts < 10) setTimeout(pollStats, 2000);
      };
      setTimeout(pollStats, 2000); // start polling after 2s (give background thread time)
    }
  }
}

async function fetchStagingBreakdown(stagingDb, outputFormat) {
  try {
    const url = `/api/staging-info?staging_db=${encodeURIComponent(stagingDb)}&output_format=${encodeURIComponent(outputFormat||'both')}`;
    const r = await fetch(url);
    const d = await r.json();
    const el = document.getElementById('staging-breakdown');
    if (!el) return;
    const { n_staged=0, n_already=0, n_net_new=0 } = d;
    if (n_staged === 0) {
      el.innerHTML = '<span style="color:var(--text3)">No gate-passing triples in staging.</span>';
    } else if (n_already === 0) {
      el.innerHTML = `<span style="font-weight:700">${n_staged}</span> triple(s) extracted — <span style="color:var(--green);font-weight:700">all ${n_staged} are new</span> to the atomspace.`;
    } else {
      el.innerHTML =
        `<span style="font-weight:700">${n_staged}</span> triple(s) extracted from this paper:<br>` +
        `<span style="color:var(--yellow)">${n_already}</span> already in atomspace <span style="color:var(--text3)">(confidence will be updated)</span><br>` +
        `<span style="color:var(--green);font-weight:700">${n_net_new}</span> are new and will be added`;
    }
    el.style.display = 'block';
  } catch(e) {}
}

function renderInspectActions(d) {
  // Buttons removed — outputs accessible via the output cards above
}

function renderOutput(d) {
  _outputData = d;
  window.renderOutput = renderOutput;   // expose for state restore
  AppState.save(d);                     // persist to localStorage

  // ── If multiple existing runs found — show selector before output ─────────
  if (d.existing_runs && d.existing_runs.length > 1) {
    _showRunSelector(d.existing_runs, d);
  }

  // Store staging info for commit
  _stagingDb = d.staging_db || '';
  if (_stagingDb) {
    document.getElementById('commit-section').style.display = 'block';
    document.getElementById('commit-btn').disabled = false;
    document.getElementById('commit-btn').textContent = '✓ Commit to Unified KG';
    document.getElementById('commit-result').style.display = 'none';
    fetchStagingBreakdown(_stagingDb, _commitFormat);
  } else if (d.n_runs >= 1) {
    // Existing outputs loaded — no staging DB but show action buttons
    _showExistingOutputActions(d);
  }
  renderInspectActions(d);
  // Load human review triples if any
  if (d.run_dir && d.staging_db) loadHumanReview(d.run_dir, d.staging_db);
  document.getElementById('output').classList.add('show');

  // Refresh verification stats from server (verification_report.json may have been
  // updated since the pipeline ran, e.g. after human review approvals or verify_kg rebuild)
  if (d.run_dir) {
    fetch(`/api/run-graph-stats?run_dir=${encodeURIComponent(d.run_dir)}`)
      .then(r => r.json())
      .then(sd => {
        if (sd.total > 0 && (sd.verified !== (d.verified||0) || sd.total !== (d.total||0))) {
          _outputData = { ..._outputData, verified: sd.verified, total: sd.total };
          // Re-render just the inline donut/counter
          _refreshVerificationCounter(sd.verified, sd.total);
        }
      }).catch(() => {});
  }

  const cards = [];
  // Donut/bar = inline confirmation rate (verified triples against source text)
  // paper_precision is a separate accuracy metric shown only on the Neo4j card
  const pct = d.total > 0 ? Math.round(d.verified / d.total * 100) : (d.ver_pct || 0);
  const color = pct>=90?'var(--green)':pct>=70?'var(--yellow)':'var(--red)';

  // Card 1: Paper graph
  if (d.graph_html) {
    cards.push({
      icon:'◈', label:'Paper Graph',
      meta:`${d.neo4j_edges||0} edges · ${d.neo4j_nodes||0} nodes`,
      badge:'Neo4j', badgeCls:'oc-neo4j',
      src: fileUrl(d.graph_html), title:'Paper Knowledge Graph',
    });
  }

  // Card 2: Neo4j verification — show precision/recall/F1 if available
  if (d.ver_html && d.neo4j_edges > 0) {
    const prec = d.paper_precision != null ? Math.round(d.paper_precision*100)+'% P' : pct+'%';
    const rec  = d.paper_recall    != null ? Math.round(d.paper_recall*100)+'% R'    : '';
    const f1   = d.paper_f1        != null ? Math.round(d.paper_f1*100)+'% F1'       : '';
    const metaStr = [prec, rec, f1].filter(Boolean).join(' · ');
    cards.push({
      icon:'✓', label:'Neo4j Verification',
      meta: metaStr || `${pct}% confirmed`,
      badge:'Neo4j', badgeCls:'oc-neo4j',
      src: fileUrl(d.ver_html), title:'Neo4j Verification Report',
    });
  }

  // Card 3: MeTTa verification
  if (d.ver_html && d.metta_edges > 0) {
    cards.push({
      icon:'✓', label:'MeTTa Verification',
      meta:`${pct}% confirmed`,
      badge:'MeTTa', badgeCls:'oc-metta',
      src: fileUrl(d.ver_html), title:'MeTTa Verification Report',
    });
  }

  // Card: Inline verification summary
  cards.push({
    icon:'≡', label:'Inline Summary',
    meta:`${d.verified||0}/${d.total||0} triples`,
    badge:'Verify', badgeCls:'oc-verify',
    src: null, title:'Inline Verification',
    inline: true,
  });

  // Card: Manage Triples (inline editor for staging DB)
  if (d.staging_db) {
    cards.push({
      icon:'✎', label:'Manage Triples',
      meta:'View · Edit · Delete',
      badge:'Edit', badgeCls:'oc-edit',
      src: null, title:'Manage KG Triples',
      manageTriples: true, stagingDb: d.staging_db, runDir: d.run_dir || '',
    });
  }

  const cardsHtml = cards.map((c, i) => `
    <div class="out-card" id="oc-${i}" onclick="onCardClick(${i})" title="${c.inline?'Scroll to summary below':c.manageTriples?'Click to view and edit triples':'Click to preview · ↗ to open in new tab'}">
      <div class="oc-icon">${c.icon}</div>
      <div class="oc-label">${c.label}</div>
      <div class="oc-meta">${c.meta}</div>
      ${c.inline
        ? `<span class="oc-badge ${c.badgeCls}">${c.badge}</span>`
        : `<a class="oc-badge ${c.badgeCls}" href="${c.src}" target="_blank"
             onclick="event.stopPropagation()" title="Open in new tab">${c.badge} ↗</a>`
      }
    </div>`).join('');

  document.getElementById('out-cards').innerHTML = cardsHtml;

  // Show group labels
  if (cards.length > 0) {
    // kg-group-label removed — all cards on same row now
  }

  // PLN card — only shown when pln_graph.html was explicitly returned by the backend
  const plnHtml = d.pln_html || '';
  const plnSrc  = plnHtml ? fileUrl(plnHtml) : '';
  if (plnSrc && _PLN_ENABLED) {
    document.getElementById('pln-cards').innerHTML = `
      <div class="out-card pln-card" onclick="openViewer('${plnSrc}','PLN AtomSpace')"
           title="Opens PLN graph — stv values, inferred triples, contradictions ↗">
        <div class="oc-icon">🔮</div>
        <div class="oc-label">PLN AtomSpace</div>
        <div class="oc-meta">${d.pln_extracted||0} extracted · ${d.pln_inferred||0} inferred</div>
        <span class="oc-badge oc-pln">MeTTa + stv ↗</span>
      </div>`;
  } else {
    document.getElementById('pln-cards').innerHTML = '';
  }

  // Store for click handler
  window._outCards = cards;

  // Render inline verification summary
  if (d.total > 0) {
    const rows = (d.ver_results||[]).map(r => {
      const ok  = r.verified === true || r.overall_status === 'CONFIRMED';
      const rel = r.relation || r.label || '';
      // subject/object come from verify_kg.py; fallback to _name variants
      const subj = r.subject || r.subject_name || r.source_name || '';
      const obj  = r.object  || r.object_name  || r.target_name  || '';
      // Evidence: supporting sentence from sources array, or mismatch note
      const src0 = (r.sources||[])[0] || {};
      const evidence = src0.sentence || src0.note || r.supporting_text || r.mismatch_reason || '';
      return `<tr>
        <td style="color:${ok?'var(--green)':'var(--red)'};font-weight:700;text-align:center;width:24px">${ok?'✓':'✗'}</td>
        <td><span class="sub">${esc(subj)}</span></td>
        <td><span class="pred">${esc(rel)}</span></td>
        <td><span class="obj">${esc(obj)}</span></td>
        <td style="color:var(--text3);font-size:10px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
            title="${esc(evidence)}">${esc(evidence.slice(0,90))}${evidence.length>90?'…':''}</td>
      </tr>`;
    }).join('');
    const vc = document.getElementById('ver-content');
    vc.style.display = 'block';
    const _pctRaw = pct;
    const _clr    = _pctRaw>=90?'var(--green)':_pctRaw>=70?'var(--yellow)':'var(--red)';
    const _clrHex = _pctRaw>=90?'#22c55e':_pctRaw>=70?'#eab308':'#ef4444';
    const _barW   = Math.round(_pctRaw);
    vc.innerHTML = `
      <div class="ver-wrap ver-collapsed" id="ver-wrap-toggle" onclick="toggleVerification(this)" title="Click to expand / collapse">
        <div style="display:flex;align-items:center;gap:16px">

          <!-- Percentage ring -->
          <div style="position:relative;flex-shrink:0;width:54px;height:54px">
            <svg width="54" height="54" viewBox="0 0 54 54">
              <circle cx="27" cy="27" r="22" fill="none" stroke="var(--bg3)" stroke-width="4"/>
              <circle id="ver-arc" cx="27" cy="27" r="22" fill="none" stroke="${_clrHex}" stroke-width="4"
                stroke-dasharray="${Math.round(_pctRaw/100*138.2)} 138.2"
                stroke-dashoffset="34.6" stroke-linecap="round"
                transform="rotate(-90 27 27)"/>
            </svg>
            <div id="ver-pct-ring" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
              font-size:12px;font-weight:800;font-family:var(--mono);color:${_clrHex}">${_pctRaw}%</div>
          </div>

          <!-- Text -->
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;font-weight:700;color:var(--text);margin-bottom:3px">
              <span id="ver-count" style="color:${_clrHex}">${d.verified}</span>
              <span id="ver-total" style="color:var(--text3);font-weight:400"> / ${d.total}</span>
              &nbsp;relations confirmed against source text
            </div>
            <!-- Progress bar -->
            <div style="height:4px;background:var(--bg3);border-radius:2px;margin-bottom:5px;overflow:hidden">
              <div id="ver-bar" style="height:100%;width:${_barW}%;background:${_clrHex};border-radius:2px;transition:width .6s ease"></div>
            </div>
            <div style="font-size:10px;color:var(--text3)">Click to ${d.ver_results?.length?'see triple-level details':'collapse'}</div>
          </div>

          <span id="ver-chevron" style="color:var(--text3);font-size:11px;flex-shrink:0">▼</span>
        </div>

        <div class="ver-body" style="display:none;margin-top:14px">
          ${rows?`<table class="rtable"><thead><tr>
            <th style="width:28px"></th>
            <th>Subject</th><th>Relation</th><th>Object</th><th>Evidence</th>
          </tr></thead><tbody>${rows}</tbody></table>`:'<div style="color:var(--text3);font-size:12px;padding:8px 0">No triple-level details available.</div>'}
        </div>
      </div>`;
  }

  document.getElementById('output').scrollIntoView({ behavior:'smooth', block:'start' });
}

function toggleVerification(el) {
  const body = el.querySelector('.ver-body');
  const chev = el.querySelector('#ver-chevron');
  const isCollapsed = body.style.display === 'none';
  body.style.display = isCollapsed ? 'block' : 'none';
  if (chev) chev.textContent = isCollapsed ? '▲' : '▼';
  el.querySelector('[title]') && el.setAttribute('title', isCollapsed ? 'Click to collapse' : 'Click to expand');
}

function onCardClick(i) {
  const c = window._outCards[i];
  if (!c) return;
  document.querySelectorAll('.out-card').forEach(el => el.classList.remove('active'));
  document.getElementById('oc-'+i).classList.add('active');

  if (c.inline) {
    closeViewer();
    document.getElementById('ver-content').scrollIntoView({ behavior:'smooth', block:'start' });
    return;
  }
  if (c.manageTriples) {
    closeViewer();
    openTriplesPanel(c.stagingDb, c.runDir, 'Paper Triples');
    return;
  }
  if (c.src) openViewer(c.src, c.title);
}

