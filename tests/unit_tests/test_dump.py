"""Contract of support.dump: tree comparison, determinism, confinement to the output directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from support.dump import (
    SECTIONS,
    _Dumper,
    compare_dirs,
    dump_all,
    format_comparison,
    main,
    safe_name,
)


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return root


def test_identical_trees_compare_identical(tmp_path: Path) -> None:
    files = {"a.txt": "x\n", "d/b.txt": "y\n"}
    comparison = compare_dirs(_tree(tmp_path / "A", files), _tree(tmp_path / "B", files))
    assert comparison.identical
    assert (comparison.added, comparison.removed, comparison.changed) == ((), (), ())


def test_changed_file_reports_first_differing_line(tmp_path: Path) -> None:
    a = _tree(tmp_path / "A", {"d/f.txt": "one\ntwo\nthree\n"})
    b = _tree(tmp_path / "B", {"d/f.txt": "one\nTWO\nthree\n"})
    comparison = compare_dirs(a, b)
    assert not comparison.identical
    assert [d.path for d in comparison.changed] == ["d/f.txt"]
    first = comparison.changed[0].first
    assert "2" in first and "two" in first and "TWO" in first


def test_added_and_removed_files(tmp_path: Path) -> None:
    a = _tree(tmp_path / "A", {"keep.txt": "k\n", "gone/x.txt": "x\n"})
    b = _tree(tmp_path / "B", {"keep.txt": "k\n", "new/y.txt": "y\n"})
    comparison = compare_dirs(a, b)
    assert comparison.added == ("new/y.txt",)
    assert comparison.removed == ("gone/x.txt",)
    assert comparison.changed == ()
    assert not comparison.identical


def test_longer_file_differs_at_its_end(tmp_path: Path) -> None:
    a = _tree(tmp_path / "A", {"f.txt": "one\n"})
    b = _tree(tmp_path / "B", {"f.txt": "one\ntwo\n"})
    assert [d.path for d in compare_dirs(a, b).changed] == ["f.txt"]


def test_report_caps_each_kind_and_summarises(tmp_path: Path) -> None:
    a = tmp_path / "A"
    a.mkdir()
    b = _tree(tmp_path / "B", {f"f{i}.txt": "x\n" for i in range(5)})
    text = format_comparison(compare_dirs(a, b), cap=2)
    assert text.count("added") >= 2
    assert "3 more" in text
    assert "5 added" in text


def test_main_compare_exit_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = _tree(tmp_path / "A", {"f.txt": "1\n"})
    same = _tree(tmp_path / "B", {"f.txt": "1\n"})
    other = _tree(tmp_path / "C", {"f.txt": "2\n"})
    assert main(["--compare", str(a), str(same)]) == 0
    assert main(["--compare", str(a), str(other)]) == 1
    assert "f.txt" in capsys.readouterr().out


def test_dump_of_a_subset_is_deterministic_and_confined(tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    counts = dump_all(first, limit=1)
    dump_all(second, limit=1)
    assert compare_dirs(first, second).identical
    assert (first / "COUNTS.txt").is_file()
    assert counts["files"] > 0
    assert not (first / ".work").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["one", "two"]


def test_dump_sections_select_directories(tmp_path: Path) -> None:
    out = tmp_path / "d"
    dump_all(out, sections=("expressions",), limit=1)
    assert sorted(p.name for p in out.iterdir()) == ["COUNTS.txt", "expressions"]
    assert set(SECTIONS) == {"trace", "expressions", "sv", "cli"}


def test_compare_requires_existing_directories(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = _tree(tmp_path / "A", {"f.txt": "1\n"})
    with pytest.raises(NotADirectoryError):
        compare_dirs(real, tmp_path / "missing")
    (tmp_path / "file").write_text("x")
    assert main(["--compare", str(real), str(tmp_path / "missing")]) == 2
    assert main(["--compare", str(tmp_path / "file"), str(real)]) == 2
    assert "error" in capsys.readouterr().err


def test_safe_name_has_no_separators_or_parent_references() -> None:
    for text in ("x/../../escaped", "a\b", "..", ".", "c:d", "plain_name-1.2"):
        name = safe_name(text)
        assert "/" not in name and "\\" not in name and ":" not in name
        assert name not in ("", ".", "..")
    assert safe_name("plain_name-1.2") == "plain_name-1.2"
    assert safe_name("x/../../escaped") != safe_name("x/..%2F..")


def test_writes_cannot_leave_the_output_directory(tmp_path: Path) -> None:
    dumper = _Dumper(tmp_path / "out", None)
    (tmp_path / "out").mkdir()
    dumper.write("ok/file.txt", "x")
    with pytest.raises(ValueError, match="outside"):
        dumper.write("../escaped.txt", "x")
    assert not (tmp_path / "escaped.txt").exists()


def test_distinct_names_never_encode_alike() -> None:
    assert safe_name("x€") != safe_name("x AC")
    assert safe_name("a%20") != safe_name("a ")
    names = ["x y", "x%20y", "x€y", "x/y", r"x\y", "x:y", "X y"]
    assert len({safe_name(n) for n in names}) == len(names)


def test_writing_the_same_path_twice_fails(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    dumper = _Dumper(tmp_path / "out", None)
    dumper.write("trace/a.txt", "1")
    with pytest.raises(ValueError, match="twice"):
        dumper.write("trace/a.txt", "2")
    with pytest.raises(ValueError, match="twice"):
        dumper.write("trace/A.txt", "3")
