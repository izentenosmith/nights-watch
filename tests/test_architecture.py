"""The dependency rule (PLAN.md §4): domain code imports no framework, client or store."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "nights_watch"
FORBIDDEN = {"fastapi", "starlette", "httpx", "pydantic_settings", "langgraph", "sqlite3"}
DOMAIN_MODULES = sorted(
    p for p in SRC.rglob("*.py") if "domain" in p.relative_to(SRC).with_suffix("").parts
)


def imported_modules(path: Path, root: Path = SRC.parent) -> set[str]:
    """Absolute names of everything the file imports; relative imports are resolved."""
    package = list(path.relative_to(root).with_suffix("").parts)
    package = package[:-1]  # the directory: a module's package, or __init__'s own
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules.add(node.module)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1]
            if node.module:
                modules.add(".".join([*base, node.module]))
            else:
                modules |= {".".join([*base, alias.name]) for alias in node.names}
    return modules


def test_there_are_domain_modules_to_check():
    assert {p.relative_to(SRC).as_posix() for p in DOMAIN_MODULES} >= {
        "catalog/domain.py",
        "triage/domain/alert.py",
        "notification/domain.py",
    }


@pytest.mark.parametrize("path", DOMAIN_MODULES, ids=lambda p: p.relative_to(SRC).as_posix())
def test_domain_modules_import_no_framework_client_or_store(path):
    roots = {m.split(".")[0] for m in imported_modules(path)}
    assert roots & FORBIDDEN == set()


@pytest.mark.parametrize(
    "path, allowed",
    [
        ("catalog/domain.py", set()),
        ("triage/domain/alert.py", set()),
        ("notification/domain.py", {"nights_watch.triage.domain.alert"}),
    ],
)
def test_domain_modules_depend_only_on_the_contexts_they_are_allowed_to(path, allowed):
    own = {m for m in imported_modules(SRC / path) if m.startswith("nights_watch")}
    assert own <= allowed


def test_the_scanner_catches_a_forbidden_import(tmp_path):
    leaky = tmp_path / "domain.py"
    leaky.write_text("import httpx\nfrom fastapi import FastAPI\n")
    assert imported_modules(leaky, root=tmp_path) == {"httpx", "fastapi"}


def test_the_scanner_resolves_relative_imports(tmp_path):
    domain = tmp_path / "nights_watch" / "notification" / "domain.py"
    domain.parent.mkdir(parents=True)
    domain.write_text("from ..triage.application import receive_alert\nfrom . import channels\n")
    assert imported_modules(domain, root=tmp_path) == {
        "nights_watch.triage.application",
        "nights_watch.notification.channels",
    }
