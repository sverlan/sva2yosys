"""Test support: acceptance, size and time survey of sva2yosys over the public SVA corpora.

A regression tool, not a pytest test (a run takes minutes)::

    uv run python tests/support/survey.py <corpora-root> [--out <dir>] [--baseline [<file>]]
        [--update-baseline] [--jobs <n>] [corpus ...]

``<corpora-root>`` holds the checkouts fetched by ``tests/support/corpora.py`` (one directory
per corpus). ``corpus`` names select recipes (default: those whose checkout exists):
``axi``, ``sv-tests``, ``cc``, ``ot``, ``wb2axip``, ``ab``. Nothing from a corpus is ever executed:
files are only passed to the sva2yosys command line, in a subprocess per job.

Contract
--------
* **Recipes.** ``RECIPES`` is the one table of corpora; each recipe plans a list of jobs
  (a label and sva2yosys arguments) from the corpora root and a scratch directory.
* **AssertionBench** (``ab``, checkout ``assertion_data_for_LLM``; acceptance
  only, the repository's published formal verdicts are never used). One job per design folder
  ``verified_assertions/<group>/<design>/`` that has a ``bindings.sva``: the verification
  files are ``bindings.sva`` and the property
  module, the design files (``-v``) the ``*.v`` of the folder that no other of them
  includes (an included file is a header, found through ``-I <folder>``; giving it as a design
  file too makes the compilation fail), the top ``<design>`` (the folder name). The property
  module is the ``*.sva`` file of the folder that defines
  ``module i_<design>`` (``property.sva`` when several do, else the first by name); when the
  folder has none but a ``property.sva.tar.gz``, the member of the archive that is a regular
  file ending in ``.sva`` and defining that module is extracted (standard library ``tarfile``)
  into the scratch directory (a member with an absolute path, a drive or a ``..`` component
  is refused, never extracted); a folder with no usable property module is not a job. The job
  label is ``ab/<group>/<design>``. Each run of an AssertionBench job may take 300 seconds
  (the default, 120, made a two-minute job's result depend on machine load); the designs of
  ``_AB_TOO_LARGE`` are not jobs, with their reason (``MAC_rx_FF``: 144261 assertions in one
  job, not finished in 25 minutes).
* **Records.** Each job runs the CLI check mode and yields one ``Entry`` per directive line:
  ``corpus``, ``run`` (job label), ``file``, ``name`` (qualified directive name), ``status``
  (``ok``, ``skipped``, ``rejected``; ``tool-error`` for a job that printed no directive
  line and failed or timed out, with its first error line as reason), ``category`` (the
  rejection reason through ``normalise_reason``; empty for ``ok``) and, for ``ok``, the
  check mode's ``slots`` and ``bits`` of that directive. A directive of a library module is
  counted once per job that elaborates it.
* **Size.** Two figures. (1) Per directive, the check mode's ``bits`` (temporal state bits;
  ``None`` is counted as 0) and ``slots`` of every accepted directive, wherever it is
  written. (2) Per job, in bytes: a second run of the job with ``-o`` writes
  ``sva2yosys_monitors.sv``; its size is the job's monitor size and the largest
  ``module ... endmodule`` in it the job's largest-module size. A directive has no module of
  its own (monitors are shared per assertion site) and the rewrite translates only the
  assertions of the *verification* files (the first file of a job), so bytes cover those
  only: a job whose assertions sit in library (design) files has few or no bytes. A job
  with no accepted directive, or whose ``-o`` run fails, has no byte size.
* **Time.** The wall-clock seconds of the check-mode subprocess (interpreter start and
  Slang elaboration included), per job; the summary adds them up. No per-directive time.
* **Summary.** Per corpus: the count per status, the ``top`` most frequent rejection
  categories (count descending, then name), the total and the largest per-directive ``bits``,
  the total monitor bytes over jobs, the largest module over jobs, and the total seconds (one
  decimal).
* **Baseline** (``tests/support/survey_baseline.json``, committed, human-readable, sorted
  keys): the summaries of the corpora. ``save_baseline`` merges the given corpora into the
  file. ``compare_baselines`` fails (exit status 1) on any change of a status count or of
  the count of a rejection category listed in either top list, on total bits or total bytes
  growing beyond ``Tolerance.total_growth`` (default 10 %), on the largest directive (bits) or
  the largest module (bytes) growing by ``Tolerance.max_factor`` (default 2x) or more,
  and on a corpus missing from the baseline.
  Seconds beyond ``Tolerance.time_growth`` (default +50 %) are only a notice (timing is
  noisy). The baseline change is reviewed like ``expectations.txt``.
* **Completion.** A job is incomplete when its check run times out, exits with a status
  other than 0 or 1, prints a traceback, or prints directive lines without the summary line;
  a job whose ``-o`` run fails while it has accepted directives has a failed size. The
  exit status must match the summary line (1 iff it reports a rejection). The baseline
  records the incomplete count and the labels of the jobs without size; any change of the
  count, or a job without size that had one in the baseline, fails the comparison.
  The children run with UTF-8 output. An empty selection of corpora is
  an error; a run without corpus names also fails when a baseline corpus was not surveyed.
* **Directive baseline** (``tests/support/survey_directives.tsv``, committed, next to the
  aggregate baseline; for ``--baseline FILE`` it is ``FILE`` with the suffix ``.tsv``): the
  aggregate figures cannot see swaps between directives, so every directive is recorded. The
  first line is the comment ``# survey-directives v2``; then one line per entry, tab-separated
  ``corpus, run, name, occurrence, status, category, bits``, sorted by those columns (the
  occurrence numerically; so the file is deterministic); ``category`` is empty for ``ok``;
  ``bits`` is the decimal count or ``-`` (not ok, or unknown); tabs and line breaks inside a
  field become spaces. Timing and slots are never recorded. **Identity.** A directive is
  identified by ``(corpus, run, name, occurrence)``: ``occurrence`` is its 1-based number among
  the entries with the same ``(corpus, run, name)`` in the order the job reports them (the
  tool's output order is deterministic), so two directives of one run with the same name (two
  ``(unnamed)`` ones) cannot swap their values unseen. ``directive_rows`` numbers its entries
  in the order given. ``--update-baseline`` writes the file too, merging by corpus like
  ``save_baseline``: ``save_directives`` takes the names of the surveyed corpora explicitly and
  replaces the stored rows of all of them, so a corpus surveyed with no directive loses its
  stale rows. ``--baseline`` also compares it, for the surveyed corpora only (again passed
  explicitly to ``compare_directives``, not inferred from the rows): one line
  ``<corpus> <run> <name>: <old> -> <new>`` (``<name> #<n>`` for an occurrence after the first)
  per directive added (old ``<absent>``), removed (new ``<absent>``) or changed in status,
  category or bits (``<status>/<category>/<bits>``); ``main`` prints each as ``DIRECTIVE: ...``
  and counts it as a failure. A missing directive file is one failure saying to run
  ``--update-baseline``.
* **Aggregate-only corpora** (``AGGREGATE_ONLY``: ``ab``). A corpus with very many, nearly
  uniform directives is compared in aggregate only: its summary stays in the aggregate baseline
  (counts, rejection categories, sizes) but ``directive_entries`` leaves its directives out of
  the directive baseline, which neither stores nor compares them (``--update-baseline`` removes
  any stored rows of such a corpus). Reason: AssertionBench has 149k directives, all accepted,
  which would take the directive file from 0.3 MB to 17 MB. Blind spots of the aggregate
  comparison, accepted for such a corpus: per-directive bit changes that cancel in the total,
  size decreases (only growth is compared, with a tolerance), growth within the tolerance, and
  swaps of results between directives.
* **Parallel jobs** (``--jobs <n>``, default 1, at least 1; anything else is a usage error).
  The jobs of one corpus run on ``n`` worker threads, each launching its subprocesses
  (``run_corpus_jobs``); corpora still run one after another. The result is independent of
  ``n``: entries, run records, summaries and both baselines are collected in plan order, so they
  equal those of a sequential run. Each job writes its ``-o`` output to a directory of its own
  under the scratch directory, so concurrent jobs never share files. Only the seconds depend on
  the load that parallel runs put on the machine (a notice, never a failure); the per-run
  timeout applies as before, so choose ``n`` below the number of cores to keep jobs from
  timing out under load.
* **Progress.** ``run_corpus_jobs`` writes one line to standard error as each job finishes,
  ``<corpus> <done>/<total> <label>``, where ``done`` counts the finished jobs (so it is right
  whatever order parallel jobs finish in). Standard output, the records, the summaries and the
  baselines are unaffected.
* Writes only to ``--out`` (jsonl per corpus), the baseline files and a scratch directory
  that is removed at the end. Nothing here imports pyslang; ``src/`` never imports this
  module.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath

_DEFAULT_BASELINE = Path(__file__).resolve().with_name("survey_baseline.json")
_DEFAULT_DIRECTIVES = Path(__file__).resolve().with_name("survey_directives.tsv")
_DIRECTIVES_HEADER = "# survey-directives v2"
_ABSENT = "<absent>"
_MONITORS_FILE = "sva2yosys_monitors.sv"
_TIMEOUT = 120
_REASON_WIDTH = 150
_TOP = 15
_VERSION = 1

_OK_LINE = re.compile(r"^ok\s+(\S+)\s+\w+\s+slots=(\d+)\s+form=\S+\s+bits=(\d+|None)\s*$")
_BAD_LINE = re.compile(r"^(rejected|skipped)\s+(.+?:\d+:\d+|<unknown>)  (\S+): (.*)$")
_SUMMARY_LINE = re.compile(r"^\d+ assertion\(s\): .*?(\d+) rejected\s*$", re.M)


class Status(Enum):
    """Outcome of one directive (or, for ``TOOL_ERROR``, of a job without directive lines)."""

    OK = "ok"
    SKIPPED = "skipped"
    REJECTED = "rejected"
    TOOL_ERROR = "tool-error"


@dataclass(frozen=True)
class ParsedLine:
    """One directive line of the check mode's output."""

    status: Status
    name: str
    location: str
    """Source location of a rejected or skipped directive; empty for ``ok``."""
    message: str
    slots: int | None
    bits: int | None


