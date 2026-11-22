"""Consequents that can never fail in the rewrite (CONTRACT.md, point 15)."""

from __future__ import annotations

from sva2yosys.rewrite import Rewrite, Severity, rewrite, syntax_items
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions

TEXT = """module m(input logic clk, a, b, c);
  w1: assert property (@(posedge clk) a |-> ##[1:$] b);
  w2: assume property (@(posedge clk) a |-> ##[1:$] b);
  w3: assert property (@(posedge clk) (a |-> b) and (a |-> ##[1:$] c));
  n1: assert property (@(posedge clk) a |-> b[*1:$]);
  n2: cover property (@(posedge clk) a ##[1:$] b);
  for (genvar i = 0; i < 2; i++) begin : g
    w4: assert property (@(posedge clk) a |-> ##[1:$] c);
  end
endmodule
"""


def _translate(text: str) -> Rewrite:
    sources = [("m.sv", text)]
    document = export_texts(sources, SlangOptions(top=("m",)))
    return rewrite(sources, frozenset({"m.sv"}), syntax_items(document), convert(document))


def test_a_warning_per_site_that_cannot_fail_located_at_the_directive() -> None:
    """15."""
    result = _translate(TEXT)
    warnings = [d for d in result.warnings if "can never fail" in d.message]
    assert all(d.severity is Severity.WARNING for d in warnings)
    assert [(d.source.line if d.source else 0) for d in warnings] == [2, 3, 4, 8]
    by_name = {d.message.split(":")[0]: d.message for d in warnings}
    assert "this assert can never fail:" in by_name["m.w1"]
    assert "this assume can never fail, so it constrains nothing:" in by_name["m.w2"]
    assert "conjunct 2 of 2 of this assert can never fail:" in by_name["m.w3"]
    assert not [d for d in result.warnings if ".n1" in d.message or ".n2" in d.message]


def test_two_elaborated_instances_of_one_site_warn_once() -> None:
    """15: the loop body is one site elaborated twice."""
    result = _translate(TEXT)
    in_loop = [d for d in result.warnings if d.source is not None and d.source.line == 8]
    assert len(in_loop) == 1
