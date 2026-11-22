"""Unit contract of ``sva2yosys.temporal.netlist``: folding, hash-consing, evaluation and the
sweep of unused registers (module docstring)."""

from __future__ import annotations

import pytest

from sva2yosys.temporal.netlist import (
    AgeOutputs,
    Gate,
    NAnd,
    NConst,
    Netlist,
    NetlistBuilder,
    NInput,
    NNot,
    NOr,
    NReg,
    evaluate,
    next_state,
)


def _builder() -> NetlistBuilder:
    return NetlistBuilder()


# --- folding ---------------------------------------------------------------------------------


def test_and_with_a_false_operand_is_false() -> None:
    b = _builder()
    x = b.input(0)
    assert b.and_((x, b.const(False))) == b.const(False)


def test_and_drops_true_operands() -> None:
    b = _builder()
    x, y = b.input(0), b.input(1)
    assert b.and_((x, b.const(True))) == x
    assert b.and_((x, b.const(True), y)) == b.and_((x, y))


def test_or_with_a_true_operand_is_true() -> None:
    b = _builder()
    x = b.input(0)
    assert b.or_((x, b.const(True))) == b.const(True)


def test_or_drops_false_operands() -> None:
    b = _builder()
    x, y = b.input(0), b.input(1)
    assert b.or_((x, b.const(False))) == x
    assert b.or_((x, b.const(False), y)) == b.or_((x, y))


def test_empty_and_is_true_and_empty_or_is_false() -> None:
    b = _builder()
    assert b.and_(()) == b.const(True)
    assert b.or_(()) == b.const(False)


def test_single_operand_is_returned_itself() -> None:
    b = _builder()
    x = b.input(0)
    assert b.and_((x,)) == x
    assert b.or_((x,)) == x
    assert b.and_((x, x)) == x
    assert b.or_((x, x, x)) == x


def test_double_negation_cancels() -> None:
    b = _builder()
    x = b.input(0)
    assert b.not_(b.not_(x)) == x


def test_negation_of_a_constant_is_a_constant() -> None:
    b = _builder()
    assert b.not_(b.const(True)) == b.const(False)
    assert b.not_(b.const(False)) == b.const(True)


def test_a_variable_and_its_negation() -> None:
    b = _builder()
    x, y = b.input(0), b.input(1)
    assert b.and_((x, b.not_(x))) == b.const(False)
    assert b.or_((x, b.not_(x))) == b.const(True)
    assert b.and_((x, y, b.not_(x))) == b.const(False)
    assert b.or_((x, y, b.not_(y))) == b.const(True)


def test_nested_gates_of_the_same_kind_are_flattened() -> None:
    b = _builder()
    x, y, z = b.input(0), b.input(1), b.input(2)
    assert b.and_((b.and_((x, y)), z)) == b.and_((x, y, z))
    assert b.or_((b.or_((x, y)), z)) == b.or_((x, y, z))


def test_duplicates_are_removed_and_order_does_not_matter() -> None:
    b = _builder()
    x, y = b.input(0), b.input(1)
    assert b.and_((x, y, x)) == b.and_((y, x))
    assert b.or_((y, x, y)) == b.or_((x, y))


def test_and_and_or_are_different_gates() -> None:
    b = _builder()
    x, y = b.input(0), b.input(1)
    assert b.and_((x, y)) != b.or_((x, y))


# --- hash-consing ----------------------------------------------------------------------------


def test_equal_gates_are_one_gate_and_create_nothing_new() -> None:
    b = _builder()
    x, y = b.input(0), b.input(1)
    first = b.and_((x, y))
    count = b.gate_count
    assert b.and_((y, x)) == first
    assert b.input(0) == x
    assert b.not_(x) == b.not_(x)
    assert b.gate_count == count + 1  # only the one negation is new
    assert b.const(True) == b.const(True)


def test_distinct_inputs_are_distinct_gates() -> None:
    b = _builder()
    assert b.input(0) != b.input(1)
    assert b.const(True) != b.const(False)


