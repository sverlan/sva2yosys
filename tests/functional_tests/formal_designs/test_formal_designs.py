"""Small designs end to end: CLI, then Yosys/SBY (CONTRACT.md)."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from support.toolchain import SbyResult, SbyStatus, Tool, run_sby
from sva2yosys.cli import main

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXAMPLE = ROOT / "examples" / "handshake"


@dataclass(frozen=True)
class Design:
    top: str
    props: Path
    rtl: Path
    mutants: Path
    covers: dict[str, int]


DESIGNS = {
    "handshake": Design(
        "handshake",
        EXAMPLE / "props" / "handshake_props.sv",
        EXAMPLE / "rtl",
        FIXTURES / "handshake_mutants",
        {"c_done": 3},
    ),
    "delays": Design(
        "top",
        FIXTURES / "delays" / "props" / "dly_props.sv",
        FIXTURES / "delays" / "rtl",
        FIXTURES / "delays" / "mutants",
        {"c_out (u1)": 1, "c_out (u3)": 3},
    ),
    "pipe3": Design(
        "pipe3",
        FIXTURES / "pipe3" / "props" / "pipe3_props.sv",
        FIXTURES / "pipe3" / "rtl",
        FIXTURES / "pipe3" / "mutants",
        {"c_burst": 5},
    ),
    "fsm_enum": Design(
        "fsm",
        FIXTURES / "fsm_enum" / "props" / "fsm_props.sv",
        FIXTURES / "fsm_enum" / "rtl",
        FIXTURES / "fsm_enum" / "mutants",
        {"c_cycle": 2},
    ),
    "tied_reset": Design(
        "tied",
        FIXTURES / "tied_reset" / "props" / "stage_props.sv",
        FIXTURES / "tied_reset" / "rtl",
        FIXTURES / "tied_reset" / "mutants",
        {"c_follow (u0)": 1, "c_follow (u1)": 1, "c_follow (u2)": 1},
    ),
    "initial_counter": Design(
        "cnt",
        FIXTURES / "initial_counter" / "props" / "cnt_props.sv",
        FIXTURES / "initial_counter" / "rtl",
        FIXTURES / "initial_counter" / "mutants",
        {"c_three": 3},
    ),
}

MUTANTS = [
    ("handshake", "slow_ack", 4),
    ("handshake", "drops_req", 3),
    ("pipe3", "short", 3),
    ("delays", "long", 1),
    ("pipe3", "reset_keeps_last", 3),
    ("fsm_enum", "done_to_busy", 1),
    ("tied_reset", "late_reset", 1),
    ("initial_counter", "starts_at_one", 9),
]

_SBY = """[tasks]
{task}

[options]
mode {mode}
depth 10

[engines]
{engine}

[script]
script sva2yosys_read.ys
prep -top {top}

