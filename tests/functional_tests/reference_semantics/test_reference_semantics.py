"""Layer 1: the reference evaluator against the normative trace tables (CONTRACT.md)."""

from __future__ import annotations

from pathlib import Path

import pytest

from support.reference.evaluate import (
    AttemptResult,
    Evaluation,
    Outcome,
    Verdict,
    attempt_results,
    evaluate,
    evaluate_attempts,
    finalize,
    finalize_attempts,
)
from support.reference.lasso import Lasso, lasso_attempt_results
from support.reference.trace import Trace
from support.tracefile import TraceCase, load_cases, sv_source
from sva2yosys.ir.directive import Directive
from sva2yosys.ir.property import Not, SequenceProperty
from sva2yosys.passes.first_sample import first_sample_problems
from sva2yosys.passes.validate import validation_problems
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MODULE = "tc"
CASES = [case for path in sorted(FIXTURES.glob("*.trace")) for case in load_cases(path)]


def _trace(case: TraceCase) -> Trace:
    return Trace(tuple({f"{MODULE}.{name}": v for name, v in row.items()} for row in case.samples))


def test_fixtures_are_present() -> None:
    assert len(CASES) >= 45
    assert any(c.attempt_scope for c in CASES)
    assert len([c for c in CASES if c.loop is not None]) >= 25


@pytest.mark.parametrize("case", CASES, ids=[f"{c.file}::{c.name}" for c in CASES])
def test_trace_table(case: TraceCase) -> None:
    doc = export_texts([(f"{case.name}.sv", sv_source(case, MODULE))], SlangOptions(top=(MODULE,)))
    conversion = convert(doc)
    assert conversion.rejections == ()
    assert len(conversion.directives) == 1
    directive = conversion.directives[0]
    if case.rejected_by is not None:
        # a declared rejection (any rule, the first-sample rule included)
        assert case.rejected_by in "; ".join(validation_problems(directive)), "declared rejection"
    else:
        assert first_sample_problems(directive) == ()
    if case.oracle_only:
        assert validation_problems(directive), "the pipeline must still reject this property"
    else:
        assert validation_problems(directive) == (), "the pipeline must accept this property"
    if case.attempt_scope:
        _check_attempt_table(case, directive)
        return

    result = evaluate(directive.property, _trace(case), directive.disable)

    if case.first_failure_only:
        # A property 'and': exact up to and including the first failure, at the attempt level
        # too (the earliest decided failing attempt), and no success is defined.
        assert set(result.failures[:1]) == case.fails, "first failure"
        decided = [a.decided for a in result.attempts if a.verdict is Verdict.FAIL]
        assert min((d for d in decided if d is not None), default=None) == min(
            case.fails, default=None
        ), "earliest failing attempt"
        _cross_check_attempts(case, directive, result)
        return
    if case.kind != "cover":
        assert set(result.failures) == case.fails, "failures"
    assert set(result.successes) == case.passes, "successes"
    if case.pending is not None:
        pending = {
            obligation.start
            for attempt in result.attempts
            for obligation in attempt.obligations
            if obligation.verdict is Verdict.PENDING
        }
        assert len(pending) == case.pending, "pending obligation starts"
    if case.final_fail is not None:
        final = finalize(directive.property, result, case.kind == "cover")
        assert len(final.end_failures) == case.final_fail, "obligation starts failing at the end"
    _cross_check_attempts(case, directive, result)


def _token(attempt: AttemptResult) -> str:
    """The ``outcome`` row token of an attempt (see the tracefile docstring)."""
    match attempt.outcome:
        case Outcome.PENDING:
            return "p"
        case Outcome.DISABLED:
            return "d"
        case Outcome.TRUE:
            return {True: "T", False: "t", None: "U"}[attempt.nonvacuous]
        case Outcome.FALSE:
            return {True: "F", False: "f", None: "X"}[attempt.nonvacuous]


def _check_attempt_table(case: TraceCase, directive: Directive) -> None:
    """An attempt-scope case: per-attempt rows, fail/pass, pending and final-fail."""
    trace = _trace(case)
    cover = case.kind == "cover"
    result = evaluate_attempts(directive.property, trace, directive.disable)
    assert result.attempts == attempt_results(directive.property, trace, directive.disable)
    if case.outcomes is not None:
        assert tuple(_token(a) for a in result.attempts) == case.outcomes, "outcome row"
    if case.decisions is not None:
        assert tuple(a.decided for a in result.attempts) == case.decisions, "decided row"
    if not cover:
        assert set(result.failures) == case.fails, "failures"
    assert set(result.successes) == case.passes, "successes"
    pending = {a.start for a in result.attempts if a.outcome is Outcome.PENDING}
    if case.pending is not None:
        assert len(pending) == case.pending, "pending attempts"
    if case.final_fail is not None:
        final = finalize_attempts(directive.property, trace, directive.disable, cover)
        failing = {s for s, outcome in enumerate(final) if outcome is Outcome.FALSE}
        assert len(pending & failing) == case.final_fail, "pending attempts failing at the end"
    if case.loop is not None:
        _check_lasso(case, directive)


_LASSO_TOKENS = {Outcome.TRUE: "T", Outcome.FALSE: "F", Outcome.DISABLED: "D"}


def _check_lasso(case: TraceCase, directive: Directive) -> None:
    """The infinite-trace rows of a lasso case, and the consistency of the infinite results
    with the finite evaluation of the written samples (a decision made on a finite prefix is
    the infinite trace's decision)."""
    assert case.loop is not None
    lasso = Lasso(_trace(case).samples, case.loop)
    cover = case.kind == "cover"
    infinite = lasso_attempt_results(directive.property, lasso, directive.disable, cover)
    if case.inf_outcomes is not None:
        assert tuple(_LASSO_TOKENS[r.outcome] for r in infinite) == case.inf_outcomes, "inf-outcome"
    if case.inf_decisions is not None:
        assert tuple(r.decided for r in infinite) == case.inf_decisions, "inf-decided"
    finite = attempt_results(directive.property, _trace(case), directive.disable)
    for online, result in zip(finite, infinite, strict=True):
        if online.outcome is Outcome.PENDING:
            continue
        expected = {Outcome.TRUE: Outcome.TRUE, Outcome.FALSE: Outcome.FALSE}
        assert result.outcome is expected.get(online.outcome, Outcome.DISABLED), "finite agrees"
        assert result.decided == online.decided, "finite decision agrees"


def _cross_check_attempts(case: TraceCase, directive: Directive, obligations: Evaluation) -> None:
    """The per-attempt oracle agrees with the obligation-level one where both are defined:
    the first failure (assert/assume), the whole failure set of a (negated) sequence
    property, the successes of a cover."""
    attempts = evaluate_attempts(directive.property, _trace(case), directive.disable)
    if case.kind == "cover":
        assert attempts.successes == obligations.successes, "attempt oracle: successes"
        return
    assert attempts.failures[:1] == obligations.failures[:1], "attempt oracle: first failure"
    prop = directive.property
    if isinstance(prop, SequenceProperty) or (
        isinstance(prop, Not) and isinstance(prop.operand, SequenceProperty)
    ):
        assert attempts.failures == obligations.failures, "attempt oracle: failures"