@dataclass(frozen=True)
class Entry:
    """One directive of one job (see the module contract)."""

    corpus: str
    run: str
    file: str
    name: str
    status: Status
    category: str
    slots: int | None
    bits: int | None


@dataclass(frozen=True, order=True)
class DirectiveRow:
    """One line of the directive baseline (all columns as strings; see the module contract)."""

    corpus: str
    run: str
    name: str
    occurrence: int
    status: str
    category: str
    bits: str


@dataclass(frozen=True)
class RunRecord:
    """One job: its check-mode seconds and its monitor sizes in bytes (``None``: unknown)."""

    corpus: str
    run: str
    seconds: float
    monitor_bytes: int | None
    largest_module_bytes: int | None
    completed: bool = True
    """False: the check run timed out, died with an unexpected exit status, or printed
    directive lines without its summary line (a crash after partial output)."""
    size_failed: bool = False
    """True: the job has accepted directives but its ``-o`` run failed or wrote no monitors
    file, so its sizes are unknown."""


@dataclass(frozen=True)
class Summary:
    """The baseline-relevant figures of one corpus."""

    counts: dict[str, int]
    top_categories: tuple[tuple[str, int], ...]
    total_bits: int
    max_bits: int
    total_bytes: int
    max_module_bytes: int
    seconds: float
    incomplete_jobs: int = 0
    size_failed_jobs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Tolerance:
    """Thresholds of ``compare_baselines``."""

    total_growth: float = 0.10
    max_factor: float = 2.0
    time_growth: float = 0.50


