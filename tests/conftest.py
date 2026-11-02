import sqlite3

import pytest

from emporio.etl import build_database


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    """A fresh database built from data/raw, isolated from data/emporio.db."""
    path = tmp_path_factory.mktemp("db") / "emporio.db"
    build_database(db_path=path)
    return path


@pytest.fixture
def conn(db_path):
    connection = sqlite3.connect(db_path)
    yield connection
    connection.close()
