"""Functional contract of the command-line interface (see CONTRACT.md in this folder)."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from importlib.metadata import entry_points, version
from pathlib import Path

import pytest

from support.toolchain import SbyStatus, Tool, run_sby
from sva2yosys.cli import main
from sva2yosys.rewrite import MONITORS_FILE, rewrite, syntax_items
from sva2yosys.slang.convert import convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import LibrarySearch, SlangOptions, load_design

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    status = main(list(args))
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_console_script_resolves_to_main() -> None:
    """1."""
    (script,) = [ep for ep in entry_points(group="console_scripts") if ep.name == "sva2yosys"]
    assert script.value == "sva2yosys.cli:main"
    assert script.load() is main


def test_module_help() -> None:
    """1."""
    completed = subprocess.run(
        [sys.executable, "-m", "sva2yosys", "--help"], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0
    assert "usage: sva2yosys" in completed.stdout


def test_accepted_design(capsys: pytest.CaptureFixture[str]) -> None:
    """2."""
    status, out, _ = _run(capsys, str(FIXTURES / "accepted.sv"))
    lines = out.splitlines()
    assert status == 0
    ok = [line for line in lines if line.startswith("ok")]
    assert len(ok) == 3
    assert any(
        "accepted.handshake" in line and "assert" in line and "slots=3" in line for line in ok
    )
    assert any("accepted.(unnamed)" in line and "slots=0" in line for line in ok)  # req |=> ack
    assert any("accepted.no_both" in line and "assume" in line for line in ok)
    assert "3 assertion(s): 3 accepted, 0 rejected" in lines[-1]


def test_rejections_are_located(capsys: pytest.CaptureFixture[str]) -> None:
    """3, 5."""
    status, out, _ = _run(capsys, str(FIXTURES / "mixed.sv"), "--top", "mixed", "-D", "WITH_GOOD")
    lines = out.splitlines()
    assert status == 1
    rejected = [line for line in lines if line.startswith("rejected")]
    assert len(rejected) == 2
    assert any(f"{FIXTURES / 'mixed.sv'}:6:" in line and "liveness" in line for line in rejected)
    assert any("mixed.two_clocks" in line and "multiclock" in line for line in rejected)
    assert any(line.startswith("ok") and "mixed.good" in line for line in lines)
    assert "3 assertion(s): 1 accepted, 2 rejected" in lines[-1]


def test_first_sample_rejection_is_reported(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """3: rejections after conversion (validation, first-sample rule) are reported too."""
    source = tmp_path / "f.sv"
    source.write_text(
        "module f(input logic clk, a, b, c);\n"
        "  q: assert property (@(posedge clk) ($past(a) || b) |-> c);\n"
        "endmodule\n"
    )
    status, out, _ = _run(capsys, str(source))
    assert status == 1
    assert f"rejected  {source}:2:" in out
    assert "f.q:" in out
    assert "first sample" in out


def test_per_attempt_machine_is_reported(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """2a: a nested implication is `form=attempt`."""
    source = tmp_path / "f.sv"
    source.write_text(
        "module f(input logic clk, a, b, c);\n"
        "  q: assert property (@(posedge clk) a |-> (b |-> c));\n"
        "endmodule\n"
    )
    status, out, _ = _run(capsys, str(source))
    assert status == 0
    assert "slots=0  form=attempt  bits=" in out
    assert "1 assertion(s): 1 accepted, 0 rejected" in out


def test_mixed_forms_are_joined(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """2a: the conjuncts of a property `and` are joined, canonical and per-attempt."""
    source = tmp_path / "f.sv"
    source.write_text(
        "module f(input logic clk, a, b, c, d);\n"
        "  q: assert property (@(posedge clk) (a |-> b) and (c |-> (d |-> a)));\n"
        "endmodule\n"
    )
    status, out, _ = _run(capsys, str(source))
    assert status == 0
    assert "form=direct+attempt" in out
    assert "1 assertion(s): 1 accepted, 0 rejected" in out


@pytest.mark.parametrize(
    ("property_text", "fragment"),
    [
        ("(a |-> b) or (c |-> d)", "cover of this property is not supported"),
        ("not (a |-> (b |-> c))", "cover of this property is not supported"),
    ],
)
def test_cover_rule_rejection_is_reported(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, property_text: str, fragment: str
) -> None:
    """3: a cover whose nonvacuity is unknown is rejected by validation, located."""
    source = tmp_path / "f.sv"
    source.write_text(
        "module f(input logic clk, a, b, c, d);\n"
        f"  q: cover property (@(posedge clk) {property_text});\n"
        "endmodule\n"
    )
    status, out, _ = _run(capsys, str(source))
    assert status == 1
    assert f"rejected  {source}:2:" in out
    assert "f.q:" in out
    assert fragment in out


def test_relative_paths_in_locations(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """3: locations repeat the path as given."""
    monkeypatch.chdir(FIXTURES.parent)
    _, out, _ = _run(capsys, str(Path("fixtures") / "mixed.sv"))
    assert f"rejected  {Path('fixtures') / 'mixed.sv'}:6:" in out


def test_define_controls_preprocessing(capsys: pytest.CaptureFixture[str]) -> None:
    """5."""
    _, out, _ = _run(capsys, str(FIXTURES / "mixed.sv"))
    assert "mixed.good" not in out
    assert "2 assertion(s): 0 accepted, 2 rejected" in out


def test_include_directory(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """5."""
    top = tmp_path / "top.sv"
    top.write_text('`include "accepted.sv"\n')
    status, out, _ = _run(capsys, str(top), "-I", str(FIXTURES))
    assert status == 0
    assert "3 accepted" in out


def test_compile_error(capsys: pytest.CaptureFixture[str]) -> None:
    """4."""
    status, out, err = _run(capsys, str(FIXTURES / "broken.sv"))
    assert status == 1
    assert "missing_signal" in err
    assert out == ""


def test_no_input_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """6."""
    with pytest.raises(SystemExit) as info:
        main([])
    assert info.value.code == 2


COUNTER = FIXTURES.parents[1] / "rewrite" / "fixtures" / "counter"
DESIGN = COUNTER / "design"
PROPS = COUNTER / "verify" / "counter_props.sv"


def test_generation_writes_the_rewrite(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """7."""
    out_dir = tmp_path / "out"
    status, out, _ = _run(
        capsys, str(PROPS), "-y", str(DESIGN), "--top", "counter", "-o", str(out_dir)
    )
    assert status == 0
    search = LibrarySearch(directories=(DESIGN,))
    sources = load_design([PROPS], search, SlangOptions(top=("counter",)))
    document = export_texts(sources, SlangOptions(top=("counter",)))
    expected = rewrite(sources, frozenset({str(PROPS)}), syntax_items(document), convert(document))
    assert [f.name for f in expected.files] == ["counter_props.sv", "counter.sv"]
    for output in expected.files:
        assert (out_dir / output.name).read_text() == output.text
    assert (out_dir / MONITORS_FILE).read_text() == expected.monitors
    listed = (out_dir / "sva2yosys.f").read_text().splitlines()
    assert listed == [MONITORS_FILE, "counter.sv", "counter_props.sv"]
    wrote = [line for line in out.splitlines() if line.startswith("wrote ")]
    assert len(wrote) == 5


def test_library_directories_load_only_what_is_needed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """9: bind targets, --top modules and their instances are found; nothing else."""
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "counter.sv").write_text((DESIGN / "counter.sv").read_text())
    (rtl / "top.sv").write_text(
        "module top(input logic clk, rst, en, output logic [3:0] cnt);\n"
        "  counter u_counter(.clk(clk), .rst(rst), .en(en), .cnt(cnt));\nendmodule\n"
    )
    (rtl / "unused.sv").write_text(
        "module unused(input logic c);\n  assert property (@(posedge c) c);\nendmodule\n"
    )
    out_dir = tmp_path / "out"
    status, out, _ = _run(capsys, str(PROPS), "-y", str(rtl), "--top", "top", "-o", str(out_dir))
    assert status == 0, out
    listed = (out_dir / "sva2yosys.f").read_text().splitlines()
    assert listed == [
        MONITORS_FILE,
        "counter.sv",
        str((rtl / "top.sv").resolve()),
        "counter_props.sv",
    ]
    status, out, _ = _run(capsys, str(PROPS), "-y", str(rtl), "--top", "top")
    assert status == 0
    assert "top.u_counter.u_props.a_steps" in out


def test_filelists(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """9: -F with design files, -y and +define+."""
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "counter.sv").write_text((DESIGN / "counter.sv").read_text())
    (rtl / "rtl.f").write_text("// the design\n-y .\n+define+WITH_STEPS\n")
    props = PREPROC / "verify" / "counter_props.sv"
    out_dir = tmp_path / "out"
    status, _, err = _run(
        capsys, str(props), "-F", str(rtl / "rtl.f"), "--top", "counter", "-o", str(out_dir)
    )
    assert status == 0, err
    script = (out_dir / "sva2yosys_read.ys").read_text()
    assert "-DWITH_STEPS=1" in script
    assert "__sva2yosys_i0" in (out_dir / "counter_props.sv").read_text()
    (rtl / "bad.f").write_text("+unknown+opt\n")
    status, _, err = _run(capsys, str(props), "-F", str(rtl / "bad.f"))
    assert status == 1
    assert "unsupported filelist option +unknown+opt" in err


def test_packages_are_read_first(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """7: a package of the design precedes the files that import it."""
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "widths.sv").write_text("package widths;\n  localparam int W = 4;\nendpackage\n")
    (rtl / "counter.sv").write_text((DESIGN / "counter.sv").read_text())
    props = tmp_path / "props.sv"
    props.write_text(
        "module chk import widths::*; (input logic clk, input logic [W-1:0] cnt);\n"
        "  assert property (@(posedge clk) cnt != 4'd15 |=> cnt != 4'd0);\nendmodule\n"
        "bind counter chk u_chk(.clk(clk), .cnt(cnt));\n"
    )
    out_dir = tmp_path / "out"
    status, _, err = _run(
        capsys, str(props), "-y", str(rtl), "--top", "counter", "-o", str(out_dir)
    )
    assert status == 0, err
    listed = (out_dir / "sva2yosys.f").read_text().splitlines()
    assert listed[0] == MONITORS_FILE
    assert sorted(listed[1:]) == sorted(
        [str((rtl / "widths.sv").resolve()), "counter.sv", "props.sv"]
    )
    assert listed.index(str((rtl / "widths.sv").resolve())) < listed.index("props.sv")


def test_generation_errors_write_nothing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """8."""
    status, _, err = _run(
        capsys, str(FIXTURES / "mixed.sv"), "--top", "mixed", "-o", str(tmp_path / "o")
    )
    assert status == 1
    assert "error:" in err and "unbounded" in err
    assert not (tmp_path / "o").exists()


def test_output_never_replaces_an_input(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """8."""
    design = tmp_path / "counter.sv"
    design.write_text((DESIGN / "counter.sv").read_text())
    original = design.read_text()
    status, out, err = _run(
        capsys, str(PROPS), "-v", str(design), "--top", "counter", "-o", str(tmp_path)
    )
    assert status == 1
    assert "would overwrite an input file" in err
    assert out == ""
    assert design.read_text() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["counter.sv"]


def test_written_files_use_newline_line_ends(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """7."""
    status, _, _ = _run(
        capsys, str(PROPS), "-y", str(DESIGN), "--top", "counter", "-o", str(tmp_path / "out")
    )
    assert status == 0
    for path in (tmp_path / "out").iterdir():
        assert b"\r" not in path.read_bytes(), path.name


def test_output_names_must_be_unique(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """8: an input named like the file list."""
    verify = tmp_path / "in" / "sva2yosys.f"
    verify.parent.mkdir()
    verify.write_text(
        "module p(input logic clk, a);\n  assert property (@(posedge clk) a);\nendmodule\n"
    )
    status, out, err = _run(capsys, str(verify), "--top", "p", "-o", str(tmp_path / "o"))
    assert status == 1
    assert "two output files would be named sva2yosys.f" in err
    assert out == ""
    assert not (tmp_path / "o").exists()


def test_temporary_files_never_touch_existing_files(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """7: an untouched input named like a naive temporary file."""
    lookalike = tmp_path / "sva2yosys_monitors.sv.tmp"
    lookalike.write_text("module keep; endmodule\n")
    status, _, _ = _run(
        capsys,
        str(PROPS),
        "-v",
        str(lookalike),
        "-y",
        str(DESIGN),
        "--top",
        "counter",
        "-o",
        str(tmp_path),
    )
    assert status == 0
    assert lookalike.read_text() == "module keep; endmodule\n"
    assert not list(tmp_path.glob(".sva2yosys-*"))


PREPROC = FIXTURES / "preproc"


def _generate(
    capsys: pytest.CaptureFixture[str], out: Path, design: Path, *extra: str
) -> tuple[int, str, str]:
    props = PREPROC / "verify" / "counter_props.sv"
    return _run(capsys, str(props), "-v", str(design), "--top", "counter", "-o", str(out), *extra)


def test_read_script_keeps_the_preprocessing_environment(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """7."""
    design = COUNTER / "design" / "counter.sv"
    out = tmp_path / "out"
    extra = ("-D", "WITH_STEPS", "-I", str(tmp_path))
    assert _generate(capsys, out, design, *extra)[0] == 0
    lines = (out / "sva2yosys_read.ys").read_text().splitlines()
    head = "read_verilog -formal -sv -DWITH_STEPS=1"
    assert lines == [
        f"{head} -I{tmp_path.resolve()} {(out / MONITORS_FILE).resolve()}",
        f"{head} -I{design.parent.resolve()} -I{tmp_path.resolve()} "
        f"{(out / 'counter.sv').resolve()}",
        f"{head} -I{(PREPROC / 'verify').resolve()} -I{tmp_path.resolve()} "
        f"{(out / 'counter_props.sv').resolve()}",
    ]


def test_an_include_shadowed_in_the_output_directory_is_an_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """8."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "glue.svh").write_text("// another glue\n")
    status, _, err = _generate(capsys, out, COUNTER / "design" / "counter.sv", "-D", "WITH_STEPS")
    assert status == 1
    assert 'the include "glue.svh"' in err
    assert sorted(p.name for p in out.iterdir()) == ["glue.svh"]


