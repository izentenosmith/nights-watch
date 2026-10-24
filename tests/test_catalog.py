import re
import traceback
from pathlib import Path

import pytest
import yaml

from nights_watch.catalog.yaml_catalog import CatalogError, load_catalog, parse_app_file

CATALOG = Path(__file__).parent.parent / "catalog"

# Values that must never be committed: the catalog holds env var names, not their values.
SECRET_PATTERNS = [
    re.compile(p)
    for p in (
        r"https?://[^/\s:@]+:[^/\s@]+@",  # credentials in a URL
        r"hooks\.slack\.com",  # Slack webhook
        r"webhook\.office\.com|logic\.azure\.com|[?&]sig=",  # Teams webhook / signed URL
        r"\bxox[abprs]-",  # Slack token
        r"\bgh[pousr]_[A-Za-z0-9]{20,}|github_pat_",  # GitHub token
        r"\bsk-[A-Za-z0-9-]{20,}",  # Anthropic / OpenAI key
        r"\bEAA[A-Za-z0-9]{20,}",  # Meta (WhatsApp) token
        r"\+\d{8,15}\b",  # a phone number
    )
]


def strings_in(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            yield from strings_in(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings_in(v)
    elif node is not None:
        yield str(node)


def secrets_in(path: Path) -> list[str]:
    return [
        s for s in strings_in(yaml.safe_load(path.read_text()))
        if any(p.search(s) for p in SECRET_PATTERNS)
    ]


ALL_FILES = sorted(CATALOG.glob("*.yaml"))


def test_catalog_has_the_template_and_the_sample():
    assert {p.name for p in ALL_FILES} >= {"_template.yaml", "knock-knock.yaml"}


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.name)
def test_every_catalog_file_passes_the_schema(path):
    parse_app_file(path)


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.name)
def test_no_secrets_in_the_catalog(path):
    assert secrets_in(path) == []


def test_load_catalog_skips_the_template_and_keys_by_name():
    assert list(load_catalog(CATALOG)) == ["knock-knock"]


@pytest.mark.parametrize(
    "value",
    [
        "https://hooks.slack.com/services/T000/B000/XXXX",
        "https://prod-00.westus.logic.azure.com/workflows/abc?sig=abc",
        "https://user:pass@prometheus.example.com",
        "xoxb-1234-5678-abcdef",
        "ghp_" + "a" * 36,
        "sk-ant-api03-" + "a" * 30,
        "+56912345678",
    ],
)
def test_secret_scan_catches_planted_secrets(tmp_path, value):
    path = tmp_path / "leaky.yaml"
    path.write_text(yaml.safe_dump({"notify": {"slack": {"webhook_env": value}}}))
    assert secrets_in(path) == [value]


def _write(tmp_path, name, **overrides):
    app = {
        "name": name,
        "owner": "team",
        "description": "an app",
        "match": {"namespace": name},
        "sources": {},
    } | overrides
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(app))
    return path


def test_name_must_match_the_filename(tmp_path):
    _write(tmp_path, "shop").rename(tmp_path / "store.yaml")
    with pytest.raises(CatalogError, match="must match the filename"):
        load_catalog(tmp_path)


def test_unknown_keys_fail_loudly(tmp_path):
    _write(tmp_path, "shop", sources={"prometeus": {"selector": "{}"}})
    with pytest.raises(CatalogError, match="shop.yaml"):
        load_catalog(tmp_path)


def test_an_app_needs_at_least_one_matcher(tmp_path):
    _write(tmp_path, "shop", match={})
    with pytest.raises(CatalogError):
        load_catalog(tmp_path)


def test_env_fields_hold_nw_variable_names_not_values(tmp_path):
    _write(tmp_path, "shop", notify={"slack": {"webhook_env": "https://example.com/hook"}})
    with pytest.raises(CatalogError, match="webhook_env"):
        load_catalog(tmp_path)


def test_channel_without_env_falls_back_to_the_global_channel(tmp_path):
    _write(tmp_path, "shop", notify={"whatsapp": {"min_severity": "warning"}})
    app = load_catalog(tmp_path)["shop"]
    assert app.notify.whatsapp.to_env is None
    assert app.notify.whatsapp.min_severity == "warning"


