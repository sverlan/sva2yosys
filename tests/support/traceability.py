"""Traceability page: which constructs the trace cases exercise, and with what evidence.

Test-support module (never imported by ``src/``). Its output is ``docs/traceability.md``,
a generated, committed page; ``tests/functional_tests/reference_semantics/
test_traceability.py`` fails when the page is stale or when the construct mapping and
``docs/supported-sva.md`` disagree.

What is generated. For every case of every ``*.trace`` file of
``tests/functional_tests/reference_semantics/fixtures``, in sorted file order:

1. The case is placed in a module (``support.tracefile.sv_source``), compiled and exported
   with Slang, and converted to the IR by the real frontend. Cases that the pipeline
   rejects (``support: oracle``) are converted too: the converter accepts them, the
   rejection comes from validation, so they have an IR.
2. :func:`constructs_of` walks the directive with an explicit ``match`` over every IR node
   class and returns the set of :class:`Construct` it contains (the directive kind and
   ``disable iff`` included). Three things the IR does not keep are read from the case's
   property and ``disable`` text instead (SystemVerilog comments removed first): the bit
   counts (``$countones``, ``$onehot``, ``$onehot0``), ``$isunknown`` and ``inside``, which the
   converter lowers before the IR exists, and the number of arguments of each ``$past`` call (the IR
   keeps only the depth). Everything else comes from the IR.
3. Each construct is mapped to the exact row label(s) of the ``docs/supported-sva.md``
   matrix (:data:`ROWS`). The matrix rows that no trace case can exercise (procedural
   assertions, the clocking event, ``default clocking``, ``default disable iff``, ``bind``,
   parameters, generate loops) are listed in :data:`STRUCTURAL` with the numbered
   points of the ``CONTRACT.md`` files that state their guarantee. The number of arguments of
   ``$past`` is read from the text (the IR keeps only the depth, so ``$past(a, 1)`` would look
   like ``$past(a)``).

The page has a summary table (construct, number of cases, cases per class, number of
reference-only cases, layers) and one section per construct: the matrix rows, the clauses
cited (with counts), the interpretations used, the layers, and the case names grouped by
trace file with links to the files. Reference-only cases (``support: oracle``: the pipeline
rejects the property; they include every ``must-reject`` case) are listed apart, with their
``rejected-by`` text. A case counts once per construct, however often the construct occurs
in it, and once per class: ``interpretation`` cases count once in the interpretation column
whatever the number of ids they cite. The output is sorted and carries no timestamp.

Layers. A construct's layers are: the reference evaluator and the hand-derived tables
(always); the temporal machine and the generated monitor under SBY (when the construct has
at least one accepted case: these layers run every accepted case); the liveness task (when
it has an accepted lasso ``assert`` case that the liveness test would run: the directive compiles
to exactly one configuration machine with a liveness part).

Coverage rules, enforced by the functional test: the committed page equals :func:`render`;
every :class:`Construct` has at least one accepted case; every row of the matrix whose
status contains ``TESTED`` (also after other words, as in the mixed-status declaration rows)
is mapped by :data:`ROWS` or
:data:`STRUCTURAL`; every mapped row exists in the matrix (this catches renames).

Command line (from the repository root)::

    uv run python tests/support/traceability.py            # print the page
    uv run python tests/support/traceability.py --write    # rewrite docs/traceability.md
    uv run python tests/support/traceability.py --check    # exit 1 if the page differs
"""

from __future__ import annotations

import argparse
import functools
import re
import sys
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

if __package__ in (None, ""):  # run as a script: make the `support` package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support.tracefile import CaseClass, TraceCase, load_cases, sv_source
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import (
    Binary,
    BitSelect,
    Concat,
    Conditional,
    Const,
    Convert,
    Expr,
    FirstSample,
    RangeSelect,
    Replicate,
    Sampled,
    SampledFunction,
    Signal,
    Unary,
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
    Bool,
    Goto,
    NonConsecutive,
    Repeat,
    SeqAnd,
    SeqConcat,
    SeqIntersect,
    SeqOr,
    Sequence,
    Throughout,
    Within,
)
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.compile import CompileError, compile_monitors
from sva2yosys.temporal.configurations import ConfigMachine

