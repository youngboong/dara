from dara.eflech_worker import EflechWorker


def test_teil_ranges_patch_broad_interval_in_wide_scan():
    """A 40-degree TEIL interval must not be accepted in a 90-degree scan."""
    assert EflechWorker._teil_ranges_need_patch(
        [9.9, 28.8, 68.4], [28.8, 68.4, 100.1], 90.2
    )


def test_teil_ranges_accept_reasonably_sized_intervals():
    assert not EflechWorker._teil_ranges_need_patch(
        [10.0, 22.0, 34.0], [22.0, 34.0, 46.0], 36.0
    )


def test_teil_ranges_patch_invalid_or_incomplete_intervals():
    assert EflechWorker._teil_ranges_need_patch([], [], 50.0)
    assert EflechWorker._teil_ranges_need_patch([10.0], [], 50.0)
    assert EflechWorker._teil_ranges_need_patch([20.0], [20.0], 50.0)
