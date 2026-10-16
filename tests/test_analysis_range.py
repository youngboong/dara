import asyncio
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException, UploadFile

from dara.analysis_range import analysis_mask, normalize_excluded_ranges, validate_analysis_range
from dara.cif import Cif
from dara.generate_control_file import generate_control_file
from dara.jobs import PhaseSearchMaker
from dara.server import api_router
from dara.structure_db import CODDatabase
from dara.xrd import XRDData


def test_ranges_merge_and_mask_including_boundaries():
    assert normalize_excluded_ranges([[40, 43], [20, 23], [22, 25]]) == [[20, 25], [40, 43]]
    assert analysis_mask([10, 20, 25, 26, 40, 44, 90], 15, 80, [[20, 25], [40, 43]]).tolist() == [False, False, False, True, False, True, False]


@pytest.mark.parametrize("value", ['wrong', [[3, 2]], [[1, float('nan')]], [[-1, 3]], [[1]], [[1, 181]]])
def test_invalid_exclusions_rejected(value):
    with pytest.raises(ValueError):
        normalize_excluded_ranges(value)


def test_range_validation_rejects_empty_or_reversed_fit():
    angles = np.arange(10, 31)
    for args in [(40, 50, []), (20, 10, []), (10, 30, [[10, 29]])]:
        with pytest.raises(ValueError):
            validate_analysis_range(angles, *args)
    assert validate_analysis_range(angles, 12, 28, '[[20,22]]') == {
        'wmin': 12, 'wmax': 28, 'excluded_ranges': [[20, 22]],
    }


def test_native_cut_preserves_measured_pattern(tmp_path):
    pattern = tmp_path / 'sample.xy'
    data = np.column_stack((np.arange(10, 51), np.arange(1, 42)))
    np.savetxt(pattern, data)
    source = tmp_path / 'source'
    source.mkdir()
    profile = source / 'test.geq'
    profile.write_bytes(b'test profile')
    phase = tmp_path / 'test.str'
    phase.write_text('PHASE=Test SpacegroupNo=225 A=0.4\n')
    result = generate_control_file(pattern, [phase], profile, wmin=12, wmax=48, excluded_ranges=[[20, 23], [40, 42]])
    control = result.read_text()
    assert 'WMIN=12' in control and 'WMAX=48' in control
    assert 'CUT[1]=20:23' in control and 'CUT[2]=40:42' in control
    np.testing.assert_array_equal(np.loadtxt(pattern), data)


def test_peak_detection_does_not_bridge_excluded_data(monkeypatch):
    from dara import peak_detection

    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame({'2theta': [kwargs['pattern'][0, 0]], 'intensity': [1], 'b1': [0.001], 'b2': [0]})
    monkeypatch.setattr(peak_detection, 'EflechWorker', lambda: SimpleNamespace(run_peak_detection=run))
    data = np.column_stack((np.arange(10, 31), np.ones(21)))
    peaks = peak_detection.detect_peaks(data, wmin=12, wmax=28, excluded_ranges=[[18, 21]])
    assert len(calls) == 2
    assert calls[0]['pattern'][:, 0].tolist() == list(range(12, 18))
    assert calls[1]['pattern'][:, 0].tolist() == list(range(22, 29))
    assert peaks['2theta'].tolist() == [12, 22]


def test_search_peak_list_respects_explicit_limit_and_exclusion(monkeypatch):
    from dara.search import tree

    calls = []
    def detect(*args, **kwargs):
        calls.append(kwargs)
        return pd.DataFrame({'2theta': [15, 20, 30, 40], 'intensity': [10] * 4, 'b1': [0.001] * 4})
    monkeypatch.setattr(tree, 'detect_peaks', detect)
    target = SimpleNamespace(
        enable_angular_cut=False, pattern_path='fake.xy', wavelength='Cu', instrument_profile='Aeris',
        refinement_params={'wmin': 18, 'wmax': 35, 'excluded_ranges': [[19, 21]]},
        phase_params={'b1': 0.001},
    )
    peaks = tree.SearchTree._detect_peak_in_pattern(target)
    assert peaks['2theta'].tolist() == [30]
    assert target.peak_obs[:, 0].tolist() == [30]
    assert calls[0]['wmax'] == 35


def test_diagnostic_markers_ignore_excluded_peaks(monkeypatch):
    from dara import peak_detection
    from dara.refine import _attach_peak_markers

    monkeypatch.setattr(peak_detection, 'detect_peaks', lambda *args, **kwargs: pd.DataFrame({
        '2theta': [20, 30], 'intensity': [100, 100],
    }))
    result = SimpleNamespace(
        peak_data=pd.DataFrame({'2theta': [30], 'intensity': [100]}),
        plot_data=SimpleNamespace(x=np.arange(10, 41, 0.1),
                                 y_obs=np.ones(310), y_calc=np.ones(310), y_bkg=np.zeros(310)),
        lst_data=SimpleNamespace(rwp=3),
    )
    _attach_peak_markers(result, 'fake.xy', 'Cu', 'Aeris', use_residual=False,
                         refinement_params={'excluded_ranges': [[19, 21]]})
    assert result.refinement_metrics.missing_peaks is None


