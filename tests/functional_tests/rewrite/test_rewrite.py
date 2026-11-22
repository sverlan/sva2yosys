"""Source rewriting (CONTRACT.md)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from support.toolchain import SbyResult, SbyStatus, Tool, run_sby, run_yosys
from sva2yosys.codegen.emit_configurations import live_define
from sva2yosys.codegen.systemverilog import monitor_module, monitor_ports
from sva2yosys.ir.directive import Directive
from sva2yosys.optimize.forms import Form, Strategy
from sva2yosys.rewrite import (
    MONITORS_FILE,
    FileRole,
    ItemKind,
    Rewrite,
    RewriteError,
    Severity,
    rewrite,
    syntax_items,
)
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_design, export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.compile import compile_directive

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EDITED = {
    ItemKind.ASSERTION,
    ItemKind.PROPERTY,
    ItemKind.SEQUENCE,
    ItemKind.DEFAULT_CLOCKING,
    ItemKind.DEFAULT_DISABLE,
    ItemKind.BIND,
}


def _sources(case: str, design: str = "design") -> list[tuple[str, str]]:
    root = FIXTURES / case
    files = sorted((root / design).glob("*.sv")) + sorted((root / "verify").glob("*.sv"))
    return [(path.relative_to(FIXTURES).as_posix(), path.read_text()) for path in files]


def _restrict_sources(top: str) -> list[tuple[str, str]]:
    """The one fixture file (one module) of a restrict case; each case elaborates alone."""
    return [(name, text) for name, text in _sources("restrict") if name.endswith(f"/{top}.sv")]


def _verification(sources: list[tuple[str, str]]) -> frozenset[str]:
    return frozenset(name for name, _ in sources if "/verify/" in name)


def _translate(
    sources: list[tuple[str, str]],
    verification: frozenset[str],
    top: str,
    design_sva: Severity = Severity.ERROR,
    strategy: Strategy = Strategy.DIRECT,
    equivalence: bool = False,
) -> tuple[Rewrite, list[Directive]]:
    document = export_texts(sources, SlangOptions(top=(top,)))
    conversion = convert(document)
    result = rewrite(
        sources,
        verification,
        syntax_items(document),
        conversion,
        design_sva,
        strategy,
        equivalence,
    )
    return result, list(conversion.directives)


# The pipe design keeps one assertion of its own (contract point 6).
_ALLOWED = {"pipe": Severity.WARNING}


def _fixture(case: str, top: str, design: str = "design") -> tuple[Rewrite, list[Directive]]:
    sources = _sources(case, design)
    return _translate(sources, _verification(sources), top, _ALLOWED.get(case, Severity.ERROR))


def _errors(sources: dict[str, str], verification: set[str], top: str) -> list[str]:
    listed = list(sources.items())
    with pytest.raises(RewriteError) as caught:
        _translate(listed, frozenset(verification), top)
    errors = [d for d in caught.value.diagnostics if d.severity is Severity.ERROR]
    assert errors
    return [str(d) for d in errors]


def _without_context(text: str) -> str:
    """``text`` without the ``// as written:``, ``// clock:`` and ``// disable iff:`` lines."""
    return re.sub(r"^// (?:as written|clock|disable iff):.*\n", "", text, flags=re.MULTILINE)


def _code(text: str) -> str:
    """``text`` without its block comments (those quote the assertions as written)."""
    return re.sub(r"/\*.*?\*/", "", text)


def _output(result: Rewrite, source: str) -> str:
    (text,) = [f.text for f in result.files if f.source == source]
    return text


# --- 1: verbatim preservation ------------------------------------------------------------


@pytest.mark.parametrize(("case", "top"), [("counter", "counter"), ("pipe", "top")])
def test_verification_files_are_preserved_outside_the_edits(case: str, top: str) -> None:
    """1."""
    sources = _sources(case)
    document = export_texts(sources, SlangOptions(top=(top,)))
    items = syntax_items(document)
    allowed = _ALLOWED.get(case, Severity.ERROR)
    result = rewrite(sources, _verification(sources), items, convert(document), allowed)
    for name, text in sources:
        if name not in _verification(sources):
            continue
        output = _output(result, name)
        original_lines = text.split("\n")
        output_lines = output.split("\n")
        assert len(output_lines) == len(original_lines)
        edited = [i for i in items if i.source.file == name and i.kind in EDITED]
        masked = [False] * len(text)
        for item in edited:
            for offset in range(item.start, item.end):
                masked[offset] = True
        offset = 0
        for original, rewritten in zip(original_lines, output_lines, strict=True):
            line_mask = masked[offset : offset + len(original)]
            offset += len(original) + 1
            if not any(line_mask):
                assert rewritten == original
                continue
            first = line_mask.index(True)
            last = len(line_mask) - line_mask[::-1].index(True)
            assert rewritten.startswith(original[:first])
            assert rewritten.endswith(original[last:])


# --- 2: monitor instances ----------------------------------------------------------------


def _local(path: str) -> str:
    return path.rsplit(".", 1)[-1]


def test_assertions_become_monitor_instances() -> None:
    """2."""
    result, directives = _fixture("counter", "counter")
    name = "counter/verify/counter_props.sv"
    lines = _output(result, name).split("\n")
    ordered = sorted(directives, key=lambda d: (d.source.line, d.source.column))
    assert [d.name for d in ordered] == ["a_steps", "a_holds", "c_wraps"]
    for number, directive in enumerate(ordered):
        machine = compile_directive(directive)
        ports = "".join(f", .i{i}({_local(path)})" for i, path in enumerate(monitor_ports(machine)))
        instance = (
            f"__sva2yosys_p{number} __sva2yosys_i{number} "
            f"(.clk({_local(directive.clock.signal.path)}){ports}, .fail(), .success());"
        )
        assert instance in lines[directive.source.line - 1]
        # Localized: the same monitor with the instance path dropped from every name.
        # (the context comments are checked in test_rewrite_comments.py)
        expected = _without_context(monitor_module(machine, f"__sva2yosys_p{number}"))
        assert expected.replace(f"{directive.scope}.", "") in _without_context(result.monitors)
    assert result.monitors.count("endmodule") == 3


# --- 3: declarations ---------------------------------------------------------------------


def test_sva_declarations_are_removed() -> None:
    """3."""
    result, _ = _fixture("counter", "counter")
    output = _code(_output(result, "counter/verify/counter_props.sv"))
    for keyword in ("property", "endproperty", "sequence", "endsequence", "clocking", "disable"):
        assert re.search(rf"\b{keyword}\b", output) is None, keyword
    assert "assign at_max = cnt == 4'd15;" in output
    assert "// trailing comment stays" in output


# --- 4: binds ----------------------------------------------------------------------------


def _without(copy: str, inserted: str) -> str:
    return copy.replace(inserted + "\n", "", 1)


def test_binds_are_lowered_into_design_copies() -> None:
    """4."""
    sources = dict(_sources("counter"))
    result, _ = _fixture("counter", "counter")
    assert "bind" not in _output(result, "counter/verify/counter_props.sv")
    (copy,) = [f for f in result.files if f.role is FileRole.DESIGN_COPY]
    assert copy.source == "counter/design/counter.sv"
    assert copy.name == "counter.sv"
    inserted = "counter_props u_props (\n    .clk(clk), .rst(rst), .en(en), .cnt(cnt)\n);"
    assert inserted + "\nendmodule" in copy.text
    assert _without(copy.text, inserted) == sources[copy.source]


def test_bind_into_a_verification_module() -> None:
    """4: the target module is in a verification file."""
    sources = {
        "v.sv": (
            "module chk(input logic clk, a);\n"
            "  assert property (@(posedge clk) a);\n"
            "endmodule\n"
            "module host(input logic clk, a);\n"
            "endmodule\n"
            "bind host chk u_chk (.clk(clk), .a(a));\n"
        )
    }
    result, _ = _translate(list(sources.items()), frozenset({"v.sv"}), "host")
    assert [f.role for f in result.files] == [FileRole.VERIFICATION]
    output = result.files[0].text
    assert "chk u_chk (.clk(clk), .a(a));\nendmodule" in output
    assert "bind" not in output


def test_design_files_without_targets_are_not_output() -> None:
    """4."""
    sources = {
        "d.sv": "module other; endmodule\nmodule top(input logic clk, a); endmodule\n",
        "e.sv": "module unrelated; endmodule\n",
        "v.sv": (
            "module chk(input logic clk, a);\n"
            "  assert property (@(posedge clk) a);\n"
            "endmodule\n"
            "bind top chk u_chk (.clk(clk), .a(a));\n"
        ),
    }
    result, _ = _translate(list(sources.items()), frozenset({"v.sv"}), "top")
    assert [(f.source, f.role) for f in result.files] == [
        ("d.sv", FileRole.DESIGN_COPY),
        ("v.sv", FileRole.VERIFICATION),
    ]
    # A single-line module: the instantiation goes on its own line before endmodule.
    assert result.files[0].text == (
        "module other; endmodule\n"
        "module top(input logic clk, a); \nchk u_chk (.clk(clk), .a(a));\nendmodule\n"
    )


def test_bind_variants_are_inserted_verbatim() -> None:
    """4: several binds into one module, `.*`, parameter overrides, `endmodule : label`."""
    sources = {
        "d.sv": (
            "module host #(parameter int W = 2) (input logic clk, input logic [W-1:0] v);\n"
            "endmodule : host\n"
            "module top(input logic clk, input logic [1:0] v2, input logic [3:0] v4);\n"
            "  host #(2) u2(.clk(clk), .v(v2));\n  host #(4) u4(.clk(clk), .v(v4));\n"
            "endmodule\n"
        ),
        "v.sv": (
            "module chk #(parameter int W = 1) (input logic clk, input logic [W-1:0] v);\n"
            "  logic any;\n  assign any = |v;\n"
            "  assert property (@(posedge clk) any);\n"
            "endmodule\n"
            "bind host chk #(.W(W)) u_a (.*);\n"
            "bind host chk #(.W(W)) u_b (.clk(clk), .v(~v));\n"
        ),
    }
    result, _ = _translate(list(sources.items()), frozenset({"v.sv"}), "top")
    copy = result.files[0].text
    assert copy.startswith(
        "module host #(parameter int W = 2) (input logic clk, input logic [W-1:0] v);\n"
        "chk #(.W(W)) u_a (.*);\nchk #(.W(W)) u_b (.clk(clk), .v(~v));\nendmodule : host\n"
    )
    assert "bind" not in result.files[1].text


def test_all_clocking_blocks_are_removed() -> None:
    """3: a named block made default by a separate `default clocking` reference."""
    text = (
        "module p(input logic clk, a);\n"
        "  clocking cb @(posedge clk); endclocking\n"
        "  default clocking cb;\n"
        "  assert property (a);\n"
        "endmodule\n"
    )
    result, _ = _translate([("v.sv", text)], frozenset({"v.sv"}), "p")
    output = result.files[0].text
    assert "clocking" not in output
    assert output.count("\n") == text.count("\n")


# --- 5, 6: shared monitors, design warnings ------------------------------------------------


def test_instances_and_generate_iterations_share_one_monitor() -> None:
    """5."""
    result, directives = _fixture("pipe", "top")
    assert len([d for d in directives if d.name == "a_bit"]) == 8
    output = _output(result, "pipe/verify/pipe_props.sv")
    assert output.count("__sva2yosys_i") == 1
    assert "__sva2yosys_p0 __sva2yosys_i0 (.clk(clk), .i0(din), .i1(dout)" in output
    assert result.monitors.count("endmodule") == 1


def test_sva_in_design_files_is_an_error_by_default() -> None:
    """6."""
    sources = _sources("pipe")
    with pytest.raises(RewriteError) as caught:
        _translate(sources, _verification(sources), "top")
    (error,) = caught.value.diagnostics
    assert error.severity is Severity.ERROR
    assert str(error).startswith("pipe/design/pipe.sv:12:5: error: assertion in design file")
    assert "verification file" in error.message


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("module top; endmodule\nproperty q; 1'b1; endproperty\n", "property"),
        (
            "module top; endmodule\nmodule unused(input logic c);\n"
            "  assert property (@(posedge c) c);\nendmodule\n",
            "assertion",
        ),
        ("module top; endmodule\nbind top unused2 u();\nmodule unused2; endmodule\n", "bind"),
    ],
)
def test_every_kind_of_design_sva_is_reported(text: str, kind: str) -> None:
    """6: elaborated or not, assertions, declarations and binds."""
    sources = [("d.sv", text), ("v.sv", "module chk; endmodule\n")]
    with pytest.raises(RewriteError) as caught:
        _translate(sources, frozenset({"v.sv"}), "top")
    assert any(f"{kind} in design file d.sv" in d.message for d in caught.value.diagnostics)