def test_gate_callback_is_called_once_per_new_gate() -> None:
    calls: list[None] = []
    b = NetlistBuilder(on_gate=lambda: calls.append(None))
    x, y = b.input(0), b.input(1)
    b.and_((x, y))
    b.and_((y, x))
    b.or_((x, b.not_(y)))
    assert len(calls) == b.gate_count


def test_register_callback_is_called_once_per_register() -> None:
    registers: list[None] = []
    b = NetlistBuilder(on_register=lambda: registers.append(None))
    x = b.input(0)
    b.add_register(x, 1, (), "letter", 0)
    b.add_register(x, 2, (0,), "done", 1)
    assert len(registers) == 2


def test_add_register_returns_consecutive_indices() -> None:
    b = _builder()
    x = b.input(0)
    assert [b.add_register(x, 1, (), "letter", i) for i in range(3)] == [0, 1, 2]


# --- evaluation ------------------------------------------------------------------------------


def _shift_register() -> Netlist:
    """Two registers in a chain fed by condition 0; ``fail`` is the second one's value."""
    b = _builder()
    i = b.input(0)
    first = b.add_register(i, 1, (), "letter", 0)
    value0 = b.register_value(first)
    second = b.add_register(value0, 2, (), "letter", 1)
    value1 = b.register_value(second)
    zero = b.const(False)
    outputs = (AgeOutputs(zero, zero, zero, zero),)
    return b.finish(outputs, fail=value1, success=zero, overflow=zero)


def _run(netlist: Netlist, inputs: list[bool]) -> list[bool]:
    """The ``fail`` output at every sample."""
    state = (False,) * len(netlist.registers)
    fails: list[bool] = []
    for sample in inputs:

        def level(condition: int, sample: bool = sample) -> bool:
            return sample

        values = evaluate(netlist, state, level)
        fails.append(values[netlist.fail])
        state = next_state(netlist, values)
        assert len(state) == len(netlist.registers)
    return fails


def test_shift_register_delays_the_input_by_two_samples() -> None:
    netlist = _shift_register()
    assert len(netlist.registers) == 2
    assert _run(netlist, [True, False, False, False]) == [False, False, True, False]
    assert _run(netlist, [True, True, False, True, False]) == [False, False, True, True, False]


def test_registers_start_false() -> None:
    netlist = _shift_register()
    values = evaluate(netlist, (False, False), lambda condition: True)
    assert values[netlist.fail] is False


def test_inputs_are_asked_for_at_most_once_per_gate() -> None:
    b = _builder()
    x, y = b.input(0), b.input(1)
    zero = b.const(False)
    gate = b.and_((x, b.not_(y)))
    netlist = b.finish((AgeOutputs(zero, zero, zero, zero),), gate, gate, zero)
    asked: list[int] = []

    def inputs(condition: int) -> bool:
        asked.append(condition)
        return condition == 0

    values = evaluate(netlist, (), inputs)
    assert values[netlist.fail] is True
    assert sorted(asked) == sorted(set(asked))


def test_gate_values_follow_the_gate_kinds() -> None:
    b = _builder()
    x, y = b.input(0), b.input(1)
    zero = b.const(False)
    land, lor, lnot = b.and_((x, y)), b.or_((x, y)), b.not_(x)
    netlist = b.finish((AgeOutputs(land, lor, lnot, zero),), land, lor, zero)
    for left in (False, True):
        for right in (False, True):
            levels = (left, right)

            def level(condition: int, levels: tuple[bool, bool] = levels) -> bool:
                return levels[condition]

            values = evaluate(netlist, (), level)
            age = netlist.ages[0]
            assert values[age.true] is (left and right)
            assert values[age.false] is (left or right)
            assert values[age.nonvacuous] is (not left)


# --- finish ----------------------------------------------------------------------------------


