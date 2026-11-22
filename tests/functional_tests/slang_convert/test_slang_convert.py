"""Functional contract of the Slang export → IR conversion (see CONTRACT.md)."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from sva2yosys.codegen.systemverilog import monitor_module
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import (
    BIT,
    Binary,
    BinaryOp,
    BitSelect,
    Const,
    FirstSample,
    RangeSelect,
    Sampled,
    SampledDefault,
    SampledFunction,
    Signal,
    Unary,
    UnaryOp,
    ValueType,
)
from sva2yosys.ir.property import (
    Always,
    Eventually,
    Implication,
    Nexttime,
    Not,
    PropAnd,
    Property,
    PropIf,
    PropOr,
    SequenceProperty,
    Strength,
    Until,
)
from sva2yosys.ir.sequence import (
    TRUE,
    Bool,
    Delay,
    Goto,
    NonConsecutive,
    Repeat,
    SeqAnd,
    SeqConcat,
    SeqOr,
    Sequence,
)
from sva2yosys.passes.validate import validation_problems
from sva2yosys.slang.convert import Conversion, FormatError, convert
from sva2yosys.slang.export import export_design, export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.compile import compile_directive
from sva2yosys.temporal.lower import lower

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROPS = "basic_top.u_props"
DEFAULTS = "basic_top.u_defaults"


@pytest.fixture(scope="module")
def fixture_conversion() -> Conversion:
    doc = export_design([FIXTURES / "basic_properties.sv"], SlangOptions(top=("basic_top",)))
    return convert(doc)


def _directive(conversion: Conversion, scope: str, name: str) -> Directive:
    found = [d for d in conversion.directives if d.scope == scope and d.name == name]
    assert len(found) == 1, f"{scope}.{name}: {conversion.rejections}"
    return found[0]


def _sig(name: str, width: int = 1, signed: bool = False, scope: str = PROPS) -> Signal:
    return Signal(f"{scope}.{name}", ValueType(width, signed))


def _b(name: str, scope: str = PROPS) -> Bool:
    return Bool(_sig(name, scope=scope))


def _imp(antecedent: Sequence, consequent: Sequence, overlapping: bool = True) -> Implication:
    return Implication(antecedent, SequenceProperty(consequent), overlapping)


def _then(delay: tuple[int, int | None], sequence: Sequence) -> SeqConcat:
    return SeqConcat(TRUE, Delay(*delay), sequence)


def _nodes(node: object) -> Iterator[object]:
    """All IR nodes reachable from ``node`` (dataclass fields and tuples)."""
    yield node
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        for field in dataclasses.fields(node):
            yield from _nodes(getattr(node, field.name))
    elif isinstance(node, tuple):
        for item in node:
            yield from _nodes(item)


# --- 1-3: directive fields, clock, disable ----------------------------------------------


def test_nothing_rejected_in_fixture(fixture_conversion: Conversion) -> None:
    """1."""
    assert fixture_conversion.rejections == ()
    assert len(fixture_conversion.directives) == 20


def test_directive_kinds_and_order(fixture_conversion: Conversion) -> None:
    """1."""
    props = [d for d in fixture_conversion.directives if d.scope == PROPS]
    assert [d.name for d in props][:11] == [f"p{i:02d}" for i in range(1, 12)]
    kinds = {d.name: d.kind for d in props}
    assert kinds["q_assume"] is DirectiveKind.ASSUME
    assert kinds["q_cover"] is DirectiveKind.COVER
    assert kinds["p01"] is DirectiveKind.ASSERT
    source = _directive(fixture_conversion, PROPS, "p01").source
    assert (source.file, source.line, source.column) == (
        str(FIXTURES / "basic_properties.sv"),
        13,
        5,
    )


def test_explicit_clock(fixture_conversion: Conversion) -> None:
    """2."""
    clock = _directive(fixture_conversion, PROPS, "p01").clock
    assert clock == Clock(_sig("clk"), Edge.POSEDGE)


def test_default_clock_and_disable(fixture_conversion: Conversion) -> None:
    """2, 3."""
    directive = _directive(fixture_conversion, DEFAULTS, "d_named")
    assert directive.clock == Clock(_sig("clk", scope=DEFAULTS), Edge.POSEDGE)
    assert directive.disable == Unary(UnaryOp.LOGICAL_NOT, _sig("rst_n", scope=DEFAULTS), BIT)


def test_explicit_disable_overrides_default(fixture_conversion: Conversion) -> None:
    """3."""
    directive = _directive(fixture_conversion, DEFAULTS, "d_override")
    assert directive.disable == _sig("rst2", scope=DEFAULTS)
    assert directive.property == _imp(_b("req", DEFAULTS), _b("ack", DEFAULTS), overlapping=False)


def test_no_disable(fixture_conversion: Conversion) -> None:
    """3."""
    assert _directive(fixture_conversion, PROPS, "p01").disable is None


# --- 5, 6, 8: properties and sequences --------------------------------------------------

_SPEC: dict[str, Property] = {
    "p01": _imp(_b("a"), _b("b")),
    "p02": _imp(_b("a"), _b("b"), overlapping=False),
    "p03": _imp(_b("a"), _then((1, 1), _b("b"))),
    "p04": _imp(_b("a"), _then((5, 5), _b("b"))),
    "p05": _imp(_b("a"), _then((1, 5), _b("b"))),
    "p06": _imp(_b("a"), SeqConcat(_b("b"), Delay(1, 3), _b("c"))),
    "p07": _imp(_b("a"), SeqConcat(SeqConcat(_b("b"), Delay(1, 3), _b("c")), Delay(1, 1), _b("d"))),
    "p08": _imp(_b("a"), Repeat(_b("b"), 3, 3)),
    "p09": _imp(_b("a"), Repeat(_b("b"), 2, 5)),
    "p10": _imp(Bool(Sampled(SampledFunction.ROSE, _sig("a"), BIT)), _then((1, 3), _b("b"))),
    "p11": _imp(_b("a"), _then((3, 3), _b("b"))),
}


@pytest.mark.parametrize("name", sorted(_SPEC))
def test_specification_properties(fixture_conversion: Conversion, name: str) -> None:
    """4, 5, 7."""
    assert _directive(fixture_conversion, PROPS, name).property == _SPEC[name]


def test_specification_disable(fixture_conversion: Conversion) -> None:
    """3, 7."""
    disable = _directive(fixture_conversion, PROPS, "p11").disable
    assert disable == Unary(UnaryOp.LOGICAL_NOT, _sig("rst_n"), BIT)


def test_sequence_property_with_fusion_and_empty_repetition(fixture_conversion: Conversion) -> None:
    """4, 5."""
    assert _directive(fixture_conversion, PROPS, "q_seq").property == SequenceProperty(
        SeqConcat(SeqConcat(_b("a"), Delay(0, 0), _b("b")), Delay(0, 2), Repeat(_b("c"), 0, 2))
    )


def test_named_declarations(fixture_conversion: Conversion) -> None:
    """5."""
    directive = _directive(fixture_conversion, DEFAULTS, "d_named")
    assert directive.property == _imp(_b("req", DEFAULTS), _then((1, 3), _b("ack", DEFAULTS)))


def test_cover_sequence_property(fixture_conversion: Conversion) -> None:
    """5."""
    directive = _directive(fixture_conversion, PROPS, "q_cover")
    assert directive.property == SequenceProperty(SeqConcat(_b("a"), Delay(1, 2), _b("b")))


# --- 7: expressions ---------------------------------------------------------------------


def test_sampled_value_functions(fixture_conversion: Conversion) -> None:
    """7."""
    prop = _directive(fixture_conversion, PROPS, "q_sample").property
    sampled = {(n.function, n.operand) for n in _nodes(prop) if isinstance(n, Sampled)}
    assert sampled == {
        (SampledFunction.STABLE, _sig("v", 4)),
        (SampledFunction.CHANGED, _sig("a")),
        (SampledFunction.FELL, _sig("b")),
        (SampledFunction.PAST, _sig("c")),
    }


def test_selects_map_to_bit_positions(fixture_conversion: Conversion) -> None:
    """7."""
    prop = _directive(fixture_conversion, PROPS, "x_select").property
    bits = {(n.value, n.index) for n in _nodes(prop) if isinstance(n, BitSelect)}
    # `asc` is an `input bit` (2-state: its default sampled value is known)
    asc = dataclasses.replace(_sig("asc", 4), default=SampledDefault.KNOWN)
    assert bits == {(_sig("v", 4), 2), (_sig("off", 4), 1), (asc, 3)}
    parts = {(n.value, n.msb, n.lsb, n.type) for n in _nodes(prop) if isinstance(n, RangeSelect)}
    assert parts == {(_sig("v", 4), 3, 2, ValueType(2))}
    consts = {n for n in _nodes(prop) if isinstance(n, Const)}
    assert Const(2, ValueType(2)) in consts


def test_signed_operands(fixture_conversion: Conversion) -> None:
    """7."""
    prop = _directive(fixture_conversion, PROPS, "x_signed").property
    # -8'sd3 is folded by Slang's constant evaluation: the bit pattern of -3.
    eight = ValueType(8, True)
    assert Const(253, eight) in set(_nodes(prop))
    assert not any(isinstance(n, Unary) and n.op is UnaryOp.MINUS for n in _nodes(prop))
    assert _sig("s", 8, True) in set(_nodes(prop))
    ops = {n.op for n in _nodes(prop) if hasattr(n, "op")}
    assert BinaryOp.ARITHMETIC_SHIFT_RIGHT in ops
    assert BinaryOp.LESS_THAN in ops


def test_conditional_concat_replication(fixture_conversion: Conversion) -> None:
    """7."""
    prop = _directive(fixture_conversion, PROPS, "x_mix").property
    names = {type(n).__name__ for n in _nodes(prop)}
    assert {"Conditional", "Concat", "Replicate", "Binary"} <= names


# --- 9: rejections ------------------------------------------------------------------------

_TEMPLATE = """module r(input logic clk, clk2, en, a, b, c, d,
         input logic [3:0] v, input logic [1:0] i);
  function automatic logic f(logic x); return x; endfunction
  real rr = 0.0;
  sub u_sub(.x(a));
  {snippet}
