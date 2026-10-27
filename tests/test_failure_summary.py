import pytest

from dara.server.failure_summary import summarize_failure


def _tb(last):
    return f'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\n    boom()\n{last}\n'


@pytest.mark.parametrize(
    "text,code",
    [
        ("Job process exited without a completed result. Completed checkpoints are retained.", "interrupted"),
        ("Unexpected shutdown of the worker process.", "interrupted"),
        (_tb("RuntimeError: No peaks are detected in the pattern"), "no_peaks"),
        (_tb("ValueError: No reference CIFs were found for the given elements"), "no_candidates"),
        (_tb("RuntimeError: Final refinement failed for every candidate."), "final_refinement_failed"),
        (_tb("ValueError: MP_API_KEY is not set"), "mp_key"),
        (_tb("mp_api.client.exceptions.MPRestError: Materials Project request failed: 403 Forbidden"), "mp_key"),
        (_tb("subprocess.TimeoutExpired: Command bgmn timed out after 300 seconds"), "timeout"),
        (_tb("requests.exceptions.ConnectionError: HTTPSConnectionPool(host=www.crystallography.net)"), "network"),
        (_tb("ray.exceptions.RaySystemError: System error: Failed to start the raylet"), "ray"),
        (_tb("MemoryError: Unable to allocate 2.00 GiB for an array"), "memory"),
    ],
)
def test_rules(text, code):
    summary = summarize_failure(text)
    assert summary["code"] == code
    assert summary["title"] and summary["hint"]
    assert len(summary["detail"]) <= 300


def test_bgmn_exit_code_in_title():
    summary = summarize_failure(_tb("RuntimeError: Error in BGMN refinement. The exit code is -1073741819."))
    assert summary["code"] == "bgmn_exit"
    assert "-1073741819" in summary["title"]


def test_empty_and_unknown():
    assert summarize_failure(None) is None
    assert summarize_failure("") is None
    assert summarize_failure("   \n") is None
    unknown = summarize_failure(_tb("KeyError: 'x'"))
    assert unknown["code"] == "unknown"
    assert unknown["title"] == "Analysis failed: KeyError"
    assert unknown["detail"] == "KeyError: 'x'"
    assert summarize_failure("something odd")["title"] == "Analysis failed"


def test_detail_is_truncated():
    assert len(summarize_failure(_tb("ValueError: " + "x" * 1000))["detail"]) == 300


_RAY_FRAMES = (
    '  File "ray/_private/worker.py", line 2667, in get\n'
    "    values, debugger_breakpoint = worker.get_objects(object_refs, timeout=timeout)\n"
    '  File "python/ray/_raylet.pyx", line 3571, in ray._raylet.CoreWorker.get_objects\n'
    '  File "python/ray/_raylet.pyx", line 590, in ray._raylet.check_status\n'
)


def test_ray_get_traceback_ending_in_keyboard_interrupt_is_interrupted():
    summary = summarize_failure(_tb(_RAY_FRAMES + "KeyboardInterrupt"))
    assert summary["code"] == "interrupted"
    assert "Ray" not in summary["title"]


def test_ray_raylet_frames_do_not_trigger_ray_startup_title():
    summary = summarize_failure(_tb(_RAY_FRAMES + "ValueError: unrelated failure"))
    assert summary["code"] == "unknown"
    assert "Ray" not in summary["title"]
    assert summary["title"] == "Analysis failed: ValueError"


def test_ray_startup_signatures_still_match():
    for last in (
        "ray.exceptions.RaySystemError: System error: boom",
        "RuntimeError: Failed to start the dashboard",
        "RuntimeError: Failed to connect to the GCS server",
    ):
        assert summarize_failure(_tb(last))["code"] == "ray"


def test_detail_prefers_exception_line_over_trailing_link():
    text = (
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "pydantic_core._pydantic_core.ValidationError: 1 validation error for Foo\n"
        "bar\n"
        "  Input should be a valid integer [type=int_parsing, input_value='x']\n"
        "    For further information visit https://errors.pydantic.dev/2.7/v/int_parsing\n"
    )
    summary = summarize_failure(text)
    assert summary["detail"].startswith("pydantic_core._pydantic_core.ValidationError")
    assert "errors.pydantic.dev" not in summary["detail"]
    assert summary["title"] == "Analysis failed: ValidationError"
    # No exception-looking line -> previous behaviour (last non-empty line).
    assert summarize_failure("something odd\nlast words")["detail"] == "last words"
