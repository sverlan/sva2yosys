"""The EBMC regression suite, an external opt-in known-answer corpus (CONTRACT.md)."""

from __future__ import annotations

import hashlib
import inspect
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from support.ebmc_corpus import (
    FAIL,
    PASS,
    REACHED,
    UNREACHED,
    CaseResult,
    Expected,
    Kind,
    PropertyResult,
    RunMode,
    Status,
    Tools,
    parse_desc,
    run_case,
)
from support.toolchain import Tool, run_yosys

EXPECTATIONS = Path(__file__).resolve().parent / "fixtures" / "expectations.txt"

CLASSES = ("agrees", "agrees-bounded", "excluded", "rejected", "skipped")
HEADERS = (
    "ebmc-version",
    "corpus-commit",
    "corpus-sha256",
    "oss-cad-suite",
    "yosys-version",
    "sby-version",
)
REPRESENTATIVES = (
    "cycle_delay5",  # bounded answers, bmc
    "sequence_implication4",  # REFUTED, bmc
    "nexttime1.bdd",  # unbounded PROVED, prove (adapted clock)
    "cover2",  # a cover reached and a cover not reached
    "pending_intersect1",  # the excluded case (trace case intersect_of_goto_2_and_goto_3...)
    "sva_implies1",  # rejected
    "sequence_not1",  # skipped as a whole
)
"""The cheap cases of the default tier (CONTRACT.md 12): no liveness part, a few seconds."""


@dataclass(frozen=True)
class Line:
    desc: str  # the case, the .desc file's stem
    label: str  # the property ("main.p0", "[.*]"), or "-" for a case skipped as a whole
    cls: str  # one of CLASSES
    detail: str  # the reason (agrees-bounded, excluded, skipped) or the message fragment
    note: str


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
        text, _, note = raw.partition(" # ")
        desc, label, cls, *rest = text.split(maxsplit=3)
        lines.append(Line(desc, label, cls, rest[0].strip() if rest else "", note.strip()))
    return headers, lines


HEADER, LINES = _read()


def _by_desc() -> dict[str, list[Line]]:
    grouped: dict[str, list[Line]] = {}
    for line in LINES:
        grouped.setdefault(line.desc, []).append(line)
    return grouped


BY_DESC = _by_desc()


def _with(cls: str) -> list[str]:
    return [name for name, lines in BY_DESC.items() if any(line.cls == cls for line in lines)]


# --- the digest (CONTRACT.md 1) -------------------------------------------------------------


def corpus_digest(corpus: Path) -> str:
    """SHA-256 of every ``.desc`` and ``.sv`` file of the corpus directory (name and content,
    line ends normalised to LF), in name order."""
    digest = hashlib.sha256()
    # Sorted by the name string: sorting Paths is case-insensitive on Windows only.
    for path in sorted(
        (p for p in corpus.iterdir() if p.suffix in (".desc", ".sv")), key=lambda p: p.name
    ):
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(path.name.encode() + b"\0" + content + b"\0")
    return digest.hexdigest()


_DIGEST_MESSAGE = (
    "the corpus content changed since fixtures/expectations.txt was written: "
    "fetch the pinned commit (tests/support/corpora.py), or classify every case again and "
    "update its corpus-sha256 line"
)


def require_classified_corpus(corpus: Path, recorded: str) -> None:
    """Fail (never skip) unless the digest of ``corpus`` is ``recorded``."""
    if corpus_digest(corpus) != recorded:
        pytest.fail(_DIGEST_MESSAGE, pytrace=False)


@pytest.fixture(scope="session")
def classified_corpus(ebmc_tests: Path) -> Path:
    """The corpus, once its digest is checked (CONTRACT.md 1): on another revision every test
    using this fixture fails with the one message."""
    require_classified_corpus(ebmc_tests, HEADER["corpus-sha256"])
    return ebmc_tests


def test_the_corpus_is_the_classified_one(ebmc_tests: Path) -> None:
    """1. The representative: the digest check itself, not through the fixture."""
    assert corpus_digest(ebmc_tests) == HEADER["corpus-sha256"], _DIGEST_MESSAGE