endmodule
module sub(input logic x); logic y; assign y = x; endmodule
"""

_REJECTED = [
    (
        "proc_clock_differs",
        "always @(posedge clk) x: assert property (@(posedge clk2) a);",
        "differs from",
    ),
    (
        "proc_initial_delay",
        "initial begin x: assert property (@(posedge clk) a); #1; end",
        "may not fall through",
    ),
    (
        "proc_initial_loop",
        "initial for (int k = 0; k < 2; k++) x: assert property (@(posedge clk) a);",
        "loop",
    ),
    (
        "proc_initial_clock_differs",
        "initial @(posedge clk) x: assert property (@(posedge clk2) a);",
        "differs from",
    ),
    ("proc_initial_no_clock", "initial x: assert property (a);", "16.16"),
    ("proc_latch", "always_latch x: assert property (@(posedge clk) a);", "always_latch"),
    ("proc_comb", "always_comb x: assert property (@(posedge clk) a);", "always_comb"),
    (
        "proc_loop",
        "always @(posedge clk) for (int k = 0; k < 2; k++) x: assert property (a);",
        "loop",
    ),
    (
        "proc_casez",
        "always @(posedge clk) casez (v) 4'b1???: x: assert property (a); default: ; endcase",
        "casez",
    ),
    (
        "proc_blocking",
        "logic t; always @(posedge clk) t = a;\n"
        "  always @(posedge clk) if (t) x: assert property (b);",
        "blocking",
    ),
    ("proc_sampled", "always @(posedge clk) if ($rose(a)) x: assert property (b);", "sampled"),
    (
        "proc_event_list",
        "always @(posedge clk or posedge en) x: assert property (@(posedge clk) a);",
        "event",
    ),
    ("no_clock", "x: assert property (a);", "no clock"),
    ("both_edges", "x: assert property (@(edge clk) a);", "BothEdges"),
    ("wide_clock", "x: assert property (@(posedge v) a);", "1-bit"),
    (
        "system_function",
        "x: assert property (@(posedge clk) $countbits(v, 1'b1) == 0);",
        "$countbits",
    ),
    (
        "indexed_part_select",
        "x: assert property (@(posedge clk) v[1+:2] == 2'b11);",
        "indexed part",
    ),
    ("index_out_of_range", "x: assert property (@(posedge clk) v[7]);", "out of range"),
    ("cover_sequence", "x: cover sequence (@(posedge clk) a ##1 b);", "cover sequence"),
    ("negedge", "x: assert property (@(negedge clk) a);", "negedge"),
    ("iff", "x: assert property (@(posedge clk iff en) a);", "iff"),
    (
        "multiclock",
        "x: assert property (@(posedge clk) a |=> @(posedge clk2) b);",
        "multiclock SVA properties are not supported yet",
    ),
    (
        "match_items",
        "property lv; logic t; (a, t = b) ##1 (c == t); endproperty\n"
        "  x: assert property (@(posedge clk) lv);",
        "match items",
    ),
    (
        "until_sequence_operand",
        "x: assert property (@(posedge clk) (a ##1 b) until c);",
        "operands of 'until' must be Boolean",
    ),
    (
        "until_repeated_operand",
        "x: assert property (@(posedge clk) a s_until b[*2]);",
        "operands of 's_until' must be Boolean",
    ),
    (
        "until_with_property_operand",
        "x: assert property (@(posedge clk) a until_with (b |-> c));",
        "operands of 'until_with' must be Boolean",
    ),
    (
        "s_until_with_sequence_operand",
        "x: assert property (@(posedge clk) a s_until_with (b ##1 c));",
        "operands of 's_until_with' must be Boolean",
    ),
    (
        "first_match",
        "x: assert property (@(posedge clk) a |-> first_match(b ##[1:2] c));",
        "FirstMatch",
    ),
    (
        "if_else_past",
        "x: assert property (@(posedge clk) if ($past(a)) b else c);",
        "uses $past",
    ),
    ("hierarchical", "x: assert property (@(posedge clk) u_sub.y);", "hierarchical"),
    ("variable_select", "x: assert property (@(posedge clk) v[i]);", "variable"),
    ("past_gated", "x: assert property (@(posedge clk) $past(a, 2, en));", "gating"),
    (
        "disable_register",
        "logic r0 = 1'b0; always_ff @(posedge clk) r0 <= a;\n"
        "  x: assert property (@(posedge clk) disable iff (r0) a |-> b);",
        "may change at the clock edge",
    ),
    (
        "onehot0_of_past",
        "x: assert property (@(posedge clk) $onehot0($past(v)) |-> a);",
        "$onehot0 of an operand using $past",
    ),
    (
        "countones_of_past",
        "x: assert property (@(posedge clk) $countones(v ^ $past(v, 2)) == 0);",
        "$countones of an operand using $past",
    ),
    ("onehot_of_past", "x: assert property (@(posedge clk) !$onehot($past(v)));", "$onehot of"),
    ("nested_sampled", "x: assert property (@(posedge clk) $rose($past(a)));", "nested"),
    ("user_call", "x: assert property (@(posedge clk) f(a));", "Call"),
    ("power", "x: assert property (@(posedge clk) (v ** 2) > 0);", "Power"),
    ("wildcard", "x: assert property (@(posedge clk) v ==? 4'b1x0x);", "WildcardEquality"),
    ("xz_constant", "x: assert property (@(posedge clk) v == 4'b1x00);", "x/z"),
    ("non_integral", "x: assert property (@(posedge clk) rr > 0.5);", "type"),
]


@pytest.mark.parametrize(
    ("snippet", "fragment"), [(s, f) for _, s, f in _REJECTED], ids=[n for n, _, _ in _REJECTED]
)
def test_rejected(snippet: str, fragment: str) -> None:
    """9."""
    text = _TEMPLATE.format(snippet=snippet)
    conversion = convert(export_texts([("r.sv", text)], SlangOptions(top=("r",))))
    assert conversion.directives == ()
    assert len(conversion.rejections) == 1
    rejection = conversion.rejections[0]
    assert (rejection.scope, rejection.name) == ("r", "x")
    assert fragment in rejection.message
    assert rejection.source is not None
    assert rejection.source.file == "r.sv"


def test_rejection_does_not_affect_other_assertions() -> None:
    """9."""
    snippet = (
        "x: assert property (@(posedge clk) a until (b ##1 c));\n"
        "  y: assert property (@(posedge clk) a |-> b);"
    )
    conversion = convert(
        export_texts([("r.sv", _TEMPLATE.format(snippet=snippet))], SlangOptions(top=("r",)))
    )
    assert [r.name for r in conversion.rejections] == ["x"]
    assert [d.name for d in conversion.directives] == ["y"]


@pytest.mark.parametrize(
    "body",
    [
        "x: {kind} property (@(posedge clk) a);",
        "x: {kind} property (@(posedge clk) disable iff (en) a |=> b);",
        "default clocking cb @(posedge clk); endclocking\n"
        "default disable iff (en);\n"
        "property q; a |-> ##[1:2] b; endproperty\n"
        "x: {kind} property (q);",
        "always @(posedge clk) if (en) x: {kind} property (a |=> b);",
        "initial x: {kind} property (@(posedge clk) a |=> b);",
        "x: {kind} property (@(posedge clk) a |-> s_eventually b);",
    ],
    ids=["boolean", "disable", "default-and-named", "procedural", "initial", "fairness"],
)
def test_restrict_normalizes_to_the_same_assumption_with_source_provenance(body: str) -> None:
    """1: restrict has assume's formal meaning, including context and validation."""
    directives: list[Directive] = []
    for kind in ("restrict", "assume"):
        text = _TEMPLATE.format(snippet=body.format(kind=kind))
        converted = convert(export_texts([("r.sv", text)], SlangOptions(top=("r",))))
        assert converted.rejections == ()
        (directive,) = converted.directives
        assert directive.kind is DirectiveKind.ASSUME
        assert (directive.scope, directive.name) == ("r", "x")
        assert directive.source.file == "r.sv"
        assert (
            text.splitlines()[directive.source.line - 1]
            .lstrip()
            .startswith(("x:", "always", "initial"))
        )
        assert validation_problems(directive) == ()
        directives.append(directive)
    restricted, assumed = directives
    assert restricted.property == assumed.property
    assert restricted.clock == assumed.clock
    assert restricted.disable == assumed.disable
    assert restricted.source.line == assumed.source.line
    assert restricted.source.column == assumed.source.column


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("x: restrict property (a);", "no clock"),
        ("x: restrict property (@(negedge clk) a);", "negedge"),
        ("x: restrict property (@(posedge clk) first_match(a ##1 b));", "FirstMatch"),
        (
            "logic r0 = 1'b0; always_ff @(posedge clk) r0 <= a;\n"
            "x: restrict property (@(posedge clk) disable iff (r0) a);",
            "may change at the clock edge",
        ),
    ],
    ids=["no-clock", "negedge", "first-match", "register-disable"],
)
def test_restrict_retains_located_frontend_rejections(body: str, fragment: str) -> None:
    """1, 9: normalization never bypasses assume's frontend restrictions."""
    converted = convert(
        export_texts([("r.sv", _TEMPLATE.format(snippet=body))], SlangOptions(top=("r",)))
    )
    assert converted.directives == ()
    (rejection,) = converted.rejections
    assert (rejection.scope, rejection.name) == ("r", "x")
    assert fragment in rejection.message
    assert rejection.source is not None
    assert rejection.source.file == "r.sv"


