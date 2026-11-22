"""Boundary contracts of the bounded-expression interface."""

import re

import pytest

from sva2yosys.codegen.expressions import MAX_FANIN, Expressions


def test_empty_boolean_and_vector_identities() -> None:
    expressions = Expressions()
    assert expressions.conjunction([]) == "1'b1"
    assert expressions.disjunction([]) == "1'b0"
    assert expressions.vector([]) == "1'b0"
    assert expressions.lines == []


def test_a_concatenation_requires_positive_width_operands() -> None:
    for operands in ([], [("a", 0)], [("a", -1)]):
        with pytest.raises(ValueError):
            Expressions().concat(operands)


@pytest.mark.parametrize("count", [1, 8, 9, 65, 4096])
@pytest.mark.parametrize("operator", ["&", "|"])
def test_large_boolean_joins_keep_all_operands_with_bounded_local_syntax(
    count: int, operator: str
) -> None:
    expressions = Expressions()
    operands = [f"b{i}" for i in range(count)]
    result = (
        expressions.conjunction(operands) if operator == "&" else expressions.disjunction(operands)
    )
    assignments = re.findall(r"assign (\w+) = (.*);", "\n".join(expressions.lines))
    definitions = dict(assignments)

    def expand(value: str) -> list[str]:
        if value in definitions:
            return expand(definitions[value])
        return [
            name
            for part in value.split(f" {operator} ")
            for name in (expand(part) if part in definitions else [part])
        ]

    assert expand(result) == operands
    for _, value in [*assignments, ("result", result)]:
        assert value.count(operator) < MAX_FANIN
    # Each input matters: the last operand differs from the neutral value, then the
    # first does. This catches dropped or duplicated chunks without relying on names.
    for changed in (0, count - 1):
        for neutral in (0, 1):
            values = dict.fromkeys(operands, neutral)
            values[operands[changed]] = 1 - neutral
            expected = all(values.values()) if operator == "&" else any(values.values())
            for name, value in assignments:
                values[name] = eval(value, {"__builtins__": {}}, values)
            assert eval(result, {"__builtins__": {}}, values) == expected


@pytest.mark.parametrize("count", [1, 8, 9, 65, 4096])
def test_concatenation_chunks_keep_operand_widths_and_order(count: int) -> None:
    expressions = Expressions()
    operands = [(f"b{i}", i % 7 + 1) for i in range(count)]
    result = expressions.concat(operands)
    assignments = dict(re.findall(r"assign (\w+) = (.*);", "\n".join(expressions.lines)))
    widths = {
        name: int(msb) + 1
        for msb, name in re.findall(r"logic \[(\d+):0\] (\w+);", "\n".join(expressions.lines))
    }
    widths.update(operands)

    def expand(value: str) -> list[str]:
        if value in assignments:
            expanded = expand(assignments[value])
            assert widths[value] == sum(widths[name] for name in expanded)
            return expanded
        if value.startswith("{"):
            parts = value[1:-1].split(", ")
            assert len(parts) <= MAX_FANIN
            return [name for part in parts for name in expand(part)]
        return [value]

    assert result.startswith("{")  # singleton concatenation remains unsigned too
    assert expand(result) == [name for name, _ in operands]


def test_bit_vectors_put_the_first_operand_in_the_least_significant_bit() -> None:
    expressions = Expressions()
    assert expressions.vector(["a", "b", "c"]) == "{c, b, a}"


def test_intermediate_declarations_and_assignments_can_be_emitted_separately() -> None:
    expressions = Expressions()
    expressions.disjunction([f"b{i}" for i in range(65)])
    assert expressions.declarations
    assert expressions.assignments
    assert all("logic " in line and "assign " not in line for line in expressions.declarations)
    assert all("assign " in line for line in expressions.assignments)
    declared = set(re.findall(r"(\w+);$", "\n".join(expressions.declarations), re.MULTILINE))
    assigned = set(re.findall(r"assign (\w+) =", "\n".join(expressions.assignments)))
    assert declared == assigned
