"""Unit contract of the traceability generator (the docstring of ``support.traceability``)."""

from __future__ import annotations

from support.traceability import (
    ROWS,
    STRUCTURAL,
    CaseEvidence,
    Construct,
    constructs_of,
    mapped_rows,
    matrix_rows,
    past_arities,
    render,
    verified_rows,
)
from support.tracefile import CaseClass, TraceCase
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Sampled, SampledFunction, Signal
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
from sva2yosys.ir.sequence import Bool, Delay, Goto, Repeat, SeqConcat, Sequence, Throughout
from sva2yosys.ir.source import SourceRange

A = Signal("m.a", BIT)
B = Signal("m.b", BIT)
CLOCK = Clock(Signal("m.clk", BIT), Edge.POSEDGE)


def _directive(
    prop: Property, kind: DirectiveKind = DirectiveKind.ASSERT, disable: Signal | None = None
) -> Directive:
    return Directive(kind, "p", "m", CLOCK, disable, prop, SourceRange("m.sv", 1, 1, 1, 2))


def _constructs(prop: Property, **kwargs: object) -> frozenset[Construct]:
    return constructs_of(_directive(prop, **kwargs))  # type: ignore[arg-type]


def test_kind_and_disable_are_constructs() -> None:
    prop = SequenceProperty(Bool(A))
    assert _constructs(prop) == {
        Construct.ASSERT,
        Construct.BOOLEAN,
        Construct.SEQUENCE_PROPERTY,
    }
    found = _constructs(prop, kind=DirectiveKind.COVER, disable=B)
    assert Construct.COVER in found
    assert Construct.DISABLE_IFF in found
    assert Construct.ASSERT not in found


def test_sequence_constructs_by_bound() -> None:
    seq: Sequence = SeqConcat(Repeat(Bool(A), 1, None), Delay(1, 1), Goto(B, 1, 2))
    found = _constructs(
        Implication(seq, SequenceProperty(SeqConcat(Bool(A), Delay(0, 2), Bool(B))), True)
    )
    assert {
        Construct.REPEAT_UNBOUNDED,
        Construct.FIXED_DELAY,
        Construct.GOTO,
        Construct.DELAY_RANGE,
        Construct.IMPLICATION,
    } <= found
    assert Construct.DELAY_UNBOUNDED not in found
    assert Construct.REPEAT_BOUNDED not in found
    assert Construct.SEQUENCE_PROPERTY not in found  # only a property that is itself a sequence


def test_property_constructs() -> None:
    inner = SequenceProperty(Bool(A), Strength.STRONG)
    assert Construct.STRENGTH in _constructs(inner)
    assert _constructs(Always(0, None, False, inner)) >= {Construct.ALWAYS_UNBOUNDED}
    assert Construct.BOUNDED_TEMPORAL in _constructs(Always(0, 2, False, inner))
    assert Construct.S_EVENTUALLY_UNBOUNDED in _constructs(Eventually(0, None, True, inner))
    assert Construct.BOUNDED_TEMPORAL in _constructs(Nexttime(1, False, inner))
    assert Construct.NOT in _constructs(Not(inner))
    assert Construct.UNTIL in _constructs(Until(A, B, False, False))
    assert Construct.S_UNTIL in _constructs(Until(A, B, True, True))
    assert Construct.PROPERTY_OR_NESTED in _constructs(PropOr((inner, inner)))
    nested = Implication(Bool(A), Implication(Bool(B), inner, True), True)
    assert Construct.PROPERTY_OR_NESTED in _constructs(nested)
    assert Construct.SEQUENCE_COMBINATORS in _constructs(SequenceProperty(Throughout(A, Bool(B))))


def test_sampled_functions_by_kind_and_depth() -> None:
    past = Sampled(SampledFunction.PAST, A, BIT)
    deep = Sampled(SampledFunction.PAST, A, BIT, depth=3)
    rose = Sampled(SampledFunction.ROSE, A, BIT)
    one = _directive(SequenceProperty(Bool(past)))
    assert Construct.PAST in constructs_of(one, "$past(a)")
    found = constructs_of(_directive(SequenceProperty(Bool(deep))), "$past(a, 3)")
    assert Construct.PAST_DEPTH in found
    assert Construct.PAST not in found
    # the IR keeps depth 1 for an explicit depth 1: the written arity decides
    explicit = constructs_of(one, "$past(a, 1)")
    assert Construct.PAST_DEPTH in explicit
    assert Construct.PAST not in explicit
    assert Construct.EDGE_FUNCTIONS in _constructs(SequenceProperty(Bool(rose)))


