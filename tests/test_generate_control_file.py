from dara.generate_control_file import copy_instrument_files


def test_copy_instrument_files_includes_companion_ger(tmp_path):
    source_dir = tmp_path / "source"
    working_dir = tmp_path / "working"
    source_dir.mkdir()
    working_dir.mkdir()
    geq_path = source_dir / "instrument.geq"
    ger_path = source_dir / "instrument.ger"
    geq_path.write_bytes(b"compiled profile")
    ger_path.write_text("profile source")

    instrument_name = copy_instrument_files(geq_path, working_dir)

    assert instrument_name == "instrument"
    assert (working_dir / geq_path.name).read_bytes() == b"compiled profile"
    assert (working_dir / ger_path.name).read_text() == "profile source"
