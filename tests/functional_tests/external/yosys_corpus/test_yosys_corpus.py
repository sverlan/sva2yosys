"""The Yosys SVA tests, an external opt-in corpus (CONTRACT.md)."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

from support.toolchain import SbyStatus, Tool, run_sby
from sva2yosys.cli import main

EXPECTATIONS = Path(__file__).resolve().parent / "fixtures" / "expectations.txt"


@dataclass(frozen=True)
class Case:
    name: str  # the corpus file's stem
    variant: str  # "pass" or "fail"
    expectation: str  # "pass", "fail", "rejected" or "skipped"
    detail: str  # the rejection fragment, or the reason for skipping


def _cases() -> list[Case]:
    cases: list[Case] = []
    for line in EXPECTATIONS.read_text().splitlines():
        text = line.split("#", 1)[0].strip()
        if not text or text.startswith("corpus-sha256:"):
            continue
        name, variant, expectation, *rest = text.split(maxsplit=3)
        assert variant in ("pass", "fail") and expectation in (
            "pass",
            "fail",
            "rejected",
            "skipped",
        ), line
        cases.append(Case(name, variant, expectation, rest[0].strip() if rest else ""))
    return cases


CASES = _cases()
RUN = [case for case in CASES if case.expectation != "skipped"]
ACCEPTED = [case for case in CASES if case.expectation in ("pass", "fail")]

_SBY = """[tasks]
bmc

[options]
mode bmc
depth 10

[engines]
smtbmc yices

[script]
script sva2yosys_read.ys
prep -top top
chformal -early -assume

[files]
build/sva2yosys_read.ys
"""


def _id(case: Case) -> str:
    return f"{case.name}-{case.variant}"


def _translate(
    capsys: pytest.CaptureFixture[str], corpus: Path, case: Case, build: Path
) -> tuple[int, str]:
    defines = ["-D", "FAIL"] if case.variant == "fail" else []
    status = main([str(corpus / f"{case.name}.sv"), *defines, "-o", str(build)])
    _, err = capsys.readouterr()
    return status, err


def _recorded_digest() -> str:
    for line in EXPECTATIONS.read_text().splitlines():
        if line.startswith("corpus-sha256:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("fixtures/expectations.txt has no corpus-sha256 line")


def corpus_digest(corpus: Path) -> str:
    """SHA-256 of every file of the corpus directory (name and content, line ends
    normalised to LF), in name order."""
    digest = hashlib.sha256()
    # Sorted by the name string: sorting Paths is case-insensitive on Windows only.
    for path in sorted((p for p in corpus.iterdir() if p.is_file()), key=lambda p: p.name):
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(path.name.encode() + b"\0" + content + b"\0")
    return digest.hexdigest()


_DIGEST_MESSAGE = (
    "the corpus content changed since fixtures/expectations.txt was written: "
    "fetch the pinned commit (tests/support/corpora.py), or re-classify every case and "
    "update its corpus-sha256 line"
)


def require_classified_corpus(corpus: Path, recorded: str) -> None:
    """Fail (never skip) unless the digest of ``corpus`` is ``recorded``."""
    if corpus_digest(corpus) != recorded:
        pytest.fail(_DIGEST_MESSAGE, pytrace=False)


@pytest.fixture(scope="session")
def classified_corpus(yosys_sva_tests: Path) -> Path:
    """The corpus, once its digest is checked (CONTRACT.md 1): on another revision every test
    using this fixture fails with the one message, instead of reporting meaningless mismatches."""
    require_classified_corpus(yosys_sva_tests, _recorded_digest())
    return yosys_sva_tests


def test_the_corpus_is_the_classified_one(yosys_sva_tests: Path) -> None:
    """1. The representative: the digest check itself, not through the fixture."""
    assert corpus_digest(yosys_sva_tests) == _recorded_digest(), _DIGEST_MESSAGE


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
        and "yosys_sva_tests" in inspect.signature(function).parameters
    ]
    assert unguarded == []


def test_every_corpus_case_is_listed(classified_corpus: Path) -> None:
    """1."""
    corpus = {(path.stem, v) for path in classified_corpus.glob("*.sv") for v in ("pass", "fail")}
    listed = {(case.name, case.variant) for case in CASES}
    assert len(listed) == len(CASES), "a case is listed twice"
    assert corpus - listed == set(), "corpus cases missing from fixtures/expectations.txt"
    assert listed - corpus == set(), "listed cases that the corpus lacks"


@pytest.mark.parametrize("case", RUN, ids=_id)
def test_acceptance_is_as_listed(
    capsys: pytest.CaptureFixture[str], classified_corpus: Path, tmp_path: Path, case: Case
) -> None:
    """2."""
    status, err = _translate(capsys, classified_corpus, case, tmp_path / "build")
    if case.expectation == "rejected":
        assert status == 1, err
        errors = [line for line in err.splitlines() if ": error: " in line]
        assert errors and case.detail in errors[0], err
    else:
        assert status == 0, err


@pytest.mark.slow
@pytest.mark.parametrize("case", ACCEPTED, ids=_id)
def test_accepted_case_has_the_listed_verdict(
    capsys: pytest.CaptureFixture[str],
    classified_corpus: Path,
    sby_tool: Tool,
    tmp_path: Path,
    case: Case,
) -> None:
    """3."""
    status, err = _translate(capsys, classified_corpus, case, tmp_path / "build")
    assert status == 0, err
    sby_file = tmp_path / "corpus.sby"
    sby_file.write_text(_SBY)
    result = run_sby(sby_tool, sby_file, "bmc", tmp_path / "work")
    expected = SbyStatus.PASS if case.expectation == "pass" else SbyStatus.FAIL
    assert result.status is expected, result.log[-3000:]