def _references(gates: tuple[Gate, ...], index: int) -> tuple[int, ...]:
    match gates[index]:
        case NNot(operand=operand):
            return (operand,)
        case NAnd(operands=operands) | NOr(operands=operands):
            return operands
        case NConst() | NInput() | NReg():
            return ()


def test_registers_outside_the_cone_of_the_outputs_are_dropped() -> None:
    b = _builder()
    i = b.input(0)
    used = b.add_register(i, 1, (0,), "letter", 0)
    chained = b.add_register(b.register_value(used), 2, (0,), "letter", 1)
    b.add_register(i, 1, (1,), "letter", 0)  # nothing reads it
    zero = b.const(False)
    netlist = b.finish(
        (AgeOutputs(zero, zero, zero, zero),),
        fail=b.register_value(chained),
        success=zero,
        overflow=zero,
    )
    assert len(netlist.registers) == 2
    assert [(r.age, r.path, r.index) for r in netlist.registers] == [(1, (0,), 0), (2, (0,), 1)]
    assert _run(netlist, [True, False, False]) == [False, False, True]


def test_a_register_read_only_through_another_registers_next_is_kept() -> None:
    b = _builder()
    i = b.input(0)
    first = b.add_register(i, 1, (), "letter", 0)
    second = b.add_register(b.register_value(first), 2, (), "letter", 1)
    zero = b.const(False)
    netlist = b.finish(
        (AgeOutputs(zero, zero, zero, zero),),
        fail=b.register_value(second),
        success=zero,
        overflow=zero,
    )
    assert len(netlist.registers) == 2


def test_each_output_keeps_its_own_cone() -> None:
    b = _builder()
    i = b.input(0)
    for_success = b.add_register(i, 1, (), "acc", 0)
    for_overflow = b.add_register(i, 1, (1,), "acc", 0)
    for_age = b.add_register(i, 1, (2,), "acc", 0)
    zero = b.const(False)
    netlist = b.finish(
        (AgeOutputs(b.register_value(for_age), zero, zero, zero),),
        fail=zero,
        success=b.register_value(for_success),
        overflow=b.register_value(for_overflow),
    )
    assert len(netlist.registers) == 3


def test_gates_stay_topological_after_the_sweep() -> None:
    b = _builder()
    i, j = b.input(0), b.input(1)
    b.add_register(i, 1, (9,), "letter", 0)  # dropped
    keep = b.add_register(b.and_((i, j)), 1, (), "letter", 0)
    value = b.register_value(keep)
    out = b.or_((value, b.not_(j)))
    zero = b.const(False)
    netlist = b.finish((AgeOutputs(out, zero, zero, zero),), out, zero, zero)
    for index in range(len(netlist.gates)):
        assert all(ref < index for ref in _references(netlist.gates, index))
    for gate in netlist.gates:
        if isinstance(gate, NReg):
            assert 0 <= gate.register < len(netlist.registers)
    for register in netlist.registers:
        assert 0 <= register.next < len(netlist.gates)
    for output in (netlist.fail, netlist.success, netlist.overflow):
        assert 0 <= output < len(netlist.gates)


def test_finish_keeps_the_register_metadata() -> None:
    b = _builder()
    i = b.input(0)
    r = b.add_register(i, 3, (1, 0), "done", 2)
    zero = b.const(False)
    netlist = b.finish((AgeOutputs(zero, zero, zero, zero),), b.register_value(r), zero, zero)
    (register,) = netlist.registers
    assert (register.age, register.path, register.part, register.index) == (3, (1, 0), "done", 2)
    assert isinstance(netlist.gates[register.next], NInput)


def test_an_empty_netlist_has_no_registers() -> None:
    b = _builder()
    zero = b.const(False)
    netlist = b.finish((AgeOutputs(zero, zero, zero, zero),), zero, zero, zero)
    assert netlist.registers == ()
    values = evaluate(netlist, (), lambda condition: pytest.fail("no input expected"))
    assert values[netlist.fail] is False
    assert next_state(netlist, values) == ()
