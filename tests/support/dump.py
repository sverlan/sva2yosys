"""Test support: dump every output the product generates from the checked-in fixtures.

A refactoring check for changes that claim no behaviour change: dump before the change, dump
after, compare. The dumps are never committed and no test compares against one, so the rule
"tests check behaviour, not text" is unchanged::

    uv run python tests/support/dump.py <outdir>
    uv run python tests/support/dump.py --compare <dirA> <dirB>

Contract
--------
* ``dump_all`` writes only below ``out`` (it first empties ``out``; scratch files of the CLI
  runs live in ``out/.work``, removed before it returns). The content is deterministic: two
  dumps of the same tree are byte-identical (paths of scratch files are replaced by
  ``<WORK>``; files are UTF-8 with ``\\n`` line ends).
* The dump holds, per section: ``trace/`` (every case of every trace table: the monitors of
  every machine kind and every form, and the reference evaluator's results), ``expressions/``
  (each line of the expression table), ``sv/`` (every ``.sv`` fixture of the functional
  tests), ``cli/`` (the command-line outputs: stdout, stderr, exit status and written files
  of the CLI and rewrite fixtures) and ``COUNTS.txt``. A construct the product rejects or a
  stage that raises is dumped as its error text, so a change of rejection shows up too.
* ``limit`` caps the number of items per section (a deterministic prefix); ``sections``
  selects sections; both exist for quick checks.
* ``compare_dirs`` lists, for two dump trees, the files only in the second (added), only in
  the first (removed) and different (changed, with the first differing line). Comparison is
  by bytes.
* ``compare_dirs`` requires two existing directories (``main`` prints an error and
  returns 2 otherwise). Case and expression names become file names through ``safe_name``
  (no separators or ``..``), and no write may leave ``out`` or repeat a path
  (case-insensitively); both raise ``ValueError``.
* ``main`` returns 0 for a dump, and for ``--compare`` 0 iff the trees are identical.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import random
import re
import shutil
import sys
import traceback
from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

if __package__ in (None, ""):  # run as a script: make the `support` package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support.reference.evaluate import evaluate, evaluate_attempts, finalize_attempts
from support.reference.lasso import Lasso, lasso_attempt_results
from support.reference.trace import Trace
from support.tracefile import TraceCase, load_cases, sv_source
from sva2yosys.cli import main as cli_main
from sva2yosys.codegen.core import AnyMachine
from sva2yosys.codegen.systemverilog import monitor_module, monitor_port_types, monitor_ports
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.optimize.forms import Form
from sva2yosys.slang.convert import Conversion, convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import SlangOptions
from sva2yosys.temporal.compile import (
    compile_attempt_machine,
    compile_configuration_machine,
    compile_directive,
    compile_monitors,
)

SECTIONS: tuple[str, ...] = ("trace", "expressions", "sv", "cli")

_MODULE = "tc"
_MONITOR = "__sva2yosys_p0"
_FUNCTIONAL = Path(__file__).resolve().parents[1] / "functional_tests"
_STRATEGIES = ("direct", "auto", "window", "history")
_EXPRESSION_SIGNALS = {"a": 1, "b": 1, "u": 4, "v": 4, "w": 8, "s": 4, "z": 8}
_SIGNED = frozenset({"s", "z"})
_COUNT_KEYS = ("files", "trace cases", "monitors", "cli runs", "expressions")
_DIFF_WIDTH = 100


@dataclass(frozen=True)
class Difference:
    """One changed file: its relative path and a description of the first difference."""

    path: str
    first: str


@dataclass(frozen=True)
class Comparison:
    """Result of comparing two dump trees (paths relative, POSIX separators, sorted)."""

    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[Difference, ...]

    @property
    def identical(self) -> bool:
        return not (self.added or self.removed or self.changed)


def dump_all(
    out: Path, *, sections: Collection[str] = SECTIONS, limit: int | None = None
) -> dict[str, int]:
    """Write the dump below ``out``; return the counts (also written to ``COUNTS.txt``)."""
    unknown = set(sections) - set(SECTIONS)
    if unknown:
        raise ValueError(f"unknown section(s) {sorted(unknown)}; known: {list(SECTIONS)}")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    random.seed(0)
    dumper = _Dumper(out, limit)
    try:
        if "trace" in sections:
            dumper.traces()
        if "expressions" in sections:
            dumper.expressions()
        if "sv" in sections:
            dumper.sv_fixtures()
        if "cli" in sections:
            dumper.cli()
    finally:
        shutil.rmtree(dumper.work, ignore_errors=True)
    dumper.write("COUNTS.txt", "".join(f"{k}: {v}\n" for k, v in dumper.counts.items()))
    return dict(dumper.counts)


def compare_dirs(first: Path, second: Path) -> Comparison:
    """Compare two dump trees (``NotADirectoryError`` unless both are directories)."""
    for root in (first, second):
        if not root.is_dir():
            raise NotADirectoryError(f"{root} is not an existing directory")
    names_a, names_b = _files(first), _files(second)
    changed: list[Difference] = []
    for name in sorted(names_a & names_b):
        bytes_a, bytes_b = (first / name).read_bytes(), (second / name).read_bytes()
        if bytes_a != bytes_b:
            changed.append(Difference(name, _first_difference(bytes_a, bytes_b)))
    return Comparison(
        added=tuple(sorted(names_b - names_a)),
        removed=tuple(sorted(names_a - names_b)),
        changed=tuple(changed),
    )


def format_comparison(comparison: Comparison, cap: int = 40) -> str:
    """A report: one line per added, removed and changed file (at most ``cap`` of each
    kind, then a count of the rest), and a final summary line."""
    lines: list[str] = []
    groups: tuple[tuple[str, Sequence[str]], ...] = (
        ("added", comparison.added),
        ("removed", comparison.removed),
        ("changed", [f"{d.path}: {d.first}" for d in comparison.changed]),
    )
    for label, items in groups:
        lines.extend(f"{label}: {item}" for item in items[:cap])
        if len(items) > cap:
            lines.append(f"{label}: ... {len(items) - cap} more")
    if comparison.identical:
        lines.append("identical")
    else:
        lines.append(
            f"{len(comparison.added)} added, {len(comparison.removed)} removed, "
            f"{len(comparison.changed)} changed"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Command line: ``OUTDIR`` or ``--compare DIR_A DIR_B``."""
    parser = argparse.ArgumentParser(
        prog="dump.py",
        description="Dump the generated outputs of the fixtures, or compare two dumps.",
    )
    parser.add_argument("outdir", nargs="?", help="directory to (re)create with the dump")
    parser.add_argument("--compare", nargs=2, metavar=("DIR_A", "DIR_B"))
    arguments = parser.parse_args(argv)
    if (arguments.outdir is None) == (arguments.compare is None):
        parser.error("give OUTDIR, or --compare DIR_A DIR_B")
    if arguments.compare is not None:
        try:
            comparison = compare_dirs(Path(arguments.compare[0]), Path(arguments.compare[1]))
        except NotADirectoryError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(format_comparison(comparison), end="")
        return 0 if comparison.identical else 1
    print(dump_all(Path(arguments.outdir).resolve()))
    return 0


