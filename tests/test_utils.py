from unittest.mock import MagicMock, patch

from dara.utils import get_entries_in_chemsys_mp


def test_get_entries_in_chemsys_mp_pins_legacy_thermo_type():
    expected_entries = [object()]
    rester = MagicMock()
    rester.get_entries_in_chemsys.return_value = expected_entries

    with patch("mp_api.client.MPRester") as rester_cls:
        rester_cls.return_value.__enter__.return_value = rester
        entries = get_entries_in_chemsys_mp("Fe-O")

    assert entries == expected_entries
    rester.get_entries_in_chemsys.assert_called_once_with(
        "Fe-O",
        additional_criteria={"thermo_types": ["GGA_GGA+U"]},
    )
