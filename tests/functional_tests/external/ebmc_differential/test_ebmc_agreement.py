"""The EBMC differential tier: the trace tables against EBMC and Spot (CONTRACT.md)."""

from __future__ import annotations

import dataclasses
import inspect
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from support.ebmc_differential import (
    AGREES,
    CLASSES,
    DISAGREES,
    ERROR,
    NOT_COMPARED,
    Engine,
    Judgement,
    Selection,
    case_key,
    ebmc_version,
    expected_verdicts,
    load_all_cases,
    parse_allow_list,
    run_job,
    select,
    spot_command,
    spot_version,
    traces_digest,
)
from support.toolchain import Tool, path_for_tool, run_tool

AREA = Path(__file__).resolve().parent
EXPECTATIONS = AREA / "fixtures" / "expectations.txt"
HEADERS = ("ebmc-version", "spot-version", "traces-sha256")

ALLOW = parse_allow_list((AREA / "fixtures" / "allow_list.txt").read_text(encoding="utf-8"))
CASES = {case_key(case): case for case in load_all_cases()}
SELECTIONS = [selection for case in CASES.values() for selection in select(case, ALLOW)]
COMPARED = [selection for selection in SELECTIONS if selection.engine is not None]


@dataclass(frozen=True)
class Line:
    key: str  # <trace file stem>:<case>
    engine: str  # an engine name, or "-" for a case no engine compares
    cls: str  # one of CLASSES, or TODO for a disagreement not yet derived
    detail: str


def _read() -> tuple[dict[str, str], dict[tuple[str, str], Line]]:
    headers: dict[str, str] = {}
    lines: dict[tuple[str, str], Line] = {}
    for raw in EXPECTATIONS.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        key, _, value = raw.partition(": ")
        if key in HEADERS:
            headers[key] = value.strip()
            continue
        text = raw.partition(" # ")[0]
        name, engine, rest = text.split(maxsplit=2)
        cls, detail = _split_class(rest)
        lines[(name, engine)] = Line(name, engine, cls, detail)
    return headers, lines


def _split_class(text: str) -> tuple[str, str]:
    for cls in (*CLASSES, "TODO"):
        if text == cls or text.startswith(cls + " "):
            return cls, text[len(cls) :].strip()
    return text, ""


HEADER, TABLE = _read()


def _line(selection: Selection) -> Line:
    engine = selection.engine.value if selection.engine is not None else "-"
    return TABLE[(selection.key, engine)]


# --- the digest and the versions (CONTRACT.md 9) ----------------------------------------------

_DIGEST_MESSAGE = (
    "the trace files changed since fixtures/expectations.txt was written: classify every case "
    "again (python tests/support/ebmc_differential.py --draft) and update its traces-sha256 line"
)


@pytest.fixture(scope="session")
def classified_traces() -> None:
    """The trace files, once their digest is checked: on other content every tool test of this
    area fails with the one message (CONTRACT.md 9)."""
    if traces_digest() != HEADER["traces-sha256"]:
        pytest.fail(_DIGEST_MESSAGE, pytrace=False)


@pytest.fixture(scope="session")
def classified_ebmc(
    classified_traces: None, ebmc_tool: Tool, tmp_path_factory: pytest.TempPathFactory
) -> Tool:
    """Check EBMC's version before any comparison, including EBMC-only selections."""
    found = ebmc_version(ebmc_tool, tmp_path_factory.mktemp("ebmc-version"))
    assert found == HEADER["ebmc-version"], (
        f"EBMC {found} is not the classified {HEADER['ebmc-version']}: classify again"
    )
    return ebmc_tool


