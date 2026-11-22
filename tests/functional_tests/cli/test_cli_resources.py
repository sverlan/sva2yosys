"""Located resource diagnostics and explicit overrides (CONTRACT point 20)."""

from pathlib import Path

import pytest

from sva2yosys.cli import main


@pytest.mark.parametrize("kind", ["assert", "assume", "cover"])
@pytest.mark.parametrize("output", [False, True])
def test_huge_canonical_ranges_reject_without_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str, output: bool
) -> None:
    source = tmp_path / "huge.sv"
    source.write_text(
        f"module m(input clk,a,b);\np: {kind} property "
        "(@(posedge clk) a |-> ##1000000000 b);\nendmodule\n",
        encoding="utf-8",
    )
    destination = tmp_path / "out"
    args = [str(source), "-o", str(destination)] if output else [str(source)]
    assert main(args) == 1
    captured = capsys.readouterr()
    diagnostic = captured.out + captured.err
    assert "huge.sv:2:" in diagnostic
    assert "expansion" in diagnostic
    assert not destination.exists()


@pytest.mark.parametrize("output", [False, True])
def test_explicit_state_limit_is_shared_by_check_and_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], output: bool
) -> None:
    source = tmp_path / "limit.sv"
    source.write_text(
        "module m(input clk,a,b);\np: assert property (@(posedge clk) a |-> ##8 b);\nendmodule\n",
        encoding="utf-8",
    )
    destination = tmp_path / "out"
    args = [str(source), "--max-state-bits", "7"]
    if output:
        args += ["-o", str(destination)]
    assert main(args) == 1
    captured = capsys.readouterr()
    assert "7 temporal state bits" in (captured.out + captured.err)
    assert not destination.exists()
    args[2] = "8"
    assert main(args) == 0


@pytest.mark.parametrize(
    "flag,resource",
    [
        ("--max-expansion", "expansion"),
        ("--max-work", "construction"),
        ("--max-emission", "emission"),
    ],
)
@pytest.mark.parametrize("output", [False, True])
def test_other_overrides_are_shared_by_check_and_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    resource: str,
    output: bool,
) -> None:
    source = tmp_path / "small.sv"
    source.write_text(
        "module m(input clk,a,b);\np: assert property (@(posedge clk) a |-> ##8 b);\nendmodule\n",
        encoding="utf-8",
    )
    destination = tmp_path / "out"
    args = [str(source), flag, "0"]
    if output:
        args += ["-o", str(destination)]
    assert main(args) == 1
    captured = capsys.readouterr()
    assert "small.sv:2:" in captured.out + captured.err
    assert resource in captured.out + captured.err
    assert not destination.exists()
