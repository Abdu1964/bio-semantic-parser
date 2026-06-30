import pytest
import yaml

from registry.registry import SourceRegistry


def write_config(path, sources):
    path.write_text(yaml.dump({"sources": sources}, sort_keys=False))
    return str(path)


@pytest.fixture
def sample_sources():
    return [
        {"name": "pubmed", "type": "api", "format": "xml", "id_field": "PMID"},
        {"name": "biorxiv", "type": "api", "format": "json", "id_field": "doi"},
    ]


@pytest.fixture
def config_path(tmp_path, sample_sources):
    return write_config(tmp_path / "sources.yaml", sample_sources)


def test_load_returns_sources(config_path, sample_sources):
    registry = SourceRegistry(config_path)
    assert registry.get_all_sources() == sample_sources


def test_load_missing_file_raises(tmp_path):
    missing = str(tmp_path / "does_not_exist.yaml")
    with pytest.raises(FileNotFoundError):
        SourceRegistry(missing)


def test_load_empty_sources_key(tmp_path):
    path = write_config(tmp_path / "empty.yaml", [])
    registry = SourceRegistry(path)
    assert registry.get_all_sources() == []


def test_load_no_sources_key(tmp_path):
    path = tmp_path / "nokey.yaml"
    path.write_text(yaml.dump({"other": 1}))
    registry = SourceRegistry(str(path))
    assert registry.get_all_sources() == []


def test_get_source_by_name_found(config_path):
    registry = SourceRegistry(config_path)
    source = registry.get_source_by_name("biorxiv")
    assert source is not None
    assert source["id_field"] == "doi"


def test_get_source_by_name_missing(config_path):
    registry = SourceRegistry(config_path)
    assert registry.get_source_by_name("nonexistent") is None


def test_register_source_appends_in_memory(config_path):
    registry = SourceRegistry(config_path)
    registry.register_source(
        {"name": "trials", "type": "api", "format": "json", "base_url": "https://x"}
    )
    entry = registry.get_source_by_name("trials")
    assert entry is not None
    assert entry["type"] == "api"
    assert entry["format"] == "json"
    assert entry["base_url"] == "https://x"


def test_register_source_persists_to_file(config_path):
    registry = SourceRegistry(config_path)
    registry.register_source({"name": "trials", "base_url": "https://x"})

    reloaded = SourceRegistry(config_path)
    assert reloaded.get_source_by_name("trials") is not None


def test_register_source_applies_defaults(config_path):
    registry = SourceRegistry(config_path)
    registry.register_source({"name": "bare"})
    entry = registry.get_source_by_name("bare")
    assert entry["type"] == "api"
    assert entry["format"] == "unknown"
    assert entry["base_url"] is None


def test_register_source_deduplicates(config_path):
    registry = SourceRegistry(config_path)
    before = len(registry.get_all_sources())
    registry.register_source({"name": "pubmed", "base_url": "https://changed"})

    assert len(registry.get_all_sources()) == before
    assert registry.get_source_by_name("pubmed").get("base_url") != "https://changed"


def test_register_source_dedup_not_persisted(config_path):
    registry = SourceRegistry(config_path)
    registry.register_source({"name": "pubmed"})
    reloaded = SourceRegistry(config_path)
    names = [s["name"] for s in reloaded.get_all_sources()]
    assert names.count("pubmed") == 1
