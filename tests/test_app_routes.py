import pytest

from dara.server.app import app


def _flatten(route) -> list[str]:
    """Paths of a route; newer FastAPI nests included routers instead of flattening them."""
    nested = getattr(route, "original_router", None)
    if nested is not None:
        prefix = getattr(getattr(route, "include_context", None), "prefix", "") or ""
        return [prefix + path for child in nested.routes for path in _flatten(child)]
    path = getattr(route, "path", None)
    return [path] if path is not None else []


def _top_level():
    return [_flatten(route) for route in app.routes]


def _paths():
    return [path for group in _top_level() for path in group]


def test_w2_routes_are_registered():
    paths = _paths()
    assert "/api/task/{task_id}/export/{fmt}" in paths
    assert "/api/task/{task_id}/figure.{fmt}" in paths
    assert "/api/task/{task_id}/report" in paths
    assert "/api/submit" in paths


@pytest.mark.parametrize(
    "module,expected",
    [
        ("dara.server.reference_library_router", ["/api/reference-cifs"]),
        ("dara.server.mp_router", ["/api/mp-key/status"]),
        ("dara.server.series_router", ["/api/series", "/api/submit-series"]),
    ],
)
def test_optional_router_routes_when_the_module_exists(module, expected):
    pytest.importorskip(module)
    paths = _paths()
    for path in expected:
        assert path in paths, f"{path} from {module} is not registered"


def test_static_mount_is_last_so_api_routes_stay_reachable():
    groups = _top_level()
    mount_index = next(i for i, group in enumerate(groups) if group == [""])
    assert mount_index == len(groups) - 1
    api_groups = [i for i, group in enumerate(groups) if any(p.startswith("/api") for p in group)]
    assert api_groups and max(api_groups) < mount_index


def test_optional_router_list_names_all_four_modules():
    import dara.server.app as app_module

    assert set(app_module._OPTIONAL_ROUTERS) == {
        "dara.server.export_router",
        "dara.server.reference_library_router",
        "dara.server.mp_router",
        "dara.server.series_router",
    }