def test_design_sva_is_an_error_unless_allowed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """7, 8."""
    design = tmp_path / "counter.sv"
    text = (COUNTER / "design" / "counter.sv").read_text()
    design.write_text(
        text.replace("endmodule", "  a: assert property (@(posedge clk) 1);\nendmodule")
    )
    status, _, err = _generate(capsys, tmp_path / "o", design, "-D", "WITH_STEPS")
    assert status == 1
    assert "assertion in design file" in err
    assert not (tmp_path / "o").exists()
    status, out, _ = _generate(
        capsys, tmp_path / "o", design, "-D", "WITH_STEPS", "--allow-design-sva"
    )
    assert status == 0
    assert "warning: assertion in design file" in out


_SBY = """[tasks]
{mode}

[options]
mode {mode}
depth 12

[engines]
{engine}

[script]
script {script}
prep -top counter
"""


@pytest.mark.parametrize("design", ["design", "mutant"])
def test_read_script_under_sby(
    capsys: pytest.CaptureFixture[str], sby_tool: Tool, tmp_path: Path, design: str
) -> None:
    """7: the generated read script reproduces the preprocessing environment."""
    out = tmp_path / "out"
    source = COUNTER.parent / "counter" / design / "counter.sv"
    assert _generate(capsys, out, source, "-D", "WITH_STEPS")[0] == 0
    mode, engine = ("prove", "abc pdr") if design == "design" else ("bmc", "smtbmc yices")
    sby = tmp_path / "check.sby"
    script = (out / "sva2yosys_read.ys").resolve().as_posix()
    sby.write_text(_SBY.format(mode=mode, engine=engine, script=script))
    result = run_sby(sby_tool, sby, mode, tmp_path / "work")
    if design == "design":
        assert result.status is SbyStatus.PASS, result.log[-3000:]
    else:
        assert result.status is SbyStatus.FAIL, result.log[-3000:]
        assert [hit.step for hit in result.failed_assertions] == [8]


