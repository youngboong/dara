import asyncio
import io
import json
import os
import subprocess
import sys
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import numpy as np
import pytest
from fastapi import HTTPException, UploadFile
from monty.json import MontyDecoder
from pydantic import ValidationError

from dara import instrument_profiles, task_progress
from dara.cif2str import make_peak_parameter_str
from dara.generate_control_file import copy_instrument_files
from dara.refinement_controls import RefinementControls, apply_controls, override_phase_settings
from dara.server import candidate_comparison_router as comparison, instrument_router, task_control_router as control, worker
from test_selected_refinement import _document, _refined, references


def test_controls_preserve_and_bound():
    original = {"lattice_range": .03, "gewicht": "SPHAR4", "k1": "0_0^1"}
    assert apply_controls(original, RefinementControls()) == original
    changed = apply_controls(original, RefinementControls(lattice="fixed", orientation="isotropic", peak_width="instrument_only"))
    assert original["lattice_range"] == .03
    assert changed["lattice_range"] == "fixed" and changed["gewicht"] == "0_0"
    generated = make_peak_parameter_str(changed['k1'], changed['k2'], changed['b1'], changed['gewicht'], 4)
    assert "k1=0" in generated and "B1=0" in generated
    assert "PARAM=k1" not in generated
    assert apply_controls(None, RefinementControls(lattice="refine", lattice_percent=3))["lattice_range"] == .03
    for data in ({"lattice_percent":0}, {"lattice_percent":float('nan')}, {"orientation":"arbitrary"}, {"unsafe":1}):
        with pytest.raises(ValidationError):
            RefinementControls(**data)


def test_explicit_controls_override_screening_constraints():
    from dara.refine import RefinementPhase
    original = RefinementPhase(path='test.cif', params={'k1':'0_0^0.01','lattice_range':.05})
    overrides = apply_controls({}, RefinementControls(lattice='fixed', peak_width='refine'))
    adjusted = override_phase_settings([original], overrides)[0]
    assert adjusted.params['lattice_range'] == 'fixed'
    assert adjusted.params['k1'] == '0_0^1'
    assert original.params['lattice_range'] == .05


def test_atomic_checkpoint_and_owner_guard(tmp_path, monkeypatch, references):
    monkeypatch.setenv("DARA_TASK_PROGRESS_DIR", str(tmp_path))
    monkeypatch.setenv("DARA_TASK_OWNER_PID", "other-process")
    task_progress.report_progress("bad")
    assert not (tmp_path / 'progress.json').exists()
    monkeypatch.setenv("DARA_TASK_OWNER_PID", str(os.getpid()))
    task_progress.report_progress("Final refinement", 1, 2)
    assert json.loads((tmp_path / 'progress.json').read_text())['completed'] == 1
    working = tmp_path / 'work'
    working.mkdir()
    (working / 'sample.xy').write_text('10 1\n20 5\n30 1\n')
    task_progress.checkpoint_candidate(1, _refined(references), working)
    with ZipFile(tmp_path / 'candidate_1.zip') as archive:
        decoded = MontyDecoder().process_decoded(json.loads(archive.read('refinement.json')))
        assert decoded.lst_data.rwp == 5
        assert 'sample.xy' in archive.namelist()
    assert not list(tmp_path.glob('*.tmp'))


@pytest.fixture
def record_store(monkeypatch, tmp_path):
    rows = [{"index": 1, "uuid": str(uuid4()), "status":"READY"}]
    class Store:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def query_one(self, criteria):
            return next((deepcopy(row) for row in rows if all(row.get(k)==v for k,v in criteria.items())), None)
        def update(self, record): rows[0].update(record)
    monkeypatch.setattr(control, 'get_worker_store', Store)
    monkeypatch.setattr(worker, 'get_worker_store', Store)
    monkeypatch.setattr(control, 'get_dara_server_settings', lambda: SimpleNamespace(montydb_path=str(tmp_path/'montydb')))
    return rows


def test_cancel_transitions_and_progress(record_store):
    assert asyncio.run(control.cancel(1))['status'] == 'CANCELLED'
    assert asyncio.run(control.progress(1))['completed_candidates'] == 0
    record_store[0]['status'] = 'RUNNING'
    assert asyncio.run(control.cancel(1))['status'] == 'CANCELLING'
    record_store[0]['status'] = 'COMPLETED'
    with pytest.raises(HTTPException) as error:
        asyncio.run(control.cancel(1))
    assert error.value.status_code == 409
    with pytest.raises(ValueError):
        control.task_directory('../../escape')


def test_supervisor_cancels_only_owned_process(record_store, monkeypatch):
    class Process:
        pid = 999999
        def poll(self): return None
    process = Process()
    def launch(*args, **kwargs):
        record_store[0]['status'] = 'CANCELLING'
        return process
    monkeypatch.setattr(worker.subprocess, 'Popen', launch)
    killed = []
    monkeypatch.setattr(worker, '_terminate_tree', lambda proc: killed.append(proc))
    worker.run_job(record_store[0]['uuid'])
    assert killed == [process]
    assert record_store[0]['status'] == 'CANCELLED'


def test_actual_owned_process_termination():
    # Small disposable child, no user processes or jobs involved.
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        worker._terminate_tree(process)
        assert process.poll() is not None
    finally:
        if process.poll() is None: process.kill()


def test_registration_validation_and_profile_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(instrument_profiles, 'registry_directory', lambda: tmp_path/'profiles')
    monkeypatch.setattr(instrument_router, 'registry_directory', lambda: tmp_path/'profiles')
    def request(**kwargs):
        return asyncio.run(instrument_router.register_profile(label='Lab Cu', geometry='Bragg-Brentano, fixed slit', radiation='Cu',
            notes=kwargs.pop('notes',''), verified=kwargs.pop('verified',False), verified_by=kwargs.pop('verified_by',''),
            geq=UploadFile(file=io.BytesIO(b'geq fixture'),filename='lab.geq'), ger=None, **kwargs))
    with pytest.raises(HTTPException):
        request(verified=True)
    profile = request()
    assert not profile['verified']
    assert profile['id'] in instrument_profiles.registered_profiles()
    directory = tmp_path/'work'
    directory.mkdir()
    name = copy_instrument_files(profile['id'], directory)
    assert (directory/f'{name}.geq').read_bytes() == b'geq fixture'
    with pytest.raises(ValueError):
        instrument_profiles.registered_profile_path('user-../../escape')


def test_candidate_comparison_grid_exclusions_and_identity(monkeypatch, references):
    document = _document(references)
    left = _refined(references)
    right = _refined(references[:1])
    right.plot_data.y_calc = [2, 2, 2]
    document.results = [([], left), ([], right)]
    document.search_kwargs = {'refinement_params': {'excluded_ranges': [[19,21]]}}
    monkeypatch.setattr(comparison, '_load', lambda _: ({}, document))
    response = asyncio.run(comparison.compare_candidates(1, 0, 1))
    assert response['observed'][1] is None
    assert len(response['left_only']) == 1 and not response['right_only']
    assert all(not 19 <= p['two_theta'] <= 21 for p in response['regions'])
    with pytest.raises(HTTPException):
        asyncio.run(comparison.compare_candidates(1, 0, 0))
    right.plot_data.x = [11, 21, 31]
    with pytest.raises(HTTPException) as error:
        asyncio.run(comparison.compare_candidates(1, 0, 1))
    assert error.value.status_code == 409
