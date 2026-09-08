from pydantic import TypeAdapter

from dara.schema import PhaseSearchDocument


def test_candidate_peak_lists_allow_candidates_without_markers():
    annotation = PhaseSearchDocument.model_fields["extra_peaks"].annotation

    value = TypeAdapter(annotation).validate_python([None, [[25.1, 0.0]]])

    assert value == [None, [[25.1, 0.0]]]