# --- 4, 5, 6: unbounded constructs (converted; the machines reject them in validation) ----


def _convert_one(snippet: str) -> Property:
    text = _TEMPLATE.format(snippet=f"x: assert property (@(posedge clk) {snippet});")
    conversion = convert(export_texts([("r.sv", text)], SlangOptions(top=("r",))))
    assert conversion.rejections == (), conversion.rejections
    assert len(conversion.directives) == 1
    return conversion.directives[0].property


def _r(name: str) -> Signal:
    return _sig(name, scope="r")


def _rb(name: str) -> Bool:
    return Bool(_r(name))


def _rimp(antecedent: Sequence, consequent: Sequence) -> Implication:
    return Implication(antecedent, SequenceProperty(consequent), True)


def _seq(sequence: Sequence) -> SequenceProperty:
    return SequenceProperty(sequence)


_AND_BC = Binary(BinaryOp.LOGICAL_AND, _r("b"), _r("c"), BIT)

_CONVERTED_1C = [
    ("delay_dollar", "a |-> ##[1:$] b", _rimp(_rb("a"), _then((1, None), _rb("b")))),
    ("delay_star", "a |-> ##[*] b", _rimp(_rb("a"), _then((0, None), _rb("b")))),
    ("delay_plus", "a |-> ##[+] b", _rimp(_rb("a"), _then((1, None), _rb("b")))),
    ("delay_dollar_middle", "a ##[2:$] b", _seq(SeqConcat(_rb("a"), Delay(2, None), _rb("b")))),
    ("repeat_dollar", "a |-> b[*1:$]", _rimp(_rb("a"), Repeat(_rb("b"), 1, None))),
    ("repeat_plus", "a |-> b[+]", _rimp(_rb("a"), Repeat(_rb("b"), 1, None))),
    (
        "repeat_star_antecedent",
        "b[*0:$] ##1 c |-> a",
        _rimp(SeqConcat(Repeat(_rb("b"), 0, None), Delay(1, 1), _rb("c")), _rb("a")),
    ),
    ("goto", "a |-> b[->2]", _rimp(_rb("a"), Goto(_r("b"), 2, 2))),
    ("goto_range", "a |-> b[->1:3]", _rimp(_rb("a"), Goto(_r("b"), 1, 3))),
    ("goto_dollar", "a |-> b[->2:$]", _rimp(_rb("a"), Goto(_r("b"), 2, None))),
    ("goto_antecedent", "b[->2] |-> a", _rimp(Goto(_r("b"), 2, 2), _rb("a"))),
    ("nonconsecutive", "a |-> b[=2]", _rimp(_rb("a"), NonConsecutive(_r("b"), 2, 2))),
    (
        "nonconsecutive_range",
        "a ##1 b[=1:3]",
        _seq(SeqConcat(_rb("a"), Delay(1, 1), NonConsecutive(_r("b"), 1, 3))),
    ),
    ("nonconsecutive_dollar", "a |-> b[=1:$]", _rimp(_rb("a"), NonConsecutive(_r("b"), 1, None))),
    ("goto_of_expression", "a |-> (b && c)[->1]", _rimp(_rb("a"), Goto(_AND_BC, 1, 1))),
    ("always", "always a", Always(0, None, False, _seq(_rb("a")))),
    ("always_from", "always [2:$] a", Always(2, None, False, _seq(_rb("a")))),
    ("always_implication", "always (a |-> b)", Always(0, None, False, _rimp(_rb("a"), _rb("b")))),
    ("always_bounded_unchanged", "always [1:3] a", Always(1, 3, False, _seq(_rb("a")))),
    (
        "nested_always",
        "a |-> nexttime always b",
        Implication(_rb("a"), Nexttime(1, False, Always(0, None, False, _seq(_rb("b")))), True),
    ),
    ("s_eventually", "s_eventually a", Eventually(0, None, True, _seq(_rb("a")))),
    ("s_eventually_from", "s_eventually [1:$] a", Eventually(1, None, True, _seq(_rb("a")))),
    (
        "s_eventually_sequence",
        "s_eventually (a ##1 b)",
        Eventually(0, None, True, _seq(SeqConcat(_rb("a"), Delay(1, 1), _rb("b")))),
    ),
    (
        "s_eventually_bounded_unchanged",
        "s_eventually [0:2] a",
        Eventually(0, 2, True, _seq(_rb("a"))),
    ),
    ("until", "a until b", Until(_r("a"), _r("b"), False, False)),
    ("s_until", "a s_until b", Until(_r("a"), _r("b"), True, False)),
    ("until_with", "a until_with b", Until(_r("a"), _r("b"), False, True)),
    ("s_until_with", "a s_until_with b", Until(_r("a"), _r("b"), True, True)),
    (
        "until_expressions",
        "(b && c) until !a",
        Until(_AND_BC, Unary(UnaryOp.LOGICAL_NOT, _r("a"), BIT), False, False),
    ),
    (
        "until_in_implication",
        "a |-> (b until c)",
        Implication(_rb("a"), Until(_r("b"), _r("c"), False, False), True),
    ),
    ("not_until", "not (a s_until b)", Not(Until(_r("a"), _r("b"), True, False))),
]


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [(s, e) for _, s, e in _CONVERTED_1C],
    ids=[n for n, _, _ in _CONVERTED_1C],
)
def test_unbounded_constructs_convert(snippet: str, expected: Property) -> None:
    """4, 5, 6."""
    assert _convert_one(snippet) == expected


