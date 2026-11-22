"""Compact DIRECT against the frozen 6a dense emitter (CONTRACT point 21)."""

import hashlib
import re
from dataclasses import replace
from pathlib import Path

import pytest

from support.dense_slots import module as dense_module
from support.monitor_text import without_wire_comments
from support.toolchain import SbyStatus, Tool, run_sby, run_yosys
from sva2yosys.codegen.direct import layout
from sva2yosys.codegen.systemverilog import (
    declare,
    monitor_module,
    monitor_port_types,
)
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangError, SlangOptions
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.automaton import Automaton
from sva2yosys.temporal.compile import CompileError, Monitor, compile_monitors
from sva2yosys.temporal.configurations import ConfigMachine
from sva2yosys.temporal.machine import Machine

FIXTURES = Path(__file__).parent / "fixtures"


def _compile(prop: str, kind: str = "assert") -> Monitor:
    source = (FIXTURES / "depth.sv").read_text(encoding="utf-8")
    source = source.replace("@KIND@", kind).replace("@PROPERTY@", prop)
    conversion = convert(export_texts([("compact.sv", source)], SlangOptions(top=("tc",))))
    assert not conversion.rejections
    (directive,) = conversion.directives
    (machine,) = compile_monitors(directive)
    return machine


def _machine(prop: str, kind: str = "assert") -> Machine:
    machine = _compile(prop, kind)
    assert isinstance(machine, Machine)
    return machine


def _equivalence(tool: Tool, directory: Path, machine: Machine, mutant: bool = False) -> SbyStatus:
    compact = monitor_module(machine, "mon")
    if mutant:
        # Origin: plan 6b discharge mutant. With a only at t=0 and b only at t=1,
        # the real obligation passes at t=1. Keeping its successors can fail at t=3.
        assert "!pipe1_accept &" in compact
        compact = compact.replace("!pipe1_accept &", "1'b1 &")
    (directory / "monitors.sv").write_text(
        dense_module(machine, "dense") + "\n" + compact, encoding="utf-8"
    )
    types = monitor_port_types(machine)
    miter = (FIXTURES / "compact_miter.sv").read_text(encoding="utf-8")
    miter = miter.replace(
        "@PORTS@", ", ".join(f"input {declare(t, f'i{i}')}" for i, t in enumerate(types))
    )
    miter = miter.replace("@CONNECTIONS@", "".join(f".i{i}(i{i}), " for i in range(len(types))))
    (directory / "miter.sv").write_text(miter, encoding="utf-8")
    script = directory / "compact.sby"
    script.write_text((FIXTURES / "compact.sby").read_text(encoding="utf-8"), encoding="utf-8")
    result = run_sby(tool, script, "prove", directory / "work")
    assert result.status in (SbyStatus.PASS, SbyStatus.FAIL), result.log[-4000:]
    return result.status


@pytest.mark.parametrize(
    "prop", ["a |-> ##3 b", "a |-> b[*3]", "a |-> ##[0:3] b", "a |-> ##[1:3] $past(b)"]
)
def test_compact_events_equal_dense_at_every_sample(
    sby_tool: Tool, tmp_path: Path, prop: str
) -> None:
    machine = _machine(prop)
    assert layout(machine) is not None
    assert _equivalence(sby_tool, tmp_path, machine) is SbyStatus.PASS


@pytest.mark.parametrize(
    ("prop", "fixture"),
    [
        ("a |-> ##2 b", "compact_delay_expected.sv"),
        ("a |-> b[*3]", "compact_repeat_expected.sv"),
        ("a |-> ##[1:3] b", "compact_window_expected.sv"),
    ],
)
def test_events_equal_hand_derived_sample_rules(
    sby_tool: Tool, tmp_path: Path, prop: str, fixture: str
) -> None:
    (tmp_path / "monitors.sv").write_text(monitor_module(_machine(prop), "mon"), encoding="utf-8")
    (tmp_path / "miter.sv").write_text(
        (FIXTURES / fixture).read_text(encoding="utf-8"), encoding="utf-8"
    )
    script = tmp_path / "compact.sby"
    script.write_text((FIXTURES / "compact.sby").read_text(encoding="utf-8"), encoding="utf-8")
    result = run_sby(sby_tool, script, "prove", tmp_path / "work")
    assert result.status is SbyStatus.PASS, result.log[-4000:]


SMALL = (
    [f"a |-> ##{n} b" for n in (0, 1, 2, 3, 8)]
    + [f"a |=> b[*{n}]" for n in (1, 2, 3, 8)]
    + [f"a |-> ##[0:{n}] b" for n in (0, 1, 2, 3, 8)]
    + [f"a |=> ##[1:{n}] b" for n in (1, 2, 3, 8)]
    + [
        "a |-> b ##1 c",
        "a |-> not (b[*3])",
        "a |-> ##2 1'b0",
        "a |-> ##[0:3] 1'b1",
        "$rose(a) |-> ##[1:3] $past(b)",
        "a |-> b[*1:3]",
        "a |-> ##[1:3] b ##1 c",  # Deliberate dense fallback.
    ]
)


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["assert", "assume", "cover"])
@pytest.mark.parametrize("prop", SMALL)
def test_boundary_and_sampling_events_equal_dense(
    sby_tool: Tool, tmp_path: Path, prop: str, kind: str
) -> None:
    assert _equivalence(sby_tool, tmp_path, _machine(prop, kind)) is SbyStatus.PASS


def test_failing_to_discharge_an_accepted_obligation_is_caught(
    sby_tool: Tool, tmp_path: Path
) -> None:
    assert _equivalence(sby_tool, tmp_path, _machine("a |-> ##[1:3] b"), True) is SbyStatus.FAIL