def test_top_is_required_with_design_files(capsys: pytest.CaptureFixture[str]) -> None:
    """6."""
    with pytest.raises(SystemExit) as info:
        main([str(PROPS), "-y", str(DESIGN)])
    assert info.value.code == 2
    assert "--top is required" in capsys.readouterr().err


def test_a_verification_module_never_stands_in_for_the_top(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """9: --top is looked up in the design even when a verification file declares it."""
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "top.sv").write_text("module top(input logic clk); endmodule\n")
    props = tmp_path / "props.sv"
    props.write_text(
        "module top(input logic clk);\n  assert property (@(posedge clk) 1'b1);\nendmodule\n"
    )
    status, out, err = _run(capsys, str(props), "-y", str(rtl), "--top", "top")
    assert status == 1, out
    assert "'top' is declared in several files" in err


def test_packages_are_read_in_dependency_order(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """7: a package importing another one declared in a later file."""
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "consumer.sv").write_text(
        "package consumer;\n  import base::*;\n  localparam int W2 = W;\nendpackage\n"
    )
    (rtl / "base.sv").write_text("package base;\n  localparam int W = 4;\nendpackage\n")
    (rtl / "counter.sv").write_text((DESIGN / "counter.sv").read_text())
    props = tmp_path / "props.sv"
    props.write_text(
        "module chk import consumer::*; (input logic clk, input logic [W2-1:0] cnt);\n"
        "  assert property (@(posedge clk) cnt != 4'd15 |=> cnt != 4'd0);\nendmodule\n"
        "bind counter chk u_chk(.clk(clk), .cnt(cnt));\n"
    )
    out_dir = tmp_path / "out"
    status, _, err = _run(
        capsys,
        str(props),
        "-v",
        str(rtl / "consumer.sv"),
        "-v",
        str(rtl / "base.sv"),
        "-y",
        str(rtl),
        "--top",
        "counter",
        "-o",
        str(out_dir),
    )
    assert status == 0, err
    listed = (out_dir / "sva2yosys.f").read_text().splitlines()
    assert listed == [
        MONITORS_FILE,
        str((rtl / "base.sv").resolve()),
        str((rtl / "consumer.sv").resolve()),
        "counter.sv",
        "props.sv",
    ]


def test_package_cycles_between_files_are_refused(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """8."""
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "a.sv").write_text(
        "package pa;\n  localparam int A = 1;\nendpackage\n"
        "package pc;\n  localparam int C = pb::B;\nendpackage\n"
    )
    (rtl / "b.sv").write_text("package pb;\n  localparam int B = pa::A;\nendpackage\n")
    props = tmp_path / "props.sv"
    props.write_text(
        "module p(input logic clk);\n  localparam int X = pc::C;\n"
        "  assert property (@(posedge clk) 1'b1);\nendmodule\n"
    )
    status, _, err = _run(
        capsys,
        str(props),
        "-v",
        str(rtl / "a.sv"),
        "-v",
        str(rtl / "b.sv"),
        "--top",
        "p",
        "-o",
        str(tmp_path / "out"),
    )
    assert status == 1
    assert "form a cycle" in err
    assert not (tmp_path / "out").exists()


