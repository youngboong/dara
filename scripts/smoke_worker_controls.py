"""Exercise the real job subprocess and cancellation with a disposable local DB."""
import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread

from worker_fixture_job import bounded_job


if __name__ == "__main__":
    root = Path('artifacts/worker-smoke').resolve()
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root) as temporary:
        os.environ['DARA_SERVER_DATABASE_BACKEND'] = 'monty'
        os.environ['DARA_SERVER_MONTYDB_PATH'] = str(Path(temporary) / 'montydb')
        os.environ['PYTHONPATH'] = str(Path(__file__).resolve().parent) + os.pathsep + os.environ.get('PYTHONPATH', '')
        from dara.server.worker import add_job_to_queue, run_job
        from dara.server.utils import get_worker_store, get_result_store
        from dara.server.task_control_router import cancel

        job = bounded_job()
        index = add_job_to_queue(job, 'Synthetic QA')
        stop = Event()
        thread = Thread(target=run_job, args=(job.uuid, stop), daemon=True)
        thread.start()
        thread.join(60)
        if thread.is_alive():
            stop.set()
            thread.join(20)
            raise RuntimeError('Synthetic worker did not complete within 60 seconds')
        with get_worker_store() as store:
            record = store.query_one({'index':index})
            assert record['status'] == 'COMPLETED', record.get('error', record['status'])
        with get_result_store() as store:
            assert store.query_one({'uuid':job.uuid})['output']['worker_smoke'] == 'completed'
        job = bounded_job()
        index = add_job_to_queue(job, 'Synthetic QA cancellation')
        stop = Event()
        thread = Thread(target=run_job, args=(job.uuid, stop), daemon=True)
        thread.start()
        # An authoritative cancellation marker also covers READY -> RUNNING races.
        asyncio.run(cancel(index))
        thread.join(20)
        if thread.is_alive():
            stop.set()
            thread.join(20)
            raise RuntimeError('Cancellation did not stop the synthetic task')
        with get_worker_store() as store:
            assert store.query_one({'index':index})['status'] == 'CANCELLED'
        print('PASS: isolated job completion, persisted result and cancellation; user DB untouched.')
