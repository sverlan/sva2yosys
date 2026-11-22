"""Shared fixtures: explicit external resources and cumulative FAST/EXTENDED/SLOW scopes.

A run uses only local resources by default. ``--external-tools`` and
``--external-corpora`` (``none``, ``all`` or a comma-separated list; see CONTRIBUTING.md and
``support.selection``) select external tools and corpora by name. Their paths come from
the environment, typically loaded from ``.env`` by ``uv run --env-file .env pytest``.
A selected resource that is unavailable stops the run with a usage error. A test
requesting a resource that was not selected is skipped, and the end of the run
summarizes what was left out (tiers, tools, corpora), each axis counted independently over
the collected tests (a test can count under several axes).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from support.selection import (
    CORPORA,
    OPTION_CORPORA,
    OPTION_TOOLS,
    TOOLS,
    Unmet,
    corpus_not_selected_reason,
    is_other_reason,
    parse_selection,
    summarize,
    tier_reason,
    tool_not_selected_reason,
    unavailable,
    unmet,
)
from support.tiers import Tier, includes, selected_tier
from support.toolchain import (
    ENV_EBMC_TESTS,
    ENV_LTL2TGBA,
    ENV_SBY_EXAMPLES,
    ENV_YOSYS_SVA_TESTS,
    LTL2TGBA,
    Tool,
    directory_from_env,
    tool_directory_from_env,
    tool_from_env,
)

ROOT = Path(__file__).resolve().parents[1]

_TOOLS_KEY = pytest.StashKey[frozenset[str]]()
_CORPORA_KEY = pytest.StashKey[frozenset[str]]()
_UNMET_KEY = pytest.StashKey[list[Unmet]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--extended",
        action="store_true",
        help="also run the extended test tier (see CONTRIBUTING.md)",
    )
    parser.addoption(
        "--slow",
        action="store_true",
        help="run every tier, including slow stress tests (see CONTRIBUTING.md)",
    )
    parser.addoption(
        OPTION_TOOLS,
        default="none",
        help="external tools to use: none (default), all, or a comma-separated list of "
        f"{', '.join(t.name for t in TOOLS)} (see CONTRIBUTING.md)",
    )
    parser.addoption(
        OPTION_CORPORA,
        default="none",
        help="external corpora to use: none (default), all, or a comma-separated list of "
        f"{', '.join(c.name for c in CORPORA)} (see CONTRIBUTING.md)",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Parse the resource selection; a selected but unavailable resource is a usage error."""
    try:
        tools = parse_selection(
            str(config.getoption(OPTION_TOOLS)), (t.name for t in TOOLS), OPTION_TOOLS
        )
        corpora = parse_selection(
            str(config.getoption(OPTION_CORPORA)), (c.name for c in CORPORA), OPTION_CORPORA
        )
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error
    missing = unavailable(tools, corpora, os.environ, ROOT)
    if missing:
        raise pytest.UsageError("\n".join(missing))
    config.stash[_TOOLS_KEY] = tools
    config.stash[_CORPORA_KEY] = corpora


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply the cumulative selection contract in support.tiers; slow wins ties."""
    selected = selected_tier(
        extended=config.getoption("--extended"), slow=config.getoption("--slow")
    )
    for item in items:
        required = selected_tier(
            extended=item.get_closest_marker("extended") is not None,
            slow=item.get_closest_marker("slow") is not None,
        )
        if not includes(selected, required):
            option = "--slow" if required is Tier.SLOW else "--extended (or --slow)"
            item.add_marker(pytest.mark.skip(reason=tier_reason(required.name.lower(), option)))


def pytest_collection_finish(session: pytest.Session) -> None:
    """Record, per collected test, the tier, tools and corpora it needs and the run lacks."""
    config = session.config
    selected = selected_tier(
        extended=config.getoption("--extended"), slow=config.getoption("--slow")
    )
    tools: frozenset[str] = config.stash[_TOOLS_KEY]
    corpora: frozenset[str] = config.stash[_CORPORA_KEY]
    needs: list[Unmet] = []
    for item in session.items:
        required = selected_tier(
            extended=item.get_closest_marker("extended") is not None,
            slow=item.get_closest_marker("slow") is not None,
        )
        missing = None if includes(selected, required) else required.name.lower()
        fixtures: tuple[str, ...] = getattr(item, "fixturenames", ())
        needs.append(unmet(missing, fixtures, tools, corpora))
    config.stash[_UNMET_KEY] = needs


def _require(config: pytest.Config, name: str) -> Tool:
    """The selected tool ``name`` (availability was checked at configure time)."""
    if name not in config.stash[_TOOLS_KEY]:
        pytest.skip(tool_not_selected_reason(name))
    resource = next(t for t in TOOLS if t.name == name)
    tool = tool_from_env(resource.variable, os.environ, command=resource.command)
    assert tool is not None
    return tool


@pytest.fixture(scope="session")
def yosys_tool(request: pytest.FixtureRequest) -> Tool:
    """The configured ``yosys``; skips the test unless ``--external-tools`` selects it."""
    return _require(request.config, "yosys")


@pytest.fixture(scope="session")
def sby_tool(request: pytest.FixtureRequest) -> Tool:
    """The configured ``sby``; skips the test unless ``--external-tools`` selects it."""
    return _require(request.config, "sby")


@pytest.fixture(scope="session")
def sby_live_tool(request: pytest.FixtureRequest) -> Tool:
    """The configured liveness ``sby`` command; skips unless ``--external-tools`` selects it."""
    return _require(request.config, "sby-live")


@pytest.fixture(scope="session")
def slang_tool(request: pytest.FixtureRequest) -> Tool:
    """The configured ``slang`` executable; skips unless ``--external-tools`` selects it."""
    return _require(request.config, "slang")


@pytest.fixture(scope="session")
def ebmc_tool(request: pytest.FixtureRequest) -> Tool:
    """The configured EBMC command; skips unless ``--external-tools`` selects ``ebmc``."""
    return _require(request.config, "ebmc")


@pytest.fixture(scope="session")
def spot_dir(request: pytest.FixtureRequest) -> Path:
    """The directory that contains Spot's ``ltl2tgba`` (EBMC's ``--buechi`` finds it on its
    PATH); skips unless ``--external-tools`` selects ``spot``."""
    if "spot" not in request.config.stash[_TOOLS_KEY]:
        pytest.skip(tool_not_selected_reason("spot"))
    directory = tool_directory_from_env(ENV_LTL2TGBA, os.environ, ROOT, LTL2TGBA)
    assert directory is not None
    return directory


@pytest.fixture(scope="session")
def verilator_tool(request: pytest.FixtureRequest) -> Tool:
    """The configured Verilator executable; skips unless ``--external-tools`` selects it."""
    return _require(request.config, "verilator")


@pytest.fixture(scope="session")
def yosys_sva_tests(request: pytest.FixtureRequest) -> Path:
    """The checkout of the Yosys SVA tests (a relative path is taken from the repository
    root); skips the test unless ``--external-corpora`` selects ``yosys-sva``."""
    if "yosys-sva" not in request.config.stash[_CORPORA_KEY]:
        pytest.skip(corpus_not_selected_reason("yosys-sva"))
    directory = directory_from_env(ENV_YOSYS_SVA_TESTS, os.environ, ROOT)
    assert directory is not None
    return directory


@pytest.fixture(scope="session")
def ebmc_tests(request: pytest.FixtureRequest) -> Path:
    """The checkout of the EBMC regression tests (``regression/verilog/SVA`` of hw-cbmc; a
    relative path is taken from the repository root); skips the test unless
    ``--external-corpora`` selects ``ebmc``."""
    if "ebmc" not in request.config.stash[_CORPORA_KEY]:
        pytest.skip(corpus_not_selected_reason("ebmc"))
    directory = directory_from_env(ENV_EBMC_TESTS, os.environ, ROOT)
    assert directory is not None
    return directory


@pytest.fixture(scope="session")
def sby_examples(request: pytest.FixtureRequest) -> Path:
    """The checkout root of the SymbiYosys sources (``docs/examples`` and ``tests`` of
    YosysHQ/sby; a relative path is taken from the repository root); skips the test unless
    ``--external-corpora`` selects ``sby-examples``."""
    if "sby-examples" not in request.config.stash[_CORPORA_KEY]:
        pytest.skip(corpus_not_selected_reason("sby-examples"))
    directory = directory_from_env(ENV_SBY_EXAMPLES, os.environ, ROOT)
    assert directory is not None
    return directory


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """List what this run left out: tiers, unselected tools and corpora, each axis counted
    over all the collected tests (a test missing several things counts under each)."""
    other = 0
    for report in terminalreporter.stats.get("skipped", []):
        longrepr = getattr(report, "longrepr", None)
        reason = (
            str(longrepr[2]).removeprefix("Skipped: ")
            if isinstance(longrepr, tuple) and len(longrepr) == 3
            else ""
        )
        other += is_other_reason(reason)
    needs = terminalreporter.config.stash.get(_UNMET_KEY, [])
    lines = summarize(needs, other)
    if lines:
        terminalreporter.section("left out of this run")
        terminalreporter.write_line("(a test counts under every axis it misses)")
        for line in lines:
            terminalreporter.write_line(line)
