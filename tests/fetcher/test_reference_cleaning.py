"""Layer 3 noise removal: page furniture, contents/index pages, reference entries."""
from src.fetcher.cleaner import TextCleaner, PAGE_BREAK

PROSE = ("Experimental evolution postponed aging in Drosophila populations (Rose, 1984). "
         "Hamilton (1966) showed the force of natural selection declines with age. ")
REFS = ("References and further reading Remolina, S. C., Chang, P. L., Leips, J., & Hughes, K. A. (2012). "
        "Genomic basis of aging and life-history evolution in Drosophila melangaster. Evolution, 66(11), "
        "3390e3403. https://doi.org/10.1111/j.1558-5646.2012.01710.x "
        "Rose, M. R. (1982). Antagonistic pleiotropy, dominance, and genetic variation. Heredity, 48(1), "
        "63e78. https://doi.org/10.1038/hdy.1982.7 ")


def test_reference_entries_removed_and_prose_kept():
    cleaner = TextCleaner()
    out = cleaner.clean(PROSE + REFS)
    assert cleaner.references_removed == 2
    assert "melangaster" not in out and "doi.org" not in out and "References" not in out
    assert "(Rose, 1984)" in out and "Hamilton (1966)" in out


def test_single_citation_like_sentence_is_not_removed():
    text = PROSE + "Smith, J. (2001). One stray match in prose should stay."
    assert "Smith, J." in TextCleaner().clean(text)


def _pages(*pages):
    return PAGE_BREAK.join(pages)


def test_page_furniture_removed():
    pages = [f"Running header of the book {i}\nCopyright Elsevier 2025\nTable {i} shows the content here\n{i}"
             for i in range(10)]
    out = TextCleaner()._remove_page_noise(_pages(*pages))
    assert "Running header" not in out and "Copyright" not in out
    assert all(f"Table {i} shows the content here" in out for i in range(10))


def test_short_documents_untouched():
    text = _pages("Header line here\nA", "Header line here\nB", "Header line here\nC")
    assert TextCleaner()._remove_page_noise(text) == text


def test_contents_and_index_pages_removed():
    contents = "\n".join(f"{n}.\nChapter title number {n}\n{10 + n}" for n in range(1, 12))
    index = "\n".join(f"Author{n}, A., {n}, {n + 3}e{n + 5}" for n in range(30))
    prose = lambda p: "\n".join(f"Page {p} line {i}: the force of natural selection falls with adult age."
                                 for i in range(20))
    cleaner = TextCleaner()
    out = cleaner._remove_page_noise(_pages(prose(1), contents, prose(2), index, prose(3)))
    assert cleaner.toc_pages_removed == 2
    assert "Chapter title number" not in out and "Author3" not in out
    assert out.count("force of natural selection") == 60


def test_subject_index_and_blank_pages_removed():
    index = "\n".join(f"Mutation accumulation theory {n}, {n}e{n + 3}\nwrapped subentry text" if n % 4 == 0
                      else f"Stress resistance and lifespan, {n}e{n + 2}" for n in range(30))
    blank = "This page intentionally left blank"
    prose = lambda p: "\n".join(f"Page {p} line {i}: the force of natural selection falls with adult age, 1980."
                                 for i in range(20))
    table = "\n".join(f"Gene {n} expression measured in the treated population of flies {n}" for n in range(20))
    out = TextCleaner()._remove_page_noise(_pages(prose(1), blank, index, prose(2), table, prose(3)))
    assert "Stress resistance and lifespan" not in out and blank not in out
    assert out.count("force of natural selection") == 60 and "Gene 3 expression" in out


def test_dash_glyph_repaired_only_in_affected_documents():
    affected = ("HutchinsoneGilford progeria (pp. 206e217); see 233e237, 12e14, 100e120 and 7e9. "
                "LeBlanc and McKenzie are unchanged.")
    c = TextCleaner()
    out = c._repair_dash_glyphs(affected)
    assert "Hutchinson–Gilford" in out and "206–217" in out and "LeBlanc" in out
    assert c.dashes_repaired == 6
    assert TextCleaner()._repair_dash_glyphs("HutchinsoneGilford, 2e5") == "HutchinsoneGilford, 2e5"
