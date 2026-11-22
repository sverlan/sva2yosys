"""Skipped X-checks in the rewrite (CONTRACT.md, point 9)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from support.toolchain import SbyResult, SbyStatus, Tool, run_sby
from sva2yosys.rewrite import (
    MONITORS_FILE,
    FileRole,
    Rewrite,
    RewriteError,
    Severity,
    rewrite,
    syntax_items,
)
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SKIPPED = ["a_known", "a_en_known", "m_known", "c_unknown", "a_trigger"]


def _sources(design: str) -> list[tuple[str, str]]:
    files = [
        FIXTURES / "counter" / design / "counter.sv",
        FIXTURES / "xchecks" / "verify" / "counter_xprops.sv",
    ]
    return [(path.relative_to(FIXTURES).as_posix(), path.read_text()) for path in files]


def _translate(sources: list[tuple[str, str]], verification: frozenset[str], top: str) -> Rewrite:
    document = export_texts(sources, SlangOptions(top=(top,)))
    return rewrite(sources, verification, syntax_items(document), convert(document))


def _counter(design: str = "design") -> tuple[list[tuple[str, str]], Rewrite]:
    sources = _sources(design)
    verification = frozenset(name for name, _ in sources if "/verify/" in name)
    return sources, _translate(sources, verification, "counter")


def _text(result: Rewrite, name: str) -> str:
    (text,) = [f.text for f in result.files if f.source.endswith(name)]
    return text


def test_skipped_directives_are_replaced_by_a_comment_and_keep_their_lines() -> None:
    """9."""
    sources, result = _counter()
    original = dict(sources)["xchecks/verify/counter_xprops.sv"]
    text = _text(result, "counter_xprops.sv")
    assert len(text.splitlines()) == len(original.splitlines())
    assert text.count("/* sva2yosys: skipped (X-check):") == 5
    for name in SKIPPED:
        line = next(i for i, row in enumerate(original.splitlines()) if f"{name}:" in row)
        assert "/* sva2yosys: skipped (X-check):" in text.splitlines()[line], name
    code = re.sub(r"/\*.*?\*/", "", text)  # the comments quote the assertions as written
    assert not re.search(r"\b(assert|assume|cover)\s+property\b", code)


def test_only_the_real_directives_get_monitors() -> None:
    """9."""
    _, result = _counter()
    assert result.monitors.count("module __sva2yosys_p") == 2
    text = _text(result, "counter_xprops.sv")
    assert text.count("__sva2yosys_p0 __sva2yosys_i0") == 1  # a_real
    assert text.count("__sva2yosys_p1 __sva2yosys_i1") == 1  # c_max
    assert "__sva2yosys_p2" not in text


def test_each_skipped_site_has_one_note_and_the_warning_is_kept() -> None:
    """9."""
    sources, result = _counter()
    original = dict(sources)["xchecks/verify/counter_xprops.sv"].splitlines()
    assert [d.severity for d in result.skipped] == [Severity.NOTE] * 5
    noted_lines = [d.source.line for d in result.skipped if d.source is not None]
    expected = [
        next(i for i, row in enumerate(original, start=1) if f"{name}:" in row) for name in SKIPPED
    ]
    assert noted_lines == expected
    assert all("X-check" in d.message for d in result.skipped)
    (warning,) = result.warnings
    assert warning.severity is Severity.WARNING
    assert warning.source is not None
    assert original[warning.source.line - 1].lstrip().startswith("a_real:")
    assert "dropped" in warning.message


def test_the_output_has_no_concurrent_assertion_left() -> None:
    """9: it compiles and exports without any assertion."""
    _, result = _counter()
    texts = [(f.name, f.text) for f in result.files if f.role is FileRole.VERIFICATION]
    texts.append(("counter.sv", dict(_sources("design"))["counter/design/counter.sv"]))
    texts.append((MONITORS_FILE, result.monitors))
    document = export_texts(texts, SlangOptions(top=("counter",)))
    assert document["assertions"] == []


def test_rewriting_is_deterministic() -> None:
    """9."""
    _, first = _counter()
    _, second = _counter()
    assert first == second


def test_a_procedural_skipped_directive_leaves_a_null_statement() -> None:
    """9."""
    text = (
        "module m(input logic clk, en, a);\n"
        "  always @(posedge clk)\n"
        "    if (en) x: assert property (!$isunknown(a));\n"
        "    else y: assert property (a);\n"
        "endmodule\n"
    )
    result = _translate([("m.sv", text)], frozenset({"m.sv"}), "m")
    (output,) = [f.text for f in result.files]
    assert len(output.splitlines()) == len(text.splitlines())
    line = output.splitlines()[2]
    assert line.startswith("    if (en) ;") and "sva2yosys: skipped (X-check)" in line
    assert "else ;" in output.splitlines()[3]
    assert len(result.skipped) == 1
    # the else branch still elaborates, with its instance after the block
    assert result.monitors.count("module __sva2yosys_p") == 1


_DISAGREE = """module m #(parameter bit K = 0)(input logic clk, a);
  x: assert property (@(posedge clk) K ? !$isunknown(a) : a);
