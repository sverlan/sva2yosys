"""Unit contract of ``sva2yosys.rewrite.syntax_items`` (its docstring)."""

from __future__ import annotations

import pytest

from sva2yosys.ir.source import SourceRange
from sva2yosys.rewrite import Container, ItemKind, SyntaxItem, syntax_items

_ITEM: dict[str, object] = {
    "kind": "BindDirective",
    "source_file_start": "v.sv",
    "source_file_end": "v.sv",
    "source_line_start": 4,
    "source_line_end": 4,
    "source_column_start": 1,
    "source_column_end": 30,
    "sva2yosys_start": 60,
    "sva2yosys_end": 89,
    "sva2yosys_macro": False,
    "sva2yosys_container": "none",
    "sva2yosys_inputFile": "v.sv",
    "name": "host",
    "sva2yosys_instanceBind": False,
    "sva2yosys_insertAt": 70,
    "sva2yosys_insertEnd": 89,
}


def test_items_are_read() -> None:
    (item,) = syntax_items({"sva2yosys_syntax": [_ITEM]})
    assert item == SyntaxItem(
        kind=ItemKind.BIND,
        source=SourceRange("v.sv", 4, 1, 4, 30),
        start=60,
        end=89,
        macro=False,
        container=Container.NONE,
        input_file="v.sv",
        name="host",
        instance_bind=False,
        insert_at=70,
        insert_end=89,
    )


@pytest.mark.parametrize(
    "document",
    [
        None,
        {},
        {"sva2yosys_syntax": {}},
        {"sva2yosys_syntax": ["not an object"]},
        {"sva2yosys_syntax": [{**_ITEM, "kind": "Unknown"}]},
        {"sva2yosys_syntax": [{**_ITEM, "sva2yosys_start": "60"}]},
        {"sva2yosys_syntax": [{**_ITEM, "sva2yosys_start": True}]},
        {"sva2yosys_syntax": [{**_ITEM, "name": 3}]},
        {"sva2yosys_syntax": [{**_ITEM, "sva2yosys_macro": 0}]},
        {"sva2yosys_syntax": [{**_ITEM, "sva2yosys_container": "class"}]},
        {"sva2yosys_syntax": [{k: v for k, v in _ITEM.items() if k != "name"}]},
    ],
)
def test_malformed_inventories_are_rejected(document: object) -> None:
    with pytest.raises(ValueError, match="syntax"):
        syntax_items(document)