def test_assertions_in_design_files_are_warnings_when_allowed() -> None:
    """6."""
    result, _ = _fixture("pipe", "top")
    (warning,) = result.warnings
    assert warning.severity is Severity.WARNING
    assert warning.source is not None
    assert (warning.source.file, warning.source.line) == ("pipe/design/pipe.sv", 12)
    (copy,) = [f for f in result.files if f.role is FileRole.DESIGN_COPY]
    assert "a_design: assert property (@(posedge clk) q == r);" in copy.text


def test_rewrite_is_deterministic() -> None:
    """7."""
    assert _fixture("counter", "counter")[0] == _fixture("counter", "counter")[0]


@pytest.mark.parametrize("strategy", [Strategy.DIRECT, Strategy.WINDOW, Strategy.AUTO])
def test_per_attempt_monitor_is_spliced(strategy: Strategy) -> None:
    """7a: a nested implication gets an attempt monitor, whatever the strategy."""
    sources = [
        (
            "v.sv",
            "module p(input logic clk, a, b, c);\n"
            "  assert property (@(posedge clk) a |-> (b |-> c));\nendmodule\n",
        )
    ]
    result, _ = _translate(sources, frozenset({"v.sv"}), "p", strategy=strategy, equivalence=True)
    assert "// form: attempt" in result.monitors
    assert all(form is Form.DIRECT for form in result.forms.values())
    assert result.forms
    assert result.equivalence == {}
    (output,) = result.files
    assert "assert property" not in _code(output.text)


# --- 8: errors ---------------------------------------------------------------------------

_CHK = "module chk(input logic clk, a);\n  assert property (@(posedge clk) a);\nendmodule\n"

ERROR_CASES = [
    pytest.param(
        {
            "v.sv": "module p(input logic clk, a);\n"
            "  assume property (@(posedge clk) a |-> strong(##[1:$] a));\nendmodule\n"
        },
        "p",
        "v.sv:2:3",
        "unbounded",
        id="rejected-assertion",
    ),
    *[
        pytest.param(
            {"v.sv": f"module p(input logic clk, a, b, c);\n  {text};\nendmodule\n"},
            "p",
            "v.sv:2:3",
            fragment,
            id=name,
        )
        for name, text, fragment in (
            (
                "cover-of-property-or",
                "cover property (@(posedge clk) (a |-> b) or (c |-> a))",
                "cover of this property is not supported",
            ),
            (
                "cover-of-eventually-with-late-nonvacuity",
                "cover property (@(posedge clk) eventually [0:1] (((a ##2 b) or c) |-> a))",
                "cover of this property is not supported",
            ),
            (
                "cover-of-not-of-nested-implication",
                "cover property (@(posedge clk) not (a |-> (b |-> c)))",
                "cover of this property is not supported",
            ),
            (
                "if-else-condition-with-past",
                "assert property (@(posedge clk) if ($past(a)) (b |-> c) else c)",
                "uses $past",
            ),
        )
    ],
    pytest.param(
        {"v.sv": "module p(input logic clk, a);\nendmodule\n" + _CHK.replace("chk", "q")},
        "p",
        "v.sv:4:3",
        "not elaborated",
        id="not-elaborated",
    ),
    pytest.param(
        {
            "v.sv": "`define A assert property (@(posedge clk) a);\n"
            "module p(input logic clk, a);\n  `A\nendmodule\n"
        },
        "p",
        "v.sv:3",
        "macro",
        id="macro-assertion",
    ),
    pytest.param(
        {
            "v.sv": "interface i(input logic clk, a);\n  assert property (@(posedge clk) a);\n"
            "endinterface\nmodule p(input logic clk, a);\n  i u(.clk(clk), .a(a));\nendmodule\n"
        },
        "p",
        "v.sv:2:3",
        "interface",
        id="assertion-in-interface",
    ),
    pytest.param(
        {
            "v.sv": 'module c #(parameter string MODE = "a") (input logic clk, a, b);\n'
            '  assert property (@(posedge clk) (MODE == "a") ? a : b);\nendmodule\n'
            "module p(input logic clk, a, b);\n"
            '  c #("a") u1(.clk(clk), .a(a), .b(b));\n'
            '  c #("b") u2(.clk(clk), .a(a), .b(b));\nendmodule\n'
        },
        "p",
        "v.sv:2:3",
        "MODE is visible at this assertion but cannot be compared",
        id="instances-differ-by-a-string",
    ),
    pytest.param(
        {
            "v.sv": 'module c #(parameter int N = 1, parameter string MODE = "a")'
            " (input logic clk, a, b);\n"
            '  assert property (@(posedge clk) (MODE == "a") ? a : b);\nendmodule\n'
            "module p(input logic clk, a, b);\n"
            '  c #(1, "a") u1(.clk(clk), .a(a), .b(b));\n'
            '  c #(2, "b") u2(.clk(clk), .a(a), .b(b));\nendmodule\n'
        },
        "p",
        "v.sv:2:3",
        "MODE is visible at this assertion but cannot be compared",
        id="string-parameter-hidden-behind-an-integral-one",
    ),
    pytest.param(
        {
            "v.sv": "module c #(parameter type T = logic) (input logic clk, a);\n"
            "  assert property (@(posedge clk) a |-> ##($bits(T)) a);\nendmodule\n"
            "module p(input logic clk, a);\n"
            "  c #(logic [3:0]) u1(.clk(clk), .a(a));\nendmodule\n"
        },
        "p",
        "v.sv:2:3",
        "T is visible at this assertion but cannot be compared",
        id="type-parameter",
    ),
    pytest.param(
        {
            "v.sv": "interface it #(parameter int N = 1) (); endinterface\n"
            "module c (it p, input logic clk, a);\n"
            "  assert property (@(posedge clk) a |-> ##(p.N) a);\nendmodule\n"
            "module q(input logic clk, a);\n  it #(3) i();\n"
            "  c u(.p(i), .clk(clk), .a(a));\nendmodule\n"
        },
        "q",
        "v.sv:3:3",
        "p is visible at this assertion but cannot be compared",
        id="interface-port",
    ),
    pytest.param(
        {
            "v.sv": "module c #(parameter int \\P-x = 3, parameter int P = 4, x = 1)"
            " (input logic clk, a);\n"
            "  assert property (@(posedge clk) a |-> ##(\\P-x ) a);\nendmodule\n"
            "module q(input logic clk, a);\n  c u(.clk(clk), .a(a));\nendmodule\n"
        },
        "q",
        "v.sv:2:3",
        "P-x is visible at this assertion but cannot be compared",
        id="escaped-parameter-name",
    ),
    pytest.param(
        {
            "v.sv": "package k;\n  logic x;\nendpackage\nmodule p(input logic clk);\n"
            "  assert property (@(posedge clk) k::x);\nendmodule\n"
        },
        "p",
        "v.sv:5:3",
        "scope",
        id="package-signal",
    ),
    pytest.param(
        {"v.sv": "checker ck(logic x);\nendchecker\nmodule p; endmodule\n"},
        "p",
        "v.sv:1:1",
        "checker",
        id="checker",
    ),
    pytest.param(
        {
            "d.sv": "module host(input logic clk, a); endmodule\n"
            "module top(input logic clk, a);\n  host u1(.clk(clk), .a(a));\nendmodule\n",
            "v.sv": _CHK + "bind host: top.u1 chk u_chk (.clk(clk), .a(a));\n",
        },
        "top",
        "v.sv:4:1",
        "instance bind",
        id="instance-bind-list",
    ),
    pytest.param(
        {
            "d.sv": "module host(input logic clk, a); endmodule\n"
            "module top(input logic clk, a);\n  host u1(.clk(clk), .a(a));\nendmodule\n",
            "v.sv": _CHK + "bind top.u1 chk u_chk (.clk(clk), .a(a));\n",
        },
        "top",
        "v.sv:4:1",
        "instance bind",
        id="instance-bind-path",
    ),
    pytest.param(
        {
            "d.sv": "module top(input logic clk, a); endmodule\n",
            "v.sv": _CHK + "`define B bind top chk u_chk (.clk(clk), .a(a));\n`B\n",
        },
        "top",
        "v.sv:5",
        "macro",
        id="macro-bind",
    ),
    pytest.param(
        {
            "a/x.sv": "module top(input logic clk, a); endmodule\n",
            "b/x.sv": _CHK + "bind top chk u_chk (.clk(clk), .a(a));\n",
        },
        "top",
        None,
        "x.sv",
        id="name-collision",
    ),
    pytest.param(
        {
            MONITORS_FILE: "module p(input logic clk, a);\n"
            "  assert property (@(posedge clk) a);\nendmodule\n"
        },
        "p",
        None,
        MONITORS_FILE,
        id="monitors-file-name",
    ),
    pytest.param(
        {
            "v.sv": "module p(input logic clk, outer);\n"
            "  property q; @(posedge clk) outer; endproperty\n"
            "  if (1) begin : g\n    logic outer;\n    a: assert property (q);\n  end\n"
            "endmodule\n"
        },
        "p",
        "v.sv:2:30",
        "means something else",
        id="shadowed-name",
    ),
    pytest.param(
        {
            "v.sv": "module p(input logic clk, a);\n"
            "  initial begin\n    assert property (@(posedge clk) a);\n    #1;\n  end\nendmodule\n"
        },
        "p",
        "v.sv:3:5",
        "may not fall through",
        id="procedural-assertion-in-initial-with-a-delay",
    ),
    pytest.param(
        {
            "v.sv": "module p(input logic clk, a);\n"
            "  for (genvar i = 0; i < 2; i++)\n"
            "    always @(posedge clk) assert property (a);\nendmodule\n"
        },
        "p",
        "v.sv:3:27",
        "unbraced",
        id="procedural-assertion-in-unbraced-generate",
    ),
    pytest.param(
        {
            "v.sv": "module p(input logic clk, a, b);\n"
            "  cover sequence (@(posedge clk) a ##1 b);\nendmodule\n"
        },
        "p",
        "v.sv:2:3",
        "cover sequence",
        id="cover-sequence",
    ),
    pytest.param(
        {
            "v.sv": "module p(input logic clk, a, b, c);\n"
            "  cover property (@(posedge clk) a ##[1:$] b |-> c);\nendmodule\n"
        },
        "p",
        "v.sv:2:3",
        "a cover of an implication with an unbounded antecedent",
        id="cover-of-an-unbounded-antecedent",
    ),
    pytest.param(
        {
            "v.sv": "module p(input logic clk, a, b);\n"
            "  assume property (@(posedge clk) a |-> strong(##[1:$] b));\nendmodule\n"
        },
        "p",
        "v.sv:2:3",
        "(liveness) in 'assume'",
        id="strong-unbounded-consequent",
    ),
    pytest.param(
        {
            "v.sv": "module p(input logic clk, a, b);\n"
            "  cover property (@(posedge clk) always a);\nendmodule\n"
        },
        "p",
        "v.sv:2:3",
        "unbounded 'always'",
        id="cover-of-an-unbounded-always",
    ),
]


@pytest.mark.parametrize(("sources", "top", "where", "fragment"), ERROR_CASES)
def test_errors(sources: dict[str, str], top: str, where: str | None, fragment: str) -> None:
    """8."""
    verification = {name for name in sources if not name.startswith(("d.sv", "a/"))}
    errors = _errors(sources, verification, top)
    assert any(fragment in e and (where is None or e.startswith(where)) for e in errors), errors


