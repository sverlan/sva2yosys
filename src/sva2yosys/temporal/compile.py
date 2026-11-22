"""Compile an IR directive into its canonical :class:`~sva2yosys.temporal.machine.Machine`."""

from __future__ import annotations

from dataclasses import replace

from sva2yosys.ir.directive import Directive, DirectiveKind
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
    Until,
    conjuncts,
)
from sva2yosys.ir.sequence import TRUE, Delay, SeqConcat, Sequence
from sva2yosys.passes.first_sample import gated_samples
from sva2yosys.passes.validate import (
    has_unbounded_part,
    strong_unbounded_part,
    validation_problems,
)
from sva2yosys.temporal.attempts import AttemptMachine, build_attempt_machine
from sva2yosys.temporal.automaton import ConditionTable, build_automaton
from sva2yosys.temporal.bounds import obligation_slots, sequence_bounds
from sva2yosys.temporal.budgets import (
    DEFAULT_BUDGETS,
    DEFAULT_LIMITS,
    BudgetError,
    Budgets,
    ResourceLimits,
    Work,
    check_expressions,
    check_letter_budget,
    preflight,
)
from sva2yosys.temporal.configurations import ConfigMachine, configuration_sets, never_fails
from sva2yosys.temporal.liveness import Liveness, liveness_kind
from sva2yosys.temporal.lower import lower
from sva2yosys.temporal.machine import Machine, check_resources
from sva2yosys.temporal.nonvacuity import cover_problem

type Monitor = Machine | AttemptMachine | ConfigMachine
"""What a directive part compiles to: the canonical machine where it is exact, the
configuration-set machine for a weak unbounded consequent, else the
per-attempt machine."""


class CompileError(ValueError):
    """The directive is outside what the machine supports (the message says why)."""


def _resources(directive: Directive, limits: ResourceLimits) -> Work:
    try:
        work = preflight(directive.property, limits)
        if directive.disable is not None:
            check_expressions((directive.disable,), limits, work=work)
        return work
    except BudgetError as error:
        raise CompileError(str(error)) from error


def machine_exact(prop: Property, cover: bool) -> bool:
    """Whether the canonical :class:`Machine` decides the lowered property ``prop`` exactly.

    - ``assert`` / ``assume`` (``cover`` false): ``SequenceProperty``, ``Not`` of a
      ``SequenceProperty``, or an ``Implication`` whose consequent is one of those (the
      antecedent may be unbounded: the antecedent frontier is one merged letter
      set; validation rejects an unbounded consequent).
    - ``cover``: the same, but an implication only when its antecedent has a single match
      length (``sequence_bounds(...).min_length == .max_length``; an antecedent with no
      match counts as single-length). Validation accepts every other shape too (apart from
      the cover rule and the operand counts); those get the per-attempt machine.
    - ``nexttime`` / ``always`` / ``eventually`` are lowered to implications (possibly
      under ``not``) by :func:`~sva2yosys.temporal.lower.lower`, so they are judged by
      their lowered shape (``nexttime [n] a`` is canonical, ``always [m:n] a`` too for an
      ``assert``; a ``cover`` needs a single-length window). A node that survives lowering
      (invalid) is never exact.
    """
    if needs_configurations(prop, cover):
        return False
    match prop:
        case SequenceProperty() | Not(operand=SequenceProperty()):
            return True
        case Implication(antecedent=antecedent, consequent=consequent):
            if not isinstance(consequent, SequenceProperty) and not (
                isinstance(consequent, Not) and isinstance(consequent.operand, SequenceProperty)
            ):
                return False
            if not cover:
                return True
            bounds = sequence_bounds(antecedent)
            if bounds.unbounded:
                return True  # a cover of it is rejected by validation (never reaches here)
            return bounds.min_length == bounds.max_length
        case (
            Not() | PropAnd() | PropOr() | PropIf() | Nexttime() | Always() | Eventually() | Until()
        ):
            return False  # (the temporal nodes never survive lowering when valid)


