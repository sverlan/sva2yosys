"""Functional contract of the parameter dependencies (CONTRACT.md, point 28).

The expected sets are hand-derived from the text of each fixture and the rules of the contract
(``fixtures/dependencies/expected.txt``); no code computed them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sva2yosys.slang.export import JsonObject, export_texts
from sva2yosys.slang.invoke import SlangOptions

DEPENDENCIES = Path(__file__).resolve().parent / "fixtures" / "dependencies"
TABLE = DEPENDENCIES / "expected.txt"
ROW = re.compile(
    r"^(?P<file>\S+\.sv):(?P<label>\w+) -> (?:unknown|\{(?P<names>[^}]*)\})"
    r"(?: ; hidden \{(?P<hidden>[^}]*)\})?$"
)


def _rows() -> list[tuple[str, str, tuple[str, ...] | None, tuple[str, ...]]]:
    rows: list[tuple[str, str, tuple[str, ...] | None, tuple[str, ...]]] = []
    for line in TABLE.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = ROW.match(line)
        assert match is not None, f"malformed row of expected.txt: {line!r}"
        names = match["names"]
        expected = None if names is None else tuple(n for n in names.split(",") if n)
        hidden = tuple(n for n in (match["hidden"] or "").split(",") if n)
        rows.append((match["file"], match["label"], expected, hidden))
    return rows


def _records(text: str, name: str = "t.sv", top: str = "m") -> list[JsonObject]:
    document = export_texts([(name, text)], SlangOptions(top=(top,)))
    records = document["assertions"]
    assert isinstance(records, list)
    result: list[JsonObject] = []
    for record in records:
        assert isinstance(record, dict)
        result.append(record)
    return result


def _field(record: JsonObject) -> tuple[str, ...] | None:
    assert "sva2yosys_dependencies" in record, f"{record['name']}: no sva2yosys_dependencies"
    value = record["sva2yosys_dependencies"]
    if value is None:
        return None
    assert isinstance(value, list)
    assert all(isinstance(name, str) for name in value)
    return tuple(str(name) for name in value)


def _hidden(record: JsonObject) -> tuple[str, ...]:
    assert "sva2yosys_hiddenParameters" in record, f"{record['name']}: no hidden parameters"
    value = record["sva2yosys_hiddenParameters"]
    assert isinstance(value, list)
    return tuple(str(name) for name in value)


def _dependencies(text: str, label: str, top: str = "m") -> tuple[str, ...] | None:
    """The dependencies of the records labelled ``label``; the same in every instance."""
    found = {_field(r) for r in _records(text, top=top) if r["name"] == label}
    assert len(found) == 1, f"{label}: {len(found)} different results {found}"
    return next(iter(found))


def _hidden_of(text: str, label: str, top: str = "m") -> tuple[str, ...]:
    """The hidden dependencies of the records labelled ``label``; the same in every instance."""
    found = {_hidden(r) for r in _records(text, top=top) if r["name"] == label}
    assert len(found) == 1, f"{label}: {len(found)} different results {found}"
    return next(iter(found))


# --- the table of fixtures ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("file", "label", "expected", "hidden"),
    [pytest.param(*row, id=f"{row[0]}:{row[1]}") for row in _rows()],
)
def test_dependencies_of_each_fixture_assertion(
    file: str, label: str, expected: tuple[str, ...] | None, hidden: tuple[str, ...]
) -> None:
    """The sorted names of the record equal the hand-derived set (``None``: unknown), and so
    do its hidden dependencies (none unless the row says ``; hidden``)."""
    text = (DEPENDENCIES / file).read_text()
    assert _dependencies(text, label) == expected
    assert _hidden_of(text, label) == hidden


def test_every_fixture_has_rows_and_every_row_names_a_fixture() -> None:
    files = {path.name for path in DEPENDENCIES.glob("*.sv")}
    assert {file for file, _, _, _ in _rows()} == files
    for file, label, _, _ in _rows():
        text = (DEPENDENCIES / file).read_text()
        assert re.search(rf"\b{label}:\s*assert\b", text), f"{file} has no assertion {label}"


@pytest.mark.parametrize("file", sorted(p.name for p in DEPENDENCIES.glob("*.sv")))
def test_every_record_carries_a_sorted_subset_of_the_visible_parameters(file: str) -> None:
    """The key is never absent; a list is sorted, without duplicates, among the visible names."""
    records = _records((DEPENDENCIES / file).read_text())
    assert records
    for record in records:
        value = _field(record)
        if value is None:
            continue
        visible = record["sva2yosys_parameters"]
        assert isinstance(visible, list)
        names = {str(_entry(entry)["name"]) for entry in visible}
        assert list(value) == sorted(set(value))
        assert set(value) <= names


def _entry(value: object) -> JsonObject:
    assert isinstance(value, dict)
    return value


# --- soundness holes (point 28): one counterexample test each ------------------------------


def test_a_later_inner_declaration_does_not_capture_a_name_used_in_a_cast() -> None:
    """The cast reads the module's W: the inner ``localparam W`` follows the assertion."""
    text = (
        "module m #(parameter W = 2) (input logic clk, a, b);\n"
        "  if (1) begin : g\n"
        "    x: assert property (@(posedge clk) ((W)'(a) == 1) |-> b);\n"
        "    localparam W = 3;\n"
        "  end\n"
        "endmodule\n"
    )
    assert _dependencies(text, "x") == ("W",)


