"""Functional contract of the Slang frontend export (see CONTRACT.md in this folder)."""

from __future__ import annotations

import json
import os
from importlib.metadata import version
from pathlib import Path

import pytest

from support.toolchain import Tool, run_slang_ast_json
from sva2yosys.slang.export import (
    FORMAT,
    FORMAT_VERSION,
    JsonObject,
    JsonValue,
    export_compiled,
    export_design,
    export_texts,
)
from sva2yosys.slang.invoke import SlangError, SlangOptions, compile_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXPECTED = FIXTURES / "expected"
UPDATE = os.environ.get("SVA2YOSYS_UPDATE_SNAPSHOTS") == "1"
RANGE_KEYS = (
    "source_file_start",
    "source_file_end",
    "source_line_start",
    "source_line_end",
    "source_column_start",
    "source_column_end",
)


@pytest.fixture
def in_fixtures(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run with fixtures/ as working directory, so exported paths are relative."""
    monkeypatch.chdir(FIXTURES)
    return FIXTURES


def _export(name: str, top: tuple[str, ...] = ()) -> JsonObject:
    return export_design([Path(name)], SlangOptions(top=top))


def _records(doc: JsonObject) -> list[JsonObject]:
    records = doc["assertions"]
    assert isinstance(records, list)
    result: list[JsonObject] = []
    for record in records:
        assert isinstance(record, dict)
        result.append(record)
    return result


def _obj(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


def _by_name(doc: JsonObject, scope: str, name: str) -> JsonObject:
    matches = [r for r in _records(doc) if r["scope"] == scope and r["name"] == name]
    assert len(matches) == 1, f"{scope}.{name}: {len(matches)} records"
    return matches[0]


def _walk(node: JsonValue) -> list[JsonObject]:
    """All JSON objects in ``node``, depth first."""
    found: list[JsonObject] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk(value))
    return found


# --- 1, 5: envelope and golden exports ---------------------------------------------------


def test_envelope(in_fixtures: Path) -> None:
    """1."""
    doc = _export("basic_forms.sv")
    assert doc["format"] == FORMAT
    assert doc["formatVersion"] == FORMAT_VERSION
    assert doc["slangVersion"] == version("pyslang")
    assert isinstance(doc["assertions"], list)


@pytest.mark.parametrize(
    ("fixture", "top"),
    [
        ("basic_forms.sv", ()),
        ("defaults_and_decls.sv", ("top",)),
        ("checker_bind_hier.sv", ("top",)),
        ("expressions.sv", ("expr_top",)),
    ],
)
def test_export_matches_golden(in_fixtures: Path, fixture: str, top: tuple[str, ...]) -> None:
    """5."""
    doc = _export(fixture, top)
    text = json.dumps(doc, indent=1, sort_keys=False) + "\n"
    golden = EXPECTED / (Path(fixture).stem + ".json")
    if UPDATE:
        golden.parent.mkdir(exist_ok=True)
        golden.write_text(text, encoding="utf-8")
    assert golden.is_file(), f"missing golden {golden.name}; run with SVA2YOSYS_UPDATE_SNAPSHOTS=1"
    assert text == golden.read_text(encoding="utf-8")


# --- 2, 3: records -----------------------------------------------------------------------


def test_one_record_per_assertion_per_instance(in_fixtures: Path) -> None:
    """2."""
    doc = _export("defaults_and_decls.sv", ("top",))
    keys = [(r["scope"], r["name"]) for r in _records(doc)]
    assert keys == [
        ("top.u3", "p_named"),
        ("top.u3", "p_inline"),
        ("top.u5", "p_named"),
        ("top.u5", "p_inline"),
    ]


def test_checker_and_bound_instances_have_records(in_fixtures: Path) -> None:
    """2."""
    doc = _export("checker_bind_hier.sv", ("top",))
    keys = {(r["scope"], r["name"]) for r in _records(doc)}
    assert keys == {
        ("top", "h_assert"),
        ("top.u_chk", "c_assert"),
        ("top.u_dut.u_props", "b_assert"),
    }


def test_record_fields(in_fixtures: Path) -> None:
    """3."""
    doc = _export("basic_forms.sv")
    kinds = [r["assertionKind"] for r in _records(doc)]
    assert kinds.count("Assume") == 1
    assert kinds.count("CoverProperty") == 1
    record = _by_name(doc, "basic_forms", "p_window")
    assert record["kind"] == "ConcurrentAssertion"
    assert record["assertionKind"] == "Assert"
    assert {k: record[k] for k in RANGE_KEYS} == {
        "source_file_start": "basic_forms.sv",
        "source_file_end": "basic_forms.sv",
        "source_line_start": 11,
        "source_line_end": 11,
        "source_column_start": 5,
        "source_column_end": 69,  # exclusive end, as in Slang
    }
    assert record["sva2yosys_procedural"] is False
    assert record["sva2yosys_defaultDisable"] is None


# --- 4: native encoding ------------------------------------------------------------------


def test_window_property_native_encoding(in_fixtures: Path) -> None:
    """4."""
    spec = _obj(_by_name(_export("basic_forms.sv"), "basic_forms", "p_window")["propertySpec"])
    assert spec["kind"] == "Clocking"
    clocking = _obj(spec["clocking"])
    assert clocking["kind"] == "SignalEvent"
    assert clocking["edge"] == "PosEdge"
    assert _obj(clocking["expr"])["symbol"] == "clk"
    implication = _obj(spec["expr"])
    assert implication["kind"] == "Binary"
    assert implication["op"] == "OverlappedImplication"
    consequent = _obj(implication["right"])
    assert consequent["kind"] == "SequenceConcat"
    elements = consequent["elements"]
    assert isinstance(elements, list)
    element = _obj(elements[0])
    assert (element["min"], element["max"]) == (1, 5)
    assert _obj(_obj(element["sequence"])["expr"])["sva2yosys_path"] == "basic_forms.b"


def test_repetition_and_fusion_encoding(in_fixtures: Path) -> None:
    """4."""
    doc = _export("basic_forms.sv")
    rep = _obj(_obj(_obj(_by_name(doc, "basic_forms", "p_rep25")["propertySpec"])["expr"])["right"])
    assert rep["kind"] == "Simple"
    assert rep["repetition"] == {"kind": "Consecutive", "min": 2, "max": 5}
    chain = _obj(
        _obj(_obj(_by_name(doc, "basic_forms", "p_chain")["propertySpec"])["expr"])["right"]
    )
    elements = chain["elements"]
    assert isinstance(elements, list)
    assert [(_obj(e)["min"], _obj(e)["max"]) for e in elements] == [(0, 0), (1, 3)]


def test_unbounded_range_is_dollar() -> None:
    """4."""
    doc = export_texts(
        [
            (
                "u.sv",
                "module u(input logic clk, a, b);\n"
                "  p: assert property (@(posedge clk) a |-> ##[1:$] b);\n"
                "  q: assert property (@(posedge clk) a |-> b[*2:$]);\nendmodule\n",
            )
        ]
    )
    delays = [n for n in _walk(doc["assertions"]) if "min" in n and "sequence" in n]
    reps = [n for n in _walk(doc["assertions"]) if n.get("kind") == "Consecutive"]
    assert [(d["min"], d["max"]) for d in delays] == [(1, "$")]
    assert [(r["min"], r["max"]) for r in reps] == [(2, "$")]


def test_sampled_value_calls_are_system_calls(in_fixtures: Path) -> None:
    """4."""
    spec = _by_name(_export("basic_forms.sv"), "basic_forms", "p_sampled")["propertySpec"]
    calls = [n["subroutine"] for n in _walk(spec) if n.get("kind") == "Call"]
    assert sorted(str(c) for c in calls) == ["$changed", "$fell", "$past", "$stable"]


# --- 6, 7: folding and arguments ---------------------------------------------------------


def test_parameter_bound_folded_per_instance(in_fixtures: Path) -> None:
    """6."""
    doc = _export("defaults_and_decls.sv", ("top",))
    for scope, high in (("top.u3", 3), ("top.u5", 5)):
        spec = _by_name(doc, scope, "p_named")["propertySpec"]
        ranges = [(n["min"], n["max"]) for n in _walk(spec) if "sequence" in n and "min" in n]
        assert ranges == [(1, high)]


def test_arguments_are_substituted(in_fixtures: Path) -> None:
    """7."""
    spec = _by_name(_export("defaults_and_decls.sv", ("top",)), "top.u3", "p_named")["propertySpec"]
    instances = [n for n in _walk(spec) if n.get("kind") == "AssertionInstance"]
    assert [n["symbol"] for n in instances] == ["p_handshake", "s_resp"]
    paths = {n["sva2yosys_path"] for n in _walk(spec) if n.get("kind") == "NamedValue"}
    assert paths == {"top.u3.req", "top.u3.ack"}


# --- 8, 9: default clocking and default disable ------------------------------------------


def test_default_clocking_resolved(in_fixtures: Path) -> None:
    """8."""
    record = _by_name(_export("defaults_and_decls.sv", ("top",)), "top.u5", "p_inline")
    clock = _obj(record["sva2yosys_clock"])
    assert clock["kind"] == "SignalEvent"
    assert clock["edge"] == "PosEdge"
    assert _obj(clock["expr"])["sva2yosys_path"] == "top.u5.clk"
    assert _obj(record["propertySpec"])["kind"] == "Binary"


def test_explicit_clock_reported_as_clock(in_fixtures: Path) -> None:
    """8."""
    record = _by_name(_export("basic_forms.sv"), "basic_forms", "p_overlap")
    assert _obj(record["sva2yosys_clock"])["edge"] == "PosEdge"
    assert _obj(_obj(record["sva2yosys_clock"])["expr"])["sva2yosys_path"] == "basic_forms.clk"


def test_default_disable_bound_in_scope(in_fixtures: Path) -> None:
    """9."""
    doc = _export("defaults_and_decls.sv", ("top",))
    for scope in ("top.u3", "top.u5"):
        disable = _obj(_by_name(doc, scope, "p_inline")["sva2yosys_defaultDisable"])
        assert disable["kind"] == "UnaryOp"
        assert disable["op"] == "LogicalNot"
        assert _obj(disable["operand"])["sva2yosys_path"] == f"{scope}.rst_n"


def test_no_default_disable_is_null(in_fixtures: Path) -> None:
    """9."""
    for record in _records(_export("basic_forms.sv")):
        assert record["sva2yosys_defaultDisable"] is None


def test_explicit_disable_stays_in_property(in_fixtures: Path) -> None:
    """9."""
    spec = _obj(_by_name(_export("basic_forms.sv"), "basic_forms", "p_disable")["propertySpec"])
    inner = _obj(spec["expr"])
    assert inner["kind"] == "DisableIff"
    assert _obj(inner["condition"])["kind"] == "UnaryOp"


# --- 10, 11: procedural flag and unsupported nodes ---------------------------------------


def test_procedural_flag() -> None:
    """10."""
    doc = export_texts(
        [
            (
                "p.sv",
                "module p(input logic clk, a, b);\n"
                "  m: assert property (@(posedge clk) a |-> b);\n"
                "  always @(posedge clk) begin\n"
                "    inner: assert property (a |-> b);\n"
                "  end\nendmodule\n",
            )
        ]
    )
    flags = {r["name"]: r["sva2yosys_procedural"] for r in _records(doc)}
    assert flags == {"m": False, "inner": True}


_PROCEDURAL = """module p(input logic clk, a, b, input logic [1:0] v);
  always @(posedge clk) begin
    if (a) begin
      x1: assert property (b);
    end else if (b) x2: assert property (a);
    case (v)
      2'd1, 2'd2: x3: cover property (a);
      2'd3: x4: assert property (b);
      default: x5: assert property (a);
    endcase
  end
endmodule
"""


def _enable(record: JsonObject) -> list[tuple[str, bool, int]]:
    """Per entry of sva2yosys_enable: kind, negated, number of case items."""
    entries = record["sva2yosys_enable"]
    assert isinstance(entries, list)
    result: list[tuple[str, bool, int]] = []
    for entry in entries:
        entry = _obj(entry)
        items = entry.get("items", [])
        assert isinstance(items, list) and isinstance(entry["negated"], bool)
        result.append((str(entry["kind"]), entry["negated"], len(items)))
    return result


def test_procedural_enabling_conditions() -> None:
    """10."""
    records = {r["name"]: r for r in _records(export_texts([("p.sv", _PROCEDURAL)]))}
    for record in records.values():
        assert record["sva2yosys_procedureIssue"] is None
        clock = _obj(record["sva2yosys_procedureClock"])
        assert (clock["kind"], clock["edge"], _obj(clock["expr"])["symbol"]) == (
            "SignalEvent",
            "PosEdge",
            "clk",
        )
    assert _enable(records["x1"]) == [("If", False, 0)]
    first = records["x1"]["sva2yosys_enable"]
    assert isinstance(first, list)
    assert _obj(_obj(first[0])["expr"])["symbol"] == "a"
    assert _enable(records["x2"]) == [("If", True, 0), ("If", False, 0)]
    assert _enable(records["x3"]) == [("Case", False, 2)]
    assert _enable(records["x4"]) == [("Case", False, 1), ("Case", True, 2)]
    assert _enable(records["x5"]) == [("Case", True, 3)]


_ISSUES = """module sub(input logic clk, en, b);
  always @(posedge clk) if (en) z1: assert property (b);
endmodule
module p(input logic clk, a, b);
  logic t, q, c, e;
  always @(posedge clk) t = a;
  always_ff @(posedge clk) q <= a;
  always_comb c = q & a;
  always @(a) e = b;
  wire w = q;
  sub u_bad(.clk(clk), .en(t), .b(b));
  sub u_good(.clk(clk), .en(q), .b(b));
  sub u_input(.clk(clk), .en(a), .b(b));
  always @(posedge clk) begin
    if (t) y1: assert property (b);
    if (q) y2: assert property (b);
    if (a) y2b: assert property (b);
  end
  always @(posedge clk) for (int k = 0; k < 2; k++) y3: assert property (a);
  logic q2, q3;
  logic clk2;
  always_ff @(posedge clk2) q2 <= a;
  always_ff @(posedge clk or negedge b) q3 <= a;
  always @(posedge clk) begin
    y13: assert property (a);
    #1;
  end
  always @(posedge clk) if (q2) y14: assert property (a);
  always @(posedge clk) if (q3) y15: assert property (a);
  logic q4, r4;
  always @(posedge clk) begin
    q4 <= a;
    {q4, r4} = {a, b};
  end
  always @(posedge clk) if (q4) y16: assert property (a);
  initial y4: assert property (@(posedge clk) a);
  always_comb y5: assert property (@(posedge clk) a);
  always @(posedge clk or posedge a) y6: assert property (@(posedge clk) b);
  always @(posedge clk) casez (t) 1'b?: y7: assert property (a); endcase
  always @(posedge clk) if (clk) y8: assert property (a);
  always @(posedge clk) begin : blk
    q <= a;
    if (a) disable blk;
    y9: assert property (b);
  end
  always @(posedge clk) if (c) y10: assert property (a);
  always @(posedge clk) if (e) y11: assert property (a);
  always @(posedge clk) if (w) y12: assert property (a);
endmodule
"""


def test_procedural_issues() -> None:
    """10."""
    records = {
        (r["scope"], r["name"]): r
        for r in _records(export_texts([("p.sv", _ISSUES)], SlangOptions(top=("p",))))
    }

    def issue(scope: str, name: str) -> str:
        value = records[(scope, name)]["sva2yosys_procedureIssue"]
        return "" if value is None else str(value)

    assert "t" in issue("p", "y1") and "blocking" in issue("p", "y1")
    assert issue("p", "y2") == ""  # q: nonblocking in a clocked block
    assert issue("p", "y2b") == ""  # a: a top-level input other than the clock
    assert "loop" in issue("p", "y3")
    assert issue("p", "y4") == ""  # an initial block
    assert "always_comb" in issue("p", "y5")
    assert "event" in issue("p", "y6")
    assert "casez" in issue("p", "y7")
    assert "clock of the block" in issue("p", "y8")
    assert "may not fall through" in issue("p", "y9")
    assert "combinational" in issue("p", "y10")  # always_comb
    assert "combinational" in issue("p", "y11")  # an explicit level-sensitive list
    assert "continuous" in issue("p", "y12")
    assert "en" in issue("p.u_bad", "z1") and "blocking" in issue("p.u_bad", "z1")
    assert issue("p.u_good", "z1") == ""  # en <- q by a plain port connection
    assert issue("p.u_input", "z1") == ""
    assert "may not fall through" in issue("p", "y13")  # a delay after the assertion
    assert "another event" in issue("p", "y14")  # written at another clock
    assert "another event" in issue("p", "y15")  # an asynchronous reset in the event list
    assert "blocking" in issue("p", "y16")  # a blocking write through a concatenation
    assert records[("p", "y4")]["sva2yosys_procedureClock"] is None
    assert records[("p", "y4")]["sva2yosys_procedureKind"] == "initial"
    assert records[("p", "y2")]["sva2yosys_procedureKind"] == "always"


_INITIAL = """module p(input logic clk, a, b, c, input logic [1:0] v);
  logic q;
  always_ff @(posedge clk) q <= a;
  default clocking @(posedge clk); endclocking
  // explicit clock in the assertion
  initial i1: assert property (@(posedge clk) a |-> b);
  // default clocking
  initial i2: cover property (a ##1 b);
  // inferred from the single event control of the initial block (16.14.6)
  initial @(posedge clk) i3: assume property (a);
  initial begin
    if (c) i4: assert property (a);
    else i5: assert property (b);
    case (v) 2'd1: i6: assert property (a); default: ; endcase
  end
  initial if (q) i7: assert property (b);
  initial for (int k = 0; k < 2; k++) j1: assert property (a);
  initial begin j2: assert property (a); #1; end
  initial begin j3: assert property (a); fork join end
  initial #1 j4: assert property (a);
  initial @(posedge clk or posedge a) j5: assert property (b);
  initial begin @(posedge clk); j6: assert property (a); end
  always_latch if (c) j7: assert property (@(posedge clk) a);
endmodule
"""


def _initial_records() -> dict[str, JsonObject]:
    return {
        str(r["name"]): r
        for r in _records(export_texts([("p.sv", _INITIAL)], SlangOptions(top=("p",))))
    }


def test_initial_block_assertions_are_exported() -> None:
    """10 (IEEE 1800-2017 16.14.6)."""
    records = _initial_records()
    for name in ("i1", "i2", "i3"):
        record = records[name]
        assert record["sva2yosys_procedural"] is True
        assert record["sva2yosys_procedureKind"] == "initial"
        assert record["sva2yosys_procedureIssue"] is None, name
        clock = _obj(record["sva2yosys_clock"])  # the clock Slang resolves
        assert (clock["kind"], clock["edge"], _obj(clock["expr"])["symbol"]) == (
            "SignalEvent",
            "PosEdge",
            "clk",
        )
    # an event control of the block is exported; otherwise there is none
    assert records["i1"]["sva2yosys_procedureClock"] is None
    assert records["i2"]["sva2yosys_procedureClock"] is None
    assert _obj(records["i3"]["sva2yosys_procedureClock"])["edge"] == "PosEdge"
    assert _enable(records["i1"]) == []


def test_initial_block_rejected_forms_are_issues() -> None:
    """10: path conditions (if, else, case: evaluated at time 0, not at the first
    tick, 16.14.6.2), loops, fork, delays, event lists, other block kinds."""
    records = _initial_records()
    for name, fragment in (
        ("i4", "evaluated when the procedure runs at time 0"),
        ("i5", "evaluated when the procedure runs at time 0"),
        ("i6", "evaluated when the procedure runs at time 0"),
        ("i7", "evaluated when the procedure runs at time 0"),
        ("j1", "loop"),
        ("j2", "may not fall through"),
        ("j3", "may not fall through"),
        ("j4", "event control"),
        ("j5", "event control"),
        ("j6", "may not fall through"),
        ("j7", "always_latch"),
    ):
        issue = records[name]["sva2yosys_procedureIssue"]
        assert isinstance(issue, str) and fragment in issue, (name, issue)


def test_initial_block_without_a_clock_has_none() -> None:
    """10: no explicit clock, no default clocking, no event control: the record
    has no clock (the converter rejects it citing 16.16)."""
    text = "module p(input logic a);\n  initial n1: assert property (a);\nendmodule\n"
    (record,) = _records(export_texts([("p.sv", text)]))
    assert record["sva2yosys_procedureKind"] == "initial"
    assert record["sva2yosys_clock"] is None


def test_initial_block_clock_comes_from_its_event_control() -> None:
    """10: ``initial @(posedge clk) assert property (a)`` without default
    clocking infers the clock from the block (16.14.6)."""
    text = (
        "module p(input logic clk, a);\n"
        "  initial @(posedge clk) n1: assert property (a);\nendmodule\n"
    )
    (record,) = _records(export_texts([("p.sv", text)]))
    assert record["sva2yosys_procedureIssue"] is None
    assert _obj(_obj(record["sva2yosys_clock"])["expr"])["symbol"] == "clk"


def test_unsupported_nodes_are_marked() -> None:
    """11."""
    doc = export_texts(
        [
            (
                "x.sv",
                "module x(input logic clk, a, b);\n"
                "  p: assert property (@(posedge clk) a |-> first_match(##[1:2] b));\nendmodule\n",
            )
        ]
    )
    marked = [n for n in _walk(doc["assertions"]) if n.get("sva2yosys_unsupported") is True]
    assert [n["kind"] for n in marked] == ["FirstMatch"]
    assert all(key in marked[0] for key in RANGE_KEYS)


def test_support_boundaries_preserve_nodes_or_located_export_gaps(in_fixtures: Path) -> None:
    """Support-table audit: elaboration/export is separate from converter admission."""
    doc = _export("support_boundaries.sv", ("support_boundaries",))
    records = {str(record["name"]): record for record in _records(doc)}
    assert set(records) == {
        "r_restrict",
        "r_negedge",
        "r_iff",
        "r_negedge_iff",
        "r_multiclock",
        "r_gated_past",
        "r_clocked_past",
        "r_nested",
        "r_triggered",
        "r_first_match",
        "r_package_seq",
        "r_package_prop",
    }
    for name, record in records.items():
        marked = [node for node in _walk(record) if node.get("sva2yosys_unsupported")]
        expected = {
            "r_first_match": ["FirstMatch"],
            "r_clocked_past": ["EmptyArgument", "ClockingEvent"],
        }.get(name, [])
        assert [node["kind"] for node in marked] == expected, name
        assert all(all(key in node for key in RANGE_KEYS) for node in marked)
        assert record["source_file_start"] == "support_boundaries.sv"
    assert records["r_restrict"]["assertionKind"] == "Restrict"
    assert _obj(records["r_negedge"]["sva2yosys_clock"])["edge"] == "NegEdge"
    assert _obj(_obj(records["r_iff"]["sva2yosys_clock"])["iff"])["symbol"] == "en"
    negative_iff = _obj(records["r_negedge_iff"]["sva2yosys_clock"])
    assert negative_iff["edge"] == "NegEdge"
    assert _obj(negative_iff["iff"])["symbol"] == "en"
    clocks = [
        node
        for node in _walk(records["r_multiclock"]["propertySpec"])
        if node.get("kind") == "SignalEvent"
    ]
    assert [_obj(clock["expr"])["symbol"] for clock in clocks] == ["clk", "clk2"]
    calls = [
        node
        for node in _walk(records["r_gated_past"]["propertySpec"])
        if node.get("kind") == "Call"
    ]
    arguments = calls[0]["arguments"]
    assert isinstance(arguments, list)
    assert len(arguments) == 3 and _obj(arguments[2])["symbol"] == "en"
    assert [
        node["subroutine"]
        for node in _walk(records["r_nested"]["propertySpec"])
        if node.get("kind") == "Call"
    ] == ["$rose", "$past"]
    for name in ("r_triggered", "r_package_seq", "r_package_prop"):
        instances = [
            node
            for node in _walk(records[name]["propertySpec"])
            if node.get("kind") == "AssertionInstance"
        ]
        assert len(instances) == 1
        assert _obj(instances[0]["body"])["kind"] in ("SequenceConcat", "Binary")


# --- 12: errors --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "top"),
    [
        ("basic_forms.sv", ()),
        ("defaults_and_decls.sv", ("top",)),
        ("checker_bind_hier.sv", ("top",)),
        ("expressions.sv", ("expr_top",)),
    ],
)
def test_export_is_plain_json_in_memory(
    in_fixtures: Path, fixture: str, top: tuple[str, ...]
) -> None:
    """13."""
    before = sorted(p.name for p in in_fixtures.iterdir())
    doc = _export(fixture, top)
    assert json.loads(json.dumps(doc)) == doc
    for node in _walk(doc):
        for value in node.values():
            assert value is None or isinstance(value, (bool, int, str, list, dict))
    assert sorted(p.name for p in in_fixtures.iterdir()) == before


def test_compile_error_is_reported() -> None:
    """12."""
    text = "module e(input logic clk);\n  p: assert property (@(posedge clk) nope);\nendmodule\n"
    with pytest.raises(SlangError) as info:
        export_texts([("e.sv", text)])
    diag = info.value.diagnostics[0]
    assert diag.code == "UndeclaredIdentifier"
    assert diag.location is not None
    assert (diag.location.file, diag.location.line, diag.location.column) == ("e.sv", 2, 38)
    assert "nope" in diag.message
    assert "e.sv:2:38" in info.value.report


# --- expressions.sv: kinds, iff clocks, arrays, default disable positions ----------------


def _expressions() -> JsonObject:
    return _export("expressions.sv", ("expr_top",))


def test_expression_kinds_exported(in_fixtures: Path) -> None:
    """4."""
    doc = _expressions()
    kinds = {n.get("kind") for n in _walk(doc["assertions"])}
    expected = {
        "ElementSelect",
        "RangeSelect",
        "Concatenation",
        "Replication",
        "ConditionalOp",
        "Conversion",
        "IntegerLiteral",
        "BinaryOp",
        "UnaryOp",
        "NamedValue",
    }
    assert expected <= kinds
    select = _obj(_obj(_by_name(doc, "expr_top.u_expr", "e_select")["propertySpec"])["expr"])
    left = _obj(_obj(select["left"])["expr"])
    assert left["kind"] == "ElementSelect"
    assert _obj(left["value"])["sva2yosys_path"] == "expr_top.u_expr.v"
    ranged = _obj(_obj(_obj(select["right"])["expr"])["left"])
    assert ranged["kind"] == "RangeSelect"
    assert ranged["selectionKind"] == "Simple"


def test_folded_constant(in_fixtures: Path) -> None:
    """4."""
    spec = _by_name(_expressions(), "expr_top.u_expr", "e_const")["propertySpec"]
    equality = [n for n in _walk(spec) if n.get("op") == "Equality"]
    assert [n["constant"] for n in equality] == ["1'b1"]


def test_user_function_call_is_unsupported(in_fixtures: Path) -> None:
    """11."""
    spec = _by_name(_expressions(), "expr_top.u_expr", "e_ucall")["propertySpec"]
    marked = [n for n in _walk(spec) if n.get("sva2yosys_unsupported") is True]
    assert [n["kind"] for n in marked] == ["Call"]


def test_iff_clock(in_fixtures: Path) -> None:
    """8."""
    clock = _obj(_by_name(_expressions(), "expr_top.u_expr", "e_iff")["sva2yosys_clock"])
    assert clock["edge"] == "PosEdge"
    assert _obj(clock["iff"])["sva2yosys_path"] == "expr_top.u_expr.en"


def test_generate_array_scopes(in_fixtures: Path) -> None:
    """2."""
    scopes = [r["scope"] for r in _records(_expressions()) if r["name"] == "g_bit"]
    assert scopes == ["expr_top.u_expr.g[0]", "expr_top.u_expr.g[1]"]


def test_repeated_identical_instances_get_their_own_clock(in_fixtures: Path) -> None:
    """8."""
    doc = _expressions()
    for index in (0, 1):
        scope = f"expr_top.u_leaf[{index}]"
        clock = _obj(_by_name(doc, scope, "l_assert")["sva2yosys_clock"])
        assert _obj(clock["expr"])["sva2yosys_path"] == f"{scope}.clk"


def _text_at(text: str, node: JsonObject) -> str:
    lines = text.splitlines()
    line, column = node["source_line_start"], node["source_column_start"]
    end_line, end_column = node["source_line_end"], node["source_column_end"]
    assert isinstance(line, int) and isinstance(column, int)
    assert isinstance(end_line, int) and isinstance(end_column, int)
    assert line == end_line
    return lines[line - 1][column - 1 : end_column - 1]


def test_default_disable_positions_refer_to_original_text(in_fixtures: Path) -> None:
    """9."""
    doc = _expressions()
    text = (in_fixtures / "expressions.sv").read_text()
    leaf = _obj(_by_name(doc, "expr_top.u_leaf[1]", "l_assert")["sva2yosys_defaultDisable"])
    assert leaf["op"] == "LogicalOr"
    rst = _obj(leaf["left"])
    x = _obj(_obj(leaf["right"])["operand"])
    assert _text_at(text, rst) == "rst"
    assert (rst["source_line_start"], x["source_line_start"]) == (28, 29)
    assert _text_at(text, x) == "x"
    second = _obj(_by_name(doc, "expr_top.u_second", "s_assert")["sva2yosys_defaultDisable"])
    assert second["source_line_start"] == 35
    assert _text_at(text, second) == "r2"


def test_export_compiled_has_no_default_disable(in_fixtures: Path) -> None:
    """14."""
    compiled = compile_design([Path("defaults_and_decls.sv")], SlangOptions(top=("top",)))
    doc = export_compiled(compiled)
    records = _records(doc)
    assert len(records) == 4
    assert all(r["sva2yosys_defaultDisable"] is None for r in records)
    assert all(r["sva2yosys_clock"] is not None for r in records)


# --- 15: native compatibility (opt-in) ----------------------------------------------------

_FIXTURE_TOPS = [
    ("basic_forms.sv", ()),
    ("defaults_and_decls.sv", ("top",)),
    ("checker_bind_hier.sv", ("top",)),
    ("expressions.sv", ("expr_top",)),
]


def _native_statements(node: object) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    if isinstance(node, dict):
        if node.get("kind") == "ConcurrentAssertion":
            found.append(node)
        for value in node.values():
            found.extend(_native_statements(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_native_statements(value))
    return found


def _subset_problems(ours: object, native: object, path: str) -> list[str]:
    """Where ``ours`` is not a subset of ``native`` (see contract point 15)."""
    if isinstance(ours, dict):
        if not isinstance(native, dict):
            return [f"{path}: native is not an object"]
        problems: list[str] = []
        for key, value in ours.items():
            if key.startswith("sva2yosys_"):
                continue
            if key not in native:
                problems.append(f"{path}.{key}: missing in native")
                continue
            theirs = native[key]
            if key == "symbol" and isinstance(theirs, str):
                theirs = theirs.rsplit(" ", 1)[-1]
            problems.extend(_subset_problems(value, theirs, f"{path}.{key}"))
        return problems
    if isinstance(ours, list):
        if not isinstance(native, list) or len(ours) != len(native):
            return [f"{path}: lists differ"]
        result: list[str] = []
        for index, (item, other) in enumerate(zip(ours, native, strict=True)):
            result.extend(_subset_problems(item, other, f"{path}[{index}]"))
        return result
    return [] if ours == native else [f"{path}: ours={ours!r} native={native!r}"]


@pytest.mark.parametrize(("fixture", "top"), _FIXTURE_TOPS)
def test_export_is_subset_of_native_ast_json(
    in_fixtures: Path, slang_tool: Tool, fixture: str, top: tuple[str, ...]
) -> None:
    """15."""
    native = _native_statements(run_slang_ast_json(slang_tool, [fixture], in_fixtures, top=top))
    by_position: dict[tuple[object, object], list[dict[str, object]]] = {}
    for statement in native:
        position = (statement.get("source_line_start"), statement.get("source_column_start"))
        by_position.setdefault(position, []).append(statement)
    records = _records(_export(fixture, top))
    assert records
    for record in records:
        compared = {
            key: value
            for key, value in record.items()
            if key in ("kind", "assertionKind", "propertySpec") or key.startswith("source_")
        }
        candidates = by_position.get(
            (record["source_line_start"], record["source_column_start"]), []
        )
        attempts = [
            _subset_problems(compared, candidate, f"{record['scope']}.{record['name']}")
            for candidate in candidates
        ]
        assert attempts, f"no native statement for {record['scope']}.{record['name']}"
        assert min(attempts, key=len) == []


# --- 16, 17: syntax inventory, generate twins ---------------------------------------------

_INVENTORY = """module m(input logic clk, rst, a, b);
  default clocking cb @(posedge clk); endclocking
  clocking other @(posedge clk); endclocking
  default disable iff (rst);
  sequence s1(x); x ##1 b; endsequence
  property p1; a |-> s1(a); endproperty
  lbl: assert property (p1);
  always @(posedge clk) begin
    assert property (a);
  end
  `define AST assert property (b);
  `AST
endmodule
module unused(input logic clk, a);
  cover property (@(posedge clk) a);
endmodule
interface ifc(input logic clk);
endinterface
bind m chk u_c (.clk(clk));
bind m: top.u chk u_d (.clk(clk));
module chk(input logic clk); endmodule
module top(input logic clk, rst, a, b); m u(.*); endmodule
"""


def _items() -> list[JsonObject]:
    doc = export_texts([("inv.sv", _INVENTORY)], SlangOptions(top=("top",)))
    items = doc["sva2yosys_syntax"]
    assert isinstance(items, list)
    return [_obj(item) for item in items]


def test_syntax_inventory_lists_sva_items() -> None:
    """16."""
    items = _items()
    kinds = [item["kind"] for item in items]
    assert kinds.count("ConcurrentAssertion") == 4
    assert kinds.count("DefaultClocking") == 1
    for kind in ("DefaultDisableDeclaration", "SequenceDeclaration", "PropertyDeclaration"):
        assert kinds.count(kind) == 1
    assert kinds.count("BindDirective") == 2
    assert [i["name"] for i in items if i["kind"] == "ModuleDeclaration"] == [
        "m",
        "unused",
        "chk",
        "top",
    ]
    for item in items:
        if item["sva2yosys_macro"]:
            continue
        start, end = item["sva2yosys_start"], item["sva2yosys_end"]
        assert isinstance(start, int) and isinstance(end, int)
        text = _INVENTORY[start:end]
        match item["kind"]:
            case "ConcurrentAssertion":
                assert text.startswith(("lbl: assert", "assert", "cover")) and text.endswith(";")
            case "ModuleDeclaration":
                insert = item["sva2yosys_insertAt"]
                assert isinstance(insert, int)
                assert _INVENTORY[insert:].startswith("endmodule")
            case _:
                assert text.strip() == text


def test_syntax_inventory_marks_macros_containers_and_binds() -> None:
    """16."""
    items = _items()
    assertions = [i for i in items if i["kind"] == "ConcurrentAssertion"]
    macro = [i for i in assertions if i["sva2yosys_macro"]]
    assert len(macro) == 1 and macro[0]["source_line_start"] == 12
    assert {i["sva2yosys_container"] for i in assertions} == {"module"}
    binds = [i for i in items if i["kind"] == "BindDirective"]
    assert [(b["name"], b["sva2yosys_instanceBind"]) for b in binds] == [("m", False), ("m", True)]
    first = binds[0]
    start, end = first["sva2yosys_insertAt"], first["sva2yosys_insertEnd"]
    assert isinstance(start, int) and isinstance(end, int)
    assert _INVENTORY[start:end] == "chk u_c (.clk(clk));"
    assert all(i["sva2yosys_container"] == "none" for i in binds)


def test_identical_instances_with_generate_loops() -> None:
    """17."""
    source = (
        "module p(input logic clk, input logic [1:0] d);\n"
        "  for (genvar i = 0; i < 2; i++) begin : g\n"
        "    logic x;\n    assign x = d[i];\n"
        "    a: assert property (@(posedge clk) x);\n  end\nendmodule\n"
        "module top(input logic clk, input logic [1:0] d0, d1);\n"
        "  p u0(.clk(clk), .d(d0));\n  p u1(.clk(clk), .d(d1));\nendmodule\n"
    )
    doc = export_texts([("g.sv", source)], SlangOptions(top=("top",)))
    records = _records(doc)
    scopes = [str(record["scope"]) for record in records]
    assert scopes == ["top.u0.g[0]", "top.u0.g[1]", "top.u1.g[0]", "top.u1.g[1]"]
    for record, scope in zip(records, scopes, strict=True):
        instance = scope.rsplit(".", 1)[0]
        clock = _obj(_obj(record["sva2yosys_clock"])["expr"])
        assert clock["sva2yosys_path"] == f"{instance}.clk"
        paths = {node.get("sva2yosys_path") for node in _walk(record["propertySpec"])}
        assert f"{scope}.x" in paths


def test_visibility_at_the_assertion() -> None:
    """18."""
    source = (
        "module s(input logic clk, rst, a);\n"
        "  default disable iff (rst);\n"
        "  for (genvar i = 0; i < 1; i++) begin : g\n"
        "    logic rst;\n    assign rst = 1'b0;\n"
        "    x: assert property (@(posedge clk) a);\n  end\n"
        "  y: assert property (@(posedge clk) a);\nendmodule\n"
    )
    doc = export_texts([("s.sv", source)], SlangOptions(top=("s",)))
    for record in _records(doc):
        disable = _obj(record["sva2yosys_defaultDisable"])
        assert disable["sva2yosys_visibleAtSite"] is (record["name"] == "y")
        clock = _obj(_obj(record["sva2yosys_clock"])["expr"])
        assert clock["sva2yosys_visibleAtSite"] is True


def test_default_disable_after_non_ascii_text() -> None:
    """9."""
    source = (
        "// délai µs — ünïcödé\nmodule p(input logic clk, rst, a);\n"
        "  default disable iff (rst);\n  x: assert property (@(posedge clk) a);\nendmodule\n"
    )
    doc = export_texts([("u.sv", source)], SlangOptions(top=("p",)))
    (record,) = _records(doc)
    disable = _obj(record["sva2yosys_defaultDisable"])
    assert disable["sva2yosys_path"] == "p.rst"
    assert (disable["source_line_start"], disable["source_column_start"]) == (3, 24)


def test_default_disable_in_an_included_file_is_an_error(tmp_path: Path) -> None:
    """9."""
    (tmp_path / "inc.svh").write_text("default disable iff (rst);\n")
    source = (
        'module p(input logic clk, rst, a);\n  `include "inc.svh"\n'
        "  x: assert property (@(posedge clk) a);\nendmodule\n"
    )
    with pytest.raises(SlangError) as caught:
        export_texts([("p.sv", source)], SlangOptions(top=("p",), include_dirs=(tmp_path,)))
    assert caught.value.diagnostics[0].code == "UnsupportedDefaultDisable"


def test_includes_are_listed(tmp_path: Path) -> None:
    """19."""
    (tmp_path / "outer.svh").write_text('`include "inner.svh"\n')
    (tmp_path / "inner.svh").write_text("logic x;\n")
    doc = export_texts(
        [("p.sv", 'module p;\n  `include "outer.svh"\nendmodule\n')],
        SlangOptions(top=("p",), include_dirs=(tmp_path,)),
    )
    listed = doc["sva2yosys_includes"]
    assert isinstance(listed, list)
    includes = [_obj(i) for i in listed]
    assert [(i["sva2yosys_inputFile"], i["path"]) for i in includes] == [
        ("p.sv", "outer.svh"),
        ("p.sv", "inner.svh"),
    ]
    resolved = [Path(str(i["sva2yosys_resolved"])).resolve() for i in includes]
    assert resolved == [(tmp_path / "outer.svh").resolve(), (tmp_path / "inner.svh").resolve()]


def test_packages_are_listed() -> None:
    """20."""
    sources = [
        ("a.sv", "package pa; endpackage\npackage pb; endpackage\n"),
        ("b.sv", "module m; endmodule\n"),
    ]
    doc = export_texts(sources, SlangOptions(top=("m",)))
    assert doc["sva2yosys_packages"] == [
        {"name": "pa", "sva2yosys_inputFile": "a.sv"},
        {"name": "pb", "sva2yosys_inputFile": "a.sv"},
    ]


def test_constant_expressions_are_folded() -> None:
    """21."""
    source = (
        "module m #(parameter int N = 3) (input logic clk, input logic [3:0] u);\n"
        "  x: assert property (@(posedge clk) u / -1 + u / (N + 1) + $bits(u) == u + 4'bx01);\n"
        "endmodule\n"
    )
    (record,) = _records(export_texts([("m.sv", source)], SlangOptions(top=("m",))))
    folded = {
        (node.get("kind"), node.get("op"), node.get("constant"))
        for node in _walk(record["propertySpec"])
    }
    # In this unsigned 32-bit context, -1 is 2**32 - 1.
    assert ("UnaryOp", "Minus", "32'd4294967295") in folded
    assert ("BinaryOp", "Add", "32'd4") in folded
    assert ("Call", None, "4") in folded
    assert ("IntegerLiteral", None, "4'bxx01") in folded
    assert all(
        constant is None
        for kind, op, constant in folded
        if kind == "BinaryOp" and op in ("Divide", "Equality")
    )


def test_package_uses_are_listed() -> None:
    """22."""
    sources = [
        ("a.sv", "package pa;\n  localparam int A = 1;\nendpackage\n"),
        ("b.sv", "module m import pa::*; ();\n  localparam int B = pa::A + A;\nendmodule\n"),
    ]
    doc = export_texts(sources, SlangOptions(top=("m",)))
    assert doc["sva2yosys_packageUses"] == [{"name": "pa", "sva2yosys_inputFile": "b.sv"}]


def test_syntax_inventory_gives_the_insert_point_of_procedural_assertions() -> None:
    """16."""
    text = (
        "module p(input logic clk, a);\n"
        "  always @(posedge clk) begin\n"
        "    if (a) x: assert property (a);\n"
        "  end\n"
        "  y: assert property (@(posedge clk) a);\n"
        "endmodule\n"
    )
    items = export_texts([("p.sv", text)])["sva2yosys_syntax"]
    assert isinstance(items, list)
    inserts = [
        _obj(item)["sva2yosys_insertAt"]
        for item in items
        if _obj(item)["kind"] == "ConcurrentAssertion"
    ]
    assert inserts == [text.index("  end\n") + len("  end"), 0]


def test_procedural_assertions_in_unbraced_generate_bodies_have_no_insert_point() -> None:
    """16."""
    text = (
        "module p(input logic clk, a);\n"
        "  for (genvar i = 0; i < 2; i++)\n"
        "    always @(posedge clk) x: assert property (a);\n"
        "  for (genvar j = 0; j < 2; j++) begin : g\n"
        "    always @(posedge clk) y: assert property (a);\n"
        "  end\n"
        "endmodule\n"
    )
    items = export_texts([("p.sv", text)])["sva2yosys_syntax"]
    assert isinstance(items, list)
    inserts = [
        _obj(item)["sva2yosys_insertAt"]
        for item in items
        if _obj(item)["kind"] == "ConcurrentAssertion"
    ]
    second = text.index("    always @(posedge clk) y")
    assert inserts == [-1, text.index("\n", second)]


_DISABLES = """module q(input logic clk, a, b);
  logic r = 1'b0;
  always_ff @(posedge clk) r <= a;
  default disable iff (r);
  x5: assert property (@(posedge clk) a |-> b);
  x6: assert property (@(posedge clk) disable iff (!b) a |-> b);
endmodule
module s(input logic clk, rst_n, a, b);
  x7: assert property (@(posedge clk) disable iff (!rst_n) a |-> b);
endmodule
module p(input logic clk, rst_n, a, b);
  logic r = 1'b0;
  always_ff @(posedge clk) r <= a;
  wire rn = !rst_n;
  wire mixed = rn | r;
  wire #1 late = rst_n;
  x1: assert property (@(posedge clk) disable iff (!rst_n) a |-> b);
  x2: assert property (@(posedge clk) disable iff (rn) a |-> b);
  x3: assert property (@(posedge clk) disable iff (r) a |-> b);
  x4: assert property (@(posedge clk) disable iff (mixed) a |-> b);
  x8: assert property (@(posedge clk) disable iff (late) a |-> b);
  q u_q(.clk(clk), .a(a), .b(b));
  s u_s(.clk(clk), .rst_n(rst_n), .a(a), .b(b));
endmodule
"""


def test_disable_conditions_that_may_change_at_the_edge() -> None:
    """23."""
    document = export_texts([("p.sv", _DISABLES)], SlangOptions(top=("p",)))
    issues = {r["name"]: r["sva2yosys_disableIssue"] for r in _records(document)}
    assert issues["x1"] is None  # a top-level input
    assert issues["x2"] is None  # a continuous function of top-level inputs
    assert "p.r" in str(issues["x3"]) and "clock edge" in str(issues["x3"])  # a register
    assert "p.r" in str(issues["x4"])  # a net reading a register
    assert "p.u_q.r" in str(issues["x5"])  # the default disable reads a register
    assert issues["x6"] is None  # an explicit disable replaces the default
    assert issues["x7"] is None  # an input port connected to a top-level input
    assert "delay" in str(issues["x8"])  # a delayed net


_PORT_EXPRESSIONS = """module arb(input logic clk, req_chk_i, a, b);
  y: assert property (@(posedge clk) disable iff (!req_chk_i) a |-> b);
endmodule
module p(input logic clk, rst_n, a, b);
  logic r = 1'b0;
  always_ff @(posedge clk) r <= a;
  arb u_const(.clk(clk), .req_chk_i(1'b1), .a(a), .b(b));
  arb u_expr(.clk(clk), .req_chk_i(rst_n && !a), .a(a), .b(b));
  arb u_reg(.clk(clk), .req_chk_i(r || rst_n), .a(a), .b(b));
endmodule
"""


def test_disable_through_an_input_port_connected_to_an_expression() -> None:
    """23: such a port is a continuous assignment of the expression (§23.3.3)."""
    document = export_texts([("p.sv", _PORT_EXPRESSIONS)], SlangOptions(top=("p",)))
    issues = {r["scope"]: r["sva2yosys_disableIssue"] for r in _records(document)}
    assert issues["p.u_const"] is None  # a constant connection (OpenTitan prim_arbiter)
    assert issues["p.u_expr"] is None  # an expression of top-level inputs
    assert "p.r" in str(issues["p.u_reg"])  # an expression reading a register


def test_disable_through_a_cyclic_port_expression_is_rejected() -> None:
    """23. Counterexample (review): a port connected to an expression
    of itself recursed without end."""
    text = """module leaf(input logic clk, rst, a);
  y: assert property (@(posedge clk) disable iff (rst) a);
endmodule
module p(input logic clk, a);
  leaf u(.clk(clk), .rst(!u.rst), .a(a));
endmodule
"""
    document = export_texts([("p.sv", text)], SlangOptions(top=("p",)))
    (record,) = _records(document)
    assert "cycle" in str(record["sva2yosys_disableIssue"])


def test_disable_through_repeated_port_expressions_is_fast() -> None:
    """23. Counterexample (review): two ports per level, each connected
    to an expression of both ports of the level above, nested 40 deep, were traversed
    2^40 times."""
    depth = 40
    modules = [
        "module n0(input logic clk, ra, rb, a);\n"
        "  y: assert property (@(posedge clk) disable iff (ra) a);\nendmodule\n"
    ]
    for level in range(1, depth + 1):
        modules.append(
            f"module n{level}(input logic clk, ra, rb, a);\n"
            f"  n{level - 1} u(.clk(clk), .ra(ra && rb), .rb(rb || ra), .a(a));\n"
            "endmodule\n"
        )
    document = export_texts([("n.sv", "".join(modules))], SlangOptions(top=(f"n{depth}",)))
    (record,) = _records(document)
    assert record["sva2yosys_disableIssue"] is None


_TWINS = """module sync(input logic clk, rst_src_ni, rst_dst_ni, a, b);
  wire effective_rst_n;
  assign effective_rst_n = rst_src_ni && rst_dst_ni;
  y: assert property (@(posedge clk) disable iff (!effective_rst_n) a |-> b);
endmodule
module p(input logic clk, rst_ni, rst_ref_ni, a, b);
  logic r = 1'b0;
  always_ff @(posedge clk) r <= a;
  sync u0(.clk(clk), .rst_src_ni(rst_ni), .rst_dst_ni(rst_ref_ni), .a(a), .b(b));
  sync u1(.clk(clk), .rst_src_ni(rst_ref_ni), .rst_dst_ni(rst_ni), .a(a), .b(b));
  sync u2(.clk(clk), .rst_src_ni(1'b1), .rst_dst_ni(rst_ni), .a(a), .b(b));
  sync u3(.clk(clk), .rst_src_ni(r), .rst_dst_ni(rst_ni), .a(a), .b(b));
endmodule
"""


def test_disable_in_every_one_of_identical_instances() -> None:
    """23: Slang analyses the drivers of one instance per identical body (OpenTitan
    prim_sync_reqack); each instance is judged by its own connections."""
    document = export_texts([("p.sv", _TWINS)], SlangOptions(top=("p",)))
    issues = {r["scope"]: r["sva2yosys_disableIssue"] for r in _records(document)}
    assert issues["p.u0"] is None
    assert issues["p.u1"] is None
    assert issues["p.u2"] is None  # a constant connection
    assert "p.r" in str(issues["p.u3"])  # the twin's connection is not borrowed


_DRIVEN_PORTS = """module dp(input wire clk, rst_n, a, b);
  assign rst_n = a;
  y: assert property (@(posedge clk) disable iff (!rst_n) a |-> b);
endmodule
module p(input logic clk, rst_n, a, b);
  dp u0(.clk(clk), .rst_n(rst_n), .a(a), .b(b));
  dp u1(.clk(clk), .rst_n(rst_n), .a(a), .b(b));
endmodule
"""


def test_disable_on_an_input_port_also_driven_inside_in_every_instance() -> None:
    """23. Counterexample (review): Slang gives no drivers in the
    instance it does not analyse, where the port's own assignment was missed."""
    document = export_texts([("p.sv", _DRIVEN_PORTS)], SlangOptions(top=("p",)))
    issues = {r["scope"]: r["sva2yosys_disableIssue"] for r in _records(document)}
    assert issues["p.u0"] is not None
    assert issues["p.u1"] is not None


_SAMPLED_DEFAULTS = """
module sd(input logic clk, input logic il, input bit ib);
  wire w;
  logic u;
  bit b;
  int i;
  logic z = 1'b0;
  p: assert property (@(posedge clk) $past(w) && $past(u) && $past(b) && $past(i)
                      && $past(z) && $past(il) && $past(ib));
endmodule
"""


def _named_values(node: object) -> dict[str, object]:
    found: dict[str, object] = {}
    if isinstance(node, list):
        for item in node:
            found.update(_named_values(item))
    elif isinstance(node, dict):
        if node.get("kind") == "NamedValue":
            found[str(node["symbol"])] = node.get("sva2yosys_sampledDefault")
        for value in node.values():
            found.update(_named_values(value))
    return found


def test_sampled_default_of_named_values() -> None:
    """24: the class of the default sampled value (IEEE 1800-2017 section 16.5.1), by hand:
    4-state nets and uninitialised 4-state variables and ports are X; 2-state types and
    initialised variables are known."""
    document = export_texts([("sd.sv", _SAMPLED_DEFAULTS)], SlangOptions(top=("sd",)))
    (record,) = _records(document)
    values = _named_values(record["propertySpec"])
    assert values == {
        "clk": "x",
        "w": "x",
        "u": "x",
        "b": "known",
        "i": "known",
        "z": "known",
        "il": "x",
        "ib": "known",
    }


def test_conversions_carry_whether_the_target_type_is_four_state() -> None:
    """24: ``bit'(a)`` converts to a 2-state type (X becomes 0, IEEE 1800 §6.11.2),
    ``logic'(b)`` and ``signed'(v)`` to 4-state types. Origin: a review."""
    source = (
        "module s(input logic clk, a, input bit b, input logic [1:0] v);\n"
        "  p0: assert property (@(posedge clk) bit'(a));\n"
        "  p1: assert property (@(posedge clk) logic'(b));\n"
        "  p2: assert property (@(posedge clk) signed'(v) == 0);\n"
        "endmodule\n"
    )
    doc = export_texts([("s.sv", source)], SlangOptions(top=("s",)))
    for name, expected in (("p0", False), ("p1", True), ("p2", True)):
        conversions = [n for n in _walk(_by_name(doc, "s", name)) if n.get("kind") == "Conversion"]
        assert conversions, name
        assert conversions[0]["sva2yosys_fourState"] is expected, name


_INTEGRAL_TYPES = """module it(input logic clk);
  typedef enum {A, B, C} e_t;
  typedef enum logic [1:0] {D, E, F} e2_t;
  typedef logic [7:0] byte_t;
  typedef struct packed {logic [3:0] a; logic b;} s_t;
  e_t e; e2_t e2; byte_t bt; s_t s;
  logic [3:0][7:0] m; logic [0:3] rev;
  int i; integer ig; byte by; int unsigned iu; logic l;
  logic [7:0] u [4];
  real r;
  p: assert property (@(posedge clk)
    e == A && e2 == D && bt == 0 && s == s && m == m && rev == 0 && i == 0 && ig == 0
    && by == 0 && iu == 0 && l && u[0] == 0 && r == 0.0);
endmodule
"""


def _named_by_symbol(node: JsonValue) -> dict[str, JsonObject]:
    return {str(n["symbol"]): n for n in _walk(node) if n.get("kind") == "NamedValue"}


def test_integral_type_description_of_expressions() -> None:
    """26: ``sva2yosys_integral`` is ``{width, signed, msb, lsb, dims}`` (plus ``aggregate``
    for a packed struct or union). Expected values by hand from IEEE 1800-2017 §6.11, §6.19,
    §7.2, §7.4: a default enum has the base type ``int`` (32 bits, signed, 2-state); a typedef
    is its canonical type; ``logic [3:0][7:0]`` has two packed dimensions, the outer one
    ``[3:0]``; ``logic [0:3]`` keeps its direction; a scalar integral type (``int``, ``byte``,
    ``logic``) has no packed dimension (dims 0) and the range ``[width-1:0]``; a packed struct
    is one flat vector (``[4:0]`` for 4 + 1 bits). An unpacked array and ``real`` have ``false``, no
    field. Origin: a review."""
    doc = export_texts([("it.sv", _INTEGRAL_TYPES)], SlangOptions(top=("it",)))
    (record,) = _records(doc)
    nodes = _named_by_symbol(record["propertySpec"])

    def field(name: str) -> JsonValue:
        return nodes[name].get("sva2yosys_integral")

    def vec(width: int, signed: bool, msb: int, lsb: int, dims: int) -> JsonObject:
        return {"width": width, "signed": signed, "msb": msb, "lsb": lsb, "dims": dims}

    assert field("e") == vec(32, True, 31, 0, 1)
    assert field("e2") == vec(2, False, 1, 0, 1)
    assert field("bt") == vec(8, False, 7, 0, 1)
    assert field("s") == {**vec(5, False, 4, 0, 1), "aggregate": True}
    assert field("m") == vec(32, False, 3, 0, 2)
    assert field("rev") == vec(4, False, 0, 3, 1)
    assert field("i") == vec(32, True, 31, 0, 0)
    assert field("ig") == vec(32, True, 31, 0, 0)
    assert field("by") == vec(8, True, 7, 0, 0)
    assert field("iu") == vec(32, False, 31, 0, 0)
    assert field("l") == vec(1, False, 0, 0, 0)
    assert field("u") is False
    assert field("r") is False
    # Operators carry it too: ``e == A`` is a 1-bit unsigned (4-state) value.
    equalities = [n for n in _walk(record["propertySpec"]) if n.get("op") == "Equality"]
    assert equalities
    assert all(n["sva2yosys_integral"] == vec(1, False, 0, 0, 0) for n in equalities)


def test_conversion_kinds_are_exported() -> None:
    """27: the conversions Slang writes carry their kind (``Propagated`` for an operand
    converted to its context, ``Explicit`` for a cast). Origin: a review."""
    source = (
        "module i(input logic clk, input logic [3:0] v, input logic signed [3:0] s);\n"
        "  p: assert property (@(posedge clk) v + s == 4'd1);\n"
        "  q: assert property (@(posedge clk) signed'(v) == s);\n"
        "endmodule\n"
    )
    doc = export_texts([("i.sv", source)], SlangOptions(top=("i",)))
    propagated = [n for n in _walk(_by_name(doc, "i", "p")) if n.get("kind") == "Conversion"]
    assert propagated
    assert {n["sva2yosys_conversionKind"] for n in propagated} == {"Propagated"}
    explicit = [n for n in _walk(_by_name(doc, "i", "q")) if n.get("kind") == "Conversion"]
    assert "Explicit" in {n["sva2yosys_conversionKind"] for n in explicit}


def test_inside_is_exported() -> None:
    """27: ``inside`` is ``Inside`` with ``left`` and ``ranges`` (a range item is
    ``ValueRange`` with ``left`` and ``right``). Origin: a review."""
    source = (
        "module i(input logic clk, input logic [3:0] v, input logic signed [3:0] s);\n"
        "  p: assert property (@(posedge clk) v inside {1, [2:3], 4'b1?0?, s});\n"
        "  q: assert property (@(posedge clk) signed'(v) == s);\n"
        "endmodule\n"
    )
    doc = export_texts([("i.sv", source)], SlangOptions(top=("i",)))
    (inside,) = [n for n in _walk(_by_name(doc, "i", "p")) if n.get("kind") == "Inside"]
    assert _obj(inside["left"])["kind"] == "Conversion"
    items = inside["ranges"]
    assert isinstance(items, list)
    assert [_obj(i)["kind"] for i in items] == [
        "Conversion",
        "ValueRange",
        "Conversion",
        "Conversion",
    ]
    assert set(_obj(items[1])) >= {"left", "right"}
    assert _obj(_obj(items[2])["operand"])["value"] == "4'b1z0z"
    kinds = {
        n["sva2yosys_conversionKind"]
        for n in _walk(_by_name(doc, "i", "p"))
        if n.get("kind") == "Conversion"
    }
    assert kinds == {"Propagated"}


def test_inside_in_a_bound_marks_the_records() -> None:
    """27: an `inside` that Slang evaluates to an integer (a delay bound) makes every record
    carry `sva2yosys_insideIssue`; a Boolean `inside` and a `$past` argument do not. Origin:
    second re-review."""
    boolean = (
        "module i(input logic clk, input logic [3:0] v);\n"
        "  p: assert property (@(posedge clk) v inside {1} ##1 $past(v inside {2}));\n"
        "endmodule\n"
    )
    bound = (
        "module i(input logic clk, input logic [3:0] v);\n"
        "  p: assert property (@(posedge clk) v[0] ##(1 + (4'd1 inside {1})) v[1]);\n"
        "endmodule\n"
    )
    ok = export_texts([("i.sv", boolean)], SlangOptions(top=("i",)))
    bad = export_texts([("i.sv", bound)], SlangOptions(top=("i",)))
    assert "sva2yosys_insideIssue" not in _by_name(ok, "i", "p")
    assert isinstance(_by_name(bad, "i", "p")["sva2yosys_insideIssue"], str)