@pytest.mark.parametrize(
    ("included", "kind"),
    [
        ("assert property (@(posedge clk) a);\n", "assertion"),
        ("property q; @(posedge clk) a; endproperty\n", "property"),
        ("sequence s; a ##1 a; endsequence\n", "sequence"),
    ],
)
def test_sva_in_a_file_included_by_a_verification_file_is_an_error(
    tmp_path: Path, included: str, kind: str
) -> None:
    """8: included file."""
    (tmp_path / "inc.svh").write_text(included)
    top = tmp_path / "v.sv"
    top.write_text(
        'module p(input logic clk, a);\n  `include "inc.svh"\n'
        "  assert property (@(posedge clk) a);\nendmodule\n"
    )
    options = SlangOptions(top=("p",), include_dirs=(tmp_path,))
    document = export_design([top], options)
    sources = [(str(top), top.read_text())]
    with pytest.raises(RewriteError) as caught:
        rewrite(sources, frozenset({str(top)}), syntax_items(document), convert(document))
    messages = [str(d) for d in caught.value.diagnostics]
    assert any(f"{kind} in a file included by verification file" in m for m in messages), messages


def test_files_included_by_design_files_are_left_alone(tmp_path: Path) -> None:
    """6: an assertion included into a design file counts as design SVA."""
    (tmp_path / "inc.svh").write_text("assert property (@(posedge clk) a);\n")
    design = tmp_path / "d.sv"
    design.write_text('module p(input logic clk, a);\n  `include "inc.svh"\nendmodule\n')
    verify = tmp_path / "v.sv"
    verify.write_text(
        "module chk(input logic clk, a);\n  assert property (@(posedge clk) a);\nendmodule\n"
        "bind p chk u_chk (.clk(clk), .a(a));\n"
    )
    options = SlangOptions(top=("p",), include_dirs=(tmp_path,))
    document = export_design([design, verify], options)
    sources = [(str(design), design.read_text()), (str(verify), verify.read_text())]
    items, conversion = syntax_items(document), convert(document)
    with pytest.raises(RewriteError):
        rewrite(sources, frozenset({str(verify)}), items, conversion)
    allowed = Severity.WARNING
    result = rewrite(sources, frozenset({str(verify)}), items, conversion, allowed)
    (warning,) = result.warnings
    assert warning.source is not None and warning.source.file.endswith("inc.svh")


# --- 9: end to end -----------------------------------------------------------------------

_SBY = """[tasks]
{task}

[options]
mode {mode}
depth {depth}

[engines]
{engine}

[script]
{reads}
prep -top {top}

[files]
{files}
"""


def _write_output(result: Rewrite, sources: list[tuple[str, str]], directory: Path) -> list[str]:
    """Write the files to read, in read order; returns their names."""
    copies = {f.source for f in result.files if f.role is FileRole.DESIGN_COPY}
    names: list[str] = []
    for name, text in sources:
        if name in copies or name in _verification(sources):
            continue
        (directory / Path(name).name).write_text(text)
        names.append(Path(name).name)
    for output in result.files:
        (directory / output.name).write_text(output.text)
        names.append(output.name)
    (directory / MONITORS_FILE).write_text(result.monitors)
    return [*names, MONITORS_FILE]


def _sby_run(
    tool: Tool,
    directory: Path,
    names: list[str],
    task: str,
    mode: str,
    engine: str,
    depth: int,
    top: str = "counter",
) -> SbyResult:
    reads = "\n".join(f"read_verilog -formal -sv {name}" for name in names)
    text = _SBY.format(
        task=task,
        mode=mode,
        depth=depth,
        engine=engine,
        reads=reads,
        files="\n".join(names),
        top=top,
    )
    sby_file = directory / f"{task}.sby"
    sby_file.write_text(text)
    result = run_sby(tool, sby_file, task, directory / f"work_{task}")
    return result


@pytest.mark.parametrize("case", ["boolean", "temporal", "disabled", "procedural", "initial"])
@pytest.mark.parametrize("constrained", [True, False], ids=["restricted", "unconstrained"])
def test_restrict_constrains_formal_traces_without_excluding_every_trace(
    sby_tool: Tool, tmp_path: Path, case: str, constrained: bool
) -> None:
    """16.14.4: each restriction proves its claim and admits a reachable witness."""
    sources = _restrict_sources(f"restrict_{case}")
    if not constrained:
        sources = [
            (name, re.sub(r"scenario: restrict property[^;]+;", ";", text))
            for name, text in sources
        ]
    top = f"restrict_{case}"
    result, _ = _translate(sources, _verification(sources), top)
    names = _write_output(result, sources, tmp_path)
    checked = _sby_run(sby_tool, tmp_path, names, "bmc", "bmc", "smtbmc yices", 5, top=top)
    assert checked.status is (SbyStatus.PASS if constrained else SbyStatus.FAIL), checked.log[
        -3000:
    ]
    if constrained:
        # Reachability rules out a proof obtained by forbidding every trace.
        covered = _sby_run(sby_tool, tmp_path, names, "cover", "cover", "smtbmc yices", 5, top=top)
        assert covered.status is SbyStatus.PASS, covered.log[-3000:]


@pytest.mark.parametrize(
    ("case", "constrained", "status"),
    [
        ("fairness", True, SbyStatus.PASS),
        ("fairness", False, SbyStatus.FAIL),
        ("fairness_stronger", True, SbyStatus.FAIL),
    ],
)
def test_restrict_fairness_constrains_only_the_promised_liveness(
    sby_tool: Tool,
    sby_live_tool: Tool,
    tmp_path: Path,
    case: str,
    constrained: bool,
    status: SbyStatus,
) -> None:
    """16.14.4: supported strong restrictions share assume's fairness semantics."""
    sources = _restrict_sources(f"restrict_{case}")
    if not constrained:
        sources = [
            (name, re.sub(r"scenario: restrict property[^;]+;", ";", text))
            for name, text in sources
        ]
    result, _ = _translate(sources, _verification(sources), f"restrict_{case}")
    assert result.live
    names = _write_output(result, sources, tmp_path)
    sby_file = tmp_path / "live.sby"
    sby_file.write_text(
        _LIVE_SBY.format(
            prove_reads=_reads("prove", names, ""),
            live_reads=_reads("live", names, "-D SVA2YOSYS_LIVE "),
            files="\n".join(names),
        ).replace("prep -top p", f"prep -top restrict_{case}")
    )
    proved = run_sby(sby_tool, sby_file, "prove", tmp_path / "work_prove")
    assert proved.status is SbyStatus.PASS, proved.log[-3000:]
    lived = run_sby(sby_live_tool, sby_file, "live", tmp_path / "work_live")
    assert lived.status is status, lived.log[-3000:]


def test_restrict_needing_a_liveness_witness_is_a_located_error() -> None:
    """16.14.4 does not extend the supported strong-assumption class."""
    errors = _errors(
        {
            "v.sv": "module p(input logic clk, a, b);\n"
            "  scenario: restrict property (@(posedge clk) a |-> strong(##[1:$] b));\n"
            "endmodule\n"
        },
        {"v.sv"},
        "p",
    )
    assert any("v.sv:2:3" in error and "witness" in error for error in errors)


@pytest.mark.parametrize("design", ["design", "mutant"])
def test_rewritten_counter_under_sby(sby_tool: Tool, tmp_path: Path, design: str) -> None:
    """9."""
    sources = _sources("counter", design)
    result, _ = _translate(sources, _verification(sources), "counter")
    names = _write_output(result, sources, tmp_path)
    if design == "design":
        proved = _sby_run(sby_tool, tmp_path, names, "prove", "prove", "abc pdr", 20)
        assert proved.status is SbyStatus.PASS, proved.log[-3000:]
        covered = _sby_run(sby_tool, tmp_path, names, "cover", "cover", "smtbmc yices", 20)
        assert covered.status is SbyStatus.PASS, covered.log[-3000:]
        assert [hit.step for hit in covered.reached_covers] == [16]
    else:
        failed = _sby_run(sby_tool, tmp_path, names, "bmc", "bmc", "smtbmc yices", 12)
        assert failed.status is SbyStatus.FAIL, failed.log[-3000:]
        assert [hit.step for hit in failed.failed_assertions] == [8]


def test_non_ascii_text_before_the_edits() -> None:
    """1: offsets are positions in the text, not Slang's byte offsets."""
    text = (
        "// délai de 5 µs — commentaire\n"
        "module p(input logic clk, a);\n"
        "  // ünïcödé\n  q: assert property (@(posedge clk) a); // fin\n"
        "endmodule\n"
    )
    result, _ = _translate([("v.sv", text)], frozenset({"v.sv"}), "p")
    output = result.files[0].text
    assert output.split("\n")[3] == (
        "  __sva2yosys_p0 __sva2yosys_i0 (.clk(clk), .i0(a), .fail(), .success()); "
        "/* q: assert property (@(posedge clk) a); */ // fin"
    )
    assert output.replace(output.split("\n")[3], "") == text.replace(text.split("\n")[3], "")


def test_instances_that_differ_get_one_monitor_each() -> None:
    """5: per-instance and per-iteration variants, selected by parameters and genvars."""
    text = (
        "module c #(parameter int N = 1) (input logic clk, input logic [1:0] v);\n"
        "  for (genvar i = 0; i < 2; i++) begin : g\n"
        "    x: assert property (@(posedge clk) v[i] |-> ##N v[1 - i]);\n"
        "  end\nendmodule\n"
        "module p(input logic clk, input logic [1:0] v);\n"
        "  c #(1) u1(.clk(clk), .v(v));\n  c #(2) u2(.clk(clk), .v(v));\nendmodule\n"
    )
    result, directives = _translate([("v.sv", text)], frozenset({"v.sv"}), "p")
    assert len(directives) == 4
    line = result.files[0].text.split("\n")[2]
    for n, i, k in [(1, 0, 0), (1, 1, 1), (2, 0, 2), (2, 1, 3)]:
        assert f"if ((N == {n} && i == {i})) begin : __sva2yosys_p0_g{k}" in line
        assert f"__sva2yosys_p0_v{k} __sva2yosys_i0" in line
    assert _code(line).rstrip().endswith("always @* assert (1'b0); end")
    assert result.monitors.count("endmodule") == 4
    assert sorted(result.forms) == [f"__sva2yosys_p0_v{k}" for k in range(4)]


def test_the_selection_pins_every_dependent_visible_parameter() -> None:
    """5: dependent parameters that do not vary are compared too, so other combinations
    fail; here the assertion reads both N (a delay) and M (a condition)."""
    text = (
        "module c #(parameter int N = 1, parameter int M = 1) (input logic clk, a);\n"
        "  x: assert property (@(posedge clk) (M > 0) && a |-> ##N a);\nendmodule\n"
        "module p(input logic clk, a);\n"
        "  c #(1, 5) u1(.clk(clk), .a(a));\n  c #(2, 5) u2(.clk(clk), .a(a));\nendmodule\n"
    )
    result, _ = _translate([("v.sv", text)], frozenset({"v.sv"}), "p")
    line = result.files[0].text.split("\n")[1]
    assert "if ((M == 5 && N == 1)) begin : __sva2yosys_p0_g0" in line
    assert "else if ((M == 5 && N == 2)) begin : __sva2yosys_p0_g1" in line


def test_the_selection_does_not_compare_a_parameter_the_assertion_does_not_depend_on() -> None:
    """5: M is visible but unused, so it neither keys the monitor nor appears in the
    selection."""
    text = (
        "module c #(parameter int N = 1, parameter int M = 1) (input logic clk, a);\n"
        "  x: assert property (@(posedge clk) a |-> ##N a);\nendmodule\n"
        "module p(input logic clk, a);\n"
        "  c #(1, 5) u1(.clk(clk), .a(a));\n  c #(2, 5) u2(.clk(clk), .a(a));\nendmodule\n"
    )
    result, _ = _translate([("v.sv", text)], frozenset({"v.sv"}), "p")
    line = result.files[0].text.split("\n")[1]
    assert "if ((N == 1)) begin : __sva2yosys_p0_g0" in line
    assert "else if ((N == 2)) begin : __sva2yosys_p0_g1" in line
    assert "M ==" not in line


