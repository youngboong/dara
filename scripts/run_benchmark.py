"""Run the DARA accuracy benchmark (thin wrapper around ``dara.benchmark.cli``).

    python scripts/run_benchmark.py validate-gt
    python scripts/run_benchmark.py run --set pairwise --ids "^CoO-ZnO" --limit 1
    python scripts/run_benchmark.py score --from-run benchmarks/<run>
    python scripts/run_benchmark.py compare benchmarks/baseline benchmarks/round2
"""

import sys

from dara.benchmark.cli import main

if __name__ == "__main__":
    sys.exit(main())
