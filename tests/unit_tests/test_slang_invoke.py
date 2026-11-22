"""Unit contract of sva2yosys.slang.invoke (docstrings are the contract)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sva2yosys.slang.invoke import (
    LibrarySearch,
    Severity,
    SlangError,
    SlangOptions,
    SourceLocation,
    compile_design,
    compile_texts,
    load_design,
)

_TWO_TOPS = "module a; endmodule\nmodule b; endmodule\n"


def _tops(text: str, options: SlangOptions) -> list[str]:
    compiled = compile_texts([("t.sv", text)], options)
    return sorted(inst.name for inst in compiled.compilation.getRoot().topInstances)


def test_automatic_top_selection() -> None:
    assert _tops(_TWO_TOPS, SlangOptions()) == ["a", "b"]


def test_explicit_top() -> None:
    assert _tops(_TWO_TOPS, SlangOptions(top=("b",))) == ["b"]


def test_defines_reach_the_preprocessor() -> None:
    text = "`ifdef WANT\nmodule w; endmodule\n`else\nmodule n; endmodule\n`endif\n"
    assert _tops(text, SlangOptions(defines=(("WANT", "1"),))) == ["w"]
    assert _tops(text, SlangOptions()) == ["n"]


def test_include_dirs(tmp_path: Path) -> None:
    (tmp_path / "inc.svh").write_text("module from_include; endmodule\n")
    text = '`include "inc.svh"\n'
    assert _tops(text, SlangOptions(include_dirs=(tmp_path,))) == ["from_include"]


def test_error_has_location_and_report() -> None:
    with pytest.raises(SlangError) as info:
        compile_texts(
            [("bad.sv", "module m(output logic y);\n  assign y = nope;\nendmodule\n")],
            SlangOptions(),
        )
    error = info.value
    assert error.diagnostics
    first = error.diagnostics[0]
    assert first.severity is Severity.ERROR
    assert first.location == SourceLocation("bad.sv", 2, 14)
    assert first.code == "UndeclaredIdentifier"
    assert "nope" in first.message
    assert "bad.sv:2:14" in error.report


def test_warnings_are_kept() -> None:
    text = "module m(input logic [3:0] a, output logic [1:0] y);\n  assign y = a;\nendmodule\n"
    compiled = compile_texts([("w.sv", text)], SlangOptions())
    assert any(d.severity is Severity.WARNING for d in compiled.warnings)


_UNDEFINED = (
    "module f(input logic clk);\n  assert property (@(posedge clk) undefined);\nendmodule\n"
)


def test_files_are_compiled_with_paths_as_given(tmp_path: Path) -> None:
    source = tmp_path / "f.sv"
    source.write_text(_UNDEFINED)
    with pytest.raises(SlangError) as info:
        compile_design([source], SlangOptions())
    location = info.value.diagnostics[0].location
    assert location is not None
    assert Path(location.file) == source


def test_relative_paths_are_kept_as_given(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.sv").write_text(_UNDEFINED)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SlangError) as info:
        compile_design([Path("sub") / "f.sv"], SlangOptions())
    location = info.value.diagnostics[0].location
    assert location is not None
    assert Path(location.file) == Path("sub") / "f.sv"


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SlangError):
        compile_design([tmp_path / "missing.sv"], SlangOptions())


def _library(tmp_path: Path) -> Path:
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "top.sv").write_text("module top; mid u(); endmodule\n")
    (rtl / "mid.sv").write_text("module mid; leaf u(); endmodule\n")
    (rtl / "leaf.v").write_text("module leaf; endmodule\n")
    (rtl / "pkg.sv").write_text("package pkg; localparam int W = 1; endpackage\n")
    (rtl / "unused.sv").write_text("module unused; endmodule\n")
    return rtl


def test_load_design_follows_binds_tops_instances_and_imports(tmp_path: Path) -> None:
    rtl = _library(tmp_path)
    props = tmp_path / "props.sv"
    props.write_text("module chk import pkg::*; (); endmodule\nbind mid chk u_chk();\n")
    texts = load_design([props], LibrarySearch(directories=(rtl,)), SlangOptions(top=("top",)))
    names = [Path(name).name for name, _ in texts]
    assert names[0] == "props.sv"
    # unused.sv is never needed.
    assert sorted(names[1:]) == ["leaf.v", "mid.sv", "pkg.sv", "top.sv"]
    assert dict(texts)[str(props)] == props.read_text()


def test_load_design_extensions_and_files(tmp_path: Path) -> None:
    rtl = _library(tmp_path)
    props = tmp_path / "props.sv"
    props.write_text("module chk; leaf u(); endmodule\n")
    extra = tmp_path / "extra.sv"
    extra.write_text("module extra; endmodule\n")
    (rtl / "leaf.v").unlink()
    (rtl / "leaf.vh").write_text("module leaf; endmodule\n")
    search = LibrarySearch(directories=(rtl,), extensions=(".vh",), files=(extra,))
    names = [Path(name).name for name, _ in load_design([props], search, SlangOptions())]
    assert names[0] == "props.sv"
    assert sorted(names[1:]) == ["extra.sv", "leaf.vh"]


@pytest.mark.parametrize("top", [(), ("leaf",)])
def test_load_design_rejects_ambiguous_names(tmp_path: Path, top: tuple[str, ...]) -> None:
    """Found by an instantiation (the loader) or as a top (the seeded lookup)."""
    rtl = _library(tmp_path)
    (rtl / "leaf.sv").write_text("module leaf; endmodule\n")
    props = tmp_path / "props.sv"
    props.write_text("module chk; endmodule\n" if top else "module chk; leaf u(); endmodule\n")
    with pytest.raises(SlangError, match="'leaf' is ambiguous"):
        load_design([props], LibrarySearch(directories=(rtl,)), SlangOptions(top=top))


def test_load_design_reports_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(SlangError):
        load_design([tmp_path / "missing.sv"], LibrarySearch(), SlangOptions())