def safe_name(text: str) -> str:
    """``text`` as one file-name component. Every character outside ``[A-Za-z0-9_.-]`` becomes
    ``%`` and six hex digits of its code point; ``%`` itself is such a character, so the
    encoding is injective. A result made of dots only gets its dots encoded too."""
    encoded = re.sub(r"[^A-Za-z0-9_.-]", lambda m: f"%{ord(m.group()):06X}", text)
    if encoded and set(encoded) == {"."}:
        encoded = encoded.replace(".", "%00002E")
    return encoded


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def _first_difference(a: bytes, b: bytes) -> str:
    lines_a = a.decode("utf-8", errors="replace").split("\n")
    lines_b = b.decode("utf-8", errors="replace").split("\n")
    for number, (x, y) in enumerate(zip(lines_a, lines_b, strict=False), start=1):
        if x != y:
            column = next((i for i, (c, d) in enumerate(zip(x, y, strict=False)) if c != d), 0)
            start = max(0, column - _DIFF_WIDTH // 4)
            stop = start + _DIFF_WIDTH
            return f"line {number} col {column + 1}: {x[start:stop]!r} -> {y[start:stop]!r}"
    return f"line {min(len(lines_a), len(lines_b))}: one file ends early"


def _attempt(fn: Callable[[], object]) -> object:
    """The result of ``fn()``, or the exception type and message as text."""
    try:
        return fn()
    except Exception as error:
        return f"<{type(error).__name__}: {error}>"


def _reference_lines(directive: Directive, trace: Trace, loop: int | None) -> list[str]:
    """The reference evaluator's results for one directive on one trace."""
    prop, disable = directive.property, directive.disable
    cover = directive.kind is DirectiveKind.COVER
    lines = [
        f"evaluate: {_attempt(partial(evaluate, prop, trace, disable))!r}",
        f"evaluate_attempts: {_attempt(partial(evaluate_attempts, prop, trace, disable))!r}",
        f"finalize_attempts: {_attempt(partial(finalize_attempts, prop, trace, disable, cover))!r}",
    ]
    if loop is not None:
        lasso = Lasso(trace.samples, loop)
        lines.append(
            f"lasso: {_attempt(partial(lasso_attempt_results, prop, lasso, disable, cover))!r}"
        )
    return lines


def _expression_source(condition: str) -> str:
    ports = ["input logic clk"]
    for name, width in _EXPRESSION_SIGNALS.items():
        signed = " signed" if name in _SIGNED else ""
        ports.append(f"input logic{signed} [{width - 1}:0] {name}")
    return (
        f"module {_MODULE}({', '.join(ports)});\n"
        f"  p: assert property (@(posedge clk) {condition});\n"
        "endmodule\n"
    )


class _Dumper:
    def __init__(self, out: Path, limit: int | None) -> None:
        self.out = out
        self.limit = limit
        self.work = out / ".work"
        self.counts = dict.fromkeys(_COUNT_KEYS, 0)
        self.written: set[str] = set()

    def write(self, name: str, text: str) -> None:
        path = self.out / name
        if not path.resolve().is_relative_to(self.out.resolve()):
            raise ValueError(f"{name!r} would be written outside {self.out}")
        folded = name.casefold()
        if folded in self.written:
            raise ValueError(f"{name!r} would be written twice (or collides by case)")
        self.written.add(folded)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        self.counts["files"] += 1

    def capped[T](self, items: Sequence[T]) -> Sequence[T]:
        return items if self.limit is None else items[: self.limit]

    def conversion(self, name: str, source: str) -> Conversion:
        return convert(export_texts([(f"{name}.sv", source)], SlangOptions(top=(_MODULE,))))

    def monitors_text(self, directive: Directive) -> str:
        parts: list[str] = []
        builders: tuple[tuple[str, Callable[[], Sequence[AnyMachine]]], ...] = (
            ("compile_monitors", lambda: compile_monitors(directive)),
            ("compile_directive", lambda: (compile_directive(directive),)),
            ("compile_attempt_machine", lambda: (compile_attempt_machine(directive),)),
            (
                "compile_configuration_machine",
                lambda: (compile_configuration_machine(directive),),
            ),
        )
        for label, build in builders:
            parts.append(f"## {label}")
            try:
                machines = build()
            except Exception as error:
                parts.append(f"<{type(error).__name__}: {error}>")
                continue
            for index, machine in enumerate(machines):
                parts.append(f"# machine {index}: {type(machine).__name__}")
                parts.append(f"ports: {_attempt(partial(monitor_ports, machine))!r}")
                parts.append(f"types: {_attempt(partial(monitor_port_types, machine))!r}")
                for form in Form:
                    self.counts["monitors"] += 1
                    parts.append(f"### form {form.value}")
                    parts.append(str(_attempt(partial(monitor_module, machine, _MONITOR, form))))
        return "\n".join(parts) + "\n"

    def directives_text(self, conversion: Conversion) -> str:
        text = f"rejections: {conversion.rejections!r}\n"
        for index, directive in enumerate(conversion.directives):
            text += f"==== directive {index}\n" + self.monitors_text(directive)
        return text

    def traces(self) -> None:
        tables = sorted((_FUNCTIONAL / "reference_semantics" / "fixtures").glob("*.trace"))
        cases = [case for table in tables for case in load_cases(table)]
        for case in self.capped(cases):
            self.trace_case(case)

    def trace_case(self, case: TraceCase) -> None:
        self.counts["trace cases"] += 1
        conversion = self.conversion(case.name, sv_source(case, _MODULE))
        trace = Trace(
            tuple({f"{_MODULE}.{name}": v for name, v in row.items()} for row in case.samples)
        )
        reference: list[str] = []
        for index, directive in enumerate(conversion.directives):
            reference.append(f"==== directive {index}")
            reference.extend(_reference_lines(directive, trace, case.loop))
        key = safe_name(f"{Path(case.file).stem}__{case.name}")
        self.write(f"trace/{key}.monitors.txt", self.directives_text(conversion))
        self.write(f"trace/{key}.reference.txt", "\n".join(reference) + "\n")

    def expressions(self) -> None:
        path = _FUNCTIONAL / "generated_monitors" / "fixtures" / "expressions.txt"
        entries: list[tuple[int, str, str]] = []
        for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if text.strip() and not text.startswith("#"):
                name, _, condition = text.partition(":")
                entries.append((number, name.strip(), condition.strip()))
        for number, name, condition in self.capped(entries):
            self.counts["expressions"] += 1
            conversion = self.conversion(name, _expression_source(condition))
            self.write(
                f"expressions/{number:03d}_{safe_name(name)}.txt", self.directives_text(conversion)
            )

    def sv_fixtures(self) -> None:
        for path in self.capped(sorted(_FUNCTIONAL.glob("*/fixtures/**/*.sv"))):
            rel = path.relative_to(_FUNCTIONAL).as_posix()
            text = path.read_text(encoding="utf-8")

            def run(rel: str = rel, text: str = text) -> str:
                return self.directives_text(convert(export_texts([(rel, text)], SlangOptions())))

            self.write(f"sv/{rel.replace('/', '__')}.txt", str(_attempt(run)) + "\n")

    def cli_run(self, label: str, args: list[str], output_dir: bool) -> None:
        self.counts["cli runs"] += 1
        if self.work.exists():
            shutil.rmtree(self.work)
        self.work.mkdir(parents=True)
        argv = [*args, "-o", str(self.work / "out")] if output_dir else list(args)
        stdout, stderr = io.StringIO(), io.StringIO()
        status: object
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                status = cli_main(argv)
            except SystemExit as error:
                status = f"SystemExit({error.code})"
            except Exception:
                status = "EXC " + traceback.format_exc()
        body = (
            f"argv: {[self.scrub(a) for a in argv]!r}\nstatus: {status!r}\n"
            f"--- stdout\n{stdout.getvalue()}--- stderr\n{stderr.getvalue()}"
        )
        self.write(f"cli/{label}.txt", self.scrub(body))
        produced = self.work / "out"
        if output_dir and produced.exists():
            for file in sorted(produced.rglob("*")):
                if file.is_file():
                    content = file.read_text(encoding="utf-8", errors="replace")
                    name = file.relative_to(produced).as_posix()
                    self.write(f"cli/{label}.files/{name}", self.scrub(content))

    def scrub(self, text: str) -> str:
        work = str(self.work)
        return text.replace(work.replace("\\", "/"), "<WORK>").replace(work, "<WORK>")

    def cli_plan(self) -> Iterator[tuple[str, list[str], bool]]:
        fx = _FUNCTIONAL / "cli" / "fixtures"
        singles: list[tuple[str, list[str]]] = [
            ("accepted", [str(fx / "accepted.sv")]),
            ("mixed_good", [str(fx / "mixed.sv"), "--top", "mixed", "-D", "WITH_GOOD"]),
            ("mixed", [str(fx / "mixed.sv"), "--top", "mixed"]),
            ("broken", [str(fx / "broken.sv")]),
            ("xchecks_skip", [str(fx / "xchecks.sv")]),
            ("xchecks_reject", [str(fx / "xchecks.sv"), "--x-checks", "reject"]),
            ("xchecks_past", [str(fx / "xchecks_past.sv")]),
            ("xchecks_past_reject", [str(fx / "xchecks_past.sv"), "--x-checks", "reject"]),
            (
                "preproc",
                [
                    str(fx / "preproc" / "verify" / "counter_props.sv"),
                    "-I",
                    str(fx / "preproc" / "verify"),
                ],
            ),
            ("help", ["--help"]),
        ]
        for label, args in singles:
            yield label, args, False
            if label in ("accepted", "mixed_good", "xchecks_skip", "xchecks_past"):
                for strategy in _STRATEGIES:
                    yield f"{label}_o_{strategy}", [*args, "--strategy", strategy], True
        rw = _FUNCTIONAL / "rewrite" / "fixtures"
        for case in ("counter", "pipe"):
            base = [str(p) for p in sorted((rw / case / "verify").glob("*.sv"))]
            for design in sorted((rw / case / "design").glob("*.sv")):
                base += ["-v", str(design)]
            base += ["--top", case, "--allow-design-sva"]
            yield f"rw_{case}", base, False
            for strategy in _STRATEGIES:
                for equivalence in (False, True):
                    extra = ["--equivalence"] if equivalence else []
                    label = f"rw_{case}_{strategy}{'_eq' if equivalence else ''}"
                    yield label, [*base, "--strategy", strategy, *extra], True
        verify = sorted((rw / "counter" / "verify").glob("*.sv"))
        mutant = sorted((rw / "counter" / "mutant").glob("*.sv"))
        yield (
            "rw_counter_mutant",
            [*map(str, verify), "-v", str(mutant[0]), "--top", "counter"],
            True,
        )
        x_verify = sorted((rw / "xchecks" / "verify").glob("*.sv"))
        counter_design = sorted((rw / "counter" / "design").glob("*.sv"))
        for policy in ("skip", "reject"):
            yield (
                f"rw_xchecks_{policy}",
                [
                    *map(str, x_verify),
                    "-v",
                    str(counter_design[0]),
                    "--top",
                    "counter",
                    "--x-checks",
                    policy,
                ],
                True,
            )
        formal = _FUNCTIONAL / "formal_designs" / "fixtures"
        for name, top in (("delays", "top"), ("pipe3", "pipe3")):
            args = [str(p) for p in sorted((formal / name / "props").glob("*.sv"))]
            for rtl in sorted((formal / name / "rtl").glob("*.sv")):
                args += ["-v", str(rtl)]
            args += ["--top", top]
            for strategy in ("direct", "auto"):
                yield f"fd_{name}_{strategy}", [*args, "--strategy", strategy], True

    def cli(self) -> None:
        for label, args, output_dir in self.capped(list(self.cli_plan())):
            self.cli_run(label, args, output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
