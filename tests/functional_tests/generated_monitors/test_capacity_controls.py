"""Useful proof/failure/cover controls, CONTRACT point 22.

Origin: capacity probes. For unconstrained a/key and zero-initialized encoded
pipelines, decoding after N shifts gives a from N samples earlier. The correct
implementation satisfies every overlapping obligation. With N+1 shifts, a=1 at
sample zero and zero initialized history make b=0 at sample N: earliest failure N.
A cover cannot complete before N; a=1 at zero reaches it at N. IEEE 1800
16.12.7 and 16.16. The hand argument does not depend on a temporal-machine oracle.
"""

from pathlib import Path

import pytest

from support.toolchain import SbyStatus, Tool, run_sby
from sva2yosys.codegen.systemverilog import monitor_module, monitor_ports
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.compile import compile_monitors

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("n", [16, pytest.param(256, marks=pytest.mark.slow)])
@pytest.mark.parametrize("task", ["prove", "bmc", "cover"])
def test_encoded_pipeline_has_the_hand_derived_verdict(
    sby_tool: Tool, tmp_path: Path, n: int, task: str
) -> None:
    source = (FIXTURES / "capacity_source.sv").read_text(encoding="utf-8")
    source = source.replace("@KIND@", "cover" if task == "cover" else "assert")
    source = source.replace("@N@", str(n))
    conversion = convert(export_texts([("capacity.sv", source)], SlangOptions(top=("tc",))))
    assert not conversion.rejections
    (directive,) = conversion.directives
    (machine,) = compile_monitors(directive)
    assert monitor_ports(machine) == ("tc.a", "tc.b")
    (tmp_path / "monitor.sv").write_text(monitor_module(machine, "mon"), encoding="utf-8")
    bench = (FIXTURES / "capacity_tb.sv").read_text(encoding="utf-8")
    bench = bench.replace("@DELAY@", str(n + 1 if task == "bmc" else n))
    (tmp_path / "tb.sv").write_text(bench, encoding="utf-8")
    script = tmp_path / "capacity.sby"
    script.write_text(
        (FIXTURES / "capacity.sby").read_text(encoding="utf-8").replace("@DEPTH@", str(n + 3)),
        encoding="utf-8",
    )
    result = run_sby(sby_tool, script, task, tmp_path / "work")
    assert result.status is (SbyStatus.FAIL if task == "bmc" else SbyStatus.PASS), result.log[
        -4000:
    ]
    if task == "bmc":
        assert [hit.step for hit in result.failed_assertions] == [n]
    elif task == "cover":
        assert [hit.step for hit in result.reached_covers] == [n]