REPOSITORY = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY / "tests" / "functional_tests" / "reference_semantics" / "fixtures"
PAGE = REPOSITORY / "docs" / "traceability.md"
MATRIX = REPOSITORY / "docs" / "supported-sva.md"
MODULE = "tc"
_FIXTURE_LINK = "../tests/functional_tests/reference_semantics/fixtures"


class Construct(Enum):
    """A semantic construct of the ``docs/supported-sva.md`` matrix that trace cases exercise.

    The value is the name shown on the page (it contains no ``|``).
    """

    ASSERT = "assert property"
    ASSUME = "assume property"
    COVER = "cover property"
    BOOLEAN = "Boolean expressions"
    PAST = "$past(e)"
    PAST_DEPTH = "$past(e, n)"
    EDGE_FUNCTIONS = "$rose, $fell, $stable, $changed"
    BIT_COUNTS = "$countones, $onehot, $onehot0"
    ISUNKNOWN = "$isunknown"
    INSIDE = "inside"
    DISABLE_IFF = "disable iff"
    FIXED_DELAY = "fixed delay and fusion (##N, ##0)"
    DELAY_RANGE = "bounded delay range (##[N:M])"
    DELAY_UNBOUNDED = "unbounded delay (##[N:$], ##[*], ##[+])"
    REPEAT_BOUNDED = "bounded consecutive repetition ([*N], [*N:M])"
    REPEAT_UNBOUNDED = "unbounded consecutive repetition ([*], [+], [*N:$])"
    NONCONSECUTIVE = "nonconsecutive repetition ([=N])"
    GOTO = "goto repetition ([->N])"
    SEQUENCE_OR = "sequence or"
    SEQUENCE_COMBINATORS = "sequence and, intersect, within, throughout"
    SEQUENCE_PROPERTY = "sequence as property"
    IMPLICATION = "implication (overlapping and non-overlapping)"
    NOT = "not"
    PROPERTY_AND = "property and"
    PROPERTY_IF = "property if / else"
    PROPERTY_OR_NESTED = "property or, nested implication"
    BOUNDED_TEMPORAL = "nexttime, bounded always, bounded eventually"
    UNTIL = "until, until_with"
    S_UNTIL = "s_until, s_until_with"
    STRENGTH = "strong(...), weak(...)"
    ALWAYS_UNBOUNDED = "unbounded always"
    S_EVENTUALLY_UNBOUNDED = "unbounded s_eventually"
    INITIAL_PROCEDURE = "initial procedure (one attempt)"


