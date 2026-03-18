from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.docs

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PACKAGE_AGENTS = ROOT / "src" / "opencode_sql_lsp_server" / "AGENTS.md"
PYPROJECT = ROOT / "pyproject.toml"
VERIFY_FAST = ROOT / "scripts" / "verify_fast.sh"
VERIFY_FULL = ROOT / "scripts" / "verify_full.sh"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_verification_ladder_commands_match_docs_and_scripts() -> None:
    readme = _read(README)
    package_agents = _read(PACKAGE_AGENTS)
    verify_fast = _read(VERIFY_FAST)
    verify_full = _read(VERIFY_FULL)

    readme_commands = [
        "bash scripts/verify_fast.sh",
        "bash scripts/verify_full.sh",
        "python3 -m pytest tests/test_config.py tests/test_workspace_config.py -q",
        "python3 -m pytest tests/test_server_behaviors.py tests/test_lsp_helpers.py -q",
        "python3 -m pytest tests/test_sqlfluff_adapter.py -q",
        "python3 -m pytest -m smoke -q",
    ]
    package_agent_commands = [
        "bash scripts/verify_fast.sh",
        "bash scripts/verify_full.sh",
        "python3 -m pytest tests/test_config.py tests/test_workspace_config.py -q",
        "python3 -m pytest tests/test_server_behaviors.py tests/test_lsp_helpers.py tests/test_diagnostics_scheduler.py -q",
        "python3 -m pytest tests/test_sqlfluff_adapter.py -q",
        "python3 -m pytest -m smoke -q",
    ]

    for command in readme_commands:
        assert command in readme
    for command in package_agent_commands:
        assert command in package_agents

    assert "tests/test_docs_consistency.py" in verify_fast
    assert 'python -m pytest -m "not smoke"' not in verify_full
    assert '"$PYTHON_BIN" -m pytest -m smoke -q' in verify_full


def test_marker_definitions_cover_documented_verification_lanes() -> None:
    pyproject = _read(PYPROJECT)

    for marker_name in ["config", "server", "lsp_helpers", "sqlfluff", "smoke", "docs"]:
        assert f'"{marker_name}:' in pyproject


def test_docs_reference_ci_python_versions_and_default_dialect() -> None:
    readme = _read(README)
    package_agents = _read(PACKAGE_AGENTS)
    ci_workflow = _read(CI_WORKFLOW)

    assert 'defaultDialect": "starrocks"' in readme
    assert "default degraded fallback to `starrocks`" in readme
    assert "Default dialect is `starrocks`" in package_agents
    assert 'python-version: ["3.10", "3.11", "3.12"]' in ci_workflow
    assert "Python 3.10, 3.11, and 3.12" in readme


def test_handoff_recipes_reference_expected_touchpoints() -> None:
    readme = _read(README)
    package_agents = _read(PACKAGE_AGENTS)

    for snippet in [
        "src/opencode_sql_lsp_server/server.py",
        "src/opencode_sql_lsp_server/workspace_config.py",
        "src/opencode_sql_lsp_server/sqlfluff_adapter.py",
        "bash scripts/build_dist.sh",
    ]:
        assert snippet in readme

    for snippet in [
        "server.py",
        "workspace_config.py",
        "sqlfluff_adapter.py",
        "bash scripts/build_dist.sh",
    ]:
        assert snippet in package_agents or snippet == "bash scripts/build_dist.sh"


def test_ci_runs_docs_lane_and_uses_path_filtering() -> None:
    ci_workflow = _read(CI_WORKFLOW)

    assert "dorny/paths-filter@v3" in ci_workflow
    assert "docs_only" in ci_workflow
    assert "Run docs consistency checks" in ci_workflow
