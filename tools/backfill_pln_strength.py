"""Add (strength <triple> <confidence>) to per-paper edges_*.metta files from the main metta DB.
Skips unified_* folders and files already patched."""
import re
import sqlite3
from pathlib import Path

KG_ROOT = Path("data/kg_output")
DB_PATH = Path("data/triple_store_metta.db")

# Build lookup: (subject_id, relation, object_id) → confidence
print("Loading confidence values from DB...")
conn = sqlite3.connect(str(DB_PATH))
conf_map: dict[tuple, float] = {}
for row in conn.execute(
    "SELECT subject_id, relation, object_id, confidence FROM triples"
).fetchall():
    sid, rel, oid, conf = row
    # normalise IDs (DB uses MESH:C123, metta uses MESH_C123)
    sid_n = re.sub(r'[^a-zA-Z0-9_]', '_', sid or '')
    oid_n = re.sub(r'[^a-zA-Z0-9_]', '_', oid or '')
    conf_map[(sid_n, rel, oid_n)] = float(conf or 0.5)
    conf_map[(sid or '', rel, oid or '')] = float(conf or 0.5)
conn.close()
print(f"  {len(conf_map)} entries loaded")

SKIP_DIRS = {"unified_metta", "unified_neo4j", "unified_pln"}

# Regex to detect a triple line: (relation (type id) (type id))
TRIPLE_RE = re.compile(
    r'^\((\w+)\s+\((\w+)\s+(\S+)\)\s+\((\w+)\s+(\S+)\)\)\s*$'
)
STRENGTH_RE = re.compile(r'^\(strength\s+')

patched_files = 0
patched_lines = 0
skipped_files = 0

for edge_file in sorted(KG_ROOT.rglob("edges_*.metta")):
    # Skip unified folders
    parts = edge_file.parts
    if any(p in SKIP_DIRS for p in parts):
        continue

    text = edge_file.read_text(encoding="utf-8")

    # Skip if already patched
    if "(strength " in text:
        skipped_files += 1
        continue

    lines = text.splitlines(keepends=True)
    new_lines = []
    file_patched = 0

    i = 0
    while i < len(lines):
        line = lines[i]
        new_lines.append(line)

        m = TRIPLE_RE.match(line.strip())
        if m:
            rel, stype, sid, otype, oid = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            triple_expr = line.strip().rstrip()

            # Look up confidence — try normalised IDs first, then raw
            conf = (
                conf_map.get((sid, rel, oid))
                or conf_map.get((re.sub(r'[^a-zA-Z0-9_]', '_', sid), rel, re.sub(r'[^a-zA-Z0-9_]', '_', oid)))
                or 0.5
            )
            strength_line = f"(strength {triple_expr} {conf:.4f})\n"
            new_lines.append(strength_line)
            file_patched += 1

        i += 1

    if file_patched:
        edge_file.write_text("".join(new_lines), encoding="utf-8")
        patched_files += 1
        patched_lines += file_patched

print(f"\nDone.")
print(f"  Files patched : {patched_files}")
print(f"  Triples patched: {patched_lines}")
print(f"  Already patched (skipped): {skipped_files}")