def _stored_record():
    pattern = XRDData(np.arange(10, 51), np.ones(41))
    job = PhaseSearchMaker(name='original.xrdml', phase_predictor=None).make(
        xrd_data=pattern, cif_dbs=[CODDatabase()], precursors=['Fe2O3'], elements=['Fe', 'O'],
        analysis_mode='elemental', search_kwargs={
            'instrument_profile': 'PW3040-FDS-ADS-Xcelerator', 'wavelength': 'Cu',
            'refinement_params': {'wmin': 12, 'wmax': 48, 'excluded_ranges': [[20, 23]]},
        },
        final_refinement_params={'gewicht': 'ISOTROPIC', 'lattice_range': 0.012},
        additional_cif_params={'lattice_range': 0.017},
        additional_cifs=[Cif.from_file(Path(__file__).parent / 'test_data' / 'BiFeO3.cif')],
    )
    raw = job.as_dict()
    raw['function_kwargs']['cif_dbs'][0]['api_key'] = 'must-not-leak'
    return {'index': 99, 'user': 'tester', 'job': raw}


def _mock_store(monkeypatch, record):
    class Store:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def query_one(self, criteria): return record if criteria['index'] == 99 else None
    monkeypatch.setattr(api_router, 'get_worker_store', Store)


def test_clone_settings_allowlist_and_original_pattern_reuse(monkeypatch):
    record = _stored_record()
    _mock_store(monkeypatch, record)
    copied = asyncio.run(api_router.rerun_settings(99))
    assert 'must-not-leak' not in json.dumps(copied)
    assert copied['settings']['two_theta_min'] == 12
    assert copied['settings']['excluded_ranges'] == [[20, 23]]
    assert copied['additional_cif_count'] == 1
    queued = []
    monkeypatch.setattr(api_router, 'add_job_to_queue', lambda job, user: queued.append(job) or 100)
    response = asyncio.run(api_router.submit(**{
        'source_task_id': 99, 'user': 'tester', 'analysis_mode': 'elemental',
        'elements': '["Fe","O"]', 'use_cod': True, 'use_mp': False,
        'two_theta_min': 15, 'two_theta_max': 45, 'excluded_ranges': '[[24,26]]',
    }))
    assert response['wf_id'] == 100
    kwargs = queued[0].function_kwargs
    np.testing.assert_array_equal(kwargs['xrd_data'].angles, np.arange(10, 51))
    assert kwargs['search_kwargs']['refinement_params'] == {'wmin': 15, 'wmax': 45, 'excluded_ranges': [[24, 26]]}
    assert kwargs['search_kwargs']['enable_angular_cut'] is False
    assert kwargs['final_refinement_params'] == {'gewicht': 'ISOTROPIC', 'lattice_range': 0.012}
    assert kwargs['additional_cif_params'] == {'lattice_range': 0.017}
    assert len(kwargs['additional_cifs']) == 1
    assert isinstance(kwargs['additional_cifs'][0], Cif)
    assert queued[0].metadata['submission_settings']['source_task_id'] == 99
    assert record['job']['function_kwargs']['search_kwargs']['refinement_params']['wmin'] == 12
    with pytest.raises(HTTPException) as error:
        asyncio.run(api_router.rerun_settings(404))
    assert error.value.status_code == 404


def test_submit_rejects_range_excluding_all_data_before_queue(monkeypatch):
    _mock_store(monkeypatch, _stored_record())
    monkeypatch.setattr(api_router, 'add_job_to_queue', lambda *args, **kwargs: pytest.fail('Should not queue invalid input'))
    with pytest.raises(HTTPException) as error:
        asyncio.run(api_router.submit(source_task_id=99, user='tester', excluded_ranges='[[10,50]]'))
    assert error.value.status_code == 400
    assert 'fewer than three' in error.value.detail


def test_clone_can_replace_pattern_and_not_reuse_cifs(monkeypatch):
    _mock_store(monkeypatch, _stored_record())
    queued = []
    monkeypatch.setattr(api_router, 'add_job_to_queue', lambda job, user: queued.append(job) or 100)
    upload = UploadFile(filename='replacement.xy', file=BytesIO(b'20 1\n21 2\n22 3\n23 2\n24 1\n'))
    asyncio.run(api_router.submit(
        source_task_id=99, user='tester', pattern_file=upload, analysis_mode='elemental',
        elements='["Fe","O"]', use_cod=True, use_mp=False, reuse_additional_cifs=False,
    ))
    assert queued[0].name == 'replacement.xy'
    assert queued[0].function_kwargs['xrd_data'].angles.tolist() == [20, 21, 22, 23, 24]
    assert queued[0].function_kwargs['additional_cifs'] == []