def test_a_later_inner_variable_does_not_hide_the_parameter_used_in_a_cast() -> None:
    """The same, with a variable of that name declared after the assertion."""
    text = (
        "module m #(parameter W = 2) (input logic clk, a, b);\n"
        "  if (1) begin : g\n"
        "    x: assert property (@(posedge clk) ((W)'(a) == 1) |-> b);\n"
        "    logic W;\n"
        "  end\n"
        "endmodule\n"
    )
    assert _dependencies(text, "x") == ("W",)


def test_a_port_declared_in_two_places_brings_the_width_of_its_second_declaration() -> None:
    """Non-ANSI: the direction is declared in one place, the type (with W) in another."""
    text = (
        "module m #(parameter W = 4, parameter U = 2) (clk, v);\n"
        "  input clk;\n"
        "  input v;\n"
        "  logic [W-1:0] v;\n"
        "  x: assert property (@(posedge clk) v[0] |-> v[1]);\n"
        "endmodule\n"
    )
    assert _dependencies(text, "x") == ("W",)


def test_a_type_reached_only_through_a_typedef_chain() -> None:
    text = (
        "module m #(parameter W = 4, parameter N = 2, parameter U = 1) (input logic clk, b);\n"
        "  typedef logic [W-1:0] t1;\n"
        "  typedef t1 t2;\n"
        "  typedef t2 arr_t [N];\n"
        "  t2 s;\n"
        "  arr_t r;\n"
        "  x: assert property (@(posedge clk) s[0] |-> b);\n"
        "  y: assert property (@(posedge clk) r[0][0] |-> b);\n"
        "endmodule\n"
    )
    assert _dependencies(text, "x") == ("W",)
    assert _dependencies(text, "y") == ("N", "W")


def test_a_localparam_derived_from_a_parameter_stops_the_closure() -> None:
    """The rewrite compares LP, so the parameter P it is derived from is not recorded."""
    text = (
        "module m #(parameter P = 3) (input logic clk, a, b);\n"
        "  localparam LP = P * 2;\n"
        "  x: assert property (@(posedge clk) a |-> ##LP b);\n"
        "endmodule\n"
    )
    assert _dependencies(text, "x") == ("LP",)


def test_a_function_called_with_a_folded_argument() -> None:
    """``f(K)`` may fold to a constant: K is found by the identifier scan, Q in the body."""
    text = (
        "module m #(parameter K = 2, parameter Q = 1, parameter U = 5)"
        " (input logic clk, a, b);\n"
        "  function automatic bit f(int x);\n"
        "    return x == Q;\n"
        "  endfunction\n"
        "  function automatic bit g(int x);\n"
        "    return x == 2;\n"
        "  endfunction\n"
        "  x: assert property (@(posedge clk) f(K) |-> b);\n"
        "  y: assert property (@(posedge clk) g(K) |-> b);\n"
        "endmodule\n"
    )
    assert _dependencies(text, "x") == ("K", "Q")
    assert _dependencies(text, "y") == ("K",)


_PROCEDURAL = """module m #(parameter H = 5, parameter SEL = 1, parameter W = 4, parameter U = 3)
  (input logic clk, a, b);
  logic [W-1:0] c;
  always @(posedge clk) begin
    if (H > 2) x: assert property (a |-> b);
    if (U < 0) ; else y: assert property (a |-> b);
    case (SEL)
      1: z: assert property (a |-> b);
      default: ;
    endcase
    if (c[0]) w: assert property (a |-> b);
  end
endmodule
"""


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        pytest.param("x", ("H",), id="if-condition"),
        pytest.param("y", ("U",), id="else-branch-of-an-if"),
        pytest.param("z", ("SEL",), id="case-selector"),
        pytest.param("w", ("W",), id="condition-reading-a-signal-with-a-parameter-width"),
    ],
)
def test_the_enclosing_condition_of_a_procedural_assertion(
    label: str, expected: tuple[str, ...]
) -> None:
    assert _dependencies(_PROCEDURAL, label) == expected


def test_a_name_that_is_not_in_the_visible_list_is_unknown() -> None:
    """A compilation-unit parameter is not among ``sva2yosys_parameters``: unknown."""
    text = (
        "parameter int UP = 2;\n"
        "module m #(parameter N = 1) (input logic clk, a, b);\n"
        "  x: assert property (@(posedge clk) a |-> ##UP b);\n"
        "  y: assert property (@(posedge clk) a |-> b);\n"
        "endmodule\n"
    )
    assert _dependencies(text, "x") is None
    assert _dependencies(text, "y") == ()


def test_the_declarations_of_a_scope_are_walked_once_however_many_assertions_it_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Performance contract (review): a module with thousands of mined assertions made the
    analysis quadratic, because the scope was walked again for each assertion."""
    from sva2yosys.slang import dependencies

    calls: list[str] = []
    original = dependencies._walk_declarations

    def counting(owner: object, wanted: frozenset[str]) -> list[object]:
        calls.append("walk")
        return original(owner, wanted)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr(dependencies, "_walk_declarations", counting)
    body = "".join(
        f"  a{i}: assert property (@(posedge clk) a |-> ##{i % 3 + 1} b);\n" for i in range(60)
    )
    text = f"module m #(parameter P = 1)(input logic clk, a, b);\n{body}endmodule\n"
    document = export_texts([("t.sv", text)], SlangOptions(top=("m",)))
    assert len(document["assertions"]) == 60  # type: ignore[arg-type]
    assert len(calls) <= 2, f"the scope was walked {len(calls)} times for 60 assertions"
