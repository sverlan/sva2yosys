"""Contract tests for executable SVA documentation-example metadata and snippets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from support.documentation_examples import (
    Counts,
    Example,
    Execution,
    Invocation,
    load_catalog,
    main,
    run_example,
    snippet,
    synchronize,
    verify,
)

_SOURCE = "tests/functional_tests/cli/fixtures/accepted.sv"
_DEFAULT_COUNTS = Counts(accepted=2, skipped=1, rejected=0)
_DOCUMENT_BLOCK = """<!-- sva-example: {key} -->
```systemverilog
{code}
```
<!-- /sva-example -->"""


def _entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": "basic-assertion",
        "source": _SOURCE,
        "top": "accepted",
        "topics": ["assert-property", "implication"],
        "counts": {"accepted": 3, "skipped": 0, "rejected": 0},
        "check_exit": 0,
        "generation_exit": 0,
    }
    entry.update(overrides)
    return entry


def _catalog(path: Path, *entries: dict[str, object]) -> Path:
    path.write_text(json.dumps({"examples": list(entries)}), encoding="utf-8")
    return path


def _example(
    *,
    source: str = _SOURCE,
    counts: Counts | None = _DEFAULT_COUNTS,
    check_exit: int = 0,
    generation_exit: int = 0,
    options: tuple[str, ...] = (),
    check_messages: tuple[str, ...] = (),
    generation_messages: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> Example:
    return Example(
        key="sample",
        source=source,
        top="sample_top",
        options=options,
        libraries=(),
        topics=("sequence",),
        counts=counts,
        check_exit=check_exit,
        generation_exit=generation_exit,
        check_messages=check_messages,
        generation_messages=generation_messages,
        warnings=warnings,
    )


def test_catalog_loads_typed_examples_and_defaults_optional_lists(tmp_path: Path) -> None:
    (example,) = load_catalog(_catalog(tmp_path / "examples.json", _entry()))

    assert example.key == "basic-assertion"
    assert example.source == _SOURCE
    assert example.top == "accepted"
    assert example.topics == ("assert-property", "implication")
    assert example.counts == Counts(accepted=3, skipped=0, rejected=0)
    assert example.check_exit == example.generation_exit == 0
    assert example.options == example.libraries == ()
    assert example.check_messages == example.generation_messages == example.warnings == ()


def test_catalog_preserves_explicit_cli_options_libraries_and_expectations(tmp_path: Path) -> None:
    entry = _entry(
        options=["--x-checks", "reject", "--strategy=direct"],
        libraries=["examples/handshake/rtl"],
        check_messages=["accepted"],
        generation_messages=["generated"],
        warnings=["never fails"],
    )

    (example,) = load_catalog(_catalog(tmp_path / "examples.json", entry))

    assert example.options == ("--x-checks", "reject", "--strategy=direct")
    assert example.libraries == ("examples/handshake/rtl",)
    assert example.check_messages == ("accepted",)
    assert example.generation_messages == ("generated",)
    assert example.warnings == ("never fails",)


def test_catalog_rejects_value_option_without_value(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="without its value"):
        load_catalog(_catalog(tmp_path / "examples.json", _entry(options=["--strategy"])))


@pytest.mark.parametrize(
    ("entries", "fragment"),
    [
        ([{}], "required"),
        ([_entry(), _entry()], "duplicate"),
        ([_entry(id="Not a slug")], "slug"),
        ([_entry(source="../outside.sv")], "path"),
        ([_entry(source="C:/outside.sv")], "path"),
        ([_entry(source="C:outside.sv")], "path"),
        ([_entry(source="\\\\server\\share\\outside.sv")], "path"),
        ([_entry(source="\\outside.sv")], "path"),
        ([_entry(source="tests/functional_tests/cli/fixtures/accepted.txt")], ".sv"),
        ([_entry(libraries=["../outside"])], "path"),
        ([_entry(options=["-o", "elsewhere"])], "output"),
        ([_entry(options=["-obuild"])], "output"),
        ([_entry(options=["--output-dir", "elsewhere"])], "output"),
        ([_entry(options=["--output-dir=elsewhere"])], "output"),
        ([_entry(counts={"accepted": True, "skipped": 0, "rejected": 0})], "count"),
        ([_entry(counts={"accepted": -1, "skipped": 0, "rejected": 0})], "count"),
        ([_entry(counts={"accepted": 1, "skipped": 0})], "count"),
        ([_entry(check_exit=True)], "exit"),
        ([_entry(topics=["sequence", 1])], "topics"),
        ([_entry(warnings="unexpected string")], "warnings"),
        ([_entry(counts=None)], "frontend"),
    ],
)
def test_catalog_rejects_invalid_fields(
    tmp_path: Path, entries: list[dict[str, object]], fragment: str
) -> None:
    with pytest.raises(ValueError, match=fragment):
        load_catalog(_catalog(tmp_path / "examples.json", *entries))


def test_catalog_allows_frontend_error_without_summary(tmp_path: Path) -> None:
    frontend_error = _entry(
        counts=None, check_exit=1, generation_exit=1, check_messages=["frontend error"]
    )

    (example,) = load_catalog(_catalog(tmp_path / "examples.json", frontend_error))

    assert example.counts is None
    assert example.check_exit == example.generation_exit == 1


def test_snippet_returns_whole_source_when_no_reference_pair_exists() -> None:
    source = "\nmodule example;\n  assert property (a |-> b);\nendmodule\n"

    assert snippet(source) == source.strip("\n")


def test_snippet_selects_marked_source_and_normalizes_crlf() -> None:
    source = (
        "\r\n// reference-begin\r\n"
        "module example;\r\n  assert property (a |-> b);\r\nendmodule\r\n"
        "// reference-end\r\n"
    )

    assert snippet(source) == "module example;\n  assert property (a |-> b);\nendmodule"


@pytest.mark.parametrize(
    "source",
    [
        "// reference-begin\nmodule example;",
        "module example;\n// reference-end",
        "// reference-end\nmodule example;\n// reference-begin",
        "// reference-begin\nmodule one;\n// reference-end\n"
        "// reference-begin\nmodule two;\n// reference-end",
        "// reference-begin\nmodule example;\n// reference-end\n// reference-end",
    ],
)
def test_snippet_rejects_malformed_reference_pairs(source: str) -> None:
    with pytest.raises(ValueError, match="reference"):
        snippet(source)


def test_synchronize_replaces_only_the_marked_code_body(tmp_path: Path) -> None:
    source = tmp_path / "source.sv"
    source.write_text("module sample;\n  assert property (a |-> b);\nendmodule\n")
    example = _example(source="source.sv")
    document = (
        "Before.\n\n" + _DOCUMENT_BLOCK.format(key=example.key, code="stale code") + "\n\nAfter.\n"
    )

    updated = synchronize(document, (example,), tmp_path)

    assert updated == (
        "Before.\n\n"
        + _DOCUMENT_BLOCK.format(
            key=example.key,
            code="module sample;\n  assert property (a |-> b);\nendmodule",
        )
        + "\n\nAfter.\n"
    )


@pytest.mark.parametrize(
    "document",
    [
        "No example block.\n",
        _DOCUMENT_BLOCK.format(key="other", code="module other;"),
        _DOCUMENT_BLOCK.format(key="sample", code="module one;")
        + "\n"
        + _DOCUMENT_BLOCK.format(key="sample", code="module two;"),
        "<!-- sva-example: sample -->\nmodule sample;\n<!-- /sva-example -->",
        "<!-- sva-example: sample -->\n```systemverilog\nno close\n<!-- /sva-example -->",
        "<!-- sva-example: sample -->\n```systemverilog\n````\n<!-- /sva-example -->",
    ],
)
def test_synchronize_rejects_missing_unknown_duplicate_or_malformed_blocks(
    tmp_path: Path, document: str
) -> None:
    example = _example()

    with pytest.raises(ValueError):
        synchronize(document, (example,), tmp_path)


def test_verify_accepts_exact_counts_messages_and_warning(tmp_path: Path) -> None:
    example = _example(
        check_messages=("accepted example",),
        generation_messages=("wrote output",),
        warnings=("never fails",),
    )
    output = "3 assertion(s): 2 accepted, 1 skipped (X-check), 0 rejected\n"
    execution = Execution(
        Invocation(0, output + "warning  sample: never fails\naccepted example\n", ""),
        Invocation(0, "wrote output\nsample.sv:2:3: warning: never fails\n", ""),
    )

    assert verify(example, execution) == ()


@pytest.mark.parametrize(
    ("example", "execution"),
    [
        (
            _example(),
            Execution(
                Invocation(0, "1 assertion(s): 1 accepted, 0 rejected\n", ""),
                Invocation(0, "1 assertion(s): 1 accepted, 0 rejected\n", ""),
            ),
        ),
        (
            _example(check_exit=1, generation_exit=1),
            Execution(Invocation(0, "", ""), Invocation(0, "", "")),
        ),
        (
            _example(check_messages=("missing text",)),
            Execution(
                Invocation(0, "3 assertion(s): 2 accepted, 1 skipped (X-check), 0 rejected\n", ""),
                Invocation(0, "3 assertion(s): 2 accepted, 1 skipped (X-check), 0 rejected\n", ""),
            ),
        ),
        (
            _example(warnings=("expected warning",)),
            Execution(
                Invocation(0, "3 assertion(s): 2 accepted, 1 skipped (X-check), 0 rejected\n", ""),
                Invocation(0, "3 assertion(s): 2 accepted, 1 skipped (X-check), 0 rejected\n", ""),
            ),
        ),
        (
            _example(),
            Execution(
                Invocation(
                    0,
                    "3 assertion(s): 2 accepted, 1 skipped (X-check), 0 rejected\nwarning  extra\n",
                    "",
                ),
                Invocation(0, "3 assertion(s): 2 accepted, 1 skipped (X-check), 0 rejected\n", ""),
            ),
        ),
    ],
)
def test_verify_reports_wrong_counts_exits_messages_and_warnings(
    example: Example, execution: Execution
) -> None:
    assert verify(example, execution)


def test_verify_frontend_error_requires_absent_summaries_and_matches_messages() -> None:
    example = _example(
        counts=None, check_exit=1, generation_exit=1, check_messages=("syntax error",)
    )
    good = Execution(
        Invocation(1, "syntax error: bad token\n", ""),
        Invocation(1, "syntax error: bad token\n", ""),
    )
    bad = Execution(
        Invocation(1, "1 assertion(s): 0 accepted, 1 rejected\nsyntax error\n", ""),
        Invocation(1, "syntax error\n", ""),
    )

    assert verify(example, good) == ()
    assert verify(example, bad)


def test_verify_does_not_require_generation_warnings_after_generation_rejects() -> None:
    example = _example(
        counts=Counts(accepted=1, skipped=0, rejected=0),
        check_exit=0,
        generation_exit=1,
        warnings=("never fails",),
    )
    summary = "1 assertion(s): 1 accepted, 0 rejected\n"
    execution = Execution(
        Invocation(0, summary + "warning  sample: never fails\n", ""),
        Invocation(1, "generation rejected\n", ""),
    )

    assert verify(example, execution) == ()


def test_verify_counts_source_located_generation_warnings() -> None:
    example = _example(warnings=("never fails",))
    summary = "3 assertion(s): 2 accepted, 1 skipped (X-check), 0 rejected\n"
    execution = Execution(
        Invocation(0, summary + "warning  sample: never fails\n", ""),
        Invocation(0, "wrote output\nsample.sv:2:3: warning: never fails\n", ""),
    )

    assert verify(example, execution) == ()


def test_verify_rejects_unexpected_source_located_generation_warning() -> None:
    example = _example()
    summary = "3 assertion(s): 2 accepted, 1 skipped (X-check), 0 rejected\n"
    execution = Execution(
        Invocation(0, summary, ""),
        Invocation(0, "wrote output\nsample.sv:2:3: warning: extra\n", ""),
    )

    assert any("generation warning count" in error for error in verify(example, execution))


def test_run_example_uses_both_cli_modes_and_confines_output(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = root / "example.sv"
    source.parent.mkdir(parents=True)
    source.write_text(
        "module sample_top(input logic clk, a, b);\n"
        "  assert property (@(posedge clk) a |-> b);\nendmodule\n"
    )
    workdir = tmp_path / "work"
    workdir.mkdir()
    example = Example(
        key="sample",
        source="example.sv",
        top="sample_top",
        options=(),
        libraries=(),
        topics=("implication",),
        counts=Counts(accepted=1, skipped=0, rejected=0),
        check_exit=0,
        generation_exit=0,
        check_messages=(),
        generation_messages=(),
        warnings=(),
    )

    execution = run_example(example, root, workdir)

    assert execution.check.status == execution.generation.status == 0
    assert "1 assertion(s): 1 accepted, 0 rejected" in execution.check.stdout
    assert "wrote " in execution.generation.stdout
    generated = [path for path in workdir.rglob("*") if path.is_file()]
    assert generated
    assert all(path.is_relative_to(workdir) for path in generated)
    assert [path for path in root.rglob("*") if path.is_file()] == [source]
    assert source.read_text(encoding="utf-8").startswith("module sample_top")


def test_main_check_and_write_use_explicit_paths(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "sample.sv"
    source.write_text(
        "module sample_top(input logic clk, a, b);\n"
        "  assert property (@(posedge clk) a |-> b);\n"
        "endmodule\n"
    )
    catalog = _catalog(
        tmp_path / "examples.json",
        _entry(
            source="sample.sv",
            top="sample_top",
            counts={"accepted": 1, "skipped": 0, "rejected": 0},
        ),
    )
    example = Example(
        key="basic-assertion",
        source="sample.sv",
        top="sample_top",
        options=(),
        libraries=(),
        topics=("assert-property", "implication"),
        counts=Counts(1, 0, 0),
        check_exit=0,
        generation_exit=0,
        check_messages=(),
        generation_messages=(),
        warnings=(),
    )
    document = tmp_path / "reference.md"
    document.write_text(_DOCUMENT_BLOCK.format(key=example.key, code="stale"), encoding="utf-8")
    args = ["--catalog", str(catalog), "--document", str(document), "--root", str(root)]

    assert main([*args, "--check"]) == 1
    assert main([*args, "--write"]) == 0
    assert main([*args, "--check"]) == 0
    assert document.read_text(encoding="utf-8") == _DOCUMENT_BLOCK.format(
        key="basic-assertion",
        code=(
            "module sample_top(input logic clk, a, b);\n"
            "  assert property (@(posedge clk) a |-> b);\n"
            "endmodule"
        ),
    )