def test_another_revision_fails_instead_of_skipping_and_every_corpus_test_depends_on_it(
    tmp_path: Path,
) -> None:
    """1. Needs no corpus: a directory with other content fails the guard; the other tests
    reach the corpus only through the guarded fixture."""
    (tmp_path / "a.sv").write_text("module a; endmodule")
    recorded = corpus_digest(tmp_path)
    require_classified_corpus(tmp_path, recorded)
    with pytest.raises(pytest.fail.Exception, match="fetch the pinned commit"):
        require_classified_corpus(tmp_path, "0" * 64)
    unguarded = [
        name
        for name, function in globals().items()
        if name.startswith("test_")
        and name != "test_the_corpus_is_the_classified_one"
        and callable(function)
        and "ebmc_tests" in inspect.signature(function).parameters
    ]
    assert unguarded == []


def test_every_corpus_case_is_listed(classified_corpus: Path) -> None:
    """1. Every .desc is listed, none is missing from the corpus, and a case that is not skipped
    as a whole has one line per expected property line, with its label, in order."""
    corpus = {path.stem for path in classified_corpus.glob("*.desc")}
    assert corpus - set(BY_DESC) == set(), "corpus cases missing from fixtures/expectations.txt"
    assert set(BY_DESC) - corpus == set(), "listed cases that the corpus lacks"
    for name, lines in BY_DESC.items():
        if len(lines) == 1 and lines[0].label == "-":
            continue
        desc = parse_desc((classified_corpus / f"{name}.desc").read_text(encoding="utf-8"), name)
        labels = [v.qualified or "[.*]" for v in desc.verdicts]
        assert [line.label for line in lines] == labels, name


def test_the_table_is_well_formed() -> None:
    """6, 7-11. Needs no corpus: every header is present, every class is one of the five (there
    is no `disagrees`), a rejected line has its fragment, and every other non-agreeing class
    has its reason."""
    assert sorted(HEADER) == sorted(HEADERS)
    assert re.fullmatch(r"[0-9a-f]{64}", HEADER["corpus-sha256"])
    assert HEADER["corpus-commit"] == "2a3263c66d042364769353ea0b068ea1f5eec54e"
    for line in LINES:
        assert line.cls in CLASSES, line
        if line.cls != "agrees":
            assert line.detail, line
        if line.label == "-":
            assert line.cls == "skipped", line
    assert {line.desc for line in LINES if line.cls == "excluded"} == {"pending_intersect1"}
    assert {line.desc for line in LINES if line.cls == "agrees-bounded"} == {
        "cover1.bdd",
        "cover3.bdd.buechi",
        "cover4",
        "cover4.bdd",
        "cover4.buechi",
        "cover4.k-induction",
        "sequence2.bmc",
        "sequence3.bmc",
    }
    assert set(REPRESENTATIVES) <= set(BY_DESC)


def test_the_tools_are_the_classified_ones(yosys_tool: Tool, tmp_path: Path) -> None:
    """2. The configured Yosys is the version the table was classified with."""
    log = run_yosys(yosys_tool, ["help help"], tmp_path)
    found = re.findall(r"Yosys (\S+) \(git", log)
    assert found, log[-500:]
    assert found[-1] == HEADER["yosys-version"], (
        f"Yosys {found[-1]} is not the classified {HEADER['yosys-version']}: "
        "classify the corpus again with this version and update fixtures/expectations.txt"
    )


# --- running the cases (CONTRACT.md 3-6) -------------------------------------------------------

_RESULTS: dict[str, CaseResult] = {}
"""The results of the session, by case: a case runs SBY once, whatever tests look at it."""


@dataclass
class Runner:
    corpus: Path
    work: Path
    tools: Tools
    results: dict[str, CaseResult] = field(default_factory=lambda: _RESULTS)

    def result(self, name: str) -> CaseResult:
        if name not in self.results:
            path = self.corpus / f"{name}.desc"
            desc = parse_desc(path.read_text(encoding="utf-8"), name)
            self.results[name] = run_case(desc, self.corpus, self.work / name, self.tools)
        return self.results[name]


@pytest.fixture(scope="session")
def fast_runner(
    classified_corpus: Path, sby_tool: Tool, tmp_path_factory: pytest.TempPathFactory
) -> Runner:
    """Runs cases with ``sby`` alone: for the cases of the default tier, which have no
    liveness part."""
    return Runner(classified_corpus, tmp_path_factory.mktemp("ebmc"), Tools(sby_tool))


@pytest.fixture(scope="session")
def full_runner(
    classified_corpus: Path,
    sby_tool: Tool,
    sby_live_tool: Tool,
    tmp_path_factory: pytest.TempPathFactory,
) -> Runner:
    """Runs cases with ``sby`` and the liveness ``sby``."""
    return Runner(
        classified_corpus, tmp_path_factory.mktemp("ebmc"), Tools(sby_tool, sby_live_tool)
    )