@pytest.mark.parametrize(
    ("snippet", "fragment"),
    [
        ("not always a", "unbounded 'always' (no upper bound) is not supported yet"),
        ("not always [2:$] a", "unbounded 'always' (no upper bound) is not supported yet"),
        ("a or always b", "unbounded 'always' (no upper bound) is not supported yet"),
        ("a |-> nexttime always b", "unbounded 'always'"),
    ],
)
def test_unbounded_constructs_are_rejected_by_validation(snippet: str, fragment: str) -> None:
    """9: converted, then rejected by validation for the machines (reference semantics only)."""
    text = _TEMPLATE.format(snippet=f"x: assert property (@(posedge clk) {snippet});")
    conversion = convert(export_texts([("r.sv", text)], SlangOptions(top=("r",))))
    (directive,) = conversion.directives
    problems = validation_problems(directive)
    assert any(fragment in p for p in problems), problems


@pytest.mark.parametrize(
    "snippet",
    [
        "a |-> strong(##[1:$] b)",
        "a |-> strong(b[*1:$])",
        "a |-> strong(b[->2])",
        "a |-> strong(b[->2:$])",
        "a |-> strong(b[=2])",
        "s_eventually a",
        "a s_until b",
        "a s_until_with b",
    ],
)
def test_strong_unbounded_properties_pass_validation_for_assert_and_assume(
    snippet: str,
) -> None:
    """9: liveness for an ``assert``; for an ``assume`` validation reports no
    liveness problem either (the compiler rejects the ones needing a witness)."""
    for kind in ("assert", "assume"):
        text = _TEMPLATE.format(snippet=f"x: {kind} property (@(posedge clk) {snippet});")
        conversion = convert(export_texts([("r.sv", text)], SlangOptions(top=("r",))))
        (directive,) = conversion.directives
        assert validation_problems(directive) == (), kind


