"""The dependency rule (PLAN.md §4): domain code imports no framework, client or store."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "nights_watch"
FORBIDDEN = {"fastapi", "starlette", "httpx", "pydantic_settings", "langgraph", "sqlite3"}
DOMAIN_MODULES = sorted(
    p for p in SRC.rglob("*.py") if "domain" in p.relative_to(SRC).with_suffix("").parts
)


def imported_modules(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
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
    assert imported_modules(leaky) == {"httpx", "fastapi"}
