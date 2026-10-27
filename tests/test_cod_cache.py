"""Offline tests for the persistent COD CIF cache and partial-download handling."""

import importlib.util
import threading
from pathlib import Path

import pytest

from dara import structure_db
from dara.server.fit_cautions import search_cautions
from dara.structure_db import (
    CODDatabase,
    CODDownloadError,
    atomic_write_text,
    cod_cache_dir,
    is_valid_cif_file,
)

CIF_TEXT = """data_{id}
_cell_length_a 4.2
_cell_length_b 4.2
_cell_length_c 4.2
"""


class FakeCif:
    def __init__(self, cod_id):
        self.cod_id = cod_id

    def __str__(self):
        return CIF_TEXT.format(id=self.cod_id)


def write_valid(folder, cod_id):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{cod_id}.cif").write_text(CIF_TEXT.format(id=cod_id), encoding="utf-8")


@pytest.fixture
def db(monkeypatch, tmp_path):
    """A COD database with no local copy, no delays and a temporary cache."""
    monkeypatch.setenv("DARA_COD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(CODDatabase, "REQUEST_DELAY_S", 0)
    database = CODDatabase(path_to_cifs=tmp_path / "no_such_local_cod")
    assert not database.local_copy_found
    return database


def fake_downloader(monkeypatch, failing=(), calls=None):
    calls = calls if calls is not None else []
    lock = threading.Lock()

    def download(cod_id, max_attempts=3):
        with lock:
            calls.append(str(cod_id))
        if str(cod_id) in failing:
            raise ConnectionError("connection reset")
        return FakeCif(cod_id)

    monkeypatch.setattr(CODDatabase, "_download_cod", staticmethod(download))
    return calls


ROWS = [
    ("FeO", "1000001", 225, 0.0),
    ("Fe2O3", "1000002", 167, 0.01),
    ("Fe3O4", "1000003", 227, 0.0),
]


def test_cache_dir_env_and_default(monkeypatch, tmp_path):
    monkeypatch.setenv("DARA_COD_CACHE_DIR", str(tmp_path / "x"))
    assert cod_cache_dir() == (tmp_path / "x").resolve()
    monkeypatch.delenv("DARA_COD_CACHE_DIR")
    default = cod_cache_dir()
    assert default.is_absolute() and default.name == "dara_downloaded_cifs"


def test_cached_files_are_reused_without_download(db, monkeypatch):
    for _, code, _, _ in ROWS:
        write_valid(cod_cache_dir(), code)
    calls = fake_downloader(monkeypatch)

    file_map = db._generate_file_map(ROWS, 0.1, True)

    assert calls == []
    assert len(file_map) == 3
    assert db.last_unavailable == []
    assert "FeO_225_(cod_1000001)-0.cif" in file_map.values()


def test_only_missing_files_are_downloaded(db, monkeypatch):
    write_valid(cod_cache_dir(), "1000001")
    calls = fake_downloader(monkeypatch)

    file_map = db._generate_file_map(ROWS, 0.1, True)

    assert sorted(calls) == ["1000002", "1000003"]
    assert len(file_map) == 3
    assert all(is_valid_cif_file(path) for path in file_map)


def test_invalid_cached_file_is_redownloaded(db, monkeypatch):
    folder = cod_cache_dir()
    folder.mkdir(parents=True)
    (folder / "1000001.cif").write_text("")  # truncated
    (folder / "1000002.cif").write_text("data_1000002\n")  # no cell
    write_valid(folder, "1000003")
    calls = fake_downloader(monkeypatch)

    db._generate_file_map(ROWS, 0.1, True)

    assert sorted(calls) == ["1000001", "1000002"]
    assert is_valid_cif_file(folder / "1000001.cif")


def test_filtered_rows_are_not_downloaded(db, monkeypatch):
    rows = ROWS + [("O2", "1000009", 1, 0.0), ("Fe", "1000010", 229, 0.9)]
    calls = fake_downloader(monkeypatch)

    file_map = db._generate_file_map(rows, 0.1, True)

    assert sorted(calls) == ["1000001", "1000002", "1000003"]
    assert len(file_map) == 3


def test_partial_failure_is_skipped_and_recorded(db, monkeypatch):
    fake_downloader(monkeypatch, failing={"1000002"})

    file_map = db._generate_file_map(ROWS, 0.1, True)

    assert db.last_unavailable == ["1000002"]
    assert len(file_map) == 2
    assert all("1000002" not in name for name in file_map.values())
    assert all(Path(path).is_file() for path in file_map)


def test_all_failed_and_none_cached_raises(db, monkeypatch):
    fake_downloader(monkeypatch, failing={"1000001", "1000002", "1000003"})

    with pytest.raises(CODDownloadError, match="None of the 3"):
        db._generate_file_map(ROWS, 0.1, True)
    assert sorted(db.last_unavailable) == ["1000001", "1000002", "1000003"]


def test_all_failed_but_cached_does_not_raise(db, monkeypatch):
    for _, code, _, _ in ROWS:
        write_valid(cod_cache_dir(), code)
    fake_downloader(monkeypatch, failing={"1000001", "1000002", "1000003"})

    assert len(db._generate_file_map(ROWS, 0.1, True)) == 3


def test_download_structures_skips_failures_and_raises_if_all_fail(db, monkeypatch, tmp_path):
    fake_downloader(monkeypatch, failing={"1000002"})
    cifs = db.download_structures(
        ["1000001", "1000002"], save=True, default_folder=tmp_path / "out"
    )
    assert [c.cod_id for c in cifs] == ["1000001"]
    assert (tmp_path / "out" / "1000001.cif").is_file()
    assert db.last_unavailable == ["1000002"]

    fake_downloader(monkeypatch, failing={"1000001"})
    with pytest.raises(CODDownloadError):
        db.download_structures(["1000001"])


def test_circuit_breaker_stops_hammering_dead_server(db, monkeypatch):
    monkeypatch.setattr(CODDatabase, "MAX_WORKERS", 1)
    monkeypatch.setattr(CODDatabase, "MAX_CONSECUTIVE_FAILURES", 3)
    ids = [f"20000{i:02d}" for i in range(20)]
    calls = fake_downloader(monkeypatch, failing=set(ids))
    with pytest.raises(CODDownloadError):
        db.download_structures(ids)
    assert len(calls) == 3
    assert sorted(db.last_unavailable) == sorted(ids)


def test_get_cifs_by_chemsys_without_copy_does_not_download(db, monkeypatch):
    monkeypatch.setattr(db, "_preparsed_info", {"Fe-O": [list(r) for r in ROWS]})
    calls = fake_downloader(monkeypatch)
    assert db.get_cifs_by_chemsys("Fe-O", copy_files=False) == ["1000001", "1000002", "1000003"]
    assert calls == []


def test_get_cifs_by_chemsys_copies_cached_with_metadata(db, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "_preparsed_info", {"Fe-O": [list(r) for r in ROWS]})
    fake_downloader(monkeypatch, failing={"1000003"})
    dest = tmp_path / "job_cifs"
    db.get_cifs_by_chemsys("Fe-O", copy_files=True, dest_dir=str(dest))
    assert sorted(p.name for p in dest.glob("*.cif")) == [
        "Fe2O3_167_(cod_1000002)-10.cif",
        "FeO_225_(cod_1000001)-0.cif",
    ]
    assert db.last_unavailable == ["1000003"]


def test_atomic_write_never_leaves_truncated_file(tmp_path, monkeypatch):
    target = tmp_path / "a" / "1.cif"
    atomic_write_text(target, "complete")
    assert target.read_text() == "complete"
    assert list(target.parent.glob("*.tmp")) == []

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(structure_db.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "new content")
    assert target.read_text() == "complete"  # untouched
    assert list(target.parent.glob("*.tmp")) == []  # temp cleaned up


def test_failed_save_does_not_publish_partial_cif(db, monkeypatch):
    fake_downloader(monkeypatch)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(structure_db.os, "replace", boom)
    with pytest.raises(CODDownloadError):
        db._ensure_cached(["1000001"], cod_cache_dir())
    assert not (cod_cache_dir() / "1000001.cif").exists()


def test_references_unavailable_caution():
    cautions = search_cautions({"cod_unavailable": ["1", "2", "3"]}, {})
    assert [(c["code"], c["level"]) for c in cautions] == [("references_unavailable", "warning")]
    assert cautions[0]["message"] == (
        "3 COD reference structures could not be downloaded and were not considered."
    )
    assert search_cautions({"cod_unavailable": []}, {}) == []


def _load_seed_script():
    path = Path(__file__).parents[1] / "scripts" / "seed_cod_cache.py"
    spec = importlib.util.spec_from_file_location("seed_cod_cache", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_script(tmp_path):
    seed = _load_seed_script()
    src = tmp_path / "src"
    write_valid(src / "s1" / "dara_downloaded_cifs", "1000001")  # raw original
    renamed = src / "s1" / "dara_cifs"
    renamed.mkdir(parents=True)
    (renamed / "FeO_225_(cod_1000001)-0.cif").write_text(CIF_TEXT.format(id="renamed"))
    (renamed / "Fe2O3_167_(cod_1000002)-10.cif").write_text(CIF_TEXT.format(id="b"))
    (renamed / "Fe3O4_227_(mp_mp-19306)-1.cif").write_text(CIF_TEXT.format(id="mp"))
    (renamed / "Fe_229_(cod_1000003)-0.cif").write_text("")  # invalid
    cache = tmp_path / "cache"
    write_valid(cache, "1000002")  # already cached

    counts = seed.seed_cache([src], cache)

    assert counts["seeded"] == 1 and counts["already_cached"] == 1 and counts["invalid"] == 1
    assert sorted(p.name for p in cache.glob("*.cif")) == ["1000001.cif", "1000002.cif"]
    # the raw original wins over the renamed copy
    assert "data_1000001" in (cache / "1000001.cif").read_text()
    assert seed.seed_cache([src], cache)["seeded"] == 0  # idempotent
    assert seed.cod_id_from_name(Path("Fe_229_(cod_9012761)-None.cif")) == "9012761"
    assert seed.cod_id_from_name(Path("Fe_229_(mp_mp-1)-1.cif")) is None
