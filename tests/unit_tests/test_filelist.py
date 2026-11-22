"""Unit contract of ``sva2yosys.filelist`` (its module docstring)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sva2yosys.filelist import FileList, FileListError, read_filelist


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_every_entry_kind(tmp_path: Path) -> None:
    lst = _write(
        tmp_path / "proj" / "rtl.f",
        "// sources\n"
        "top.sv  sub/a.sv  # trailing comment\n"
        "/* block\n comment */ -v pkgs.sv\n"
        "-y lib +libext+.sv+.v --libext .svh\n"
        "-I inc -Iinc2 +incdir+x+y\n"
        "-D A -DB=2 +define+C=3+D\n",
    )
    base = lst.parent
    assert read_filelist(lst, relative_to_list=True) == FileList(
        files=(base / "top.sv", base / "sub/a.sv", base / "pkgs.sv"),
        library_dirs=(base / "lib",),
        library_extensions=(".sv", ".v", ".svh"),
        include_dirs=(base / "inc", base / "inc2", base / "x", base / "y"),
        defines=(("A", "1"), ("B", "2"), ("C", "3"), ("D", "1")),
    )


def test_minus_f_is_relative_to_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lst = _write(tmp_path / "proj" / "rtl.f", "top.sv\n")
    monkeypatch.chdir(tmp_path)
    assert read_filelist(lst, relative_to_list=False).files == (Path("top.sv"),)


def test_nested_lists_and_absolute_paths(tmp_path: Path) -> None:
    inner = _write(tmp_path / "ip" / "ip.f", "core.sv\n")
    absolute = tmp_path / "abs.sv"
    outer = _write(tmp_path / "proj" / "all.f", f"-F {inner}\n{absolute}\n")
    assert read_filelist(outer, relative_to_list=True).files == (
        inner.parent / "core.sv",
        absolute,
    )


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("+unknown+x\n", "unsupported filelist option +unknown+x"),
        ("--timescale 1ns/1ps\n", "unsupported filelist option --timescale"),
        ("-y\n", "-y needs an argument"),
        ("-F self.f\n", "includes itself"),
        ("-F missing.f\n", "cannot read filelist"),
        ("//server/share/a.sv\n", "ambiguous"),
        ("a.sv /* never closed\n", "unterminated"),
    ],
)
def test_errors(tmp_path: Path, text: str, fragment: str) -> None:
    lst = _write(tmp_path / "self.f", text)
    with pytest.raises(FileListError, match=fragment.replace("+", r"\+")):
        read_filelist(lst, relative_to_list=True)


def test_comment_characters_inside_entries_are_ordinary(tmp_path: Path) -> None:
    lst = _write(
        tmp_path / "rtl.f",
        "rtl/foo#v2.sv rtl/a//b.sv  # a comment\n"
        "// a full-line comment with -unknown options\n"
        "/* a block\n spanning -lines */ c.sv\n",
    )
    base = lst.parent
    assert read_filelist(lst, relative_to_list=True).files == (
        base / "rtl/foo#v2.sv",
        base / "rtl/a//b.sv",
        base / "c.sv",
    )