def test_a_single_parametrised_variant_is_guarded() -> None:
    """5: one monitor for every instance, still selected by the parameters' values."""
    text = (
        "module c #(parameter int N = 1) (input logic clk, a);\n"
        "  x: assert property (@(posedge clk) a |-> ##N a);\nendmodule\n"
        "module p(input logic clk, a);\n"
        "  c #(3) u1(.clk(clk), .a(a));\n  c #(3) u2(.clk(clk), .a(a));\nendmodule\n"
    )
    result, _ = _translate([("v.sv", text)], frozenset({"v.sv"}), "p")
    line = result.files[0].text.split("\n")[1]
    assert line.lstrip().startswith(
        "if ((N == 3)) begin : __sva2yosys_p0_g0 __sva2yosys_p0 __sva2yosys_i0 "
    )
    assert _code(line).rstrip().endswith("always @* assert (1'b0); end")
    assert result.monitors.count("endmodule") == 1


def test_parameters_declared_after_the_assertion_are_not_compared() -> None:
    """5: only what is declared before the assertion is visible there."""
    text = (
        "module c #(parameter int N = 1) (input logic clk, a);\n"
        "  x: assert property (@(posedge clk) a |-> ##N a);\n"
        '  localparam int L = N;\n  localparam string S = "s";\nendmodule\n'
        "module p(input logic clk, a);\n"
        "  c #(1) u1(.clk(clk), .a(a));\n  c #(2) u2(.clk(clk), .a(a));\nendmodule\n"
    )
    result, _ = _translate([("v.sv", text)], frozenset({"v.sv"}), "p")
    line = result.files[0].text.split("\n")[1]
    assert "if ((N == 1)) begin" in line and "else if ((N == 2)) begin" in line
    assert "L ==" not in line


def test_a_declaration_after_the_assertion_does_not_hide_the_outer_one() -> None:
    """5: the inner N follows the assertion, so the assertion sees the module's N."""
    text = (
        "module c #(parameter int N = 1) (input logic clk, a);\n"
        "  if (1) begin : g\n"
        "    x: assert property (@(posedge clk) a |-> ##N a);\n"
        "    localparam int N = 7;\n  end\nendmodule\n"
        "module p(input logic clk, a);\n  c #(3) u1(.clk(clk), .a(a));\nendmodule\n"
    )
    result, _ = _translate([("v.sv", text)], frozenset({"v.sv"}), "p")
    line = result.files[0].text.split("\n")[2]
    assert line.lstrip().startswith("if ((N == 3)) begin : __sva2yosys_p0_g0 ")


@pytest.mark.parametrize("after", [False, True])
def test_included_parameters_count_only_before_the_assertion(tmp_path: Path, after: bool) -> None:
    """5: declaration order is the order in the compilation unit, includes expanded."""
    (tmp_path / "inc.svh").write_text("localparam int L = N + 1;\n")
    include = '  `include "inc.svh"\n'
    assertion = "  x: assert property (@(posedge clk) a |-> ##N a);\n"
    body = assertion + include if after else include + assertion
    top = tmp_path / "v.sv"
    top.write_text(
        "module c #(parameter int N = 1) (input logic clk, a);\n" + body + "endmodule\n"
        "module p(input logic clk, a);\n  c #(2) u1(.clk(clk), .a(a));\nendmodule\n"
    )
    document = export_design([top], SlangOptions(top=("p",), include_dirs=(tmp_path,)))
    (directive,) = convert(document).directives
    assert [name for name, _ in directive.parameters] == (["N"] if after else ["L", "N"])


_UNTYPED = (
    "module c #(parameter P = 1) (input logic clk, a);\n"
    "  x: assert property (@(posedge clk) a |-> ##($bits(P)) a);\nendmodule\n"
)


def test_an_untyped_parameter_is_compared_with_its_width_and_signedness() -> None:
    """5: P == 1'b1 alone would also hold for P = 8'b1, which needs another monitor."""
    text = (
        _UNTYPED + "module p(input logic clk, a);\n  c #(1'b1) u1(.clk(clk), .a(a));\nendmodule\n"
    )
    result, _ = _translate([("v.sv", text)], frozenset({"v.sv"}), "p")
    line = result.files[0].text.split("\n")[1]
    assert "if ((P == 1'b1 && $bits(P) == 1 && (((P - P) - 1) < 0) == 1'b0)) begin" in line


@pytest.mark.parametrize(("value", "selected"), [("1'b1", True), ("8'b1", False), ("1", False)])
def test_yosys_selects_the_monitor_only_for_the_observed_type(
    yosys_tool: Tool, tmp_path: Path, value: str, selected: bool
) -> None:
    """5: elaborated by Yosys with another width or signedness, the guard does not hold."""
    text = (
        _UNTYPED + "module p(input logic clk, a);\n  c #(1'b1) u1(.clk(clk), .a(a));\nendmodule\n"
    )
    result, _ = _translate([("v.sv", text)], frozenset({"v.sv"}), "p")
    (tmp_path / "v.sv").write_text(result.files[0].text.replace("c #(1'b1)", f"c #({value})"))
    (tmp_path / MONITORS_FILE).write_text(result.monitors)
    check = "-assert-count 1" if selected else "-assert-none"
    run_yosys(
        yosys_tool,
        [
            f"read_verilog -formal -sv v.sv {MONITORS_FILE}",
            "hierarchy -top p",
            f"select {check} t:__sva2yosys_p0",
        ],
        tmp_path,
    )


# --- procedural assertions ---------------------------------------------------------------

_GATED = """module p(input logic clk, en, a);
  logic b = 1'b0;
  always_ff @(posedge clk) b <= {next};
  always @(posedge clk) begin
    if (en) x: assert property (a |=> b);
  end
endmodule
"""


def test_procedural_assertion_moves_after_its_block() -> None:
    """2."""
    text = _GATED.format(next="a & en")
    result, _ = _translate([("v.sv", text)], frozenset({"v.sv"}), "p")
    output = result.files[0].text
    lines = output.split("\n")
    assert len(lines) == len(text.split("\n"))
    assert lines[4] == "    if (en) ;"
    assert lines[5].startswith("  end __sva2yosys_p0 __sva2yosys_i0 (.clk(clk), ")
    assert lines[5].endswith(".fail(), .success()); /* x: assert property (a |=> b); */")


@pytest.mark.parametrize(
    ("design", "status", "step"),
    [
        # b follows a only when en: exactly what the enabled attempts require
        ("a & en", SbyStatus.PASS, None),
        # never when en: the attempt enabled at step 0 fails at step 1
        ("a & !en", SbyStatus.FAIL, 1),
    ],
)
def test_procedural_assertion_under_sby(
    sby_tool: Tool, tmp_path: Path, design: str, status: SbyStatus, step: int | None
) -> None:
    """9."""
    sources = [("x/verify/v.sv", _GATED.format(next=design))]
    result, _ = _translate(sources, _verification(sources), "p")
    names = _write_output(result, sources, tmp_path)
    mode = "prove" if status is SbyStatus.PASS else "bmc"
    engine = "abc pdr" if mode == "prove" else "smtbmc yices"
    checked = _sby_run(sby_tool, tmp_path, names, mode, mode, engine, 10, top="p")
    assert checked.status is status, checked.log[-3000:]
    if step is not None:
        assert [hit.step for hit in checked.failed_assertions] == [step]


def test_localized_monitors_keep_the_strength() -> None:
    """2: the monitor is generated from the localized assertion, strength included."""
    from sva2yosys.ir.property import SequenceProperty, Strength
    from sva2yosys.rewrite import _localize

    text = """module p(input logic clk, a, b);
  x: assert property (@(posedge clk) strong(a ##1 b));\nendmodule\n"""
    _, (directive,) = _translate([("v.sv", text)], frozenset({"v.sv"}), "p")
    localized = _localize(directive, {"p.clk": "clk", "p.a": "a", "p.b": "b"})
    assert isinstance(localized.property, SequenceProperty)
    assert localized.property.strength is Strength.STRONG


_PROCEDURAL_HEAD = "module p(input logic clk, en, a, b, rst, input logic [1:0] sel);\n"

PROCEDURAL_CASES = [
    # cover: reachable when enabled; unreachable when the enable is never true
    ("always @(posedge clk) if (en) c: cover property (a |-> b);", "cover", SbyStatus.PASS),
    (
        "logic g = 1'b0;\n  always_ff @(posedge clk) g <= 1'b0;\n"
        "  always @(posedge clk) if (g) c: cover property (a |-> b);",
        "cover",
        SbyStatus.FAIL,
    ),
    # an enabled assumption constrains the enabled cycles only
    (
        "always @(posedge clk) if (en) m: assume property (a |=> b);\n"
        "  x: assert property (@(posedge clk) en && a |=> b);",
        "prove",
        SbyStatus.PASS,
    ),
    (
        "always @(posedge clk) if (en) m: assume property (a |=> b);\n"
        "  x: assert property (@(posedge clk) a |=> b);",
        "bmc",
        SbyStatus.FAIL,
    ),
    # case: a later identical item is unreachable; default is "none of the items"
    (
        "always @(posedge clk) case (sel)\n"
        "    2'd0: x0: assert property (sel == 2'd0);\n"
        "    2'd0: x1: assert property (1'b0);\n"
        "    default: x2: assert property (sel != 2'd0);\n"
        "  endcase",
        "prove",
        SbyStatus.PASS,
    ),
    (
        "always @(posedge clk) case (sel)\n"
        "    2'd0: x0: assert property (sel == 2'd0);\n"
        "    default: x2: assert property (sel == 2'd3);\n"
        "  endcase",
        "bmc",
        SbyStatus.FAIL,
    ),
    # disable iff aborts the obligations of an enabled assertion
    (
        "always @(posedge clk) if (en) x: assert property (disable iff (rst) a |-> ##2 b);\n"
        "  m: assume property (@(posedge clk) a |=> rst);",
        "prove",
        SbyStatus.PASS,
    ),
    (
        "always @(posedge clk) if (en) x: assert property (a |-> ##2 b);\n"
        "  m: assume property (@(posedge clk) a |=> rst);",
        "bmc",
        SbyStatus.FAIL,
    ),
]


@pytest.mark.parametrize(
    ("body", "mode", "status"),
    PROCEDURAL_CASES,
    ids=[
        "cover-enabled",
        "cover-never-enabled",
        "assume-enabled-proves",
        "assume-only-when-enabled",
        "case-priority-and-default",
        "case-default-wrong",
        "disable-aborts",
        "no-disable-fails",
    ],
)
def test_procedural_directives_under_sby(
    sby_tool: Tool, tmp_path: Path, body: str, mode: str, status: SbyStatus
) -> None:
    """9."""
    sources = [("x/verify/v.sv", _PROCEDURAL_HEAD + "  " + body + "\nendmodule\n")]
    result, _ = _translate(sources, _verification(sources), "p")
    names = _write_output(result, sources, tmp_path)
    engine = "abc pdr" if mode == "prove" else "smtbmc yices"
    checked = _sby_run(sby_tool, tmp_path, names, mode, mode, engine, 10, top="p")
    assert checked.status is status, checked.log[-3000:]


def test_property_and_gets_one_monitor_instance_per_conjunct() -> None:
    """2: a property 'and' of assert/assume is checked by one monitor per conjunct, each
    asserting its own conjunct; a cover of it gets one per-attempt monitor."""
    text = (
        "module c(input logic clk, input logic a, b, d);\n"
        "  x: assert property (@(posedge clk) (a |-> b) and not d);\n"
        "endmodule\n"
    )
    result, directives = _translate([("c.sv", text)], frozenset({"c.sv"}), "c")
    assert len(directives) == 1
    line = result.files[0].text.split("\n")[1]
    assert "if (1'b1) begin : __sva2yosys_p0_and " in line
    assert "__sva2yosys_p0_c0 __sva2yosys_i0_c0 (" in line
    assert "__sva2yosys_p0_c1 __sva2yosys_i0_c1 (" in line
    assert result.monitors.count("endmodule") == 2
    assert sorted(result.forms) == ["__sva2yosys_p0_c0", "__sva2yosys_p0_c1"]
    covered, _ = _translate([("c.sv", text.replace("assert", "cover"))], frozenset({"c.sv"}), "c")
    line = covered.files[0].text.split("\n")[1]
    assert "__sva2yosys_p0_and" not in line
    assert "_c0" not in line
    assert "_c1" not in line
    assert "__sva2yosys_p0 __sva2yosys_i0 (" in line
    assert covered.monitors.count("endmodule") == 1
    assert "// form: attempt" in covered.monitors


