"""Binds with the ``slang`` frontend (CONTRACT.md point 23)."""

from __future__ import annotations

import re

import pytest

from sva2yosys.passes.frontend import YosysFrontend
from sva2yosys.rewrite import FileRole, Rewrite, RewriteError, Severity, rewrite, syntax_items
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions

NL = chr(10)

COUNTER_DESIGN = NL.join(
    [
        "module counter(input logic clk, rst, en, output logic [3:0] cnt);",
        "  always_ff @(posedge clk) if (rst) cnt <= 4'd0; else if (en) cnt <= cnt + 1'b1;",
        "endmodule",
        "",
    ]
)
COUNTER_PROPS = NL.join(
    [
        "module counter_props(input logic clk, rst, en, input logic [3:0] cnt);",
        "  a_reset: assert property (@(posedge clk) rst |=> cnt == 4'd0);",
        "  c_wrap: cover property (@(posedge clk) cnt == 4'd15);",
        "endmodule",
        "",
        "bind counter counter_props u_props (",
        "    .clk(clk), .rst(rst), .en(en), .cnt(cnt)",
        ");",
        "",
    ]
)
BIND = re.compile(r"bind counter counter_props u_props \(.*?\);", re.DOTALL)


def _run(
    sources: list[tuple[str, str]],
    top: str,
    frontend: YosysFrontend | None = None,
) -> Rewrite:
    document = export_texts(sources, SlangOptions(top=(top,)))
    verification = frozenset(name for name, _ in sources if name.startswith("verify/"))
    items = syntax_items(document)
    conversion = convert(document)
    if frontend is None:
        return rewrite(sources, verification, items, conversion, Severity.ERROR)
    return rewrite(sources, verification, items, conversion, Severity.ERROR, frontend=frontend)


def _counter(frontend: YosysFrontend | None = None) -> Rewrite:
    sources = [("design/counter.sv", COUNTER_DESIGN), ("verify/counter_props.sv", COUNTER_PROPS)]
    return _run(sources, "counter", frontend)


def _text(result: Rewrite, source: str) -> str:
    (text,) = [f.text for f in result.files if f.source == source]
    return text


def test_the_default_frontend_is_verilog_and_changes_nothing() -> None:
    plain = _counter()
    explicit = _counter(YosysFrontend.VERILOG)
    assert [(f.source, f.role, f.text) for f in plain.files] == [
        (f.source, f.role, f.text) for f in explicit.files
    ]
    assert plain.monitors == explicit.monitors
    assert any(f.role is FileRole.DESIGN_COPY for f in plain.files)


def test_binds_are_kept_and_no_design_file_is_copied() -> None:
    slang = _counter(YosysFrontend.SLANG)
    assert [f.role for f in slang.files] == [FileRole.VERIFICATION]
    kept = _text(slang, "verify/counter_props.sv")
    (bind,) = BIND.findall(COUNTER_PROPS)
    assert bind in kept


def test_the_written_file_differs_from_the_default_one_only_in_the_kept_bind() -> None:
    slang = _text(_counter(YosysFrontend.SLANG), "verify/counter_props.sv")
    default = _text(_counter(), "verify/counter_props.sv")
    blanked = BIND.sub(lambda match: re.sub(r"[^\n]", "", match.group(0)), slang)
    assert blanked == default
    assert _counter(YosysFrontend.SLANG).monitors == _counter().monitors


def test_a_bind_into_a_verification_module_is_kept() -> None:
    text = NL.join(
        [
            "module chk(input logic clk, a);",
            "  assert property (@(posedge clk) a);",
            "endmodule",
            "module host(input logic clk, a);",
            "endmodule",
            "bind host chk u_chk (.clk(clk), .a(a));",
            "",
        ]
    )
    result = _run([("verify/v.sv", text)], "host", YosysFrontend.SLANG)
    assert [f.role for f in result.files] == [FileRole.VERIFICATION]
    assert "bind host chk u_chk (.clk(clk), .a(a));" in result.files[0].text


def test_an_instance_bind_is_still_refused() -> None:
    props = NL.join(
        [
            "module chk(input logic clk, a);",
            "  assert property (@(posedge clk) a);",
            "endmodule",
            "bind top.u1 chk u_chk (.clk(clk), .a(a));",
            "",
        ]
    )
    design = NL.join(
        [
            "module leaf(input logic clk, a); endmodule",
            "module top(input logic clk, a); leaf u1(.clk(clk), .a(a)); endmodule",
            "",
        ]
    )
    sources = [("design/d.sv", design), ("verify/p.sv", props)]
    with pytest.raises(RewriteError) as caught:
        _run(sources, "top", YosysFrontend.SLANG)
    assert any("instance bind" in str(d) for d in caught.value.diagnostics)


SAFE = "  p_safe: assert property (@(posedge clk) a |-> ##1 b);"
LIVE = "  p_live: assert property (@(posedge clk) a |-> s_eventually b);"
COVER = "  c: cover property (@(posedge clk) a ##1 b);"


def _props(*lines: str) -> str:
    return NL.join(["module live_props(input logic clk, a, b);", *lines, "endmodule", ""])


def test_a_liveness_part_is_refused_with_the_slang_frontend() -> None:
    sources = [("verify/live.sv", _props(SAFE, LIVE))]
    accepted = _run(sources, "live_props")
    assert accepted.live
    with pytest.raises(RewriteError) as caught:
        _run(sources, "live_props", YosysFrontend.SLANG)
    messages = [str(d) for d in caught.value.diagnostics]
    assert any(
        "live_props.p_live" in m and "--yosys-frontend slang" in m and "read_slang" in m
        for m in messages
    ), messages
    assert not any("live_props.p_safe" in m for m in messages)


def test_safety_directives_and_covers_are_accepted_with_the_slang_frontend() -> None:
    result = _run([("verify/live.sv", _props(SAFE, COVER))], "live_props", YosysFrontend.SLANG)
    assert not result.live
