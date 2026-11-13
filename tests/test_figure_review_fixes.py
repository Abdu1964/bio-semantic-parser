"""Figure storage, JATS namespaces, grounding, relation cap, self-loops, pinned review images."""
import json

import pytest

from src.figures import extract
from src.figures.jats_figures import extract_jats_figures
from src.figures.vision import reader


def test_figure_dir_rejects_dot_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "FIGURES_ROOT", tmp_path)
    for bad in ("..", ".", "", " "):
        with pytest.raises(ValueError):
            extract.figures_dir(bad)
    assert extract.figures_dir("10.1/x:y") == tmp_path / "10.1_x_y"


def test_empty_figure_list_replaces_old_index(tmp_path):
    from src.figures.figure import Figure
    out = tmp_path / "doc"
    extract.save_figures([Figure(fig_id="f1", label="Figure 1", caption="old", source="pdf", image=b"x", ext="png")], out)
    extract.save_figures([], out)
    assert json.loads((out / "figures.json").read_text()) == [] and not (out / "f1.png").exists()


def test_jats_default_namespace():
    xml = ('<article xmlns="http://jats.nlm.nih.gov" xmlns:xlink="http://www.w3.org/1999/xlink"><body>'
           '<fig id="f1"><label>Figure 1</label><caption><p>IL6 rises</p></caption>'
           '<graphic xlink:href="g1"/></fig></body></article>')
    figs = extract_jats_figures(xml, [("g1.png", b"PNG")])
    assert len(figs) == 1 and figs[0].label == "Figure 1" and figs[0].image == b"PNG"


def _rel(s, o):
    return {"extraction_viable": True, "subject_name": s, "subject_type": "CELL_TYPE", "relation": "associates_with",
            "object_name": o, "object_type": "CELL_TYPE", "reasoning": "the bar chart in this panel compares these two cell populations directly"}


def test_reader_cap_grounding_and_signed_names(monkeypatch):
    monkeypatch.setattr(reader, "figure_text", lambda image: "")
    monkeypatch.setattr(reader, "_images", lambda image: [])
    monkeypatch.setattr(reader, "_MAX_RELATIONS", 2)
    monkeypatch.setattr("src.figures.ocr.available", lambda: False)
    monkeypatch.setattr(reader, "_ask", lambda msgs: {"kind": "chart", "relations": [
        _rel("CD4+ T cells", "CD4- T cells"), _rel("CD4+ T cells", "B cells"), _rel("NK cells", "B cells")]})
    out = reader.read_figure(b"img", caption="CD4+ T cells vs CD4- T cells and B cells and NK cells")
    names = [(r.subject_name, r.object_name) for _, r in out["relations"]]
    assert names == [("CD4+ T cells", "CD4- T cells"), ("CD4+ T cells", "B cells")]      # not a self-loop; cap of 2
    monkeypatch.setattr(reader, "_ask", lambda msgs: {"kind": "chart", "relations": [_rel("TREM2", "microglia")]})
    assert reader.read_figure(b"img", caption="CD4+ T cells")["ungrounded"] == 1       # caption grounds the names


def test_review_card_keeps_its_own_figure_copy(tmp_path):
    from src.publish.human_review import append
    img = tmp_path / "figures" / "doc" / "f1.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"old image")
    run = tmp_path / "run"
    append([{"subject_name": "A", "relation": "regulates", "object_name": "B", "figure": {"id": "f1", "file": str(img)}}], run)
    img.write_bytes(b"new image")                                         # a rerun replaces the shared file
    card = json.loads((run / "human_review.jsonl").read_text().splitlines()[0])
    assert open(card["figure"]["file"], "rb").read() == b"old image"