@dataclass(frozen=True)
class Report:
    """Differences against a baseline: ``failures`` set the exit status, ``notices`` do not."""

    failures: tuple[str, ...]
    notices: tuple[str, ...]


@dataclass(frozen=True)
class Job:
    """One sva2yosys invocation: a label and its arguments."""

    label: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class Recipe:
    """A corpus: its name, the directory under the corpora root, and its job planner."""

    name: str
    directory: str
    plan: Callable[[Path, Path], list[Job]]
    """``plan(corpus_dir, scratch_dir)``: the jobs."""
    timeout: int = _TIMEOUT
    """Seconds allowed to each sva2yosys run of a job (a run past it is a ``timeout``)."""
    left_out: tuple[tuple[str, str], ...] = ()
    """(design, reason) of the designs the planner leaves out; printed with the summary."""


def normalise_reason(text: str) -> str:
    """The category of a rejection reason: identifiers, numbers and paths masked, at most 150
    characters."""
    text = re.sub(
        r"the disable iff condition reads .*?, which may change",
        "the disable iff condition reads X, which may change",
        text,
    )
    text = re.sub(r"\([^()]*is not a top-level input[^)]*\)", "(...)", text)
    text = re.sub(r"\([^()]*connected to an expression[^)]*\)", "(...)", text)
    text = re.sub(r"[A-Za-z]:\\\S+", "<path>", text)
    text = re.sub(r"(?<![\w.])/(?:[\w.-]+/)+[\w.-]+", "<path>", text)
    text = re.sub(r"'[\w.]+::\w+(\[[^']*\])?'", "'PKG::ID'", text)
    text = re.sub(r"unsupported type '[^']*'", "unsupported type (non-packed-integral)", text)
    text = re.sub(r"\d+", "N", text)
    return text[:_REASON_WIDTH]


def parse_check_output(stdout: str) -> list[ParsedLine]:
    """The ``ok``, ``rejected`` and ``skipped`` lines of the check mode's output (warnings
    and the summary line are ignored)."""
    parsed: list[ParsedLine] = []
    for line in stdout.splitlines():
        ok = _OK_LINE.match(line)
        if ok is not None:
            bits = None if ok.group(3) == "None" else int(ok.group(3))
            parsed.append(
                ParsedLine(Status.OK, ok.group(1), "", "", int(ok.group(2)), bits),
            )
            continue
        bad = _BAD_LINE.match(line)
        if bad is not None:
            status = Status.REJECTED if bad.group(1) == "rejected" else Status.SKIPPED
            parsed.append(ParsedLine(status, bad.group(3), bad.group(2), bad.group(4), None, None))
    return parsed


def parse_module_sizes(text: str) -> tuple[int, ...]:
    """The byte size (UTF-8) of each ``module ... endmodule`` chunk of a monitors file."""
    chunks = text.split("endmodule")
    return tuple(len((c + "endmodule").strip().encode("utf-8")) for c in chunks[:-1])


def summarise(entries: Sequence[Entry], runs: Sequence[RunRecord], top: int = _TOP) -> Summary:
    """The summary of one corpus's entries and runs."""
    counts = {status.value: 0 for status in Status}
    categories: dict[str, int] = {}
    for entry in entries:
        counts[entry.status.value] += 1
        if entry.category:
            categories[entry.category] = categories.get(entry.category, 0) + 1
    ranked = sorted(categories.items(), key=lambda item: (-item[1], item[0]))
    return Summary(
        counts=counts,
        top_categories=tuple(ranked[:top]),
        total_bits=sum(e.bits or 0 for e in entries),
        max_bits=max((e.bits or 0 for e in entries), default=0),
        total_bytes=sum(r.monitor_bytes or 0 for r in runs),
        max_module_bytes=max((r.largest_module_bytes or 0 for r in runs), default=0),
        seconds=round(sum(r.seconds for r in runs), 1),
        incomplete_jobs=sum(1 for r in runs if not r.completed),
        size_failed_jobs=tuple(sorted(r.run for r in runs if r.size_failed)),
    )


