"""Wide generated expressions load and process without parser-stack failures (20)."""

import re
from pathlib import Path

import pytest

from support.toolchain import SbyStatus, Tool, run_sby, run_yosys
from sva2yosys.codegen.expressions import MAX_FANIN, MAX_LOCAL_DEPTH
from sva2yosys.codegen.systemverilog import monitor_module, monitor_ports
from sva2yosys.optimize.forms import Form
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions, compile_texts
from sva2yosys.temporal.attempts import AttemptMachine
from sva2yosys.temporal.compile import Monitor, compile_monitors
from sva2yosys.temporal.configurations import ConfigMachine
from sva2yosys.temporal.machine import Machine
from sva2yosys.temporal.netlist import NAnd, NOr

FIXTURES = Path(__file__).parent / "fixtures"


def _machine(prop: str, kind: str = "assert") -> Monitor:
    source = (FIXTURES / "depth.sv").read_text(encoding="utf-8")
    source = source.replace("@KIND@", kind).replace("@PROPERTY@", prop)
    conversion = convert(export_texts([("depth.sv", source)], SlangOptions(top=("tc",))))
    assert not conversion.rejections
    (directive,) = conversion.directives
    (machine,) = compile_monitors(directive)
    return machine


def _monitor(prop: str, kind: str = "assert", form: Form = Form.DIRECT) -> str:
    return monitor_module(_machine(prop, kind), "mon", form)


def _process(tool: Tool, sby: Tool, directory: Path, monitor: str) -> None:
    (directory / "monitor.sv").write_text(monitor, encoding="utf-8")
    log = run_yosys(
        tool,
        [
            "read_verilog -formal -sv monitor.sv",
            "hierarchy -check -top mon",
            "proc",
            "prep -top mon",
            "async2sync",
            "opt",
            "check -assert",
        ],
        directory,
    )
    assert "implicitly declared" not in log
    ports = re.findall(r"input (logic(?: signed)? \[\d+:0\]) (i\d+),", monitor)
    assert ports
    bench = (FIXTURES / "depth_process_tb.sv").read_text(encoding="utf-8")
    bench = bench.replace("@PORTS@", ", ".join(f"input {t} {n}" for t, n in ports))
    bench = bench.replace("@CONNECTIONS@", "".join(f".{n}({n}), " for _, n in ports))
    (directory / "tb.sv").write_text(bench, encoding="utf-8")
    script = directory / "depth_process.sby"
    script.write_text((FIXTURES / script.name).read_text(encoding="utf-8"), encoding="utf-8")
    result = run_sby(sby, script, "prove", directory / "work")
    assert result.status is SbyStatus.FAIL, result.log[-4000:]


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["assert", "assume"])
def test_four_thousand_antecedent_matches_process(
    yosys_tool: Tool, sby_tool: Tool, tmp_path: Path, kind: str
) -> None:
    _process(yosys_tool, sby_tool, tmp_path, _monitor("a[*1:4096] |-> b", kind))


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["assert", "assume", "cover"])
@pytest.mark.parametrize("count", [16, 32, 64])
@pytest.mark.parametrize("shape", ["##N b", "b[*N]", "##[1:N] b"])
def test_dense_consequent_expressions_process(
    yosys_tool: Tool, sby_tool: Tool, tmp_path: Path, kind: str, count: int, shape: str
) -> None:
    _process(
        yosys_tool, sby_tool, tmp_path, _monitor("a |-> " + shape.replace("N", str(count)), kind)
    )


@pytest.mark.parametrize(
    ("prop", "kind", "form"),
    [
        ("a |-> ##[1:16] b", "assert", Form.WINDOW),
        ("a |-> ##16 b", "assert", Form.HISTORY),
        ("a[*1:16] |-> (b or nexttime c)", "assert", Form.DIRECT),
        ("a[*1:32] |-> ##[1:$] b", "assert", Form.DIRECT),
        ("a[*1:32] |-> s_eventually b", "assume", Form.DIRECT),
        pytest.param(
            "a |-> strong(##[1:$] b ##7 c)",
            "assert",
            Form.DIRECT,
            marks=pytest.mark.extended,
        ),
    ],
)
def test_all_emission_routes_process(
    yosys_tool: Tool, sby_tool: Tool, tmp_path: Path, prop: str, kind: str, form: Form
) -> None:
    _process(yosys_tool, sby_tool, tmp_path, _monitor(prop, kind, form))


@pytest.mark.parametrize(
    ("prop", "expected"),
    [
        ("a |-> ##16 b", Machine),
        ("a |-> ##[1:$] b ##3 c", ConfigMachine),
        ("a[*1:16] |-> (b or nexttime c)", AttemptMachine),
    ],
)
def test_stress_properties_exercise_the_named_machine_class(
    prop: str, expected: type[Monitor]
) -> None:
    assert isinstance(_machine(prop), expected)


