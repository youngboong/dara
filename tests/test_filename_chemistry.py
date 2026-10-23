import pytest

from dara.filename_chemistry import suggest_filename_chemistry


@pytest.mark.parametrize('filename, expected', [
    ('BiFeO3_800C_60min.xrdml', ['Bi', 'Fe', 'O']),
    ('Cr2O3_CaF2_CaCO3_960C.raw', ['Cr', 'O', 'Ca', 'F', 'C']),
    ('2Fe3O4-3Y2O3_1000C_60min.xrdml', ['Fe', 'O', 'Y']),
    ('NiO_0p7028-La(OH)3_0p1010-ZnO_0p2000--8min.xrdml', ['Ni', 'O', 'La', 'H', 'Zn']),
    ('NH4H2PO4-Na2CO3_200C_60min.xy', ['N', 'H', 'P', 'O', 'Na', 'C']),
    ('sample_800_C_60_s_2min_RT_10K.raw', []),
    ('C_S_Ni_800C.xy', []),
    ('SCAN_No2_sample123_800C.xy', []),
    ('sampleBiFeO3_800C.xy', []),
    ('La1-xSrxMnO3.xy', []),
    ('Bad2O3_Fe(OH_800C.xy', []),
    ('sample_Li0.5Ni0.5O_25C.xy', ['Li', 'Ni', 'O']),
    ('C:/BiFeO3/sample_800C.xy', []),
    ('sample_800°C_60min.xy', []),
])
def test_conservative_filename_elements(filename, expected):
    assert suggest_filename_chemistry(filename)['elements'] == expected


def test_deduplicates_formulas_and_rejects_zero_counts():
    result = suggest_filename_chemistry('BiFeO3_BiFeO3_Bi0O3.xy')
    assert result['formulas'] == ['BiFeO3']
