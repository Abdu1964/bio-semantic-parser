"""pln_graph.html from PeTTaChainer atoms, in the style of tools/visualize_graph.py:
original triples solid, coloured by strength; inferred ones dashed purple."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path


# ── Atom regex ─────────────────────────────────────────────────────────────────
_ATOM_RE = re.compile(
    r'^\(:\s+(\S+)\s+\((\w+)\s+(.*?)\)\s+\(STV\s+([\d.]+)\s+([\d.]+)\)\s*\)\s*$'
)
_PC_ATOM_RE = re.compile(
    r'^\(\((\w+)\s+(.*?)\)\s+\([a-zA-Z0-9]+\s+MAIN\s+Nil\)\s+(\S+)\s+\(STV\s+([\d.]+)\s+([\d.]+)\)\)\s*$'
)
_ENTITY_RE = re.compile(r'^\((\w+)\s+(.+?)\)$')


def _pascal_to_label(name: str) -> str:
    return re.sub(r'(?<=[a-z])(?=[A-Z])', '_', name).replace('__', '_').lower().replace('_', ' ')


def _entity_parts(expr: str) -> tuple[str, str]:
    m = _ENTITY_RE.match(expr.strip())
    if m:
        return m.group(1).lower(), m.group(2).strip()
    return '', expr.strip()


def _node_color(etype: str) -> str:
    return {
        'gene': '#4F9CF9', 'protein': '#6366F1', 'transcript': '#818CF8',
        'rna': '#A78BFA', 'genomic_variant': '#C084FC',
        'small_molecule': '#34D399', 'drug': '#10B981',
        'disease': '#F87171', 'cancer': '#EF4444',
        'phenotype': '#FBBF24', 'symptom': '#F59E0B',
        'pathway': '#E879F9', 'biological_process': '#D946EF',
        'molecular_function': '#A855F7', 'cellular_component': '#7C3AED',
        'tissue': '#2DD4BF', 'cell_type': '#14B8A6', 'cell_line': '#0D9488',
        'organism': '#FB7185', 'enhancer': '#F472B6',
        'food': '#82e0aa', 'device': '#85c1e9',
        'physiological_process': '#a29bfe',
        'clinical_finding': '#f1948a', 'clinical_intervention': '#a9cce3',
    }.get(etype, '#64748B')


def _split_top(s: str) -> list[str]:
    parts, depth, buf = [], 0, []
    for ch in s:
        if ch == '(':
            depth += 1; buf.append(ch)
        elif ch == ')':
            depth -= 1; buf.append(ch)
        elif ch == ' ' and depth == 0:
            t = ''.join(buf).strip()
            if t: parts.append(t)
            buf = []
        else:
            buf.append(ch)
    t = ''.join(buf).strip()
    if t: parts.append(t)
    return parts


def _parse_one_atom(a: str) -> dict | None:
    a = a.strip()
    m = _ATOM_RE.match(a)
    if m:
        name, pred, args_str, s, c = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
    else:
        m = _PC_ATOM_RE.match(a)
        if not m:
            return None
        pred, args_str, name, s, c = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
    args = _split_top(args_str)
    if len(args) < 2:
        return None
    subj_type, subj_id = _entity_parts(args[0])
    obj_type,  obj_id  = _entity_parts(args[1])
    return {
        'name': name, 'predicate': pred,
        'label': _pascal_to_label(pred),
        'subject_type': subj_type, 'subject_id': subj_id,
        'object_type':  obj_type,  'object_id':  obj_id,
        'strength': float(s), 'confidence': float(c),
        'raw': a,
    }


def _parse_atoms(atom_strings: list[str]) -> list[dict]:
    parsed = []
    for a in atom_strings:
        t = _parse_one_atom(a)
        if t:
            parsed.append(t)
    return parsed


# ── Main visualizer ─────────────────────────────────────────────────────────────

def generate_pln_html(
    original_atoms: list[str],
    all_atoms: list[str],
    out_path: str | Path,
    paper_id: str = '',
    atom_meta: dict | None = None,
    label_map: dict | None = None,
    entities: dict | None = None,
) -> Path:
    """entities: entity symbol → {'name', 'type'}, for atoms whose entities are bare symbols."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    id_to_label: dict[str, str] = {}
    if label_map:
        for lbl, (_, eid_val) in label_map.items():
            if eid_val not in id_to_label:
                id_to_label[eid_val] = lbl.title()

    def _display(entity_id: str) -> str:
        lbl = id_to_label.get(entity_id, '')
        return lbl[:28] if lbl else entity_id[:24]

    original = _parse_atoms(original_atoms)
    original_sem = {(t['predicate'].lower(), t['subject_id'], t['object_id']) for t in original}
    all_parsed = _parse_atoms(all_atoms)
    inferred = [
        t for t in all_parsed
        if (t['predicate'].lower(), t['subject_id'], t['object_id']) not in original_sem
        and 'Implication' not in t.get('predicate', '')
    ]

    n_backward = sum(1 for t in inferred if (atom_meta or {}).get(t['name'], {}).get('_via') == 'backward')
    n_forward = len(inferred) - n_backward
    nodes_map: dict = {}
    vis_edges: list = []
    eid = 0

    def _node(nid: str, ntype: str) -> None:
        if nid and nid not in nodes_map:
            known = (entities or {}).get(nid, {})
            ntype = ntype or known.get('type', '')
            human = known.get('name') or id_to_label.get(nid, '')
            label = (human[:18] if human else
                     (nid.split(':')[-1][:18] if ':' in nid else nid[:18]))
            color = _node_color(ntype)
            nodes_map[nid] = {
                'id': nid, 'label': label,
                'title': f'{human or nid} ({ntype}) · {nid}',
                'color': {'background': color, 'border': color,
                          'highlight': {'background': '#FFFFFF', 'border': color},
                          'hover': {'background': '#FFFFFF', 'border': color}},
                'size': 14,
                'font': {'color': '#ffffff', 'size': 12, 'face': 'Inter, sans-serif',
                         'strokeWidth': 3, 'strokeColor': '#00000066'},
                'shadow': {'enabled': True, 'color': color + '88', 'size': 16, 'x': 0, 'y': 0},
                'borderWidth': 2, 'borderWidthSelected': 3,
                '_name': human or nid,
                '_type': ntype,
                '_props': {'name': human or nid, 'id': nid, 'id_source': ntype.upper()},
            }

    for t in original:
        sid, oid = t['subject_id'], t['object_id']
        _node(sid, t['subject_type'])
        _node(oid, t['object_type'])
        if sid and oid:
            s, c = t['strength'], t['confidence']
            ec = '#238636' if c >= 0.75 else ('#d29922' if c >= 0.55 else '#8b949e')
            meta = (atom_meta or {}).get(t['name'], {})
            vis_edges.append({
                'id': eid, 'from': sid, 'to': oid,
                'label': t['label'],
                'title': f"{t['label']} · stv({s:.2f}, {c:.2f})",
                'color': {'color': ec + 'cc', 'highlight': '#ffffff', 'hover': '#ffffff', 'opacity': 0.85},
                'width': max(1.5, s * 3.5), 'dashes': False,
                'arrows': {'to': {'enabled': True, 'scaleFactor': 0.6}},
                'font': {'color': '#ffffff', 'size': 11, 'align': 'middle',
                         'face': 'Inter, sans-serif', 'strokeWidth': 3, 'strokeColor': '#00000066'},
                'shadow': {'enabled': True, 'color': ec + '55', 'size': 8, 'x': 0, 'y': 0},
                '_relation': t['label'],
                '_subject_name': _display(sid), '_object_name': _display(oid),
                '_subject_id': sid, '_object_id': oid,
                '_confidence': s, '_negated': False,
                '_paper': meta.get('paper_id', ''),
                '_edge_type': 'extracted',
                '_stv': f"(stv {s:.2f} {c:.2f})",
                '_strength': s, '_conf_val': c,
                '_reasoning': meta.get('reasoning', ''),
                '_source': meta.get('source', ''),
                '_section': meta.get('section', ''),
                '_paper_url': meta.get('paper_url', ''),
                '_chain': '', '_rule': '',
                '_premise1': '', '_premise1_stv': '',
                '_premise2': '', '_premise2_stv': '',
            })
            eid += 1

    for t in inferred:
        sid, oid = t['subject_id'], t['object_id']
        _node(sid, t['subject_type'])
        _node(oid, t['object_type'])
        if sid and oid:
            s, c = t['strength'], t['confidence']
            meta = (atom_meta or {}).get(t['name'], {})
            proof = meta.get('_proof', {})
            via = meta.get('_via', 'forward')                  # forward chaining, or backward for a question
            vcol = '#a855f7' if via == 'forward' else '#22d3ee'
            icon = '🔮' if via == 'forward' else '🧭'
            slbl, olbl = _display(sid), _display(oid)
            vis_edges.append({
                'id': eid, 'from': sid, 'to': oid,
                'label': f"{icon} {t['label']}",
                'title': f"{icon} {t['label']} [inferred by {via} chaining] · stv({s:.2f}, {c:.2f})",
                'color': {'color': vcol + 'cc', 'highlight': '#ffffff', 'hover': '#ffffff', 'opacity': 0.85},
                'width': 2, 'dashes': [8, 4],
                'arrows': {'to': {'enabled': True, 'scaleFactor': 0.6}},
                'font': {'color': vcol, 'size': 11, 'align': 'middle',
                         'face': 'Inter, sans-serif', 'strokeWidth': 3, 'strokeColor': '#00000066'},
                'shadow': {'enabled': True, 'color': vcol + '55', 'size': 8, 'x': 0, 'y': 0},
                '_relation': f"{icon} {t['label']}",
                '_via': via, '_question': meta.get('_question', ''),
                '_subject_name': slbl, '_object_name': olbl,
                '_subject_id': sid, '_object_id': oid,
                '_confidence': s, '_negated': False,
                '_paper': '',
                '_edge_type': 'inferred',
                '_stv': f"(stv {s:.2f} {c:.2f})",
                '_strength': s, '_conf_val': c,
                '_reasoning': '', '_source': '', '_section': '', '_paper_url': '',
                '_chain': f"{slbl} → {t['label']} → {olbl}",
                '_rule': proof.get('rule', ''),
                '_premise1': proof.get('premise1', ''),
                '_premise1_stv': proof.get('premise1_stv', ''),
                '_premise2': proof.get('premise2', ''),
                '_premise2_stv': proof.get('premise2_stv', ''),
            })
            eid += 1

    vis_nodes = list(nodes_map.values())
    nodes_json = json.dumps(vis_nodes)
    edges_json = json.dumps(vis_edges)
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    # Paper dropdown options
    _paper_ids = sorted({
        e.get('_paper', '') for e in vis_edges
        if e.get('_paper', '') and e.get('_edge_type') == 'extracted'
    })
    def _paper_label(pid: str) -> str:
        return re.sub(r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_', '', pid) or pid
    paper_opts = ''.join(
        f'<option value="{p}">{_paper_label(p)}</option>' for p in _paper_ids
    )

    # Legend: node types present in graph
    shown_etypes = {n.get('_type', 'other') for n in vis_nodes}
    legend_nodes_html = ''.join(
        f'<div class="legend-item">'
        f'<div class="legend-dot" style="background:{_node_color(t)}"></div>'
        f'{t.replace("_", " ").title()}</div>'
        for t in sorted(shown_etypes)
    )

    # vis.js — inline if local bundle exists, else CDN
    _vis_path = (Path(__file__).resolve().parents[2]
                 / 'lib' / 'vis-9.1.2' / 'vis-network-standalone.min.js')
    vis_script = (
        f'<script>{_vis_path.read_text(encoding="utf-8")}</script>'
        if _vis_path.exists()
        else '<script src="https://unpkg.com/vis-network@9.1.2/standalone/umd/vis-network.min.js"></script>'
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>PLN AtomSpace{' — ' + paper_id if paper_id else ''}</title>
{vis_script}
<style>
  /* ── Identical to tools/visualize_graph.py ── */
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ width: 100%; height: 100%; overflow: hidden; }}
  body {{ background: #060d1a; color: #e2e8f0; font-family: 'Inter', 'Segoe UI', system-ui, sans-serif; display: flex; flex-direction: column; }}
  #header {{ background: linear-gradient(135deg, #0d1929 0%, #0f2136 100%); border-bottom: 1px solid #1a3350; padding: 0 20px; display: flex; align-items: center; gap: 12px; flex-shrink: 0; height: 52px; box-shadow: 0 2px 20px rgba(0,0,0,.5); flex-wrap: wrap; }}
  #header h1 {{ font-size: 15px; font-weight: 700; color: #a855f7; letter-spacing: -0.3px; }}
  #stats {{ font-size: 12px; color: #4a7fa5; margin-left: auto; font-weight: 500; }}
  #search-box {{ background: #0d1f2f; border: 1px solid #1a3350; border-radius: 8px; padding: 6px 12px; color: #e2e8f0; font-size: 12px; width: 200px; font-family: inherit; transition: border-color .2s; }}
  #search-box:focus {{ outline: none; border-color: #a855f7; box-shadow: 0 0 0 3px rgba(168,85,247,.15); }}
  #main {{ display: flex; height: calc(100vh - 52px); overflow: hidden; }}
  #graph-container {{ flex: 1; position: relative; height: calc(100vh - 52px); overflow: hidden; background: radial-gradient(ellipse at center, #0a1628 0%, #060d1a 70%); }}
  #network {{ width: 100%; height: calc(100vh - 52px); }}
  #side-panel {{ width: 360px; background: linear-gradient(180deg, #0d1929 0%, #0a1421 100%); border-left: 1px solid #1a3350; overflow-y: auto; flex-shrink: 0; transition: width 0.25s cubic-bezier(.4,0,.2,1); box-shadow: -4px 0 24px rgba(0,0,0,.4); }}
  #side-panel.hidden {{ width: 0; overflow: hidden; }}
  #panel-content {{ padding: 20px; }}
  #panel-title {{ font-size: 13px; font-weight: 700; color: #a855f7; margin-bottom: 16px; border-bottom: 1px solid #1a3350; padding-bottom: 10px; display: flex; align-items: center; gap: 8px; letter-spacing: .3px; text-transform: uppercase; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 10px; font-weight: 700; letter-spacing: .5px; }}
  .header-btn {{ background: rgba(168,85,247,.12); border: 1px solid rgba(168,85,247,.3); border-radius: 8px; padding: 5px 12px; color: #a855f7; font-size: 12px; font-family: inherit; cursor: pointer; transition: all .2s; white-space: nowrap; }}
  .header-btn:hover {{ background: rgba(168,85,247,.2); }}
  .field-group {{ margin-bottom: 16px; }}
  .field-label {{ font-size: 10px; color: #4a7fa5; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; font-weight: 600; }}
  .field-value {{ font-size: 12px; color: #cbd5e1; background: rgba(255,255,255,.04); padding: 8px 12px; border-radius: 8px; border: 1px solid #1a3350; word-break: break-word; line-height: 1.5; }}
  .field-value.canonical {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #38bdf8; }}
  .relation-row {{ background: rgba(255,255,255,.03); border: 1px solid #1a3350; border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; cursor: pointer; transition: all .15s; }}
  .relation-row:hover {{ border-color: #a855f7; background: rgba(168,85,247,.06); transform: translateX(2px); }}
  .relation-arrow {{ color: #a855f7; font-size: 12px; }}
  .conf-bar {{ height: 3px; border-radius: 2px; background: #1a3350; margin-top: 6px; }}
  .conf-fill {{ height: 100%; border-radius: 2px; background: linear-gradient(90deg, #a855f7, #6366f1); }}
  #placeholder {{ color: #2d5a80; font-size: 13px; text-align: center; padding: 60px 20px; }}
  #placeholder .icon {{ font-size: 48px; margin-bottom: 16px; opacity: .5; }}
  #legend {{ position: absolute; bottom: 16px; left: 16px; background: rgba(6,13,26,.88); border: 1px solid #1a3350; border-radius: 12px; padding: 12px 16px; font-size: 11px; max-width: 210px; backdrop-filter: blur(8px); }}
  #legend h4 {{ color: #4a7fa5; margin-bottom: 8px; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; font-weight: 600; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; margin-bottom: 3px; color: #c9d1d9; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
  .legend-dash {{ width: 20px; flex-shrink: 0; border-top: 2px dashed; }}
  #close-panel {{ cursor: pointer; color: #8b949e; font-size: 18px; margin-left: auto; }}
  #close-panel:hover {{ color: #e6edf3; }}
  .node-hero {{ display: flex; align-items: flex-start; gap: 14px; padding: 14px 0 16px; }}
  .node-avatar {{ width: 50px; height: 50px; border-radius: 15px; display: flex; align-items: center; justify-content: center; font-size: 20px; font-weight: 800; color: #04121c; flex-shrink: 0; box-shadow: 0 4px 18px rgba(0,0,0,.4); }}
  .node-hero-info {{ min-width: 0; flex: 1; padding-top: 2px; }}
  .node-hero-name {{ font-size: 16px; font-weight: 700; color: #f1f5f9; line-height: 1.35; word-break: break-word; }}
  .pill {{ display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 20px; font-size: 10px; font-weight: 700; letter-spacing: .3px; white-space: nowrap; }}
  .prop-table-wrap {{ background: rgba(255,255,255,.03); border: 1px solid #1a3350; border-radius: 10px; overflow: hidden; margin-top: 4px; }}
  .prop-table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  .prop-table tr {{ border-bottom: 1px solid #16283f; transition: background .15s; }}
  .prop-table tr:last-child {{ border-bottom: none; }}
  .prop-table tr:hover {{ background: rgba(255,255,255,.035); }}
  .prop-table td {{ padding: 9px 12px; vertical-align: top; }}
  .prop-table td.k {{ width: 32%; color: #4a7fa5; font-size: 10.5px; text-transform: uppercase; letter-spacing: .6px; font-weight: 700; white-space: nowrap; }}
  .prop-table td.v {{ color: #e2e8f0; word-break: break-word; }}
  .prop-mono {{ font-family: 'JetBrains Mono', monospace; font-size: 11.5px; color: #7dd3fc; }}
  .copy-btn {{ cursor: pointer; opacity: .45; transition: opacity .15s, transform .15s; font-size: 12px; margin-left: 8px; }}
  .copy-btn:hover {{ opacity: 1; transform: scale(1.15); }}
  #paper-filter {{ background: #0d1f2f; border: 1px solid #1a3350; border-radius: 8px; padding: 5px 10px; color: #e2e8f0; font-size: 12px; font-family: inherit; cursor: pointer; max-width: 200px; transition: border-color .2s; }}
  #paper-filter:focus {{ outline: none; border-color: #a855f7; }}
  #paper-filter option {{ background: #0d1929; }}
  #logo-home {{ border-radius: 8px; padding: 4px 8px; margin-left: -8px; transition: background .2s; }}
  #logo-home:hover {{ background: rgba(168,85,247,.12); }}
  .conf-detail {{ display: none; background: #0d1117; border-radius: 8px; padding: 12px; border: 1px solid #21262d; font-size: 11px; margin-top: 8px; }}
  .stv-how {{ display: flex; align-items: center; justify-content: space-between; font-size: 10px; color: #a855f7; font-weight: 600; border: 1px solid #a855f733; border-radius: 4px; padding: 2px 8px; background: #a855f708; gap: 4px; }}
</style>
</head>
<body>

<div id="header">
  <div id="logo-home" onclick="goHome()" title="Back to pipeline"
       style="display:flex;align-items:center;gap:8px;cursor:pointer">
    <span style="font-size:20px">🔮</span>
    <h1>PLN AtomSpace</h1>
  </div>
  <select id="paper-filter" onchange="filterByPaper(this.value)" title="Filter by source paper">
    <option value="">📄 All papers</option>
    {paper_opts}
  </select>
  <button class="header-btn" id="facts-badge">✓ {len(original)} facts</button>
  <button class="header-btn" id="inferred-badge" data-via="forward"
          style="color:#c084fc;border-color:rgba(192,132,252,.3);cursor:pointer"
          title="Derived by forward chaining: every relation the rules imply"
          onclick="showInferredOnly(this, 'forward')">🔮 {n_forward} forward-inferred</button>
  <button class="header-btn" id="backward-badge" data-via="backward"
          style="color:#22d3ee;border-color:rgba(34,211,238,.3);cursor:pointer"
          title="Proved by backward chaining while answering a question"
          onclick="showInferredOnly(this, 'backward')">🧭 {n_backward} backward-inferred</button>
  <button class="header-btn" id="inferred-toggle" onclick="toggleInferred(this)">⛓ Hide inferred</button>
  <input id="search-box" type="text" placeholder="Search nodes…" oninput="searchNodes(this.value)">
  <span id="stats">{len(vis_nodes)} nodes · {len(vis_edges)} edges · {ts}</span>
</div>

<div id="main">
  <div id="graph-container">
    <div id="network"></div>
    <div id="legend">
      <h4>Node types</h4>
      {legend_nodes_html}
      <h4 style="margin-top:8px">Edges</h4>
      <div class="legend-item">
        <div style="width:20px;height:3px;border-radius:2px;background:#8b949e;flex-shrink:0"></div>
        PLN extracted
      </div>
      <div class="legend-item">
        <div class="legend-dash" style="border-color:#a855f7"></div>
        PLN inferred
      </div>
    </div>
  </div>

  <div id="side-panel" class="hidden">
    <div id="panel-content">
      <div id="placeholder">
        <div class="icon">🔮</div>
        Click a <strong>node</strong> or <strong>edge</strong><br>to see PLN details here
      </div>
    </div>
  </div>
</div>

<script>
window.onerror = function(msg, src, line) {{
  var el = document.getElementById('network');
  if (el) el.innerHTML = '<div style="color:#f85149;padding:20px;font-family:monospace;font-size:13px">JS ERROR: ' + msg + '<br>Line: ' + line + '</div>';
}};

const NODES_DATA = {nodes_json};
const EDGES_DATA = {edges_json};

function esc(s) {{
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function copyToClipboard(el, text) {{
  const flash = () => {{
    const prev = el.textContent;
    el.textContent = '✅';
    setTimeout(() => {{ el.textContent = prev; }}, 900);
  }};
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text).then(flash).catch(flash);
    return;
  }}
  try {{
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select(); document.execCommand('copy');
    document.body.removeChild(ta);
  }} catch(e) {{}}
  flash();
}}

const nodeMap = {{}};
NODES_DATA.forEach(n => nodeMap[n.id] = n);
const edgeMap = {{}};
EDGES_DATA.forEach(e => edgeMap[e.id] = e);

var inferredVisible = true;
var _focusedNode = null;
var _inferredOnlyMode = false;

// ── Inferred-only view ───────────────────────────────────────────────────────
function showInferredOnly(btn, via) {{
  var again = _inferredOnlyMode === via;
  _inferredOnlyMode = again ? false : via;
  _focusedNode = null;
  document.querySelectorAll('[data-via]').forEach(b => {{ b.style.background = ''; }});

  if (!_inferredOnlyMode) {{
    // Restore full graph
    nodesDS.update(NODES_DATA.map(n => ({{id: n.id, hidden: false}})));
    edgesDS.update(EDGES_DATA.map(e => ({{id: e.id, hidden: e._edge_type === 'inferred' && !inferredVisible}})));
    hidePanel();
    return;
  }}

  // Isolate the subgraph inferred in this direction
  var inferredEdges = EDGES_DATA.filter(e => e._edge_type === 'inferred' && e._via === via);
  var fwd = via === 'forward', col = fwd ? '#c084fc' : '#22d3ee';
  var inferredNodeIds = new Set();
  inferredEdges.forEach(e => {{ inferredNodeIds.add(e.from); inferredNodeIds.add(e.to); }});

  nodesDS.update(NODES_DATA.map(n => ({{id: n.id, hidden: !inferredNodeIds.has(n.id)}})));
  edgesDS.update(EDGES_DATA.map(e => ({{id: e.id, hidden: !(e._edge_type === 'inferred' && e._via === via)}})));
  if (inferredNodeIds.size > 0) {{
    network.fit({{nodes: [...inferredNodeIds], animation: {{duration: 500, easingFunction: 'easeInOutQuad'}}}});
  }}

  btn.style.background = fwd ? 'rgba(192,132,252,0.2)' : 'rgba(34,211,238,0.15)';

  // Build side-panel table of inferred atoms
  var html = `<div style="display:flex;justify-content:flex-end">
    <span id="close-panel" onclick="showInferredOnly(document.querySelector('[data-via=${{via}}]'), '${{via}}')" style="cursor:pointer">✕</span>
  </div>
  <div style="color:${{col}};font-size:13px;font-weight:600;margin-bottom:12px">
    ${{fwd ? '🔮 Forward-inferred' : '🧭 Backward-inferred'}} (${{inferredEdges.length}})
  </div>
  <div style="font-size:11px;color:#8b949e;margin-bottom:12px">
    ${{fwd ? 'Derived by forward chaining: every relation the rules imply from the facts.'
           : 'Proved by backward chaining while answering a question; the question is on each row.'}}
    Click any row for full derivation.
    ${{inferredEdges.length ? '' : '<br><br>None yet' + (fwd ? '.' : ': ask questions in the PLN board.')}}
  </div>`;

  inferredEdges.forEach(e => {{
    var rel = (e._relation || '').replace(/_/g, ' ');
    var m   = (e._stv || '').match(/([0-9.]+)[,\s]+([0-9.]+)/);
    var stv = m ? 'stv(' + parseFloat(m[1]).toFixed(2) + ', ' + parseFloat(m[2]).toFixed(2) + ')' : '';
    html += `<div class="relation-row" data-edge-id="${{e.id}}"
               style="border-left:2px solid #c084fc;padding:6px 10px;margin-bottom:6px;cursor:pointer">
      <strong style="color:#c084fc">${{esc(rel)}}</strong><br>
      <span style="font-size:11px;color:#c9d1d9">${{esc(e._subject_name || e.from)}}</span>
      <span style="color:#484f58"> → </span>
      <span style="font-size:11px;color:#c9d1d9">${{esc(e._object_name || e.to)}}</span><br>
      <span style="font-family:monospace;font-size:10px;color:#6e7681">${{stv}}</span>
      ${{e._question ? `<br><span style="font-size:10px;color:#22d3ee">for: ${{esc(e._question)}}</span>` : ''}}
    </div>`;
  }});

  document.getElementById('panel-content').innerHTML = html;
  document.getElementById('side-panel').classList.remove('hidden');
  document.querySelectorAll('#panel-content .relation-row[data-edge-id]').forEach(el => {{
    el.addEventListener('click', () => showEdgePanel(parseInt(el.dataset.edgeId)));
  }});
}}

// ── Paper filter ─────────────────────────────────────────────────────────────
function filterByPaper(paperId) {{
  _focusedNode = null;
  if (!paperId) {{
    nodesDS.update(NODES_DATA.map(n => ({{id: n.id, hidden: false}})));
    edgesDS.update(EDGES_DATA.map(e => ({{id: e.id, hidden: e._edge_type === 'inferred' && !inferredVisible}})));
    return;
  }}
  var paperNodeIds = new Set();
  EDGES_DATA.forEach(e => {{
    if (e._paper === paperId && e._edge_type === 'extracted') {{
      paperNodeIds.add(e.from); paperNodeIds.add(e.to);
    }}
  }});
  var visibleNodeIds = new Set();
  var edgeUpdates = EDGES_DATA.map(e => {{
    var show = false;
    if (e._edge_type === 'extracted') {{
      show = (e._paper === paperId);
    }} else if (e._edge_type === 'inferred' && inferredVisible) {{
      show = paperNodeIds.has(e.from) && paperNodeIds.has(e.to);
    }}
    if (show) {{ visibleNodeIds.add(e.from); visibleNodeIds.add(e.to); }}
    return {{id: e.id, hidden: !show}};
  }});
  edgesDS.update(edgeUpdates);
  nodesDS.update(NODES_DATA.map(n => ({{id: n.id, hidden: !visibleNodeIds.has(n.id)}})));
  if (visibleNodeIds.size > 0) {{
    network.fit({{nodes: [...visibleNodeIds], animation: {{duration: 500, easingFunction: 'easeInOutQuad'}}}});
  }}
}}

// ── Inferred toggle ──────────────────────────────────────────────────────────
function toggleInferred(btn) {{
  inferredVisible = !inferredVisible;
  btn.textContent = inferredVisible ? '⛓ Hide inferred' : '⛓ Show inferred';
  btn.style.opacity = inferredVisible ? '1' : '0.5';
  edgesDS.update(EDGES_DATA.map(e => ({{id: e.id, hidden: e._edge_type === 'inferred' && !inferredVisible}})));
}}

// vis.js DataSets
const nodesDS = new vis.DataSet(NODES_DATA);
const edgesDS = new vis.DataSet(EDGES_DATA);

var network;

// ── STV detail builder (for edge panel) ─────────────────────────────────────
function buildStvDetail(e) {{
  var s = e._strength || 0;
  var c = e._conf_val || 0;
  var N = Math.round(c / (1 - c + 1e-6));
  var barColor = c >= 0.75 ? '#238636' : c >= 0.55 ? '#d29922' : '#8b949e';
  var isInf = e._edge_type === 'inferred';

  var provenanceHtml = '';
  if (isInf) {{
    provenanceHtml =
      '<div style="margin-bottom:10px;padding:10px 12px;background:#0d1117;border-radius:8px;border-left:3px solid #a855f7">' +
      '<div style="color:' + (e._via === 'backward' ? '#22d3ee' : '#c084fc') + ';font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">' +
        (e._via === 'backward' ? 'Backward Chaining Proof' : 'Forward Chaining Proof') + '</div>' +
      (e._question ? '<div style="color:#8b949e;font-size:10px;margin-bottom:6px">Proved for the question: <span style="color:#22d3ee">' + esc(e._question) + '</span></div>' : '') +
      '<div style="color:#8b949e;font-size:10px;margin-bottom:6px">Rule: <code style="color:#a855f7">' + esc(e._rule || 'rule') + '</code></div>' +
      (e._premise1 && e._premise2
        ? '<div style="background:#161b22;border-radius:6px;padding:8px 10px;margin-bottom:6px">' +
            '<div style="color:#58a6ff;font-size:10px;font-weight:700;margin-bottom:3px">P1</div>' +
            '<div style="color:#e6edf3;font-family:monospace;font-size:11px">' + esc(e._premise1) + '</div>' +
            '<div style="color:#484f58;font-size:10px;margin-top:2px">' + esc(e._premise1_stv) + '</div>' +
            '<div style="color:#3fb950;font-size:10px;font-weight:700;margin:6px 0 3px">P2</div>' +
            '<div style="color:#e6edf3;font-family:monospace;font-size:11px">' + esc(e._premise2) + '</div>' +
            '<div style="color:#484f58;font-size:10px;margin-top:2px">' + esc(e._premise2_stv) + '</div>' +
          '</div>'
        : '<div style="color:#c9d1d9;font-size:11px;line-height:1.6">Two extracted atoms with matching bridge entity triggered this rule.</div>') +
      '<div style="color:#484f58;font-size:10px;margin-top:4px">→ <span style="color:#c084fc;font-family:monospace">' + esc(e._chain || '') + '</span></div>' +
      '</div>';
  }} else if (e._reasoning || e._source) {{
    provenanceHtml =
      '<div style="margin-bottom:10px;padding:10px 12px;background:#0d1117;border-radius:8px;border-left:3px solid #238636">' +
      '<div style="color:#3fb950;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">LLM Extraction Origin</div>' +
      (e._source
        ? '<div style="margin-bottom:6px"><span style="color:#484f58;font-size:10px">Paper: </span>' +
            (/^https?:\/\//.test(e._paper_url || '')
              ? '<a href="' + esc(e._paper_url) + '" target="_blank" style="color:#58a6ff;font-size:11px;text-decoration:none">' + esc(e._source) + ' ↗</a>'
              : '<span style="color:#79c0ff;font-size:11px">' + esc(e._source) + '</span>') +
          '</div>'
        : '') +
      (e._section ? '<div style="margin-bottom:6px"><span style="color:#484f58;font-size:10px">Section: </span><span style="color:#8b949e;font-size:11px">' + esc(e._section) + '</span></div>' : '') +
      (e._reasoning
        ? '<div style="color:#484f58;font-size:10px;margin-bottom:4px">Extracted from:</div>' +
          '<div style="color:#c9d1d9;font-size:11px;line-height:1.6;background:#161b22;padding:8px;border-radius:6px;font-style:italic">&ldquo;' + esc(e._reasoning) + '&rdquo;</div>'
        : '') +
      '</div>';
  }}

  var strengthHtml =
    '<div style="margin-bottom:10px;padding:10px 12px;background:#161b22;border-radius:8px;border-left:3px solid #a855f7">' +
    '<div style="color:#a855f7;font-weight:700;margin-bottom:6px;font-size:12px">strength = ' + s.toFixed(3) + '</div>' +
    '<div style="color:#c9d1d9;line-height:1.6;font-size:11px">' +
    (isInf
      ? 'Propagated from weakest premise strength via PeTTaChainer forward chaining.'
      : 'LLM extraction confidence from Layer 7 — how certain the model was that this relation appears in the source text. Stored as <code style="color:#79c0ff;font-size:10px">(STV ' + s.toFixed(2) + ' ' + c.toFixed(2) + ')</code>.') +
    '</div></div>';

  var confCalc = isInf
    ? '<div style="color:#c9d1d9;font-size:11px;line-height:1.6">Propagated via PeTTaChainer. confidence = min(premise confidences) — never exceeds its weakest evidence source.</div>'
    : '<div style="background:#0d1117;border-radius:6px;padding:8px 10px;margin:6px 0;font-size:11px">' +
        '<div style="color:#8b949e;margin-bottom:4px;font-size:10px">PLN Truth_w2c formula:</div>' +
        '<div style="color:#e6edf3;font-family:monospace">c = N / (N + 1)</div>' +
        '<div style="color:#e6edf3;font-family:monospace">c = <span style="color:#a855f7">' + N + '</span> / (<span style="color:#a855f7">' + N + '</span> + 1) = <span style="color:' + barColor + ';font-weight:700">' + c.toFixed(3) + '</span></div>' +
        '<div style="color:#484f58;font-size:10px;margin-top:6px">N = number of source papers reporting this relation</div>' +
      '</div>';

  var confHtml =
    '<div style="padding:10px 12px;background:#161b22;border-radius:8px;border-left:3px solid ' + barColor + '">' +
    '<div style="color:' + barColor + ';font-weight:700;margin-bottom:6px;font-size:12px">confidence = ' + c.toFixed(3) +
      (N === 1 ? ' (1 paper)' : N === 2 ? ' (2 papers)' : N >= 3 ? ' (' + N + ' papers)' : '') + '</div>' +
    confCalc +
    '<div style="background:#21262d;border-radius:3px;height:6px;margin:10px 0 4px;width:100%">' +
      '<div style="background:' + barColor + ';height:6px;border-radius:3px;width:' + Math.round(c * 100) + '%"></div>' +
    '</div>' +
    '<div style="display:flex;justify-content:space-between;color:#484f58;font-size:10px">' +
      '<span>0</span><span>0.5 (1)</span><span>0.67 (2)</span><span>0.75 (3)</span><span>1.0</span>' +
    '</div></div>';

  return provenanceHtml + strengthHtml + confHtml;
}}

// ── Network init ─────────────────────────────────────────────────────────────
function initNetwork() {{
  var container = document.getElementById('network');
  network = new vis.Network(container, {{nodes: nodesDS, edges: edgesDS}}, {{
    physics: {{
      barnesHut: {{
        gravitationalConstant: -12000,
        centralGravity: 0.1,
        springLength: 220,
        springConstant: 0.04,
        damping: 0.12,
        avoidOverlap: 0.3
      }},
      stabilization: {{iterations: 150, updateInterval: 50, fit: false}}
    }},
    layout: {{improvedLayout: false}},
    interaction: {{
      hover: true, tooltipDelay: 200,
      navigationButtons: true, keyboard: true,
      zoomView: true, zoomSpeed: 0.5,
      minScale: 0.05, maxScale: 5,
      multiselect: false
    }},
    edges: {{
      smooth: {{type: 'continuous', roundness: 0.3}},
      scaling: {{label: {{enabled: true, drawThreshold: 0.8, maxVisible: 16}}}}
    }},
    nodes: {{
      shape: 'dot', borderWidth: 2,
      scaling: {{label: {{enabled: true, min: 10, max: 18, drawThreshold: 0.4, maxVisible: 20}}}}
    }}
  }});

  network.once('stabilizationIterationsDone', function() {{
    network.fit({{animation: {{duration: 600, easingFunction: 'easeInOutQuad'}}}});
    network.setOptions({{physics: {{enabled: false}}}});
  }});

  // ── focusNode — same as visualize_graph.py ───────────────────────────────
  function focusNode(nodeId) {{
    if (_focusedNode === nodeId) {{
      _focusedNode = null;
      nodesDS.update(NODES_DATA.map(n => ({{id: n.id, hidden: false}})));
      edgesDS.update(EDGES_DATA.map(e => ({{id: e.id, hidden: e._edge_type === 'inferred' && !inferredVisible}})));
      return;
    }}
    _focusedNode = nodeId;
    var connectedEdgeIds = new Set(
      EDGES_DATA.filter(e => e.from === nodeId || e.to === nodeId).map(e => e.id)
    );
    var neighborIds = new Set([nodeId]);
    EDGES_DATA.forEach(e => {{
      if (e.from === nodeId) neighborIds.add(e.to);
      if (e.to   === nodeId) neighborIds.add(e.from);
    }});
    nodesDS.update(NODES_DATA.map(n => ({{id: n.id, hidden: !neighborIds.has(n.id)}})));
    edgesDS.update(EDGES_DATA.map(e => ({{id: e.id, hidden: !connectedEdgeIds.has(e.id)}})));
    network.fit({{nodes: [...neighborIds], animation: {{duration: 400, easingFunction: 'easeInOutQuad'}}}});
  }}

  // ── showNodePanel — same structure as visualize_graph.py ─────────────────
  function showNodePanel(nodeId) {{
    var n = nodeMap[nodeId];
    if (!n) return;
    document.getElementById('side-panel').classList.remove('hidden');

    var outEdges = EDGES_DATA.filter(e => e.from === nodeId);
    var inEdges  = EDGES_DATA.filter(e => e.to   === nodeId);
    var typeDisp = (n._type || 'entity').replace(/_/g, ' ');
    var bg       = n.color.background;
    var props    = n._props || {{}};
    var initial  = (typeDisp.trim().charAt(0) || '?').toUpperCase();

    var html = `
      <div style="display:flex;justify-content:flex-end">
        <span id="close-panel" onclick="hidePanel()">✕</span>
      </div>
      <div class="node-hero">
        <div class="node-avatar" style="background:linear-gradient(135deg,${{bg}},${{bg}}bb)">${{initial}}</div>
        <div class="node-hero-info">
          <div class="node-hero-name">${{esc(props.name || n._name || '')}}</div>
        </div>
      </div>
      <div style="font-size:10px;color:#4a7fa5;margin-bottom:14px;padding:6px 10px;
           background:rgba(168,85,247,.08);border-radius:6px;border:1px solid rgba(168,85,247,.2)">
        🔍 Showing subgraph — click node again or empty space to restore full graph
      </div>`;

    // PLN STV: average across adjacent edges
    var allConn = outEdges.concat(inEdges);
    var stvVals = allConn.map(function(e) {{
      var m = (e._stv || '').match(/([0-9.]+)[,\\s]+([0-9.]+)/);
      return m ? {{s: parseFloat(m[1]), c: parseFloat(m[2])}} : null;
    }}).filter(Boolean);
    var avgS = stvVals.length ? stvVals.reduce(function(a,v){{return a+v.s;}}, 0) / stvVals.length : null;
    var avgC = stvVals.length ? stvVals.reduce(function(a,v){{return a+v.c;}}, 0) / stvVals.length : null;
    var confColor = avgC === null ? '#8b949e' : avgC >= 0.75 ? '#238636' : avgC >= 0.55 ? '#d29922' : '#8b949e';

    var rows = [];
    rows.push(['Type', `<span class="pill" style="background:${{bg}}22;color:${{bg}};border:1px solid ${{bg}}55">${{esc(typeDisp)}}</span>`]);
    if (props.id) {{
      rows.push(['ID', `<span class="prop-mono">${{esc(props.id)}}</span><span class="copy-btn" data-copy="${{esc(props.id)}}" title="Copy ID">📋</span>`]);
    }}
    if (props.id_source) {{
      rows.push(['ID source', `<span class="pill" style="background:rgba(56,189,248,.1);color:#7dd3fc;border:1px solid rgba(56,189,248,.3)">${{esc(String(props.id_source).toUpperCase())}}</span>`]);
    }}
    if (avgS !== null) {{
      rows.push(['PLN STV',
        `<span style="color:${{confColor}};font-family:monospace;font-size:11px">stv(${{avgS.toFixed(2)}}, ${{avgC.toFixed(2)}})</span>` +
        (stvVals.length > 1 ? `<span style="color:#484f58;font-size:10px;margin-left:6px">(avg of ${{stvVals.length}} edges)</span>` : '')
      ]);
    }}

    html += `<div class="field-group">
      <div class="field-label">Properties</div>
      <div class="prop-table-wrap">
        <table class="prop-table">
          ${{rows.map(([k, v]) => `<tr><td class="k">${{k}}</td><td class="v">${{v}}</td></tr>`).join('')}}
        </table>
      </div>
    </div>`;

    if (outEdges.length > 0) {{
      html += `<div class="field-group"><div class="field-label">Outgoing (${{outEdges.length}})</div>`;
      outEdges.forEach(e => {{
        html += `<div class="relation-row" data-edge-id="${{e.id}}">
          <span class="relation-arrow">→</span>
          <strong style="color:${{e.color.color}}">${{esc((e._relation||'').replace(/_/g,' '))}}</strong>
          <br><span style="color:#8b949e;font-size:11px">${{esc(e._object_name)}}</span>
          <div class="conf-bar"><div class="conf-fill" style="width:${{e._confidence*100}}%"></div></div>
        </div>`;
      }});
      html += `</div>`;
    }}
    if (inEdges.length > 0) {{
      html += `<div class="field-group"><div class="field-label">Incoming (${{inEdges.length}})</div>`;
      inEdges.forEach(e => {{
        html += `<div class="relation-row" data-edge-id="${{e.id}}">
          <span class="relation-arrow">←</span>
          <strong style="color:${{e.color.color}}">${{esc((e._relation||'').replace(/_/g,' '))}}</strong>
          <br><span style="color:#8b949e;font-size:11px">${{esc(e._subject_name)}}</span>
          <div class="conf-bar"><div class="conf-fill" style="width:${{e._confidence*100}}%"></div></div>
        </div>`;
      }});
      html += `</div>`;
    }}

    document.getElementById('panel-content').innerHTML = html;
    document.getElementById('panel-content').querySelectorAll('[data-edge-id]').forEach(el => {{
      el.addEventListener('click', () => showEdgePanel(parseInt(el.dataset.edgeId)));
    }});
    document.getElementById('panel-content').querySelectorAll('.copy-btn[data-copy]').forEach(el => {{
      el.addEventListener('click', () => copyToClipboard(el, el.dataset.copy));
    }});
  }}

  // ── showEdgePanel — PLN STV version ─────────────────────────────────────
  function showEdgePanel(edgeId) {{
    var e = edgeMap[edgeId];
    if (!e) return;
    document.getElementById('side-panel').classList.remove('hidden');

    var subjectNode = nodeMap[e.from] || {{}};
    var objectNode  = nodeMap[e.to]   || {{}};
    var subjColor   = (subjectNode.color || {{}}).background || '#888';
    var objColor    = (objectNode.color  || {{}}).background || '#888';
    var relColor    = e.color.color;
    var isInf       = e._edge_type === 'inferred';
    var typeColor   = isInf ? '#a855f7' : '#238636';
    var s = e._strength || 0;
    var c = e._conf_val || 0;
    var barColor = c >= 0.75 ? '#238636' : c >= 0.55 ? '#d29922' : '#8b949e';
    var relLabel = (e._relation || '').replace(/🔮/g, '').trim().replace(/_/g, ' ');

    var sDetailHtml = buildStvDetail(e);

    var html = `
      <div id="panel-title">
        <span style="color:${{relColor}};font-size:18px">⟶</span>
        ${{isInf ? '🔮 PLN Inferred' : '📐 Extracted'}}
        <span id="close-panel" onclick="hidePanel()">✕</span>
      </div>

      <div class="field-group">
        <div class="field-label">Triple (S → P → O)</div>
        <div class="field-value" style="line-height:1.8">
          <span style="background:${{subjColor}}22;color:${{subjColor}};padding:2px 6px;border-radius:4px;font-size:12px">
            ${{esc(e._subject_name || e.from)}}
          </span>
          <br>
          <span style="color:${{relColor}};font-weight:600;padding:2px 6px">
            ${{esc(relLabel)}}
            ${{isInf ? '<span class="badge" style="background:#a855f722;color:#a855f7;border:1px solid #a855f744;margin-left:6px;font-size:9px">PLN INFERRED</span>' : ''}}
          </span>
          <br>
          <span style="background:${{objColor}}22;color:${{objColor}};padding:2px 6px;border-radius:4px;font-size:12px">
            ${{esc(e._object_name || e.to)}}
          </span>
        </div>
      </div>

      <div class="field-group">
        <div class="field-label" style="color:#8b949e;cursor:pointer;user-select:none;
             display:flex;align-items:center;justify-content:space-between"
             onclick="(function(el){{
               var d = el.closest('.field-group').querySelector('.conf-detail');
               var arr = el.querySelector('.arr-txt');
               if (d.style.display === 'none' || !d.style.display) {{
                 d.style.display = 'block'; arr.textContent = '▲';
               }} else {{
                 d.style.display = 'none'; arr.textContent = '▼';
               }}
             }})(this)" title="Click to see how this STV was computed">
          PLN Truth Value
          <span class="stv-how"><span>▶ how it’s calculated?</span><span class="arr-txt">▼</span></span>
        </div>
        <div class="field-value">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
            <span style="font-size:15px;font-weight:800;font-family:monospace;color:${{barColor}}">
              ${{e._stv || '(stv ? ?)'}}
            </span>
            <div style="flex:1">
              <div style="background:#21262d;border-radius:3px;height:6px">
                <div style="background:${{barColor}};height:6px;border-radius:3px;width:${{Math.round(s*100)}}%"></div>
              </div>
              <div style="font-size:10px;color:#484f58;margin-top:3px">
                strength (LLM conf) ${{isInf ? '· PLN inferred' : '· PLN extracted'}}
              </div>
            </div>
          </div>
          <div class="conf-detail" style="display:none">
            ${{sDetailHtml}}
          </div>
        </div>
      </div>`;

    if (e._paper) {{
      html += `
        <div class="field-group">
          <div class="field-label">Source paper</div>
          <div class="field-value canonical">${{esc(e._paper)}}${{e._section ? '  ·  ' + esc(e._section) : ''}}</div>
        </div>`;
    }}
    if (e._reasoning && !isInf) {{
      html += `
        <div class="field-group">
          <div class="field-label">Reasoning trace</div>
          <div class="field-value" style="font-style:italic;color:#c9d1d9">&ldquo;${{esc(e._reasoning)}}&rdquo;</div>
        </div>`;
    }}
    if (isInf && e._chain) {{
      html += `
        <div class="field-group">
          <div class="field-label">Derivation chain</div>
          <div class="field-value canonical" style="color:#c084fc">${{esc(e._chain)}}</div>
          <div style="color:#484f58;font-size:10px;margin-top:4px">PeTTaChainer · rules from the schema and learned from the facts</div>
        </div>`;
    }}
    html += `
      <div class="field-group">
        <div class="field-label">Subject ID</div>
        <div class="field-value canonical">${{esc(e._subject_id || e.from)}}</div>
      </div>
      <div class="field-group">
        <div class="field-label">Object ID</div>
        <div class="field-value canonical">${{esc(e._object_id || e.to)}}</div>
      </div>`;

    document.getElementById('panel-content').innerHTML = html;
    network.selectEdges([edgeId]);
  }}

  function hidePanel() {{
    document.getElementById('side-panel').classList.add('hidden');
    network.unselectAll();
  }}

  function searchNodes(query) {{
    if (!query) {{
      nodesDS.update(NODES_DATA.map(n => ({{id: n.id, hidden: false}})));
      edgesDS.update(EDGES_DATA.map(e => ({{id: e.id, hidden: e._edge_type === 'inferred' && !inferredVisible}})));
      return;
    }}
    var q = query.toLowerCase();
    var visibleIds = new Set(
      NODES_DATA.filter(n =>
        (n._name || '').toLowerCase().includes(q) ||
        (n._type || '').toLowerCase().includes(q) ||
        n.id.toLowerCase().includes(q)
      ).map(n => n.id)
    );
    nodesDS.update(NODES_DATA.map(n => ({{id: n.id, hidden: !visibleIds.has(n.id)}})));
    edgesDS.update(EDGES_DATA.map(e => ({{
      id: e.id,
      hidden: !(visibleIds.has(e.from) && visibleIds.has(e.to))
    }})));
  }}

  // ── Click handler — same as visualize_graph.py ───────────────────────────
  network.on('click', function(params) {{
    if (params.nodes.length > 0) {{
      var clickedId = params.nodes[0];
      if (_focusedNode && clickedId !== _focusedNode) {{
        showNodePanel(clickedId);
      }} else {{
        focusNode(clickedId);
        showNodePanel(clickedId);
      }}
    }} else if (params.edges.length > 0) {{
      showEdgePanel(params.edges[0]);
    }} else {{
      if (_focusedNode) {{
        _focusedNode = null;
        nodesDS.update(NODES_DATA.map(n => ({{id: n.id, hidden: false}})));
        edgesDS.update(EDGES_DATA.map(e => ({{id: e.id, hidden: e._edge_type === 'inferred' && !inferredVisible}})));
      }}
      hidePanel();
    }}
  }});

  network.on('doubleClick', function(params) {{
    if (params.nodes.length > 0) {{
      network.focus(params.nodes[0], {{scale: 1.5, animation: true}});
    }}
  }});

  window.showNodePanel = showNodePanel;
  window.showEdgePanel = showEdgePanel;
  window.hidePanel     = hidePanel;
  window.searchNodes   = searchNodes;
}} // end initNetwork

window.addEventListener('load', function() {{
  requestAnimationFrame(function() {{
    requestAnimationFrame(initNetwork);
  }});
}});

function goHome() {{
  var top = window.top || window.self;
  top.location.href = '/';
}}
</script>
</body>
</html>"""

    out_path.write_text(html, encoding='utf-8')
    _write_metta_files(out_path.parent, paper_id, original, inferred, id_to_label)
    return out_path


def _write_metta_files(folder: Path, paper_id: str, original: list, inferred: list,
                       id_to_label: dict | None = None) -> None:
    """Write PLN atomspace: flat .metta files at root + per-category metta/ subfolder."""
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    header = (
        f"; PLN AtomSpace — {paper_id or 'unknown'}\n"
        f"; Generated: {ts}\n"
        f"; Format: (: name (Pred subj obj) (STV strength confidence))\n"
        f"; Compatible: PeTTaChainer, Hyperon ECAN, PatternMiner, NAC\n"
        f"; STV: strength = LLM extraction confidence · confidence = N/(N+1) where N = evidence papers\n\n"
    )
    _lbl = id_to_label or {}

    def _name_slug(text: str) -> str:
        return re.sub(r'[^a-z0-9]+', '_', (text or '').lower()).strip('_')[:40]

    def _atom_line(t: dict, prefix: str = '') -> str:
        sid    = t.get('subject_id', '')
        oid    = t.get('object_id',  '')
        sname  = _lbl.get(sid) or sid
        oname  = _lbl.get(oid) or oid
        stype  = (t.get('subject_type') or 'entity').lower()
        otype  = (t.get('object_type')  or 'entity').lower()
        s_slug = _name_slug(sname)
        o_slug = _name_slug(oname)
        # Always derive name from human-readable slugs, not stored ID-based name
        name   = f"{t['predicate'].lower()}_{s_slug}_{o_slug}"
        subj_expr = f"({stype} {s_slug})"
        obj_expr  = f"({otype} {o_slug})"
        return f"(: {prefix}{name} ({t['predicate']} {subj_expr} {obj_expr}) (STV {t['strength']:.4f} {t['confidence']:.4f}))\n"

    orig_lines = [_atom_line(t) for t in original]
    inf_lines  = [_atom_line(t, 'pln_') for t in inferred]

    (folder / 'pln_original.metta').write_text(
        header + f"; === ORIGINAL EXTRACTED ATOMS ({len(orig_lines)}) ===\n\n" + ''.join(orig_lines),
        encoding='utf-8',
    )
    (folder / 'pln_inferred.metta').write_text(
        header + f"; === PLN INFERRED ATOMS ({len(inf_lines)}) ===\n\n" + ''.join(inf_lines),
        encoding='utf-8',
    )
    (folder / 'pln_atomspace.metta').write_text(
        header
        + f"; === ORIGINAL EXTRACTED ATOMS ({len(orig_lines)}) ===\n\n"
        + ''.join(orig_lines)
        + f"\n; === PLN INFERRED ATOMS ({len(inf_lines)}) ===\n\n"
        + ''.join(inf_lines),
        encoding='utf-8',
    )

    # pln_metta/ mirrors unified_metta/metta/: edges_*.metta extracted, inferred_*.metta inferred
    from collections import defaultdict

    def _rel_key(t: dict) -> tuple[str, str, str]:
        stype = (t.get('subject_type') or 'unknown').lower()
        otype = (t.get('object_type')  or 'unknown').lower()
        pred  = t.get('predicate', '').lower()
        return stype, pred, otype

    # extracted: relation_key → list of atom lines
    extracted_by_rel: dict[tuple, list[str]] = defaultdict(list)
    for t in original:
        extracted_by_rel[_rel_key(t)].append(_atom_line(t))

    # inferred: relation_key → list of atom lines
    inferred_by_rel: dict[tuple, list[str]] = defaultdict(list)
    for t in inferred:
        inferred_by_rel[_rel_key(t)].append(_atom_line(t, 'pln_inferred_'))

    metta_dir = folder / 'pln_metta'           # never the run's own metta/, which PLN reads as input
    metta_dir.mkdir(exist_ok=True)

    # pln_metta/<subject_type>/edges_*.metta and inferred_*.metta
    all_rel_keys = set(extracted_by_rel) | set(inferred_by_rel)
    cat_manifest: dict[str, list[str]] = {}

    for (stype, pred, otype) in sorted(all_rel_keys):
        type_dir = metta_dir / stype
        type_dir.mkdir(exist_ok=True)
        cat_manifest.setdefault(stype, [])

        rel_name = f"{stype}_{pred}_{otype}"

        ext_lines = extracted_by_rel.get((stype, pred, otype), [])
        if ext_lines:
            fname = f"edges_{rel_name}.metta"
            (type_dir / fname).write_text(
                f"; {fname} — PLN extracted (STV format)\n; {ts}\n\n" + ''.join(ext_lines),
                encoding='utf-8',
            )
            cat_manifest[stype].append(fname)

        inf_lines = inferred_by_rel.get((stype, pred, otype), [])
        if inf_lines:
            fname = f"inferred_{rel_name}.metta"
            (type_dir / fname).write_text(
                f"; {fname} — PLN inferred via forward chaining (STV format)\n; {ts}\n\n" + ''.join(inf_lines),
                encoding='utf-8',
            )
            cat_manifest[stype].append(fname)

    import json as _json
    manifest = {
        'paper_id':        paper_id,
        'generated_at':    ts,
        'format':          'PeTTaChainer MeTTa (STV)',
        'compatible_with': ['PeTTaChainer', 'Hyperon ECAN', 'PatternMiner', 'NAC'],
        'atom_counts': {
            'original': len(orig_lines),
            'inferred': len(inf_lines),
            'total':    len(orig_lines) + len(inf_lines),
        },
        'files': {
            'full_atomspace': 'pln_atomspace.metta',
            'original_only':  'pln_original.metta',
            'inferred_only':  'pln_inferred.metta',
            'visualization':  'pln_atomspace.html',
        },
        'metta_dir':        'pln_metta',
        'metta_categories': cat_manifest,
        'stv_schema': {
            'strength':   'LLM extraction confidence (0–1)',
            'confidence': 'N/(N+1) where N = number of evidence papers',
        },
    }
    (folder / 'manifest.json').write_text(_json.dumps(manifest, indent=2), encoding='utf-8')
