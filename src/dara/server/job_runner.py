"""Isolated process entry point. The supervisor owns this entire process tree."""
import os
import sys


if __name__ == "__main__":
    os.environ["DARA_TASK_OWNER_PID"] = str(os.getpid())
    from dara.server.worker import execute_job

    execute_job(sys.argv[1])
