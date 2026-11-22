"""Coverage only: malformed export documents raise FormatError (convert's docstring)."""

from __future__ import annotations

import copy

import pytest

from sva2yosys.slang.convert import FormatError, convert
from sva2yosys.slang.export import JsonObject, JsonValue, export_texts

_TEXT = (
    "module m(input logic clk, a, b, input logic [3:0] v);\n"
    "  x: assert property (@(posedge clk) a ##1 {v, a} == 5'b1 |-> b);\n"
    "endmodule\n"
)


def _doc() -> JsonObject:
    return export_texts([("m.sv", _TEXT)])


def _record(doc: JsonObject) -> JsonObject:
    records = doc["assertions"]
    assert isinstance(records, list) and isinstance(records[0], dict)
    return records[0]


def _spec(doc: JsonObject) -> JsonObject:
    spec = _record(doc)["propertySpec"]
    assert isinstance(spec, dict)
    return spec


def _antecedent(doc: JsonObject) -> JsonObject:
    implication = _spec(doc)["expr"]
    assert isinstance(implication, dict)
    left = implication["left"]
    assert isinstance(left, dict)
    return left


def _break_assertions(doc: JsonObject) -> None:
    doc["assertions"] = {"not": "a list"}


def _break_scope(doc: JsonObject) -> None:
    _record(doc)["scope"] = 3


def _break_record(doc: JsonObject) -> None:
    doc["assertions"] = ["not an object"]


def _break_source(doc: JsonObject) -> None:
    del _record(doc)["source_line_start"]


def _break_elements(doc: JsonObject) -> None:
    _antecedent(doc)["elements"] = []


def _objects(node: JsonValue) -> list[JsonObject]:
    found: list[JsonObject] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_objects(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_objects(value))
    return found


def _break_operands(doc: JsonObject) -> None:
    concatenations = [n for n in _objects(doc) if n.get("kind") == "Concatenation"]
    assert concatenations
    concatenations[0]["operands"] = "not a list"


@pytest.mark.parametrize(
    "breaker",
    [
        _break_assertions,
        _break_scope,
        _break_record,
        _break_source,
        _break_elements,
        _break_operands,
    ],
)
def test_malformed_document(breaker: object) -> None:
    doc = copy.deepcopy(_doc())
    assert callable(breaker)
    breaker(doc)
    with pytest.raises(FormatError):
        convert(doc)
