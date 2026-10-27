"""Benchmark runner, report and CLI without BGMN or network access."""

import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from dara.benchmark import cli, runner
from dara.benchmark.ground_truth import load_ground_truth
from dara.benchmark.scoring import aggregate

FIXTURE = Path(__file__).parent / "test_data" / "benchmark" / "run_fixture"
SAMPLE_A = "CoO-ZnO_1100C_60min"
SAMPLE_B = "Cr2O3-2ZnO_1100C_60min"


@pytest.fixture(scope="module")
def pairwise():
    return load_ground_truth("pairwise")


def fixture_summary(sample_id):
    return json.loads((FIXTURE / "samples" / sample_id / "summary.json").read_text(encoding="utf-8"))


class FakeJob:
    uuid = "uuid-1"

    def as_dict(self):
        return {"name": "fake"}


@pytest.fixture
def fake_api(monkeypatch):
    calls = {"build": []}

    def build(pattern, name, **kwargs):
        calls["build"].append((name, kwargs))
        return FakeJob()

    def summarize(record, document, *, persist_summary=True):
        assert persist_summary is False
        assert record["status"] == "COMPLETED" and record["start_time"] <= record["end_time"]
        return fixture_summary(document["sample"])

    monkeypatch.setattr(runner, "_load_api", lambda: (build, summarize))
    return calls


def pick(pairwise, *ids):
    by = {s.id: s for s in pairwise.samples}
    return [(pairwise, by[i]) for i in ids]


def test_run_benchmark_layout_and_resume(tmp_path, pairwise, fake_api):
    executed = []

    def execute(job, root_dir):
        executed.append(Path(root_dir))
        return {"sample": SAMPLE_A if len(executed) == 1 else SAMPLE_B}

    logs = []
    result = runner.run_benchmark(
        pick(pairwise, SAMPLE_A, SAMPLE_B), tmp_path / "run", execute_fn=execute, log=logs.append,
    )
    run_dir = tmp_path / "run"
    assert len(executed) == 2
    for name in ("run.json", "scores.json", "report.md", "progress.log"):
        assert (run_dir / name).is_file(), name
    sample_dir = run_dir / "samples" / SAMPLE_A
    for name in ("score.json", "summary.json", "document.json.gz"):
        assert (sample_dir / name).is_file(), name
    score = json.loads((sample_dir / "score.json").read_text(encoding="utf-8"))
    assert score["status"] == "scored" and score["tp"] == 2 and score["exact_set"] is True
    assert score["runtime_s"] is not None
    assert result["aggregate"]["n_samples"] == 2
    progress = (run_dir / "progress.log").read_text().strip().splitlines()
    assert len(progress) == 2 and f" | {SAMPLE_A} | scored | " in progress[0]
    # the job was built from the ground-truth submission
    name, kwargs = fake_api["build"][0]
    assert name == f"{SAMPLE_A}.xrdml"
    assert kwargs["analysis_mode"] == "elemental"
    assert kwargs["precursor_formulas"] == ["CoO", "ZnO"]
    assert kwargs["use_mp"] is True and kwargs["use_cod"] is True
    report = (run_dir / "report.md").read_text()
    assert report.startswith("# DARA accuracy benchmark") and "## Per sample" in report

    # a second call resumes: nothing is executed again
    executed.clear()
    again = runner.run_benchmark(
        pick(pairwise, SAMPLE_A, SAMPLE_B), run_dir, execute_fn=execute, log=logs.append,
    )
    assert executed == []
    assert again["aggregate"]["n_samples"] == 2
    assert any("skipped" in line for line in logs)
    # --no-resume runs the sample again
    runner.run_benchmark(pick(pairwise, SAMPLE_A), run_dir, resume=False, execute_fn=execute, log=logs.append)
    assert len(executed) == 1