def test_property_and_under_an_unbraced_if_stays_one_statement() -> None:
    """2: the conjunct instances are one generate block, so an unbraced 'if ... else' and
    an unbraced loop keep their meaning."""
    text = (
        "module c #(parameter int P = 1) (input logic clk, input logic a, b, d);\n"
        "  if (P) x: assert property (@(posedge clk) (a |-> b) and not d);\n"
        "  else begin end\n"
        "endmodule\n"
    )
    result, _ = _translate([("c.sv", text)], frozenset({"c.sv"}), "c")
    out = result.files[0].text
    assert "if (P) if (1'b1) begin : __sva2yosys_p0_and " in out
    assert "end else begin end" in out, out


def test_property_and_keeps_the_enclosing_else(yosys_tool: Tool, tmp_path: Path) -> None:
    """2: elaborated by Yosys, the original else branch still drives its output when the
    condition is false (the wrapper's own empty else keeps it with the outer if)."""
    text = (
        "module c #(parameter int P = 1) (input logic clk, a, b, output logic o);\n"
        "  if (P) x: assert property (@(posedge clk) (a |-> b) and not a);\n"
        "  else assign o = 1'b1;\n"
        "endmodule\n"
    )
    # Translated with P = 1 (the assertion is elaborated), elaborated by Yosys with P = 0.
    result, _ = _translate([("c.sv", text)], frozenset({"c.sv"}), "c")
    output = result.files[0].text
    assert "parameter int P = 1" in output
    (tmp_path / "c.sv").write_text(output.replace("parameter int P = 1", "parameter int P = 0"))
    (tmp_path / MONITORS_FILE).write_text(result.monitors)
    run_yosys(
        yosys_tool,
        [
            f"read_verilog -formal -sv c.sv {MONITORS_FILE}",
            "hierarchy -top c",
            "proc",
            "flatten",
            "opt",
            "sat -prove o 1 -verify",
        ],
        tmp_path,
    )


# --- 10: per-attempt properties under SBY ------------------------------------------------

_ATTEMPT_HEAD = "module p(input logic clk, a, b, c, d, e, rst);\n"
_CK = "@(posedge clk)"


def _directives(*lines: str) -> str:
    return "".join(f"  {line.replace('@@', _CK)};\n" for line in lines)


_PASS, _FAIL = SbyStatus.PASS, SbyStatus.FAIL

# (directives, mode, expected status); "cover" FAIL means: not reached.
ATTEMPT_FAST_CASES = {
    "nested-implication-proves": (
        _directives(
            "assert property (@@ a |-> (b |-> c))",
            "assume property (@@ a && b |-> c)",
        ),
        "prove",
        _PASS,
    ),
    "nested-implication-fails-unassumed": (
        _directives("assert property (@@ a |-> (b |-> c))"),
        "bmc",
        _FAIL,
    ),
}

ATTEMPT_SLOW_CASES = {
    "not-proves": (
        _directives(
            "assert property (@@ not (a |-> b))",
            "assume property (@@ a && !b)",
        ),
        "prove",
        _PASS,
    ),
    "not-fails-when-vacuous": (
        _directives(
            "assert property (@@ not (a |-> b))",
            "assume property (@@ !a)",
        ),
        "bmc",
        _FAIL,
    ),
    "or-proves": (
        _directives(
            "assert property (@@ (a |-> b) or (c |-> d))",
            "assume property (@@ a |-> b)",
        ),
        "prove",
        _PASS,
    ),
    "or-fails-unassumed": (
        _directives("assert property (@@ (a |-> b) or (c |-> d))"),
        "bmc",
        _FAIL,
    ),
    "if-else-proves": (
        _directives(
            "assert property (@@ if (e) (a |-> b) else (c |-> d))",
            "assume property (@@ e && a |-> b)",
            "assume property (@@ !e && c |-> d)",
        ),
        "prove",
        _PASS,
    ),
    "if-else-fails-with-one-branch-assumed": (
        _directives(
            "assert property (@@ if (e) (a |-> b) else (c |-> d))",
            "assume property (@@ e && a |-> b)",
        ),
        "bmc",
        _FAIL,
    ),
    "if-else-strengths-proves": (
        _directives(
            "assert property (@@ if (e) strong(a) else weak(b))",
            "assume property (@@ e |-> a)",
            "assume property (@@ !e |-> b)",
        ),
        "prove",
        _PASS,
    ),
    "if-else-strengths-fails-unassumed": (
        _directives("assert property (@@ if (e) strong(a) else weak(b))"),
        "bmc",
        _FAIL,
    ),
    "disable-iff-proves": (
        _directives(
            "assert property (@@ disable iff (rst) a |-> (b |-> ##1 c))",
            "assume property (@@ a && b |=> rst)",
        ),
        "prove",
        _PASS,
    ),
    "no-disable-iff-fails": (
        _directives(
            "assert property (@@ a |-> (b |-> ##1 c))",
            "assume property (@@ a && b |=> rst)",
        ),
        "bmc",
        _FAIL,
    ),
    "per-attempt-assumption-proves": (
        _directives(
            "assume property (@@ a |-> (b |-> c))",
            "assert property (@@ a && b |-> c)",
        ),
        "prove",
        _PASS,
    ),
    "per-attempt-assumption-needed": (
        _directives("assert property (@@ a && b |-> c)"),
        "bmc",
        _FAIL,
    ),
    "cover-and-reached": (
        _directives("cover property (@@ (a |-> b) and (c |-> d))"),
        "cover",
        _PASS,
    ),
    "cover-and-vacuous-not-reached": (
        _directives(
            "cover property (@@ (a |-> b) and (c |-> d))",
            "assume property (@@ !a && !c)",
        ),
        "cover",
        _FAIL,
    ),
    "cover-range-reached": (
        _directives("cover property (@@ a ##[1:2] b |-> c)"),
        "cover",
        _PASS,
    ),
    "cover-range-vacuous-not-reached": (
        _directives(
            "cover property (@@ a ##[1:2] b |-> c)",
            "assume property (@@ !a)",
        ),
        "cover",
        _FAIL,
    ),
    "cover-range-one-consequent-fails-not-reached": (
        _directives(
            "cover property (@@ a ##[1:2] b |-> c)",
            "assume property (@@ b)",
            "assume property (@@ a ##2 b |-> !c)",
        ),
        "cover",
        _FAIL,
    ),
    "cover-range-one-consequent-fails-reached-without-it": (
        _directives(
            "cover property (@@ a ##[1:2] b |-> c)",
            "assume property (@@ b)",
        ),
        "cover",
        _PASS,
    ),
    "cover-not-reached": (
        _directives("cover property (@@ not (a |-> b))"),
        "cover",
        _PASS,
    ),
    "cover-not-vacuous-not-reached": (
        _directives(
            "cover property (@@ not (a |-> b))",
            "assume property (@@ !a)",
        ),
        "cover",
        _FAIL,
    ),
}


def _check_attempt_case(
    sby_tool: Tool, tmp_path: Path, body: str, mode: str, status: SbyStatus
) -> None:
    sources = [("x/verify/v.sv", _ATTEMPT_HEAD + body + "endmodule\n")]
    result, _ = _translate(sources, _verification(sources), "p")
    names = _write_output(result, sources, tmp_path)
    engine = "abc pdr" if mode == "prove" else "smtbmc yices"
    checked = _sby_run(sby_tool, tmp_path, names, mode, mode, engine, 10, top="p")
    assert checked.status is status, checked.log[-3000:]


@pytest.mark.parametrize(
    ("body", "mode", "status"), list(ATTEMPT_FAST_CASES.values()), ids=list(ATTEMPT_FAST_CASES)
)
def test_per_attempt_directives_under_sby(
    sby_tool: Tool, tmp_path: Path, body: str, mode: str, status: SbyStatus
) -> None:
    """10: the representative per-attempt cases."""
    _check_attempt_case(sby_tool, tmp_path, body, mode, status)


@pytest.mark.slow
@pytest.mark.parametrize(
    ("body", "mode", "status"), list(ATTEMPT_SLOW_CASES.values()), ids=list(ATTEMPT_SLOW_CASES)
)
def test_every_per_attempt_directive_under_sby(
    sby_tool: Tool, tmp_path: Path, body: str, mode: str, status: SbyStatus
) -> None:
    """10: every per-attempt case, each with its counterpart."""
    _check_attempt_case(sby_tool, tmp_path, body, mode, status)


# --- 11: temporal operators under SBY ----------------------------------------------------

# (directives, mode, expected status, expected failing step or None)
type TemporalCase = tuple[str, str, SbyStatus, int | None]

TEMPORAL_FAST_CASES: dict[str, TemporalCase] = {
    "nexttime-proves": (
        _directives(
            "assert property (@@ a |-> nexttime [2] b)",
            "assume property (@@ a |-> ##2 b)",
        ),
        "prove",
        _PASS,
        None,
    ),
    "nexttime-fails-unassumed-at-step-2": (
        _directives("assert property (@@ a |-> nexttime [2] b)"),
        "bmc",
        _FAIL,
        2,
    ),
}

TEMPORAL_SLOW_CASES: dict[str, TemporalCase] = {
    "nexttime-zero-fails-at-step-0": (
        _directives("assert property (@@ nexttime [0] a)"),
        "bmc",
        _FAIL,
        0,
    ),
    "nexttime-zero-proves-when-assumed": (
        _directives("assert property (@@ nexttime [0] a)", "assume property (@@ a)"),
        "prove",
        _PASS,
        None,
    ),
    "always-proves": (
        _directives(
            "assert property (@@ a |-> always [1:2] b)",
            "assume property (@@ a |-> ##1 b ##1 b)",
        ),
        "prove",
        _PASS,
        None,
    ),
    "always-fails-with-only-the-first-offset-assumed": (
        _directives(
            "assert property (@@ a |-> always [1:2] b)",
            "assume property (@@ a |-> ##1 b)",
        ),
        "bmc",
        _FAIL,
        None,
    ),
    "s-eventually-proves": (
        _directives(
            "assert property (@@ a |-> s_eventually [1:2] b)",
            "assume property (@@ a |-> ##[1:2] b)",
        ),
        "prove",
        _PASS,
        None,
    ),
    "s-eventually-fails-unassumed-when-the-window-closes": (
        _directives("assert property (@@ a |-> s_eventually [1:2] b)"),
        "bmc",
        _FAIL,
        2,
    ),
    "eventually-of-implication-proves": (
        _directives(
            "assert property (@@ eventually [0:1] (c |-> d))",
            "assume property (@@ c |-> d)",
        ),
        "prove",
        _PASS,
        None,
    ),
    "eventually-of-implication-fails-on-two-failing-samples": (
        _directives(
            "assert property (@@ eventually [0:1] (c |-> d))",
            "assume property (@@ c && !d)",
        ),
        "bmc",
        _FAIL,
        None,
    ),
    "cover-always-reached": (
        _directives("cover property (@@ always [1:2] b)"),
        "cover",
        _PASS,
        None,
    ),
    "cover-always-not-reached-when-b-is-low": (
        _directives("cover property (@@ always [1:2] b)", "assume property (@@ !b)"),
        "cover",
        _FAIL,
        None,
    ),
    "cover-eventually-reached": (
        _directives("cover property (@@ eventually [0:2] (a ##1 b))"),
        "cover",
        _PASS,
        None,
    ),
    "cover-eventually-not-reached-when-b-is-low": (
        _directives("cover property (@@ eventually [0:2] (a ##1 b))", "assume property (@@ !b)"),
        "cover",
        _FAIL,
        None,
    ),
}


def _check_temporal_case(sby_tool: Tool, tmp_path: Path, case: TemporalCase) -> None:
    body, mode, status, step = case
    sources = [("x/verify/v.sv", _ATTEMPT_HEAD + body + "endmodule\n")]
    result, _ = _translate(sources, _verification(sources), "p")
    names = _write_output(result, sources, tmp_path)
    engine = "abc pdr" if mode == "prove" else "smtbmc yices"
    checked = _sby_run(sby_tool, tmp_path, names, mode, mode, engine, 10, top="p")
    assert checked.status is status, checked.log[-3000:]
    if step is not None:
        # BMC stops at the first failing step; SBY's summary omits the step number at step 0
        checked_steps = [
            int(n) for n in re.findall(r"Checking assertions in step (\d+)", checked.log)
        ]
        assert checked_steps[-1] == step, checked.log[-3000:]


