"""Bounded, pinned SVA flow observations (CONTRACT.md)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from support.documentation_examples import Example, load_catalog, run_example, synchronize, verify
from support.interpretations import require_pin
from support.toolchain import SbyStatus, Tool, run_sby, run_tool
from sva2yosys.slang.export import export_design
from sva2yosys.slang.invoke import SlangOptions

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAGE = ROOT / "docs" / "tool-comparison.md"
MATRIX = ROOT / "docs" / "supported-sva.md"
SUPPORT_END = "## Comparison with other Yosys SVA flows"


def comparison_section() -> str:
    """The comparison matrix at the top of the evidence page, up to its next section."""
    page = PAGE.read_text(encoding="utf-8")
    return page.split("## Comparison matrix", 1)[1].split("\n## ", 1)[0]


def native_pin() -> dict[str, str]:
    """The pins of this platform: the same release of the suite is built differently on Linux."""
    pin = json.loads((FIXTURES / "native.json").read_text(encoding="utf-8"))
    return {**pin, **pin["linux"]} if sys.platform.startswith("linux") else pin


@dataclass(frozen=True)
class NativeCase:
    """One isolated syntax/context observation; acceptance is not a verdict.

    A rejected case must have the declared syntax fragment. An accepted case
    must produce one assertion or live check after hierarchy/proc. Both CLI
    expectations and the canonical source are named by the same example key.
    """

    key: str
    diagnostic: str | None
    flavor: str
    slang_accepts: bool


CASES = (
    NativeCase("boolean-pass", None, "assert", True),
    NativeCase("boolean-fail", None, "assert", True),
    NativeCase("explicit-clock", "unexpected '@'", "assert", True),
    NativeCase("implication", "unexpected '>'", "assert", False),
    NativeCase("delay", "unexpected '#'", "assert", False),
    NativeCase("eventually", None, "live", False),
)


@pytest.fixture(scope="session")
def classified_inputs() -> None:
    """2: require classified sources even when a tool test is selected on its own."""
    digest = hashlib.sha256()
    for path in sorted([*FIXTURES.glob("*.sv"), FIXTURES / "examples.json"]):
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(path.name.encode() + b"\0" + content + b"\0")
    pin = json.loads((FIXTURES / "native.json").read_text(encoding="utf-8"))
    require_pin(digest.hexdigest(), pin["inputs_sha256"], "comparison inputs")


def test_sources_catalog_and_documented_examples_stay_synchronized(classified_inputs: None) -> None:
    """1, 2: source changes require deliberate reclassification and snippet updates."""
    examples = load_catalog(FIXTURES / "examples.json")
    assert {example.key for example in examples} == {case.key for case in CASES}
    assert {Path(example.source).name for example in examples} == {
        source.name for source in FIXTURES.glob("*.sv")
    }
    pin = json.loads((FIXTURES / "native.json").read_text(encoding="utf-8"))
    page = PAGE.read_text(encoding="utf-8")
    assert synchronize(page, examples, ROOT) == page
    for pins in (pin, pin["linux"]):  # both builds of the classified release are documented
        assert pins["yosys_version"] in page
        assert pins["slang_plugin_sha256"] in page
    for case in CASES:
        block = page.split(f"<!-- sva-example: {case.key} -->", 1)[1]
        observation = block.split("<!-- /sva-example -->", 1)[1].strip().splitlines()[0]
        if case.diagnostic is None:
            assert observation.startswith(f"Native: accepted, flavor `{case.flavor}`"), case.key
            if case.key == "boolean-pass":
                assert "SBY `prove`: **PASS**" in observation
            elif case.key == "boolean-fail":
                assert "SBY `prove`: **FAIL**" in observation
        else:
            assert observation == (f"Native: rejected, syntax diagnostic `{case.diagnostic}`."), (
                case.key
            )


def test_comparison_retains_six_columns_and_audits_every_source_only_cell() -> None:
    """4: every inferred cell has a public, pinned evidence entry, never a run claim."""
    section = comparison_section()
    lines = [line for line in section.splitlines() if line.startswith("|")]
    assert lines[0] == (
        "| Construct | sva2yosys | Native Yosys | yosys-slang | Yosys+Verific | EBMC |"
    )
    page = PAGE.read_text(encoding="utf-8")
    assert "not run" in section.lower()
    assert "public documentation and source code" in section
    ids: list[str] = []
    for line in lines[2:]:
        # Escaped implication bars are content, not Markdown column separators.
        cells = re.split(r"(?<!\\)\|", line)[1:-1]
        assert len(cells) == 6, line
        for index, value in enumerate(cells[1:], start=1):
            mark = value.strip().lstrip("[")
            if index == 4:  # inferred, not executed: (✓), (✗), (◐); ❔ stays
                assert mark.startswith(("(✓)", "(✗)", "(◐)", "❔")), value
            else:
                assert mark.startswith(("✓", "✗", "◐", "❔")), value
        cell = cells[4].strip()
        match = re.fullmatch(r"\[[^]]+\]\(#(v\d\d)\)", cell)
        assert match is not None, cell
        key = match.group(1)
        ids.append(key)
        assert f'<a id="{key}"></a>' in page
    assert len(ids) == len(set(ids))
    assert set(ids) == set(re.findall(r'<a id="(v\d\d)"></a>', page))
    references = dict(re.findall(r"^\[([^]]+)\]: (https://github\.com/\S+)$", page, re.MULTILINE))
    for key in ids:
        (entry,) = [line for line in page.splitlines() if f'<a id="{key}"></a>' in line]
        evidence = re.findall(r"\[[^]]+\]\[([^]]+)\]", entry)
        assert evidence, key
        assert all(reference in references for reference in evidence), key
        assert "**D" in entry or "**S" in entry or "**NM" in entry, key
    assert "0ad867ab51111cd5b083b28f934c9a764cca33f3" in page
    assert "79fc9eddca5e33a28cc2f2cd19c9825efd9a7255" in page


def test_support_marks_and_ebmc_cells_separate_capability_from_coverage() -> None:
    """4: frontend marks are explicit; oracle coverage is not an EBMC limitation."""
    support = MATRIX.read_text(encoding="utf-8").split(SUPPORT_END, 1)[0]
    comparison = comparison_section()
    assert "N/A" in support and "❔" in support
    for line in support.split("## Matrix", 1)[1].splitlines():
        if not line.startswith("| ") or line.startswith("| Construct"):
            continue
        cells = re.split(r"(?<!\\)\|", line)[1:-1]
        assert len(cells) == 4, line
        assert cells[2].strip().startswith(("✓", "◐", "❔", "N/A")), line
    for line in comparison.splitlines():
        if not line.startswith("| ") or line.startswith("| Construct"):
            continue
        cells = re.split(r"(?<!\\)\|", line)[1:-1]
        assert not re.search(r"\b(lasso|subsets?|representatives?|measured)\b", cells[5]), line


@pytest.mark.parametrize("example", load_catalog(FIXTURES / "examples.json"), ids=lambda e: e.key)
def test_comparison_source_matches_both_sva2yosys_cli_modes(
    classified_inputs: None, example: Example, tmp_path: Path
) -> None:
    """1: the same input really passes through the documented lowering flow."""
    export_design([ROOT / example.source], SlangOptions(top=(example.top,)))
    execution = run_example(example, ROOT, tmp_path)
    assert verify(example, execution) == ()


@pytest.fixture(scope="session")
def classified_yosys(
    classified_inputs: None, yosys_tool: Tool, tmp_path_factory: pytest.TempPathFactory
) -> Tool:
    """2: do not apply pinned observations to an unclassified Yosys build."""
    pin = native_pin()
    result = run_tool(
        yosys_tool,
        [*yosys_tool.command, "-V"],
        tmp_path_factory.mktemp("comparison-version"),
        30,
        "comparison version",
    )
    assert result.returncode == 0, result.stderr
    require_pin(result.stdout.strip(), pin["yosys_version"], "comparison Yosys")
    return yosys_tool


@pytest.fixture(scope="session")
def classified_slang_plugin(classified_yosys: Tool) -> Path:
    """Pin the existing plugin separately from the host Yosys executable."""
    pin = native_pin()
    installation = Path(classified_yosys.command[0]).resolve().parent.parent
    relative = pin["slang_plugin_relative"]
    assert isinstance(relative, str)
    plugin = installation / relative
    assert plugin.is_file(), "classified plugin is missing; reclassify the configured installation"
    require_pin(
        hashlib.sha256(plugin.read_bytes()).hexdigest(),
        pin["slang_plugin_sha256"],
        "comparison yosys-slang binary",
    )
    return plugin


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
def test_yosys_slang_observation(
    classified_yosys: Tool, classified_slang_plugin: Path, case: NativeCase, tmp_path: Path
) -> None:
    """2: a located unsupported-SVA result or one assertion cell, not a load failure."""
    source = FIXTURES / f"{case.key}.sv"
    shutil.copyfile(source, tmp_path / source.name)
    commands = (
        f'plugin -i "{classified_slang_plugin.as_posix()}"; '
        f"read_slang -D FORMAL --top top {source.name}"
    )
    if case.slang_accepts:
        commands += "; hierarchy -top top; proc; select -assert-count 1 t:$check"
    result = run_tool(
        classified_yosys,
        [*classified_yosys.command, "-p", commands],
        tmp_path,
        30,
        "yosys-slang comparison",
    )
    log = result.stdout + result.stderr
    if case.slang_accepts:
        assert result.returncode == 0, log
    else:
        assert result.returncode == 1, log
        assert re.search(
            rf"{re.escape(source.name)}:\d+: ERROR: encountered unsupported SVA feature", log
        ), log


@pytest.mark.parametrize(
    ("key", "expected"), [("boolean-pass", SbyStatus.PASS), ("boolean-fail", SbyStatus.FAIL)]
)
def test_yosys_slang_boolean_proof_and_counterexample(
    classified_yosys: Tool,
    classified_slang_plugin: Path,
    sby_tool: Tool,
    tmp_path: Path,
    key: str,
    expected: SbyStatus,
) -> None:
    """3: yosys-slang acceptance of a Boolean assertion is backed by opposite formal controls."""
    source = FIXTURES / f"{key}.sv"
    shutil.copyfile(source, tmp_path / source.name)
    config = tmp_path / "slang.sby"
    lines = [
        "[tasks]",
        "prove",
        "",
        "[options]",
        "mode prove",
        "",
        "[engines]",
        "abc pdr",
        "",
        "[script]",
        f'plugin -i "{classified_slang_plugin.as_posix()}"',
        f"read_slang -D FORMAL --top top {source.name}",
        "prep -top top",
        "",
        "[files]",
        source.name,
        "",
    ]
    config.write_text("\n".join(lines), encoding="utf-8")
    result = run_sby(sby_tool, config, "prove", tmp_path / "work")
    assert result.status is expected, result.log[-3000:]


def test_yosys_slang_ignore_control_drops_checks_instead_of_lowering_them(
    classified_yosys: Tool, classified_slang_plugin: Path, tmp_path: Path
) -> None:
    """2: the same RTL can elaborate when all its directives are explicitly ignored."""
    source = FIXTURES / "boolean-pass.sv"
    shutil.copyfile(source, tmp_path / source.name)
    commands = (
        f'plugin -i "{classified_slang_plugin.as_posix()}"; '
        f"read_slang -D FORMAL --ignore-assertions --top top {source.name}; "
        "hierarchy -top top; proc; select -assert-none t:$check"
    )
    result = run_tool(
        classified_yosys,
        [*classified_yosys.command, "-p", commands],
        tmp_path,
        30,
        "yosys-slang ignored-directive control",
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
def test_native_yosys_observation(classified_yosys: Tool, case: NativeCase, tmp_path: Path) -> None:
    """2: a syntax rejection must identify this source and the isolated construct."""
    source = FIXTURES / f"{case.key}.sv"
    shutil.copyfile(source, tmp_path / source.name)
    commands = (
        f"read_verilog -formal -sv {source.name}; hierarchy -top top; proc; "
        f"select -assert-count 1 t:$check r:FLAVOR={case.flavor} %i"
    )
    result = run_tool(
        classified_yosys,
        [*classified_yosys.command, "-p", commands],
        tmp_path,
        30,
        "native comparison",
    )
    log = result.stdout + result.stderr
    if case.diagnostic is None:
        assert result.returncode == 0, log
    else:
        assert result.returncode == 1, log
        assert re.search(rf"{re.escape(source.name)}:\d+: ERROR: syntax error", log), log
        assert case.diagnostic in log, log


@pytest.mark.parametrize(
    ("key", "expected"), [("boolean-pass", SbyStatus.PASS), ("boolean-fail", SbyStatus.FAIL)]
)
def test_native_boolean_proof_and_counterexample(
    classified_yosys: Tool, sby_tool: Tool, tmp_path: Path, key: str, expected: SbyStatus
) -> None:
    """3: native acceptance is backed by opposite, hand-derived formal controls."""
    source = FIXTURES / f"{key}.sv"
    shutil.copyfile(source, tmp_path / source.name)
    config = tmp_path / "native.sby"
    config.write_text(
        "[tasks]\nprove\n\n[options]\nmode prove\n\n[engines]\nabc pdr\n\n[script]\n"
        f"read_verilog -formal -sv {source.name}\nprep -top top\n\n[files]\n{source.name}\n",
        encoding="utf-8",
    )
    result = run_sby(sby_tool, config, "prove", tmp_path / "work")
    assert result.status is expected, result.log[-3000:]
