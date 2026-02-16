"""Shared test fixtures."""

import pytest
from pathlib import Path

from schnabel.db import Database


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a temporary database."""
    db = Database(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def fixtures_dir():
    """Path to test fixtures."""
    return Path(__file__).parent / "fixtures"
