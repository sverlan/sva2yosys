"""Explicit selection of external tools and corpora for a test run.

Contract. A test run uses only local resources by default. External tools
(:data:`TOOLS`) and external corpora (:data:`CORPORA`) are selected by name with
``--external-tools`` and ``--external-corpora``; the value is ``none`` (the default),
``all``, or a comma-separated list of known names. This module holds the pure logic
(no pytest import); ``tests/conftest.py`` wires it into pytest.

* :func:`parse_selection` turns the option text into a set of names; any malformed
  text raises ``ValueError`` naming the option, the offender and the known names.
* :func:`unavailable` lists what a selection needs but the environment lacks (a tool whose
  :attr:`Resource.executable_in` is set is a directory that must contain that executable). A
  selected resource that is unavailable stops the run; it is never a skip.
* The ``*_reason`` functions build the skip reasons of the skips themselves, with stable
  prefixes (``tier ``, ``external tool not selected: ``, ``external corpus not selected: ``).
* The end-of-run summary does not count those reasons (pytest keeps one reason per skipped
  test, so a tier skip would hide a missing tool, and a test missing two tools would count
  once). :func:`unmet` computes, for each collected test, its needs that the run does not
  provide, per axis: the tier it needs above the selected one, the unselected tools and the
  unselected corpora whose fixture (:attr:`Resource.fixture`) is in its fixture closure.
  :func:`summarize` counts each axis independently over those, in a deterministic order:
  tiers, tools, corpora, other (the skips whose reason is outside the three axes,
  :func:`is_other_reason`). A test counts under every axis it misses, so the lines do not add
  up to a number of tests.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from support.toolchain import (
    ENV_EBMC,
    ENV_EBMC_TESTS,
    ENV_LTL2TGBA,
    ENV_SBY,
    ENV_SBY_EXAMPLES,
    ENV_SBY_LIVE,
    ENV_SLANG,
    ENV_YOSYS,
    ENV_YOSYS_SVA_TESTS,
    LTL2TGBA,
    directory_from_env,
    tool_directory_from_env,
    tool_from_env,
)
from support.verilator import ENV_VERILATOR
from support.verilator import configuration as verilator_configuration


@dataclass(frozen=True)
class Resource:
    """A selectable external resource.

    Attributes:
        name: The name used on the command line.
        variable: The environment variable that configures it.
        command: For a tool, whether the variable holds a command (see
            :func:`support.toolchain.tool_from_env`). Unused for a corpus.
    """

    name: str
    variable: str
    command: bool = False
    fixture: str = ""
    """The pytest fixture through which a test requests the resource."""
    executable_in: str = ""
    """For a tool: when set, the variable names a directory that must contain a file of this
    name (Spot's ``ltl2tgba``, found on another tool's PATH) instead of an executable."""


TOOLS: tuple[Resource, ...] = (
    Resource("yosys", ENV_YOSYS, fixture="yosys_tool"),
    Resource("sby", ENV_SBY, fixture="sby_tool"),
    Resource("sby-live", ENV_SBY_LIVE, command=True, fixture="sby_live_tool"),
    Resource("slang", ENV_SLANG, fixture="slang_tool"),
    Resource("ebmc", ENV_EBMC, command=True, fixture="ebmc_tool"),
    Resource("spot", ENV_LTL2TGBA, fixture="spot_dir", executable_in=LTL2TGBA),
    Resource("verilator", ENV_VERILATOR, fixture="verilator_tool"),
)
"""The selectable external tools, in summary order."""

CORPORA: tuple[Resource, ...] = (
    Resource("yosys-sva", ENV_YOSYS_SVA_TESTS, fixture="yosys_sva_tests"),
    Resource("ebmc", ENV_EBMC_TESTS, fixture="ebmc_tests"),
    Resource("sby-examples", ENV_SBY_EXAMPLES, fixture="sby_examples"),
)
"""The selectable external corpora (directories), in summary order."""

OPTION_TOOLS = "--external-tools"
OPTION_CORPORA = "--external-corpora"

_TIER_PREFIX = "tier "
_TOOL_PREFIX = "external tool not selected: "
_CORPUS_PREFIX = "external corpus not selected: "


def parse_selection(text: str, known: Iterable[str], option: str) -> frozenset[str]:
    """Parse the value of a selection option.

    Args:
        text: ``none`` (or blank) for nothing, ``all`` for every known name, or a
            comma-separated list of known names (whitespace ignored, duplicates fine).
        known: The known names.
        option: The option name, used in error messages.

    Returns:
        The selected names.

    Raises:
        ValueError: An unknown name, an empty item, or ``all``/``none`` mixed with
            other items. The message names the option, the offender and the known names.
    """
    names = tuple(known)
    listing = ", ".join(names)
    stripped = text.strip()
    if not stripped:
        return frozenset()
    items = [item.strip() for item in stripped.split(",")]
    for item in items:
        if not item:
            raise ValueError(
                f"{option}: empty item in {text!r}; use none, all or names from: {listing}"
            )
    if len(items) == 1 and items[0] == "none":
        return frozenset()
    if len(items) == 1 and items[0] == "all":
        return frozenset(names)
    for item in items:
        if item in ("all", "none"):
            raise ValueError(f"{option}: {item!r} cannot be combined with other items in {text!r}")
        if item not in names:
            raise ValueError(f"{option}: unknown name {item!r}; known names: {listing}")
    return frozenset(items)


def unavailable(
    tools: Iterable[str],
    corpora: Iterable[str],
    environ: Mapping[str, str],
    root: Path,
) -> list[str]:
    """List the selected resources the environment cannot provide.

    A tool is available as :func:`support.toolchain.tool_from_env` says (a command
    variable only has to be non-blank); a corpus as
    :func:`support.toolchain.directory_from_env` says (relative to ``root``).

    Args:
        tools: Selected tool names (members of :data:`TOOLS`).
        corpora: Selected corpus names (members of :data:`CORPORA`).
        environ: The environment to read.
        root: Base for a relative corpus path.

    Returns:
        One message per unavailable resource, tools first, in declaration order;
        empty when everything selected is available.
    """
    selected_tools = frozenset(tools)
    selected_corpora = frozenset(corpora)
    messages: list[str] = []
    for tool in TOOLS:
        if tool.name not in selected_tools:
            continue
        if tool.name == "verilator":
            try:
                verilator_configuration(environ)
            except ValueError as error:
                messages.append(
                    f"external tool verilator is selected but its configuration is invalid: "
                    f"{error} (see .env.example)"
                )
            continue
        if tool.executable_in:
            found = tool_directory_from_env(tool.variable, environ, root, tool.executable_in)
            if found is None:
                messages.append(
                    f"external tool {tool.name} is selected but {tool.variable} is unset "
                    f"or does not name a directory containing {tool.executable_in} "
                    "(see .env.example)"
                )
        elif tool_from_env(tool.variable, environ, command=tool.command) is None:
            what = "a command" if tool.command else "an existing file"
            messages.append(
                f"external tool {tool.name} is selected but {tool.variable} is unset "
                f"or does not name {what} (see .env.example)"
            )
    for corpus in CORPORA:
        if corpus.name in selected_corpora and (
            directory_from_env(corpus.variable, environ, root) is None
        ):
            messages.append(
                f"external corpus {corpus.name} is selected but {corpus.variable} is unset "
                "or does not name an existing directory (see .env.example)"
            )
    return messages


def tier_reason(tier_name: str, option: str) -> str:
    """Skip reason of a test left out by the tier selection, e.g. ``tier slow: run with --slow``."""
    return f"{_TIER_PREFIX}{tier_name}: run with {option}"


def tool_not_selected_reason(name: str) -> str:
    """Skip reason of a test needing a tool that was not selected."""
    return f"{_TOOL_PREFIX}{name} ({OPTION_TOOLS}={name} or all)"


def corpus_not_selected_reason(name: str) -> str:
    """Skip reason of a test needing a corpus that was not selected."""
    return f"{_CORPUS_PREFIX}{name} ({OPTION_CORPORA}={name} or all)"


@dataclass(frozen=True)
class Unmet:
    """What one collected test needs that the run does not provide, per axis.

    Attributes:
        tier: ``extended`` or ``slow`` when the test needs a higher tier than the selected
            one, else ``None``.
        tools: Names of the unselected tools the test needs.
        corpora: Names of the unselected corpora the test needs.
    """

    tier: str | None = None
    tools: frozenset[str] = frozenset()
    corpora: frozenset[str] = frozenset()


def unmet(
    tier: str | None,
    fixtures: Iterable[str],
    selected_tools: Iterable[str],
    selected_corpora: Iterable[str],
) -> Unmet:
    """The unmet needs of a test whose fixture closure is ``fixtures`` (``item.fixturenames``)
    and whose missing tier, if any, is ``tier``; the tools and corpora are those whose
    fixture is requested and whose name is not selected."""
    names = frozenset(fixtures)
    tools, corpora = frozenset(selected_tools), frozenset(selected_corpora)
    return Unmet(
        tier,
        frozenset(t.name for t in TOOLS if t.fixture in names and t.name not in tools),
        frozenset(c.name for c in CORPORA if c.fixture in names and c.name not in corpora),
    )


def summarize(needs: Iterable[Unmet], other: int = 0) -> list[str]:
    """Summary lines of what a run leaves out, computed per axis independently.

    Counts the tests per tier left out (``extended`` then ``slow``), per unselected tool (in
    :data:`TOOLS` order) and per unselected corpus (in :data:`CORPORA` order), each over the
    collected tests, whatever their other needs: a test missing a tier and two tools is
    counted under all three, so the lines do not add up to a number of tests. ``other`` (the
    number of skips for any other reason) closes the list. Categories with a zero count are
    omitted.

    Args:
        needs: One :class:`Unmet` per collected test.
        other: Tests skipped for a reason outside the three axes.

    Returns:
        The lines; empty when nothing was left out.
    """
    order = [
        *(f"tier {name}" for name in ("extended", "slow")),
        *(f"tool {tool.name}" for tool in TOOLS),
        *(f"corpus {corpus.name}" for corpus in CORPORA),
        "other",
    ]
    counts = dict.fromkeys(order, 0)
    counts["other"] = other
    for need in needs:
        if need.tier is not None:
            counts[f"tier {need.tier}"] += 1
        for tool in need.tools:
            counts[f"tool {tool}"] += 1
        for corpus in need.corpora:
            counts[f"corpus {corpus}"] += 1
    return [_line(key, counts[key]) for key in order if counts[key]]


def is_other_reason(reason: str) -> bool:
    """Whether a skip reason is outside the three axes (tier, tool, corpus)."""
    return _category(reason) == "other"


def _line(key: str, count: int) -> str:
    if key.startswith("tier "):
        return f"{key[5:]} tier: {count} tests (run with --{key[5:]})"
    if key.startswith("tool "):
        return f"external tool {key[5:]}: {count} tests ({OPTION_TOOLS}={key[5:]} or all)"
    if key.startswith("corpus "):
        return f"external corpus {key[7:]}: {count} tests ({OPTION_CORPORA}={key[7:]} or all)"
    return f"other: {count} tests"


def _category(reason: str) -> str:
    """The summary category of a skip reason."""
    if reason.startswith(_TIER_PREFIX):
        return "tier " + reason[len(_TIER_PREFIX) :].split(":", 1)[0]
    if reason.startswith(_TOOL_PREFIX):
        return "tool " + reason[len(_TOOL_PREFIX) :].split(" ", 1)[0]
    if reason.startswith(_CORPUS_PREFIX):
        return "corpus " + reason[len(_CORPUS_PREFIX) :].split(" ", 1)[0]
    return "other"
