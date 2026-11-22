"""Contract of support.package_check (the check of the built files in CI)."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from support.package_check import main, package_problems

ROOT = "sva2yosys-0.1.0"
SDIST = ("LICENSE", "README.md", "pyproject.toml", "src/sva2yosys/__init__.py")
NEWER_UV = (*SDIST, "pyproject.toml.orig")
WHEEL = (
    "sva2yosys/__init__.py",
    "sva2yosys-0.1.0.dist-info/entry_points.txt",
    "sva2yosys-0.1.0.dist-info/METADATA",
)


def _build(folder: Path, sdist: tuple[str, ...] = SDIST, wheel: tuple[str, ...] = WHEEL) -> Path:
    with tarfile.open(folder / f"{ROOT}.tar.gz", "w:gz") as archive:
        for name in sdist:
            data = b"x"
            member = tarfile.TarInfo(f"{ROOT}/{name}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    with zipfile.ZipFile(folder / f"{ROOT}-py3-none-any.whl", "w") as built:
        for name in wheel:
            built.writestr(name, "x")
    return folder


def test_a_complete_build_has_no_problem(tmp_path: Path) -> None:
    assert package_problems(_build(tmp_path)) == []
    assert main([str(tmp_path)]) == 0


@pytest.mark.parametrize(
    "private",
    ["notes/plan.md", "docs/x.md", "tests/t.py", ".env", "src/other/x.py"],
)
def test_a_file_outside_the_package_is_refused(tmp_path: Path, private: str) -> None:
    problems = package_problems(_build(tmp_path, sdist=(*SDIST, private)))
    assert any("source archive" in p and private in p for p in problems)
    problems = package_problems(_build(tmp_path, wheel=(*WHEEL, private)))
    assert any("wheel" in p and private in p for p in problems)


def test_the_original_pyproject_a_newer_uv_keeps_is_accepted(tmp_path: Path) -> None:
    assert package_problems(_build(tmp_path, sdist=NEWER_UV)) == []


def test_a_missing_required_file_is_refused(tmp_path: Path) -> None:
    problems = package_problems(_build(tmp_path, sdist=("README.md", "pyproject.toml")))
    assert problems == ["the source archive has no LICENSE"]


def test_a_wheel_without_the_package_or_entry_points_is_refused(tmp_path: Path) -> None:
    problems = package_problems(_build(tmp_path, wheel=("sva2yosys-0.1.0.dist-info/METADATA",)))
    assert "the wheel has no sva2yosys/__init__.py" in problems
    assert "the wheel has no entry points" in problems


def test_a_folder_without_exactly_one_of_each_file_is_refused(tmp_path: Path) -> None:
    assert len(package_problems(tmp_path)) == 2
    folder = _build(tmp_path)
    (folder / "sva2yosys-0.1.1.tar.gz").write_bytes(b"")
    assert package_problems(folder) == [
        "expected one source archive in " + str(folder) + ", found 2"
    ]
    assert main([str(folder)]) == 1
