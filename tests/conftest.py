import pytest


@pytest.fixture(autouse=True)
def _own_working_directory(tmp_path, monkeypatch):
    """Each test runs in its own directory, so relative defaults (data/nightswatch.sqlite, .env)
    never touch the repository."""
    monkeypatch.chdir(tmp_path)