def _ebmc_verdict(expected: Expected) -> str:
    """What EBMC's answer means for our outcome (CONTRACT.md 5, 7)."""
    if expected.kind is Kind.COVER:
        return REACHED if expected.status is Status.PROVED else UNREACHED
    return FAIL if expected.status is Status.REFUTED else PASS


def _check(line: Line, result: CaseResult, index: int) -> None:
    """The check of one property line against the case's result (the class's point)."""
    where = f"{line.desc} {line.label}"
    if line.cls == "skipped" and line.label == "-":
        assert result.skipped is not None and result.skipped == line.detail, where
        return
    assert result.skipped is None, f"{where}: skipped as a whole: {result.skipped}"
    found: PropertyResult = result.results[index]
    assert (found.expected.qualified or "[.*]") == line.label, where
    detail = f"{where}: ours {found.outcome.result} ({found.outcome.detail}), class {found.cls}"
    match line.cls:
        case "agrees":  # 7
            assert found.cls == "agrees", detail
            assert found.outcome.result == _ebmc_verdict(found.expected), detail
        case "agrees-bounded":  # 8
            assert found.cls == "bound-gap", detail
            if found.expected.kind is Kind.COVER:  # a cover "never reached" without a bound
                assert found.expected.status is Status.REFUTED, detail
                assert found.outcome.result == UNREACHED, detail
            else:
                assert found.expected.status is Status.PROVED_UP_TO_BOUND, detail
                assert set(found.outcome.failed_in) == {RunMode.LIVE}, detail
        case "excluded":  # 9
            assert found.cls == "disagrees", detail
            assert found.outcome.result == FAIL, detail
        case "rejected":  # 10
            assert found.outcome.result == "rejected", detail
            assert line.detail in found.outcome.detail, detail
        case "skipped":  # 11
            assert found.outcome.result in ("skipped", "no-directive"), detail
            assert line.detail in found.cls, detail
        case _:
            raise AssertionError(f"unknown class {line.cls!r}")


def _check_case(runner: Runner, name: str, classes: tuple[str, ...] = CLASSES) -> None:
    result = runner.result(name)
    for index, line in enumerate(BY_DESC[name]):
        if line.cls in classes:
            _check(line, result, index)


def test_representative_cases_have_their_recorded_classes(
    fast_runner: Runner,
) -> None:
    """3-12. The cheap representatives, one SBY run per case: bounded and refuted, unbounded
    proved with the adapted clock, a cover reached and one not reached, the excluded case, a
    rejected case and a case skipped as a whole."""
    for name in REPRESENTATIVES:
        _check_case(fast_runner, name)


def test_the_representatives_cover_every_run_mode_and_class() -> None:
    """12. Guards the choice of the representatives (no corpus needed)."""
    classes = {line.cls for name in REPRESENTATIVES for line in BY_DESC[name]}
    assert classes == {"agrees", "excluded", "rejected", "skipped"}


@pytest.mark.slow
@pytest.mark.parametrize("name", _with("agrees"), ids=str)
def test_agreeing_properties_have_the_ebmc_verdict(full_runner: Runner, name: str) -> None:
    """5, 7."""
    _check_case(full_runner, name, ("agrees",))


@pytest.mark.slow
@pytest.mark.parametrize("name", _with("agrees-bounded"), ids=str)
def test_bounded_gap_properties_agree_on_the_bound_and_fail_in_liveness(
    full_runner: Runner, name: str
) -> None:
    """8."""
    _check_case(full_runner, name, ("agrees-bounded",))


@pytest.mark.slow
@pytest.mark.parametrize("name", _with("excluded"), ids=str)
def test_excluded_properties_have_the_verdict_the_reason_states(
    full_runner: Runner, name: str
) -> None:
    """9."""
    _check_case(full_runner, name, ("excluded",))


@pytest.mark.slow
@pytest.mark.parametrize("name", _with("rejected"), ids=str)
def test_rejected_properties_are_rejected_with_the_fragment(full_runner: Runner, name: str) -> None:
    """6, 10."""
    _check_case(full_runner, name, ("rejected",))


@pytest.mark.slow
@pytest.mark.parametrize("name", _with("skipped"), ids=str)
def test_skipped_properties_are_skipped_for_the_reason(full_runner: Runner, name: str) -> None:
    """3, 11."""
    _check_case(full_runner, name, ("skipped",))