# --- 12: unbounded antecedents and unbounded always under SBY ------------------

UNBOUNDED_FAST_CASES: dict[str, TemporalCase] = {
    "unbounded-delay-antecedent-proves": (
        _directives("assert property (@@ a ##[1:$] b |-> c)", "assume property (@@ b |-> c)"),
        "prove",
        _PASS,
        None,
    ),
    "unbounded-delay-antecedent-fails-unassumed": (
        _directives("assert property (@@ a ##[1:$] b |-> c)"),
        "bmc",
        _FAIL,
        1,
    ),
    "always-proves": (
        _directives("assert property (@@ always a)", "assume property (@@ a)"),
        "prove",
        _PASS,
        None,
    ),
    "always-fails-unassumed": (
        _directives("assert property (@@ always a)"),
        "bmc",
        _FAIL,
        0,
    ),
    "implication-into-always-fails-unassumed": (
        _directives("assert property (@@ a |=> always b)"),
        "bmc",
        _FAIL,
        1,
    ),
}

UNBOUNDED_SLOW_CASES: dict[str, TemporalCase] = {
    "repetition-antecedent-proves": (
        _directives(
            "assert property (@@ a[*2:$] ##1 b |=> c)",
            "assume property (@@ a ##1 a ##1 b |=> c)",
        ),
        "prove",
        _PASS,
        None,
    ),
    "repetition-antecedent-fails-without-the-assumption": (
        _directives(
            "assert property (@@ a[*2:$] ##1 b |=> c)",
            "assume property (@@ a ##1 b |-> c)",
        ),
        "bmc",
        _FAIL,
        3,
    ),
    "goto-antecedent-proves": (
        _directives("assert property (@@ a ##1 b[->2] |-> c)", "assume property (@@ b |-> c)"),
        "prove",
        _PASS,
        None,
    ),
    "goto-antecedent-fails-unassumed": (
        _directives("assert property (@@ a ##1 b[->2] |-> c)"),
        "bmc",
        _FAIL,
        2,
    ),
    "nonconsecutive-antecedent-proves": (
        _directives("assert property (@@ a ##1 b[=2] ##1 c |-> d)", "assume property (@@ c |-> d)"),
        "prove",
        _PASS,
        None,
    ),
    "nonconsecutive-antecedent-fails-unassumed": (
        _directives("assert property (@@ a ##1 b[=2] ##1 c |-> d)"),
        "bmc",
        _FAIL,
        3,
    ),
    "always-of-an-implication-proves": (
        _directives(
            "assert property (@@ always [1:$] (a |-> ##1 b))",
            "assume property (@@ a |-> ##1 b)",
        ),
        "prove",
        _PASS,
        None,
    ),
    "always-of-an-implication-fails-unassumed": (
        _directives("assert property (@@ always [1:$] (a |-> ##1 b))"),
        "bmc",
        _FAIL,
        2,
    ),
    "implication-into-always-proves": (
        _directives("assert property (@@ a |-> always b)", "assume property (@@ b)"),
        "prove",
        _PASS,
        None,
    ),
    "disable-iff-masks-an-unbounded-antecedent": (
        _directives(
            "assert property (@@ disable iff (rst) a ##[1:$] b |-> c)",
            "assume property (@@ rst)",
        ),
        "prove",
        _PASS,
        None,
    ),
    "disable-iff-low-does-not-mask-an-unbounded-antecedent": (
        _directives(
            "assert property (@@ disable iff (rst) a ##[1:$] b |-> c)",
            "assume property (@@ !rst)",
        ),
        "bmc",
        _FAIL,
        1,
    ),
    "assumed-unbounded-antecedent-constrains": (
        _directives("assume property (@@ a ##[1:$] b |-> c)", "assert property (@@ a ##1 b |-> c)"),
        "prove",
        _PASS,
        None,
    ),
    "assumed-always-constrains": (
        _directives("assume property (@@ always a)", "assert property (@@ a)"),
        "prove",
        _PASS,
        None,
    ),
}


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(value, marks=() if index < 2 else pytest.mark.extended)
        for index, value in enumerate(list(UNBOUNDED_FAST_CASES.values()))
    ],
    ids=list(UNBOUNDED_FAST_CASES),
)
def test_unbounded_directives_under_sby(sby_tool: Tool, tmp_path: Path, case: TemporalCase) -> None:
    """12: the representative cases."""
    _check_temporal_case(sby_tool, tmp_path, case)


@pytest.mark.slow
@pytest.mark.parametrize(
    "case", list(UNBOUNDED_SLOW_CASES.values()), ids=list(UNBOUNDED_SLOW_CASES)
)
def test_every_unbounded_directive_case_under_sby(
    sby_tool: Tool, tmp_path: Path, case: TemporalCase
) -> None:
    """12: every case, each with its counterpart."""
    _check_temporal_case(sby_tool, tmp_path, case)


@pytest.mark.parametrize("strategy", [Strategy.AUTO, Strategy.WINDOW, Strategy.HISTORY])
def test_an_unbounded_antecedent_gets_the_direct_form_under_every_strategy(
    strategy: Strategy,
) -> None:
    """12: the optimised forms do not apply; the monitor is the direct one, and no
    equivalence job is made."""
    sources = [
        (
            "x/verify/v.sv",
            _ATTEMPT_HEAD
            + _directives("assert property (@@ a ##[1:$] b |-> ##[1:8] c)")
            + "endmodule\n",
        )
    ]
    result, _ = _translate(
        sources, _verification(sources), "p", strategy=strategy, equivalence=True
    )
    assert result.forms
    assert all(form is Form.DIRECT for form in result.forms.values())
    assert result.equivalence == {}


# --- 13: weak unbounded consequents under SBY -------------------------------------

CONFIGURATION_FAST_CASES: dict[str, TemporalCase] = {
    "weak-unbounded-consequent-cannot-fail": (
        _directives("assert property (@@ a |-> ##[1:$] b)"),
        "prove",
        _PASS,
        None,
    ),
    "loop-consequent-fails-unassumed": (
        _directives("assert property (@@ a |-> b[*1:$] ##1 c)"),
        "bmc",
        _FAIL,
        0,
    ),
    "loop-consequent-proves-under-an-assumption": (
        _directives(
            "assert property (@@ a |-> b[*1:$] ##1 c)", "assume property (@@ a |-> b ##1 c)"
        ),
        "prove",
        _PASS,
        None,
    ),
    # a constant-false condition is evaluated at its own sample
    "constant-false-unbounded-consequent-cannot-fail": (
        _directives("assert property (@@ a |-> ##[1:$] 1'b0)"),
        "prove",
        _PASS,
        None,
    ),
    "constant-false-one-sample-ahead-fails-at-step-1": (
        _directives("assert property (@@ a |-> ##1 1'b0)"),
        "bmc",
        _FAIL,
        1,
    ),
}

CONFIGURATION_SLOW_CASES: dict[str, TemporalCase] = {
    "goto-consequent-fails-unassumed": (
        _directives("assert property (@@ a |-> b[->2] ##1 c)"),
        "bmc",
        _FAIL,
        2,
    ),
    "goto-consequent-proves-under-an-assumption": (
        _directives("assert property (@@ a |-> b[->2] ##1 c)", "assume property (@@ !a)"),
        "prove",
        _PASS,
        None,
    ),
    "always-of-a-loop-consequent-fails-unassumed": (
        _directives("assert property (@@ always (a |-> b[*1:$] ##1 c))"),
        "bmc",
        _FAIL,
        0,
    ),
    "always-of-a-loop-consequent-proves-under-an-assumption": (
        _directives(
            "assert property (@@ always (a |-> b[*1:$] ##1 c))",
            "assume property (@@ a |-> b ##1 c)",
        ),
        "prove",
        _PASS,
        None,
    ),
    "disable-iff-masks-a-loop-consequent": (
        _directives(
            "assert property (@@ disable iff (rst) a |-> b[*1:$] ##1 c)",
            "assume property (@@ rst)",
        ),
        "prove",
        _PASS,
        None,
    ),
    "disable-iff-low-does-not-mask-a-loop-consequent": (
        _directives(
            "assert property (@@ disable iff (rst) a |-> b[*1:$] ##1 c)",
            "assume property (@@ !rst)",
        ),
        "bmc",
        _FAIL,
        0,
    ),
    "assumed-loop-consequent-constrains": (
        _directives("assume property (@@ a |-> b[*1:$] ##1 c)", "assert property (@@ a |-> b)"),
        "prove",
        _PASS,
        None,
    ),
    "nonoverlapping-loop-consequent-fails-unassumed": (
        _directives("assert property (@@ a |=> b[*2:$])"),
        "bmc",
        _FAIL,
        1,
    ),
    "disable-iff-masks-a-constant-false-consequent": (
        _directives(
            "assert property (@@ disable iff (rst) a |-> ##1 1'b0)", "assume property (@@ rst)"
        ),
        "prove",
        _PASS,
        None,
    ),
    "disable-iff-low-does-not-mask-a-constant-false-consequent": (
        _directives(
            "assert property (@@ disable iff (rst) a |-> ##1 1'b0)", "assume property (@@ !rst)"
        ),
        "bmc",
        _FAIL,
        1,
    ),
    "constant-false-consequent-proves-when-never-triggered": (
        _directives("assert property (@@ a |-> ##1 1'b0)", "assume property (@@ !a)"),
        "prove",
        _PASS,
        None,
    ),
}


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(value, marks=() if index < 2 else pytest.mark.extended)
        for index, value in enumerate(list(CONFIGURATION_FAST_CASES.values()))
    ],
    ids=list(CONFIGURATION_FAST_CASES),
)
def test_weak_unbounded_consequents_under_sby(
    sby_tool: Tool, tmp_path: Path, case: TemporalCase
) -> None:
    """13: the representative cases."""
    _check_temporal_case(sby_tool, tmp_path, case)


@pytest.mark.slow
@pytest.mark.parametrize(
    "case", list(CONFIGURATION_SLOW_CASES.values()), ids=list(CONFIGURATION_SLOW_CASES)
)
def test_every_weak_unbounded_consequent_case_under_sby(
    sby_tool: Tool, tmp_path: Path, case: TemporalCase
) -> None:
    """13: every case, each with its counterpart."""
    _check_temporal_case(sby_tool, tmp_path, case)


@pytest.mark.parametrize("strategy", [Strategy.AUTO, Strategy.WINDOW, Strategy.HISTORY])
def test_a_weak_unbounded_consequent_gets_the_direct_form_under_every_strategy(
    strategy: Strategy,
) -> None:
    """13: the optimised forms do not apply, and no equivalence job is made."""
    sources = [
        (
            "x/verify/v.sv",
            _ATTEMPT_HEAD + _directives("assert property (@@ a |-> b[*1:$] ##1 c)") + "endmodule\n",
        )
    ]
    result, _ = _translate(
        sources, _verification(sources), "p", strategy=strategy, equivalence=True
    )
    assert result.forms
    assert all(form is Form.DIRECT for form in result.forms.values())
    assert result.equivalence == {}


@pytest.mark.parametrize("case", list(TEMPORAL_FAST_CASES.values()), ids=list(TEMPORAL_FAST_CASES))
def test_temporal_operators_under_sby(sby_tool: Tool, tmp_path: Path, case: TemporalCase) -> None:
    """11: the representative temporal-operator cases."""
    _check_temporal_case(sby_tool, tmp_path, case)


@pytest.mark.slow
@pytest.mark.parametrize("case", list(TEMPORAL_SLOW_CASES.values()), ids=list(TEMPORAL_SLOW_CASES))
def test_every_temporal_operator_case_under_sby(
    sby_tool: Tool, tmp_path: Path, case: TemporalCase
) -> None:
    """11: every temporal-operator case, each with its counterpart."""
    _check_temporal_case(sby_tool, tmp_path, case)


