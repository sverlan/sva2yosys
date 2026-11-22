"""Located compiler/emission exhaustion and overrides, CONTRACT point 19."""

from dataclasses import replace

import pytest

from sva2yosys.optimize.forms import Strategy
from sva2yosys.rewrite import RewriteError, rewrite, syntax_items
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.budgets import DEFAULT_LIMITS


@pytest.mark.parametrize(
    "prop,strategy,resource",
    [
        ("a |-> ##1000000000 b", Strategy.DIRECT, "expansion"),
        ("a |-> ##64 b", Strategy.HISTORY, "emission"),
    ],
)
def test_rewrite_returns_located_exhaustion_without_a_partial_result(
    prop: str,
    strategy: Strategy,
    resource: str,
) -> None:
    sources = [
        (
            "resources.sv",
            f"module m(input clk,a,b);\np: assert property (@(posedge clk) {prop});\nendmodule\n",
        )
    ]
    document = export_texts(sources, SlangOptions(top=("m",)))
    conversion = convert(document)
    limits = replace(DEFAULT_LIMITS, emission=5000)
    with pytest.raises(RewriteError) as caught:
        rewrite(
            sources,
            frozenset({"resources.sv"}),
            syntax_items(document),
            conversion,
            strategy=strategy,
            limits=limits,
        )
    diagnostic = "\n".join(map(str, caught.value.diagnostics))
    assert "resources.sv:2:" in diagnostic
    assert resource in diagnostic
    if resource == "emission":
        result = rewrite(
            sources,
            frozenset({"resources.sv"}),
            syntax_items(document),
            conversion,
            strategy=Strategy.DIRECT,
            limits=limits,
        )
        assert result.monitors
