"""The Yosys frontend option (CONTRACT.md point 23)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sva2yosys.cli import main

FIXTURES = Path(__file__).resolve().parent / "fixtures"
COUNTER = FIXTURES.parents[1] / "rewrite" / "fixtures" / "counter"
DESIGN = COUNTER / "design"
PROPS = COUNTER / "verify" / "counter_props.sv"

NL = chr(10)
STRONG = NL.join(
    [
        "module s(input logic clk, a, b);",
        "  safe: assert property (@(posedge clk) a |-> ##1 b);",
        "  live: assert property (@(posedge clk) a |-> s_eventually b);",
        "  cov: cover property (@(posedge clk) a ##1 b);",
        "endmodule",
        "",
    ]
)


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    status = main(list(args))
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def _files(directory: Path) -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(directory.iterdir()) if p.is_file()}


def test_the_default_frontend_gives_the_same_files_as_no_option(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    common = [str(PROPS), "-y", str(DESIGN), "--top", "counter"]
    _run(capsys, *common, "-o", str(tmp_path / "a"))
    _run(capsys, *common, "--yosys-frontend", "verilog", "-o", str(tmp_path / "b"))
    first, second = _files(tmp_path / "a"), _files(tmp_path / "b")
    assert first.keys() == second.keys()
    for name in first:
        left = first[name].replace(str(tmp_path / "a"), "<OUT>")
        right = second[name].replace(str(tmp_path / "b"), "<OUT>")
        assert left == right, name


def test_check_mode_refuses_a_liveness_part_with_slang(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    props = tmp_path / "s.sv"
    props.write_text(STRONG)
    status, out, _ = _run(capsys, str(props), "--yosys-frontend", "slang")
    lines = out.splitlines()
    assert status == 1
    ok = [line.split()[1] for line in lines if line.startswith("ok")]
    assert ok == ["s.safe", "s.cov"]
    (rejected,) = [line for line in lines if line.startswith("rejected")]
    assert "s.live" in rejected
    assert "--yosys-frontend slang" in rejected and "read_slang" in rejected
    assert "3 assertion(s): 2 accepted, 1 rejected" in lines[-1]
    status, out, _ = _run(capsys, str(props))
    assert status == 0
    assert "live=pending" in out


def test_generation_writes_nothing_when_a_liveness_part_is_refused(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    props = tmp_path / "s.sv"
    props.write_text(STRONG)
    out_dir = tmp_path / "out"
    status, _, err = _run(
        capsys, str(props), "--top", "s", "--yosys-frontend", "slang", "-o", str(out_dir)
    )
    assert status == 1
    assert "s.live" in err and "--yosys-frontend slang" in err
    assert not out_dir.exists() or not any(out_dir.iterdir())


def test_slang_with_output_needs_a_top(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as info:
        main([str(PROPS), "--yosys-frontend", "slang", "-o", str(tmp_path / "out")])
    assert info.value.code == 2
    assert "--top" in capsys.readouterr().err


def test_an_unknown_frontend_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main([str(PROPS), "--yosys-frontend", "icarus"])
    assert info.value.code == 2


def test_the_slang_files(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    status, _, err = _run(
        capsys,
        str(PROPS),
        "-y",
        str(DESIGN),
        "--top",
        "counter",
        "-D",
        "X=1",
        "--yosys-frontend",
        "slang",
        "-o",
        str(out_dir),
    )
    assert status == 0, err
    names = sorted(p.name for p in out_dir.iterdir())
    assert names == [
        "counter_props.sv",
        "sva2yosys.f",
        "sva2yosys_monitors.sv",
        "sva2yosys_read.ys",
    ]
    assert "bind counter counter_props u_props" in (out_dir / "counter_props.sv").read_text()
    script = (out_dir / "sva2yosys_read.ys").read_text().splitlines()
    assert script[0] == "plugin -i slang"
    (command,) = script[1:]
    words = command.split()
    assert words[:3] == ["read_slang", "--best-effort-hierarchy", "--allow-use-before-declare"]
    assert "-D" in words and "FORMAL" in words and "-DX=1" in words
    assert words[words.index("--top") + 1] == "counter"
    paths = [w for w in words if w.endswith(".sv")]
    assert [Path(p).name for p in paths] == [
        "sva2yosys_monitors.sv",
        "counter.sv",
        "counter_props.sv",
    ]
    assert Path(paths[1]) == (DESIGN / "counter.sv").resolve()
    assert Path(paths[2]) == (out_dir / "counter_props.sv").resolve()
    assert f"-I{PROPS.parent.resolve()}" in words
    listed = (out_dir / "sva2yosys.f").read_text().splitlines()
    assert listed == [
        "sva2yosys_monitors.sv",
        str((DESIGN / "counter.sv").resolve()),
        "counter_props.sv",
    ]
