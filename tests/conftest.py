"""Shared pytest fixtures for PyQt6 tests."""

import gc
from collections.abc import Iterator

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    app.closeAllWindows()
    app.processEvents()
    gc.collect()
    app.processEvents()
