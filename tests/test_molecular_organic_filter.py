import pytest

from dara.server.peak_significance import is_molecular_organic


@pytest.mark.parametrize("formula", ["C11O3", "C10NO2", "C21O2", "C6H6", "C13O11", "PH9C3NO6"])
def test_carbon_compounds_without_metals_are_molecular(formula):
    assert is_molecular_organic(formula)


@pytest.mark.parametrize("formula", ["C", "Na2CO3", "CaCO3", "SiC", "NaP2H5O8", "P4O7", "H5N7", "Fe2O3",
                                     "NaPH3C2N3O4", "not a formula"])
def test_inorganic_and_metal_containing_phases_are_kept(formula):
    assert not is_molecular_organic(formula)
