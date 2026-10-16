"""Serve synthetic workflow fixtures without touching the user's DB or running jobs.

Run from the repository: python scripts/serve_workflow_qa.py
Only for local UI checks; the in-memory state disappears when the process exits.
"""

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from monty.json import MontyEncoder

from dara.cif import Cif
from dara.jobs import PhaseSearchMaker
from dara.result import DiaResult, LstResult, PhaseResult, RefinementMetrics, RefinementResult
from dara.schema import PhaseSearchDocument
from dara.server import api_router, comparison_router, review_router, selected_refinement_router, worker
from dara.server import candidate_comparison_router, instrument_router, task_control_router
from dara import instrument_profiles
from dara.xrd import XRDData


class MemoryStore:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def query_one(self, criteria=None, properties=None):
        return next(iter(self.query(criteria)), None)

    def query(self, criteria=None, properties=None, sort=None, skip=0, limit=0, **kwargs):
        rows = [copy.deepcopy(row) for row in self.rows if all(
            row.get(key) in value['$in'] if isinstance(value, dict) and '$in' in value else row.get(key) == value
            for key, value in (criteria or {}).items()
        )]
        for key, direction in (sort or {}).items():
            rows.sort(key=lambda row: row.get(key), reverse=direction < 0)
        return rows[skip:skip + limit] if limit else rows[skip:]

    def count(self, criteria=None):
        return len(self.query(criteria))

    def update(self, docs, **kwargs):
        for doc in docs if isinstance(docs, list) else [docs]:
            previous = next((row for row in self.rows if row['uuid'] == doc['uuid']), None)
            if previous is None:
                self.rows.append(copy.deepcopy(doc))
            else:
                previous.update(copy.deepcopy(doc))


def encoded(value):
    return json.loads(json.dumps(value, cls=MontyEncoder))


def fixture(task_id):
    cif = Cif.from_file(Path('tests/test_data/BiFeO3.cif'))
    cif.filename = 'BiFeO3_161_(qa_1)'
    x = np.linspace(10, 70, 1201)
    profile = sum(1000 * np.exp(-((x - position) / .18) ** 2) for position in [22, 32, 39, 46, 57])
    observed = profile + 50 + np.sin(x) * 3
    pattern = XRDData(x, observed * task_id)
    phase = PhaseResult(
        SpacegroupNo=161, HermannMauguin='R3c', XrayDensity=8.3, Rphase=3.1,
        UNIT='NM', GEWICHT=1.0, GEWICHT_NAME=None, A=(.5578, .00002), C=(1.386, .0001),
    )
    lst = LstResult(
        raw_lst='', pattern_name=f'QA sample {task_id}', num_steps=10,
        Rp=3, Rpb=4, R=4, Rwp=4.2, Rexp=2, d=1.8, **{'1-rho': 8},
        phases_results={cif.filename: phase},
    )
    fit = RefinementResult(
        lst_data=lst,
        plot_data=DiaResult(x=x.tolist(), y_obs=observed.tolist(), y_calc=(profile+50).tolist(),
                            y_bkg=(np.ones(x.size)*50).tolist(), structs={cif.filename: profile.tolist()}),
        peak_data=pd.DataFrame([{'phase': cif.filename, '2theta': angle, 'intensity': 1000} for angle in [22, 32, 39, 46, 57]]),
        refinement_metrics=RefinementMetrics(rwp=4.2, missing_peaks=[], extra_peaks=[], intensity_mismatch_peaks=[]),
    )
    options = {'instrument_profile': 'Aeris-fds-Pixcel1d-Medipix3', 'wavelength': 'Cu',
               'refinement_params': {'wmin': 10, 'wmax': 70, 'excluded_ranges': [[28, 29]]}}
    job = PhaseSearchMaker(name=f'QA sample {task_id}.xy', phase_predictor=None).make(
        xrd_data=pattern, cifs=[cif], elements=['Bi','Fe','O'], analysis_mode='elemental', search_kwargs=options,
    )
    document = PhaseSearchDocument(
        xrd_data=pattern, input_cifs=[cif], results=[([[cif]], fit)], final_result=fit, best_rwp=4.2,
        screening_rwps=[6.1], analysis_mode='elemental', elements=['Bi','Fe','O'],
        search_kwargs=options, run_final_refinement=True, candidate_sources=['cod'], cifs_folder_name='qa_cifs',
    )
    now = datetime.now(tz=timezone.utc)
    record = {'uuid':job.uuid, 'index':task_id, 'job':encoded(job.as_dict()), 'status':'COMPLETED',
              'user':'QA fixture', 'submitted_time':now-timedelta(seconds=60),
              'start_time':now-timedelta(seconds=50), 'end_time':now}
    return record, {'uuid':job.uuid, 'index':1, 'output':encoded(document)}


def create_app():
    pairs = [fixture(1), fixture(2), fixture(3)]
    from monty.json import MontyDecoder
    document = MontyDecoder().process_decoded(pairs[2][1]['output'])
    alternative = Cif.from_file(Path('tests/test_data/Bi2Fe4O9.cif'))
    alternative.filename = 'Bi2Fe4O9_55_(qa_2)'
    fit = document.final_result.model_copy(deep=True)
    phase = next(iter(fit.lst_data.phases_results.values())).model_copy(deep=True)
    phase.spacegroup_no = 55
    phase.hermann_mauguin = 'Pbam'
    fit.lst_data.phases_results = {alternative.filename: phase}
    fit.lst_data.rwp = 7.3
    fit.plot_data.y_calc = (np.asarray(fit.plot_data.y_calc) * .95).tolist()
    fit.plot_data.structs = {alternative.filename: next(iter(fit.plot_data.structs.values()))}
    fit.peak_data['phase'] = alternative.filename
    document.results.append(([[alternative]], fit))
    document.screening_rwps.append(8.1)
    pairs[2][1]['output'] = encoded(document)
    jobs = MemoryStore([pair[0] for pair in pairs])
    results = MemoryStore([pair[1] for pair in pairs])
    for module in [api_router, comparison_router, review_router, selected_refinement_router, worker, task_control_router]:
        module.get_worker_store = lambda: jobs
        module.get_result_store = lambda: results
    app = FastAPI()
    qa_directory = Path('artifacts/workflow-qa').resolve()
    instrument_profiles.registry_directory = lambda: qa_directory / 'profiles'
    instrument_router.registry_directory = instrument_profiles.registry_directory
    task_control_router.task_directory = lambda uuid: qa_directory / 'checkpoints' / uuid
    for module in [api_router, comparison_router, review_router, selected_refinement_router, candidate_comparison_router, task_control_router, instrument_router]:
        app.include_router(module.router)
    app.mount('', StaticFiles(directory='src/dara/server/ui/public', html=True))
    return app


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(create_app(), host='127.0.0.1', port=8899)