def test_severity_floor_is_one_of_the_three_levels(tmp_path):
    _write(tmp_path, "shop", notify={"slack": {"min_severity": "urgent"}})
    with pytest.raises(CatalogError):
        load_catalog(tmp_path)


def test_channel_without_a_floor_inherits_the_global_floor(tmp_path):
    _write(tmp_path, "shop", notify={"slack": {"webhook_env": "NW_SHOP_SLACK_WEBHOOK_URL"}})
    assert load_catalog(tmp_path)["shop"].notify.slack.min_severity is None


def test_an_app_file_becomes_a_watched_app(tmp_path):
    _write(tmp_path, "shop", match={"namespace": "shop", "app": "api"})
    app = load_catalog(tmp_path)["shop"].to_watched_app()
    assert app.name == "shop"
    assert app.matcher.matches({"namespace": "shop", "app": "api", "pod": "x"})


def everything_printed_about(error: BaseException) -> str:
    """The error's text, its formatted traceback, and every exception chained to it."""
    printed = "".join(traceback.format_exception(error))
    while error is not None:
        printed += repr(error) + str(error)
        error = error.__cause__ or error.__context__
    return printed


@pytest.mark.parametrize(
    "notify, secret",
    [
        ({"slack": {"webhook_env": "https://hooks.slack.com/services/T0/B0/ABCDEFGHIJKLMNOPQRSTUVWX"}},
         "QRSTUVWX"),
        ({"whatsapp": {"to_env": "+56912345678"}}, "56912345678"),
    ],
    ids=["webhook url", "phone number"],
)
def test_a_validation_error_names_the_field_but_never_echoes_the_value(tmp_path, notify, secret):
    _write(tmp_path, "shop", notify=notify)
    with pytest.raises(CatalogError) as error:
        load_catalog(tmp_path)
    assert "shop.yaml" in str(error.value) and "notify." in str(error.value)
    assert secret not in everything_printed_about(error.value)


def test_a_secret_pasted_as_a_key_is_not_echoed(tmp_path):
    secret = "https://hooks.slack.com/services/T0/B0/PASTEDKEY"
    (tmp_path / "shop.yaml").write_text(
        "name: shop\nowner: t\ndescription: d\nmatch: {a: b}\nsources: {}\n"
        f"notify:\n  slack:\n    {secret}:\n"
    )
    with pytest.raises(CatalogError) as error:
        load_catalog(tmp_path)
    assert "shop.yaml" in str(error.value) and "notify.slack" in str(error.value)
    assert "PASTEDKEY" not in everything_printed_about(error.value)


@pytest.mark.parametrize(
    "text",
    [
        "name: shop\nnotify: {slack: {webhook_env: xoxb-SECRET-TOKEN\n",
        "name: shop\nnotify:\n  slack:\n    webhook_env: xoxb: SECRET\n",
    ],
    ids=["unclosed mapping", "error on the secret's line"],
)
def test_a_yaml_syntax_error_doesnt_echo_the_line(tmp_path, text):
    (tmp_path / "shop.yaml").write_text(text)
    with pytest.raises(CatalogError) as error:
        load_catalog(tmp_path)
    assert "shop.yaml" in str(error.value)
    assert "SECRET" not in everything_printed_about(error.value)


@pytest.mark.parametrize(
    "content",
    [b"\xff\xfename: shop\n", b""],
    ids=["not utf-8", "empty"],
)
def test_an_unreadable_or_empty_file_is_named(tmp_path, content):
    (tmp_path / "shop.yaml").write_bytes(content)
    with pytest.raises(CatalogError) as error:
        load_catalog(tmp_path)
    assert str(error.value).startswith("shop.yaml: ")
    assert ": :" not in str(error.value)


def test_a_directory_named_like_an_app_file_is_named(tmp_path):
    (tmp_path / "shop.yaml").mkdir()
    with pytest.raises(CatalogError, match="^shop.yaml: "):
        load_catalog(tmp_path)


def test_the_catalog_path_must_be_an_existing_directory(tmp_path):
    with pytest.raises(CatalogError, match="does not exist"):
        load_catalog(tmp_path / "nowhere")
    (tmp_path / "file.yaml").write_text("")
    with pytest.raises(CatalogError, match="not a directory"):
        load_catalog(tmp_path / "file.yaml")
