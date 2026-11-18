"""The corpus check reads each file as Layer 3 does, not as raw markup."""
from tools import check_corpus


def test_html_and_json_read_through_their_handlers(tmp_path, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_reader(name, *a, **k):
        if name == "src.fetcher.document_reader":
            raise ImportError(name)
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_reader)
    page = tmp_path / "paper.html"
    page.write_text("<html><nav>Home</nav><p>RTD-1 suppressed CCL7.</p><script>x()</script></html>")
    data = tmp_path / "paper.json"
    data.write_text('{"text": "RTD-1 suppressed CCL7."}')
    html = check_corpus._read(page)
    assert "RTD-1 suppressed CCL7." in html and "<" not in html          # text, not markup
    assert check_corpus._read(data) == "RTD-1 suppressed CCL7."          # value, not the JSON