endmodule
module top(input logic clk, a);
  m #(.K(0)) u0(.clk(clk), .a(a));
  m #(.K(1)) u1(.clk(clk), .a(a));
endmodule
"""


def test_instances_that_disagree_on_skipping_are_an_error() -> None:
    """9."""
    with pytest.raises(RewriteError) as caught:
        _translate([("m.sv", _DISAGREE)], frozenset({"m.sv"}), "top")
    errors = [d for d in caught.value.diagnostics if d.severity is Severity.ERROR]
    assert len(errors) == 1
    assert errors[0].source is not None and errors[0].source.line == 2
    assert "X-check" in errors[0].message


def test_an_unelaborated_skipped_site_is_still_reported() -> None:
    """9: skipping does not hide the 'not elaborated' error."""
    text = (
        "module dead(input logic clk, a);\n"
        "  x: assert property (@(posedge clk) !$isunknown(a));\n"
        "endmodule\n"
        "module top(input logic clk, a);\n"
        "endmodule\n"
    )
    with pytest.raises(RewriteError) as caught:
        _translate([("m.sv", text)], frozenset({"m.sv"}), "top")
    assert any("not elaborated" in d.message for d in caught.value.diagnostics)


def test_a_skipped_unbraced_generate_body_stays_valid() -> None:
    """9: the rewritten module elaborates (the generate if keeps a body)."""
    text = (
        "module m(input logic clk, a);\n"
        "  if (1) x: assert property (@(posedge clk) !$isunknown(a));\n"
        "  else begin end\n"
        "  for (genvar i = 0; i < 2; i++) y: assert property (@(posedge clk) !$isunknown(a));\n"
        "endmodule\n"
    )
    result = _translate([("m.sv", text)], frozenset({"m.sv"}), "m")
    assert len(result.skipped) == 2
    (output,) = [f.text for f in result.files]
    assert len(output.splitlines()) == len(text.splitlines())
    document = export_texts([("m.sv", output)], SlangOptions(top=("m",)))
    assert document["assertions"] == []


# --- end to end under SBY ---------------------------------------------------------------------

_SBY = """[tasks]
{task}

[options]
mode {mode}
depth {depth}

[engines]
{engine}

[script]
{reads}
prep -top counter

