"""Reference CIF library endpoints (no network)."""

import asyncio
import json
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile

from dara.server import reference_library_router as lib

NACL = """data_NaCl
_symmetry_space_group_name_H-M 'P 1'
loop_
_symmetry_equiv_pos_as_xyz
x,y,z
_cell_length_a {a}
_cell_length_b 5.64
_cell_length_c 5.64
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
Na1 Na 0 0 0 {occ}
Na2 Na 0 0.5 0.5 1
Na3 Na 0.5 0 0.5 1
Na4 Na 0.5 0.5 0 1
Cl1 Cl 0.5 0.5 0.5 1
Cl2 Cl 0.5 0 0 1
Cl3 Cl 0 0.5 0 1
Cl4 Cl 0 0 0.5 1
"""


def cif_bytes(a="5.64", occ="1", comment=""):
    return (NACL.format(a=a, occ=occ) + comment).encode()


def upload(content: bytes, name="NaCl.cif"):
    return UploadFile(filename=name, file=BytesIO(content))


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "library_directory", lambda: tmp_path / "reference-cifs")
    return tmp_path / "reference-cifs"


def add(content, note="", name="NaCl.cif"):
    return asyncio.run(lib.add_reference_cif(upload(content, name), note))


def test_round_trip(library):
    assert asyncio.run(lib.list_reference_cifs()) == {"items": []}
    item = add(cif_bytes(), note="lab sample")
    assert len(item["id"]) == 32 and item["filename"] == "NaCl.cif"
    assert item["formula"] == "NaCl" and item["spacegroup_number"] == 225
    assert item["note"] == "lab sample" and item["validation"] == []
    assert (library / f"{item['id']}.cif").is_file()
    assert json.loads((library / "registry.json").read_text())["items"][0]["id"] == item["id"]
    listed = asyncio.run(lib.list_reference_cifs())["items"]
    assert [entry["id"] for entry in listed] == [item["id"]]

    assert asyncio.run(lib.delete_reference_cif(item["id"])) == {"deleted": item["id"]}
    assert not (library / f"{item['id']}.cif").exists()
    assert asyncio.run(lib.list_reference_cifs()) == {"items": []}


def test_newest_first(library):
    first = add(cif_bytes(comment="\n# one\n"))
    second = add(cif_bytes(comment="\n# two\n"))
    # make the order independent of clock resolution
    registry = json.loads((library / "registry.json").read_text())
    registry["items"][0]["added_at"] = "2026-01-01T00:00:00+00:00"
    registry["items"][1]["added_at"] = "2026-01-02T00:00:00+00:00"
    (library / "registry.json").write_text(json.dumps(registry))
    listed = asyncio.run(lib.list_reference_cifs())["items"]
    assert [entry["id"] for entry in listed] == [second["id"], first["id"]]


def test_duplicate_is_conflict(library):
    item = add(cif_bytes())
    response = add(cif_bytes(), name="again.cif")
    assert response.status_code == 409
    assert json.loads(response.body) == {"message": "This CIF is already in the library.", "id": item["id"]}
    assert len(asyncio.run(lib.list_reference_cifs())["items"]) == 1


def test_bad_cifs_are_rejected(library):
    for content in (b"this is not a cif", cif_bytes(a="0"), b"  \n"):
        with pytest.raises(HTTPException) as caught:
            add(content)
        assert caught.value.status_code == 400
        assert str(caught.value.detail).startswith("The CIF could not be used")
    assert asyncio.run(lib.list_reference_cifs())["items"] == []


def test_cif_without_a_buildable_structure_is_rejected(library):
    # a data block that parses but holds no atoms/symmetry used to be accepted with a warning
    with pytest.raises(HTTPException) as caught:
        add(b"data_bad\n_cell_length_a 1\n")
    assert caught.value.status_code == 400
    assert "no crystal structure could be built" in caught.value.detail
    assert asyncio.run(lib.list_reference_cifs())["items"] == []
    assert not list(library.glob("*.cif")) if library.exists() else True


def test_zero_lattice_message(library):
    with pytest.raises(HTTPException) as caught:
        add(cif_bytes(a="0"))
    assert "zero or negative lattice" in caught.value.detail


def test_too_large(library, monkeypatch):
    monkeypatch.setattr(lib, "MAX_CIF_BYTES", 100)
    with pytest.raises(HTTPException) as caught:
        add(cif_bytes())
    assert caught.value.status_code == 413


def test_other_issues_are_stored_as_warnings(library):
    item = add(cif_bytes(occ="1.3"))
    assert [issue["code"] for issue in item["validation"]] == ["occupancy_sum_gt_1"]
    assert item["validation"][0]["severity"] == "warning"


def test_delete_unknown_and_invalid_ids(library):
    for bad in ("0" * 32, "../registry", "ABC", "x" * 32):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(lib.delete_reference_cif(bad))
        assert caught.value.status_code == 404
    add(cif_bytes())
    assert (library / "registry.json").is_file()


def test_load_library_cifs_order_and_unknown(library):
    first = add(cif_bytes(comment="\n# one\n"))
    second = add(cif_bytes(a="5.7"))
    loaded = lib.load_library_cifs([second["id"], first["id"]])
    assert len(loaded) == 2
    assert loaded[0].name == loaded[1].name == "NaCl_225" or all(c.name for c in loaded)
    assert "5.7" in str(loaded[0]) and "5.64" in str(loaded[1])
    with pytest.raises(KeyError) as caught:
        lib.load_library_cifs([first["id"], "f" * 32])
    assert caught.value.args[0] == "f" * 32
    with pytest.raises(KeyError):
        lib.load_library_cifs(["../../etc/passwd"])
    assert lib.load_library_cifs([]) == []


def test_router_exposes_expected_routes():
    paths = {(tuple(sorted(route.methods)), route.path) for route in lib.router.routes}
    assert (("GET",), "/api/reference-cifs") in paths
    assert (("POST",), "/api/reference-cifs") in paths
    assert (("DELETE",), "/api/reference-cifs/{item_id}") in paths