def test_per_attempt_case_exercises_a_gate_wider_than_one_chunk() -> None:
    machine = _machine("a[*1:16] |-> (b or nexttime c)")
    assert isinstance(machine, AttemptMachine)
    assert any(
        isinstance(gate, NAnd | NOr) and len(gate.operands) > MAX_FANIN
        for gate in machine.netlist.gates
    )


@pytest.mark.parametrize(
    ("prop", "form", "live"),
    [
        ("a |-> ##16 b", Form.DIRECT, False),
        ("a |-> ##16 b", Form.HISTORY, False),
        ("a |-> ##[1:$] b ##3 c", Form.DIRECT, False),
        ("a[*1:16] |-> (b or nexttime c)", Form.DIRECT, False),
        ("a |-> strong(##[1:$] b ##7 c)", Form.DIRECT, True),
        ("{" + ", ".join(["s", "a", "b"] * 10) + "} != 0", Form.DIRECT, False),
    ],
)
def test_chunked_monitors_elaborate_with_slang_without_forward_references(
    prop: str, form: Form, live: bool
) -> None:
    defines = (("SVA2YOSYS_LIVE", "1"),) if live else ()
    # compile_texts raises SlangError on any error diagnostic (its public contract).
    compile_texts(
        [("monitor.sv", _monitor(prop, form=form))], SlangOptions(top=("mon",), defines=defines)
    )


@pytest.mark.slow
def test_wide_witness_expressions_process_in_the_live_task(
    sby_live_tool: Tool, tmp_path: Path
) -> None:
    # req once and b=0 forever leaves the chosen obligation pending: live must FAIL.
    monitor = _monitor("a |-> strong(##[1:$] b ##7 c)")
    (tmp_path / "monitor.sv").write_text(monitor, encoding="utf-8")
    script = tmp_path / "depth_live.sby"
    script.write_text((FIXTURES / script.name).read_text(encoding="utf-8"), encoding="utf-8")
    result = run_sby(sby_live_tool, script, "live", tmp_path / "work")
    assert result.status is SbyStatus.FAIL, result.log[-4000:]