[files]
{files}
"""


def _write(result: Rewrite, sources: list[tuple[str, str]], directory: Path) -> list[str]:
    names: list[str] = []
    copies = {f.source for f in result.files if f.role is FileRole.DESIGN_COPY}
    for name, text in sources:
        if "/verify/" in name or name in copies:
            continue
        (directory / Path(name).name).write_text(text)
        names.append(Path(name).name)
    for output in result.files:
        (directory / output.name).write_text(output.text)
        names.append(output.name)
    (directory / MONITORS_FILE).write_text(result.monitors)
    return [*names, MONITORS_FILE]


def _sby(
    tool: Tool, directory: Path, names: list[str], task: str, engine: str, depth: int
) -> SbyResult:
    reads = "\n".join(f"read_verilog -formal -sv {name}" for name in names)
    mode = {"prove": "prove", "bmc": "bmc", "cover": "cover"}[task]
    (directory / f"{task}.sby").write_text(
        _SBY.format(
            task=task, mode=mode, depth=depth, engine=engine, reads=reads, files="\n".join(names)
        )
    )
    return run_sby(tool, directory / f"{task}.sby", task, directory / f"work_{task}")


def test_the_real_assertion_proves_and_the_cover_is_reached(sby_tool: Tool, tmp_path: Path) -> None:
    """9."""
    sources, result = _counter("design")
    names = _write(result, sources, tmp_path)
    proved = _sby(sby_tool, tmp_path, names, "prove", "abc pdr", 20)
    assert proved.status is SbyStatus.PASS, proved.log[-3000:]
    covered = _sby(sby_tool, tmp_path, names, "cover", "smtbmc yices", 20)
    assert covered.status is SbyStatus.PASS, covered.log[-3000:]
    assert [hit.step for hit in covered.reached_covers] == [15]  # c_max only


def test_the_real_assertion_still_fails_on_the_mutant(sby_tool: Tool, tmp_path: Path) -> None:
    """9."""
    sources, result = _counter("mutant")
    names = _write(result, sources, tmp_path)
    failed = _sby(sby_tool, tmp_path, names, "bmc", "smtbmc yices", 12)
    assert failed.status is SbyStatus.FAIL, failed.log[-3000:]
    assert [hit.step for hit in failed.failed_assertions] == [8]


# a skip that a parameter decides: rewritten for K = 1, elaborated for K = 0 or 1
_PARAMETRIC = """module m #(parameter bit K = 1)(input logic clk, a, b);
  x: assert property (@(posedge clk) if (K) !$isunknown(a) else b);
endmodule
"""
_PARAMETRIC_TOP = """module top(input logic clk, a, b);
  m #(.K({k})) u(.clk(clk), .a(a), .b(b));
endmodule
"""
_PARAMETRIC_SBY = """[tasks]
bmc

[options]
mode bmc
depth 3

[engines]
smtbmc yices

[script]
read_verilog -formal -sv m.sv
read_verilog -formal -sv top.sv
read_verilog -formal -sv {monitors}
prep -top top

[files]
m.sv
top.sv
{monitors}
"""


@pytest.mark.parametrize(("k", "status"), [(1, SbyStatus.PASS), (0, SbyStatus.FAIL)])
def test_a_skip_holds_only_for_the_parameters_it_was_decided_for(
    sby_tool: Tool, tmp_path: Path, k: int, status: SbyStatus
) -> None:
    """9: with other parameter values the rewritten module fails instead of losing the
    assertion."""
    top = _PARAMETRIC_TOP.format(k=1)
    result = _translate([("m.sv", _PARAMETRIC), ("top.sv", top)], frozenset({"m.sv"}), "top")
    assert len(result.skipped) == 1
    (output,) = [f.text for f in result.files if f.name.endswith("m.sv")]
    (tmp_path / "m.sv").write_text(output)
    (tmp_path / "top.sv").write_text(_PARAMETRIC_TOP.format(k=k))
    (tmp_path / MONITORS_FILE).write_text(result.monitors)
    (tmp_path / "x.sby").write_text(_PARAMETRIC_SBY.format(monitors=MONITORS_FILE))
    outcome = run_sby(sby_tool, tmp_path / "x.sby", "bmc", tmp_path / "work")
    assert outcome.status is status, outcome.log[-3000:]