ROWS: dict[Construct, tuple[str, ...]] = {
    Construct.ASSERT: ("`assert property`",),
    Construct.ASSUME: ("`assume property`",),
    Construct.COVER: ("`cover property`",),
    Construct.BOOLEAN: ("Boolean expressions over design signals",),
    Construct.PAST: (
        "`$past(e)` (one argument); conditions that could be known true at the first sample "
        "are rejected, and so are `$past` and the edge functions on operands whose default "
        "sampled value (IEEE 1800 §16.5.1: 2-state types, initialised variables, constants "
        "in the operand) is not all X (`docs/semantics.md` §8)",
    ),
    Construct.PAST_DEPTH: ("`$past(e, n)` (constant `n >= 1`)",),
    Construct.EDGE_FUNCTIONS: ("`$rose`, `$fell`, `$stable`, `$changed`",),
    Construct.BIT_COUNTS: ("`$countones`, `$onehot`, `$onehot0`",),
    Construct.ISUNKNOWN: ("`$isunknown(e)` (§20.9)",),
    Construct.INSIDE: ("`e inside { i1, [lo:hi], ... }` (§11.4.13)",),
    Construct.DISABLE_IFF: ("`disable iff (e)`",),
    Construct.FIXED_DELAY: ("`##N`, `##0` (fusion)",),
    Construct.DELAY_RANGE: ("`##[N:M]`, `##[0:N]`",),
    Construct.DELAY_UNBOUNDED: ("`##[N:$]`, `##[*]`, `##[+]`",),
    Construct.REPEAT_BOUNDED: ("`[*N]`, `[*N:M]`, `[*0]`, `[*0:N]`",),
    Construct.REPEAT_UNBOUNDED: ("`[*]`, `[+]`, `[*N:$]`",),
    Construct.NONCONSECUTIVE: ("`[=N]`, `[=N:M]`, `[=N:$]` (nonconsecutive)",),
    Construct.GOTO: ("`[->N]`, `[->N:M]`, `[->N:$]` (goto)",),
    Construct.SEQUENCE_OR: ("sequence `or`",),
    Construct.SEQUENCE_COMBINATORS: ("sequence `and`, `intersect`, `within`, `throughout`",),
    Construct.SEQUENCE_PROPERTY: ("Sequence as property",),
    Construct.IMPLICATION: ("`|->`, `|=>`",),
    Construct.NOT: ("`not`",),
    Construct.PROPERTY_AND: ("property `and` of non-sequences (`p and q`, §16.12.5)",),
    Construct.PROPERTY_IF: ("property `if (e) p [else q]`",),
    Construct.PROPERTY_OR_NESTED: (
        "property `or` (of non-sequences); implication with a property consequent (nested "
        "implication, `a |-> not (b |-> c)`, `a |-> (p or q)`, …)",
    ),
    Construct.BOUNDED_TEMPORAL: (
        "`nexttime [n]`, `s_nexttime [n]` (default 1, `[0]` included); `always [m:n]`, "
        "`s_always [m:n]`, `eventually [m:n]`, `s_eventually [m:n]` (constant `0 <= m <= n`)",
    ),
    Construct.UNTIL: ("`until`, `until_with`",),
    Construct.S_UNTIL: ("`s_until`, `s_until_with`",),
    Construct.STRENGTH: ("`strong(...)`, `weak(...)`",),
    Construct.ALWAYS_UNBOUNDED: ("unbounded `always p`, `always [m:$] p`",),
    Construct.S_EVENTUALLY_UNBOUNDED: ("unbounded `s_eventually p`, `s_eventually [m:$] p`",),
    Construct.INITIAL_PROCEDURE: ("Concurrent assertions in `initial` procedures (§16.14.6)",),
}
"""The exact matrix row label(s) of each construct (``|`` unescaped)."""


@dataclass(frozen=True)
class Evidence:
    """A numbered point of a functional area's ``CONTRACT.md`` that states a guarantee."""

    area: str
    point: str


STRUCTURAL: dict[str, tuple[Evidence, ...]] = {
    "`restrict property`": (Evidence("slang_convert", "1"), Evidence("rewrite", "2")),
    "Concurrent assertions in `always @(posedge clk)` / `always_ff` (procedural, §16.14.6)": (
        Evidence("slang_convert", "4"),
        Evidence("rewrite", "2"),
    ),
    "`@(posedge clk)`": (Evidence("slang_convert", "2"),),
    "Packed integral operand types as whole values": (
        Evidence("slang_convert", "14"),
        Evidence("slang_frontend", "26"),
        Evidence("formal_designs", "1"),
    ),
    "`default clocking`": (Evidence("slang_frontend", "8"), Evidence("slang_convert", "2")),
    "`default disable iff`": (Evidence("slang_frontend", "9"), Evidence("slang_convert", "3")),
    "`bind <module> <instantiation>` in a verification file": (Evidence("rewrite", "4"),),
    "Parameter-dependent bounds, same value in all instances": (Evidence("rewrite", "5"),),
    "Parameter-dependent bounds or widths, differing per instance": (Evidence("rewrite", "5"),),
    "Assertions in generate loops": (Evidence("rewrite", "5"), Evidence("slang_frontend", "17")),
    "`sequence … endsequence` with arguments": (
        Evidence("slang_convert", "5"),
        Evidence("rewrite", "3"),
    ),
    "`property … endproperty` with arguments": (
        Evidence("slang_convert", "5"),
        Evidence("rewrite", "3"),
    ),
}
"""Matrix rows with a TESTED status that trace cases cannot exercise (every case is one
module-level ``@(posedge clk)`` directive, no hierarchy), with the numbered points of the
``CONTRACT.md`` files that state the guarantee. The two declaration rows have a mixed status
(CODEGEN IMPLEMENTED with arguments, TESTED without): the points cover named declarations
(``slang_convert`` 5: converted through their substituted bodies; ``rewrite`` 3: removed from
verification files)."""

