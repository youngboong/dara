"""Shared test set-up."""

import pytest


@pytest.fixture(autouse=True)
def _fresh_document_cache():
    """Decoded task documents are cached by task uuid; tests reuse uuids with other documents."""
    from dara.server import quick_refine

    quick_refine._DOCUMENTS.clear()
    yield
    quick_refine._DOCUMENTS.clear()
