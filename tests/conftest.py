"""
Test fixtures — shared database setup/teardown for all tests.

Each test gets a fresh in-memory SQLite database (via tmp file) to ensure isolation.
"""

import os
import tempfile
import pytest
from src.db.connection import set_db_path, init_db, close_connection
from seed_data import seed_catalog


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """
    Create a fresh SQLite database for each test.
    Uses a temp file (not :memory:) because WAL mode requires a real file,
    and we need to test concurrent access.
    """
    db_path = str(tmp_path / "test_store.db")
    set_db_path(db_path)
    init_db()
    seed_catalog()
    yield db_path
    close_connection()