_LOWERED_BIT_COUNT = re.compile(r"\$(?:countones|onehot0?)\b")
_LOWERED_ISUNKNOWN = re.compile(r"\$isunknown\b")
_LOWERED_INSIDE = re.compile(r"\binside\s*\{")


# --- construct collection ---------------------------------------------------------------------


def constructs_of(directive: Directive, text: str = "") -> frozenset[Construct]:
    """The constructs contained in ``directive``.

    ``text`` is the property and ``disable`` text of the case. It decides what the IR does not
    keep: the number of arguments of ``$past`` (``$past(a, 1)`` is the ``$past(e, n)`` row)
    and the constructs the converter lowers before the IR (bit counts, ``$isunknown``).
    """
    text = strip_comments(text)
    found: set[Construct] = set()
    match directive.kind:
        case DirectiveKind.ASSERT:
            found.add(Construct.ASSERT)
        case DirectiveKind.ASSUME:
            found.add(Construct.ASSUME)
        case DirectiveKind.COVER:
            found.add(Construct.COVER)
    if directive.disable is not None:
        found.add(Construct.DISABLE_IFF)
        _expr(directive.disable, found)
    if isinstance(directive.property, SequenceProperty):
        found.add(Construct.SEQUENCE_PROPERTY)
    _property(directive.property, found)
    for arity in past_arities(text):
        found.add(Construct.PAST if arity == 1 else Construct.PAST_DEPTH)
    if _LOWERED_BIT_COUNT.search(text):
        found.add(Construct.BIT_COUNTS)
    if _LOWERED_ISUNKNOWN.search(text):
        found.add(Construct.ISUNKNOWN)
    if _LOWERED_INSIDE.search(text):
        found.add(Construct.INSIDE)
    return frozenset(found)