@pytest.mark.parametrize(
    "snippet",
    [
        "a ##[1:$] b |-> c",
        "a[*1:$] ##1 b |=> c",
        "b[->2] |-> c",
        "b[=2] ##1 a |-> c",
        "always a",
        "always [2:$] (a |-> ##1 b)",
        "a |-> always b",
        "a |=> always [1:$] b",
    ],
)
def test_unbounded_antecedents_and_always_pass_validation(snippet: str) -> None:
    """9: an unbounded antecedent into a bounded consequent, and unbounded
    ``always`` over a supported operand, are accepted for ``assert`` and ``assume``, and a
    ``cover`` of them is rejected."""
    template = "x: {kind} property (@(posedge clk) {snippet});"
    for kind in ("assert", "assume"):
        text = _TEMPLATE.format(snippet=template.format(kind=kind, snippet=snippet))
        (directive,) = convert(export_texts([("r.sv", text)], SlangOptions(top=("r",)))).directives
        assert validation_problems(directive) == ()
    text = _TEMPLATE.format(snippet=template.format(kind="cover", snippet=snippet))
    (directive,) = convert(export_texts([("r.sv", text)], SlangOptions(top=("r",)))).directives
    assert validation_problems(directive)


def _mutated(snippet: str, changes: dict[str, object]) -> Conversion:
    """The conversion of ``snippet``'s export with ``changes`` applied to its property
    operator node (Slang itself rejects these spellings before the converter)."""
    text = _TEMPLATE.format(snippet=f"x: assert property (@(posedge clk) {snippet});")
    doc = export_texts([("r.sv", text)], SlangOptions(top=("r",)))
    records = cast(list[Any], doc["assertions"])
    node = cast(dict[str, Any], records[0]["propertySpec"])
    while node["kind"] == "Clocking":
        node = node["expr"]
    node.update(changes)
    return convert(doc)


@pytest.mark.parametrize(
    ("snippet", "changes", "fragment"),
    [
        ("always [1:2] a", {"op": "SAlways", "max": "$"}, "'s_always' without an upper bound"),
        ("always a", {"op": "SAlways"}, "'s_always' without an upper bound"),
        ("s_eventually [1:2] a", {"op": "Eventually", "max": "$"}, "'eventually' without an upper"),
        ("s_eventually a", {"op": "Eventually"}, "'eventually' without an upper bound"),
        ("nexttime [1] a", {"max": "$"}, "'nexttime' takes a single count"),
    ],
)
def test_weak_eventually_and_strong_always_must_be_bounded(
    snippet: str, changes: dict[str, object], fragment: str
) -> None:
    """9: IEEE allows `$` only for `always` and `s_eventually`."""
    conversion = _mutated(snippet, changes)
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert fragment in rejection.message
    assert rejection.source is not None


# --- 10: format ----------------------------------------------------------------------------


@pytest.mark.parametrize("change", [{"format": "other"}, {"formatVersion": 99}])
def test_wrong_format(change: dict[str, object]) -> None:
    """10."""
    doc = export_texts([("r.sv", _TEMPLATE.format(snippet=""))], SlangOptions(top=("r",)))
    doc.update(change)  # type: ignore[arg-type]
    with pytest.raises(FormatError):
        convert(doc)


def test_negative_literal_is_twos_complement_pattern() -> None:
    """7: Slang prints 8'shff on a byte as -8'sd1; the bit pattern is 255."""
    text = (
        "module n(input logic clk, input byte by);\n"
        "  x: assert property (@(posedge clk) by == 8'shff);\n"
        "endmodule\n"
    )
    conversion = convert(export_texts([("n.sv", text)], SlangOptions(top=("n",))))
    consts = {n for n in _nodes(conversion.directives[0].property) if isinstance(n, Const)}
    assert consts == {Const(255, ValueType(8, True))}


def test_repetition_of_a_parenthesised_sequence() -> None:
    """6."""
    text = (
        "module w(input logic clk, a, b, c);\n"
        "  x: assert property (@(posedge clk) a |-> (b ##1 c)[*2]);\n"
        "endmodule\n"
    )
    conversion = convert(export_texts([("w.sv", text)], SlangOptions(top=("w",))))
    b, c = Bool(_sig("b", scope="w")), Bool(_sig("c", scope="w"))
    assert conversion.directives[0].property == _imp(
        Bool(_sig("a", scope="w")), Repeat(SeqConcat(b, Delay(1, 1), c), 2, 2)
    )


def test_parameter_bounds_differing_per_instance() -> None:
    """1, 5: each instance gets its own directive with its own folded bound."""
    text = (
        "module leaf #(parameter int N = 1) (input logic clk, a, b);\n"
        "  x: assert property (@(posedge clk) a |-> ##[1:N] b);\n"
        "endmodule\n"
        "module top (input logic clk, a, b);\n"
        "  leaf #(.N(2)) u2 (.*);\n"
        "  leaf #(.N(4)) u4 (.*);\n"
        "endmodule\n"
    )
    conversion = convert(export_texts([("p.sv", text)], SlangOptions(top=("top",))))
    bounds = {
        d.scope: d.property.consequent.sequence.delay
        for d in conversion.directives
        if isinstance(d.property, Implication)
        and isinstance(d.property.consequent, SequenceProperty)
        and isinstance(d.property.consequent.sequence, SeqConcat)
    }
    assert bounds == {"top.u2": Delay(1, 2), "top.u4": Delay(1, 4)}


_SHADOWED = """module s(input logic clk, rst, a);
  default clocking @(posedge clk); endclocking
  default disable iff (rst);
  for (genvar i = 0; i < 1; i++) begin : g
    logic rst;
    assign rst = 1'b0;
    x: assert property (a);
  end
  y: assert property (a);
endmodule
"""


def test_name_shadowed_at_the_assertion_is_rejected() -> None:
    """9."""
    conversion = convert(export_texts([("s.sv", _SHADOWED)], SlangOptions(top=("s",))))
    (rejection,) = conversion.rejections
    assert (rejection.scope, rejection.name) == ("s.g[0]", "x")
    assert "'rst' means something else" in rejection.message
    assert rejection.source is not None and rejection.source.line == 3
    assert [d.name for d in conversion.directives] == ["y"]


def test_past_with_a_depth() -> None:
    """7."""
    snippet = "x: assert property (@(posedge clk) $past(v, 3) == 4'd2 |-> a);"
    conversion = convert(
        export_texts([("r.sv", _TEMPLATE.format(snippet=snippet))], SlangOptions(top=("r",)))
    )
    (directive,) = conversion.directives
    sampled = [n for n in _nodes(directive.property) if isinstance(n, Sampled)]
    assert [(s.function, s.depth) for s in sampled] == [(SampledFunction.PAST, 3)]


def test_bit_counts_are_lowered_to_sums_of_bits() -> None:
    """7."""
    snippet = "x: assert property (@(posedge clk) $onehot(v) |-> $countones(v) <= 2);"
    conversion = convert(
        export_texts([("r.sv", _TEMPLATE.format(snippet=snippet))], SlangOptions(top=("r",)))
    )
    (directive,) = conversion.directives
    nodes = list(_nodes(directive.property))
    selects = {n.index for n in nodes if isinstance(n, BitSelect)}
    assert selects == {0, 1, 2, 3}
    assert not any(isinstance(n, Sampled) for n in nodes)
    ops = {n.op for n in nodes if isinstance(n, Binary)}
    assert {BinaryOp.ADD, BinaryOp.EQUALITY, BinaryOp.LESS_THAN_EQUAL} <= ops


