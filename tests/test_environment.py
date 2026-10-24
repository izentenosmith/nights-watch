import os

import pytest
import yaml
from fastapi.testclient import TestClient

from nights_watch.bootstrap import create_app, read_environment


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No NW_* from the developer's shell, and a working directory of our own."""
    for name in [n for n in os.environ if n.startswith("NW_")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    return monkeypatch


def test_the_process_environment_wins_over_dotenv(clean_env, tmp_path):
    (tmp_path / ".env").write_text("NW_SLACK_WEBHOOK_URL=from-dotenv\nNW_TEAMS_WEBHOOK_URL=teams\n")
    clean_env.setenv("NW_SLACK_WEBHOOK_URL", "from-process")
    env = read_environment()
    assert env["NW_SLACK_WEBHOOK_URL"] == "from-process"
    assert env["NW_TEAMS_WEBHOOK_URL"] == "teams"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_variable_counts_as_unset_and_doesnt_hide_dotenv(clean_env, tmp_path, blank):
    (tmp_path / ".env").write_text("NW_SLACK_WEBHOOK_URL=from-dotenv\nNW_TEAMS_WEBHOOK_URL=\n")
    clean_env.setenv("NW_SLACK_WEBHOOK_URL", blank)
    clean_env.setenv("NW_WHATSAPP_TO", blank)
    env = read_environment()
    assert env["NW_SLACK_WEBHOOK_URL"] == "from-dotenv"
    assert "NW_TEAMS_WEBHOOK_URL" not in env
    assert "NW_WHATSAPP_TO" not in env


def test_without_dotenv_only_the_process_environment_counts(clean_env):
    clean_env.setenv("NW_SLACK_WEBHOOK_URL", "x")
    assert read_environment()["NW_SLACK_WEBHOOK_URL"] == "x"


def test_create_app_loads_the_catalog_from_nw_catalog_dir(clean_env, tmp_path):
    catalog = tmp_path / "apps"
    catalog.mkdir()
    app = {"name": "shop", "owner": "t", "description": "d", "match": {"ns": "shop"}, "sources": {}}
    (catalog / "shop.yaml").write_text(yaml.safe_dump(app))
    (tmp_path / ".env").write_text(f"NW_CATALOG_DIR={catalog}\n")
    with TestClient(create_app()) as client:
        assert [a["name"] for a in client.get("/apps").json()] == ["shop"]


def test_a_padded_value_arrives_stripped(clean_env):
    clean_env.setenv("NW_SLACK_WEBHOOK_URL", "  https://x.test/hook \n")
    assert read_environment()["NW_SLACK_WEBHOOK_URL"] == "https://x.test/hook"
