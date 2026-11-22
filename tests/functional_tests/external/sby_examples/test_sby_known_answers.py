"""The SymbiYosys examples, an external opt-in known-answer corpus (CONTRACT.md)."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from support.sby_examples import (
    SbyTools,
    TaskResult,
    classify_file,
    file_digest,
    find_sby_files,
    run_task,
    task_names,
)
from support.toolchain import Tool, run_sby, run_yosys

EXPECTATIONS = Path(__file__).resolve().parent / "fixtures" / "expectations.txt"

CLASSES = ("agrees", "agrees-isolated", "rejected", "skipped", "excluded")
HEADERS = ("corpus-commit", "corpus-sha256", "oss-cad-suite", "yosys-version", "sby-version")
REPRESENTATIVES = (
    ("tests/links/symlink.sby", "-"),  # skipped as a whole (a feature of SBY itself)
    ("docs/examples/dft/data_diode.sby", "diode"),  # skipped: proprietary-frontend
    ("docs/examples/fifo/fifo.sby", "noverific"),  # skipped: no concurrent SVA under the macros
    ("docs/examples/demos/up_down_counter.sby", "suprove"),  # rejected: unclocked assertion
    ("docs/examples/fifo/fifo.sby", "cover"),  # rejected: cover mode, no isolation
    ("docs/examples/fifo/fifo_extra_tests.sby", "basic"),  # agrees, covers isolated
    ("docs/examples/fifo/fifo_extra_tests.sby", "nofullskip"),  # agrees on an expected fail
)
"""The cheap lines of the default tier (CONTRACT.md 12)."""


@dataclass(frozen=True)
class Line:
    file: str
    task: str  # the task name, or "-" (no tasks, or the whole file skipped)
    cls: str  # one of CLASSES
    detail: str  # the reason, the fragment or the note


def _read() -> tuple[dict[str, str], list[Line]]:
    headers: dict[str, str] = {}
    lines: list[Line] = []
    for raw in EXPECTATIONS.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        key, _, value = raw.partition(": ")
        if key in HEADERS:
            headers[key] = value.strip()
            continue
        file, task, cls, *rest = raw.split(maxsplit=3)
        lines.append(Line(file, task, cls, rest[0].strip() if rest else ""))
    return headers, lines


HEADER, LINES = _read()
BY_KEY = {(line.file, line.task): line for line in LINES}


def _with(cls: str) -> list[tuple[str, str]]:
    return [(line.file, line.task) for line in LINES if line.cls == cls]


# --- the digest (CONTRACT.md 1) -------------------------------------------------------------

_DIGEST_MESSAGE = (
    "the corpus content changed since fixtures/expectations.txt was written: "
    "fetch the pinned commit (tests/support/corpora.py), or classify every job again and "
    "update its corpus-sha256 line"
)


def require_classified_corpus(corpus: Path, recorded: str) -> None:
    """Fail (never skip) unless the digest of ``corpus`` is ``recorded``."""
    if file_digest(corpus) != recorded:
        pytest.fail(_DIGEST_MESSAGE, pytrace=False)


@pytest.fixture(scope="session")
def classified_corpus(sby_examples: Path) -> Path:
    """The checkout, once its digest is checked (CONTRACT.md 1): on another revision every test
    using this fixture fails with the one message."""
    require_classified_corpus(sby_examples, HEADER["corpus-sha256"])
    return sby_examples


def test_the_corpus_is_the_classified_one(sby_examples: Path) -> None:
    """1. The representative: the digest check itself, not through the fixture."""
    assert file_digest(sby_examples) == HEADER["corpus-sha256"], _DIGEST_MESSAGE


def test_another_revision_fails_instead_of_skipping_and_every_corpus_test_depends_on_it(
    tmp_path: Path,
) -> None:
    """1. Needs no corpus: other content fails the guard; the other tests reach the corpus only
    through the guarded fixture."""
    (tmp_path / "docs" / "examples").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.sby").write_text("[options]\n")
    recorded = file_digest(tmp_path)
    require_classified_corpus(tmp_path, recorded)
    with pytest.raises(pytest.fail.Exception, match="fetch the pinned commit"):
        require_classified_corpus(tmp_path, "0" * 64)
    unguarded = [
        name
        for name, function in globals().items()
        if name.startswith("test_")
        and name != "test_the_corpus_is_the_classified_one"
        and callable(function)
        and "sby_examples" in inspect.signature(function).parameters
    ]
    assert unguarded == []


def test_every_job_is_listed(classified_corpus: Path, sby_tool: Tool) -> None:
    """1. Every .sby file is listed, none is missing from the corpus, and a file that is not
    skipped as a whole has one line per task that sby prints for it."""
    files = find_sby_files(classified_corpus)
    listed = {line.file for line in LINES}
    assert set(files) - listed == set(), "jobs missing from fixtures/expectations.txt"
    assert listed - set(files) == set(), "listed jobs that the corpus lacks"
    for file in files:
        rows = [line for line in LINES if line.file == file]
        listed_tasks = [line.task for line in rows]
        if listed_tasks == ["-"]:
            continue  # skipped as a whole, or a job without tasks
        tasks = task_names(sby_tool, classified_corpus, file)
        assert tasks is not None, file
        assert sorted(listed_tasks) == sorted(tasks), file


def test_the_table_is_well_formed() -> None:
    """3-11. Needs no corpus: every header is present, every class is known, every line but an
    agreeing one has its detail, and the excluded line is the one recorded disagreement."""
    assert sorted(HEADER) == sorted(HEADERS)
    assert re.fullmatch(r"[0-9a-f]{64}", HEADER["corpus-sha256"])
    assert HEADER["corpus-commit"] == "79fc9eddca5e33a28cc2f2cd19c9825efd9a7255"
    assert len(BY_KEY) == len(LINES), "a (file, task) pair is listed twice"
    for line in LINES:
        assert line.cls in CLASSES, line
        if line.cls != "agrees":
            assert line.detail, line
    assert _with("excluded") == [("docs/examples/fifo/fifo.sby", "nofullskip")]
    assert set(REPRESENTATIVES) <= set(BY_KEY)
    assert {BY_KEY[key].cls for key in REPRESENTATIVES} == {
        "skipped",
        "rejected",
        "agrees-isolated",
    }


def test_the_tools_are_the_classified_ones(yosys_tool: Tool, tmp_path: Path) -> None:
    """2. The configured Yosys is the version the table was classified with."""
    log = run_yosys(yosys_tool, ["help help"], tmp_path)
    found = re.findall(r"Yosys (\S+) \(git", log)
    assert found, log[-500:]
    assert found[-1] == HEADER["yosys-version"], (
        f"Yosys {found[-1]} is not the classified {HEADER['yosys-version']}: "
        "classify the corpus again with this version and update fixtures/expectations.txt"
    )


# --- running the tasks (CONTRACT.md 3-7) -------------------------------------------------------

_RESULTS: dict[tuple[str, str], TaskResult] = {}
"""The results of the session, by (file, task): a task runs once whatever tests look at it."""

_REASONS: dict[str, str | None] = {}
"""The reasons a whole job is not compared (``None``: it is), by file."""


@dataclass
class Runner:
    corpus: Path
    work: Path
    tools: SbyTools

    def file_reason(self, file: str) -> str | None:
        if file not in _REASONS:
            _REASONS[file] = classify_file(self.corpus, file, self.tools.sby)
        return _REASONS[file]

    def result(self, file: str, task: str) -> TaskResult:
        key = (file, task)
        if key not in _RESULTS:
            work = self.work / f"{len(_RESULTS)}"
            name = "" if task == "-" else task
            _RESULTS[key] = run_task(self.corpus, file, name, work, self.tools, self.tools.sby)
        return _RESULTS[key]


@pytest.fixture(scope="session")
def runner(
    classified_corpus: Path, sby_tool: Tool, tmp_path_factory: pytest.TempPathFactory
) -> Runner:
    """Runs tasks with ``sby`` alone (a ``live`` task needs the liveness ``sby`` as well, which
    no recorded line uses: they are rejected before the run)."""
    return Runner(classified_corpus, tmp_path_factory.mktemp("sby_examples"), SbyTools(sby_tool))


def _check(runner: Runner, line: Line) -> None:
    """The check of one line against the run (the class's point)."""
    where = f"{line.file} {line.task}"
    if line.task == "-":
        reason = runner.file_reason(line.file)
        if reason is not None:  # skipped as a whole
            assert line.cls == "skipped" and reason == line.detail, f"{where}: {reason}"
            return
    result = runner.result(line.file, line.task)
    detail = f"{where}: ours {result.outcome.kind} {result.outcome.detail!r}"
    match line.cls:
        case "agrees":  # 8
            assert result.cls == "agrees", detail
        case "agrees-isolated":  # 8
            assert result.cls == "agrees-isolated", detail
            assert line.detail in result.outcome.isolated, detail
        case "excluded":  # 9
            assert result.cls == "differs" and result.outcome.detail == "fail", detail
            plain = run_sby(
                runner.tools.sby,
                runner.corpus / line.file,
                line.task,
                runner.work / "plain",
                timeout=300.0,
            )
            assert plain.status.value.lower() == result.outcome.detail, detail
            ours = sorted(result.outcome.failures)
            theirs = sorted((hit.name, hit.step or 0) for hit in plain.failed_assertions)
            assert ours, f"{detail}: no failed assertion reported"
            assert ours == theirs, detail  # the same assertions at the same steps
            assert {name for name, _ in ours} == {"fifo.a_count_diff"}, detail  # not a monitor
        case "rejected":  # 10
            assert result.outcome.kind == "rejected", detail
            assert line.detail in result.outcome.detail, detail
        case "skipped":  # 11
            assert result.outcome.kind == "skipped", detail
            assert result.outcome.detail == line.detail, detail
        case _:
            raise AssertionError(f"unknown class {line.cls!r}")


def test_representative_lines_have_their_recorded_classes(runner: Runner) -> None:
    """3-12. The cheap representatives: a job skipped as a whole, tasks skipped for
    a proprietary-frontend command and for the macros, a rejected task,
    an isolated agreeing task and an agreeing expected failure."""
    for key in REPRESENTATIVES:
        _check(runner, BY_KEY[key])


@pytest.mark.slow
@pytest.mark.parametrize("key", _with("agrees") + _with("agrees-isolated"), ids=str)
def test_agreeing_tasks_end_with_the_expected_status(runner: Runner, key: tuple[str, str]) -> None:
    """7, 8."""
    _check(runner, BY_KEY[key])


@pytest.mark.slow
@pytest.mark.parametrize("key", _with("excluded"), ids=str)
def test_excluded_tasks_end_as_plain_sby_does_on_the_original_job(
    runner: Runner, key: tuple[str, str]
) -> None:
    """9."""
    _check(runner, BY_KEY[key])


@pytest.mark.slow
@pytest.mark.parametrize("key", _with("rejected"), ids=str)
def test_rejected_tasks_are_rejected_with_the_fragment(
    runner: Runner, key: tuple[str, str]
) -> None:
    """6, 10."""
    _check(runner, BY_KEY[key])


@pytest.mark.slow
@pytest.mark.parametrize("key", _with("skipped"), ids=str)
def test_skipped_tasks_are_skipped_for_the_reason(runner: Runner, key: tuple[str, str]) -> None:
    """3, 4, 11."""
    _check(runner, BY_KEY[key])
