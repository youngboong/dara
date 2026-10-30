import os

from dara.server.utils import DaraMontyStore, _storage_configured


def _store(path):
    return DaraMontyStore(database_name="dara", collection_name="jobs", database_path=str(path),
                          storage="sqlite", key=["uuid", "index"])


def test_existing_identical_storage_config_is_not_rewritten(tmp_path):
    store = _store(tmp_path)
    store.connect()
    store.update({"uuid": "a", "index": 1, "status": "READY"})
    cfg = tmp_path / "monty.storage.cfg"
    assert _storage_configured(str(tmp_path), "sqlite", store.storage_kwargs)
    os.utime(cfg, (1_000_000, 1_000_000))

    second = _store(tmp_path)
    second.connect()
    assert os.stat(cfg).st_mtime == 1_000_000  # not rewritten
    assert second.query_one({"uuid": "a"})["status"] == "READY"


def test_damaged_storage_config_is_repaired(tmp_path):
    _store(tmp_path).connect()
    (tmp_path / "monty.storage.cfg").write_text("", encoding="utf-8")
    assert not _storage_configured(str(tmp_path), "sqlite", {"use_bson": True, "mongo_version": "4.0"})
    store = _store(tmp_path)
    store.connect()
    assert _storage_configured(str(tmp_path), "sqlite", store.storage_kwargs)
