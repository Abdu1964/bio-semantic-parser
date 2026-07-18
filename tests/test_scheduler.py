import hashlib
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
import yaml

# conftest.py already inserts project root + src/
from src.scheduler.scheduler import Scheduler
from src.registry.registry import SourceRegistry

# helpers

def _make_registry(tmp_path, sources):
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(yaml.dump({"sources": sources}))
    return SourceRegistry(str(cfg))


def _make_scheduler(tmp_path, sources=None):
    if sources is None:
        sources = [
            {
                "name": "pubmed",
                "type": "api",
                "base_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/",
                "search_query": "CRISPR",
                "id_field": "PMID",
                "api_key_env": "",
            }
        ]
    registry = _make_registry(tmp_path, sources)
    db_path = str(tmp_path / "state" / "src.scheduler.db")
    return Scheduler(registry, db_path)


# DB initialisation

class TestInitDb:
    def test_creates_processed_ids_table(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        conn = sqlite3.connect(sched.db_path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "processed_ids" in tables

    def test_creates_source_runs_table(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        conn = sqlite3.connect(sched.db_path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "source_runs" in tables

    def test_db_directory_created_automatically(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c" / "sched.db"
        registry = _make_registry(tmp_path, [])
        Scheduler(registry, str(nested))
        assert nested.exists()


# last_run / set_last_run

class TestLastRun:
    def test_get_last_run_returns_7_days_ago_for_unknown_source(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        result = sched.get_last_run("brand_new_source")
        expected = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        assert result == expected

    def test_set_and_get_last_run_roundtrip(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        sched.set_last_run("pubmed")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert sched.get_last_run("pubmed") == today

    def test_set_last_run_idempotent(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        sched.set_last_run("pubmed")
        sched.set_last_run("pubmed")  # should not duplicate
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert sched.get_last_run("pubmed") == today


# is_processed / mark_processed / should_process

class TestProcessedIds:
    def test_new_id_not_processed(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        assert sched.is_processed("novel-doc-abc") is False

    def test_mark_then_is_processed(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        sched.mark_processed("doc-001", "pubmed", "standard")
        assert sched.is_processed("doc-001") is True

    def test_mark_processed_idempotent(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        sched.mark_processed("doc-001", "pubmed", "standard")
        sched.mark_processed("doc-001", "pubmed", "standard")  # INSERT OR IGNORE
        conn = sqlite3.connect(sched.db_path)
        count = conn.execute("SELECT COUNT(*) FROM processed_ids WHERE id=?", ("doc-001",)).fetchone()[0]
        conn.close()
        assert count == 1

    def test_should_process_returns_true_and_marks(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        assert sched.should_process("new-doc", "pubmed", "standard") is True
        assert sched.is_processed("new-doc") is True

    def test_should_process_returns_false_for_duplicate(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        sched.mark_processed("dup-doc", "pubmed", "standard")
        assert sched.should_process("dup-doc", "pubmed", "standard") is False

    def test_get_processed_by_source_empty(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        assert sched.get_processed_by_source("pubmed") == []

    def test_get_processed_by_source_returns_rows(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        sched.mark_processed("p1", "pubmed", "standard")
        sched.mark_processed("p2", "pubmed", "standard")
        sched.mark_processed("b1", "biorxiv", "standard")
        rows = sched.get_processed_by_source("pubmed")
        ids = {r[0] for r in rows}
        assert ids == {"p1", "p2"}


# resolve_id

class TestResolveId:
    def test_with_id_field_returns_content_and_standard(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        source = {"name": "pubmed", "id_field": "PMID"}
        doc_id, id_type = sched.resolve_id(source, "12345678")
        assert doc_id == "12345678"
        assert id_type == "standard"

    def test_without_id_field_returns_sha256(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        source = {"name": "biorxiv"}
        content = "some content"
        doc_id, id_type = sched.resolve_id(source, content)
        assert id_type == "sha256"
        expected = hashlib.sha256(content.encode()).hexdigest()
        assert doc_id == expected

    def test_no_content_no_id_field_uses_source_name(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        source = {"name": "geo"}
        doc_id, id_type = sched.resolve_id(source)
        assert id_type == "sha256"
        expected = hashlib.sha256("geo".encode()).hexdigest()
        assert doc_id == expected


# _build_fetch_url

class TestBuildFetchUrl:
    @pytest.fixture
    def sched(self, tmp_path):
        return _make_scheduler(tmp_path)

    def test_pubmed(self, sched):
        source = {"name": "pubmed", "base_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"}
        url = sched._build_fetch_url(source, "12345678")
        assert "db=pubmed" in url
        assert "12345678" in url

    def test_pmc(self, sched):
        source = {"name": "pmc", "base_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"}
        url = sched._build_fetch_url(source, "PMC123")
        assert "db=pmc" in url
        assert "PMC123" in url

    def test_geo(self, sched):
        source = {"name": "geo", "base_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"}
        url = sched._build_fetch_url(source, "GDS999")
        assert "db=gds" in url

    def test_biorxiv(self, sched):
        source = {"name": "biorxiv", "base_url": "https://api.biorxiv.org/details/biorxiv/"}
        url = sched._build_fetch_url(source, "10.1101/2024.01.01")
        assert "10.1101/2024.01.01" in url

    def test_clinicaltrials(self, sched):
        source = {"name": "clinicaltrials", "base_url": "https://clinicaltrials.gov/api/v2/studies/"}
        url = sched._build_fetch_url(source, "NCT00000001")
        assert "NCT00000001" in url

    def test_unknown_source_returns_empty_string(self, sched):
        source = {"name": "unknown_db", "base_url": "https://example.com/"}
        assert sched._build_fetch_url(source, "X") == ""


# get_new_ids routing

class TestGetNewIds:
    """Verifies that get_new_ids dispatches to the correct private method."""

    @pytest.fixture
    def sched(self, tmp_path):
        return _make_scheduler(tmp_path)

    def test_pubmed_calls_query_ncbi(self, sched):
        source = {"name": "pubmed", "base_url": "https://x/", "search_query": "q", "api_key_env": ""}
        with patch.object(sched, "_query_ncbi", return_value=["111", "222"]) as mock:
            result = sched.get_new_ids(source, "2024-01-01")
        mock.assert_called_once_with(source, "2024-01-01")
        assert result == ["111", "222"]

    def test_pmc_calls_query_ncbi(self, sched):
        source = {"name": "pmc", "base_url": "https://x/", "search_query": "q", "api_key_env": ""}
        with patch.object(sched, "_query_ncbi", return_value=[]) as mock:
            sched.get_new_ids(source, "2024-01-01")
        mock.assert_called_once()

    def test_geo_calls_query_ncbi(self, sched):
        source = {"name": "geo", "base_url": "https://x/", "search_query": "q", "api_key_env": ""}
        with patch.object(sched, "_query_ncbi", return_value=[]) as mock:
            sched.get_new_ids(source, "2024-01-01")
        mock.assert_called_once()

    def test_biorxiv_calls_query_biorxiv(self, sched):
        source = {"name": "biorxiv", "base_url": "https://api.biorxiv.org/", "search_query": "q"}
        with patch.object(sched, "_query_biorxiv", return_value=["10.1101/a"]) as mock:
            result = sched.get_new_ids(source, "2024-01-01")
        mock.assert_called_once_with(source, "2024-01-01")
        assert result == ["10.1101/a"]

    def test_clinicaltrials_calls_query_clinicaltrials(self, sched):
        source = {"name": "clinicaltrials", "base_url": "https://ct.gov/", "search_query": "q"}
        with patch.object(sched, "_query_clinicaltrials", return_value=["NCT0001"]) as mock:
            result = sched.get_new_ids(source, "2024-01-01")
        mock.assert_called_once()
        assert result == ["NCT0001"]

    def test_unknown_source_returns_empty_list(self, sched):
        source = {"name": "some_other_db", "search_query": "q"}
        result = sched.get_new_ids(source, "2024-01-01")
        assert result == []


# _query_ncbi (mocked HTTP)

class TestQueryNcbi:
    def test_returns_id_list(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        source = {
            "name": "pubmed",
            "base_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/",
            "search_query": "CRISPR",
            "api_key_env": "",
        }
        fake_response = MagicMock()
        fake_response.json.return_value = {
            "esearchresult": {"idlist": ["38000001", "38000002"]}
        }
        fake_response.raise_for_status = MagicMock()

        with patch("src.scheduler.scheduler.requests.get", return_value=fake_response):
            ids = sched._query_ncbi(source, "2024-01-01")

        assert ids == ["38000001", "38000002"]

    def test_appends_api_key_when_env_set(self, tmp_path, monkeypatch):
        sched = _make_scheduler(tmp_path)
        monkeypatch.setenv("NCBI_API_KEY", "MYKEY123")
        source = {
            "name": "pubmed",
            "base_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/",
            "search_query": "CRISPR",
            "api_key_env": "NCBI_API_KEY",
        }
        fake_response = MagicMock()
        fake_response.json.return_value = {"esearchresult": {"idlist": []}}
        fake_response.raise_for_status = MagicMock()

        captured = {}
        def capture_get(url, **kwargs):
            captured["url"] = url
            return fake_response

        with patch("src.scheduler.scheduler.requests.get", side_effect=capture_get):
            sched._query_ncbi(source, "2024-01-01")

        assert "MYKEY123" in captured["url"]


# _query_biorxiv

class TestQueryBiorxiv:
    def test_extracts_dois(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        source = {"name": "biorxiv", "base_url": "https://api.biorxiv.org/details/biorxiv/"}
        fake_response = MagicMock()
        fake_response.json.return_value = {
            "collection": [
                {"doi": "10.1101/2024.01.01.000001"},
                {"doi": "10.1101/2024.01.02.000002"},
                {"other": "no doi here"},
            ]
        }
        fake_response.raise_for_status = MagicMock()

        with patch("src.scheduler.scheduler.requests.get", return_value=fake_response):
            dois = sched._query_biorxiv(source, "2024-01-01")

        assert dois == ["10.1101/2024.01.01.000001", "10.1101/2024.01.02.000002"]


# _query_clinicaltrials

class TestQueryClinicalTrials:
    def test_extracts_nct_ids(self, tmp_path):
        sched = _make_scheduler(tmp_path)
        source = {"name": "clinicaltrials", "base_url": "https://clinicaltrials.gov/api/v2/studies"}
        fake_response = MagicMock()
        fake_response.json.return_value = {
            "studies": [
                {"protocolSection": {"identificationModule": {"nctId": "NCT00000001"}}},
                {"protocolSection": {"identificationModule": {"nctId": "NCT00000002"}}},
                {"protocolSection": {}},  # missing identificationModule
            ]
        }
        fake_response.raise_for_status = MagicMock()

        with patch("src.scheduler.scheduler.requests.get", return_value=fake_response):
            nct_ids = sched._query_clinicaltrials(source, "2024-01-01")

        assert "NCT00000001" in nct_ids
        assert "NCT00000002" in nct_ids
        assert len(nct_ids) == 2


# _scan_file_inbox

class TestScanFileInbox:
    def test_processes_matching_file(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        pdf_file = inbox / "paper.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test content")

        source = {"name": "local_pdf", "type": "file", "watch_dir": str(inbox), "format": "pdf"}
        sched = _make_scheduler(tmp_path, [source])

        mock_fetcher = MagicMock()
        results = sched._scan_file_inbox(source, mock_fetcher)

        assert results["processed"] == 1
        assert results["skipped"] == 0
        assert results["errors"] == 0
        mock_fetcher.fetch.assert_called_once()

    def test_skips_wrong_extension(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "paper.txt").write_text("not a pdf")

        source = {"name": "local_pdf", "type": "file", "watch_dir": str(inbox), "format": "pdf"}
        sched = _make_scheduler(tmp_path, [source])

        mock_fetcher = MagicMock()
        results = sched._scan_file_inbox(source, mock_fetcher)
        assert results["processed"] == 0
        mock_fetcher.fetch.assert_not_called()

    def test_skips_already_processed_file(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        pdf_file = inbox / "paper.pdf"
        content = b"%PDF-1.4 test content"
        pdf_file.write_bytes(content)

        source = {"name": "local_pdf", "type": "file", "watch_dir": str(inbox), "format": "pdf"}
        sched = _make_scheduler(tmp_path, [source])

        doc_id = hashlib.sha256(content).hexdigest()
        sched.mark_processed(doc_id, "local_pdf", "sha256")

        mock_fetcher = MagicMock()
        results = sched._scan_file_inbox(source, mock_fetcher)
        assert results["skipped"] == 1
        mock_fetcher.fetch.assert_not_called()

    def test_missing_watch_dir_returns_zeros(self, tmp_path):
        source = {
            "name": "local_pdf",
            "type": "file",
            "watch_dir": str(tmp_path / "does_not_exist"),
            "format": "pdf",
        }
        sched = _make_scheduler(tmp_path, [source])
        mock_fetcher = MagicMock()
        results = sched._scan_file_inbox(source, mock_fetcher)
        assert results == {"processed": 0, "skipped": 0, "errors": 0}

    def test_fetch_exception_counted_as_error(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        pdf_file = inbox / "paper.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 boom")

        source = {"name": "local_pdf", "type": "file", "watch_dir": str(inbox), "format": "pdf"}
        sched = _make_scheduler(tmp_path, [source])

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.side_effect = RuntimeError("parse error")
        results = sched._scan_file_inbox(source, mock_fetcher)
        assert results["errors"] == 1


# run() integration

class TestRun:
    def test_run_skips_source_without_search_query(self, tmp_path):
        sources = [{"name": "pubmed", "type": "api", "base_url": "https://x/"}]
        sched = _make_scheduler(tmp_path, sources)
        mock_fetcher = MagicMock()

        with patch.object(sched, "get_new_ids", return_value=[]):
            results = sched.run(mock_fetcher)

        # no search_query → skipped, fetcher never called
        mock_fetcher.fetch.assert_not_called()
        assert results["processed"] == 0

    def test_run_processes_new_ids(self, tmp_path):
        sources = [
            {
                "name": "pubmed",
                "type": "api",
                "base_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/",
                "search_query": "CRISPR",
                "id_field": "PMID",
            }
        ]
        sched = _make_scheduler(tmp_path, sources)
        mock_fetcher = MagicMock()

        with patch.object(sched, "get_new_ids", return_value=["111", "222"]):
            results = sched.run(mock_fetcher)

        assert results["processed"] == 2
        assert results["skipped"] == 0
        assert mock_fetcher.fetch.call_count == 2

    def test_run_skips_already_processed_ids(self, tmp_path):
        sources = [
            {
                "name": "pubmed",
                "type": "api",
                "base_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/",
                "search_query": "CRISPR",
                "id_field": "PMID",
            }
        ]
        sched = _make_scheduler(tmp_path, sources)
        sched.mark_processed("111", "pubmed", "standard")

        mock_fetcher = MagicMock()
        with patch.object(sched, "get_new_ids", return_value=["111", "222"]):
            results = sched.run(mock_fetcher)

        assert results["processed"] == 1
        assert results["skipped"] == 1

    def test_run_counts_errors(self, tmp_path):
        sources = [
            {
                "name": "pubmed",
                "type": "api",
                "base_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/",
                "search_query": "CRISPR",
                "id_field": "PMID",
            }
        ]
        sched = _make_scheduler(tmp_path, sources)
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.side_effect = RuntimeError("network fail")

        with patch.object(sched, "get_new_ids", return_value=["111"]):
            results = sched.run(mock_fetcher)

        assert results["errors"] == 1

    def test_run_file_source_delegates_to_scan_file_inbox(self, tmp_path):
        sources = [
            {"name": "local_pdf", "type": "file", "watch_dir": "/some/path", "format": "pdf"}
        ]
        sched = _make_scheduler(tmp_path, sources)
        mock_fetcher = MagicMock()

        with patch.object(
            sched, "_scan_file_inbox", return_value={"processed": 3, "skipped": 1, "errors": 0}
        ) as mock_scan:
            results = sched.run(mock_fetcher)

        mock_scan.assert_called_once()
        assert results["processed"] == 3
        assert results["skipped"] == 1

    def test_run_updates_last_run_after_api_source(self, tmp_path):
        sources = [
            {
                "name": "pubmed",
                "type": "api",
                "base_url": "https://x/",
                "search_query": "q",
                "id_field": "PMID",
            }
        ]
        sched = _make_scheduler(tmp_path, sources)
        mock_fetcher = MagicMock()

        with patch.object(sched, "get_new_ids", return_value=[]):
            sched.run(mock_fetcher)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert sched.get_last_run("pubmed") == today
