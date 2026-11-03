from dara.refine import single_refinement_threads, with_single_refinement_threads


def test_default_uses_all_cores_but_one(monkeypatch):
    monkeypatch.delenv("DARA_SINGLE_BGMN_THREADS", raising=False)
    monkeypatch.setattr("os.cpu_count", lambda: 12)
    assert single_refinement_threads() == 11
    monkeypatch.setattr("os.cpu_count", lambda: 1)
    assert single_refinement_threads() == 1


def test_env_override_and_kwargs_copy(monkeypatch):
    monkeypatch.setenv("DARA_SINGLE_BGMN_THREADS", "4")
    kwargs = {"refinement_params": {"wmin": 10, "n_threads": 1}, "wavelength": "Cu"}
    out = with_single_refinement_threads(kwargs)
    assert out["refinement_params"] == {"wmin": 10, "n_threads": 4}
    assert out["wavelength"] == "Cu"
    assert kwargs["refinement_params"]["n_threads"] == 1  # the caller's dict is untouched
    assert with_single_refinement_threads({})["refinement_params"] == {"n_threads": 4}


def test_small_batches_share_the_cores():
    from dara.search.tree import BGMN_N_THREADS, batch_threads

    assert batch_threads(3, total_cpus=12) == 4
    assert batch_threads(1, total_cpus=12) == 12
    assert batch_threads(40, total_cpus=12) == BGMN_N_THREADS  # full batch: one thread each
    assert batch_threads(0, total_cpus=12) == BGMN_N_THREADS
