"""Executable contracts for the public SVA reference examples."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from support.documentation_examples import Example, load_catalog, run_example, synchronize, verify
from support.toolchain import SbyResult, SbyStatus, Tool, path_for_tool, run_sby
from sva2yosys.cli import main
from sva2yosys.rewrite import MONITORS_FILE

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CATALOG = FIXTURES / "examples.json"
REFERENCE = ROOT / "docs" / "sva-examples.md"
MATRIX_TOPICS = frozenset(
    {
        "directives",
        "clocking",
        "disable",
        "procedural",
        "initial",
        "types",
        "expressions",
        "sampled-values",
        "functions",
        "inside",
        "delays",
        "repetition",
        "sequence-composition",
        "property-composition",
        "cover",
        "bounded-properties",
        "unbounded-antecedents",
        "always",
        "unbounded-sequences",
        "until",
        "strength",
        "liveness",
        "fairness",
        "parameters",
        "generate",
        "binding",
        "declarations",
        "x-checks",
    }
)
_EXPECTATION_MARKER = re.compile(r"^<!-- sva-expectation: ([a-z0-9-]+) -->$", re.MULTILINE)


def _catalog() -> tuple[Example, ...]:
    return load_catalog(CATALOG)


def test_reference_catalog_loads_and_has_topics() -> None:
    examples = _catalog()
    covered_topics = {topic for example in examples for topic in example.topics}

    assert examples
    assert all(example.topics for example in examples)
    # Stable catalog topic IDs mirror the support matrix's construct families; this
    # checks coverage without depending on the reference document's heading layout.
    assert covered_topics >= MATRIX_TOPICS


def test_reference_document_is_synchronized_with_canonical_sources() -> None:
    examples = _catalog()
    document = REFERENCE.read_text(encoding="utf-8")

    assert synchronize(document, examples, ROOT) == document


def test_reference_expectations_match_catalog_within_each_example_block() -> None:
    examples = _catalog()
    document = REFERENCE.read_text(encoding="utf-8")
    markers = list(_EXPECTATION_MARKER.finditer(document))
    examples_by_key = {example.key: example for example in examples}
    marker_keys = [match.group(1) for match in markers]

    assert len(marker_keys) == len(set(marker_keys))
    assert set(marker_keys) == set(examples_by_key)
    for index, key in enumerate(marker_keys):
        example = examples_by_key[key]
        start = markers[index].end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(document)
        block = document[start:end].removeprefix("\n")
        if example.counts is None:
            expected = (
                f"Check exit: {example.check_exit}. Generation exit: {example.generation_exit}. "
                "Directives: frontend error; no summary."
            )
        else:
            expected = (
                f"Check exit: {example.check_exit}. Generation exit: {example.generation_exit}. "
                f"Directives: {example.counts.accepted} accepted, {example.counts.skipped} "
                f"skipped, {example.counts.rejected} rejected."
            )
        lines = block.splitlines()
        assert lines[0] == expected, example.key
        assert [line for line in lines if line.startswith("Diagnostic fragment: ")] == [
            f"Diagnostic fragment: `{message}`." for message in example.check_messages
        ], example.key
        assert [line for line in lines if line.startswith("Warning fragment: ")] == [
            f"Warning fragment: `{warning}`." for warning in example.warnings
        ], example.key


@pytest.mark.parametrize("example", _catalog(), ids=lambda example: example.key)
def test_documented_example_matches_both_cli_modes(example: Example, tmp_path: Path) -> None:
    execution = run_example(example, ROOT, tmp_path)
    mismatches = verify(example, execution)
    assert mismatches == (), "\n".join(mismatches)

    if example.counts is None:
        # Slang/frontend failures have no directive summary. Their host-specific full
        # diagnostics are intentionally not catalogued, but their source and location are.
        report = execution.check.stdout + execution.check.stderr
        source_name = Path(example.source).name
        assert source_name in report
        assert re.search(r"\b\d+:\d+\b", report), report
    elif example.counts.rejected:
        source_name = Path(example.source).name
        for mode, invocation in (
            ("check", execution.check),
            ("generation", execution.generation),
        ):
            report = invocation.stdout + invocation.stderr
            assert source_name in report, f"{mode}: {report}"
            assert re.search(rf"{re.escape(source_name)}:\d+:\d+", report), f"{mode}: {report}"

    if example.counts is not None and example.counts.accepted and example.generation_exit == 0:
        # A successful generation must leave a monitor and rewritten source in the
        # caller-owned temporary directory, not merely return a successful status.
        generated = tuple(tmp_path.rglob(MONITORS_FILE))
        assert generated
        assert all(path.is_relative_to(tmp_path) for path in generated)


def _generate(example: Example, capsys: pytest.CaptureFixture[str], build: Path) -> None:
    source = ROOT / example.source
    libraries = [
        argument for directory in example.libraries for argument in ("-y", str(ROOT / directory))
    ]
    status = main(
        [str(source), *example.options, *libraries, "--top", example.top, "-o", str(build)]
    )
    captured = capsys.readouterr()
    assert status == 0, captured.out + captured.err
    assert (build / "sva2yosys_read.ys").is_file()


def _translate_read_paths(tool: Tool, build: Path) -> None:
    """Adapt generated read scripts for the configured SBY host, including WSL."""
    drive_path = re.compile(r"(?<!\S)([A-Za-z]:[\\/][^\s]+)")
    include_path = re.compile(r"(?<=-I)([A-Za-z]:[\\/][^\s]+)")
    for filename in ("sva2yosys_read.ys", "sva2yosys_read_live.ys"):
        script = build / filename
        if not script.is_file():
            continue
        text = script.read_text(encoding="utf-8")
        text = drive_path.sub(lambda match: path_for_tool(tool, match.group(1)), text)
        text = include_path.sub(lambda match: path_for_tool(tool, match.group(1)), text)
        script.write_text(text, encoding="utf-8")


def _control(
    tool: Tool,
    example: Example,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    task: str,
) -> SbyResult:
    build = tmp_path / "build"
    _generate(example, capsys, build)
    _translate_read_paths(tool, build)
    sby = tmp_path / f"{example.key}.sby"
    shutil.copyfile(FIXTURES / f"{example.key}.sby", sby)
    return run_sby(tool, sby, task, tmp_path / f"work_{task}")


def _by_key(key: str) -> Example:
    return next(example for example in _catalog() if example.key == key)


def test_emitted_assertion_proves_and_cover_is_reachable(
    sby_tool: Tool, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    example = _by_key("proof-cover")
    proved = _control(sby_tool, example, capsys, tmp_path / "prove", "prove")
    assert proved.status is SbyStatus.PASS, proved.log[-3000:]

    covered = _control(sby_tool, example, capsys, tmp_path / "cover", "cover")
    assert covered.status is SbyStatus.PASS, covered.log[-3000:]
    assert [hit.step for hit in covered.reached_covers] == [1]


def test_emitted_false_assertion_fails_at_initial_sample(
    sby_tool: Tool, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    result = _control(sby_tool, _by_key("assertion-failure"), capsys, tmp_path, "fail")
    assert result.status is SbyStatus.FAIL, result.log[-3000:]
    assert [hit.step for hit in result.failed_assertions] == [0]


@pytest.mark.parametrize(
    ("key", "live_status"),
    [("strong-pass", SbyStatus.PASS), ("strong-fail", SbyStatus.FAIL)],
)
def test_emitted_strong_assertion_has_safety_and_liveness_results(
    sby_tool: Tool,
    sby_live_tool: Tool,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    key: str,
    live_status: SbyStatus,
) -> None:
    example = _by_key(key)
    safety = _control(sby_tool, example, capsys, tmp_path / "prove", "prove")
    assert safety.status is SbyStatus.PASS, safety.log[-3000:]

    live = _control(sby_live_tool, example, capsys, tmp_path / "live", "live")
    assert live.status is live_status, live.log[-3000:]