_OPTIMISABLE = (
    "module p(input logic clk, rst, a, b, c, d);\n"
    "  wide: assert property (@(posedge clk) disable iff (rst) a |-> ##[1:16] b);\n"
    "  chain: assert property (@(posedge clk) disable iff (rst) a |-> ##[1:4] b ##[1:4] c);\n"
    "  plain: assert property (@(posedge clk) a |-> b);\n"
    "endmodule\n"
)


def test_strategy_selects_the_forms(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """10."""
    props = tmp_path / "p.sv"
    props.write_text(_OPTIMISABLE)
    status, out, _ = _run(capsys, str(props))
    assert status == 0
    assert out.count("form=direct") == 3
    status, out, _ = _run(capsys, str(props), "--strategy", "auto")
    assert status == 0
    lines = {line.split()[1]: line for line in out.splitlines() if line.startswith("ok")}
    assert "form=window  bits=6" in lines["p.wide"]
    assert "form=direct" in lines["p.chain"]  # auto never picks history
    assert "form=direct" in lines["p.plain"]
    status, out, _ = _run(capsys, str(props), "--strategy", "history")
    assert status == 0
    lines = {line.split()[1]: line for line in out.splitlines() if line.startswith("ok")}
    assert "form=history" in lines["p.wide"] and "form=history" in lines["p.chain"]


# review: retain a positive default CLI equivalence-job control.
@pytest.mark.parametrize(
    ("strategy", "form", "expected"),
    [
        ("auto", "window", ["__sva2yosys_p0"]),
        pytest.param(
            "history",
            "history",
            ["__sva2yosys_p0", "__sva2yosys_p1"],
            marks=pytest.mark.extended,
        ),
    ],
)
def test_equivalence_jobs_are_written_and_prove(
    capsys: pytest.CaptureFixture[str],
    sby_tool: Tool,
    tmp_path: Path,
    strategy: str,
    form: str,
    expected: list[str],
) -> None:
    """10."""
    props = tmp_path / "p.sv"
    props.write_text(_OPTIMISABLE)
    out_dir = tmp_path / "out"
    status, _, err = _run(
        capsys, str(props), "--strategy", strategy, "--equivalence", "-o", str(out_dir)
    )
    assert status == 0, err
    monitors = (out_dir / MONITORS_FILE).read_text()
    assert f"// form: {form}" in monitors
    jobs = sorted(path.parent.name for path in (out_dir / "equivalence").glob("*/equivalence.sby"))
    assert jobs == expected
    for job in jobs:
        directory = out_dir / "equivalence" / job
        result = run_sby(sby_tool, directory / "equivalence.sby", "prove", tmp_path / f"w_{job}")
        assert result.status is SbyStatus.PASS, result.log[-3000:]


def test_equivalence_needs_an_output_directory(capsys: pytest.CaptureFixture[str]) -> None:
    """6."""
    with pytest.raises(SystemExit) as info:
        main([str(FIXTURES / "accepted.sv"), "--equivalence"])
    assert info.value.code == 2


_UNBOUNDED = (
    "module p(input logic clk, a, b, c);\n"
    "  delay: assert property (@(posedge clk) a ##[1:$] b |-> ##[1:8] c);\n"
    "  rep: assume property (@(posedge clk) a[*1:$] ##1 b |=> c);\n"
    "  gone: assert property (@(posedge clk) b[->2] |-> c);\n"
    "  alw: assert property (@(posedge clk) always (a |-> b));\n"
    "  covered: cover property (@(posedge clk) a ##[1:$] b |-> c);\n"
    "  late: assume property (@(posedge clk) a |-> strong(##[1:$] b));\n"
    "endmodule\n"
)


@pytest.mark.parametrize("strategy", ["direct", "auto", "window", "history"])
def test_unbounded_antecedents_have_the_direct_form_only(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, strategy: str
) -> None:
    """11: accepted whatever ``--strategy`` says (the optimised forms do not apply to a loop
    in the antecedent); covers and unbounded consequents are still rejected, located."""
    props = tmp_path / "p.sv"
    props.write_text(_UNBOUNDED)
    status, out, _ = _run(capsys, str(props), "--strategy", strategy)
    lines = out.splitlines()
    assert status == 1
    ok = {line.split()[1]: line for line in lines if line.startswith("ok")}
    assert set(ok) == {"p.delay", "p.rep", "p.gone", "p.alw"}
    assert all("form=direct" in line for line in ok.values()), ok
    rejected = [line for line in lines if line.startswith("rejected")]
    assert len(rejected) == 2
    assert any(
        f"{props}:6:" in line
        and "p.covered" in line
        and "a cover of an implication with an unbounded antecedent" in line
        for line in rejected
    )
    assert any(
        # (a strong consequent is an assert's liveness; an assume's is rejected)
        f"{props}:7:" in line and "p.late" in line and "(liveness) in 'assume'" in line
        for line in rejected
    )
    assert "6 assertion(s): 4 accepted, 2 rejected" in lines[-1]


_CONFIGURATIONS = (
    "module q(input logic clk, a, b, c);\n"
    "  never_fails: assert property (@(posedge clk) a |-> ##[1:$] b);\n"
    "  loop: assert property (@(posedge clk) a |-> b[*1:$] ##1 c);\n"
    "  goto: assume property (@(posedge clk) a |=> b[->2]);\n"
    "  alw: assert property (@(posedge clk) always (a |-> b[*1:$] ##1 c));\n"
    "  strong_one: assume property (@(posedge clk) a |-> strong(##[1:$] b));\n"
    "  negated: assert property (@(posedge clk) not (a ##[1:$] b));\n"
    "  zero: assert property (@(posedge clk) a |-> ##[1:$] 1'b0);\n"
    "  covered: cover property (@(posedge clk) a |-> ##[1:$] b);\n"
    "endmodule\n"
)


@pytest.mark.parametrize("strategy", ["direct", "auto", "window", "history"])
def test_weak_unbounded_consequents_use_the_configuration_machine(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, strategy: str
) -> None:
    """12: accepted whatever ``--strategy`` says, warned about only where the consequent can
    never fail (``never_fails`` and ``zero``, not ``loop``, ``goto``, ``alw``); the
    others are rejected with a located message."""
    props = tmp_path / "q.sv"
    props.write_text(_CONFIGURATIONS)
    status, out, err = _run(capsys, str(props), "--strategy", strategy)
    lines = out.splitlines()
    assert status == 1
    ok = {line.split()[1]: line for line in lines if line.startswith("ok")}
    assert set(ok) == {
        "q.never_fails",
        "q.loop",
        "q.goto",
        "q.alw",
        "q.zero",
        "q.negated",
        "q.covered",
    }
    assert all("form=configurations" in line and "slots=0" in line for line in ok.values()), ok
    warned = {line.split()[2].rstrip(":") for line in lines if line.startswith("warning")}
    assert warned == {"q.never_fails", "q.zero"}  # (not `negated`: it fails at a match)
    # `not (a ##[1:$] b)` is strong (not of a weak sequence): a liveness part
    assert ok["q.negated"].endswith("live=witness"), ok["q.negated"]
    assert "warning" not in err.lower()
    rejected = {line.split()[2].rstrip(":"): line for line in lines if line.startswith("rejected")}
    assert set(rejected) == {"q.strong_one"}
    # (a constant-false condition is evaluated at its own sample, so it is a
    # legal weak unbounded consequent that never fails; 4d: the cover is accepted)
    assert "8 assertion(s): 7 accepted, 1 rejected" in lines[-1]


def test_a_property_over_the_configurations_budget_is_rejected_located(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """12."""
    props = tmp_path / "big.sv"
    props.write_text(
        "module big(input logic clk, a, b, c);\n"
        "  huge: assert property (@(posedge clk) a |-> ##[1:$] b ##12 c);\n"
        "endmodule\n"
    )
    status, out, _ = _run(capsys, str(props))
    assert status == 1
    assert f"{props}:2:" in out and "configurations" in out


def test_configuration_monitors_are_generated(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """12."""
    props = tmp_path / "g.sv"
    props.write_text(
        "module g(input logic clk, a, b, c);\n"
        "  p: assert property (@(posedge clk) a |-> b[*1:$] ##1 c);\n"
        "endmodule\n"
    )
    out_dir = tmp_path / "out"
    status, _, _ = _run(capsys, str(props), "-o", str(out_dir))
    assert status == 0
    assert "form: configurations" in (out_dir / "sva2yosys_monitors.sv").read_text()


_UNTIL = (
    "module u(input logic clk, a, b, c);\n"
    "  imp: assert property (@(posedge clk) a |-> b until c);\n"
    "  nonover: assume property (@(posedge clk) a |=> b until_with c);\n"
    "  top: assert property (@(posedge clk) b until c);\n"
    "  alw: assert property (@(posedge clk) always (a |-> b until c));\n"
    "  strong_one: assume property (@(posedge clk) a |-> b s_until c);\n"
    "  strong_with: assume property (@(posedge clk) a |-> b s_until_with c);\n"
    "  negated: assert property (@(posedge clk) not (b until c));\n"
    "  covered: cover property (@(posedge clk) b until c);\n"
    "endmodule\n"
)


@pytest.mark.parametrize("strategy", ["direct", "auto", "window", "history"])
def test_weak_until_uses_the_configuration_machine(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, strategy: str
) -> None:
    """13: accepted whatever ``--strategy`` says; a cover is rejected with a located message
    that names ``until``. The strong assumes (``s_until``, one configuration)
    are accepted as fairness assumptions."""
    props = tmp_path / "u.sv"
    props.write_text(_UNTIL)
    status, out, _ = _run(capsys, str(props), "--strategy", strategy)
    lines = out.splitlines()
    assert status == 1
    ok = {line.split()[1]: line for line in lines if line.startswith("ok")}
    assert set(ok) == {
        "u.imp",
        "u.nonover",
        "u.top",
        "u.alw",
        "u.negated",
        "u.strong_one",
        "u.strong_with",
    }
    assert all("form=configurations" in line for line in ok.values()), ok
    # (`not (b until c)` is strong, a liveness part; the others are weak;
    # the strong assumes have the pending bit as their fairness part)
    assert ok["u.negated"].endswith("live=pending")
    assert ok["u.strong_one"].endswith("live=pending")
    assert ok["u.strong_with"].endswith("live=pending")
    rejected = {line.split()[2].rstrip(":"): line for line in lines if line.startswith("rejected")}
    assert set(rejected) == {"u.covered"}
    assert "a cover of 'until' is not supported" in rejected["u.covered"]
    assert "8 assertion(s): 7 accepted, 1 rejected" in lines[-1]


_COVERS = (
    "module r(input logic clk, a, b, c);\n"
    "  delay: cover property (@(posedge clk) a ##[1:$] b);\n"
    "  goto: cover property (@(posedge clk) b[->2]);\n"
    "  impl: cover property (@(posedge clk) a |-> ##[1:$] b);\n"
    "  nonov: cover property (@(posedge clk) a |=> b[*1:$] ##1 c);\n"
    "  strong_one: cover property (@(posedge clk) strong(a ##[1:$] b));\n"
    "  weak_one: cover property (@(posedge clk) weak(a ##[1:$] b));\n"
    "  until_one: cover property (@(posedge clk) a until b);\n"
    "  until_with_one: cover property (@(posedge clk) a until_with b);\n"
    "  antecedent: cover property (@(posedge clk) a ##[1:$] b |-> c);\n"
    "  lengths: cover property (@(posedge clk) a ##[1:2] b |-> c[*1:$]);\n"
    "  alw: cover property (@(posedge clk) always (a |-> b));\n"
    "  eventual: cover property (@(posedge clk) s_eventually b);\n"
    "  negated: cover property (@(posedge clk) not (a ##[1:$] b));\n"
    "  either: cover property (@(posedge clk) (a |-> b) or (b ##[1:$] c));\n"
    "endmodule\n"
)


@pytest.mark.parametrize("strategy", ["direct", "auto", "window", "history"])
def test_covers_of_unbounded_sequences_use_the_configuration_machine(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, strategy: str
) -> None:
    """14: accepted whatever ``--strategy`` says; the others are rejected, located, with the
    reason."""
    props = tmp_path / "r.sv"
    props.write_text(_COVERS)
    status, out, _ = _run(capsys, str(props), "--strategy", strategy)
    lines = out.splitlines()
    assert status == 1
    ok = {line.split()[1]: line for line in lines if line.startswith("ok")}
    assert set(ok) == {"r.delay", "r.goto", "r.impl", "r.nonov", "r.strong_one"}
    assert all(
        "form=configurations" in line and "cover" in line and "slots=0" in line
        for line in ok.values()
    ), ok
    rejected = {line.split()[2].rstrip(":"): line for line in lines if line.startswith("rejected")}
    assert set(rejected) == {
        "r.weak_one",
        "r.until_one",
        "r.until_with_one",
        "r.antecedent",
        "r.lengths",
        "r.alw",
        "r.eventual",
        "r.negated",
        "r.either",
    }
    located = {
        name: f"{props}:{number}:"
        for number, name in enumerate(
            [
                "delay",
                "goto",
                "impl",
                "nonov",
                "strong_one",
                "weak_one",
                "until_one",
                "until_with_one",
                "antecedent",
                "lengths",
                "alw",
                "eventual",
                "negated",
                "either",
            ],
            start=2,
        )
    }
    for name, line in rejected.items():
        assert located[name.removeprefix("r.")] in line, line
    assert "a cover of a weak unbounded sequence is not supported" in rejected["r.weak_one"]
    assert "a cover of 'until' is not supported" in rejected["r.until_one"]
    assert "a cover of 'until_with' is not supported" in rejected["r.until_with_one"]
    assert "a cover of an implication with an unbounded antecedent" in rejected["r.antecedent"]
    assert "a single match length" in rejected["r.lengths"]
    assert "14 assertion(s): 5 accepted, 9 rejected" in lines[-1]


def test_cover_monitors_of_unbounded_sequences_are_generated(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """14."""
    props = tmp_path / "g.sv"
    props.write_text(
        "module g(input logic clk, a, b, c);\n"
        "  p: cover property (@(posedge clk) a |-> b[*1:$] ##1 c);\n"
        "endmodule\n"
    )
    out_dir = tmp_path / "out"
    status, _, _ = _run(capsys, str(props), "-o", str(out_dir))
    assert status == 0
    text = (out_dir / "sva2yosys_monitors.sv").read_text()
    assert "form: configurations" in text and "cover (success)" in text


_STRONG = (
    "module s(input logic clk, a, b, c);\n"
    "  pend: assert property (@(posedge clk) a |-> s_eventually b);\n"
    "  wit: assert property (@(posedge clk) a |-> strong(##[1:$] b));\n"
    "  both: assert property (@(posedge clk)\n"
    "    (a |-> s_eventually b) and (b |-> strong(##[1:$] c)));\n"
    "  loose: assert property (@(posedge clk) a |-> ##[1:$] b);\n"
    "  plain: assert property (@(posedge clk) a |-> ##1 b);\n"
    "  cov: cover property (@(posedge clk) strong(a ##[1:$] b));\n"
    "  fair: assume property (@(posedge clk) a |-> s_eventually b);\n"
    "endmodule\n"
)


def test_strong_directives_report_their_liveness_kind(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """17: ``live=pending`` / ``live=witness`` (``+`` between the parts of a property
    ``and``), nothing for a directive without a liveness part. A strong
    ``assume`` of the one-configuration class is accepted with ``live=pending`` (its fairness
    constraint), which warns about the accepted cover (point 19)."""
    props = tmp_path / "s.sv"
    props.write_text(_STRONG)
    status, out, _ = _run(capsys, str(props))
    lines = out.splitlines()
    assert status == 0
    ok = {line.split()[1]: line for line in lines if line.startswith("ok")}
    assert set(ok) == {"s.pend", "s.wit", "s.both", "s.loose", "s.plain", "s.cov", "s.fair"}
    assert ok["s.pend"].endswith("form=configurations  bits=1  live=pending"), ok["s.pend"]
    assert ok["s.fair"].endswith("form=configurations  bits=1  live=pending"), ok["s.fair"]
    assert ok["s.wit"].endswith("live=witness")
    assert ok["s.both"].endswith("live=pending+witness")
    for name in ("s.loose", "s.plain", "s.cov"):
        assert "live=" not in ok[name]
    assert not [line for line in lines if line.startswith("rejected")]
    assert "7 assertion(s): 7 accepted, 0 rejected" in lines[-1]


_NEGATED = (
    "module n(input logic clk, a, b, c);\n"
    "  weak_until: assert property (@(posedge clk) not (a until b));\n"
    "  weak_delay: assert property (@(posedge clk) not (a ##[1:$] b));\n"
    "  explicit: assert property (@(posedge clk) not weak(a ##[1:$] b));\n"
    "  impl: assert property (@(posedge clk) c |-> not (a ##[1:$] b));\n"
    "  strong_delay: assert property (@(posedge clk) not strong(a ##[1:$] b));\n"
    "  strong_until: assert property (@(posedge clk) not (a s_until b));\n"
    "  not_eventually: assert property (@(posedge clk) not s_eventually b);\n"
    "  assumed_strong: assume property (@(posedge clk) not strong(a ##[1:$] b));\n"
    "  assumed_weak: assume property (@(posedge clk) not (a ##[1:$] b));\n"
    "  covered: cover property (@(posedge clk) not (a ##[1:$] b));\n"
    "endmodule\n"
)


def test_not_of_an_unbounded_sequence_reports_its_liveness_kind(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """18: ``not`` of a weak sequence (the default, ``weak(...)``, ``until``) is
    strong: ``live=pending`` (one configuration) or ``live=witness``; ``not strong(...)``,
    ``not s_until`` and ``not s_eventually`` are weak, with no ``live=`` field and accepted as an
    ``assume`` too; an ``assume`` of ``not`` of a weak sequence and any cover are rejected."""
    props = tmp_path / "n.sv"
    props.write_text(_NEGATED)
    status, out, _ = _run(capsys, str(props))
    lines = out.splitlines()
    assert status == 1
    ok = {line.split()[1]: line for line in lines if line.startswith("ok")}
    assert set(ok) == {
        "n.weak_until",
        "n.weak_delay",
        "n.explicit",
        "n.impl",
        "n.strong_delay",
        "n.strong_until",
        "n.not_eventually",
        "n.assumed_strong",
    }
    assert all("form=configurations" in line for line in ok.values()), ok
    assert ok["n.weak_until"].endswith("live=pending")
    for name in ("n.weak_delay", "n.explicit", "n.impl"):
        assert ok[name].endswith("live=witness"), name
    for name in ("n.strong_delay", "n.strong_until", "n.not_eventually", "n.assumed_strong"):
        assert "live=" not in ok[name], name
    rejected = {line.split()[2].rstrip(":"): line for line in lines if line.startswith("rejected")}
    assert set(rejected) == {"n.assumed_weak", "n.covered"}
    assert "(liveness) in 'assume'" in rejected["n.assumed_weak"]
    assert "10 assertion(s): 8 accepted, 2 rejected" in lines[-1]


def test_the_live_read_script_is_written_for_a_negated_weak_sequence_only(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """18: ``sva2yosys_read_live.ys`` is written when a ``not weak(s)`` directive has a liveness
    part, and not when only ``not strong(s)`` directives (weak) are accepted."""
    for prop, written in (("not (a until b)", True), ("not strong(a ##[1:$] b)", False)):
        props = tmp_path / "w.sv"
        props.write_text(
            "module w(input logic clk, a, b);\n"
            f"  p: assert property (@(posedge clk) {prop});\n"
            "endmodule\n"
        )
        out_dir = tmp_path / f"out_{written}"
        status, out, _ = _run(capsys, str(props), "-o", str(out_dir))
        assert status == 0, out
        assert (out_dir / "sva2yosys_read_live.ys").exists() is written


def test_the_live_read_script_is_written_only_for_a_liveness_part(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """17: ``sva2yosys_read_live.ys`` has the commands of ``sva2yosys_read.ys`` with
    ``-DSVA2YOSYS_LIVE``, and is not written when no directive has a liveness part."""
    props = tmp_path / "s.sv"
    props.write_text(_STRONG.replace("  fair: assume", "  //"))
    out_dir = tmp_path / "out"
    status, out, _ = _run(capsys, str(props), "-D", "X=1", "-o", str(out_dir))
    assert status == 0, out
    assert f"wrote {out_dir / 'sva2yosys_read_live.ys'}" in out
    plain = (out_dir / "sva2yosys_read.ys").read_text().splitlines()
    live = (out_dir / "sva2yosys_read_live.ys").read_text().splitlines()
    assert len(plain) == len(live) >= 2
    for before, after in zip(plain, live, strict=True):
        assert "SVA2YOSYS_LIVE" not in before
        assert after == before.replace("-DX=1", "-DX=1 -DSVA2YOSYS_LIVE")
    assert "-DSVA2YOSYS_LIVE" in live[0] and (out_dir / "sva2yosys.f").read_text()

    weak_only = tmp_path / "w.sv"
    weak_only.write_text(
        "module w(input logic clk, a, b);\n"
        "  p: assert property (@(posedge clk) a |-> ##[1:$] b);\n"
        "endmodule\n"
    )
    out_dir = tmp_path / "weak"
    status, out, _ = _run(capsys, str(weak_only), "-o", str(out_dir))
    assert status == 0
    assert not (out_dir / "sva2yosys_read_live.ys").exists()
    assert "sva2yosys_read_live.ys" not in out


_COVER_WARNING = (
    "this cover's result ignores the fairness assumptions (strong assume) of this run: "
    "a reached cover is provisional, see docs/usage.md"
)

_FAIR = (
    "module f(input logic clk, a, b, c);\n"
    "  fair: assume property (@(posedge clk) a |-> s_eventually b);\n"
    "  early: cover property (@(posedge clk) a ##1 b);\n"
    "  chk: assert property (@(posedge clk) a |-> s_eventually b);\n"
    "  late: cover property (@(posedge clk) a ##[1:$] b);\n"
    "  nope: cover property (@(posedge clk) not (a ##[1:$] b));\n"
    "endmodule\n"
)


def test_a_strong_assume_is_accepted_with_its_liveness_kind_and_the_live_script(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """19: ``assume property (a |-> s_eventually b)`` is ``ok`` with
    ``live=pending`` and the live read script is written; an assume that would need a witness
    is ``rejected`` with the located message."""
    props = tmp_path / "f.sv"
    props.write_text(
        "module f(input logic clk, a, b);\n"
        "  fair: assume property (@(posedge clk) a |-> s_eventually b);\n"
        "  hold: assert property (@(posedge clk) a |-> ##1 b);\n"
        "  wit: assume property (@(posedge clk) a |-> strong(##[1:$] b));\n"
        "endmodule\n"
    )
    out_dir = tmp_path / "out"
    status, out, _ = _run(capsys, str(props))
    lines = out.splitlines()
    assert status == 1
    (fair,) = [line for line in lines if line.startswith("ok") and "f.fair" in line]
    assert "assume" in fair and fair.endswith("live=pending")
    (hold,) = [line for line in lines if line.startswith("ok") and "f.hold" in line]
    assert "live=" not in hold
    (wit,) = [line for line in lines if line.startswith("rejected")]
    assert f"{props}:4:" in wit and "f.wit" in wit
    assert "a strong unbounded property (liveness) in 'assume'" in wit
    assert "witness" in wit
    # Rewriting rejects the whole run if any directive is rejected. Write only the accepted ones.
    props.write_text(
        props.read_text().replace(
            "  wit: assume property (@(posedge clk) a |-> strong(##[1:$] b));\n", ""
        )
    )
    status, out, err = _run(capsys, str(props), "-o", str(out_dir))
    assert status == 0, err
    assert f"wrote {out_dir / 'sva2yosys_read_live.ys'}" in out


def test_every_accepted_cover_is_warned_about_next_to_a_fairness_assumption(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """19: a run with an accepted assume with a liveness part gives each accepted cover a located
    ``warning`` line right after its ``ok`` line (like the never-fails warning); a rejected cover
    gets none; the summary and the exit status are unchanged."""
    props = tmp_path / "f.sv"
    props.write_text(_FAIR)
    status, out, _ = _run(capsys, str(props))
    lines = out.splitlines()
    assert status == 1  # (the third cover is rejected: not of an unbounded sequence)
    for name, line_number in (("early", 3), ("late", 5)):
        (index,) = [
            i for i, line in enumerate(lines) if line.startswith("ok") and f"f.{name} " in line
        ]
        warning = lines[index + 1]
        text = _FAIR.splitlines()[line_number - 1]
        column = text.index(f"{name}:") + 1
        assert warning.startswith(f"warning   {props}:{line_number}:{column}  ")
        assert f"f.{name}: {_COVER_WARNING}" in warning
    warnings = [line for line in lines if line.startswith("warning")]
    assert len(warnings) == 2 and all(_COVER_WARNING in w for w in warnings)
    assert not [line for line in warnings if "f.nope" in line or "f.chk" in line]
    assert "5 assertion(s): 4 accepted, 1 rejected" in lines[-1]


def test_no_cover_warning_without_a_fairness_assumption(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """19: covers next to a strong assert, a weak assume or a ``not strong`` assume (no
    fairness constraint in the run) get no warning."""
    for other in (
        "assert property (@(posedge clk) a |-> s_eventually b)",
        "assume property (@(posedge clk) a |-> ##1 b)",
        "assume property (@(posedge clk) not strong(a ##[1:$] b))",
    ):
        props = tmp_path / "g.sv"
        props.write_text(
            "module g(input logic clk, a, b);\n"
            f"  o: {other};\n"
            "  c: cover property (@(posedge clk) a ##1 b);\n"
            "endmodule\n"
        )
        status, out, _ = _run(capsys, str(props))
        assert status == 0, other
        assert "fairness" not in out, other


def _project_version() -> str:
    root = Path(__file__).resolve().parents[3]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    release = project["version"]
    assert isinstance(release, str)
    return release


def test_version_option_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    """22."""
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == f"sva2yosys {version('sva2yosys')}\n"
    assert captured.err == ""


def test_the_version_is_written_once_and_is_semantic() -> None:
    """22."""
    release = _project_version()
    assert re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", release)
    assert version("sva2yosys") == release
    root = Path(__file__).resolve().parents[3]
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    (entry,) = [package for package in lock["package"] if package["name"] == "sva2yosys"]
    assert entry["version"] == release


_PARTS = ("p0", "p1", "p2_c0", "p2_c1")  # pend, wit, and both (two parts) of _STRONG


def test_one_live_read_script_is_written_for_each_liveness_part(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """24: SBY's ``live`` mode decides a task by the first justice verdict, so every
    liveness part of an ``assert`` has a read script of its own, ``sva2yosys_read_live_<part>.ys``:
    the commands of ``sva2yosys_read.ys`` with ``-DSVA2YOSYS_LIVE_<part>``, and the fairness
    define beside it exactly when a fairness ``assume`` is present. The combined script stays."""
    props = tmp_path / "s.sv"
    props.write_text(_STRONG)
    out_dir = tmp_path / "out"
    status, out, _ = _run(capsys, str(props), "-D", "X=1", "-o", str(out_dir))
    assert status == 0, out
    written = sorted(path.name for path in out_dir.glob("sva2yosys_read_live_*.ys"))
    assert written == sorted(f"sva2yosys_read_live_{part}.ys" for part in _PARTS)
    plain = (out_dir / "sva2yosys_read.ys").read_text().splitlines()
    for part in _PARTS:
        assert f"wrote {out_dir / f'sva2yosys_read_live_{part}.ys'}" in out
        lines = (out_dir / f"sva2yosys_read_live_{part}.ys").read_text().splitlines()
        assert lines == [
            line.replace("-DX=1", f"-DX=1 -DSVA2YOSYS_LIVE_{part} -DSVA2YOSYS_LIVE_FAIR")
            for line in plain
        ]
    assert (out_dir / "sva2yosys_read_live.ys").exists()

    unfair = tmp_path / "u.sv"
    unfair.write_text(_STRONG.replace("  fair: assume", "  //"))
    out_dir = tmp_path / "unfair"
    status, out, _ = _run(capsys, str(unfair), "-D", "X=1", "-o", str(out_dir))
    assert status == 0, out
    plain = (out_dir / "sva2yosys_read.ys").read_text().splitlines()
    lines = (out_dir / "sva2yosys_read_live_p0.ys").read_text().splitlines()
    assert lines == [line.replace("-DX=1", "-DX=1 -DSVA2YOSYS_LIVE_p0") for line in plain]


def test_several_liveness_parts_warn_that_one_task_reports_only_the_first(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """24: with more than one liveness part in a design, each part gets a warning at its
    directive naming its script; a design with one part gets none."""
    props = tmp_path / "s.sv"
    props.write_text(_STRONG)
    status, out, _ = _run(capsys, str(props), "-o", str(tmp_path / "out"))
    assert status == 0, out
    warned = [line for line in out.splitlines() if "one `live` task" in line]
    assert len(warned) == len(_PARTS)
    for part in _PARTS:
        assert sum(f"sva2yosys_read_live_{part}.ys" in line for line in warned) == 1

    single = tmp_path / "one.sv"
    single.write_text(
        "module one(input logic clk, a, b);\n"
        "  p: assert property (@(posedge clk) a |-> s_eventually b);\n"
        "endmodule\n"
    )
    status, out, _ = _run(capsys, str(single), "-o", str(tmp_path / "one"))
    assert status == 0, out
    assert "one `live` task" not in out
    assert (tmp_path / "one" / "sva2yosys_read_live_p0.ys").exists()
    weak = tmp_path / "w.sv"
    weak.write_text(
        "module w(input logic clk, a, b);\n"
        "  p: assert property (@(posedge clk) a |-> ##[1:$] b);\n"
        "endmodule\n"
    )
    status, out, _ = _run(capsys, str(weak), "-o", str(tmp_path / "weak"))
    assert not list((tmp_path / "weak").glob("sva2yosys_read_live_*.ys"))


def test_an_instanced_liveness_part_warns_that_no_define_separates_its_instances(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """24: one monitor serves every elaborated instance of its assertion and the define
    switches the monitor on, so a single liveness assertion in a module instantiated twice is two
    parts in one task; the warning says that no define separates them. The same assertion in a
    design that instantiates the module once has no warning."""
    source = (
        "module m(input logic clk, a, b);\n"
        "  p: assert property (@(posedge clk) a |-> s_eventually b);\n"
        "endmodule\n"
        "module two(input logic clk, a, b, c);\n"
        "  m u1(.clk, .a, .b);\n"
        "  m u2(.clk, .a(c), .b);\n"
        "endmodule\n"
        "module one(input logic clk, a, b);\n"
        "  m u1(.clk, .a, .b);\n"
        "endmodule\n"
    )
    props = tmp_path / "m.sv"
    props.write_text(source)
    status, out, _ = _run(capsys, str(props), "--top", "two", "-o", str(tmp_path / "two"))
    assert status == 0, out
    warned = [line for line in out.splitlines() if "2 liveness parts in one `live` task" in line]
    assert len(warned) == 1, out
    assert "2 elaborated instances share one monitor, which no define separates" in warned[0]
    status, out, _ = _run(capsys, str(props), "--top", "one", "-o", str(tmp_path / "one"))
    assert status == 0, out
    assert "liveness parts in one" not in out