# --- 14: weak until and until_with under SBY ------------------------------------

UNTIL_FAST_CASES: dict[str, TemporalCase] = {
    "until-fails-unassumed": (
        _directives("assert property (@@ a |-> b until c)"),
        "bmc",
        _FAIL,
        0,
    ),
    "until-proves-while-p-holds": (
        _directives("assert property (@@ a |-> b until c)", "assume property (@@ b)"),
        "prove",
        _PASS,
        None,
    ),
    "until-proves-when-q-holds-without-p": (
        _directives("assert property (@@ a |-> b until c)", "assume property (@@ c)"),
        "prove",
        _PASS,
        None,
    ),
    "until-with-fails-when-q-holds-without-p": (
        _directives("assert property (@@ a |-> b until_with c)", "assume property (@@ c)"),
        "bmc",
        _FAIL,
        0,
    ),
    "until-with-proves-while-p-holds": (
        _directives("assert property (@@ a |-> b until_with c)", "assume property (@@ b)"),
        "prove",
        _PASS,
        None,
    ),
    "until-false-q-cannot-fail-while-p-holds": (
        _directives("assert property (@@ b until 1'b0)", "assume property (@@ b)"),
        "prove",
        _PASS,
        None,
    ),
}

UNTIL_SLOW_CASES: dict[str, TemporalCase] = {
    "until-false-q-fails-when-p-drops": (
        _directives("assert property (@@ b until 1'b0)"),
        "bmc",
        _FAIL,
        0,
    ),
    "top-level-until-fails-unassumed": (
        _directives("assert property (@@ b until c)"),
        "bmc",
        _FAIL,
        0,
    ),
    "nonoverlapping-until-fails-unassumed": (
        _directives("assert property (@@ a |=> b until c)"),
        "bmc",
        _FAIL,
        1,
    ),
    "nonoverlapping-until-proves-while-p-holds": (
        _directives("assert property (@@ a |=> b until c)", "assume property (@@ b)"),
        "prove",
        _PASS,
        None,
    ),
    "always-until-fails-unassumed": (
        _directives("assert property (@@ always (a |-> b until c))"),
        "bmc",
        _FAIL,
        0,
    ),
    "always-until-proves-while-p-holds": (
        _directives("assert property (@@ always (a |-> b until c))", "assume property (@@ b)"),
        "prove",
        _PASS,
        None,
    ),
    "disable-iff-masks-until": (
        _directives(
            "assert property (@@ disable iff (rst) a |-> b until c)", "assume property (@@ rst)"
        ),
        "prove",
        _PASS,
        None,
    ),
    "disable-iff-low-does-not-mask-until": (
        _directives(
            "assert property (@@ disable iff (rst) a |-> b until c)", "assume property (@@ !rst)"
        ),
        "bmc",
        _FAIL,
        0,
    ),
    "assumed-until-constrains": (
        _directives("assume property (@@ a |-> b until c)", "assert property (@@ a |-> b || c)"),
        "prove",
        _PASS,
        None,
    ),
    "assumed-until-with-constrains": (
        _directives("assume property (@@ a |-> b until_with c)", "assert property (@@ a |-> b)"),
        "prove",
        _PASS,
        None,
    ),
    "past-in-p-does-not-hold-at-the-first-sample": (
        _directives("assert property (@@ a |-> $past(b) until c)", "assume property (@@ b)"),
        "bmc",
        _FAIL,
        0,
    ),
    "past-in-p-holds-after-the-first-sample": (
        _directives("assert property (@@ !a ##1 a |-> $past(b) until c)", "assume property (@@ b)"),
        "prove",
        _PASS,
        None,
    ),
}


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(value, marks=() if index in (0, 1, 3) else pytest.mark.extended)
        for index, value in enumerate(list(UNTIL_FAST_CASES.values()))
    ],
    ids=list(UNTIL_FAST_CASES),
)
def test_weak_until_under_sby(sby_tool: Tool, tmp_path: Path, case: TemporalCase) -> None:
    """14: the representative cases."""
    _check_temporal_case(sby_tool, tmp_path, case)


@pytest.mark.slow
@pytest.mark.parametrize("case", list(UNTIL_SLOW_CASES.values()), ids=list(UNTIL_SLOW_CASES))
def test_every_weak_until_case_under_sby(
    sby_tool: Tool, tmp_path: Path, case: TemporalCase
) -> None:
    """14: every case, each with its counterpart."""
    _check_temporal_case(sby_tool, tmp_path, case)


@pytest.mark.parametrize("strategy", [Strategy.AUTO, Strategy.WINDOW, Strategy.HISTORY])
def test_a_weak_until_gets_the_direct_form_under_every_strategy(strategy: Strategy) -> None:
    """14: the optimised forms do not apply, and no equivalence job is made."""
    sources = [
        (
            "x/verify/v.sv",
            _ATTEMPT_HEAD + _directives("assert property (@@ a |-> b until c)") + "endmodule\n",
        )
    ]
    result, _ = _translate(
        sources, _verification(sources), "p", strategy=strategy, equivalence=True
    )
    assert result.forms
    assert all(form is Form.DIRECT for form in result.forms.values())
    assert result.equivalence == {}


@pytest.mark.parametrize(
    "directive",
    [
        "cover property (@@ b until c)",
        # (strong or negated until in an assume is a fairness assumption
        # when it has one configuration; the ones needing a witness stay errors)
        "assume property (@@ not (b ##[1:$] c))",
        "assume property (@@ a |-> strong(##[1:$] c))",
    ],
)
def test_covered_until_and_assumes_needing_a_witness_are_errors(directive: str) -> None:
    """14, 18."""
    sources = [("x/verify/v.sv", _ATTEMPT_HEAD + _directives(directive) + "endmodule\n")]
    with pytest.raises(RewriteError, match=r"until|\(liveness\)"):
        _translate(sources, _verification(sources), "p")


@pytest.mark.parametrize(
    "directive",
    [
        "assume property (@@ a |-> b s_until c)",
        "assume property (@@ not (b until c))",
        "assume property (@@ a |-> b s_until_with c)",
    ],
)
def test_strong_and_negated_until_assumes_are_accepted(directive: str) -> None:
    """14, 18: the one-configuration class."""
    sources = [("x/verify/v.sv", _ATTEMPT_HEAD + _directives(directive) + "endmodule\n")]
    result, _ = _translate(sources, _verification(sources), "p")
    assert result.live is True


# --- strong liveness: 16 --------------------------------------------------------

_LIVE_SBY = """[tasks]
prove
live

[options]
prove: mode prove
live: mode live

[engines]
prove: abc pdr
live: aiger suprove

[script]
{prove_reads}
{live_reads}
prep -top p

[files]
{files}
"""

LIVE_CASES = {
    # (directives, status of the prove task, status of the live task): hand-derived
    "answered-always": (
        _directives(
            "assert property (@@ a |-> strong(##[1:$] b))",
            "assume property (@@ a |-> ##1 b)",
        ),
        _PASS,
        _PASS,
    ),
    "never-answered": (
        _directives("assert property (@@ a |-> strong(##[1:$] b))", "assume property (@@ !b)"),
        _PASS,
        _FAIL,
    ),
    "pending-bit-never-answered": (
        _directives("assert property (@@ a |-> s_eventually b)", "assume property (@@ !b)"),
        _PASS,
        _FAIL,
    ),
    "pending-bit-always-answered": (
        _directives("assert property (@@ a |-> s_eventually b)", "assume property (@@ b)"),
        _PASS,
        _PASS,
    ),
    # not weak(s) is strong; a match fails the prove task, an obligation pending
    # for ever the live task (L1 for a single configuration, L2 otherwise)
    "not-until-matches": (
        _directives("assert property (@@ not (a until b))", "assume property (@@ a && b)"),
        _FAIL,
        _PASS,
    ),
    "not-until-pending-for-ever": (
        _directives("assert property (@@ not (a until b))", "assume property (@@ a && !b)"),
        _PASS,
        _FAIL,
    ),
    "not-until-dies": (
        _directives("assert property (@@ not (a until b))", "assume property (@@ !a && !b)"),
        _PASS,
        _PASS,
    ),
    "not-delay-matches": (
        _directives("assert property (@@ not (a ##[1:$] b))", "assume property (@@ a && b)"),
        _FAIL,
        _PASS,
    ),
    "not-delay-pending-for-ever": (
        _directives("assert property (@@ not (a ##[1:$] b))", "assume property (@@ a && !b)"),
        _PASS,
        _FAIL,
    ),
    "not-delay-dies": (
        _directives("assert property (@@ not (a ##[1:$] b))", "assume property (@@ !a)"),
        _PASS,
        _PASS,
    ),
    # a strong assume (one configuration) is a fairness assumption in the live task
    "fair-assume-answers-the-assert": (
        _directives(
            "assert property (@@ a |-> s_eventually b)",
            "assume property (@@ a |-> s_eventually b)",
        ),
        _PASS,
        _PASS,
    ),
    "fair-assume-does-not-answer-a-stronger-assert": (
        _directives(
            "assert property (@@ a |-> s_eventually (a && b))",
            "assume property (@@ a |-> s_eventually b)",
        ),
        _PASS,
        _FAIL,
    ),
    "not-until-assume-makes-a-drop-of-a-eventual": (
        _directives(
            "assert property (@@ s_eventually (!a || b))",
            "assume property (@@ not (a until b))",
        ),
        _PASS,
        _PASS,
    ),
    "the-same-assert-without-the-assume-fails": (
        _directives("assert property (@@ s_eventually (!a || b))"),
        _PASS,
        _FAIL,
    ),
}

# not strong(s) is weak (safety only; no live task): the prove status, hand-derived
NOT_STRONG_CASES = {
    "pending-for-ever-is-true": (
        _directives("assert property (@@ not strong(a ##[1:$] b))", "assume property (@@ a && !b)"),
        _PASS,
    ),
    "match-fails": (
        _directives("assert property (@@ not strong(a ##[1:$] b))", "assume property (@@ a && b)"),
        _FAIL,
    ),
    "s-until-pending-for-ever-is-true": (
        _directives("assert property (@@ not (a s_until b))", "assume property (@@ a && !b)"),
        _PASS,
    ),
    "not-s-eventually-is-always-not": (
        _directives("assert property (@@ not s_eventually b)", "assume property (@@ !b)"),
        _PASS,
    ),
    "not-s-eventually-fails-at-b": (
        _directives("assert property (@@ not s_eventually b)", "assume property (@@ b)"),
        _FAIL,
    ),
    "an-assumed-not-strong-constrains": (
        _directives(
            "assume property (@@ not strong(a ##[1:$] b))", "assert property (@@ a |=> !b)"
        ),
        _PASS,
    ),
}


def _reads(task: str, names: list[str], flags: str) -> str:
    return "\n".join(f"{task}: read_verilog -formal -sv {flags}{name}" for name in names)