def test_failed_sample_is_recorded(tmp_path, pairwise, fake_api):
    def execute(job, root_dir):
        raise ValueError("No reference CIFs were found for this analysis.")

    result = runner.run_benchmark(pick(pairwise, SAMPLE_A), tmp_path / "run", execute_fn=execute, log=lambda _: None)
    sample_dir = tmp_path / "run" / "samples" / SAMPLE_A
    score = json.loads((sample_dir / "score.json").read_text(encoding="utf-8"))
    assert score["status"] == "failed" and "No reference CIFs" in score["error"]
    assert "Traceback" in (sample_dir / "error.txt").read_text()
    assert result["aggregate"]["n_failed"] == 1
    assert "failed" in (tmp_path / "run" / "progress.log").read_text()
    assert "No reference CIFs" in (tmp_path / "run" / "report.md").read_text()


def test_serialisation_failure_does_not_stop_the_run(tmp_path, pairwise, monkeypatch):
    class Opaque:  # monty cannot serialise this
        sample = SAMPLE_A

    monkeypatch.setattr(
        runner, "_load_api",
        lambda: (lambda *a, **k: FakeJob(), lambda record, document, **kw: fixture_summary(SAMPLE_A)),
    )
    result = runner.run_benchmark(
        pick(pairwise, SAMPLE_A), tmp_path / "run", execute_fn=lambda job, root: Opaque(), log=lambda _: None,
    )
    assert result["scores"][0]["status"] == "scored"
    assert (tmp_path / "run" / "samples" / SAMPLE_A / "summary.json").is_file()


def test_environment_switches_are_scoped(tmp_path, pairwise, fake_api, monkeypatch):
    monkeypatch.delenv("DARA_MP_LATTICE_WINDOW", raising=False)
    monkeypatch.setenv("DARA_CIF_VALIDATION", "on")
    seen = {}

    def execute(job, root_dir):
        seen["mp"] = os.environ.get("DARA_MP_LATTICE_WINDOW")
        seen["cif"] = os.environ.get("DARA_CIF_VALIDATION")
        return {"sample": SAMPLE_A}

    runner.run_benchmark(
        pick(pairwise, SAMPLE_A), tmp_path / "run", execute_fn=execute, mp_window=False,
        cif_validation=False, use_mp=False, log=lambda _: None,
    )
    assert seen == {"mp": "off", "cif": "off"}
    assert "DARA_MP_LATTICE_WINDOW" not in os.environ
    assert os.environ["DARA_CIF_VALIDATION"] == "on"
    run = json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))
    assert run["settings"]["mp_lattice_window"] is False
    assert run["settings"]["cif_validation"] is False
    assert run["settings"]["label"] == "non-standard: COD only"
    assert "COD only" in (tmp_path / "run" / "report.md").read_text()
    assert fake_api["build"][0][1]["use_mp"] is False


def test_json_default_handles_numpy_and_datetime():
    text = json.dumps(
        {"a": np.float64(1.5), "b": np.arange(3), "c": datetime(2026, 1, 1), "d": {1, 2}},
        default=runner._json_default,
    )
    loaded = json.loads(text)
    assert loaded["a"] == 1.5 and loaded["b"] == [0, 1, 2] and loaded["c"].startswith("2026-01-01")


def test_score_from_run_reproduces_committed_scores(pairwise):
    result = runner.score_from_run(FIXTURE, gt_sets=[pairwise])
    assert len(result["scores"]) == 4
    committed = {
        folder.name: json.loads((folder / "score.json").read_text(encoding="utf-8"))
        for folder in (FIXTURE / "samples").iterdir()
    }
    for score in result["scores"]:
        old = committed[score["id"]]
        for key in ("status", "tp", "fp", "fn", "exact_set", "strict_exact", "wt_mae", "wt_max_abs",
                    "rwp", "top5_contains_truth", "confidence_overall", "runtime_s", "false_negatives"):
            assert score[key] == old[key], (score["id"], key)
        assert score["matches"] == old["matches"]
    assert result["aggregate"]["n_failed"] == 1


def test_cli_score_from_run_prints_headline(capsys):
    assert cli.main(["score", "--from-run", str(FIXTURE)]) == 0
    out = capsys.readouterr().out
    assert "Phase recall (micro)" in out and "Exact phase set" in out
    assert "0.556" in out  # 5 of 9 required phases found


def test_cli_validate_gt(capsys):
    assert cli.main(["validate-gt"]) == 0
    assert "OK: 60 samples" in capsys.readouterr().out