def save_baseline(path: Path, summaries: Mapping[str, Summary]) -> None:
    """Merge ``summaries`` into the baseline file ``path`` (created if missing)."""
    corpora = {name: _to_json(s) for name, s in _read(path).items()} if path.exists() else {}
    corpora.update({name: _to_json(s) for name, s in summaries.items()})
    document = {"version": _VERSION, "corpora": corpora}
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def load_baseline(path: Path) -> dict[str, Summary]:
    """The summaries stored in the baseline file ``path``."""
    return _read(path)


AGGREGATE_ONLY: frozenset[str] = frozenset({"ab"})
"""Corpora compared in aggregate only: their directives are not written to the directive
baseline (see the module contract)."""


def directive_entries(entries: Sequence[Entry]) -> list[Entry]:
    """The entries that belong in the directive baseline: those of the corpora that are not
    :data:`AGGREGATE_ONLY`."""
    return [entry for entry in entries if entry.corpus not in AGGREGATE_ONLY]


def directive_rows(entries: Sequence[Entry]) -> list[DirectiveRow]:
    """The directive baseline rows of ``entries`` (see the module contract), sorted; the
    occurrence numbers follow the order of ``entries``."""
    seen: dict[tuple[str, str, str], int] = {}
    rows: list[DirectiveRow] = []
    for e in entries:
        key = (_field(e.corpus), _field(e.run), _field(e.name))
        seen[key] = seen.get(key, 0) + 1
        rows.append(
            DirectiveRow(
                *key,
                seen[key],
                e.status.value,
                _field(e.category),
                "-" if e.bits is None else str(e.bits),
            )
        )
    return sorted(rows)


