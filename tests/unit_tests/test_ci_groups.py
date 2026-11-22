"""The groups the CI pipelines run in parallel (``support.ci_groups``, contract in its module)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from support import ci_groups

ROOT = Path(__file__).resolve().parents[2]


def _paths(group: str) -> list[str]:
    return [arg for arg in ci_groups.pytest_args(group) if not arg.startswith("--")]


def _ignores(group: str) -> list[str]:
    return [
        arg.removeprefix("--ignore=")
        for arg in ci_groups.pytest_args(group)
        if arg.startswith("--ignore=")
    ]


def test_the_group_names_are_unique_and_end_with_the_catch_all() -> None:
    names = ci_groups.names()
    assert len(names) == len(set(names))
    assert names[-1] == "rest"


@pytest.mark.parametrize("group", ci_groups.names())
def test_every_path_and_ignore_of_a_group_exists(group: str) -> None:
    for path in [*_paths(group), *_ignores(group)]:
        assert (ROOT / path).exists(), path


def test_the_catch_all_ignores_exactly_the_directories_the_other_groups_take() -> None:
    expected = {
        "tests/unit_tests",
        "tests/functional_tests/temporal_machine",
        "tests/functional_tests/generated_monitors",
        "tests/functional_tests/external",
    }
    assert set(_ignores("rest")) == expected
    others = [path for group in ci_groups.names()[:-1] for path in _paths(group)]
    for directory in expected:  # each one is taken by some other group
        assert any(path == directory or path.startswith(directory + "/") for path in others)


def test_the_long_areas_are_cut_into_parts_that_fit_the_time_limit() -> None:
    temporal_files = {
        path
        for group in ci_groups.names()
        if group.startswith("temporal")
        for path in _paths(group)
        if path.endswith(".py")
    }
    assert len(temporal_files) == 4  # never_fails, configuration, until, unbounded
    assert set(_ignores("temporal-rest")) == temporal_files
    assert _paths("generated-monitors-main") == [
        "tests/functional_tests/generated_monitors/test_generated_monitors.py"
    ]
    assert _ignores("generated-monitors-rest") == _paths("generated-monitors-main")


def test_an_unknown_group_is_a_key_error() -> None:
    with pytest.raises(KeyError):
        ci_groups.pytest_args("nope")


def test_the_command_line_prints_the_names_and_the_arguments() -> None:
    script = ROOT / "tests" / "support" / "ci_groups.py"
    names = subprocess.run(
        [sys.executable, str(script), "--names"], capture_output=True, text=True, check=True
    )
    assert json.loads(names.stdout) == ci_groups.names()
    args = subprocess.run(
        [sys.executable, str(script), "generated-monitors-rest"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert args.stdout.split() == list(ci_groups.pytest_args("generated-monitors-rest"))
    bad = subprocess.run([sys.executable, str(script), "nope"], capture_output=True, text=True)
    assert bad.returncode == 2


@pytest.mark.extended
def test_the_groups_collect_every_test_exactly_once() -> None:
    def collect(*args: str) -> list[str]:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
                "--external-tools=none",
                "--external-corpora=none",
                *args,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return [line for line in completed.stdout.splitlines() if "::" in line]

    whole = collect("tests")
    seen: list[str] = []
    for group in ci_groups.names():
        seen.extend(collect(*ci_groups.pytest_args(group)))
    assert len(seen) == len(set(seen))
    assert sorted(seen) == sorted(whole)