def needs_configurations(prop: Property, cover: bool) -> bool:
    """Whether the lowered property ``prop`` is decided by the configuration-set machine
    (``docs/semantics.md`` §9.6, R2): a sequence property or an implication whose consequent
    is a sequence property, or ``not`` of one (the negated machine), with an
    unbounded part (a ``$`` range, goto or nonconsecutive repetition, structurally). A
    ``cover`` of one consumes the machine's
    ``success`` (validation has already restricted the cover to a strong sequence,
    or an implication with a single-length antecedent into one), so ``cover`` makes no
    difference here."""
    match prop:
        case (
            SequenceProperty(sequence=body)
            | Not(operand=SequenceProperty(sequence=body))
            | Implication(consequent=SequenceProperty(sequence=body))
            | Implication(consequent=Not(operand=SequenceProperty(sequence=body)))
        ):
            return has_unbounded_part(SequenceProperty(body))
        case _:
            return False


def compile_configuration_machine(
    directive: Directive,
    budgets: Budgets = DEFAULT_BUDGETS,
    *,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> ConfigMachine:
    """Build the configuration-set machine of ``directive`` (one part of
    :func:`monitor_directives`, accepted by validation).

    Raises:
        CompileError: validation reports a problem, the lowered property is not one for
            which :func:`needs_configurations` holds, the consequent can match empty, or a
            budget is exceeded (the letter estimate first, before any range is iterated;
            then ``budgets.configurations``), or a strong assumption needs several
            configurations (a witness would weaken the assumption).
    """
    work = _resources(directive, limits)
    try:
        check_letter_budget(directive.property, budgets)
    except BudgetError as error:
        raise CompileError(str(error).replace("per-attempt", "configuration-set")) from error
    problems = validation_problems(directive)
    if problems:
        raise CompileError("; ".join(problems))
    directive = replace(directive, property=lower(directive.property))
    if not needs_configurations(directive.property, directive.kind is DirectiveKind.COVER):
        raise CompileError("this directive has no unbounded consequent")
    antecedent, consequent, negated = operands(directive.property)
    table = ConditionTable()
    disable = table.identify(directive.disable) if directive.disable is not None else None
    try:
        antecedent_automaton = (
            build_automaton(antecedent, table, work=work) if antecedent is not None else None
        )
        if sequence_bounds(consequent).nullable:
            raise CompileError("a property sequence or consequent must not admit an empty match")
        consequent_automaton = build_automaton(consequent, table, work=work)
        found = configuration_sets(consequent_automaton, budgets.configurations, work=work)
    except BudgetError as error:
        raise CompileError(str(error)) from error
    except ValueError as error:
        raise CompileError(str(error)) from error
    conditions = tuple(table.conditions)
    return ConfigMachine(
        directive=directive,
        conditions=conditions,
        past_depth=tuple(gated_samples(c) for c in conditions),
        disable=disable,
        antecedent=antecedent_automaton,
        consequent=consequent_automaton,
        configurations=found,
        liveness=_liveness(directive, len(found)),
        negated=negated,
        limits=limits,
    )


ASSUME_LIVENESS = (
    "a strong unbounded property (liveness) in 'assume' is supported only when its "
    "obligations share one configuration (a fairness constraint); this one needs a witness, "
    "which picks one obligation, so as an assumption it would constrain only the obligation "
    "the solver chooses (docs/semantics.md section 9.6)"
)
"""The rejection of a strong ``assume`` part outside the pending-bit class."""


COVER_FAIRNESS_WARNING = (
    "this cover's result ignores the fairness assumptions (strong assume) of this run: "
    "a reached cover is provisional, see docs/usage.md"
)
"""The warning for every accepted ``cover`` of a run that also accepts an ``assume`` with a
liveness part: the ``cover`` task does not see the fairness constraint, so it can
reach a prefix that no fair trace extends."""


def is_fairness_assumption(monitor: Monitor) -> bool:
    """Whether ``monitor`` is an ``assume`` with a liveness part (a fairness constraint), the
    condition for :data:`COVER_FAIRNESS_WARNING`."""
    return (
        isinstance(monitor, ConfigMachine)
        and monitor.liveness is not None
        and monitor.directive.kind is DirectiveKind.ASSUME
    )


def _liveness(directive: Directive, configurations: int) -> Liveness | None:
    """The liveness kind of the lowered ``directive`` (one conjunct): ``None`` unless it is an
    ``assert`` or ``assume`` whose part is strong after negation
    (:func:`~sva2yosys.passes.validate.strong_unbounded_part`: a strong sequence, or ``not`` of
    a weak one; a ``cover`` consumes the machine's successes). For an ``assume`` the part must
    be in the pending-bit class (the assumption is the fairness constraint
    ``s_eventually !busy``, exact for one configuration).

    Raises:
        CompileError: an ``assume`` part that would need the witness (:data:`ASSUME_LIVENESS`).
    """
    if directive.kind is DirectiveKind.COVER or not strong_unbounded_part(directive.property):
        return None
    kind = liveness_kind(configurations)
    if directive.kind is DirectiveKind.ASSUME and kind is not Liveness.PENDING:
        raise CompileError(ASSUME_LIVENESS)
    return kind


def monitor_directives(directive: Directive) -> tuple[Directive, ...]:
    """The parts that get a monitor each: for ``assert`` / ``assume``
    :func:`conjunct_directives`; for ``cover`` the directive itself, unsplit, with its
    property as written."""
    if directive.kind is DirectiveKind.COVER:
        return (directive,)
    return conjunct_directives(directive)


def never_fails_warning(monitor: Monitor, part: int, parts: int) -> str | None:
    """The warning for a monitor that can provably never fail, else ``None``.

    Only a :class:`~sva2yosys.temporal.configurations.ConfigMachine` of an ``assert`` or
    ``assume`` without a liveness part (``liveness`` ``None``: a strong consequent can still
    fail its liveness check) can be warned about
    (:func:`~sva2yosys.temporal.configurations.never_fails`);
    a ``cover`` never fails, so it gets none. ``part`` is the 0-based index of the monitor
    among the ``parts`` of :func:`monitor_directives`: with several parts the message names
    the conjunct (``conjunct 2 of 3``). The message has no directive name or location (the
    caller adds them) and does not change the monitor.

    - ``assert``: ``this assert can never fail: its weak unbounded consequent always keeps
      a live match (a liveness check needs strong(...) or s_eventually, see docs/usage.md)``
      (for a property without antecedent: ``its weak unbounded sequence``).
    - ``assume``: ``this assume can never fail, so it constrains nothing: ...`` (same
      reason, ending ``(a fairness constraint needs a strong assume, see
      docs/usage.md)``).
    """
    if not isinstance(monitor, ConfigMachine) or not never_fails(monitor):
        return None
    if monitor.liveness is not None:
        return None  # it can still fail its liveness check
    kind = monitor.directive.kind
    if kind is DirectiveKind.COVER:
        return None
    subject = (
        f"conjunct {part + 1} of {parts} of this {kind.value}"
        if parts > 1
        else (f"this {kind.value}")
    )
    what = "consequent" if monitor.antecedent is not None else "sequence"
    reason = f"its weak unbounded {what} always keeps a live match"
    if kind is DirectiveKind.ASSUME:
        return (
            f"{subject} can never fail, so it constrains nothing: {reason} "
            "(a fairness constraint needs a strong assume, see docs/usage.md)"
        )
    return (
        f"{subject} can never fail: {reason} "
        "(a liveness check needs strong(...) or s_eventually, see docs/usage.md)"
    )


def compile_monitor(part: Directive, *, limits: ResourceLimits = DEFAULT_LIMITS) -> Monitor:
    """The monitor of one part of :func:`monitor_directives`: :func:`compile_directive` when
    :func:`machine_exact` holds for its lowered property, else
    :func:`compile_attempt_machine`."""
    _resources(part, limits)
    cover = part.kind is DirectiveKind.COVER
    lowered = lower(part.property)
    if needs_configurations(lowered, cover):
        return compile_configuration_machine(part, limits=limits)
    if machine_exact(lowered, cover):
        return compile_directive(part, limits=limits)
    return compile_attempt_machine(part, limits=limits)


def compile_monitors(
    directive: Directive, *, limits: ResourceLimits = DEFAULT_LIMITS
) -> tuple[Monitor, ...]:
    """Validate ``directive`` (all rules of
    :func:`~sva2yosys.passes.validate.validation_problems`), then compile each of
    :func:`monitor_directives` with :func:`compile_monitor`.

    Raises:
        CompileError: A rule is violated or a part cannot be compiled.
    """
    validate_directive(directive, limits=limits)
    return tuple(compile_monitor(part, limits=limits) for part in monitor_directives(directive))


def compile_attempt_machine(
    directive: Directive,
    budgets: Budgets = DEFAULT_BUDGETS,
    *,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> AttemptMachine:
    """Build the per-attempt machine of ``directive``.

    Raises:
        CompileError: ``validation_problems(directive, shapes=False)`` reports a problem (the
            structural rules, including the ``$past`` if/else rule); a
            property sequence or consequent admits an empty match at any depth of the
            property as written or lowered (nullable overlapping antecedents stay
            allowed); a ``cover`` fails the cover rule
            (:func:`~sva2yosys.temporal.nonvacuity.cover_problem`); a ``PropAnd`` / ``PropOr``
            has fewer than 2 operands; or a budget is exceeded
            (:class:`~sva2yosys.temporal.budgets.BudgetError`, message prefixed as for
            every compile error).
    """
    _resources(directive, limits)
    try:
        check_letter_budget(directive.property, budgets)
    except BudgetError as error:
        raise CompileError(str(error)) from error
    problems = validation_problems(directive, shapes=False)
    if problems:
        raise CompileError("; ".join(problems))
    if has_unbounded_part(directive.property):
        # (validation accepts the shapes the canonical machine decides; this one is bounded)
        raise CompileError("unbounded ranges ($) are not supported by the per-attempt machine")
    lowered = lower(directive.property)
    for written in (directive.property, lowered):
        if _has_nullable_sequence(written):
            raise CompileError("a property sequence or consequent must not admit an empty match")
        if _has_short_operator(written):
            raise CompileError("a property 'and' / 'or' needs at least two operands")
    if directive.kind is DirectiveKind.COVER:
        message = cover_problem(directive.property)
        if message is not None:
            raise CompileError(message)
    try:
        return build_attempt_machine(directive, budgets, limits=limits)
    except BudgetError as error:
        raise CompileError(str(error)) from error
    except ValueError as error:
        raise CompileError(str(error)) from error


def _has_nullable_sequence(prop: Property) -> bool:
    """Whether a property sequence (not an antecedent) anywhere in ``prop`` admits an empty
    match."""
    match prop:
        case SequenceProperty(sequence=sequence):
            return sequence_bounds(sequence).nullable
        case Until():
            return False
        case Implication(consequent=consequent):
            return _has_nullable_sequence(consequent)
        case (
            Not(operand=operand)
            | Nexttime(operand=operand)
            | Always(operand=operand)
            | Eventually(operand=operand)
        ):
            return _has_nullable_sequence(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return any(_has_nullable_sequence(operand) for operand in operands)
        case PropIf(then=then, otherwise=otherwise):
            return _has_nullable_sequence(then) or (
                otherwise is not None and _has_nullable_sequence(otherwise)
            )


def _has_short_operator(prop: Property) -> bool:
    """Whether a property ``and`` / ``or`` anywhere in ``prop`` has fewer than two operands."""
    match prop:
        case SequenceProperty() | Until():
            return False
        case Implication(consequent=consequent):
            return _has_short_operator(consequent)
        case (
            Not(operand=operand)
            | Nexttime(operand=operand)
            | Always(operand=operand)
            | Eventually(operand=operand)
        ):
            return _has_short_operator(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return len(operands) < 2 or any(_has_short_operator(o) for o in operands)
        case PropIf(then=then, otherwise=otherwise):
            return _has_short_operator(then) or (
                otherwise is not None and _has_short_operator(otherwise)
            )


def conjunct_directives(directive: Directive) -> tuple[Directive, ...]:
    """The directives whose monitors together check ``directive``: one per conjunct of its
    lowered property (:func:`~sva2yosys.temporal.lower.lower`) when that is a property
    ``and`` (:class:`~sva2yosys.ir.property.PropAnd`), else the directive with its
    lowered property.

    The conjunction fails exactly when a conjunct fails, so for ``assert`` and ``assume``
    (which consume failures up to and including the first) the separate monitors check
    the same traces. The conjunction's own ``success`` is not defined, so a ``cover`` is
    never split (:func:`monitor_directives`)."""
    return tuple(replace(directive, property=c) for c in conjuncts(lower(directive.property)))


def validate_directive(directive: Directive, *, limits: ResourceLimits = DEFAULT_LIMITS) -> None:
    """Raise :class:`CompileError` if ``directive`` violates a rule of
    :func:`~sva2yosys.passes.validate.validation_problems` (structural rules, the operand
    count of ``and`` / ``or``, and for a ``cover`` the cover rule)."""
    _resources(directive, limits)
    problems = validation_problems(directive)
    if problems:
        raise CompileError("; ".join(problems))


def compile_directive(directive: Directive, *, limits: ResourceLimits = DEFAULT_LIMITS) -> Machine:
    """Build the machine of ``directive``.

    - ``SequenceProperty(s)``: no antecedent; consequent ``s``.
    - ``Implication(a, SequenceProperty(c), overlapping)``: antecedent ``a`` and
      consequent ``c`` for ``|->``. For ``|=>`` (§16.12.7: ``a |=> p`` is
      ``(a ##1 1) |-> p``): antecedent ``a ##1 1`` and consequent ``c``, so the consequent
      starts at the next tick. ``|=>`` with an antecedent that admits an empty match is
      rejected by validation.
    - The ``disable iff`` condition is registered first in the condition table, then the
      antecedent's conditions, then the consequent's.
    - ``slots`` is :func:`~sva2yosys.temporal.bounds.obligation_slots` of the (normalised)
      consequent.
    - ``Not(SequenceProperty(s))`` and ``Implication(a, Not(SequenceProperty(c)), …)``:
      as without ``Not``, with ``negated`` set (an obligation fails where the sequence
      matches and succeeds where it can no longer match).
    - The directive kind does not change the machine: ``assert``/``assume`` consume its
      failures, ``cover`` its successes (each attempt counts once, at its first match).

    Raises:
        CompileError: The directive violates a rule of
            :func:`~sva2yosys.passes.validate.validation_problems` (clock form, unbounded
            range, nested sampled-value function, invalid select, first-sample problem, the
            cover rule); its lowered property is not one of the shapes above (a nested
            implication, ``not`` of a non-sequence, ``or``, ``if``, a property ``and``, which
            compiles to one machine per conjunct: :func:`compile_monitors` gives those the
            per-attempt machine); or its property sequence or consequent admits an empty
            match (Slang rejects it: ``SeqEmptyMatch``).
    """
    work = _resources(directive, limits)
    problems = validation_problems(directive)
    directive = replace(directive, property=lower(directive.property))
    if not problems and isinstance(directive.property, PropAnd):
        raise CompileError(
            "a property 'and' compiles to one machine per conjunct (conjunct_directives)"
        )
    if problems:
        raise CompileError("; ".join(problems))
    antecedent, consequent, negated = operands(directive.property)
    table = ConditionTable()
    disable = table.identify(directive.disable) if directive.disable is not None else None
    try:
        antecedent_automaton = (
            build_automaton(antecedent, table, work=work) if antecedent is not None else None
        )
        if sequence_bounds(consequent).nullable:
            raise CompileError("a property sequence or consequent must not admit an empty match")
        consequent_automaton = build_automaton(consequent, table, work=work)
        slots = obligation_slots(consequent)
    except ValueError as error:
        raise CompileError(str(error)) from error
    conditions = tuple(table.conditions)
    machine = Machine(
        directive=directive,
        conditions=conditions,
        past_depth=tuple(gated_samples(c) for c in conditions),
        disable=disable,
        antecedent=antecedent_automaton,
        consequent=consequent_automaton,
        slots=slots,
        negated=negated,
        limits=limits,
    )
    try:
        check_resources(machine, work)
    except BudgetError as error:
        raise CompileError(str(error)) from error
    return machine


def operands(prop: Property) -> tuple[Sequence | None, Sequence, bool]:
    """The normalised antecedent (``None`` for a sequence property) and consequent of the
    machine of ``prop``, and whether the consequent is negated (see
    :func:`compile_directive`)."""
    match lower(prop):
        case SequenceProperty(sequence=body):
            return None, body, False
        case Not(operand=SequenceProperty(sequence=body)):
            return None, body, True
        case Implication(antecedent=antecedent, consequent=nested, overlapping=overlapping):
            negated = isinstance(nested, Not)
            inner = nested.operand if isinstance(nested, Not) else nested
            if not isinstance(inner, SequenceProperty):
                raise CompileError("nested implications are not supported yet")
            if overlapping:
                return antecedent, inner.sequence, negated
            if sequence_bounds(antecedent).nullable:
                raise CompileError("'|=>' with an antecedent that can match empty is not supported")
            return SeqConcat(antecedent, Delay(1, 1), TRUE), inner.sequence, negated
        case PropAnd():
            raise CompileError(
                "a property 'and' compiles to one machine per conjunct (conjunct_directives)"
            )
        case Not():
            raise CompileError("'not' is supported only on a sequence (not s, a |-> not s)")
        case PropOr() | PropIf() | Nexttime() | Always() | Eventually() | Until():
            raise CompileError("this property operator is not supported yet")