def test_wide_bit_counts_compile() -> None:
    """7. The sum is a balanced tree, so a wide operand stays shallow."""
    text = """module w(input logic clk, input logic [4095:0] v);
  x: assert property (@(posedge clk) $countones(v) == 7);
endmodule
"""
    conversion = convert(export_texts([("w.sv", text)], SlangOptions(top=("w",))))
    (directive,) = conversion.directives
    assert validation_problems(directive) == ()
    machine = compile_directive(directive)
    assert "module m" in monitor_module(machine, "m")


def test_sequence_or() -> None:
    """6."""
    snippet = "x: assert property (@(posedge clk) a |-> (b ##1 c) or d);"
    conversion = convert(
        export_texts([("r.sv", _TEMPLATE.format(snippet=snippet))], SlangOptions(top=("r",)))
    )
    (directive,) = conversion.directives
    assert isinstance(directive.property, Implication)
    consequent = directive.property.consequent
    assert isinstance(consequent, SequenceProperty)
    b, c, d = (_b(name, scope="r") for name in "bcd")
    assert consequent.sequence == SeqOr(SeqConcat(b, Delay(1, 1), c), d)


# --- 4: procedural assertions ----------------------------------------------------------------

_PROC = """module r(input logic clk, a, b, c, d, input logic [1:0] v);
  always @(posedge clk) begin
    if (a) x1: assert property (b |-> c);
    else x2: assume property (c);
    case (v)
      2'd1, 2'd2: x3: cover property (d);
      default: x4: assert property (a |=> b);
    endcase
  end
  always @(posedge clk) x5: assert property (d);
endmodule
"""


def test_procedural_assertions_get_their_enabling_condition() -> None:
    """4."""
    conversion = convert(export_texts([("r.sv", _PROC)], SlangOptions(top=("r",))))
    assert conversion.rejections == ()
    directives = {d.name: d for d in conversion.directives}
    # the enabling condition as the machine sees it (if (en) p, fused by temporal.lower)
    lowered = {name: lower(d.property) for name, d in directives.items()}
    a, b, c, d = (_b(name, scope="r") for name in "abcd")
    for directive in directives.values():
        assert directive.clock == Clock(Signal("r.clk", BIT), Edge.POSEDGE)
    assert lowered["x1"] == Implication(SeqConcat(a, Delay(0, 0), b), SequenceProperty(c), True)
    assert directives["x2"].kind is DirectiveKind.ASSUME
    assert lowered["x2"] == Implication(
        Bool(Unary(UnaryOp.LOGICAL_NOT, a.expr, BIT)), SequenceProperty(c), True
    )
    # case: v == 1 || v == 2, and its negation for default
    x3 = lowered["x3"]
    assert isinstance(x3, Implication) and x3.consequent == SequenceProperty(d)
    assert isinstance(x3.antecedent, Bool)
    covered = x3.antecedent.expr
    assert isinstance(covered, Binary) and covered.op is BinaryOp.LOGICAL_OR
    assert all(
        isinstance(side, Binary) and side.op is BinaryOp.EQUALITY
        for side in (covered.left, covered.right)
    )
    x4 = lowered["x4"]
    assert x4 == Implication(
        SeqConcat(Bool(Unary(UnaryOp.LOGICAL_NOT, covered, BIT)), Delay(0, 0), a),
        SequenceProperty(b),
        False,
    )
    assert lowered["x5"] == SequenceProperty(d)  # no condition: unchanged


_INITIAL = """module r(input logic clk, a, b, c, d);
  default clocking @(posedge clk); endclocking
  initial x1: assert property (@(posedge clk) a |-> b);
  initial x2: assert property (@(posedge clk) c);
  initial x3: cover property (@(posedge clk) b ##1 c);
  initial @(posedge clk) x4: assume property (d);
  initial x5: assert property (a |=> b);
  always @(posedge clk) x6: assert property (d);
endmodule
"""


def test_initial_assertions_are_enabled_by_the_first_sample() -> None:
    """4 (IEEE 1800-2017 16.14.6): ``FirstSample`` as the enabling condition."""
    conversion = convert(export_texts([("r.sv", _INITIAL)], SlangOptions(top=("r",))))
    assert conversion.rejections == ()
    directives = {d.name: d for d in conversion.directives}
    first = Bool(FirstSample(BIT))
    a, b, c, d = (_b(name, scope="r") for name in "abcd")
    for directive in directives.values():
        assert directive.clock == Clock(Signal("r.clk", BIT), Edge.POSEDGE)  # explicit,
        # default clocking or inferred from the block's event control: the same clock
    assert directives["x1"].property == Implication(
        SeqConcat(first, Delay(0, 0), a), SequenceProperty(b), True
    )
    assert directives["x5"].property == Implication(
        SeqConcat(first, Delay(0, 0), a), SequenceProperty(b), False
    )
    # a property that is not an implication: if (FirstSample) p
    assert directives["x2"].property == PropIf(FirstSample(BIT), SequenceProperty(c), None)
    assert directives["x4"].kind is DirectiveKind.ASSUME
    assert directives["x4"].property == PropIf(FirstSample(BIT), SequenceProperty(d), None)
    assert directives["x3"].kind is DirectiveKind.COVER
    assert directives["x3"].property == PropIf(
        FirstSample(BIT), SequenceProperty(SeqConcat(b, Delay(1, 1), c)), None
    )
    # an always block is unchanged: no FirstSample
    assert directives["x6"].property == SequenceProperty(d)
    for directive in directives.values():
        assert validation_problems(directive) == ()


def test_initial_enabling_condition_is_lowered_into_the_antecedent() -> None:
    """4: the machine sees ``(FirstSample ##0 a) |-> b``."""
    conversion = convert(export_texts([("r.sv", _INITIAL)], SlangOptions(top=("r",))))
    x5 = next(d for d in conversion.directives if d.name == "x5")
    assert isinstance(x5.property, Implication)
    antecedent = x5.property.antecedent
    assert isinstance(antecedent, SeqConcat)
    assert antecedent.left == Bool(FirstSample(BIT))


@pytest.mark.parametrize(
    "snippet",
    [
        "initial if (a) x: assert property (@(posedge clk) b);",
        "initial if (a) ; else x: assert property (@(posedge clk) b);",
        "initial case (a) 1'b1: x: assert property (@(posedge clk) b); default: ; endcase",
        "initial begin if (a) x: cover property (@(posedge clk) b ##1 c); end",
    ],
    ids=["if", "else", "case", "cover_if"],
)
def test_initial_path_condition_is_rejected(snippet: str) -> None:
    """9 (IEEE 1800-2017 16.14.6.2): a condition around a concurrent assertion in an
    initial procedure is evaluated at time 0, not at the first clock tick: rejected, with
    the located message (origin: review finding P1)."""
    text = f"module r(input logic clk, a, b, c);\n  {snippet}\nendmodule\n"
    conversion = convert(export_texts([("r.sv", text)], SlangOptions(top=("r",))))
    assert conversion.directives == ()
    (rejection,) = conversion.rejections
    assert (rejection.scope, rejection.name) == ("r", "x")
    assert rejection.message == (
        "a condition around a concurrent assertion in an initial procedure is evaluated when "
        "the procedure runs at time 0, not at the assertion's first clock tick (IEEE 1800-2017 "
        "16.14.6.2); not supported: move the condition into the property (e.g. "
        "`assert property (@(posedge clk) en |-> p)`) after checking it means the same"
    )
    assert rejection.source is not None and rejection.source.file == "r.sv"
    assert rejection.source.line == 2