@pytest.mark.parametrize("empty", ["antecedent", "consequent"])
def test_empty_automata_preserve_events(sby_tool: Tool, tmp_path: Path, empty: str) -> None:
    machine = _machine("a |-> b")
    if empty == "antecedent":
        machine = replace(machine, antecedent=Automaton((), (), False))
    else:
        machine = replace(machine, consequent=Automaton((), (), False), slots=0)
    assert _equivalence(sby_tool, tmp_path, machine) is SbyStatus.PASS


@pytest.mark.parametrize("prop", ["a |-> b[*0]", "a[*0:1] |=> b"])
def test_illegal_nullable_boundaries_still_reject(prop: str) -> None:
    with pytest.raises((SlangError, CompileError), match="empty"):
        _machine(prop)


@pytest.mark.parametrize("shape", ["##N b", "b[*N]", "##[1:N] b"])
def test_selected_logic_has_linear_counts(shape: str) -> None:
    for n in (16, 32, 64):
        machine = _machine("a |-> " + shape.replace("N", str(n)))
        assert layout(machine) is not None
        monitor = monitor_module(machine, "mon")
        # Bounds count syntax/terms, not bytes; names grow as log N.
        assert len(re.findall(r"\bassign\b", monitor)) < 16 * n + 64
        assert len(re.findall(r"[&|?:]", monitor)) < 128 * n + 128


@pytest.mark.slow
@pytest.mark.parametrize("n", [1000, 4096])
@pytest.mark.parametrize("shape", ["##N b", "b[*N]", "##[1:N] b"])
@pytest.mark.parametrize("kind", ["assert", "assume", "cover"])
def test_large_compact_pipelines_load_and_prepare(
    yosys_tool: Tool, sby_tool: Tool, tmp_path: Path, n: int, shape: str, kind: str
) -> None:
    machine = _machine("a |-> " + shape.replace("N", str(n)), kind)
    assert layout(machine) is not None
    monitor = monitor_module(machine, "mon")
    assert len(re.findall(r"\bassign\b", monitor)) < 16 * n + 64
    assert len(re.findall(r"[&|?:]", monitor)) < 128 * n + 128
    _process(yosys_tool, sby_tool, tmp_path, machine, monitor)


def test_dense_oracle_retains_its_pre_compaction_bytes() -> None:
    # compact_dense.sha256 was re-recorded when the monitor header gained the comment lines
    # // as written:, // clock:, // disable iff: (without them the text hashes as before); the
    # comment that ends each condition wire is removed here before hashing.
    for row in (FIXTURES / "compact_dense.sha256").read_text(encoding="utf-8").splitlines():
        expected, prop = row.split(" ", 1)
        text = without_wire_comments(dense_module(_machine(prop), "mon"))
        assert hashlib.sha256(text.encode()).hexdigest() == expected


def _process(
    yosys_tool: Tool, sby_tool: Tool, tmp_path: Path, machine: Monitor, monitor: str
) -> None:
    (tmp_path / "monitor.sv").write_text(monitor, encoding="utf-8")
    run_yosys(
        yosys_tool,
        [
            "read_verilog -formal -sv monitor.sv",
            "hierarchy -check -top mon",
            "proc",
            "prep -top mon",
            "async2sync",
            "opt",
            "check -assert",
        ],
        tmp_path,
    )
    ports = monitor_port_types(machine)
    bench = (FIXTURES / "depth_process_tb.sv").read_text(encoding="utf-8")
    bench = bench.replace(
        "@PORTS@", ", ".join(f"input {declare(t, f'i{i}')}" for i, t in enumerate(ports))
    )
    bench = bench.replace("@CONNECTIONS@", "".join(f".i{i}(i{i}), " for i in range(len(ports))))
    (tmp_path / "tb.sv").write_text(bench, encoding="utf-8")
    script = tmp_path / "depth_process.sby"
    script.write_text(
        (FIXTURES / "depth_process.sby").read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = run_sby(sby_tool, script, "prove", tmp_path / "work")
    assert result.status is SbyStatus.FAIL, result.log[-4000:]


@pytest.mark.slow
@pytest.mark.parametrize("n", [16, 32, 64])
@pytest.mark.parametrize("shape", ["##N b", "b[*N]", "##[1:N] b"])
def test_frozen_dense_controls_load_and_prepare(
    yosys_tool: Tool, sby_tool: Tool, tmp_path: Path, n: int, shape: str
) -> None:
    machine = _machine("a |-> " + shape.replace("N", str(n)))
    _process(yosys_tool, sby_tool, tmp_path, machine, dense_module(machine, "mon"))


@pytest.mark.slow
@pytest.mark.parametrize(
    ("accepted", "rejected", "budget"),
    [
        ("a |-> ##[1:$] b ##7 c", "a |-> ##[1:$] b ##8 c", "configurations"),
        (
            "always [0:80] (a |-> nexttime [80] b)",
            "always [0:81] (a |-> nexttime [81] b)",
            "instances",
        ),
    ],
)
def test_existing_budget_family_boundaries_load_and_prepare(
    yosys_tool: Tool, sby_tool: Tool, tmp_path: Path, accepted: str, rejected: str, budget: str
) -> None:
    # Cached predecessors restore admission under the unchanged default work cap.
    machine = _compile(accepted)
    if budget == "configurations":
        assert isinstance(machine, ConfigMachine)
        assert len(machine.configurations) == 129
    else:
        assert isinstance(machine, AttemptMachine)
        assert len(machine.netlist.registers) == 6719
    _process(yosys_tool, sby_tool, tmp_path, machine, monitor_module(machine, "mon"))
    # Rejection is a separate boundary check, never counted as successful tool stress.
    with pytest.raises(CompileError, match=budget):
        _compile(rejected)
