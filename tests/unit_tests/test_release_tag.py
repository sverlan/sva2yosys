"""Contract of support.release_tag (the check before a release is published)."""

from __future__ import annotations

from pathlib import Path

import pytest

from support.release_tag import main, release_problems

NL = chr(10)
PYPROJECT = NL.join(["[project]", 'name = "x"', 'version = "0.1.0"', ""])
CHANGELOG = NL.join(
    [
        "# Changelog",
        "",
        "## [Unreleased]",
        "",
        "## [0.1.0]",
        "",
        "[Unreleased]: https://github.com/sverlan/sva2yosys/compare/v0.1.0...HEAD",
        "[0.1.0]: https://github.com/sverlan/sva2yosys/releases/tag/v0.1.0",
        "",
    ]
)


def test_a_consistent_release_has_no_problem() -> None:
    assert release_problems("v0.1.0", PYPROJECT, CHANGELOG) == []


@pytest.mark.parametrize(
    "tag",
    [
        "0.1.0",
        "v0.1",
        "v01.1.0",
        "v0.1.02",
        "v0.1.0-rc1",
        "v0.1.0-private",
        "v0.1.0-public",
        "",
    ],
)
def test_a_tag_of_another_shape_is_refused(tag: str) -> None:
    problems = release_problems(tag, PYPROJECT, CHANGELOG)
    assert problems and "vX.Y.Z" in problems[0]


def test_a_tag_number_that_differs_from_the_package_version_is_refused() -> None:
    problems = release_problems("v0.2.0", PYPROJECT, CHANGELOG)
    assert any("0.2.0" in p and "0.1.0" in p and "pyproject.toml" in p for p in problems)


def test_a_missing_package_version_is_refused() -> None:
    problems = release_problems("v0.1.0", "[project]" + NL, CHANGELOG)
    assert any("version" in p and "pyproject.toml" in p for p in problems)


@pytest.mark.parametrize(
    "heading",
    [
        "## [0.1.0] - not yet released",
        "## [0.1.0] - 2000-01-01",
        "## 0.1.0",
        "## [0.1.1]",
    ],
)
def test_a_dated_or_missing_changelog_section_is_refused(heading: str) -> None:
    text = CHANGELOG.replace("## [0.1.0]", heading)
    problems = release_problems("v0.1.0", PYPROJECT, text)
    assert any("CHANGELOG.md" in p and "0.1.0" in p for p in problems)


def test_a_changelog_without_the_release_link_is_refused() -> None:
    text = CHANGELOG.replace("releases/tag/v0.1.0", "releases/tag/v0.1.0-private")
    problems = release_problems("v0.1.0", PYPROJECT, text)
    assert any("link" in p and "v0.1.0" in p for p in problems)
    text = NL.join(line for line in CHANGELOG.split(NL) if not line.startswith("[0.1.0]:"))
    assert any("link" in p for p in release_problems("v0.1.0", PYPROJECT, text))


def test_the_command_line_reads_the_files_of_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["v0.1.0"]) == 0
    assert capsys.readouterr().out == ""
    assert main(["v0.3.0"]) == 1
    assert "0.3.0" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main([])