def test_initial_cover_of_an_unbounded_sequence_is_rejected() -> None:
    """9: ``FirstSample`` makes the cover a property ``if``, which the unbounded
    cover rules do not accept, although the same cover is accepted at module level
    (documented limitation, docs/semantics.md section 6; origin: review finding P2)."""
    snippet = "{kw}x: cover property (@(posedge clk) a ##[1:$] b);"
    results = {}
    for kw in ("", "initial "):
        text = f"module r(input logic clk, a, b);\n  {snippet.format(kw=kw)}\nendmodule\n"
        conversion = convert(export_texts([("r.sv", text)], SlangOptions(top=("r",))))
        (directive,) = conversion.directives
        results[kw] = validation_problems(directive)
    assert results[""] == ()
    assert results["initial "] == ("unbounded ranges ($) are not supported yet",)


@pytest.mark.parametrize("value", ["missing", 3])
def test_a_missing_or_malformed_disable_analysis_is_a_format_error(value: object) -> None:
    """10."""
    document = export_texts([("r.sv", _PROC)], SlangOptions(top=("r",)))
    record = document["assertions"][0]  # type: ignore[index]
    assert isinstance(record, dict)
    if value == "missing":
        del record["sva2yosys_disableIssue"]
    else:
        record["sva2yosys_disableIssue"] = value  # type: ignore[assignment]
    with pytest.raises(FormatError):
        convert(document)


@pytest.mark.parametrize(
    ("text", "strength"),
    [
        ("a ##1 b", Strength.DEFAULT),
        ("strong(a ##1 b)", Strength.STRONG),
        ("weak(a ##1 b)", Strength.WEAK),
    ],
)
def test_sequence_strength(text: str, strength: Strength) -> None:
    """5."""
    for snippet, expect_implication in (
        (f"x: assert property (@(posedge clk) {text});", False),
        (f"x: assert property (@(posedge clk) c |-> {text});", True),
    ):
        conversion = convert(
            export_texts([("r.sv", _TEMPLATE.format(snippet=snippet))], SlangOptions(top=("r",)))
        )
        (directive,) = conversion.directives
        prop = directive.property
        if expect_implication:
            assert isinstance(prop, Implication)
            prop = prop.consequent
        assert isinstance(prop, SequenceProperty)
        assert prop.strength is strength
        assert prop.sequence == SeqConcat(_b("a", scope="r"), Delay(1, 1), _b("b", scope="r"))


def test_an_unknown_strength_is_a_format_error() -> None:
    """10."""
    snippet = "x: assert property (@(posedge clk) strong(a ##1 b));"
    document = export_texts([("r.sv", _TEMPLATE.format(snippet=snippet))], SlangOptions(top=("r",)))
    text = json.dumps(document).replace('"strength": "Strong"', '"strength": "Fierce"')
    with pytest.raises(FormatError):
        convert(json.loads(text))


def test_not_of_a_sequence() -> None:
    """5."""
    a, b, c = (_b(name, scope="r") for name in "abc")
    for snippet, expected in (
        (
            "x: assert property (@(posedge clk) not (a ##1 b));",
            Not(SequenceProperty(SeqConcat(a, Delay(1, 1), b))),
        ),
        (
            "x: assert property (@(posedge clk) c |-> not (a ##1 b));",
            Implication(c, Not(SequenceProperty(SeqConcat(a, Delay(1, 1), b))), True),
        ),
    ):
        conversion = convert(
            export_texts([("r.sv", _TEMPLATE.format(snippet=snippet))], SlangOptions(top=("r",)))
        )
        (directive,) = conversion.directives
        assert directive.property == expected


def _converted(snippet: str) -> Property:
    conversion = convert(
        export_texts([("r.sv", _TEMPLATE.format(snippet=snippet))], SlangOptions(top=("r",)))
    )
    assert conversion.rejections == (), snippet
    (directive,) = conversion.directives
    return directive.property


def test_property_if() -> None:
    """5. The structure is preserved as PropIf (lowering is a separate pass)."""
    a, b, c, d = (_b(name, scope="r") for name in "abcd")
    sa = a.expr
    bc = SequenceProperty(SeqConcat(b, Delay(1, 1), c))
    cd = SequenceProperty(SeqConcat(c, Delay(1, 1), d))
    for snippet, expected in (
        ("x: assert property (@(posedge clk) if (a) b ##1 c);", PropIf(sa, bc, None)),
        (
            "x: assert property (@(posedge clk) if (a) (b |=> c));",
            PropIf(sa, Implication(b, SequenceProperty(c), False), None),
        ),
        (
            "x: assert property (@(posedge clk) if (a) b else c ##1 d);",
            PropIf(sa, SequenceProperty(b), cd),
        ),
        (
            "x: assert property (@(posedge clk) if (a) not b else not c);",
            PropIf(sa, Not(SequenceProperty(b)), Not(SequenceProperty(c))),
        ),
        (
            "x: assert property (@(posedge clk) if (a) (b |-> c) else d);",
            PropIf(sa, Implication(b, SequenceProperty(c), True), SequenceProperty(d)),
        ),
        (
            "x: assert property (@(posedge clk) if (a) strong(b) else c);",
            PropIf(sa, SequenceProperty(b, Strength.STRONG), SequenceProperty(c)),
        ),
    ):
        assert _converted(snippet) == expected, snippet


def test_property_if_in_a_procedural_enable_is_an_implication() -> None:
    """5."""
    text = (
        "module p(input logic clk, en, a, b);\n"
        "  always @(posedge clk) if (en) x: assert property (if (a) b else a);\n"
        "endmodule\n"
    )
    conversion = convert(export_texts([("p.sv", text)], SlangOptions(top=("p",))))
    (directive,) = conversion.directives
    # if (en) (if (a) b else a): kept as written, lowered to en |-> ((a ##0 b) or (!a ##0 a))
    assert isinstance(directive.property, PropIf)
    assert isinstance(directive.property.then, PropIf)
    lowered = lower(directive.property)
    assert isinstance(lowered, Implication)
    assert isinstance(lowered.consequent, SequenceProperty)
    assert isinstance(lowered.consequent.sequence, SeqOr)