def test_rewrite_live_is_set_only_for_a_liveness_part() -> None:
    """16."""
    head = _ATTEMPT_HEAD
    strong = [
        (
            "x/verify/v.sv",
            head + _directives("assert property (@@ a |-> s_eventually b)") + "endmodule\n",
        )
    ]
    weak = [
        (
            "x/verify/v.sv",
            head + _directives("assert property (@@ a |-> ##[1:$] b)") + "endmodule\n",
        )
    ]
    witness = [
        (
            "x/verify/v.sv",
            head + _directives("assert property (@@ a |-> strong(##[1:$] b))") + "endmodule\n",
        )
    ]
    negated_weak = [
        (
            "x/verify/v.sv",
            head + _directives("assert property (@@ not (a until b))") + "endmodule\n",
        )
    ]
    negated_witness = [
        (
            "x/verify/v.sv",
            head + _directives("assert property (@@ not (a ##[1:$] b))") + "endmodule\n",
        )
    ]
    negated_strong = [
        (
            "x/verify/v.sv",
            head + _directives("assert property (@@ not strong(a ##[1:$] b))") + "endmodule\n",
        )
    ]
    # (a strong assume of the one-configuration class has a liveness part, the
    # fairness constraint; a weak one or `not strong(s)` has none)
    fair = [
        (
            "x/verify/v.sv",
            head + _directives("assume property (@@ a |-> s_eventually b)") + "endmodule\n",
        )
    ]
    fair_negated = [
        (
            "x/verify/v.sv",
            head + _directives("assume property (@@ not (a until b))") + "endmodule\n",
        )
    ]
    weak_assume = [
        (
            "x/verify/v.sv",
            head + _directives("assume property (@@ not strong(a ##[1:$] b))") + "endmodule\n",
        )
    ]
    # (not weak(s) is strong, not strong(s) is weak)
    cases = (
        (fair, True),
        (fair_negated, True),
        (weak_assume, False),
        (strong, True),
        (witness, True),
        (weak, False),
        (negated_weak, True),
        (negated_witness, True),
        (negated_strong, False),
    )
    for sources, live in cases:
        result, _ = _translate(sources, _verification(sources), "p")
        assert result.live is live
        assert ("SVA2YOSYS_LIVE" in result.monitors) is live


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(value, marks=() if index < 2 else pytest.mark.extended)
        for index, value in enumerate(list(NOT_STRONG_CASES))
    ],
    ids=list(NOT_STRONG_CASES),
)
def test_not_of_a_strong_sequence_under_the_prove_task(
    sby_tool: Tool, tmp_path: Path, case: str
) -> None:
    """16: ``not strong(s)`` has no liveness part, so the ordinary read script
    decides it alone."""
    directives, prove = NOT_STRONG_CASES[case]
    sources = [("x/verify/v.sv", _ATTEMPT_HEAD + directives + "endmodule\n")]
    result, _ = _translate(sources, _verification(sources), "p")
    assert result.live is False
    names = _write_output(result, sources, tmp_path)
    sby_file = tmp_path / "live.sby"
    sby_file.write_text(
        _LIVE_SBY.format(
            prove_reads=_reads("prove", names, ""),
            live_reads=_reads("live", names, "-D SVA2YOSYS_LIVE "),
            files="\n".join(names),
        )
    )
    proved = run_sby(sby_tool, sby_file, "prove", tmp_path / "work_prove")
    assert proved.status is prove, proved.log[-3000:]


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(value, marks=() if index < 4 else pytest.mark.extended)
        for index, value in enumerate(list(LIVE_CASES))
    ],
    ids=list(LIVE_CASES),
)
def test_strong_directives_under_the_prove_and_live_tasks(
    sby_tool: Tool, sby_live_tool: Tool, tmp_path: Path, case: str
) -> None:
    """16: the ordinary read script gives the safety verdict, the same script with
    ``-D SVA2YOSYS_LIVE`` the liveness verdict."""
    directives, prove, live = LIVE_CASES[case]
    sources = [("x/verify/v.sv", _ATTEMPT_HEAD + directives + "endmodule\n")]
    result, _ = _translate(sources, _verification(sources), "p")
    names = _write_output(result, sources, tmp_path)
    sby_file = tmp_path / "live.sby"
    sby_file.write_text(
        _LIVE_SBY.format(
            prove_reads=_reads("prove", names, ""),
            live_reads=_reads("live", names, "-D SVA2YOSYS_LIVE "),
            files="\n".join(names),
        )
    )
    proved = run_sby(sby_tool, sby_file, "prove", tmp_path / "work_prove")
    assert proved.status is prove, proved.log[-3000:]
    lived = run_sby(sby_live_tool, sby_file, "live", tmp_path / "work_live")
    assert lived.status is live, lived.log[-3000:]


# --- strong assume in the one-configuration class: 18 ---------------------------

_COVER_WARNING = (
    "this cover's result ignores the fairness assumptions (strong assume) of this run: "
    "a reached cover is provisional, see docs/usage.md"
)


def _run_warnings(*lines: str) -> tuple[Rewrite, list[tuple[int, str]]]:
    """The rewrite of module ``p`` with one directive per line (the first on line 2) and its
    warnings as (line, message)."""
    sources = [("x/verify/v.sv", _ATTEMPT_HEAD + _directives(*lines) + "endmodule\n")]
    result, _ = _translate(sources, _verification(sources), "p")
    return result, [(d.source.line if d.source else 0, d.message) for d in result.warnings]


def test_every_accepted_cover_is_warned_about_when_the_run_has_a_fairness_assumption() -> None:
    """18: ``cover`` results ignore the fairness assumptions of the run (a reached cover may be
    on a prefix no fair trace extends): every accepted cover, located at the cover, and only the
    covers."""
    result, warnings = _run_warnings(
        "assume property (@@ a |-> s_eventually b)",
        "cover property (@@ a ##1 b)",
        "assert property (@@ c |-> ##1 d)",
        "cover property (@@ a ##[1:$] b)",
        "assume property (@@ !e)",
    )
    assert result.live is True
    covers = [(line, message) for line, message in warnings if _COVER_WARNING in message]
    assert [line for line, _ in covers] == [3, 5]
    assert len(warnings) == 2  # nothing else: the other directives get no warning


def test_no_cover_warning_without_a_fairness_assumption() -> None:
    """18: only a strong assert, a weak assume or a safety-only assume in the run (none of them
    a fairness constraint): the covers get no warning."""
    for other in (
        "assert property (@@ a |-> s_eventually b)",
        "assume property (@@ a |-> ##1 b)",
        "assume property (@@ not strong(a ##[1:$] b))",
    ):
        _, warnings = _run_warnings(other, "cover property (@@ a ##1 b)")
        assert not [m for _, m in warnings if _COVER_WARNING in m], other


def test_the_cover_warning_is_independent_of_the_order_of_the_directives() -> None:
    """18: the cover precedes the fairness assumption in the source, and is warned about."""
    _, warnings = _run_warnings(
        "cover property (@@ a ##1 b)", "assume property (@@ a |-> s_eventually b)"
    )
    assert [(line, _COVER_WARNING in m) for line, m in warnings] == [(2, True)]


def test_a_weak_assume_that_cannot_fail_points_at_the_strong_assume() -> None:
    """18: the never-fails hint of a weak ``assume`` is now ``a fairness constraint needs a
    strong assume, see docs/usage.md``."""
    _, warnings = _run_warnings("assume property (@@ a |-> ##[1:$] b)")
    assert len(warnings) == 1
    assert "this assume can never fail, so it constrains nothing:" in warnings[0][1]
    assert "(a fairness constraint needs a strong assume, see docs/usage.md)" in warnings[0][1]
    assert "not supported yet" not in warnings[0][1]


def test_a_signal_the_monitor_does_not_read_is_neither_a_crash_nor_a_rejection() -> None:
    """24: ``a[*0] ##0 b`` never matches, so the monitor reads ``go`` and ``b`` but not ``a``."""
    text = "".join(
        line + chr(10)
        for line in (
            "module sva_case(input logic clk, go, a, b);",
            "  p: assert property (@(posedge clk) go |-> a[*0] ##0 b);",
            "endmodule",
        )
    )
    result, directives = _translate([("v.sv", text)], frozenset({"v.sv"}), "sva_case")
    assert len(directives) == 1
    (output,) = [f.text for f in result.files]
    assert "__sva2yosys_i0" in output
    assert ".i0(go)" in output and ".i1(b)" in output
    assert "(a)" not in output.split("/*")[0].split("(.clk(clk)")[1]


# --- one SBY task per liveness part ----------------------------------------------------

# Free inputs: "never" cannot hold (s_eventually of a contradiction), "always" holds (of a
# tautology), "needs-fairness" holds exactly under the fairness assumption of its twin.
_NEVER = "assert property (@@ s_eventually (b && !b))"
_ALWAYS = "assert property (@@ s_eventually (b || !b))"
_NEEDS_FAIRNESS = "assert property (@@ a |-> s_eventually b)"
_FAIRNESS = "assume property (@@ a |-> s_eventually b)"


def _part_task(
    sby_live_tool: Tool,
    result: Rewrite,
    sources: list[tuple[str, str]],
    directory: Path,
    monitor: str,
    *defines: str,
) -> SbyStatus:
    """The status of the ``live`` task that switches on the liveness part of ``monitor`` only."""
    directory.mkdir(parents=True, exist_ok=True)
    names = _write_output(result, sources, directory)
    flags = "".join(f"-D {define} " for define in (live_define(monitor), *defines))
    sby_file = directory / f"live_{monitor}.sby"
    sby_file.write_text(
        _LIVE_SBY.format(
            prove_reads=_reads("prove", names, ""),
            live_reads=_reads("live", names, flags),
            files="\n".join(names),
        )
    )
    return run_sby(sby_live_tool, sby_file, "live", directory / f"work_{monitor}").status


def test_rewrite_names_the_liveness_parts_and_the_fairness_assumption() -> None:
    """25: ``live_monitors`` lists, in order, the monitors of ``assert`` directives with a
    liveness part (one per part of a property ``and``); ``fair`` is set by a fairness
    ``assume`` only, which has a liveness part but is switched on by the fairness define."""

    def sources_of(*lines: str) -> list[tuple[str, str]]:
        return [("x/verify/v.sv", _ATTEMPT_HEAD + _directives(*lines) + "endmodule\n")]

    cases = [
        (("assert property (@@ a |-> ##[1:$] b)",), (), False),
        ((_ALWAYS,), ("__sva2yosys_p0",), False),
        (("assert property (@@ a |-> ##1 b)", _NEVER), ("__sva2yosys_p1",), False),
        (
            (_NEVER, "assert property (@@ a |-> ##[1:$] b)", _ALWAYS),
            ("__sva2yosys_p0", "__sva2yosys_p2"),
            False,
        ),
        ((_NEEDS_FAIRNESS, _FAIRNESS), ("__sva2yosys_p0",), True),
        ((_FAIRNESS,), (), True),
    ]
    for lines, monitors, fair in cases:
        sources = sources_of(*lines)
        result, _ = _translate(sources, _verification(sources), "p")
        assert result.live_monitors == monitors, lines
        assert result.fair is fair, lines
        assert result.live is bool(monitors or fair), lines


@pytest.mark.parametrize("never_first", [True, False], ids=["failing-first", "failing-last"])
def test_each_liveness_part_has_its_own_task_whatever_the_order(
    sby_live_tool: Tool, tmp_path: Path, never_first: bool
) -> None:
    """25: SBY's ``live`` mode decides a task by the first justice verdict (SBY issue
    378), so two liveness parts in one task can hide a failure. Switched on one at a time, the
    failing part fails and the holding one passes, in either source order."""
    lines = (_NEVER, _ALWAYS) if never_first else (_ALWAYS, _NEVER)
    sources = [("x/verify/v.sv", _ATTEMPT_HEAD + _directives(*lines) + "endmodule\n")]
    result, _ = _translate(sources, _verification(sources), "p")
    assert len(result.live_monitors) == 2
    never, always = result.live_monitors if never_first else result.live_monitors[::-1]
    assert _part_task(sby_live_tool, result, sources, tmp_path / "never", never) is SbyStatus.FAIL
    assert _part_task(sby_live_tool, result, sources, tmp_path / "always", always) is SbyStatus.PASS


def test_the_fairness_define_keeps_the_assumption_on_in_a_part_task(
    sby_live_tool: Tool, tmp_path: Path
) -> None:
    """25: ``a |-> s_eventually b`` holds only under the fairness assumption beside it,
    which applies to every run. With ``SVA2YOSYS_LIVE_FAIR`` the part's task passes; without
    it the same task fails (a free ``b`` may stay low), and the other part still fails."""
    sources = [
        (
            "x/verify/v.sv",
            _ATTEMPT_HEAD + _directives(_NEEDS_FAIRNESS, _NEVER, _FAIRNESS) + "endmodule\n",
        )
    ]
    result, _ = _translate(sources, _verification(sources), "p")
    needs, never = result.live_monitors
    assert result.fair
    fair = "SVA2YOSYS_LIVE_FAIR"
    assert (
        _part_task(sby_live_tool, result, sources, tmp_path / "fair", needs, fair) is SbyStatus.PASS
    )
    assert _part_task(sby_live_tool, result, sources, tmp_path / "bare", needs) is SbyStatus.FAIL
    assert (
        _part_task(sby_live_tool, result, sources, tmp_path / "never", never, fair)
        is SbyStatus.FAIL
    )