[files]
build/sva2yosys_read.ys
"""


def _generate(
    capsys: pytest.CaptureFixture[str],
    design: Design,
    build: Path,
    *rtl: Path,
    frontend: str = "verilog",
) -> None:
    libraries = [arg for directory in rtl for arg in ("-y", str(directory))]
    arguments = [str(design.props), *libraries, "--top", design.top]
    status = main([*arguments, "--yosys-frontend", frontend, "-o", str(build)])
    out, err = capsys.readouterr()
    assert status == 0, out + err


def _sby(tool: Tool, workdir: Path, design: Design, mode: str, engine: str) -> SbyResult:
    sby_file = workdir / f"{mode}.sby"
    sby_file.write_text(_SBY.format(task=mode, mode=mode, engine=engine, top=design.top))
    return run_sby(tool, sby_file, mode, workdir / f"work_{mode}")


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=() if index == 0 else pytest.mark.extended)
        for index, value in enumerate(sorted(DESIGNS))
    ],
)
def test_correct_design_proves_and_covers(
    capsys: pytest.CaptureFixture[str], sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """1."""
    design = DESIGNS[name]
    _generate(capsys, design, tmp_path / "build", design.rtl)
    proved = _sby(sby_tool, tmp_path, design, "prove", "abc pdr")
    assert proved.status is SbyStatus.PASS, proved.log[-3000:]
    covered = _sby(sby_tool, tmp_path, design, "cover", "smtbmc yices")
    assert covered.status is SbyStatus.PASS, covered.log[-3000:]
    steps = sorted(hit.step for hit in covered.reached_covers if hit.step is not None)
    assert len(covered.reached_covers) == len(design.covers), covered.log[-3000:]
    assert steps == sorted(design.covers.values()), covered.log[-3000:]


@pytest.mark.parametrize(
    ("name", "mutant", "step"),
    [
        pytest.param(*value, marks=() if index == 0 else pytest.mark.extended)
        for index, value in enumerate(MUTANTS)
    ],
    ids=[m[1] for m in MUTANTS],
)
def test_mutant_fails_at_the_earliest_sample(
    capsys: pytest.CaptureFixture[str],
    sby_tool: Tool,
    tmp_path: Path,
    name: str,
    mutant: str,
    step: int,
) -> None:
    """2."""
    design = DESIGNS[name]
    _generate(capsys, design, tmp_path / "build", design.mutants / mutant, design.rtl)
    result = _sby(sby_tool, tmp_path, design, "bmc", "smtbmc yices")
    assert result.status is SbyStatus.FAIL, result.log[-3000:]
    steps = [hit.step for hit in result.failed_assertions if hit.step is not None]
    assert steps and min(steps) == step, result.log[-3000:]


def test_the_shipped_example_works_as_documented(
    capsys: pytest.CaptureFixture[str], sby_tool: Tool, tmp_path: Path
) -> None:
    """3."""
    readme = (EXAMPLE / "README.md").read_text()
    assert "sva2yosys props/handshake_props.sv -y rtl --top handshake -o build" in readme
    assert "sby -f handshake.sby" in readme
    _generate(capsys, DESIGNS["handshake"], tmp_path / "build", EXAMPLE / "rtl")
    shutil.copy(EXAMPLE / "handshake.sby", tmp_path / "handshake.sby")
    for task in ("prove", "cover"):
        result = run_sby(sby_tool, tmp_path / "handshake.sby", task, tmp_path / f"work_{task}")
        assert result.status is SbyStatus.PASS, result.log[-3000:]


def test_an_unforeseen_parameter_value_fails_loudly(
    capsys: pytest.CaptureFixture[str], sby_tool: Tool, tmp_path: Path
) -> None:
    """4."""
    design = DESIGNS["delays"]
    _generate(capsys, design, tmp_path / "build", design.rtl)
    # Elaborate the rewritten design with N = 2, which Slang never saw.
    script = tmp_path / "build" / "sva2yosys_read.ys"
    top = (design.rtl / "top.sv").resolve()
    changed = tmp_path / "top_n2.sv"
    changed.write_text(top.read_text().replace("dly #(.N(3))", "dly #(.N(2))"))
    script.write_text(script.read_text().replace(str(top), str(changed.resolve())))
    result = _sby(sby_tool, tmp_path, design, "bmc", "smtbmc yices")
    assert result.status is SbyStatus.FAIL, result.log[-3000:]
    # The failing assertion is the fallback branch's: only the assertion depends on N (the
    # cover reads out[0] alone, so its monitor is not guarded by N); locate it in the
    # rewritten file.
    rewritten = (tmp_path / "build" / "dly_props.sv").read_text().splitlines()
    locations = [
        f"dly_props.sv:{n}.{column}-{n}.{column + 13}"
        for n, text in enumerate(rewritten, start=1)
        if "_unexpected" in text
        for column in [text.index("assert (1'b0)") + 1]
    ]
    assert len(locations) == 1
    # SBY prints the fallback's failure without a step: look for its location.
    assert any(location in result.log for location in locations), result.log[-3000:]


def _instance(hit_name: str) -> str:
    """The instance path of a reported check: its name up to the generated witness block, in
    the naming both frontends share (``read_verilog`` writes a generate block as an escaped
    identifier and adds a ``genblk`` level for the generated ``if``; ``read_slang`` does not)."""
    path = hit_name.split("._witness_")[0].strip().replace("\\", "")
    return re.sub(r"[.]genblk[0-9]+", "", path)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(value, marks=pytest.mark.extended)
        for index, value in enumerate(sorted(DESIGNS))
    ],
)
def test_slang_frontend_correct_design_proves_and_covers(
    capsys: pytest.CaptureFixture[str], sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """5."""
    design = DESIGNS[name]
    _generate(capsys, design, tmp_path / "build", design.rtl, frontend="slang")
    proved = _sby(sby_tool, tmp_path, design, "prove", "abc pdr")
    assert proved.status is SbyStatus.PASS, proved.log[-3000:]
    covered = _sby(sby_tool, tmp_path, design, "cover", "smtbmc yices")
    assert covered.status is SbyStatus.PASS, covered.log[-3000:]
    steps = sorted(hit.step for hit in covered.reached_covers if hit.step is not None)
    assert len(covered.reached_covers) == len(design.covers), covered.log[-3000:]
    assert steps == sorted(design.covers.values()), covered.log[-3000:]


@pytest.mark.parametrize(
    ("name", "mutant", "step"),
    [pytest.param(*value, marks=pytest.mark.extended) for index, value in enumerate(MUTANTS)],
    ids=[m[1] for m in MUTANTS],
)
def test_slang_frontend_mutant_fails_like_the_default_frontend(
    capsys: pytest.CaptureFixture[str],
    sby_tool: Tool,
    tmp_path: Path,
    name: str,
    mutant: str,
    step: int,
) -> None:
    """6."""
    design = DESIGNS[name]
    failing: dict[str, tuple[int, list[str]]] = {}
    for frontend in ("verilog", "slang"):
        build = tmp_path / frontend / "build"
        _generate(capsys, design, build, design.mutants / mutant, design.rtl, frontend=frontend)
        result = _sby(sby_tool, tmp_path / frontend, design, "bmc", "smtbmc yices")
        assert result.status is SbyStatus.FAIL, result.log[-3000:]
        steps = [hit.step for hit in result.failed_assertions if hit.step is not None]
        assert steps and min(steps) == step, result.log[-3000:]
        failing[frontend] = (
            min(steps),
            sorted({_instance(hit.name) for hit in result.failed_assertions}),
        )
    assert failing["slang"] == failing["verilog"]


@pytest.mark.extended
def test_the_slang_files_read_by_read_verilog_are_rejected(
    capsys: pytest.CaptureFixture[str], sby_tool: Tool, tmp_path: Path
) -> None:
    """7."""
    design = DESIGNS["handshake"]
    mutant = design.mutants / "drops_req"
    _generate(capsys, design, tmp_path / "build", mutant, design.rtl, frontend="slang")
    script = tmp_path / "build" / "sva2yosys_read.ys"
    words = script.read_text().splitlines()[1].split()
    files = [word for word in words if word.endswith(".sv")]
    script.write_text("".join(f"read_verilog -formal -sv {file}" + chr(10) for file in files))
    proved = _sby(sby_tool, tmp_path, design, "prove", "abc pdr")
    assert proved.status is SbyStatus.ERROR, proved.log[-3000:]
    assert "syntax error" in proved.log, proved.log[-3000:]


# --- 8: other solvers -------------------------------------------------------------------------


@pytest.mark.extended
@pytest.mark.parametrize("name", sorted(DESIGNS))
def test_z3_proves_and_covers_like_the_default_engines(
    capsys: pytest.CaptureFixture[str], sby_tool: Tool, tmp_path: Path, name: str
) -> None:
    """8."""
    design = DESIGNS[name]
    _generate(capsys, design, tmp_path / "build", design.rtl)
    proved = _sby(sby_tool, tmp_path, design, "prove", "smtbmc z3")
    assert proved.status is SbyStatus.PASS, proved.log[-3000:]
    covered = _sby(sby_tool, tmp_path, design, "cover", "smtbmc z3")
    assert covered.status is SbyStatus.PASS, covered.log[-3000:]
    steps = sorted(hit.step for hit in covered.reached_covers if hit.step is not None)
    assert len(covered.reached_covers) == len(design.covers), covered.log[-3000:]
    assert steps == sorted(design.covers.values()), covered.log[-3000:]


@pytest.mark.extended
@pytest.mark.parametrize("solver", ["z3", "bitwuzla"])
@pytest.mark.parametrize(("name", "mutant", "step"), MUTANTS, ids=[m[1] for m in MUTANTS])
def test_other_solvers_find_the_same_earliest_failure(
    capsys: pytest.CaptureFixture[str],
    sby_tool: Tool,
    tmp_path: Path,
    solver: str,
    name: str,
    mutant: str,
    step: int,
) -> None:
    """8."""
    design = DESIGNS[name]
    _generate(capsys, design, tmp_path / "build", design.mutants / mutant, design.rtl)
    result = _sby(sby_tool, tmp_path, design, "bmc", f"smtbmc {solver}")
    assert result.status is SbyStatus.FAIL, result.log[-3000:]
    steps = [hit.step for hit in result.failed_assertions if hit.step is not None]
    assert steps and min(steps) == step, result.log[-3000:]
