"""Contract of support.mutants: application of a mutant to a copy, the exactly-once rule,
outcome classification and verdicts. The real campaign is not run here."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from support import mutants
from support.mutants import (
    CANARY,
    MUTANTS,
    InvalidMutantError,
    Mutant,
    Outcome,
    SetupError,
    Status,
    apply,
    check_list,
    clean,
    copy_repository,
    format_table,
    occurrences,
    pytest_command,
    record,
    run,
    run_pytest,
    run_tree,
    summary,
    verdict,
)
from support.mutants import classify_result as classify_exit_result


def _mutant(**changes: object) -> Mutant:
    fields: dict[str, object] = {
        "id": "demo",
        "category": "c",
        "path": "pkg.py",
        "old": "x = 1",
        "new": "x = 2",
        "bug": "b",
        "clause": "k",
        "tests": ("tests/unit_tests/test_ir.py",),
        "equivalent": None,
    }
    fields.update(changes)
    return Mutant(**fields)  # type: ignore[arg-type]


def _fake_source(root: Path, text: str) -> Path:
    package = root / "sva2yosys"
    package.mkdir(parents=True)
    (package / "pkg.py").write_bytes(text.encode("utf-8"))
    return root


def test_apply_replaces_the_single_occurrence_only_in_the_given_tree(tmp_path: Path) -> None:
    copy = _fake_source(tmp_path / "copy", "a = 0\nx = 1\nb = 3\n")
    other = _fake_source(tmp_path / "other", "a = 0\nx = 1\nb = 3\n")
    apply(_mutant(), copy)
    assert (copy / "sva2yosys" / "pkg.py").read_text(encoding="utf-8") == "a = 0\nx = 2\nb = 3\n"
    assert (other / "sva2yosys" / "pkg.py").read_text(encoding="utf-8") == "a = 0\nx = 1\nb = 3\n"


def test_apply_keeps_the_line_endings(tmp_path: Path) -> None:
    root = _fake_source(tmp_path, "a = 0\r\nx = 1\r\n")
    apply(_mutant(), root)
    assert (root / "sva2yosys" / "pkg.py").read_bytes() == b"a = 0\r\nx = 2\r\n"


def test_multi_line_text_matches_a_crlf_source(tmp_path: Path) -> None:
    root = _fake_source(tmp_path, "if a:\r\n    x = 1\r\nb = 3\r\n")
    mutant = _mutant(old="if a:\n    x = 1", new="if a:\n    x = 2")
    assert occurrences(mutant, "if a:\r\n    x = 1\r\n") == 1
    apply(mutant, root)
    assert (root / "sva2yosys" / "pkg.py").read_bytes() == b"if a:\r\n    x = 2\r\nb = 3\r\n"


@pytest.mark.parametrize("text", ["nothing here\n", "x = 1\nx = 1\n"])
def test_old_text_must_occur_exactly_once(tmp_path: Path, text: str) -> None:
    root = _fake_source(tmp_path, text)
    with pytest.raises(InvalidMutantError, match="occurs"):
        apply(_mutant(), root)
    assert (root / "sva2yosys" / "pkg.py").read_text(encoding="utf-8") == text


def test_missing_file_and_identity_replacement_are_invalid(tmp_path: Path) -> None:
    root = _fake_source(tmp_path, "x = 1\n")
    with pytest.raises(InvalidMutantError, match="no file"):
        apply(_mutant(path="absent.py"), root)
    with pytest.raises(InvalidMutantError, match="changes nothing"):
        apply(_mutant(new="x = 1"), root)


def _junit(*cases: tuple[str, str, str]) -> str:
    """A JUnit file with ``(classname, name, child)`` test cases; ``child`` is ``failure``,
    ``error`` or empty (a pass)."""
    body = "".join(
        f'<testcase classname="{c}" name="{n}"><{k} message="m"/></testcase>'
        if k
        else f'<testcase classname="{c}" name="{n}"/>'
        for c, n, k in cases
    )
    return f'<testsuites><testsuite name="pytest">{body}</testsuite></testsuites>'


def test_exit_zero_survives_a_failure_kills_and_every_other_result_is_invalid() -> None:
    assert classify_exit_result(0, None, "10 passed").status is Status.SURVIVED
    killed = classify_exit_result(
        1, _junit(("tests.a", "test_ok", ""), ("tests.a", "test_x", "failure")), ""
    )
    assert (killed.status, killed.detail) == (Status.KILLED, "tests.a::test_x")
    broken = classify_exit_result(2, None, "E   RuntimeError: canary\n")
    assert (broken.status, broken.detail) == (Status.INVALID, "RuntimeError: canary")
    assert classify_exit_result(5, None, "no tests ran").status is Status.INVALID
    assert classify_exit_result(1, None, "").status is Status.INVALID
    assert classify_exit_result(1, "<not xml", "").status is Status.INVALID
    assert classify_exit_result(1, _junit(("tests.a", "test_ok", "")), "").status is Status.INVALID


def test_an_error_makes_the_mutant_invalid_even_beside_real_failures() -> None:
    # origin: review. A broken import in several files is not a kill, even when exit 1.
    junit = _junit(
        ("tests.a", "test_x", "failure"),
        ("tests.b", "tests/b.py", "error"),
        ("tests.c", "tests/c.py", "error"),
    )
    outcome = classify_exit_result(1, junit, "")
    assert outcome.status is Status.INVALID
    assert "error in" in outcome.detail


def test_a_failing_snapshot_test_alone_is_not_a_behavioural_kill() -> None:
    golden = (
        "tests.functional_tests.slang_frontend.test_slang_frontend",
        "test_export_matches_golden[expr.sv-top0]",
        "failure",
    )
    behaviour = ("tests.unit_tests.test_rule", "test_rule", "failure")
    only = classify_exit_result(1, _junit(golden), "")
    assert (only.status, only.detail) == (Status.SNAPSHOT_ONLY, f"{golden[0]}::{golden[1]}")
    both = classify_exit_result(1, _junit(golden, behaviour), "")
    assert (both.status, both.detail) == (Status.KILLED, "tests.unit_tests.test_rule::test_rule")
    assert both.snapshots == (f"{golden[0]}::{golden[1]}",)
    assert verdict(_mutant(equivalent="x"), only) == "contradiction"


def test_the_traceability_page_comparison_is_a_snapshot() -> None:
    # It compares the generated Markdown page with the committed one (re-review).
    page = (
        "tests.functional_tests.reference_semantics.test_traceability_page",
        "test_committed_page_equals_the_generated_one",
        "failure",
    )
    assert classify_exit_result(1, _junit(page), "").status is Status.SNAPSHOT_ONLY


def test_a_frozen_monitor_proof_through_sby_is_behavioural() -> None:
    # The name contains "frozen_monitor" but the test runs an SBY equivalence proof.
    proof = (
        "tests.functional_tests.generated_monitors.test_expression_depth",
        "test_chunked_configuration_matches_the_frozen_monitor_and_detects_a_mutant[equivalent]",
        "failure",
    )
    assert classify_exit_result(1, _junit(proof), "").status is Status.KILLED


def test_every_listed_snapshot_test_exists() -> None:
    for node in mutants.SNAPSHOT_TESTS:
        path, _, function = node.partition("::")
        assert f"def {function}(" in (mutants.REPOSITORY / path).read_text(encoding="utf-8"), node


def test_an_unreadable_junit_file_is_invalid_not_fatal(tmp_path: Path) -> None:
    undecodable = tmp_path / "bad.xml"
    undecodable.write_bytes(b"\xff\xfe\x00<")
    assert mutants.read_junit(undecodable) is None
    assert mutants.read_junit(tmp_path) is None  # a directory
    assert mutants.read_junit(tmp_path / "absent.xml") is None
    assert classify_exit_result(1, mutants.read_junit(undecodable), "").status is Status.INVALID


def test_broken_imports_in_several_files_of_a_real_pytest_run_are_invalid(
    tmp_path: Path,
) -> None:
    # origin: review. Two test files whose import fails, run by the real command.
    copy = tmp_path / "copy"
    (copy / "src").mkdir(parents=True)
    (copy / "tests").mkdir()
    for name in ("test_one.py", "test_two.py"):
        (copy / "tests" / name).write_text("import no_such_module_r9\n", encoding="utf-8")
    (copy / "tests" / "test_three.py").write_text(
        "def test_fails():\n    assert False\n", encoding="utf-8"
    )
    (copy / "tests" / "conftest.py").write_text(_OPTIONS, encoding="utf-8")
    outcome = run_pytest(copy, ["tests"], "none", "none")
    assert outcome.status is Status.INVALID


_OPTIONS = (
    "def pytest_addoption(parser):\n"
    "    parser.addoption('--external-tools', default='none')\n"
    "    parser.addoption('--external-corpora', default='none')\n"
)


def test_the_pytest_command_always_selects_the_external_resources() -> None:
    # origin: review. Without the switches the default `none` skipped every external check.
    command = pytest_command(["tests/x.py"])
    assert "--external-tools=all" in command and "--external-corpora=all" in command
    assert command.index("--external-tools=all") < command.index("tests/x.py")
    narrowed = pytest_command(["t"], "yosys,sby", "none")
    assert "--external-tools=yosys,sby" in narrowed and "--external-corpora=none" in narrowed


def test_the_selection_reaches_the_real_pytest_run(tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    (copy / "src").mkdir(parents=True)
    (copy / "tests").mkdir()
    (copy / "tests" / "conftest.py").write_text(
        _OPTIONS + "def pytest_configure(config):\n"
        "    assert config.getoption('--external-tools') == 'sby,slang'\n"
        "    assert config.getoption('--external-corpora') == 'none'\n",
        encoding="utf-8",
    )
    (copy / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    assert run_pytest(copy, ["tests"], "sby,slang", "none").status is Status.SURVIVED


def test_a_pytest_usage_error_is_a_setup_failure_never_a_mutant_verdict(tmp_path: Path) -> None:
    # An unknown option is a usage error (exit status 4), like an unavailable selected tool.
    copy = tmp_path / "copy"
    (copy / "src").mkdir(parents=True)
    (copy / "tests").mkdir()
    (copy / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    with pytest.raises(SetupError):
        run_pytest(copy, ["tests"])


def test_a_timeout_kills_the_whole_process_tree(tmp_path: Path) -> None:
    marker = tmp_path / "grandchild-alive"
    grandchild = (
        "import time, pathlib\n"
        f"p = pathlib.Path({str(marker)!r})\n"
        "time.sleep(3)\n"
        "p.write_text('alive')\n"
    )
    parent = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}])\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_tree([sys.executable, "-c", parent], tmp_path, dict(os.environ), 1.5)
    assert time.monotonic() - started < 30
    time.sleep(4)
    assert not marker.exists()


def test_verdicts_separate_equivalent_survivors_and_contradictions() -> None:
    plain, argued = _mutant(), _mutant(equivalent="the result is masked")
    survived, killed = Outcome(Status.SURVIVED), Outcome(Status.KILLED, "t")
    assert verdict(plain, survived) == "survived"
    assert verdict(plain, killed) == "killed"
    assert verdict(argued, survived) == "equivalent"
    assert verdict(argued, killed) == "contradiction"
    assert verdict(plain, Outcome(Status.TIMEOUT)) == "timeout"
    assert verdict(argued, Outcome(Status.INVALID, "x")) == "invalid"


def test_summary_counts_the_kill_rate_without_equivalent_mutants() -> None:
    plain, argued = _mutant(), _mutant(id="other", category="d", equivalent="masked")
    records = [
        record(plain, Outcome(Status.KILLED, "t")),
        record(plain, Outcome(Status.KILLED, "t")),
        record(plain, Outcome(Status.SURVIVED)),
        record(argued, Outcome(Status.SURVIVED)),
        record(plain, Outcome(Status.SNAPSHOT_ONLY, "s")),
    ]
    totals = summary(records)
    assert totals["mutants"] == 5
    assert totals["behavioural_kill_rate"] == pytest.approx(2 / 4, abs=1e-4)
    assert totals["verdicts"] == {
        "equivalent": 1,
        "killed": 2,
        "snapshot-only": 1,
        "survived": 1,
    }
    assert not clean(records)
    assert not clean([records[0], records[4]])
    assert clean([records[0], records[3]])
    assert "equivalent" in format_table(records)


def test_copy_repository_leaves_out_caches_and_run_refuses_a_workdir_in_the_repository(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    (root / "src" / "p" / "__pycache__").mkdir(parents=True)
    (root / "src" / "p" / "m.py").write_text("x = 1\n", encoding="utf-8")
    (root / "src" / "p" / "__pycache__" / "m.pyc").write_bytes(b"")
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    copy_repository(tmp_path / "copy", root)
    assert (tmp_path / "copy" / "src" / "p" / "m.py").is_file()
    assert (tmp_path / "copy" / "pyproject.toml").is_file()
    assert not (tmp_path / "copy" / "src" / "p" / "__pycache__").exists()
    with pytest.raises(ValueError, match="outside the repository"):
        run(_mutant(), root / "work", root=root)


def test_run_reports_a_replacement_that_does_not_apply_as_invalid(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _fake_source(root / "src", "y = 1\n")
    outcome = run(_mutant(), tmp_path / "work", root=root)
    assert outcome.status is Status.INVALID
    assert "occurs 0 times" in outcome.detail
    assert not (tmp_path / "work" / "demo").exists()


def test_the_list_is_well_formed() -> None:
    assert check_list(MUTANTS) == []
    assert check_list([_mutant(), _mutant()]) == ["duplicate id demo"]
    assert check_list([_mutant(tests=())]) == ["demo: no tests"]
    assert check_list([_mutant(tests=("tests/absent.py",))]) == [
        "demo: no test path tests/absent.py"
    ]
    assert len(MUTANTS) >= 40
    assert len({m.category for m in MUTANTS}) >= 8


def test_every_mutant_applies_exactly_once_to_the_working_tree() -> None:
    source = mutants.REPOSITORY / "src" / "sva2yosys"
    for mutant in (*MUTANTS, CANARY):
        text = (source / mutant.path).read_bytes().decode("utf-8")
        assert occurrences(mutant, text) == 1, mutant.id
        assert mutant.old != mutant.new, mutant.id
