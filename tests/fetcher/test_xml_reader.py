"""The PMC XML reader keeps each paragraph exactly once."""
from src.fetcher.handlers import XMLHandler
from src.quality import repeated_share


def _read(body: str, back: str = "") -> str:
    return XMLHandler().extract(f"<article><front><abstract><p>Rapamycin extends lifespan.</p></abstract></front>"
                                f"<body>{body}</body><back>{back}</back></article>")


def test_nested_sections_read_once():
    text = _read("<sec><title>Results</title><p>Parent paragraph about mTOR signalling in aged mice.</p>"
                 "<sec><title>Lifespan</title><p>Child paragraph about median lifespan extension.</p></sec></sec>")
    assert text.count("Child paragraph") == 1 and text.count("Parent paragraph") == 1
    assert text.index("Parent paragraph") < text.index("Lifespan") < text.index("Child paragraph")


def test_body_without_sections_is_read():
    text = _read("<p>Commentary paragraph one.</p><p>Commentary paragraph two.</p>")
    assert "Commentary paragraph one." in text and "Commentary paragraph two." in text


def test_figure_captions_kept_references_skipped():
    text = _read("<sec><title>Results</title><fig><caption><p>IL6 is induced by IL-1B.</p></caption></fig></sec>",
                 back="<ref-list><ref><mixed-citation>Smith J. 2010.</mixed-citation></ref></ref-list>"
                      "<ack><p>We thank the core facility.</p></ack>")
    assert "IL6 is induced by IL-1B." in text
    assert "Smith J." not in text and "core facility" not in text


def test_long_paper_has_no_repeated_text():
    secs = "".join(f"<sec><title>Part {i}</title><p>Section {i} reports that gene G{i} regulates pathway P{i} "
                   f"in human fibroblasts under stress conditions.</p><sec><title>Detail {i}</title>"
                   f"<p>Subsection {i} shows that knockdown of G{i} reduces the expression of target T{i} "
                   f"in the same fibroblasts.</p></sec></sec>" for i in range(20))
    assert repeated_share(_read(secs)) == 0
