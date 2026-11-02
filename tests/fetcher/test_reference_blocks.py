"""Reference lists are removed by shape, in any citation style; body text and bylines stay."""
from src.fetcher.cleaner import TextCleaner

BODY = ("RTD-1 suppressed IL6 and CXCL8 expression in stimulated fibroblasts (Smith et al., 2019; Lee and Park, 2020). "
        "Liver X receptors inhibit NF-kB activation in macrophages (Joseph et al., 2003). ") * 6


def _numbered(n):
    return " ".join(f"{i}. Ahrens D, Koch AE, Pope RM, Stein M, Niedbala MJ. Expression of matrix "
                    f"metalloproteinase {i} in rheumatoid arthritis. Arthritis Rheum 39: 1576–1587, 1996."
                    for i in range(1, n + 1))


def _author_year(n):
    return " ".join(f"Fulco, C.P., Nasser, J., Jones, T.R., Munson, G., Bergman, D.T. ({2000 + i}). Activity-by-contact "
                    f"model of enhancer–promoter regulation. Nat. Genet. {i}, 1664–1669." for i in range(n))


def test_numbered_reference_list_removed():
    out = TextCleaner().clean(BODY + "REFERENCES " + _numbered(20))
    assert "Ahrens" not in out and "Arthritis Rheum" not in out
    assert "RTD-1 suppressed IL6" in out


def test_author_year_reference_list_removed():
    out = TextCleaner().clean(BODY + "References " + _author_year(20))
    assert "Fulco, C.P." not in out and "RTD-1 suppressed IL6" in out


def test_body_with_citations_and_byline_kept():
    byline = "Prasad Tongaonkar, Vasu Punj, Akshay Subramanian, Dat Q. Tran, Katie K. Trinh. "
    text = byline + BODY
    assert TextCleaner().clean(text) == TextCleaner().clean(text.replace("References", ""))
    assert "Dat Q. Tran" in TextCleaner().clean(text)
    assert "(Smith et al., 2019; Lee and Park, 2020)" in TextCleaner().clean(text)