def strip_comments(text: str) -> str:
    """``text`` with ``/* ... */`` and ``// ...`` comments replaced by a space."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", " ", text, flags=re.DOTALL)


def past_arities(text: str) -> tuple[int, ...]:
    """The number of arguments of every ``$past(...)`` call in ``text`` (balanced brackets;
    commas at depth 1 of the call separate arguments), in order of appearance."""
    text = strip_comments(text)
    arities: list[int] = []
    for match in re.finditer(r"\$past\s*\(", text):
        depth, commas = 1, 0
        for char in text[match.end() :]:
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
                if depth == 0:
                    break
            elif char == "," and depth == 1:
                commas += 1
        arities.append(commas + 1)
    return tuple(arities)


def _expr(expr: Expr, found: set[Construct]) -> None:
    found.add(Construct.BOOLEAN)
    match expr:
        case Signal() | Const():
            pass
        case FirstSample():
            # only the converter writes it: the enabling condition of an `initial` directive
            found.add(Construct.INITIAL_PROCEDURE)
        case Unary(operand=operand) | Convert(operand=operand):
            _expr(operand, found)
        case Binary(left=left, right=right):
            _expr(left, found)
            _expr(right, found)
        case Conditional(condition=condition, then=then, otherwise=otherwise):
            for part in (condition, then, otherwise):
                _expr(part, found)
        case BitSelect(value=value) | RangeSelect(value=value) | Replicate(value=value):
            _expr(value, found)
        case Concat(parts=parts):
            for part in parts:
                _expr(part, found)
        case Sampled(function=function, operand=operand):
            match function:
                case SampledFunction.PAST:
                    pass  # the IR loses the written arity: decided from the text
                case (
                    SampledFunction.ROSE
                    | SampledFunction.FELL
                    | SampledFunction.STABLE
                    | SampledFunction.CHANGED
                ):
                    found.add(Construct.EDGE_FUNCTIONS)
            _expr(operand, found)


def _sequence(sequence: Sequence, found: set[Construct]) -> None:
    match sequence:
        case Bool(expr=expr):
            _expr(expr, found)
        case SeqConcat(left=left, delay=delay, right=right):
            if delay.maximum is None:
                found.add(Construct.DELAY_UNBOUNDED)
            elif delay.minimum == delay.maximum:
                found.add(Construct.FIXED_DELAY)
            else:
                found.add(Construct.DELAY_RANGE)
            _sequence(left, found)
            _sequence(right, found)
        case Repeat(sequence=inner, maximum=maximum):
            found.add(Construct.REPEAT_UNBOUNDED if maximum is None else Construct.REPEAT_BOUNDED)
            _sequence(inner, found)
        case Goto(condition=condition):
            found.add(Construct.GOTO)
            _expr(condition, found)
        case NonConsecutive(condition=condition):
            found.add(Construct.NONCONSECUTIVE)
            _expr(condition, found)
        case SeqOr(left=left, right=right):
            found.add(Construct.SEQUENCE_OR)
            _sequence(left, found)
            _sequence(right, found)
        case SeqAnd(left=left, right=right) | SeqIntersect(left=left, right=right):
            found.add(Construct.SEQUENCE_COMBINATORS)
            _sequence(left, found)
            _sequence(right, found)
        case Throughout(condition=condition, sequence=inner):
            found.add(Construct.SEQUENCE_COMBINATORS)
            _expr(condition, found)
            _sequence(inner, found)
        case Within(inner=inner, outer=outer):
            found.add(Construct.SEQUENCE_COMBINATORS)
            _sequence(inner, found)
            _sequence(outer, found)


def _property(prop: Property, found: set[Construct], inside: bool = False) -> None:
    """``inside``: ``prop`` is in the consequent of an implication, at any depth."""
    match prop:
        case SequenceProperty(sequence=sequence, strength=strength):
            if strength is not Strength.DEFAULT:
                found.add(Construct.STRENGTH)
            _sequence(sequence, found)
        case Implication(antecedent=antecedent, consequent=consequent):
            found.add(Construct.IMPLICATION)
            if inside:
                found.add(Construct.PROPERTY_OR_NESTED)
            _sequence(antecedent, found)
            _property(consequent, found, True)
        case Not(operand=operand):
            found.add(Construct.NOT)
            _property(operand, found, inside)
        case PropAnd(operands=operands):
            found.add(Construct.PROPERTY_AND)
            for operand in operands:
                _property(operand, found, inside)
        case PropOr(operands=operands):
            found.add(Construct.PROPERTY_OR_NESTED)
            for operand in operands:
                _property(operand, found, inside)
        case PropIf(condition=condition, then=then, otherwise=otherwise):
            found.add(Construct.PROPERTY_IF)
            _expr(condition, found)
            _property(then, found, inside)
            if otherwise is not None:
                _property(otherwise, found, inside)
        case Nexttime(operand=operand):
            found.add(Construct.BOUNDED_TEMPORAL)
            _property(operand, found, inside)
        case Always(high=high, operand=operand):
            found.add(Construct.ALWAYS_UNBOUNDED if high is None else Construct.BOUNDED_TEMPORAL)
            _property(operand, found, inside)
        case Eventually(high=high, operand=operand):
            found.add(
                Construct.S_EVENTUALLY_UNBOUNDED if high is None else Construct.BOUNDED_TEMPORAL
            )
            _property(operand, found, inside)
        case Until(left=left, right=right, strong=strong):
            found.add(Construct.S_UNTIL if strong else Construct.UNTIL)
            _expr(left, found)
            _expr(right, found)


# --- cases ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseEvidence:
    """A trace case and the constructs its converted directive contains."""

    case: TraceCase
    constructs: frozenset[Construct]
    live: bool = False


@functools.cache
def collect(fixtures: Path = FIXTURES) -> tuple[CaseEvidence, ...]:
    """Convert every case of every ``*.trace`` file of ``fixtures`` (sorted by file name,
    then in file order) and collect its constructs.

    Raises:
        ValueError: A case does not convert to exactly one directive without rejection.
    """
    evidence: list[CaseEvidence] = []
    for path in sorted(fixtures.glob("*.trace")):
        for case in load_cases(path):
            document = export_texts(
                [(f"{case.name}.sv", sv_source(case, MODULE))], SlangOptions(top=(MODULE,))
            )
            conversion = convert(document)
            if conversion.rejections or len(conversion.directives) != 1:
                raise ValueError(f"{case.file}::{case.name}: the case does not convert")
            directive = conversion.directives[0]
            text = f"{case.property} {case.disable or ''}"
            live = (
                case.loop is not None
                and case.kind == "assert"
                and not case.oracle_only
                and _has_liveness_part(directive)
            )
            evidence.append(CaseEvidence(case, constructs_of(directive, text), live))
    return tuple(evidence)


def _has_liveness_part(directive: Directive) -> bool:
    """The liveness test's criterion: the directive compiles to exactly one configuration
    machine with a liveness part."""
    try:
        monitors = compile_monitors(directive)
    except CompileError:
        return False
    return (
        len(monitors) == 1
        and isinstance(monitors[0], ConfigMachine)
        and monitors[0].liveness is not None
    )


def matrix_rows(text: str) -> dict[str, str]:
    """The rows of the tables under ``## Matrix`` of ``docs/supported-sva.md``: first column
    (with ``\\|`` unescaped) to status column."""
    section = text.split("## Matrix", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, str] = {}
    for line in section.splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = re.split(r"(?<!\\)\|", line)
        label = cells[1].strip().replace("\\|", "|")
        if label != "Construct":
            rows[label] = cells[4].strip()
    return rows


def verified_rows(text: str) -> frozenset[str]:
    """The matrix rows whose status cell contains ``TESTED`` anywhere (``DIFFERENTIAL TESTED``
    and mixed-status rows such as "CODEGEN IMPLEMENTED ... without arguments: TESTED" included)."""
    return frozenset(label for label, status in matrix_rows(text).items() if "TESTED" in status)


def mapped_rows() -> frozenset[str]:
    """Every matrix row label that :data:`ROWS` or :data:`STRUCTURAL` maps."""
    labels = {label for rows in ROWS.values() for label in rows}
    return frozenset(labels | set(STRUCTURAL))


# --- rendering --------------------------------------------------------------------------------


def _clause_key(clause: str) -> tuple[int, tuple[int, ...], str]:
    if clause.startswith("Annex"):
        return (1, (), clause)
    return (0, tuple(int(part) for part in clause.split(".")), clause)


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _layers(items: list[CaseEvidence]) -> list[str]:
    layers = ["reference evaluator", "trace tables"]
    if any(not item.case.oracle_only for item in items):
        layers += ["temporal machine", "generated monitor (SBY)"]
    if any(item.live for item in items):
        layers.append("liveness task")
    return layers


def _case_lists(cases: list[TraceCase]) -> list[str]:
    lines: list[str] = []
    for file in sorted({case.file for case in cases}):
        names = []
        for case in cases:
            if case.file != file:
                continue
            entry = f"`{case.name}`"
            if case.rejected_by is not None:
                entry += f' (rejected by: "{case.rejected_by}")'
            names.append(entry)
        lines.append(f"- [{file}]({_FIXTURE_LINK}/{file}): {', '.join(names)}")
    return lines


def render(evidence: tuple[CaseEvidence, ...] | None = None) -> str:
    """The Markdown page for ``evidence`` (default: :func:`collect`)."""
    data = collect() if evidence is None else evidence
    per_item: dict[Construct, list[CaseEvidence]] = {c: [] for c in Construct}
    for item in data:
        for construct in item.constructs:
            per_item[construct].append(item)
    per = {c: [item.case for item in items] for c, items in per_item.items()}
    out = [
        "# Traceability",
        "",
        "This page is generated; do not edit it. It lists, for each construct of the "
        "[supported-construct matrix](supported-sva.md), the hand-derived trace cases "
        "(`tests/functional_tests/reference_semantics/fixtures`) that exercise it: the IEEE 1800 "
        "clauses they cite, how their verdicts are grounded, and which layers of "
        "[the validation](validation.md) run them. Regenerate it with "
        "`uv run python tests/support/traceability.py --write`; a test fails when it is stale.",
        "",
        f"{len(data)} cases in {len({i.case.file for i in data})} files. A case counts under "
        "every construct it contains. Reference only: the pipeline rejects the property "
        "(`support: oracle`, which includes every `must-reject` case), so only the reference "
        "evaluator runs it.",
        "",
        "## Summary",
        "",
        "| Construct | Cases | Normative | Interpretation | Implementation-defined "
        "| Reference only | Layers |",
        "|---|---|---|---|---|---|---|",
    ]
    for construct in Construct:
        cases = per[construct]
        classes = Counter(case.case_class for case in cases)
        reference_only = sum(1 for case in cases if case.oracle_only)
        out.append(
            f"| {_cell(construct.value)} | {len(cases)} "
            f"| {classes[CaseClass.NORMATIVE]} | {classes[CaseClass.INTERPRETATION]} "
            f"| {classes[CaseClass.IMPLEMENTATION_DEFINED]} | {reference_only} "
            f"| {', '.join(_layers(per_item[construct]))} |"
        )
    for construct in Construct:
        cases = per[construct]
        out += ["", f"## {construct.value}", "", "Matrix rows:"]
        out += [f"- {row}" for row in ROWS[construct]]
        clauses = Counter(clause for case in cases for clause in set(case.clause))
        out += [
            "",
            "Clauses cited: "
            + ", ".join(f"{c} ({clauses[c]})" for c in sorted(clauses, key=_clause_key)),
        ]
        ids = Counter(i for case in cases for i in set(case.interpretations))
        out.append(
            "Interpretations used: "
            + (
                ", ".join(f"{i} ({ids[i]})" for i in sorted(ids, key=lambda i: int(i[1:])))
                if ids
                else "none"
            )
        )
        out.append("Layers: " + ", ".join(_layers(per_item[construct])))
        accepted = [case for case in cases if not case.oracle_only]
        rejected = [case for case in cases if case.oracle_only]
        out += ["", f"Accepted cases ({len(accepted)}):", ""]
        out += _case_lists(accepted)
        if rejected:
            out += ["", f"Reference-only cases ({len(rejected)}):", ""]
            out += _case_lists(rejected)
    out += [
        "",
        "## Structural rows",
        "",
        "Matrix rows that a trace case cannot exercise (every trace case is one module-level "
        "`@(posedge clk)` directive): their evidence is the listed point of the area's contract.",
        "",
        "| Matrix row | Evidence |",
        "|---|---|",
    ]
    for row in sorted(STRUCTURAL):
        links = ", ".join(
            f"[{e.area} point {e.point}](../tests/functional_tests/{e.area}/CONTRACT.md)"
            for e in STRUCTURAL[row]
        )
        out.append(f"| {_cell(row)} | {links} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Print the page, or with ``--write`` rewrite it, or with ``--check`` verify it."""
    parser = argparse.ArgumentParser(description="Generate docs/traceability.md.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="rewrite docs/traceability.md")
    mode.add_argument("--check", action="store_true", help="exit 1 if the page differs")
    args = parser.parse_args(argv)
    page = render()
    if args.write:
        PAGE.write_text(page, encoding="utf-8", newline="\n")
    elif args.check:
        if not PAGE.exists() or PAGE.read_text(encoding="utf-8") != page:
            print("docs/traceability.md is stale: run tests/support/traceability.py --write")
            return 1
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stdout.write(page)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
