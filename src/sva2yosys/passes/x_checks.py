"""X-checks (``docs/semantics.md`` §8a): what is left of a directive once
``$isunknown`` is evaluated as 0.

The model is two-state: no value is X or Z, so ``$isunknown(e)`` (IEEE 1800 §20.9) is the
constant 0. The converter substitutes that constant, and this module decides, on the
converted IR, whether a directive still checks anything:

- :func:`fold_property` simplifies the Boolean expressions of a property with the
  constants that substitution created (and any other constant), without changing their
  value;
- :func:`never_fails` is a **sound** structural criterion: ``True`` only if the
  property holds on every trace (every finite or infinite word, whatever the design
  does). ``False`` means "not proved", never "can fail";
- :func:`never_succeeds` is the same for ``cover``: ``True`` only if the property has
  no (non-vacuous) success on any trace.

A directive for which the criterion proves that nothing can be observed is *skipped*;
otherwise it is accepted with a warning (still exact: the folded directive is the
directive in the two-state model). The criterion is conservative: a directive it cannot
decide stays a mixed directive, and a directive that could fail is never skipped.
"""

from __future__ import annotations

from dataclasses import replace

from sva2yosys.ir.expr import (
    Binary,
    BinaryOp,
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


def truth(expr: Expr) -> bool | None:
    """The truth value of a folded ``expr``: ``True``/``False`` when it is a constant
    (non-zero or zero), ``None`` otherwise."""
    if isinstance(expr, Const):
        return expr.value != 0
    return None


def _mask(value_type: ValueType) -> int:
    return (1 << value_type.width) - 1


def _signed_value(const: Const) -> int:
    """The integer a constant denotes (two's complement when its type is signed)."""
    if const.type.signed and const.value >> (const.type.width - 1):
        return const.value - (1 << const.type.width)
    return const.value


def _bit(flag: bool, value_type: ValueType) -> Const:
    return Const(int(flag), value_type)


def _unary(op: UnaryOp, operand: Expr, value_type: ValueType) -> Expr:
    if not isinstance(operand, Const):
        return Unary(op, operand, value_type)
    value, mask = operand.value, _mask(operand.type)
    modulus = 1 << value_type.width
    match op:
        case UnaryOp.LOGICAL_NOT:
            return _bit(value == 0, value_type)
        case UnaryOp.PLUS:
            return Const(_signed_value(operand) % modulus, value_type)
        case UnaryOp.MINUS:
            return Const(-_signed_value(operand) % modulus, value_type)
        case UnaryOp.BITWISE_NOT:
            return Const(~_signed_value(operand) % modulus, value_type)
        case UnaryOp.REDUCE_AND:
            return _bit(value == mask, value_type)
        case UnaryOp.REDUCE_NAND:
            return _bit(value != mask, value_type)
        case UnaryOp.REDUCE_OR:
            return _bit(value != 0, value_type)
        case UnaryOp.REDUCE_NOR:
            return _bit(value == 0, value_type)
        case UnaryOp.REDUCE_XOR:
            return _bit(value.bit_count() % 2 == 1, value_type)
        case UnaryOp.REDUCE_XNOR:
            return _bit(value.bit_count() % 2 == 0, value_type)


def _logical(op: BinaryOp, left: Expr, right: Expr, value_type: ValueType) -> Expr:
    """A logical operator (``&&``, ``||``, ``->``, ``<->``) with a folded constant operand
    short-circuited; ``Binary`` otherwise."""
    tl, tr = truth(left), truth(right)
    kept = Binary(op, left, right, value_type)

    def pass_through(operand: Expr) -> Expr:
        # the truth value of ``operand`` as the result: itself when it is already of the
        # result type, else the operator is kept
        return operand if operand.type == value_type else kept

    match op:
        case BinaryOp.LOGICAL_AND:
            if tl is False or tr is False:
                return _bit(False, value_type)
            if tl is True and tr is True:
                return _bit(True, value_type)
            if tl is True:
                return pass_through(right)
            if tr is True:
                return pass_through(left)
        case BinaryOp.LOGICAL_OR:
            if tl is True or tr is True:
                return _bit(True, value_type)
            if tl is False and tr is False:
                return _bit(False, value_type)
            if tl is False:
                return pass_through(right)
            if tr is False:
                return pass_through(left)
        case BinaryOp.LOGICAL_IMPLICATION:
            if tl is False or tr is True:
                return _bit(True, value_type)
            if tl is True and tr is False:
                return _bit(False, value_type)
            if tl is True:
                return pass_through(right)
            if tr is False:
                return Unary(UnaryOp.LOGICAL_NOT, left, value_type)
        case BinaryOp.LOGICAL_EQUIVALENCE:
            if tl is not None and tr is not None:
                return _bit(tl == tr, value_type)
            if tl is not None:
                return pass_through(right) if tl else Unary(UnaryOp.LOGICAL_NOT, right, value_type)
            if tr is not None:
                return pass_through(left) if tr else Unary(UnaryOp.LOGICAL_NOT, left, value_type)
        case _:
            raise AssertionError(op)
    return kept


def _bitwise(op: BinaryOp, left: Expr, right: Expr, value_type: ValueType) -> Expr:
    """``&``, ``|`` and ``^`` with an absorbing or neutral constant operand of the result
    type; ``Binary`` otherwise."""
    kept = Binary(op, left, right, value_type)
    ones = _mask(value_type)
    for const, other in ((left, right), (right, left)):
        if not isinstance(const, Const) or const.type != value_type or other.type != value_type:
            continue
        match op:
            case BinaryOp.BITWISE_AND:
                if const.value == 0:
                    return Const(0, value_type)
                if const.value == ones:
                    return other
            case BinaryOp.BITWISE_OR:
                if const.value == ones:
                    return Const(ones, value_type)
                if const.value == 0:
                    return other
            case BinaryOp.BITWISE_XOR:
                if const.value == 0:
                    return other
            case _:
                pass
    return kept


_LOGICAL = (
    BinaryOp.LOGICAL_AND,
    BinaryOp.LOGICAL_OR,
    BinaryOp.LOGICAL_IMPLICATION,
    BinaryOp.LOGICAL_EQUIVALENCE,
)
_BITWISE = (BinaryOp.BITWISE_AND, BinaryOp.BITWISE_OR, BinaryOp.BITWISE_XOR)


def fold_expr(expr: Expr) -> Expr:
    """``expr`` with every constant subexpression of the supported forms replaced by its
    value (same type), and Boolean operators short-circuited by a constant operand
    (``0 && x`` is 0, ``1 || x`` is 1, ``c ? x : y`` is the selected branch). The value of
    the expression is unchanged on every input; shapes that are not recognised are kept
    as they are."""
    match expr:
        case Signal() | Const() | FirstSample():
            return expr
        case Sampled(operand=operand):
            return replace(expr, operand=fold_expr(operand))
        case Unary(op=op, operand=operand, type=value_type):
            return _unary(op, fold_expr(operand), value_type)
        case Binary(op=op, left=left, right=right, type=value_type):
            folded_left, folded_right = fold_expr(left), fold_expr(right)
            if op in _LOGICAL:
                return _logical(op, folded_left, folded_right, value_type)
            if op in _BITWISE:
                return _bitwise(op, folded_left, folded_right, value_type)
            return Binary(op, folded_left, folded_right, value_type)
        case Conditional(condition=condition, then=then, otherwise=otherwise, type=value_type):
            folded = fold_expr(condition)
            chosen = truth(folded)
            if chosen is None:
                return Conditional(folded, fold_expr(then), fold_expr(otherwise), value_type)
            selected = fold_expr(then if chosen else otherwise)
            return selected if selected.type == value_type else Convert(selected, value_type)
        case Convert(operand=operand, type=value_type):
            folded = fold_expr(operand)
            if isinstance(folded, Const):
                return Const(_signed_value(folded) % (1 << value_type.width), value_type)
            return Convert(folded, value_type)
        case BitSelect(value=value, index=index, type=value_type):
            folded = fold_expr(value)
            if isinstance(folded, Const):
                return Const((folded.value >> index) & _mask(value_type), value_type)
            return BitSelect(folded, index, value_type)
        case RangeSelect(value=value, msb=msb, lsb=lsb, type=value_type):
            folded = fold_expr(value)
            if isinstance(folded, Const):
                return Const((folded.value >> lsb) & _mask(value_type), value_type)
            return RangeSelect(folded, msb, lsb, value_type)
        case Concat(parts=parts, type=value_type):
            folded_parts = tuple(fold_expr(part) for part in parts)
            consts = [part for part in folded_parts if isinstance(part, Const)]
            if len(consts) == len(folded_parts):
                number = 0
                for const in consts:
                    number = (number << const.type.width) | const.value
                return Const(number & _mask(value_type), value_type)
            return Concat(folded_parts, value_type)
        case Replicate(count=count, value=value, type=value_type):
            folded = fold_expr(value)
            if isinstance(folded, Const):
                number = 0
                for _ in range(count):
                    number = (number << folded.type.width) | folded.value
                return Const(number & _mask(value_type), value_type)
            return Replicate(count, folded, value_type)


def _fold_sequence(sequence: Sequence) -> Sequence:
    match sequence:
        case Bool(expr=expr):
            return Bool(fold_expr(expr))
        case SeqConcat(left=left, delay=delay, right=right):
            return SeqConcat(_fold_sequence(left), delay, _fold_sequence(right))
        case Repeat(sequence=inner, minimum=minimum, maximum=maximum):
            return Repeat(_fold_sequence(inner), minimum, maximum)
        case SeqOr(left=left, right=right):
            return SeqOr(_fold_sequence(left), _fold_sequence(right))
        case SeqAnd(left=left, right=right):
            return SeqAnd(_fold_sequence(left), _fold_sequence(right))
        case SeqIntersect(left=left, right=right):
            return SeqIntersect(_fold_sequence(left), _fold_sequence(right))
        case Throughout(condition=condition, sequence=inner):
            return Throughout(fold_expr(condition), _fold_sequence(inner))
        case Within(inner=inner, outer=outer):
            return Within(_fold_sequence(inner), _fold_sequence(outer))
        case Goto(condition=condition, minimum=minimum, maximum=maximum):
            return Goto(fold_expr(condition), minimum, maximum)
        case NonConsecutive(condition=condition, minimum=minimum, maximum=maximum):
            return NonConsecutive(fold_expr(condition), minimum, maximum)


def fold_property(prop: Property) -> Property:
    """``prop`` with :func:`fold_expr` applied to every expression in it (Boolean
    sequence elements, conditions, ``until`` operands, ``if`` conditions)."""
    match prop:
        case SequenceProperty(sequence=sequence, strength=strength):
            return SequenceProperty(_fold_sequence(sequence), strength)
        case Implication(antecedent=antecedent, consequent=consequent, overlapping=overlapping):
            return Implication(_fold_sequence(antecedent), fold_property(consequent), overlapping)
        case Not(operand=operand):
            return Not(fold_property(operand))
        case PropAnd(operands=operands):
            return PropAnd(tuple(fold_property(operand) for operand in operands))
        case PropOr(operands=operands):
            return PropOr(tuple(fold_property(operand) for operand in operands))
        case PropIf(condition=condition, then=then, otherwise=otherwise):
            return PropIf(
                fold_expr(condition),
                fold_property(then),
                None if otherwise is None else fold_property(otherwise),
            )
        case Nexttime(count=count, strong=strong, operand=operand):
            return Nexttime(count, strong, fold_property(operand))
        case Always(low=low, high=high, strong=strong, operand=operand):
            return Always(low, high, strong, fold_property(operand))
        case Eventually(low=low, high=high, strong=strong, operand=operand):
            return Eventually(low, high, strong, fold_property(operand))
        case Until(left=left, right=right, strong=strong, inclusive=inclusive):
            return Until(fold_expr(left), fold_expr(right), strong, inclusive)


def never_matches(sequence: Sequence) -> bool:
    """``True`` only if ``sequence`` has no match on any trace (a folded sequence)."""
    match sequence:
        case Bool(expr=expr):
            return truth(expr) is False
        case SeqConcat(left=left, right=right):
            return never_matches(left) or never_matches(right)
        case Repeat(sequence=inner, minimum=minimum):
            # [*0:n] also matches the empty sequence
            return minimum >= 1 and never_matches(inner)
        case SeqOr(left=left, right=right):
            return never_matches(left) and never_matches(right)
        case SeqAnd(left=left, right=right) | SeqIntersect(left=left, right=right):
            return never_matches(left) or never_matches(right)
        case Within(inner=inner, outer=outer):
            return never_matches(inner) or never_matches(outer)
        case Throughout(condition=condition, sequence=inner):
            # an empty match of the operand needs no sample of the condition
            return never_matches(inner) or (truth(condition) is False and not nullable(inner))
        case Goto(condition=condition, minimum=minimum):
            return minimum >= 1 and truth(condition) is False
        case NonConsecutive(condition=condition, minimum=minimum):
            return minimum >= 1 and truth(condition) is False


def nullable(sequence: Sequence) -> bool:
    """``True`` if ``sequence`` may admit an empty match (conservative: ``True`` when
    unsure)."""
    match sequence:
        case Bool():
            return False
        case SeqConcat(left=left, right=right) | SeqAnd(left=left, right=right):
            return nullable(left) and nullable(right)
        case SeqIntersect(left=left, right=right):
            return nullable(left) and nullable(right)
        case Repeat(sequence=inner, minimum=minimum):
            return minimum == 0 or nullable(inner)
        case SeqOr(left=left, right=right):
            return nullable(left) or nullable(right)
        case Throughout(sequence=inner):
            return nullable(inner)
        case Within() | Goto() | NonConsecutive():
            return True


def _always_matches(sequence: Sequence) -> bool:
    """``True`` only if no letter of ``sequence`` can refute it: from any start sample it
    matches on every trace long enough for its delays."""
    match sequence:
        case Bool(expr=expr):
            return truth(expr) is True
        case SeqConcat(left=left, right=right) | SeqAnd(left=left, right=right):
            return _always_matches(left) and _always_matches(right)
        case Repeat(sequence=inner, minimum=minimum):
            return minimum >= 1 and _always_matches(inner)
        case SeqOr(left=left, right=right):
            return _always_matches(left) or _always_matches(right)
        case SeqIntersect() | Within() | Throughout() | Goto() | NonConsecutive():
            return False


def _false_at_once(prop: Property) -> bool:
    """``True`` only if ``prop`` is false at every start sample, decided at that very
    sample: a sequence property of a constant-false Boolean."""
    match prop:
        case SequenceProperty(sequence=Bool(expr=expr)):
            return truth(expr) is False
        case (
            SequenceProperty()
            | Implication()
            | Not()
            | PropAnd()
            | PropOr()
            | PropIf()
            | Nexttime()
            | Always()
            | Eventually()
            | Until()
        ):
            return False


def never_fails(prop: Property) -> bool:
    """``True`` only if ``prop`` (folded) cannot fail in an ``assert``/``assume`` on any
    trace. The criterion, by property form:

    - a sequence property: its sequence is built of constant-true Booleans (``##n``
      delays, repetitions with at least one, ``and``/``or``) and the strength is not
      strong;
    - ``a |-> c`` / ``a |=> c``: the antecedent never matches, or ``c`` never fails;
    - ``p and q``: both never fail; ``p or q``: one of them never fails;
    - ``not p``: ``p`` is a sequence property of a constant-false Boolean;
    - ``if (e) p else q``: ``e`` is constant and the selected branch never fails, or
      both branches never fail (a missing ``else`` never fails);
    - weak ``nexttime``/``always``/``eventually``: the operand never fails;
    - weak ``until``/``until_with``: the left operand is constant true; (non-inclusive)
      the right operand is constant true.

    Everything else, including every strong form, is not proved."""
    match prop:
        case SequenceProperty(sequence=sequence, strength=strength):
            return strength is not Strength.STRONG and _always_matches(sequence)
        case Implication(antecedent=antecedent, consequent=consequent):
            return never_matches(antecedent) or never_fails(consequent)
        case PropAnd(operands=operands):
            return all(never_fails(operand) for operand in operands)
        case PropOr(operands=operands):
            return any(never_fails(operand) for operand in operands)
        case Not(operand=operand):
            return _false_at_once(operand)
        case PropIf(condition=condition, then=then, otherwise=otherwise):
            chosen = truth(condition)
            if chosen is True:
                return never_fails(then)
            if chosen is False:
                return otherwise is None or never_fails(otherwise)
            return never_fails(then) and (otherwise is None or never_fails(otherwise))
        case Nexttime(strong=strong, operand=operand):
            return not strong and never_fails(operand)
        case Always(strong=strong, operand=operand):
            return not strong and never_fails(operand)
        case Eventually(strong=strong, operand=operand):
            return not strong and never_fails(operand)
        case Until(left=left, right=right, strong=strong, inclusive=inclusive):
            if strong:
                return False
            return truth(left) is True or (not inclusive and truth(right) is True)


def _never_true(prop: Property) -> bool:
    """``True`` only if ``prop`` (folded) is never true, vacuously or not: a sequence
    property that is not weak whose sequence never matches (a weak one could stay pending
    for ever), ``and`` with such an operand, ``or`` of such operands."""
    match prop:
        case SequenceProperty(sequence=sequence, strength=strength):
            return strength is not Strength.WEAK and never_matches(sequence)
        case PropAnd(operands=operands):
            return any(_never_true(operand) for operand in operands)
        case PropOr(operands=operands):
            return all(_never_true(operand) for operand in operands)
        case Implication() | Not() | PropIf() | Nexttime() | Always() | Eventually() | Until():
            return False


def _never_nonvacuous(prop: Property) -> bool:
    """``True`` only if no attempt of ``prop`` (folded) is nonvacuous (§16.14.8, rules of
    ``docs/semantics.md`` §9.2): an implication whose antecedent never matches or whose
    consequent is never nonvacuous; ``not``, ``nexttime``, ``always`` and ``eventually``
    of such an operand; ``and``/``or`` of such operands; ``if`` whose possible branches are
    such (a missing ``else`` is vacuous). Sequences and ``until`` are always nonvacuous."""
    match prop:
        case SequenceProperty() | Until():
            return False
        case Implication(antecedent=antecedent, consequent=consequent):
            return never_matches(antecedent) or _never_nonvacuous(consequent)
        case (
            Not(operand=operand)
            | Nexttime(operand=operand)
            | Always(operand=operand)
            | Eventually(operand=operand)
        ):
            return _never_nonvacuous(operand)
        case PropAnd(operands=operands) | PropOr(operands=operands):
            return all(_never_nonvacuous(operand) for operand in operands)
        case PropIf(condition=condition, then=then, otherwise=otherwise):
            chosen = truth(condition)
            then_vacuous = chosen is False or _never_nonvacuous(then)
            else_vacuous = chosen is True or otherwise is None or _never_nonvacuous(otherwise)
            return then_vacuous and else_vacuous


def never_succeeds(prop: Property) -> bool:
    """``True`` only if no attempt of ``prop`` (folded) is both true and nonvacuous, on
    any trace (what a ``cover`` counts is such an attempt, so it can never be covered).
    Nonvacuity follows §16.14.8: ``p and q`` and ``p or q`` are nonvacuous when either
    operand is, so a vacuously true operand does not stop them from succeeding
    (``(0 |-> a) and b`` succeeds whenever ``b`` holds). The criterion:

    - the property is never true (:func:`_never_true`) or never nonvacuous
      (:func:`_never_nonvacuous`);
    - an implication whose consequent never succeeds (a true, nonvacuous implication
      has a nonvacuous consequent attempt, and all of them are true);
    - ``p and q`` whose operands all never succeed (a true, nonvacuous ``and`` has a
      true, nonvacuous operand);
    - ``if (e) p else q`` whose possible branches never succeed (a missing ``else`` is
      vacuous).

    Everything else is not proved."""
    if _never_true(prop) or _never_nonvacuous(prop):
        return True
    match prop:
        case Implication(antecedent=antecedent, consequent=consequent):
            return never_matches(antecedent) or never_succeeds(consequent)
        case PropAnd(operands=operands):
            return all(never_succeeds(operand) for operand in operands)
        case PropIf(condition=condition, then=then, otherwise=otherwise):
            chosen = truth(condition)
            then_never = chosen is False or never_succeeds(then)
            else_never = chosen is True or otherwise is None or never_succeeds(otherwise)
            return then_never and else_never
        case SequenceProperty() | Not() | PropOr() | Nexttime() | Always() | Eventually() | Until():
            return False
