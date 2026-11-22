"""Executable documentation examples and a deliberately narrow snippet format.

Contract:
* A JSON catalog has an ``examples`` list of unique slug IDs. Each example names
  a repository-relative SV source, top module, CLI options, library directories,
  topics, expected check counts and message fragments. Paths cannot escape the
  repository. Options may use CLI flags with values in the next entry or with
  ``=``. Warnings are independent of accepted/rejected/skipped status.
* Run the public CLI in check and generation modes with the same inputs. Generated
  files go only in the supplied work directory. Capture each invocation's exit,
  stdout and stderr; never derive expected answers from those outputs.
* Verify check counts, both exit statuses, declared messages and warning count.
  Missing summaries, extra warnings, wrong counts or fragments produce errors.
* Inline examples have exactly one ``<!-- sva-example: ID -->`` block per catalog
  ID, with one systemverilog fence and ``<!-- /sva-example -->``. Source text is
  canonical; an optional single pair of ``// reference-begin``/``// reference-end``
  lines selects the displayed snippet. Normalize CRLF only, preserving code text.
  Unknown, missing, duplicate or malformed markers fail rather than disappearing.
* Synchronization replaces only marked code bodies. A freshness check compares
  the document with regeneration, requires no external tool, and fails on drift.
  This helper is test infrastructure, never imported by product code.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


@dataclass(frozen=True)
class Counts:
    """Expected directive counts, independently of warnings."""

    accepted: int
    skipped: int
    rejected: int


@dataclass(frozen=True)
class Example:
    """One hand-specified CLI example and the reference topics it illustrates."""

    key: str
    source: str
    top: str
    options: tuple[str, ...]
    libraries: tuple[str, ...]
    topics: tuple[str, ...]
    counts: Counts | None
    check_exit: int
    generation_exit: int
    check_messages: tuple[str, ...]
    generation_messages: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class Invocation:
    """An actual CLI result, not an expected verdict."""

    status: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Execution:
    """Check and generation observations for identical example inputs."""

    check: Invocation
    generation: Invocation


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_SUMMARY = re.compile(
    r"^(?P<total>\d+) assertion\(s\): (?P<accepted>\d+) accepted, "
    r"(?:(?P<skipped>\d+) skipped \(X-check\), )?"
    r"(?P<rejected>\d+) rejected$"
)
_VALUE_OPTIONS = frozenset(
    {
        "-y",
        "-v",
        "-f",
        "-F",
        "-I",
        "-D",
        "--libext",
        "--top",
        "--strategy",
        "--x-checks",
        "--max-expansion",
        "--max-work",
        "--max-state-bits",
        "--max-emission",
    }
)
_START_MARKER = re.compile(r"(?m)^<!-- sva-example: ([a-z0-9]+(?:-[a-z0-9]+)*) -->[ \t]*$")
_END_MARKER = re.compile(r"(?m)^<!-- /sva-example -->[ \t]*$")
_BLOCK = re.compile(
    r"(?m)^<!-- sva-example: ([a-z0-9]+(?:-[a-z0-9]+)*) -->[ \t]*\r?\n"
    r"```systemverilog\r?\n(?P<code>.*?)\r?\n```\r?\n"
    r"<!-- /sva-example -->[ \t]*$",
    re.DOTALL,
)


def _string(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a non-empty string")
    return value


def _strings(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{where} must be a list of strings")
    return tuple(value)


def _repo_relative(value: object, where: str) -> str:
    text = _string(value, where)
    posix = PurePosixPath(text.replace("\\", "/"))
    windows = PureWindowsPath(text)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or not posix.parts
        or ".." in posix.parts
    ):
        raise ValueError(f"{where} must be a repository-relative path without '..': {text!r}")
    return text


def _path_from_relative(root: Path, text: str) -> Path:
    return root.joinpath(*PurePosixPath(text.replace("\\", "/")).parts)


def _invoke(example: Example, root: Path, output: Path | None) -> Invocation:
    from sva2yosys.cli import main as cli_main

    source = _path_from_relative(root, example.source).resolve()
    arguments = [str(source)]
    for library in example.libraries:
        arguments.extend(("-y", str(_path_from_relative(root, library).resolve())))
    arguments.extend(("--top", example.top, *example.options))
    if output is not None:
        arguments.extend(("-o", str(output.resolve())))
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = cli_main(arguments)
    except SystemExit as error:
        status = error.code if isinstance(error.code, int) else 1
    return Invocation(status, stdout.getvalue(), stderr.getvalue())


def load_catalog(path: Path) -> tuple[Example, ...]:
    """Read typed examples; ValueError on invalid fields, paths or duplicate IDs."""
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read example catalog {path}: {error}") from error
    if not isinstance(data, dict) or set(data) != {"examples"}:
        raise ValueError("catalog root must contain only the 'examples' list")
    if not isinstance(data["examples"], list):
        raise ValueError("catalog 'examples' must be a list")

    required = {"id", "source", "top", "topics", "counts", "check_exit", "generation_exit"}
    optional = {
        "options",
        "libraries",
        "check_messages",
        "generation_messages",
        "warnings",
    }
    examples: list[Example] = []
    seen: set[str] = set()
    for index, raw in enumerate(data["examples"]):
        where = f"examples[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} must be an object")
        missing = required - raw.keys()
        extra = raw.keys() - required - optional
        if missing:
            raise ValueError(f"{where} missing required fields: {', '.join(sorted(missing))}")
        if extra:
            raise ValueError(f"{where} has unknown fields: {', '.join(sorted(extra))}")

        key = _string(raw["id"], f"{where}.id")
        if _SLUG.fullmatch(key) is None:
            raise ValueError(f"{where}.id must be a lowercase slug")
        if key in seen:
            raise ValueError(f"duplicate example id {key!r}")
        seen.add(key)

        source = _repo_relative(raw["source"], f"{where}.source")
        if PurePosixPath(source.replace("\\", "/")).suffix.lower() != ".sv":
            raise ValueError(f"{where}.source must name a .sv file")
        top = _string(raw["top"], f"{where}.top")
        topics = _strings(raw["topics"], f"{where}.topics")
        options = _strings(raw.get("options", []), f"{where}.options")
        expects_value = False
        for option in options:
            if (
                option == "-o"
                or option.startswith("-o")
                or option == "--output-dir"
                or option.startswith("--output-dir=")
            ):
                raise ValueError(f"{where}.options cannot set the output directory")
            if option.startswith("-"):
                expects_value = False if expects_value else option in _VALUE_OPTIONS
            elif expects_value:
                expects_value = False
            else:
                raise ValueError(f"{where}.options entries must be CLI flags")
        if expects_value:
            raise ValueError(f"{where}.options has a flag without its value")
        libraries = tuple(
            _repo_relative(library, f"{where}.libraries")
            for library in _strings(raw.get("libraries", []), f"{where}.libraries")
        )
        check_messages = _strings(raw.get("check_messages", []), f"{where}.check_messages")
        generation_messages = _strings(
            raw.get("generation_messages", []), f"{where}.generation_messages"
        )
        warnings = _strings(raw.get("warnings", []), f"{where}.warnings")

        counts_data = raw["counts"]
        counts: Counts | None
        if counts_data is None:
            counts = None
        else:
            if not isinstance(counts_data, dict) or set(counts_data) != {
                "accepted",
                "skipped",
                "rejected",
            }:
                raise ValueError(f"{where}.counts must contain accepted, skipped and rejected")
            count_values = (counts_data[name] for name in ("accepted", "skipped", "rejected"))
            if any(type(value) is not int or value < 0 for value in count_values):
                raise ValueError(f"{where}.counts values must be non-negative integers")
            counts = Counts(
                accepted=counts_data["accepted"],
                skipped=counts_data["skipped"],
                rejected=counts_data["rejected"],
            )

        exits: list[int] = []
        for field in ("check_exit", "generation_exit"):
            status = raw[field]
            if type(status) is not int or status < 0:
                raise ValueError(f"{where}.{field} must be a non-negative integer exit status")
            exits.append(status)
        check_exit, generation_exit = exits
        if counts is None and (check_exit == 0 or generation_exit == 0):
            raise ValueError(f"{where}.counts may be null only for a frontend error")

        examples.append(
            Example(
                key=key,
                source=source,
                top=top,
                options=options,
                libraries=libraries,
                topics=topics,
                counts=counts,
                check_exit=check_exit,
                generation_exit=generation_exit,
                check_messages=check_messages,
                generation_messages=generation_messages,
                warnings=warnings,
            )
        )
    return tuple(examples)


def snippet(source: str) -> str:
    """Return canonical display text, rejecting malformed optional source markers."""
    normalized = source.replace("\r\n", "\n")
    lines = normalized.splitlines(keepends=True)
    begin = [index for index, line in enumerate(lines) if line.strip() == "// reference-begin"]
    end = [index for index, line in enumerate(lines) if line.strip() == "// reference-end"]
    marker_like = [
        line for line in lines if "// reference-begin" in line or "// reference-end" in line
    ]
    if any(line.strip() not in ("// reference-begin", "// reference-end") for line in marker_like):
        raise ValueError("malformed reference snippet marker")
    if not begin and not end:
        return normalized.strip("\n")
    if len(begin) != 1 or len(end) != 1 or begin[0] >= end[0]:
        raise ValueError("reference snippet needs exactly one ordered marker pair")
    return "".join(lines[begin[0] + 1 : end[0]]).strip("\n")


def synchronize(document: str, examples: tuple[Example, ...], root: Path) -> str:
    """Regenerate marked code bodies; ValueError on malformed/incomplete coverage."""
    identifiers = [example.key for example in examples]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate example IDs")

    starts = list(_START_MARKER.finditer(document))
    ends = list(_END_MARKER.finditer(document))
    marker_lines = [
        line
        for line in document.splitlines()
        if "<!-- sva-example" in line or "<!-- /sva-example" in line
    ]
    if len(marker_lines) != len(starts) + len(ends):
        raise ValueError("malformed example marker")
    identifiers_in_doc = [match.group(1) for match in starts]
    if len(set(identifiers_in_doc)) != len(identifiers_in_doc):
        raise ValueError("duplicate example block")
    if set(identifiers_in_doc) != set(identifiers):
        missing = sorted(set(identifiers) - set(identifiers_in_doc))
        unknown = sorted(set(identifiers_in_doc) - set(identifiers))
        raise ValueError(f"example block coverage mismatch; missing={missing}, unknown={unknown}")

    blocks = list(_BLOCK.finditer(document))
    if len(blocks) != len(starts) or len(ends) != len(starts):
        raise ValueError("malformed or incomplete example block")
    if [match.group(1) for match in blocks] != identifiers_in_doc:
        raise ValueError("malformed or misordered example block")

    by_id = {example.key: example for example in examples}
    replacements: list[tuple[int, int, str]] = []
    root = root.resolve()
    for match in blocks:
        example = by_id[match.group(1)]
        source_path = _path_from_relative(root, example.source).resolve()
        if not source_path.is_relative_to(root) or not source_path.is_file():
            raise ValueError(f"source for {example.key} is missing or outside the repository")
        try:
            source_text = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise ValueError(f"cannot read source for {example.key}: {error}") from error
        replacements.append((match.start("code"), match.end("code"), snippet(source_text)))
    updated = document
    for start, end, display in reversed(replacements):
        updated = updated[:start] + display + updated[end:]
    return updated


def run_example(example: Example, root: Path, workdir: Path) -> Execution:
    """Execute a catalog-validated example through cli.main, writing under workdir."""
    root = root.resolve()
    workdir = workdir.resolve()
    source = _path_from_relative(root, example.source).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError(f"example source is missing or outside the repository: {example.source}")
    for library in example.libraries:
        path = _path_from_relative(root, library).resolve()
        if not path.is_relative_to(root) or not path.exists():
            raise ValueError(f"example library is missing or outside the repository: {library}")
    workdir.mkdir(parents=True, exist_ok=True)
    output = workdir / example.key
    if not output.resolve().is_relative_to(workdir):
        raise ValueError("generation output escapes the work directory")
    check = _invoke(example, root, None)
    generation = _invoke(example, root, output)
    return Execution(check=check, generation=generation)


def _match_warning_fragments(
    key: str, expected: tuple[str, ...], lines: list[str], errors: list[str], mode: str
) -> None:
    unmatched = list(lines)
    for fragment in expected:
        index = next((i for i, line in enumerate(unmatched) if fragment in line), None)
        if index is None:
            errors.append(f"{key}: {mode} warnings are missing {fragment!r}")
        else:
            unmatched.pop(index)


def _is_warning(line: str) -> bool:
    return line.startswith("warning") or ": warning: " in line


def verify(example: Example, execution: Execution) -> tuple[str, ...]:
    """Return all expectation mismatches; an empty tuple means both modes agree."""
    errors: list[str] = []
    if execution.check.status != example.check_exit:
        errors.append(
            f"{example.key}: check exit {execution.check.status}, expected {example.check_exit}"
        )
    if execution.generation.status != example.generation_exit:
        errors.append(
            f"{example.key}: generation exit {execution.generation.status}, "
            f"expected {example.generation_exit}"
        )

    check_output = execution.check.stdout + execution.check.stderr
    generation_output = execution.generation.stdout + execution.generation.stderr
    for fragment in example.check_messages:
        if fragment not in check_output:
            errors.append(f"{example.key}: check output is missing {fragment!r}")
    for fragment in example.generation_messages:
        if fragment not in generation_output:
            errors.append(f"{example.key}: generation output is missing {fragment!r}")

    summaries = [line for line in check_output.splitlines() if _SUMMARY.fullmatch(line)]
    if example.counts is None:
        if summaries:
            errors.append(f"{example.key}: frontend error unexpectedly printed a check summary")
    elif len(summaries) != 1:
        errors.append(f"{example.key}: expected one check summary, found {len(summaries)}")
    else:
        match = _SUMMARY.fullmatch(summaries[0])
        assert match is not None
        actual = (
            int(match.group("accepted")),
            int(match.group("skipped") or 0),
            int(match.group("rejected")),
        )
        expected = (example.counts.accepted, example.counts.skipped, example.counts.rejected)
        if actual != expected:
            errors.append(f"{example.key}: check counts {actual}, expected {expected}")
        total = sum(actual)
        if int(match.group("total")) != total:
            errors.append(
                f"{example.key}: summary total {match.group('total')} does not equal {total}"
            )

    if any(_SUMMARY.fullmatch(line) for line in generation_output.splitlines()):
        errors.append(f"{example.key}: generation unexpectedly printed a check summary")

    check_warnings = [line for line in check_output.splitlines() if _is_warning(line)]
    if len(check_warnings) != len(example.warnings):
        errors.append(
            f"{example.key}: check warning count {len(check_warnings)}, "
            f"expected {len(example.warnings)}"
        )
    _match_warning_fragments(example.key, example.warnings, check_warnings, errors, "check")
    if execution.generation.status == 0:
        generation_warnings = [line for line in generation_output.splitlines() if _is_warning(line)]
        if len(generation_warnings) != len(example.warnings):
            errors.append(
                f"{example.key}: generation warning count {len(generation_warnings)}, "
                f"expected {len(example.warnings)}"
            )
        _match_warning_fragments(
            example.key, example.warnings, generation_warnings, errors, "generation"
        )
    return tuple(errors)


def main(argv: list[str] | None = None) -> int:
    """Refresh/check reference snippets: --write or --check; 1 on stale/invalid data."""
    parser = argparse.ArgumentParser(description="Check and refresh SVA reference examples")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="check examples and snippet freshness")
    action.add_argument("--write", action="store_true", help="rewrite marked snippets")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--document", type=Path)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args(argv)

    root = (args.root or _REPOSITORY_ROOT).resolve()
    catalog = args.catalog or Path(
        "tests/functional_tests/documentation_examples/fixtures/examples.json"
    )
    document = args.document or Path("docs/sva-examples.md")
    if not catalog.is_absolute():
        catalog = root / catalog
    if not document.is_absolute():
        document = root / document
    try:
        examples = load_catalog(catalog)
        current = document.read_text(encoding="utf-8")
        generated = synchronize(current, examples, root)
        for example in examples:
            with tempfile.TemporaryDirectory(prefix=f"sva-example-{example.key}-") as name:
                execution = run_example(example, root, Path(name))
            mismatches = verify(example, execution)
            if mismatches:
                for mismatch in mismatches:
                    print(mismatch, file=sys.stderr)
                return 1
        if args.write:
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_text(generated, encoding="utf-8", newline="\n")
            return 0
        if current != generated:
            print(f"stale documentation examples in {document}", file=sys.stderr)
            return 1
        return 0
    except (OSError, UnicodeError, ValueError) as error:
        print(f"documentation examples: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