def save_directives(path: Path, rows: Sequence[DirectiveRow], corpora: Collection[str]) -> None:
    """Merge ``rows`` into the directive baseline ``path``: the stored rows of every corpus in
    ``corpora`` (the surveyed ones, also those without a row) are replaced by ``rows``, the
    others are kept."""
    replaced = set(corpora)
    kept = [r for r in load_directives(path) if r.corpus not in replaced] if path.exists() else []
    lines = [_DIRECTIVES_HEADER] + [
        "\t".join((r.corpus, r.run, r.name, str(r.occurrence), r.status, r.category, r.bits))
        for r in sorted([*kept, *rows])
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def load_directives(path: Path) -> list[DirectiveRow]:
    """The rows stored in the directive baseline ``path``."""
    rows: list[DirectiveRow] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if number == 1:
            if line != _DIRECTIVES_HEADER:
                raise ValueError(f"{path}: expected '{_DIRECTIVES_HEADER}' on the first line")
            continue
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 7 or not parts[3].isdigit():
            raise ValueError(f"{path}:{number}: expected 7 tab-separated columns")
        rows.append(
            DirectiveRow(parts[0], parts[1], parts[2], int(parts[3]), parts[4], parts[5], parts[6])
        )
    return rows


def compare_directives(
    baseline: Sequence[DirectiveRow],
    current: Sequence[DirectiveRow],
    surveyed: Collection[str],
) -> tuple[str, ...]:
    """One line per directive added, removed or changed in status, category or bits, for the
    ``surveyed`` corpora (see the module contract); sorted by key."""
    chosen = set(surveyed)
    old = _by_key([r for r in baseline if r.corpus in chosen])
    new = _by_key([r for r in current if r.corpus in chosen])
    lines: list[str] = []
    for key in sorted(set(old) | set(new)):
        was, now = old.get(key, _ABSENT), new.get(key, _ABSENT)
        if was != now:
            suffix = f" #{key[3]}" if key[3] > 1 else ""
            lines.append(f"{key[0]} {key[1]} {key[2]}{suffix}: {was} -> {now}")
    return tuple(lines)


def _field(text: str) -> str:
    return re.sub(r"[\t\r\n]", " ", text)


def _by_key(rows: Sequence[DirectiveRow]) -> dict[tuple[str, str, str, int], str]:
    return {
        (r.corpus, r.run, r.name, r.occurrence): f"{r.status}/{r.category}/{r.bits}" for r in rows
    }


def compare_baselines(
    baseline: Mapping[str, Summary],
    current: Mapping[str, Summary],
    tolerance: Tolerance,
    require_all: bool = False,
) -> Report:
    """Compare the ``current`` corpora with ``baseline`` (see the module contract); with
    ``require_all``, a baseline corpus absent from ``current`` is a failure too."""
    failures: list[str] = []
    notices: list[str] = []
    if require_all:
        failures.extend(
            f"{name}: in the baseline but not surveyed"
            for name in sorted(set(baseline) - set(current))
        )
    for name in sorted(current):
        now = current[name]
        if name not in baseline:
            failures.append(f"{name}: no baseline (run with --update-baseline)")
            continue
        before = baseline[name]
        for status in sorted(set(before.counts) | set(now.counts)):
            old, new = before.counts.get(status, 0), now.counts.get(status, 0)
            if old != new:
                failures.append(f"{name}: {status} {old} -> {new}")
        if now.incomplete_jobs != before.incomplete_jobs:
            failures.append(
                f"{name}: incomplete jobs {before.incomplete_jobs} -> {now.incomplete_jobs}"
            )
        for job in sorted(set(now.size_failed_jobs) - set(before.size_failed_jobs)):
            failures.append(f"{name}: no monitor size for {job} (measured in the baseline)")
        old_cats, new_cats = dict(before.top_categories), dict(now.top_categories)
        for category in sorted(set(old_cats) | set(new_cats)):
            old, new = old_cats.get(category, 0), new_cats.get(category, 0)
            if old != new:
                failures.append(f"{name}: rejection '{category}' {old} -> {new}")
        for what, old, new in (
            ("total state bits", before.total_bits, now.total_bits),
            ("total monitor size", before.total_bytes, now.total_bytes),
        ):
            if new > old * (1 + tolerance.total_growth):
                failures.append(f"{name}: {what} {old} -> {new}")
        for what, old, new in (
            ("largest directive (bits)", before.max_bits, now.max_bits),
            ("largest module (bytes)", before.max_module_bytes, now.max_module_bytes),
        ):
            if old and new >= old * tolerance.max_factor:
                failures.append(f"{name}: {what} {old} -> {new}")
        if before.seconds and now.seconds > before.seconds * (1 + tolerance.time_growth):
            notices.append(f"{name}: time {before.seconds} s -> {now.seconds} s")
    return Report(tuple(failures), tuple(notices))


def _to_json(summary: Summary) -> dict[str, object]:
    return {
        "counts": summary.counts,
        "rejection_categories": [[c, n] for c, n in summary.top_categories],
        "bits_total": summary.total_bits,
        "bits_largest_directive": summary.max_bits,
        "monitor_bytes_total": summary.total_bytes,
        "monitor_bytes_largest_module": summary.max_module_bytes,
        "seconds": summary.seconds,
        "incomplete_jobs": summary.incomplete_jobs,
        "size_failed_jobs": list(summary.size_failed_jobs),
    }


def _read(path: Path) -> dict[str, Summary]:
    document = json.loads(path.read_text(encoding="utf-8"))
    summaries: dict[str, Summary] = {}
    for name, item in document["corpora"].items():
        summaries[name] = Summary(
            counts={str(k): int(v) for k, v in item["counts"].items()},
            top_categories=tuple((str(c), int(n)) for c, n in item["rejection_categories"]),
            total_bits=int(item["bits_total"]),
            max_bits=int(item["bits_largest_directive"]),
            total_bytes=int(item["monitor_bytes_total"]),
            max_module_bytes=int(item["monitor_bytes_largest_module"]),
            seconds=float(item["seconds"]),
            incomplete_jobs=int(item.get("incomplete_jobs", 0)),
            size_failed_jobs=tuple(str(j) for j in item.get("size_failed_jobs", [])),
        )
    return summaries


# ---------------------------------------------------------------- recipes


def _has_sva(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    return bool(
        re.search(r"\b(assert|assume|cover|expect)\s+property\b|^\s*property\s", text, re.M)
    )


def _first_module(path: Path) -> str | None:
    match = re.search(
        r"^\s*module\s+(\w+)", path.read_text(encoding="utf-8", errors="replace"), re.M
    )
    return match.group(1) if match else None


def _job(label: str, *args: object) -> Job:
    return Job(label, tuple(str(a) for a in args))


def _plan_axi(corpus: Path, scratch: Path) -> list[Job]:
    src = corpus / "AXI4" / "src"
    libs = ["-y", src, "-y", src / "axi4_lib", "-y", src / "axi4_spec", "-I", src]
    original = (src / "amba_axi4_protocol_checker.sv").read_text(encoding="utf-8")
    jobs: list[Job] = []
    for protocol in ("AXI4LITE", "AXI4FULL"):
        for agent in ("SOURCE", "DESTINATION", "MONITOR"):
            name = f"ck_{protocol}_{agent}"
            text = original.replace("module amba_axi4_protocol_checker", f"module {name}")
            text = re.sub(r"VERIFY_AGENT_TYPE:\s*\w+", f"VERIFY_AGENT_TYPE: {agent}", text, count=1)
            text = re.sub(r"PROTOCOL_TYPE:\s*\w+", f"PROTOCOL_TYPE: {protocol}", text, count=1)
            wrapper = scratch / f"{name}.sv"
            wrapper.write_text(text, encoding="utf-8")
            jobs.append(_job(f"axi4/{name}", wrapper, *libs, "--top", name))
    default = src / "amba_axi4_protocol_checker.sv"
    jobs.append(
        _job("axi4/protocol_checker_default", default, *libs, "--top", "amba_axi4_protocol_checker")
    )
    stream = corpus / "AXI4_STREAM"
    jobs.append(
        _job(
            "axi4_stream/amba_axi4_stream",
            stream / "amba_axi4_stream.sv",
            "-y",
            stream,
            "-I",
            stream,
            "--top",
            "amba_axi4_stream",
        )
    )
    return jobs


def _plan_sv_tests(corpus: Path, scratch: Path) -> list[Job]:
    tests = corpus / "tests"
    files = sorted((tests / "chapter-16").glob("*.sv")) + [
        p for p in sorted((tests / "chapter-9").glob("*.sv")) if _has_sva(p)
    ]
    return [_job(f"sv-tests/{f.parent.name}/{f.name}", f) for f in files]


def _plan_common_cells(corpus: Path, scratch: Path) -> list[Job]:
    common = [
        "-I",
        corpus / "include",
        "-y",
        corpus / "src",
        "-y",
        corpus / "src" / "deprecated",
        "--libext",
        ".sv",
    ]
    jobs: list[Job] = []
    for f in sorted((corpus / "formal").glob("*_properties.sv")):
        jobs.append(_job(f"cc/formal/{f.name}", f, *common, "-D", "FORMAL", "--top", f.stem))
    for f in sorted((corpus / "src").glob("*.sv")):
        text = f.read_text(encoding="utf-8", errors="replace")
        top = _first_module(f)
        if top is not None and (re.search(r"`(ASSERT|ASSUME|COVER)", text) or _has_sva(f)):
            jobs.append(_job(f"cc/src/{f.name}", f, *common, "--top", top))
    for f in sorted((corpus / "test").glob("*.sv")):
        if _has_sva(f):
            jobs.append(_job(f"cc/test/{f.name}", f, *common, "--top", _first_module(f) or f.stem))
    return jobs


def _plan_opentitan(corpus: Path, scratch: Path) -> list[Job]:
    ip = corpus / "hw" / "ip"
    libs: list[object] = []
    for directory in sorted(ip.glob("*/rtl")):
        libs += ["-y", directory]
    libs += ["-I", ip / "prim" / "rtl"]
    jobs: list[Job] = []
    for f in sorted(ip.glob("*/rtl/*.sv")):
        if f.name.startswith("prim_assert"):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        top = _first_module(f)
        if top is not None and (re.search(r"`ASSERT|`COVER|`ASSUME", text) or _has_sva(f)):
            jobs.append(_job(f"ot/{f.parent.parent.name}/{f.name}", f, *libs, "--top", top))
    return jobs


def _plan_wb2axip(corpus: Path, scratch: Path) -> list[Job]:
    libs = [
        "-I",
        corpus / "rtl",
        "-I",
        corpus / "bench" / "formal",
        "-D",
        "FORMAL",
        "-y",
        corpus / "rtl",
        "-y",
        corpus / "bench" / "formal",
        "-y",
        corpus / "bench" / "rtl",
    ]
    jobs: list[Job] = []
    for sub in ("rtl", "bench/formal", "bench/rtl", "bench/mcy/easyaxil"):
        for f in sorted((corpus / sub).glob("*")):
            if f.suffix in (".v", ".sv") and _has_sva(f) and (top := _first_module(f)):
                jobs.append(_job(f"wb2axip/{sub}/{f.name}", f, *libs, "--top", top))
    return jobs


def _safe_member(name: str) -> bool:
    """Whether an archive member name stays inside the extraction directory: no absolute path,
    drive or root, and no ``..`` component, under either path syntax."""
    posix, windows = PurePosixPath(name), PureWindowsPath(name)
    return not (
        posix.is_absolute()
        or windows.anchor
        or ".." in posix.parts
        or ".." in windows.parts
        or ":" in name
        or not name
    )


def _defines_property_module(text: str, design: str) -> bool:
    return re.search(rf"^\s*module\s+i_{re.escape(design)}\b", text, re.M) is not None


def _extract_property(archive: Path, design: str, scratch: Path) -> Path | None:
    """The ``.sva`` member of ``archive`` that defines ``module i_<design>``, extracted into
    ``scratch``; a member with an absolute path or a ``..`` component is never extracted."""
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.endswith(".sva"):
                continue
            if not _safe_member(member.name):
                continue
            data = tar.extractfile(member)
            if data is None:
                continue
            content = data.read()
            if _defines_property_module(content.decode("utf-8", errors="replace"), design):
                scratch.mkdir(parents=True, exist_ok=True)
                target = scratch / "property.sva"  # a fixed name: the member's never reaches a path
                if not target.resolve().is_relative_to(scratch.resolve()):
                    return None
                target.write_bytes(content)
                return target
    return None


def _property_module(folder: Path, design: str, scratch: Path) -> Path | None:
    """The property module of an AssertionBench design folder (see the module contract)."""
    candidates = [
        f
        for f in sorted(folder.glob("*.sva"))
        if f.name != "bindings.sva"
        and _defines_property_module(f.read_text(encoding="utf-8", errors="replace"), design)
    ]
    if candidates:
        return next((f for f in candidates if f.name == "property.sva"), candidates[0])
    archive = folder / "property.sva.tar.gz"
    return _extract_property(archive, design, scratch) if archive.is_file() else None


def _included_names(sources: Sequence[Path]) -> set[str]:
    """The base names that the sources include (the file names of their include directives)."""
    names: set[str] = set()
    for source in sources:
        text = source.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"//[^\n]*", "", text)
        names.update(
            PurePosixPath(m.replace("\\", "/")).name
            for m in re.findall(r'`include\s+"([^"]+)"', text)
        )
    return names


_AB_TOO_LARGE = {
    "MAC_rx_FF": "144261 mined assertions in one job: not finished in 25 minutes",
}
"""AssertionBench designs left out of the survey, with the reason: a job that cannot finish
within the survey's time budget would only ever be a timeout."""


def _plan_assertionbench(corpus: Path, scratch: Path) -> list[Job]:
    jobs: list[Job] = []
    for bindings in sorted((corpus / "verified_assertions").glob("*/*/bindings.sva")):
        folder = bindings.parent
        design = folder.name
        group = folder.parent.name
        if design in _AB_TOO_LARGE:
            continue
        prop = _property_module(folder, design, scratch / group / design)
        if prop is None:
            continue
        sources = sorted(folder.glob("*.v"))
        included = _included_names(sources)
        rtl: list[object] = ["-I", folder]
        for source in sources:
            if source.name not in included:
                rtl += ["-v", source]
        jobs.append(_job(f"ab/{group}/{design}", bindings, prop, *rtl, "--top", design))
    return jobs


RECIPES: tuple[Recipe, ...] = (
    Recipe("axi", "SVA-AXI4-FVIP", _plan_axi),
    Recipe("sv-tests", "sv-tests", _plan_sv_tests),
    Recipe("cc", "common_cells", _plan_common_cells),
    Recipe("ot", "opentitan", _plan_opentitan),
    Recipe("wb2axip", "wb2axip", _plan_wb2axip),
    # Some AssertionBench designs carry tens of thousands of mined assertions: a job takes
    # up to about two minutes, so the default limit made the result depend on machine load
    # (the one design that cannot finish at all is left out: _AB_TOO_LARGE).
    Recipe(
        "ab",
        "assertion_data_for_LLM",
        _plan_assertionbench,
        timeout=300,
        left_out=tuple(sorted(_AB_TOO_LARGE.items())),
    ),
)


# ---------------------------------------------------------------- running


def _run(args: Sequence[str], timeout: int) -> tuple[int | None, str, str, float]:
    """(exit status or ``None`` on timeout, stdout, stderr, seconds) of one sva2yosys run."""
    command = [sys.executable, "-m", "sva2yosys", *args]
    start = time.monotonic()
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, "", "", time.monotonic() - start
    return done.returncode, done.stdout, done.stderr, time.monotonic() - start


def _relative(location: str, root: Path) -> str:
    path = location.replace("\\", "/")
    prefix = str(root).replace("\\", "/").rstrip("/") + "/"
    if path.startswith(prefix):
        path = path[len(prefix) :]
    return re.sub(r":\d+:\d+$", "", path)


def _first_error(stderr: str) -> str:
    lines = [line for line in stderr.splitlines() if line.strip()]
    if any("uvm" in line.lower() for line in lines):
        return "error: UVM dependency"
    errors = [line for line in lines if re.search(r"(^|[\s:])error:", line)]
    return (errors or lines or ["no output"])[0]


def run_job(
    corpus: str, job: Job, root: Path, scratch: Path, timeout: int = _TIMEOUT
) -> tuple[list[Entry], RunRecord]:
    """Run one job (check mode, then ``-o`` for the monitor size; each run limited to
    ``timeout`` seconds) and record it."""
    status, stdout, stderr, seconds = _run(job.args, timeout)
    primary = _relative(job.args[0], root)
    parsed = parse_check_output(stdout)
    entries: list[Entry] = []
    for line in parsed:
        file = _relative(line.location, root) if line.location else primary
        category = normalise_reason(line.message) if line.status is not Status.OK else ""
        entries.append(
            Entry(corpus, job.label, file, line.name, line.status, category, line.slots, line.bits)
        )
    if not parsed and status != 0:
        reason = "timeout" if status is None else normalise_reason(_first_error(stderr))
        entries.append(
            Entry(corpus, job.label, primary, "(run)", Status.TOOL_ERROR, reason, None, None)
        )
    summary = _SUMMARY_LINE.search(stdout)
    if summary is not None:
        # exit status 1 exactly when the summary reports a rejection
        consistent = status == (1 if int(summary.group(1)) > 0 else 0)
    else:
        # no summary: only a diagnostic exit (status 1) without directive lines is complete
        consistent = status == 1 and not parsed
    completed = consistent and "Traceback (most recent call last)" not in stderr
    total: int | None = None
    largest: int | None = None
    size_failed = False
    if any(line.status is Status.OK for line in parsed):
        scratch.mkdir(parents=True, exist_ok=True)
        out = Path(tempfile.mkdtemp(prefix="monitors-", dir=scratch))
        code, _, _, _ = _run([*job.args, "--allow-design-sva", "-o", str(out)], timeout)
        monitors = out / _MONITORS_FILE
        if code == 0 and monitors.is_file():
            text = monitors.read_text(encoding="utf-8", errors="replace")
            sizes = parse_module_sizes(text)
            total, largest = len(text.encode("utf-8")), max(sizes, default=0)
        else:
            size_failed = True
        shutil.rmtree(out, ignore_errors=True)
    return entries, RunRecord(corpus, job.label, seconds, total, largest, completed, size_failed)


def run_corpus_jobs(
    corpus: str,
    jobs: Sequence[Job],
    root: Path,
    scratch: Path,
    timeout: int = _TIMEOUT,
    workers: int = 1,
) -> list[tuple[list[Entry], RunRecord]]:
    """``run_job`` for each of ``jobs`` on ``workers`` threads; the results are in the order
    of ``jobs`` whatever ``workers`` is (see *Parallel jobs* in the module contract)."""
    finished = 0
    lock = threading.Lock()

    def run(job: Job) -> tuple[list[Entry], RunRecord]:
        nonlocal finished
        result = run_job(corpus, job, root, scratch, timeout)
        with lock:
            finished += 1
            print(f"{corpus} {finished}/{len(jobs)} {job.label}", file=sys.stderr, flush=True)
        return result

    if workers == 1:
        return [run(job) for job in jobs]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, jobs))