@pytest.fixture(scope="session")
def classified_spot(
    classified_ebmc: Tool, spot_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """Check Spot's version before any Büchi comparison."""
    found = spot_version(classified_ebmc, spot_dir, tmp_path_factory.mktemp("spot-version"))
    assert found == HEADER["spot-version"], (
        f"Spot {found} is not the classified {HEADER['spot-version']}: classify again"
    )
    return spot_dir


def test_the_traces_are_the_classified_ones() -> None:
    """9. The representative: the digest check itself, needing no tool."""
    assert traces_digest() == HEADER["traces-sha256"], _DIGEST_MESSAGE


def test_an_ebmc_only_control_rejects_an_unclassified_version(tmp_path: Path) -> None:
    """9. Review counterexample: a selected control must check EBMC without Spot."""
    marker = tmp_path / "design-ran"
    fake = tmp_path / "ebmc.py"
    fake.write_text(
        "import sys\nfrom pathlib import Path\n"
        "if '--version' in sys.argv:\n    print('unclassified version')\n"
        f"else:\n    Path({str(marker)!r}).touch()\n"
        "    print('** Results:\\n[top.p] property: PROVED')\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["SVA2YOSYS_EBMC"] = f"{sys.executable} {fake}"
    env.pop("SVA2YOSYS_LTL2TGBA", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(Path(__file__).resolve()),
            "-k",
            "test_the_bmc_and_bdd_controls_agree and p05_latest_valid_match",
            "--external-tools=ebmc",
            "--external-corpora=none",
            f"--basetemp={tmp_path / 'child'}",
            "-o",
            f"cache_dir={tmp_path / 'cache'}",
            "-q",
            "--tb=short",
        ],
        cwd=AREA.parents[3],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 1, output
    assert "unclassified version is not the classified" in output, output
    assert not marker.exists(), "the design ran before the version was checked"


def test_every_tool_test_depends_on_the_digest() -> None:
    """9. Tool tests reach tools through fixtures checking the digest and versions."""
    direct = [
        name
        for name, function in globals().items()
        if name.startswith("test_")
        and callable(function)
        and ("ebmc_tool" in inspect.signature(function).parameters)
    ]
    assert direct == []


def test_ebmc_is_the_classified_version(classified_ebmc: Tool) -> None:
    """9. The EBMC guard runs without requiring Spot."""


def test_spot_is_the_classified_version(classified_spot: Path) -> None:
    """9. The Spot guard uses the same execution environment as EBMC."""


# --- the table (CONTRACT.md 5-7) -------------------------------------------------------------


def test_the_table_is_well_formed() -> None:
    """5-7, 9. Needs no tool: the headers, the classes, no unclassified disagreement."""
    assert sorted(HEADER) == sorted(HEADERS)
    assert re.fullmatch(r"[0-9a-f]{64}", HEADER["traces-sha256"])
    assert HEADER["ebmc-version"] == "6.1 (ebmc-6.1)"
    assert HEADER["spot-version"] == "2.16.1"
    engines = {engine.value for engine in Engine} | {"-"}
    for line in TABLE.values():
        assert line.cls != "TODO", f"{line.key} {line.engine}: a disagreement to derive by hand"
        assert line.cls in CLASSES, line
        assert line.cls != DISAGREES, line
        assert line.engine in engines, line
        if line.cls in (NOT_COMPARED, ERROR):
            assert line.detail, line
        if line.engine == "-":
            assert line.cls == NOT_COMPARED, line


def test_the_table_lists_exactly_the_selections_of_the_allow_list() -> None:
    """5. A case missing from the table, a stale case, or another engine fails; so does a
    reason that is not the allow-list's."""
    expected = {(s.key, s.engine.value if s.engine is not None else "-"): s for s in SELECTIONS}
    assert set(TABLE) == set(expected)
    for key, selection in expected.items():
        if selection.engine is None:
            assert TABLE[key].detail == selection.reason, key


def test_every_exclusion_and_shape_is_used() -> None:
    """1, 4. Needs no tool: no stale exclusion, and every shape admits cases."""
    reasons = {s.reason for s in SELECTIONS if s.engine is None}
    for exclusion in ALLOW.exclusions:
        assert exclusion.slug in reasons, f"exclusion {exclusion.slug} excludes no case"
    admitted = {s.shape for s in COMPARED}
    for shape in ALLOW.shapes:
        assert shape.name in admitted, f"shape {shape.name} admits no case"
    assert "unlisted-shape" in reasons


def _shape_control_pairs() -> list[tuple[str, str, str]]:
    return [
        (shape.name, role, key)
        for shape in ALLOW.shapes
        for role, key in (("positive", shape.positive), ("negative", shape.negative))
    ]


@pytest.mark.parametrize(("shape_name", "role", "key"), _shape_control_pairs(), ids=str)
def test_the_controls_are_admitted_by_their_shape_with_the_right_polarity(
    shape_name: str, role: str, key: str
) -> None:
    """4. Needs no tool: the control exists, its shape is the first of its engine to admit
    it, its expected verdict has the polarity of its role, and the table says it agrees."""
    shape = next(s for s in ALLOW.shapes if s.name == shape_name)
    assert key in CASES, f"{shape_name}: no case {key}"
    case = CASES[key]
    chosen = [s for s in select(case, ALLOW) if s.engine is shape.engine]
    assert [s.shape for s in chosen] == [shape_name], (shape_name, key)
    verdicts = set(expected_verdicts(case, shape.engine).values())
    if role == "positive":
        assert verdicts <= {"pass", "reached"}, (shape_name, key, verdicts)
    else:
        assert verdicts & {"fail", "unreached"}, (shape_name, key, verdicts)
    assert TABLE[(key, shape.engine.value)].cls == AGREES, (shape_name, key)


# --- running the cases (CONTRACT.md 3, 4, 8, 10, 11) ------------------------------------------

_RESULTS: dict[tuple[str, str], Judgement] = {}
"""The judgement of every (case, engine) run in the session, so each runs once."""


@dataclass
class Runner:
    ebmc: Tool
    spot: Path | None
    work: Path
    results: dict[tuple[str, str], Judgement] = field(default_factory=lambda: _RESULTS)

    def judgement(self, selection: Selection) -> Judgement:
        assert selection.engine is not None
        name = (selection.key, selection.engine.value)
        if name not in self.results:
            directory = self.work / selection.engine.value / re.sub(r"\W", "_", selection.key)
            self.results[name] = run_job(
                self.ebmc, self.spot, selection.case, selection.engine, directory
            )
        return self.results[name]


@pytest.fixture(scope="session")
def runner(classified_ebmc: Tool, tmp_path_factory: pytest.TempPathFactory) -> Runner:
    """Runs the ``bmc`` and ``bdd`` cases with EBMC alone."""
    return Runner(classified_ebmc, None, tmp_path_factory.mktemp("ebmc"))


@pytest.fixture(scope="session")
def buechi_runner(
    classified_ebmc: Tool,
    classified_spot: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> Runner:
    """Runs every case, the ``buechi`` ones with Spot's directory on EBMC's PATH."""
    return Runner(classified_ebmc, classified_spot, tmp_path_factory.mktemp("ebmc-buechi"))


def _check(runner: Runner, selection: Selection) -> None:
    """8. The case's class is the recorded one, with the recorded detail."""
    line = _line(selection)
    found = runner.judgement(selection)
    where = f"{selection.key} [{selection.engine}]: ours {found.ours}, ebmc {found.ebmc}"
    assert found.cls == line.cls, f"{where}: {found.cls} {found.detail}, recorded {line.cls}"
    assert line.detail in found.detail, f"{where}: {found.detail!r} lacks {line.detail!r}"


def _id(selection: Selection) -> str:
    return f"{selection.engine.value if selection.engine else '-'}-{selection.key}"


def _controls(engines: tuple[Engine, ...]) -> list[Selection]:
    """The selections of the positive and negative control of every shape of ``engines``."""
    found: dict[tuple[str, str], Selection] = {}
    for shape in ALLOW.shapes:
        if shape.engine not in engines:
            continue
        for key in (shape.positive, shape.negative):
            for selection in COMPARED:
                if (
                    selection.key == key
                    and selection.engine is shape.engine
                    and selection.shape == shape.name
                ):
                    found[(key, shape.engine.value)] = selection
    return list(found.values())


@pytest.mark.parametrize("selection", _controls((Engine.BMC, Engine.BDD)), ids=_id)
def test_the_bmc_and_bdd_controls_agree(runner: Runner, selection: Selection) -> None:
    """4, 11. Positive and negative control of every `bmc` and `bdd` shape."""
    assert _line(selection).cls == AGREES
    _check(runner, selection)


@pytest.mark.parametrize("selection", _controls((Engine.BUECHI,)), ids=_id)
def test_the_buechi_controls_agree(buechi_runner: Runner, selection: Selection) -> None:
    """4, 10, 11. Positive and negative control of every `buechi` shape."""
    assert _line(selection).cls == AGREES
    _check(buechi_runner, selection)


def test_the_comparison_can_disagree(runner: Runner, tmp_path: Path) -> None:
    """4. A control whose expected verdict is flipped in the table is a disagreement: the
    comparison has teeth, in both directions, for a bounded case."""
    shape = next(s for s in ALLOW.shapes if s.name == "bmc-implication")
    passing = CASES[shape.positive]
    failing = CASES[shape.negative]
    wrong_pass = dataclasses.replace(passing, fails=frozenset({0}))
    wrong_fail = dataclasses.replace(failing, fails=frozenset())
    for case in (wrong_pass, wrong_fail):
        found = run_job(runner.ebmc, None, case, Engine.BMC, tmp_path / case.name)
        assert found.cls == DISAGREES, found


def test_the_buechi_comparison_can_disagree(buechi_runner: Runner, tmp_path: Path) -> None:
    """4. The same for a lasso case checked through Spot: a flipped infinite outcome."""
    shape = next(s for s in ALLOW.shapes if s.name == "buechi-s-eventually")
    case = CASES[shape.positive]
    assert case.inf_outcomes is not None
    flipped = dataclasses.replace(case, inf_outcomes=("F",) * len(case.inf_outcomes))
    found = run_job(buechi_runner.ebmc, buechi_runner.spot, flipped, Engine.BUECHI, tmp_path)
    assert found.cls == DISAGREES, found


def test_spot_reaches_ebmc_only_through_its_directory_on_the_path(
    buechi_runner: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """10. The configured translator takes precedence over an ambient Spot installation."""
    shape = next(s for s in ALLOW.shapes if s.name == "buechi-s-eventually")
    case = CASES[shape.positive]
    assert buechi_runner.spot is not None
    # Review counterexample: Spot may already be installed on the inherited PATH.
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(buechi_runner.spot), os.environ.get("PATH", "")])
    )
    blocked = tmp_path / "blocked-spot ' $literal"
    blocked.mkdir()
    shim = blocked / "ltl2tgba"
    shim.write_text("#!/bin/sh\nexit 73\n", encoding="utf-8", newline="\n")
    shim.chmod(0o755)
    if Path(buechi_runner.ebmc.command[0]).stem.lower() == "wsl":
        # Linux's own directories precede imported Windows PATH entries. Check the
        # actual Linux environment, including a directory with shell metacharacters.
        prefix = spot_command(buechi_runner.ebmc, buechi_runner.spot)[:-1]
        bare = Tool("WSL-PATH-PROBE", (buechi_runner.ebmc.command[0], "/bin/sh"))
        literal = "$HOME ' $(literal)"
        for tool, launch in ((buechi_runner.ebmc, prefix), (bare, [bare.command[0]])):
            probe = run_tool(
                tool,
                [
                    *launch,
                    "/bin/sh",
                    "-c",
                    'printf "%s\\n%s" "$PATH" "$1"',
                    "path-probe",
                    literal,
                ],
                tmp_path,
                30,
                "Linux PATH probe",
                path_prefix=str(blocked),
            )
            assert probe.returncode == 0, probe.stderr
            search_path, echoed = probe.stdout.split("\n", maxsplit=1)
            assert search_path.split(":")[0] == path_for_tool(tool, blocked)
            assert echoed == literal
    without = run_job(buechi_runner.ebmc, blocked, case, Engine.BUECHI, tmp_path / "without")
    assert without.cls in (ERROR, NOT_COMPARED), without
    assert without.cls != AGREES
    assert buechi_runner.spot is not None
    with_spot = run_job(
        buechi_runner.ebmc, buechi_runner.spot, case, Engine.BUECHI, tmp_path / "with"
    )
    assert with_spot.cls == AGREES, with_spot


@pytest.mark.slow
@pytest.mark.parametrize(
    "selection",
    [s for s in COMPARED if s.engine in (Engine.BMC, Engine.BDD)],
    ids=_id,
)
def test_every_bmc_and_bdd_case_has_its_recorded_class(
    runner: Runner, selection: Selection
) -> None:
    """5-8."""
    _check(runner, selection)


@pytest.mark.slow
@pytest.mark.parametrize(
    "selection",
    [s for s in COMPARED if s.engine is Engine.BUECHI],
    ids=_id,
)
def test_every_buechi_case_has_its_recorded_class(
    buechi_runner: Runner, selection: Selection
) -> None:
    """5-8."""
    _check(buechi_runner, selection)