def test_property_if_without_else_in_a_procedural_enable_fuses() -> None:
    """5: if (en) assert property (if (a) b) is (en ##0 a) |-> b, as before the IR kept if."""
    text = (
        "module p(input logic clk, en, a, b);\n"
        "  always @(posedge clk) if (en) x: assert property (if (a) b);\n"
        "endmodule\n"
    )
    conversion = convert(export_texts([("p.sv", text)], SlangOptions(top=("p",))))
    (directive,) = conversion.directives
    en, a, b = (_b(name, scope="p") for name in ("en", "a", "b"))
    assert lower(directive.property) == Implication(
        SeqConcat(en, Delay(0, 0), a), SequenceProperty(b), True
    )
    assert validation_problems(directive) == ()


def test_bounded_temporal_operators() -> None:
    """5. nexttime / always / eventually and their strong forms keep their structure."""
    a, b, c = (_b(name, scope="r") for name in "abc")
    sa = SequenceProperty(a)
    nested = Implication(b, SequenceProperty(c), True)
    for snippet, expected in (
        ("x: assert property (@(posedge clk) nexttime a);", Nexttime(1, False, sa)),
        ("x: assert property (@(posedge clk) nexttime [0] a);", Nexttime(0, False, sa)),
        ("x: assert property (@(posedge clk) nexttime [2] a);", Nexttime(2, False, sa)),
        ("x: assert property (@(posedge clk) s_nexttime a);", Nexttime(1, True, sa)),
        ("x: assert property (@(posedge clk) s_nexttime [3] a);", Nexttime(3, True, sa)),
        ("x: assert property (@(posedge clk) always [1:3] a);", Always(1, 3, False, sa)),
        ("x: assert property (@(posedge clk) always [0:0] a);", Always(0, 0, False, sa)),
        ("x: assert property (@(posedge clk) s_always [1:3] a);", Always(1, 3, True, sa)),
        ("x: assert property (@(posedge clk) eventually [0:2] a);", Eventually(0, 2, False, sa)),
        ("x: assert property (@(posedge clk) s_eventually [1:2] a);", Eventually(1, 2, True, sa)),
        (
            "x: assert property (@(posedge clk) a |-> always [1:2] (b |-> c));",
            Implication(a, Always(1, 2, False, nested), True),
        ),
        (
            "x: assert property (@(posedge clk) not eventually [0:1] a);",
            Not(Eventually(0, 1, False, sa)),
        ),
        (
            "x: assert property (@(posedge clk) nexttime [2] always [0:1] a);",
            Nexttime(2, False, Always(0, 1, False, sa)),
        ),
    ):
        assert _converted(snippet) == expected, snippet


def test_bounded_temporal_operator_in_a_procedural_enable_is_wrapped_in_if() -> None:
    """5."""
    text = (
        "module p(input logic clk, en, a);\n"
        "  always @(posedge clk) if (en) x: assert property (always [1:2] a);\n"
        "endmodule\n"
    )
    conversion = convert(export_texts([("p.sv", text)], SlangOptions(top=("p",))))
    (directive,) = conversion.directives
    en, a = (_b(name, scope="p") for name in ("en", "a"))
    assert directive.property == PropIf(en.expr, Always(1, 2, False, SequenceProperty(a)), None)


def test_property_or() -> None:
    """5. Two default-strength sequences give the sequence 'or'; any other pair of
    operands gives a PropOr, flat."""
    a, b, c, d = (_b(name, scope="r") for name in "abcd")
    sa, sb, sc = SequenceProperty(a), SequenceProperty(b), SequenceProperty(c)
    ia = Implication(a, SequenceProperty(b), True)
    ic = Implication(c, SequenceProperty(d), True)
    for snippet, expected in (
        ("x: assert property (@(posedge clk) a or b);", SequenceProperty(SeqOr(a, b))),
        (
            "x: assert property (@(posedge clk) strong(a) or b);",
            PropOr((SequenceProperty(a, Strength.STRONG), sb)),
        ),
        (
            "x: assert property (@(posedge clk) a or weak(b));",
            PropOr((sa, SequenceProperty(b, Strength.WEAK))),
        ),
        ("x: assert property (@(posedge clk) (a |-> b) or (c |-> d));", PropOr((ia, ic))),
        (
            "x: assert property (@(posedge clk) (a |-> b) or (c |-> d) or c);",
            PropOr((ia, ic, sc)),
        ),
    ):
        assert _converted(snippet) == expected, snippet


def test_implication_consequent_may_be_any_property() -> None:
    """5. No rejection of a nested implication, no distribution over 'and', and 'not' of
    any property."""
    a, b, c, d = (_b(name, scope="r") for name in "abcd")
    sb, sc, sd = SequenceProperty(b), SequenceProperty(c), SequenceProperty(d)
    inner = Implication(b, sc, True)
    for snippet, expected in (
        ("x: assert property (@(posedge clk) a |-> (b |-> c));", Implication(a, inner, True)),
        (
            "x: assert property (@(posedge clk) a |-> ((b |-> c) and d));",
            Implication(a, PropAnd((inner, sd)), True),
        ),
        (
            "x: assert property (@(posedge clk) a |=> (b and (c |-> d)));",
            Implication(a, PropAnd((sb, Implication(c, sd, True))), False),
        ),
        (
            "x: assert property (@(posedge clk) not (a |-> b));",
            Not(Implication(a, sb, True)),
        ),
        ("x: assert property (@(posedge clk) not not a);", Not(Not(SequenceProperty(a)))),
    ):
        assert _converted(snippet) == expected, snippet


def test_property_and() -> None:
    """5. Two sequences give the sequence 'and'; otherwise a PropAnd, flat. An enabling
    condition maps over its operands."""
    a, b, c, d = (_b(name, scope="r") for name in "abcd")
    sb = SequenceProperty(b)
    ia, ib = Implication(a, SequenceProperty(b), True), Implication(c, SequenceProperty(d), True)
    en = _b("en", scope="r")
    for snippet, expected in (
        ("x: assert property (@(posedge clk) a and b);", SequenceProperty(SeqAnd(a, b))),
        ("x: assert property (@(posedge clk) (a |-> b) and (c |-> d));", PropAnd((ia, ib))),
        (
            "x: assert property (@(posedge clk) (a |-> b) and (c |-> d) and not a);",
            PropAnd((ia, ib, Not(SequenceProperty(a)))),
        ),
        (
            "x: assert property (@(posedge clk) strong(a) and b);",
            PropAnd((SequenceProperty(a, Strength.STRONG), sb)),
        ),
        (
            "x: assert property (@(posedge clk) a |=> (not b and not c));",
            Implication(a, PropAnd((Not(sb), Not(SequenceProperty(c)))), False),
        ),
        (
            "x: assert property (@(posedge clk) if (en) ((a |-> b) and (c |-> d)));",
            PropIf(en.expr, PropAnd((ia, ib)), None),
        ),
    ):
        assert _converted(snippet) == expected, snippet