def test_lowered_functions_are_found_in_the_text() -> None:
    directive = _directive(SequenceProperty(Bool(A)))
    assert Construct.BIT_COUNTS in constructs_of(directive, "$onehot0(v) |-> b")
    assert Construct.BIT_COUNTS in constructs_of(directive, "$countones(v) == 2")
    assert Construct.ISUNKNOWN in constructs_of(directive, "!$isunknown(v)")
    assert Construct.ISUNKNOWN not in constructs_of(directive, "a")


def test_past_arity_counts_top_level_arguments() -> None:
    assert past_arities("$past(a)") == (1,)
    assert past_arities("$past(a, 1)") == (2,)
    assert past_arities("$past(a, 2) && $past (b)") == (2, 1)
    assert past_arities("$past(f(x, y)) == $past({a, b}, 2)") == (1, 2)
    assert past_arities("$past($past(a), 1)") == (2, 1)
    assert past_arities("a ##1 b") == ()
    assert past_arities("$past(a /* , */)") == (1,)
    assert past_arities("$past /* c */ (a)") == (1,)
    assert past_arities("$past(a, // , x\n 2)") == (2,)


def test_nested_implication_is_found_inside_wrapped_consequents() -> None:
    inner = Implication(Bool(B), SequenceProperty(Bool(A)), True)
    wrappers: dict[str, Property] = {
        "not": Not(inner),
        "and": PropAnd((SequenceProperty(Bool(A)), inner)),
        "or": PropOr((SequenceProperty(Bool(A)), inner)),
        "if": PropIf(A, inner, None),
        "else": PropIf(A, SequenceProperty(Bool(A)), inner),
        "nexttime": Nexttime(1, False, inner),
        "always": Always(0, 2, False, inner),
        "eventually": Eventually(0, 2, False, inner),
        "direct": inner,
    }
    for name, consequent in wrappers.items():
        found = _constructs(Implication(Bool(A), consequent, True))
        assert Construct.PROPERTY_OR_NESTED in found, name
    # an implication outside any consequent is not nested
    assert Construct.PROPERTY_OR_NESTED not in _constructs(Not(inner))
    assert Construct.PROPERTY_OR_NESTED not in _constructs(
        Implication(Bool(A), SequenceProperty(Bool(B)), True)
    )


def test_matrix_rows_and_tested_status() -> None:
    text = (
        "## Matrix\n\n| Construct | Group | Slang | Status |\n|---|---|---|---|\n"
        "| `\\|->` | g | ✓ | TESTED |\n| other | g | ✓ | NOT PARSED |\n"
        "| diff | g | ✓ | DIFFERENTIAL TESTED: x |\n"
        "| mixed | g | ✓ | CODEGEN IMPLEMENTED: y. Without arguments: TESTED |\n\n"
        "## Notes\n\n| z | 1 | 2 | TESTED |\n"
    )
    assert matrix_rows(text) == {
        "`|->`": "TESTED",
        "other": "NOT PARSED",
        "diff": "DIFFERENTIAL TESTED: x",
        "mixed": "CODEGEN IMPLEMENTED: y. Without arguments: TESTED",
    }
    assert verified_rows(text) == {"`|->`", "diff", "mixed"}


def test_mapping_tables_are_disjoint() -> None:
    labels = [label for rows in ROWS.values() for label in rows]
    assert len(labels) == len(set(labels))
    assert not set(labels) & set(STRUCTURAL)
    assert set(ROWS) == set(Construct)
    assert mapped_rows() == set(labels) | set(STRUCTURAL)


def _case(name: str, file: str, oracle: bool = False, loop: int | None = None) -> TraceCase:
    return TraceCase(
        file=file,
        line=1,
        name=name,
        property="a",
        kind="assert",
        disable=None,
        widths={"a": 1},
        samples=({"a": 1},),
        fails=frozenset(),
        passes=frozenset(),
        pending=None,
        oracle_only=oracle,
        rejected_by="why" if oracle else None,
        loop=loop,
        clause=("16.12.2", "Annex F"),
        case_class=CaseClass.NORMATIVE,
    )


def test_render_is_deterministic_and_lists_cases_and_links() -> None:
    evidence = (
        CaseEvidence(_case("one", "x.trace"), frozenset({Construct.ASSERT, Construct.NOT})),
        CaseEvidence(_case("two", "x.trace", oracle=True), frozenset({Construct.NOT})),
        CaseEvidence(_case("three", "y.trace", loop=0), frozenset({Construct.NOT}), live=True),
    )
    page = render(evidence)
    assert page == render(evidence)
    assert "[x.trace](../tests/functional_tests/reference_semantics/fixtures/x.trace)" in page
    assert '`two` (rejected by: "why")' in page
    assert "Clauses cited: 16.12.2 (3), Annex F (3)" in page
    # a construct without any case still has a section with zero counts
    assert "## unbounded always" in page
    assert "| not | 3 | 3 | 0 | 0 | 1 |" in page
    assert "liveness task" in page
    assert "| Matrix row | Evidence |" in page
