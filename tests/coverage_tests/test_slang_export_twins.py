"""Coverage only: the defensive check of the twin-instance clock rewrite.

Slang pairs only instances with identical bodies, so a real design cannot reach the
mismatch branch; it is exercised here with synthetic records.
"""

from __future__ import annotations

import pytest

from sva2yosys.slang.export import JsonObject, _Record, _resolve_twins
from sva2yosys.slang.invoke import SlangError


def _record(scope: str, signal: str, *, analyzed: bool) -> _Record:
    json: JsonObject = {
        "name": "p",
        "propertySpec": {"kind": "Simple", "expr": {"sva2yosys_path": f"{scope}.{signal}"}},
        "sva2yosys_clock": {"kind": "SignalEvent", "expr": {"sva2yosys_path": f"{scope}.clk"}}
        if analyzed
        else None,
    }
    return _Record(json, (scope,), ("f.sv", 1, 1), key=(1, 10), analyzed=analyzed)


def test_matching_twin_gives_retargeted_clock() -> None:
    records = _resolve_twins(
        [_record("t.u0", "a", analyzed=True), _record("t.u1", "a", analyzed=False)]
    )
    assert records[1].json["sva2yosys_clock"] == {
        "kind": "SignalEvent",
        "expr": {"sva2yosys_path": "t.u1.clk"},
    }


def test_differing_twin_is_an_error() -> None:
    with pytest.raises(SlangError) as info:
        _resolve_twins([_record("t.u0", "a", analyzed=True), _record("t.u1", "b", analyzed=False)])
    assert info.value.diagnostics[0].code == "InconsistentInstanceAnalysis"