def _write_jsonl(
    out: Path, corpus: str, entries: Sequence[Entry], runs: Sequence[RunRecord]
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "type": "directive",
                "corpus": e.corpus,
                "run": e.run,
                "file": e.file,
                "name": e.name,
                "status": e.status.value,
                "category": e.category,
                "slots": e.slots,
                "bits": e.bits,
            }
        )
        for e in entries
    ]
    lines += [
        json.dumps(
            {
                "type": "run",
                "corpus": r.corpus,
                "run": r.run,
                "seconds": round(r.seconds, 2),
                "monitor_bytes": r.monitor_bytes,
                "largest_module_bytes": r.largest_module_bytes,
            }
        )
        for r in runs
    ]
    (out / f"{corpus}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _positive(text: str) -> int:
    """An ``argparse`` type: an integer of at least 1."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{value} is below 1")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Command line (see the module docstring); exit status 1 iff a baseline comparison
    fails."""
    parser = argparse.ArgumentParser(
        prog="survey.py", description="Acceptance, size and time survey over the SVA corpora."
    )
    parser.add_argument("root", type=Path, help="directory holding the corpus checkouts")
    parser.add_argument("corpus", nargs="*", help=f"one of {[r.name for r in RECIPES]}")
    parser.add_argument("--out", type=Path, help="write one jsonl file per corpus here")
    parser.add_argument(
        "--baseline",
        nargs="?",
        const=_DEFAULT_BASELINE,
        type=Path,
        help=f"compare with this baseline file (default {_DEFAULT_BASELINE.name})",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="merge this run into the baseline file (--baseline FILE, or the default)",
    )
    parser.add_argument(
        "--jobs",
        type=_positive,
        default=1,
        metavar="N",
        help="run N jobs of a corpus at the same time (default 1; results do not depend on N)",
    )
    arguments = parser.parse_args(argv)
    known = {recipe.name: recipe for recipe in RECIPES}
    unknown = [name for name in arguments.corpus if name not in known]
    if unknown:
        parser.error(f"unknown corpus {unknown}; known: {sorted(known)}")
    root = arguments.root.resolve()
    if arguments.corpus:
        chosen = [known[n] for n in arguments.corpus]
    else:
        chosen = [r for r in RECIPES if (root / r.directory).is_dir()]
    if not chosen:
        parser.error(f"no corpus to survey: none of the checkouts exists under {root}")
    summaries: dict[str, Summary] = {}
    all_entries: list[Entry] = []
    scratch = Path(tempfile.mkdtemp(prefix="sva2yosys-survey-"))
    try:
        for recipe in chosen:
            corpus_dir = root / recipe.directory
            if not corpus_dir.is_dir():
                parser.error(f"{corpus_dir} does not exist")
            entries: list[Entry] = []
            runs: list[RunRecord] = []
            planned = recipe.plan(corpus_dir, scratch)
            for job_entries, record in run_corpus_jobs(
                recipe.name, planned, root, scratch, recipe.timeout, arguments.jobs
            ):
                entries.extend(job_entries)
                runs.append(record)
            summaries[recipe.name] = summarise(entries, runs)
            all_entries.extend(entries)
            if arguments.out is not None:
                _write_jsonl(arguments.out, recipe.name, entries, runs)
            print(_format_summary(recipe.name, len(runs), summaries[recipe.name]), flush=True)
            for design, reason in recipe.left_out:
                print(f"{recipe.name}: left out {design}: {reason}", flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    path: Path = arguments.baseline if arguments.baseline is not None else _DEFAULT_BASELINE
    directives_path = _DEFAULT_DIRECTIVES if path == _DEFAULT_BASELINE else path.with_suffix(".tsv")
    rows = directive_rows(directive_entries(all_entries))
    if arguments.update_baseline:
        save_baseline(path, summaries)
        save_directives(directives_path, rows, summaries)
        print(f"baseline updated: {path} and {directives_path}")
        return 0
    if arguments.baseline is not None:
        report = compare_baselines(
            load_baseline(path), summaries, Tolerance(), require_all=not arguments.corpus
        )
        for notice in report.notices:
            print(f"notice: {notice}")
        for failure in report.failures:
            print(f"DIFFERENT: {failure}")
        differences = len(report.failures)
        if directives_path.exists():
            changes = compare_directives(
                load_directives(directives_path),
                rows,
                [name for name in summaries if name not in AGGREGATE_ONLY],
            )
            for change in changes:
                print(f"DIRECTIVE: {change}")
            differences += len(changes)
        else:
            print(f"DIFFERENT: {directives_path} is missing (run with --update-baseline)")
            differences += 1
        print("baseline: " + ("DIFFERENT" if differences else "same"))
        return 1 if differences else 0
    return 0


def _format_summary(name: str, runs: int, summary: Summary) -> str:
    counts = ", ".join(f"{k} {v}" for k, v in summary.counts.items())
    return (
        f"{name}: {runs} jobs, {counts}; monitors {summary.total_bytes} bytes "
        f"(largest module {summary.max_module_bytes}); {summary.seconds} s"
    )


if __name__ == "__main__":
    raise SystemExit(main())