def test_cli_run_requires_mp_key(monkeypatch, capsys):
    monkeypatch.delenv("MP_API_KEY", raising=False)
    assert cli.main(["run", "--set", "pairwise", "--limit", "1"]) == 2
    assert "MP_API_KEY" in capsys.readouterr().err


def test_select_samples():
    sets = [load_ground_truth("pairwise"), load_ground_truth("precursor")]
    assert len(cli.select_samples(sets)) == 60
    assert [s.id for _, s in cli.select_samples(sets, ids="^CoO-ZnO")] == ["CoO-ZnO_1100C_60min"]
    assert len(cli.select_samples(sets, limit=3)) == 3


def write_run(path: Path, scores):
    path.mkdir(parents=True)
    (path / "scores.json").write_text(
        json.dumps({"scores": scores, "aggregate": aggregate(scores)}), encoding="utf-8"
    )


def make_score(sid, tp, fp, fn, mae=1.0, lattice=0.1):
    return {
        "id": sid, "group": "pairwise", "status": "scored", "n_required_phases": tp + fn,
        "tp": tp, "fp": fp, "fn": fn, "sg_mismatch": 0, "exact_set": fp == 0 and fn == 0,
        "strict_exact": fp == 0 and fn == 0, "wt_mae": mae, "runtime_s": 10.0,
        "top5_contains_truth": True,
        "matches": [{"wt_abs_error": mae, "lattice_rel_error_pct": lattice, "verdict": None}] * tp,
        "false_positives": [], "confidence_overall": None,
    }


def test_compare_exit_codes(tmp_path, capsys):
    base = [make_score(f"s{i}", 2, 0, 0) for i in range(10)]
    write_run(tmp_path / "a", base)
    write_run(tmp_path / "same", base)
    assert cli.main(["compare", str(tmp_path / "a"), str(tmp_path / "same")]) == 0
    assert "No regression gate tripped" in capsys.readouterr().out

    worse = [make_score(f"s{i}", 2, 0, 0) for i in range(8)] + [
        make_score("s8", 1, 1, 1), make_score("s9", 1, 0, 1)
    ]
    write_run(tmp_path / "worse", worse)
    assert cli.main(["compare", str(tmp_path / "a"), str(tmp_path / "worse")]) == 1
    out = capsys.readouterr().out
    assert "REGRESSION" in out and "Newly wrong: s8, s9" in out and "phase recall dropped" in out

    better = [make_score(f"s{i}", 2, 0, 0, mae=0.5) for i in range(10)]
    worse_first = [make_score(f"s{i}", 2, 0, 0) for i in range(9)] + [make_score("s9", 1, 0, 1)]
    write_run(tmp_path / "wf", worse_first)
    write_run(tmp_path / "b", better)
    assert cli.main(["compare", str(tmp_path / "wf"), str(tmp_path / "b")]) == 0
    assert "Newly exact: s9" in capsys.readouterr().out


def test_compare_gates_each_metric():
    base = {"scores": [], "aggregate": {
        "phase_recall": 0.9, "fraction_exact_set": 0.8, "wt_mae_median": 1.0,
        "lattice_rel_error_median_pct": 0.2, "n_failed": 0,
    }}

    def check(**changes):
        other = {"scores": [], "aggregate": {**base["aggregate"], **changes}}
        return cli.compare_runs(base, other)[1]

    assert check() == []
    assert check(phase_recall=0.89) == []                 # within 0.02
    assert check(phase_recall=0.87)                       # drop > 0.02
    assert check(fraction_exact_set=0.74)                 # drop > 0.05
    assert check(wt_mae_median=1.6)                       # rise > 0.5
    assert check(lattice_rel_error_median_pct=0.35)       # rise > 0.1
    assert check(n_failed=1)                              # any new failure


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("DARA_RUN_SLOW") != "1", reason="set DARA_RUN_SLOW=1 for the real BGMN run")
def test_real_pairwise_sample(tmp_path):
    out = tmp_path / "run"
    assert cli.main(["run", "--set", "pairwise", "--ids", "^CoO-ZnO", "--limit", "1", "--out", str(out)]) == 0
    score = json.loads((out / "samples" / SAMPLE_A / "score.json").read_text(encoding="utf-8"))
    assert score["status"] == "scored"