def test_chunked_antecedent_matches_the_hand_derived_boolean_property(
    sby_tool: Tool, tmp_path: Path
) -> None:
    (tmp_path / "monitor.sv").write_text(_monitor("a[*1:1000] |-> b"), encoding="utf-8")
    for name in ("depth_miter.sv", "depth.sby"):
        (tmp_path / name).write_text(
            (FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    result = run_sby(sby_tool, tmp_path / "depth.sby", "prove", tmp_path / "work")
    assert result.status is SbyStatus.PASS, result.log[-4000:]


@pytest.mark.parametrize("mutated", [False, True], ids=["equivalent", "lost-success"])
def test_chunked_configuration_matches_the_frozen_monitor_and_detects_a_mutant(
    sby_tool: Tool, tmp_path: Path, mutated: bool
) -> None:
    machine = _machine("a |-> ##[1:$] b ##3 c")
    assert isinstance(machine, ConfigMachine) and len(machine.configurations) == 9
    assert monitor_ports(machine) == ("tc.rst", "tc.a", "tc.b", "tc.c")
    monitor = monitor_module(machine, "mon")
    if mutated:
        monitor, count = re.subn(
            r"(?m)^\s*assign success = [^;]+;", "    assign success = 1'b0;", monitor
        )
        assert count == 1
    (tmp_path / "monitor.sv").write_text(monitor, encoding="utf-8")
    (tmp_path / "baseline.sv").write_text(
        (FIXTURES / "depth_configuration_before_compact.sv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for name in ("depth_configuration_miter.sv", "depth_configuration.sby"):
        (tmp_path / name).write_text(
            (FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    result = run_sby(sby_tool, tmp_path / "depth_configuration.sby", "prove", tmp_path / "work")
    expected = SbyStatus.FAIL if mutated else SbyStatus.PASS
    assert result.status is expected, result.log[-4000:]


def _local_depth(value: str) -> int:
    """Conservative syntax depth for the emitter's expression grammar.

    Count a binary chain as left-associated, without exploiting precedence to lower
    its depth. Delimiters, unary operators, selects, ternaries and concatenations
    contribute nodes. Named wire references remain leaves, not expanded dependencies.
    """
    tokens = re.findall(
        r"\d+'s?[hbd][0-9a-fA-F_xz]+|'[01]|[a-zA-Z_$]\w*|\d+|"
        r"<<<|>>>|<<|>>|==|!=|<=|>=|&&|\|\||[{}()\[\],?:~!&|^+*/%<>=-]",
        value,
    )
    position = 0

    def expression(stops: set[str]) -> int:
        nonlocal position

        def atom() -> int:
            nonlocal position
            token = tokens[position]
            position += 1
            if token in {"~", "!", "&", "|", "^", "+", "-"}:
                return 1 + atom()
            if token == "(":
                depth = expression({")"})
                assert tokens[position] == ")"
                position += 1
                return depth
            if token == "{":
                depths = [expression({",", "}", "{"})]
                while tokens[position] == ",":
                    position += 1
                    depths.append(expression({",", "}"}))
                if tokens[position] == "{":  # replication count and value
                    position += 1
                    depths.append(1 + expression({"}"}))
                    position += 1
                assert tokens[position] == "}"
                position += 1
                return 1 + max(depths)
            return 1

        depth = atom()
        while position < len(tokens) and tokens[position] not in stops:
            operator = tokens[position]
            position += 1
            if operator == "[":
                index = expression({":", "]"})
                if tokens[position] == ":":
                    position += 1
                    index = max(index, expression({"]"}))
                assert tokens[position] == "]"
                position += 1
                depth = 1 + max(depth, index)
            elif operator == "?":
                then = expression({":"})
                position += 1
                depth = 1 + max(depth, then, expression(stops))
            else:
                depth = 1 + max(depth, atom())
        return depth

    depth = expression(set())
    assert position == len(tokens)
    return depth


def test_depth_check_detects_flat_chains_and_nested_concatenations() -> None:
    assert _local_depth(" | ".join(["a"] * 1000)) == 1000
    assert _local_depth("{a, {b, c}}") == 3
    assert _local_depth("a ? {3{b}} : c[2:0]") == 4


def _assignment_values(monitor: str) -> list[str]:
    """All combinational, initial and clocked-assignment RHSs, ignoring comments."""
    code = "\n".join(line.split("//", 1)[0] for line in monitor.splitlines())
    return re.findall(r"\b\w+(?:\[\d+\])?\s*(?:<=|=)\s*([^;\n]+);", code)


def test_depth_audit_includes_clocked_register_values() -> None:
    chain = " | ".join(["a"] * 1000)
    assert (
        max(map(_local_depth, _assignment_values(f"always @(posedge clk) r <= {chain};"))) == 1000
    )


def test_zero_width_replication_is_rejected_with_a_location_before_codegen() -> None:
    source = (FIXTURES / "depth.sv").read_text(encoding="utf-8")
    source = source.replace("@KIND@", "assert").replace("@PROPERTY@", "{s, {0{a}}} == s")
    conversion = convert(export_texts([("zero.sv", source)], SlangOptions(top=("tc",))))
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert "unsupported type 'void'" in rejection.message
    assert rejection.source is not None
    assert rejection.source.file == "zero.sv" and rejection.source.line == 2


@pytest.mark.parametrize(
    ("prop", "kind", "form"),
    [
        ("a[*1:1000] |-> b", "assert", Form.DIRECT),
        ("a |-> ##16 b", "assert", Form.DIRECT),
        ("a |-> b[*16]", "cover", Form.DIRECT),
        ("a |-> ##[1:16] b", "assume", Form.DIRECT),
        ("a |-> ##16 b", "assert", Form.HISTORY),
        ("a |-> ##[1:16] b", "assert", Form.WINDOW),
        ("a[*1:16] |-> (b or nexttime c)", "assert", Form.DIRECT),
        pytest.param(
            "a |-> strong(##[1:$] b ##7 c)",
            "assert",
            Form.DIRECT,
            marks=pytest.mark.extended,
        ),
        ("a[*1:32] |-> s_eventually b", "assume", Form.DIRECT),
    ],
)
def test_generated_local_depth_is_bounded_without_a_liveness_engine(
    prop: str, kind: str, form: Form
) -> None:
    # Includes the entire live define block even when no external tool is configured.
    monitor = _monitor(prop, kind, form)
    values = _assignment_values(monitor)
    assert values
    assert max(map(_local_depth, values)) <= MAX_LOCAL_DEPTH


@pytest.mark.parametrize(("value", "a", "b", "expected"), [(10, 1, 0, "a"), (15, 1, 1, "f")])
def test_a_wide_concatenation_preserves_width_order_and_unsigned_comparison(
    yosys_tool: Tool, tmp_path: Path, value: int, a: int, b: int, expected: str
) -> None:
    # Ten signed-nibble/bit/bit groups form an unsigned 60-bit value. Their expected
    # order and the comparison to signed -1 are hand-derived, not an oracle import.
    parts = ", ".join(["s", "a", "b"] * 10)
    prop = f"({{{parts}}} == 60'h{expected * 15}) && ({{s}} < -1)"
    monitor = _monitor(prop)
    (tmp_path / "monitor.sv").write_text(monitor, encoding="utf-8")
    run_yosys(
        yosys_tool,
        [
            "read_verilog -formal -sv monitor.sv",
            "prep -top mon",
            "flatten",
            "async2sync",
            "opt",
            # i0 is rst, i1 is s, i2/i3 are a/b. At s=-1 all bits are one;
            # {s} is unsigned 15 and -1 becomes unsigned 0xffffffff: comparison true.
            f"sat -set i0 0 -set i1 {value} -set i2 {a} -set i3 {b} -prove fail 0 -verify",
        ],
        tmp_path,
    )
