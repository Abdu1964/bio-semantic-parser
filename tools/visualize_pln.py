"""pln_graph.html from Layer 9 PLN results (kept apart from the reasoning code)."""
from pathlib import Path


def generate_pln_html(pln_result: dict, out_path: Path) -> Path:
    """PLN graph with tables: extracted triples solid by confidence, inferred dashed purple,
    contradiction losers dashed red."""
    import json as _json
    from datetime import datetime, timezone

    derived        = pln_result.get("derived", [])
    contradictions = pln_result.get("contradictions", [])
    stv_all        = pln_result.get("stv_all", [])
    revised        = pln_result.get("revised", 0)

    # ── Build vis.js nodes and edges ─────────────────────────────────────────
    nodes_map: dict = {}
    vis_edges: list = []
    edge_id = 0

    def _node(nid: str, ntype: str = "") -> None:
        if nid and nid not in nodes_map:
            short = nid.split(":")[-1][:18] if ":" in nid else nid[:18]
            color = "#a855f7" if not ntype else {
                "gene": "#58a6ff", "protein": "#3fb950", "disease": "#f85149",
                "small_molecule": "#d29922", "genomic_variant": "#e3b341",
                "cell_type": "#79c0ff", "phenotype": "#f0883e",
            }.get(ntype.lower(), "#8b949e")
            nodes_map[nid] = {"id": nid, "label": short, "title": nid,
                               "color": {"background": color, "border": color,
                                         "highlight": {"background": "#ffffff", "border": color}},
                               "font": {"color": "#e6edf3", "size": 11},
                               "size": 18, "borderWidth": 2}

    # Extracted triples — store full metadata for click panel
    edge_data: dict = {}   # edge_id → full triple data for JS click handler
    for t in stv_all:
        sid, oid = t.get("subject_id",""), t.get("object_id","")
        s = t.get("stv_strength", 0.5)
        c = t.get("stv_confidence", 0.5)
        _node(sid, t.get("subject_type",""))
        _node(oid, t.get("object_type",""))
        if sid and oid:
            edge_color = "#238636" if c >= 0.75 else ("#d29922" if c >= 0.55 else "#8b949e")
            vis_edges.append({"id": edge_id, "from": sid, "to": oid,
                               "label": f"{t.get('relation','').replace('_',' ')}\n(stv {s:.2f} {c:.2f})",
                               "color": {"color": edge_color, "highlight": edge_color},
                               "font": {"size": 9, "color": "#8b949e", "align": "middle"},
                               "arrows": "to", "width": max(1, int(c * 3)),
                               "_type": "extracted"})
            edge_data[edge_id] = {
                "type": "extracted",
                "subject_id":   sid,
                "subject_name": t.get("subject_name", sid),
                "subject_type": t.get("subject_type",""),
                "relation":     t.get("relation",""),
                "object_id":    oid,
                "object_name":  t.get("object_name", oid),
                "object_type":  t.get("object_type",""),
                "negated":      t.get("negated", False),
                "stv":          f"(stv {s:.2f} {c:.2f})",
                "reasoning":    t.get("reasoning",""),
                "sources":      t.get("source_papers",[]),
                "species":      t.get("species",""),
                "tissue":       t.get("tissue",""),
                "condition":    t.get("condition",""),
                "effect_size":  t.get("effect_size",""),
            }
            edge_id += 1

    # Inferred triples — dashed purple
    for t in derived:
        sid, oid = t.get("subject_id",""), t.get("object_id","")
        s = t.get("stv_strength", 0.5)
        c = t.get("stv_confidence", 0.5)
        chain = t.get("derived_from","")
        _node(sid, t.get("subject_type",""))
        _node(oid, t.get("object_type",""))
        if sid and oid:
            vis_edges.append({"id": edge_id, "from": sid, "to": oid,
                               "label": f"inferred\n(stv {s:.2f} {c:.2f})",
                               "color": {"color": "#a855f7", "highlight": "#c084fc"},
                               "font": {"size": 9, "color": "#c084fc", "align": "middle"},
                               "arrows": "to", "dashes": True, "width": 1,
                               "_type": "inferred"})
            edge_data[edge_id] = {
                "type":         "inferred",
                "subject_id":   sid,
                "subject_name": t.get("subject_name", sid),
                "relation":     "inferred_relation",
                "object_id":    oid,
                "object_name":  t.get("object_name", oid),
                "stv":          f"(stv {s:.2f} {c:.2f})",
                "reasoning":    f"PLN forward chaining — derived from: {chain}",
                "chain":        chain,
                "sources":      [],
            }
            edge_id += 1

    # Contradiction losers — red dashed
    for cr in contradictions:
        l = cr.get("loser", {})
        w = cr.get("winner", {})
        sid, oid = l.get("subject_id",""), l.get("object_id","")
        s_neg, c_neg = l.get("negated_stv", (0.5, 0.5))
        _node(sid); _node(oid)
        if sid and oid:
            vis_edges.append({"id": edge_id, "from": sid, "to": oid,
                               "label": f"⚡ {l.get('relation','')}\n(negated)",
                               "color": {"color": "#f85149", "highlight": "#ff7b72"},
                               "font": {"size": 9, "color": "#f85149", "align": "middle"},
                               "arrows": "to", "dashes": [5, 5], "width": 1,
                               "_type": "contradiction"})
            edge_data[edge_id] = {
                "type":     "contradiction",
                "subject_id": sid,
                "relation": l.get("relation",""),
                "object_id": oid,
                "stv":      f"(stv {s_neg:.2f} {c_neg:.2f})",
                "reasoning": f"Contradiction loser — wins: {w.get('relation','')}({w.get('subject_id','')}→{w.get('object_id','')}). "
                             f"PLN Truth_Negation applied — strength inverted, confidence preserved.",
                "sources":  [],
            }
            edge_id += 1

    nodes_json    = _json.dumps(list(nodes_map.values()))
    edges_json    = _json.dumps(vis_edges)
    edge_data_json = _json.dumps(edge_data)

    # ── Build paper title map from checkpoints (same as visualize_graph.py) ──
    _ckpt_root = Path(__file__).resolve().parents[1] / "data" / "checkpoints"
    _title_map: dict = {}
    if _ckpt_root.exists():
        for _mf in _ckpt_root.glob("*/meta.json"):
            try:
                _m = _json.loads(_mf.read_text())
                _did  = _m.get("doc_id", "")
                _src  = _m.get("source_name", "")
                _url  = _m.get("url", "")
                if not _did: continue
                if _src == "pdf":
                    _title_map[_did] = Path(_url).stem if _url else _m.get("paper_title", _did)
                else:
                    _title_map[_did] = _did
            except Exception:
                pass
    # Extend: map co-occurring DOIs to same paper title
    for _e in vis_edges:
        _sp = str(_e.get("_type",""))
        if _sp == "extracted":
            _eid = _e.get("id")
            _ed  = edge_data.get(_eid, {})
            _srcs = _ed.get("sources", [])
            _found = next((_title_map[_i] for _i in _srcs if _i in _title_map), None)
            if _found:
                for _i in _srcs:
                    _title_map.setdefault(_i, _found)
    _title_map_json = _json.dumps(_title_map)

    # Vis.js from lib or CDN
    _vis_path = Path(__file__).resolve().parents[1] / "lib" / "vis-9.1.2" / "vis-network-standalone.min.js"
    vis_script = (f"<script>{_vis_path.read_text(encoding='utf-8')}</script>"
                  if _vis_path.exists()
                  else '<script src="https://unpkg.com/vis-network@9.1.2/standalone/umd/vis-network.min.js"></script>')

    # ── Tables — every row clickable, full derivation chain expandable ────────
    inferred_rows = ""
    for i, t in enumerate(derived):
        sid  = t.get("subject_id","")
        oid  = t.get("object_id","")
        s    = t.get("stv_strength",   0.5)
        c    = t.get("stv_confidence", 0.5)
        chain = t.get("derived_from","")
        # Break chain into steps for display: A→rel1→B + B→rel2→C
        steps = chain.split("→") if chain else []
        steps_html = "".join(
            f"<span style='color:{'#79c0ff' if si%2==0 else '#a855f7'}'>{st.strip()}</span>"
            + ("<span style='color:#8b949e'> → </span>" if si < len(steps)-1 else "")
            for si, st in enumerate(steps)
        ) if steps else chain
        inferred_rows += (
            f"<tr class='clickrow' onclick='rowClick(this,\"{sid}\",\"{oid}\")' "
            f"style='cursor:pointer'>"
            f"<td class='e'>{sid[:28]}</td>"
            f"<td class='r' style='font-size:10px'>inferred</td>"
            f"<td class='e'>{oid[:28]}</td>"
            f"<td><span class='stv'>(stv {s:.2f} {c:.2f})</span></td>"
            f"<td><button class='expand-btn' onclick='event.stopPropagation();toggleChain(this)' "
            f"title='Show full derivation chain'>▶ chain</button>"
            f"<div class='chain-detail' style='display:none;margin-top:8px;padding:8px;"
            f"background:#0d1117;border-radius:6px;border-left:3px solid #a855f7'>"
            f"<div style='color:#8b949e;font-size:10px;margin-bottom:4px'>Derivation chain:</div>"
            f"<div style='font-size:11px;line-height:1.8'>{steps_html}</div>"
            f"<div style='color:#8b949e;font-size:10px;margin-top:6px;font-family:monospace'>{chain}</div>"
            f"</div></td></tr>"
        )
    if not inferred_rows:
        inferred_rows = "<tr><td colspan='5' style='color:#8b949e;text-align:center;padding:16px'>No inferred triples yet</td></tr>"

    stv_rows = ""
    for t in sorted(stv_all, key=lambda x: x.get("stv_confidence",0), reverse=True)[:30]:
        sid = t.get("subject_id","")
        oid = t.get("object_id","")
        s   = t.get("stv_strength",   0.5)
        c   = t.get("stv_confidence", 0.5)
        bar_color = "#238636" if c >= 0.75 else "#d29922" if c >= 0.55 else "#8b949e"
        stv_rows += (
            f"<tr class='clickrow' onclick='rowClick(this,\"{sid}\",\"{oid}\")' style='cursor:pointer'>"
            f"<td class='e'>{sid[:28]}</td>"
            f"<td class='r' style='font-size:10px'>{t.get('relation','')}</td>"
            f"<td class='e'>{oid[:28]}</td>"
            f"<td><span class='stv'>(stv {s:.2f} {c:.2f})</span></td>"
            f"<td><div style='background:{bar_color};height:8px;width:{int(c*80)}px;"
            f"border-radius:4px' title='confidence={c:.2f}'></div></td></tr>"
        )
    if not stv_rows:
        stv_rows = "<tr><td colspan='5' style='color:#8b949e;text-align:center;padding:16px'>No data yet</td></tr>"

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Contradiction rows for drawer
    contra_rows = "".join(
        f"<tr><td class='e' style='color:#238636'>✓ {cr.get('winner',{}).get('relation','')}</td>"
        f"<td class='e'>{cr.get('winner',{}).get('subject_id','')[:20]}</td>"
        f"<td class='e'>{cr.get('winner',{}).get('object_id','')[:20]}</td>"
        f"<td class='e' style='color:#f85149'>✗ {cr.get('loser',{}).get('relation','')}</td>"
        f"<td><span class='stv'>(stv {cr.get('loser',{}).get('negated_stv',(0.5,0.5))[0]:.2f} "
        f"{cr.get('loser',{}).get('negated_stv',(0.5,0.5))[1]:.2f})</span></td></tr>"
        for cr in contradictions
    ) or "<tr><td colspan='5' style='color:#8b949e;text-align:center;padding:12px'>No contradictions</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>PLN AtomSpace — Layer 9</title>
{vis_script}
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}}
  #header{{padding:10px 20px;background:#161b22;border-bottom:1px solid #30363d;display:flex;align-items:center;gap:10px;flex-shrink:0;flex-wrap:wrap}}
  #header h1{{color:#a855f7;font-size:16px;font-weight:700;margin-right:6px}}
  .badge{{cursor:pointer;padding:4px 12px;border-radius:10px;font-size:11px;font-weight:600;border:1px solid;transition:opacity .15s;user-select:none}}
  .badge:hover{{opacity:.8}}
  .badge.active{{box-shadow:0 0 0 2px #a855f7}}
  #body-wrap{{display:flex;flex:1;overflow:hidden}}
  #graph-wrap{{flex:1;position:relative;overflow:hidden}}
  #network{{width:100%;height:100%}}
  /* Side panel — same layout as graph.html */
  #side-panel{{width:340px;background:#161b22;border-left:1px solid #30363d;overflow-y:auto;
               transform:translateX(100%);transition:transform .25s ease;flex-shrink:0}}
  #side-panel.open{{transform:translateX(0)}}
  #panel-header{{display:flex;align-items:center;padding:12px 16px;background:#1a0f2e;
                 border-bottom:1px solid #30363d;position:sticky;top:0;z-index:1}}
  #panel-title{{color:#c084fc;font-size:13px;font-weight:700;flex:1}}
  #panel-close{{cursor:pointer;color:#8b949e;font-size:18px;padding:0 6px;line-height:1}}
  #panel-close:hover{{color:#e6edf3}}
  #panel-content{{padding:14px 16px;font-size:12px}}
  .field-group{{margin-bottom:14px}}
  .field-label{{color:#8b949e;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}}
  .field-value{{color:#c9d1d9;line-height:1.6}}
  /* Drawer still used for badge tables */
  #drawer{{position:absolute;bottom:0;left:0;right:0;background:#161b22ee;border-top:2px solid #6e40c9;
           max-height:0;overflow:hidden;transition:max-height .3s ease;z-index:10}}
  #drawer.open{{max-height:55vh;overflow-y:auto}}
  #drawer-header{{display:flex;align-items:center;padding:10px 16px;background:#1a0f2e;position:sticky;top:0;z-index:1}}
  #drawer-title{{color:#c084fc;font-size:13px;font-weight:700;flex:1}}
  #drawer-close{{cursor:pointer;color:#8b949e;font-size:18px;padding:0 6px;line-height:1}}
  #drawer-close:hover{{color:#e6edf3}}
  #drawer-body{{padding:12px 16px}}
  table{{width:100%;border-collapse:collapse;font-size:11px}}
  th{{color:#8b949e;font-weight:600;text-align:left;padding:5px 8px;border-bottom:1px solid #21262d;position:sticky;top:0;background:#161b22}}
  td{{padding:5px 8px;border-bottom:1px solid #0d1117;vertical-align:middle}}
  tr:hover td{{background:#0d1117}}
  .e{{color:#79c0ff;font-family:'Courier New',monospace;font-size:10px}}
  .r{{color:#a855f7}}
  .ch{{color:#8b949e;font-size:10px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .stv{{background:#1a0f2e;color:#c084fc;padding:1px 6px;border-radius:8px;font-family:'Courier New',monospace;font-size:10px}}
  /* Legend */
  #legend{{position:absolute;bottom:16px;left:16px;background:#161b22dd;border:1px solid #30363d;border-radius:8px;padding:8px 12px;font-size:11px;z-index:5}}
  .leg-item{{display:flex;align-items:center;gap:8px;margin:3px 0;cursor:pointer;padding:2px 4px;border-radius:4px;transition:background .15s}}
  .leg-item:hover{{background:#21262d}}
  .leg-item.active{{background:#21262d;outline:1px solid #6e40c9}}
  .leg-label{{color:#8b949e}}
  .leg-line{{height:2px;width:22px;border-radius:1px;flex-shrink:0}}
  .leg-dash{{height:0;width:22px;border-top:2px dashed;flex-shrink:0}}
  .clickrow:hover td{{background:#21262d !important;cursor:pointer}}
  .clickrow.selected td{{background:#1a0f2e !important;border-left:2px solid #a855f7}}
  .expand-btn{{background:#1a0f2e;color:#c084fc;border:1px solid #6e40c944;padding:2px 8px;
               border-radius:6px;font-size:10px;cursor:pointer;transition:background .15s}}
  .expand-btn:hover{{background:#2d1b4e}}
  .chain-detail{{color:#c9d1d9;line-height:1.6}}
  .stv-expand{{background:#0d1117;border-radius:6px;padding:14px 16px;border-left:3px solid #a855f7;
              margin:10px 0;grid-column:1 / -1;word-break:normal;white-space:normal;line-height:1.6}}
</style>
</head>
<body>
<div id="header">
  <h1>🔮 PLN Layer 9 — Bio-AtomSpace</h1>
  <button class="badge" style="color:#79c0ff;border-color:#79c0ff44;background:#79c0ff11"
          onclick="openDrawer('extracted')">{len(stv_all)} extracted</button>
  <button class="badge" style="color:#a855f7;border-color:#a855f744;background:#a855f711"
          onclick="openDrawer('inferred')">{len(derived)} inferred</button>
  <button class="badge" style="color:#f85149;border-color:#f8514944;background:#f8514911"
          onclick="openDrawer('contradictions')">{len(contradictions)} contradictions</button>
  <button class="badge" style="color:#238636;border-color:#23863644;background:#23863611"
          onclick="openDrawer('revised')">{revised} revised</button>
  <!-- Inferred toggle button -->
  <button id="inferred-toggle" class="badge" style="color:#a855f7;border-color:#a855f7;background:#a855f711;margin-left:8px"
          onclick="toggleInferred(this)">⛓ Hide inferred</button>
  <!-- Paper filter dropdown -->
  <select id="paper-filter" onchange="filterByPaperPLN(this.value)"
    style="background:#161b22;border:1px solid #30363d;color:#e6edf3;border-radius:8px;
           padding:4px 10px;font-size:11px;cursor:pointer;margin-left:8px;font-family:inherit">
    <option value="">📄 All papers</option>
  </select>
  <span style="margin-left:auto;color:#8b949e;font-size:11px">{ts}</span>
</div>

<div id="body-wrap">
<div id="graph-wrap">
  <div id="network"></div>

  <!-- Legend — clickable to filter graph -->
  <div id="legend">
    <div class="leg-item active" data-filter="all" onclick="filterGraph('all',this)">
      <div class="leg-line" style="background:#6e40c9"></div><span class="leg-label">All edges</span>
    </div>
    <div class="leg-item" data-filter="extracted" onclick="filterGraph('extracted',this)">
      <div class="leg-line" style="background:#238636"></div><span class="leg-label">High confidence</span>
    </div>
    <div class="leg-item" data-filter="low" onclick="filterGraph('low',this)">
      <div class="leg-line" style="background:#8b949e"></div><span class="leg-label">Low confidence</span>
    </div>
    <div class="leg-item" data-filter="inferred" onclick="filterGraph('inferred',this)">
      <div class="leg-dash" style="border-color:#a855f7"></div><span class="leg-label">PLN inferred</span>
    </div>
    <div class="leg-item" data-filter="contradiction" onclick="filterGraph('contradiction',this)">
      <div class="leg-dash" style="border-color:#f85149"></div><span class="leg-label">Contradiction loser</span>
    </div>
  </div>

  <!-- Drawer — badge table popups -->
  <div id="drawer">
    <div id="drawer-header">
      <span id="drawer-title"></span>
      <span id="drawer-close" onclick="closeDrawer()">✕</span>
    </div>
    <div id="drawer-body"></div>
  </div>
</div>

<!-- Side panel — node/edge detail (like graph.html) -->
<div id="side-panel">
  <div id="panel-header">
    <span id="panel-title">Click a node or edge</span>
    <span id="panel-close" onclick="hidePanel()">✕</span>
  </div>
  <div id="panel-content">
    <div style="color:#8b949e;font-size:12px;padding:20px;text-align:center">
      Click any node or edge on the graph to see its details here.
    </div>
  </div>
</div>
</div>

<script>
// Helper: build stv detail HTML — shows FULL calculation trace with real numbers
function buildStvDetail(d) {{
  var stvMatch = (d.stv||'').match(/stv ([0-9.]+) ([0-9.]+)/);
  var s = stvMatch ? parseFloat(stvMatch[1]) : 0.5;
  var c = stvMatch ? parseFloat(stvMatch[2]) : 0.5;
  var N = Math.round(c / (1 - c + 0.0001));
  var barColor = c>=0.75?'#238636':c>=0.55?'#d29922':'#8b949e';
  var isInferred = d.type === 'inferred';
  var sources = d.sources || [];

  // Strength block
  var strengthHtml =
    '<div style="margin-bottom:10px;padding:10px 12px;background:#161b22;border-radius:8px;border-left:3px solid #a855f7">' +
      '<div style="color:#a855f7;font-weight:700;margin-bottom:6px;font-size:12px">strength = ' + s.toFixed(2) + '</div>' +
      '<div style="color:#c9d1d9;line-height:1.6;font-size:11px">' +
        (isInferred
          ? 'Computed by Truth_Deduction from premise strengths.'
          : 'Starts at <b>0.5</b> (maximally uncertain prior — PLN standard).<br>' +
            'Updates via Truth_Revision when verify_kg returns evidence:<br>' +
            '<code style="color:#79c0ff;font-size:10px">CONFIRMED → (stv 1.0 0.5) → strength rises toward 1.0</code><br>' +
            '<code style="color:#f85149;font-size:10px">MISMATCH  → (stv 0.0 0.5) → strength falls toward 0.0</code>') +
      '</div>' +
      '<div style="color:#484f58;margin-top:6px;font-size:10px;font-family:monospace">Formula: Truth_Revision (PLN Book p.116)</div>' +
    '</div>';

  // Confidence block — full calculation trace
  var sourcesListHtml = '';
  if (sources.length > 0) {{
    sourcesListHtml = '<div style="margin:6px 0 4px;color:#8b949e;font-size:10px">Source papers (N=' + N + '):</div>';
    sources.forEach(function(src, i) {{
      sourcesListHtml += '<div style="color:#79c0ff;font-family:monospace;font-size:10px;padding:1px 0">' +
        (i+1) + '. ' + src + '</div>';
    }});
  }} else {{
    sourcesListHtml = '<div style="color:#8b949e;font-size:10px;margin:4px 0">N = ' + N + ' paper(s)</div>';
  }}

  var calcTrace = isInferred
    ? '<div style="color:#c9d1d9;font-size:11px">c = min(c_premise1, c_premise2, ...) via Truth_Deduction</div>'
    : sourcesListHtml +
      '<div style="background:#0d1117;border-radius:6px;padding:8px 10px;margin:8px 0;font-family:monospace;font-size:11px">' +
        '<div style="color:#8b949e;margin-bottom:4px">Calculation (Truth_w2c formula):</div>' +
        '<div style="color:#c9d1d9">c = N / (N + 1)</div>' +
        '<div style="color:#c9d1d9">c = <span style="color:#a855f7">' + N + '</span> / (<span style="color:#a855f7">' + N + '</span> + 1)</div>' +
        '<div style="color:' + barColor + ';font-weight:700">c = ' + c.toFixed(4) + ' ≈ ' + c.toFixed(2) + '</div>' +
      '</div>';

  var confidenceHtml =
    '<div style="padding:10px 12px;background:#161b22;border-radius:8px;border-left:3px solid ' + barColor + '">' +
      '<div style="color:' + barColor + ';font-weight:700;margin-bottom:6px;font-size:12px">confidence = ' + c.toFixed(2) + '</div>' +
      calcTrace +
      '<div style="background:#21262d;border-radius:3px;height:6px;margin:8px 0;width:100%">' +
        '<div style="background:' + barColor + ';height:6px;border-radius:3px;width:' + Math.round(c*100) + '%"></div>' +
      '</div>' +
      '<div style="display:flex;justify-content:space-between;color:#484f58;font-size:10px">' +
        '<span>0 (no evidence)</span><span>0.5 (N=1)</span><span>0.67 (N=2)</span><span>0.75 (N=3)</span><span>1.0 (∞)</span>' +
      '</div>' +
      '<div style="color:#484f58;margin-top:6px;font-size:10px;font-family:monospace">Source: Truth_w2c · trueagi-io/PeTTa lib_pln.metta</div>' +
    '</div>';

  return '<div style="font-size:11px">' +
    '<div style="color:#8b949e;font-size:10px;font-weight:700;letter-spacing:.06em;margin-bottom:10px;text-transform:uppercase">Full Calculation Trace</div>' +
    strengthHtml + confidenceHtml +
  '</div>';
}}

// Toggle stv detail panel
function showStvDetail(el) {{
  var detail = el.nextElementSibling;
  if (detail) {{
    var isHidden = detail.style.display === 'none' || !detail.style.display;
    detail.style.display = isHidden ? 'block' : 'none';
    el.querySelector('span').textContent = isHidden ? '▼ how?' : '▶ how?';
  }}
}}

const NODES = new vis.DataSet({nodes_json});
const EDGES = new vis.DataSet({edges_json});
const ALL_EDGES = {edges_json};
const EDGE_DATA = {edge_data_json};
const TITLE_MAP = {_title_map_json};

// ── Paper label helper ────────────────────────────────────────────────────────
function paperLabel(p) {{
  if (TITLE_MAP[p]) return TITLE_MAP[p];
  if (/^[0-9a-f]{{64}}$/.test(p)) return 'PDF · ' + p.slice(0,12) + '…';
  return p.length > 35 ? p.slice(0,33)+'…' : p;
}}

// ── Populate paper filter dropdown ────────────────────────────────────────────
(function() {{
  const seen = new Set(); const labelToId = new Map();
  ALL_EDGES.forEach(e => {{
    if (e._type !== 'extracted') return;
    var d = EDGE_DATA[e.id];
    if (!d || !d.sources) return;
    d.sources.forEach(s => {{
      var label = paperLabel(s);
      if (!labelToId.has(label)) labelToId.set(label, s);
    }});
  }});
  var sel = document.getElementById('paper-filter');
  labelToId.forEach(function(id, label) {{
    var opt = document.createElement('option');
    opt.value = label; opt.textContent = '📄 ' + label;
    sel.appendChild(opt);
  }});
}})();

// ── Filter graph by selected paper ───────────────────────────────────────────
function filterByPaperPLN(label) {{
  if (!label) {{
    // Show all
    EDGES.update(ALL_EDGES.map(e=>Object.assign({{}},e,{{hidden:false}})));
    NODES.update(NODES.get().map(n=>Object.assign({{}},n,{{hidden:false}})));
    return;
  }}
  // Find edges that belong to this paper
  var keepIds = new Set();
  var keepNodes = new Set();
  ALL_EDGES.forEach(function(e) {{
    var d = EDGE_DATA[e.id];
    var match = false;
    if (e._type === 'extracted' && d && d.sources) {{
      match = d.sources.some(s => paperLabel(s) === label);
    }}
    if (match) {{
      keepIds.add(e.id);
      keepNodes.add(e.from); keepNodes.add(e.to);
    }}
  }});
  // Also show inferred triples connected to matching nodes
  ALL_EDGES.forEach(function(e) {{
    if (e._type === 'inferred' && (keepNodes.has(e.from) || keepNodes.has(e.to))) {{
      keepIds.add(e.id);
      keepNodes.add(e.from); keepNodes.add(e.to);
    }}
  }});
  EDGES.update(ALL_EDGES.map(e=>Object.assign({{}},e,{{hidden:!keepIds.has(e.id)}})));
  NODES.update(NODES.get().map(n=>Object.assign({{}},n,{{hidden:!keepNodes.has(n.id)}})));
  network.fit({{animation:{{duration:500,easingFunction:'easeInOutQuad'}}}});
}}

const container = document.getElementById('network');
const network = new vis.Network(container, {{nodes:NODES, edges:EDGES}}, {{
  physics: {{stabilization:{{iterations:150}}, barnesHut:{{gravitationalConstant:-9000,springLength:130}}}},
  edges: {{smooth:{{type:'dynamic'}}}},
  nodes: {{shape:'dot', shadow:true}},
  interaction: {{tooltipDelay:150, hover:true, navigationButtons:true}},
  configure: false
}});

// ── Legend filter ─────────────────────────────────────────────────────────────
function filterGraph(type, el) {{
  document.querySelectorAll('.leg-item').forEach(i=>i.classList.remove('active'));
  el.classList.add('active');
  if (type === 'all') {{
    EDGES.update(ALL_EDGES.map(e=>Object.assign({{}},e,{{hidden:false}})));
    NODES.update(NODES.get().map(n=>Object.assign({{}},n,{{hidden:false}})));
    return;
  }}
  const keep = ALL_EDGES.filter(e=>{{
    if (type==='inferred')     return e.dashes===true;
    if (type==='contradiction') return Array.isArray(e.dashes);
    if (type==='extracted')    return !e.dashes && e.color && e.color.color==='#238636';
    if (type==='low')          return !e.dashes && e.color && e.color.color!=='#238636';
    return true;
  }});
  const keepIds = new Set(keep.map(e=>e.id));
  const keepNodes = new Set();
  keep.forEach(e=>{{keepNodes.add(e.from);keepNodes.add(e.to);}});
  EDGES.update(ALL_EDGES.map(e=>Object.assign({{}},e,{{hidden:!keepIds.has(e.id)}})));
  NODES.update(NODES.get().map(n=>Object.assign({{}},n,{{hidden:!keepNodes.has(n.id)}})));
}}

// ── Inferred toggle ───────────────────────────────────────────────────────────
var inferredVisible = true;
function toggleInferred(btn) {{
  inferredVisible = !inferredVisible;
  btn.textContent = inferredVisible ? '⛓ Hide inferred' : '⛓ Show inferred';
  btn.style.opacity = inferredVisible ? '1' : '0.5';
  EDGES.update(ALL_EDGES.filter(e=>e.dashes===true).map(e=>Object.assign({{}},e,{{hidden:!inferredVisible}})));
  // Re-hide nodes that only had inferred connections if hiding
  if (!inferredVisible) {{
    const visEdges = ALL_EDGES.filter(e=>e.dashes!==true);
    const visNodes = new Set();
    visEdges.forEach(e=>{{visNodes.add(e.from);visNodes.add(e.to);}});
    NODES.update(NODES.get().map(n=>Object.assign({{}},n,{{hidden:!visNodes.has(n.id)}})));
  }} else {{
    NODES.update(NODES.get().map(n=>Object.assign({{}},n,{{hidden:false}})));
  }}
}}

// ── Side panel (node/edge detail) ─────────────────────────────────────────────
function hidePanel() {{
  document.getElementById('side-panel').classList.remove('open');
}}
function showPanel(title, html) {{
  document.getElementById('panel-title').textContent = title;
  document.getElementById('panel-content').innerHTML = html;
  document.getElementById('side-panel').classList.add('open');
}}

// ── Badge drawer ──────────────────────────────────────────────────────────────
const TABLES = {{
  extracted: {{
    title: '📐 Extracted Triples with SimpleTruthValue (top 20 by confidence)',
    html: `<table><tr><th>Subject</th><th>Relation</th><th>Object</th><th>stv</th><th>Confidence</th></tr>{stv_rows}</table>`
  }},
  inferred: {{
    title: '⛓ Inferred Triples — PLN forward chaining A→B + B→C → A→C',
    html: `<table><tr><th>Subject</th><th>→</th><th>Object</th><th>stv</th><th>Derivation chain</th></tr>{inferred_rows}</table>`
  }},
  contradictions: {{
    title: '⚡ Contradiction Resolution — Truth_Negation applied to weaker triple',
    html: `<table><tr><th>Winner relation</th><th>Subject</th><th>Object</th><th>Loser relation</th><th>Loser stv (negated)</th></tr>{contra_rows}</table>`
  }},
  revised: {{
    title: '🔄 PLN Revision — stv updated from verify_kg evidence',
    html: `<p style="color:#8b949e;padding:20px;text-align:center">
      {revised} triple(s) revised.<br>
      Run verify_kg.py --fetch then re-commit to see revision results here.<br>
      Formula: Truth_Revision (PLN Book p.116)
    </p>`
  }}
}};
let drawerOpen = false;
function openDrawer(type) {{
  const d = document.getElementById('drawer');
  const t = TABLES[type];
  if (t) {{
    document.getElementById('drawer-title').textContent = t.title;
    document.getElementById('drawer-body').innerHTML = t.html;
    d.classList.add('open');
    drawerOpen = true;
    document.querySelectorAll('.badge').forEach(b=>b.classList.remove('active'));
    event.currentTarget.classList.add('active');
  }}
}}
function closeDrawer() {{
  document.getElementById('drawer').classList.remove('open');
  document.querySelectorAll('.badge').forEach(b=>b.classList.remove('active'));
  drawerOpen = false;
}}
// ── Row click — highlight node pair in graph ─────────────────────────────────
function rowClick(row, sid, oid) {{
  document.querySelectorAll('.clickrow').forEach(r=>r.classList.remove('selected'));
  row.classList.add('selected');
  // Find matching nodes and focus graph on them
  const matchNodes = NODES.get().filter(n=>n.id===sid||n.id===oid).map(n=>n.id);
  if (matchNodes.length) network.selectNodes(matchNodes);
  const matchEdges = ALL_EDGES.filter(e=>
    (e.from===sid&&e.to===oid)||(e.from===oid&&e.to===sid)
  ).map(e=>e.id);
  if (matchEdges.length) network.selectEdges(matchEdges);
  network.fit({{nodes:matchNodes, animation:{{duration:500,easingFunction:'easeInOutQuad'}}}});
}}

// ── Toggle derivation chain detail ───────────────────────────────────────────
function toggleChain(btn) {{
  const detail = btn.nextElementSibling;
  const isOpen = detail.style.display !== 'none';
  detail.style.display = isOpen ? 'none' : 'block';
  btn.textContent = isOpen ? '▶ chain' : '▼ chain';
  btn.style.background = isOpen ? '' : '#2d1b4e';
}}

// ── Graph click — show detail in SIDE PANEL (like graph.html) ───────────────
network.on('click', function(params) {{
  if (params.nodes.length) {{
    const nid = params.nodes[0];
    const outEdges = ALL_EDGES.filter(e=>e.from===nid);
    const inEdges  = ALL_EDGES.filter(e=>e.to===nid);
    var html = '<div class="field-group">' +
      '<div class="field-label">Canonical ID</div>' +
      '<div class="field-value"><code style="color:#79c0ff;font-size:12px">' + nid + '</code></div></div>';
    if (outEdges.length) {{
      html += '<div class="field-group"><div class="field-label">Outgoing (' + outEdges.length + ')</div>';
      outEdges.forEach(function(e) {{
        var d = EDGE_DATA[e.id];
        var stv = d ? '<span class="stv">' + (d.stv||'') + '</span>' : '';
        html += '<div style="padding:6px 0;border-bottom:1px solid #21262d;cursor:pointer" onclick="highlightEdge(' + e.id + ')">' +
          '<span style="color:#a855f7">' + (e.label||'').split('\\n')[0] + '</span> → ' +
          '<code style="color:#79c0ff;font-size:10px">' + e.to + '</code> ' + stv + '</div>';
      }});
      html += '</div>';
    }}
    if (inEdges.length) {{
      html += '<div class="field-group"><div class="field-label">Incoming (' + inEdges.length + ')</div>';
      inEdges.forEach(function(e) {{
        var d = EDGE_DATA[e.id];
        var stv = d ? '<span class="stv">' + (d.stv||'') + '</span>' : '';
        html += '<div style="padding:6px 0;border-bottom:1px solid #21262d;cursor:pointer" onclick="highlightEdge(' + e.id + ')">' +
          '<code style="color:#79c0ff;font-size:10px">' + e.from + '</code> → ' +
          '<span style="color:#a855f7">' + (e.label||'').split('\\n')[0] + '</span> ' + stv + '</div>';
      }});
      html += '</div>';
    }}
    showPanel('🔵 ' + nid.split(':').pop(), html);
  }} else if (params.edges.length) {{
    const eid = params.edges[0];
    const d = EDGE_DATA[eid];
    if (d) {{
      const typeIcon = d.type==='inferred' ? '⛓ PLN Inferred' : d.type==='contradiction' ? '⚡ Contradiction Loser' : '📐 Extracted';
      const typeColor = d.type==='inferred' ? '#a855f7' : d.type==='contradiction' ? '#f85149' : '#238636';
      const negatedBadge = d.negated ? '<span style="background:#f8514922;color:#f85149;border:1px solid #f8514944;padding:1px 8px;border-radius:8px;font-size:10px;margin-left:8px">NEGATED</span>' : '';
      const sourcesHtml = d.sources && d.sources.length
        ? '<div style="color:#8b949e;font-size:11px;margin-top:10px;margin-bottom:4px">Source paper(s)</div>' +
          d.sources.map(s=>'<code style="color:#79c0ff;font-size:10px;background:#0d1117;padding:2px 6px;border-radius:4px;display:inline-block;margin:2px">'+s+'</code>').join(' ')
        : '';
      const metaHtml = [
        d.species    ? '<b>Species:</b> ' + d.species    : '',
        d.tissue     ? '<b>Tissue:</b> '  + d.tissue     : '',
        d.condition  ? '<b>Condition:</b> '+ d.condition : '',
        d.effect_size? '<b>Effect:</b> '  + d.effect_size: '',
      ].filter(Boolean).join(' &nbsp;·&nbsp; ');
      const chainHtml = d.chain
        ? '<div style="color:#8b949e;font-size:11px;margin-top:10px;margin-bottom:4px">Derivation chain</div>' +
          '<div style="color:#c084fc;font-size:11px;background:#0d1117;padding:8px;border-radius:6px;line-height:1.8;font-family:monospace">' + d.chain + '</div>'
        : '';

      const edgeHtml =
        '<div style="padding:14px 16px">' +
        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">' +
          '<span style="background:' + typeColor + '22;color:' + typeColor + ';border:1px solid ' + typeColor + '44;padding:2px 10px;border-radius:8px;font-size:11px">' + d.type.toUpperCase() + '</span>' +
          '<span style="color:#a855f7;font-size:13px;font-weight:600">' + d.relation.replace(/_/g,' ') + '</span>' +
          negatedBadge +
        '</div>' +
        '<div style="display:grid;grid-template-columns:80px 1fr;gap:6px 10px;font-size:12px;margin-bottom:12px">' +
          '<span style="color:#8b949e">Subject</span>' +
          '<span><code style="color:#79c0ff">' + (d.subject_id||'') + '</code>' +
          (d.subject_name ? ' <span style="color:#8b949e">(' + d.subject_name + ')</span>' : '') + '</span>' +
          '<span style="color:#8b949e">Object</span>' +
          '<span><code style="color:#79c0ff">' + (d.object_id||'') + '</code>' +
          (d.object_name  ? ' <span style="color:#8b949e">(' + d.object_name  + ')</span>' : '') + '</span>' +
          '<span style="color:#8b949e">PLN stv</span>' +
          '<span style="color:#c084fc;font-family:monospace;cursor:pointer" onclick="showStvDetail(this)">' +
            (d.stv||'') + ' <span style="color:#6e40c9;font-size:10px">▶ how?</span>' +
          '</span>' +
          '<div class="stv-expand" style="display:none">' + buildStvDetail(d) + '</div>' +
        '</div>' +
        (d.reasoning ? '<div style="color:#8b949e;font-size:11px;margin-bottom:4px">LLM reasoning</div>' +
          '<div style="color:#c9d1d9;font-size:12px;background:#0d1117;padding:10px;border-radius:6px;line-height:1.6;border-left:3px solid #a855f7">' + d.reasoning + '</div>' : '') +
        chainHtml + sourcesHtml +
        (metaHtml ? '<div style="color:#8b949e;font-size:11px;margin-top:10px">' + metaHtml + '</div>' : '') +
        '</div>';
      showPanel(typeIcon, edgeHtml);
    }}
  }}
}});

function highlightEdge(eid) {{
  network.selectEdges([eid]);
  var e = ALL_EDGES.find(function(x){{return x.id===eid;}});
  if (e) {{
    var d = EDGE_DATA[eid];
    if (d) network.emit('click', {{edges:[eid], nodes:[]}});
  }}
}}
</script>
</body>
</html>"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
