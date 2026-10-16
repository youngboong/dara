from dara.server.app import _must_revalidate_frontend


def test_gatsby_entrypoints_are_always_revalidated():
    assert _must_revalidate_frontend("index.html", "text/html; charset=utf-8")
    assert _must_revalidate_frontend("task/index.html", "text/html; charset=utf-8")
    assert _must_revalidate_frontend("chunk-map.json", "application/json")
    assert _must_revalidate_frontend("page-data/task/page-data.json", "application/json")


def test_hashed_assets_keep_normal_static_file_caching():
    assert not _must_revalidate_frontend(
        "component---src-pages-task-js-abc123.js", "text/javascript; charset=utf-8"
    )
    assert not _must_revalidate_frontend("styles.abc123.css", "text/css; charset=utf-8")
