"""Test support: generate ``sva-ieee-corpus/`` from the hand-written trace tables.

    uv run python tests/support/trace_suite.py --write     # regenerate the committed corpus
    uv run python tests/support/trace_suite.py --check     # exit 1 and list differences

The contract is ``tests/functional_tests/sva_ieee_corpus/CONTRACT.md``. The trace tables are
canonical; the corpus is generated and never edited by hand.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

if __package__ in (None, ""):  # run as a script: make the `support` package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support import trace_driver
from support.ebmc_differential import load_all_cases, traces_digest
from support.tracefile import TraceCase

CORPUS_DIRECTORY = Path(__file__).resolve().parents[2] / "sva-ieee-corpus"
GENERATOR = "tests/support/trace_suite.py"
RESERVED = frozenset(
    {
        "clk",
        "tick",
        "tick_prev",
        "phase",
        "phase_prev",
        "row_idx",
        "row_idx_prev",
        "hexd",
        "done",
        "tb",
        "sva_case",
        "p",
        "sample_idx",
    }
)
HAND_WRITTEN = frozenset({"README.md", "LICENSE"})
GENERATED_ROOTS = ("sim", "formal", "traces/csv", "traces/wavedrom")
GENERATED_EXTENSIONS = frozenset({".sv", ".csv", ".json"})


class Category(Enum):
    """The folder of a case: one per case, by priority initial, cover, assume, live, bounded."""

    INITIAL = "initial"
    COVER = "cover"
    ASSUME = "assume"
    LIVE = "live"
    BOUNDED = "bounded"


EXPORTED_CATEGORIES = frozenset(Category)  # every case is exported (contract point 4)


class Stimulus(Enum):
    """How a file writes the input trace. The committed corpus is ``STRING`` only; ``TERNARY``
    (a chain of conditional expressions, the style of the EBMC differential design) is built on
    demand for a tool that cannot read the string rows."""

    STRING = "string"
    TERNARY = "ternary"


# The committed corpus writes a case of this many samples or more in the ternary form (contract
# point 25): the string form of such a trace is a constant of 500 bits or more read with a
# variable index, which ``read_verilog`` turns into a netlist that SBY's own engine cannot unroll.
TERNARY_FROM = 30


def stimulus_of(case: TraceCase, stimulus: Stimulus, ternary_from: int | None) -> Stimulus:
    """The form of ``case``: ternary when ``stimulus`` is ``TERNARY`` or the case has
    ``ternary_from`` samples or more, else ``stimulus``."""
    if ternary_from is not None and len(case.samples) >= ternary_from:
        return Stimulus.TERNARY
    return stimulus


class SampleIndex(Enum):
    """How the simulation form gets the sample number it prints. ``SAMPLED`` uses
    ``$sampled(tick)`` (IEEE 1800-2017 16.9.3: the Preponed value, so the number of the sample
    the assertion evaluated). ``REGISTER`` copies ``tick`` at each falling edge, for a simulator
    without ``$sampled`` in action blocks; it is built on demand only."""

    SAMPLED = "sampled"
    REGISTER = "register"


@dataclass(frozen=True)
class Attempts:
    """Per-attempt results of a table (contract point 31): ``outcomes`` is one of ``T t F f U X p
    d`` per start sample; ``decided`` the decision sample per start sample (None while pending)."""

    outcomes: str | None
    decided: tuple[int | None, ...] | None
    pending: int | None
    final_fail: int | None


@dataclass(frozen=True)
class Infinite:
    """Outcomes of a lasso case on the infinite trace (contract point 31): ``outcomes`` is one of
    ``T F D`` per start sample; ``decided`` the sample at which the attempt is decided online
    (None when no finite prefix decides it)."""

    outcomes: str
    decided: tuple[int | None, ...] | None


@dataclass(frozen=True)
class CaseEntry:
    """One manifest entry: the case id, the expectations of the table and its metadata."""

    case_id: str
    category: Category
    kind: str
    scope: str
    samples: int
    failures: tuple[int, ...]
    successes: tuple[int, ...]
    case_class: str
    interpretations: tuple[str, ...]
    clauses: tuple[str, ...]
    support_oracle: bool
    exported: bool
    loop: int | None = None
    sim: bool = True
    attempts: Attempts | None = None
    infinite: Infinite | None = None


def _stem(case: TraceCase) -> str:
    return Path(case.file).stem


def case_id(case: TraceCase) -> str:
    """``<trace file stem>/<case name>``."""
    return f"{_stem(case)}/{case.name}"


def category_of(case: TraceCase) -> Category:
    """The category of ``case`` (contract point 5)."""
    if case.procedure is not None:
        return Category.INITIAL
    if case.kind == "cover":
        return Category.COVER
    if case.kind == "assume":
        return Category.ASSUME
    if case.loop is not None:
        return Category.LIVE
    return Category.BOUNDED


def _scope(case: TraceCase) -> str:
    if case.first_failure_only:
        return "first-failure"
    return "attempt" if case.attempt_scope else "default"


def _attempts(case: TraceCase) -> Attempts | None:
    """Per-attempt and end-of-simulation results of the table (contract point 31). A directive in
    an ``initial`` procedure has one attempt, the one at sample 0: the table's later start
    positions are bookkeeping of the lowered form, not native attempts, and are left out."""
    if case.outcomes is None and case.pending is None and case.final_fail is None:
        return None
    outcomes = None if case.outcomes is None else "".join(case.outcomes)
    decided = case.decisions
    if case.procedure is not None:
        outcomes = None if outcomes is None else outcomes[:1]
        decided = None if decided is None else decided[:1]
    return Attempts(outcomes, decided, case.pending, case.final_fail)


def _infinite(case: TraceCase) -> Infinite | None:
    if case.inf_outcomes is None:
        return None
    return Infinite("".join(case.inf_outcomes), case.inf_decisions)


def entry_for(case: TraceCase) -> CaseEntry:
    """The manifest entry of ``case``; ``exported`` is false outside ``EXPORTED_CATEGORIES``."""
    category = category_of(case)
    return CaseEntry(
        case_id=case_id(case),
        category=category,
        kind=case.kind,
        scope=_scope(case),
        samples=len(case.samples),
        failures=tuple(sorted(case.fails)),
        successes=tuple(sorted(case.passes)),
        case_class=case.case_class.value,
        interpretations=case.interpretations,
        clauses=case.clause,
        support_oracle=case.oracle_only,
        exported=category in EXPORTED_CATEGORIES,
        loop=case.loop,
        sim=case.loop is None,
        attempts=_attempts(case),
        infinite=_infinite(case),
    )


def _check_names(case: TraceCase) -> None:
    for name in case.widths:
        if name in RESERVED or name.startswith("ROW_"):
            raise ValueError(f"{case_id(case)}: signal {name!r} clashes with a generated name")


def _header(case: TraceCase) -> list[str]:
    """The comment block of an SV file: provenance, expectation and the rows of the table."""
    lines = [
        f"// Generated by {GENERATOR} from {_stem(case)}.trace: do not edit.",
        f"// Case {case_id(case)}: {case.kind}, scope {_scope(case)}, "
        f"class {case.case_class.value}, IEEE 1800 clauses {' '.join(case.clause)}.",
        f"// Expected: failures at samples {sorted(case.fails)}, "
        f"non-vacuous successes at {sorted(case.passes)}, of {len(case.samples)} samples.",
        "// Sample k is the value sampled at the k-th posedge clk (from 0); only samples",
        "// 0 .. N-1 have expectations. Rows of the table (. = 0):",
    ]
    for name in case.widths:
        lines.append(f"//   {name} : {trace_driver.row_string(case, name)}")
    only = trace_driver.disable_only_signals(case)
    if only:
        lines.append(
            f"// Read only by the disable iff condition: {', '.join(only)}. The condition reads "
            "current values (IEEE 1800-2017 16.12),"
        )
        lines.append(
            "// so these signals show row k right after rising edge k, selected by the previous"
        )
        lines.append("// counter value; the other signals show row k just before the edge.")
    if case.loop is not None:
        count = len(case.samples)
        lines.append(
            f"// Infinite trace: samples 0 .. {count - 1}, then samples {case.loop} .. "
            f"{count - 1} repeat forever."
        )
    return lines


def _core(
    case: TraceCase, with_done: bool, stimulus: Stimulus, index: SampleIndex = SampleIndex.SAMPLED
) -> list[str]:
    _check_names(case)
    samples = len(case.samples)
    ports = "input logic clk, output logic done" if with_done else "input logic clk"
    lines = [f"module sva_case({ports});"]
    if case.loop is None:
        lines.extend(trace_driver.counter_lines(case))
        selector = "tick"
    else:  # a lasso: the phase runs 0 .. N-1 and jumps back to the loop sample
        lines.extend(trace_driver.phase_lines(case))
        selector = "phase"
    previous = None
    if trace_driver.disable_only_signals(case):  # read only by the disable iff condition
        previous = f"{selector}_prev"
        lines.extend(trace_driver.previous_lines(case, selector))
    if stimulus is Stimulus.STRING:
        lines.extend(trace_driver.string_stimulus_lines(case, selector, previous))
    else:
        lines.extend(trace_driver.stimulus_lines(case, selector, previous))
    if with_done:
        width = trace_driver.counter_width(samples)
        lines.append(f"  assign done = (tick == {width}'d{samples});")
    if with_done and index is SampleIndex.REGISTER:
        width = trace_driver.counter_width(samples)
        # changes on the falling edge only, so it is the sample number whatever the order in
        # which a simulator runs the update of tick and the action block
        lines.append(
            "  // sample number printed by the action blocks: tick copied at each falling edge"
        )
        lines.append(f"  logic [{width - 1}:0] sample_idx = 0;")
        lines.append("  always @(negedge clk) sample_idx <= tick;")
    return lines


def render_formal(case: TraceCase, stimulus: Stimulus = Stimulus.STRING) -> str:
    """Formal form: module ``sva_case`` with the property and the stimulus only."""
    lines = [
        *_header(case),
        *_core(case, with_done=False, stimulus=stimulus),
        f"  {trace_driver.directive_line(case, wrap_initial=True)}",
        "endmodule",
    ]
    return "\n".join(lines) + "\n"


def render_sim(
    case: TraceCase,
    stimulus: Stimulus = Stimulus.STRING,
    index: SampleIndex = SampleIndex.SAMPLED,
) -> str:
    """Simulation form: module ``sva_case`` (property, stimulus, logging, ``done``) and the top
    module ``tb`` in the same file."""
    if case.loop is not None:
        raise ValueError(f"{case_id(case)}: a lasso case has no simulation form")
    cid = case_id(case)
    disable = f"disable iff ({case.disable}) " if case.disable is not None else ""
    sample = "$sampled(tick)" if index is SampleIndex.SAMPLED else "sample_idx"
    start = "initial " if case.procedure is not None else ""
    head = f"  {start}p: {case.kind} property (@(posedge clk) {disable}{case.property})"
    if case.kind == "cover":
        actions = [f'    $display("SVA_COVER {cid} sample=%0d", {sample});']
    else:
        actions = [
            f'    $display("SVA_PASS {cid} sample=%0d", {sample});',
            f'    else $display("SVA_FAIL {cid} sample=%0d", {sample});',
        ]
    lines = [
        *_header(case),
        "// SVA_FAIL / SVA_PASS lines are the action blocks of the assertion at the decision",
        "// sample; pass actions also fire on vacuous success unless the simulator disables",
        "// them ($assertvacuousoff, IEEE 1800-2017 20.12). Run with tb as the top module.",
        *_core(case, with_done=True, stimulus=stimulus, index=index),
        head,
        *actions,
        "endmodule",
        "",
        "module tb;",
        "  logic clk = 0;",
        "  logic done;",
        "  sva_case dut(.clk(clk), .done(done));",
        "  always #5 clk = ~clk;",
        "  always @(posedge done) #1 $finish;",
        "endmodule",
    ]
    return "\n".join(lines) + "\n"


def render_csv(case: TraceCase) -> str:
    """``sample,<signals>,fail,pass`` with decimal values."""
    names = list(case.widths)
    lasso = case.loop is not None and case.inf_outcomes is not None
    extra = ["repeat", "outcome"] if lasso else []
    lines = [",".join(["sample", *names, "fail", "pass", *extra])]
    for k, row in enumerate(case.samples):
        values = [str(k), *(str(row[name]) for name in names)]
        values += [str(int(k in case.fails)), str(int(k in case.passes))]
        if case.loop is not None and case.inf_outcomes is not None:
            values += [str(int(k >= case.loop)), case.inf_outcomes[k]]
        lines.append(",".join(values))
    return "\n".join(lines) + "\n"


def _bit_wave(bits: list[int]) -> str:
    """A WaveDrom wave of a 1-bit lane: a value on its first cell and when it changes, ``.``
    (keep the previous state) otherwise. Writing an unchanged value again would draw a spike."""
    return "".join("." if k and bits[k] == bits[k - 1] else str(bit) for k, bit in enumerate(bits))


def _pulse(indices: frozenset[int], count: int) -> str:
    return _bit_wave([int(k in indices) for k in range(count)])


def render_wavedrom(case: TraceCase) -> str:
    """WaveDrom ``signal`` JSON of the same data (clock, signals, expected fail and pass).

    The textbook drawing: values change at the rising edge, the cell of sample ``k`` ends at
    the rising edge ``k`` that samples it, and the header labels (``tock``, in the middle of
    each cell) are the sample numbers. A signal read only by the ``disable iff`` condition is
    drawn one cell later, as the generated files present it: row ``k`` right after rising edge
    ``k``, where the condition reads the current value (IEEE 1800-2017 16.12), and not right
    before it. A wide signal that keeps its value is one box."""
    count = len(case.samples)
    # Sample k is drawn in cell k, which ends at the rising edge k (the value sampled there):
    # the clock has a low cell first and then one rising edge per cell, so the edges 0 .. N-2
    # are drawn and the last one, at the right border, is not.
    clock = "l" + ("p" + "." * (count - 2) if count > 1 else "")
    signal: list[dict[str, object]] = [{"name": "clk", "wave": clock}]
    late = trace_driver.disable_only_signals(case)
    for name, width in case.widths.items():
        values = [sample[name] for sample in case.samples]
        if name in late:  # row k after edge k: cell k shows row k-1 (row 0 until edge 1)
            values = [values[0], *values[:-1]]
        if width == 1:
            signal.append({"name": name, "wave": _bit_wave(values)})
        else:
            digits = (width + 3) // 4
            wave = "".join("." if k and values[k] == values[k - 1] else "=" for k in range(count))
            labels = [f"{v:0{digits}X}" for k, v in enumerate(values) if wave[k] == "="]
            signal.append({"name": name, "wave": wave, "data": labels})
    signal.append({"name": "fail", "wave": _pulse(case.fails, count)})
    signal.append({"name": "pass", "wave": _pulse(case.passes, count)})
    if case.loop is not None and case.inf_outcomes is not None:
        loop = case.loop
        signal.append(
            {
                "name": "repeat",
                "wave": _bit_wave([int(k >= loop) for k in range(count)]),
            }
        )
        outcomes = case.inf_outcomes
        wave = "".join("." if k and outcomes[k] == outcomes[k - 1] else "=" for k in range(count))
        labels = [outcomes[k] for k in range(count) if wave[k] == "="]
        signal.append({"name": "outcome", "wave": wave, "data": labels})
    head = {"text": case_id(case), "tock": 0}
    document: dict[str, object] = {"signal": signal, "head": head}
    if late:
        names = ", ".join(late)
        document["foot"] = {
            "text": f"{names}: read only by the disable iff condition, drawn right after the "
            "rising edge (current value)"
        }
    return json.dumps(document, indent=2) + "\n"


def _entry_json(entry: CaseEntry) -> str:
    return json.dumps(
        {
            "id": entry.case_id,
            "category": entry.category.value,
            "kind": entry.kind,
            "scope": entry.scope,
            "samples": entry.samples,
            "failures": list(entry.failures),
            "successes": list(entry.successes),
            "class": entry.case_class,
            "interpretations": list(entry.interpretations),
            "clauses": list(entry.clauses),
            "support_oracle": entry.support_oracle,
            "exported": entry.exported,
            "loop": entry.loop,
            "sim": entry.sim,
            "attempts": None
            if entry.attempts is None
            else {
                "outcomes": entry.attempts.outcomes,
                "decided": None if entry.attempts.decided is None else list(entry.attempts.decided),
                "pending": entry.attempts.pending,
                "final_fail": entry.attempts.final_fail,
            },
            "infinite": None
            if entry.infinite is None
            else {
                "outcomes": entry.infinite.outcomes,
                "decided": None if entry.infinite.decided is None else list(entry.infinite.decided),
            },
        }
    )


def render_manifest(
    entries: tuple[CaseEntry, ...],
    traces_sha256: str,
    stimulus: Stimulus = Stimulus.STRING,
    index: SampleIndex = SampleIndex.SAMPLED,
    ternary_from: int | None = TERNARY_FROM,
) -> str:
    """``manifest.json``: sorted by case id, one case per line, LF, UTF-8."""
    ordered = sorted(entries, key=lambda entry: entry.case_id)
    exported = sum(1 for entry in ordered if entry.exported)
    lines = [
        "{",
        f'  "generator": "{GENERATOR}: generated, do not edit",',
        f'  "traces-sha256": "{traces_sha256}",',
        f'  "stimulus": "{stimulus.value}",',
        f'  "ternary-from": {"null" if ternary_from is None else ternary_from},',
        f'  "sample-index": "{index.value}",',
        f'  "case-count": {len(ordered)},',
        f'  "exported-count": {exported},',
        '  "cases": [',
        ",\n".join(f"    {_entry_json(entry)}" for entry in ordered),
        "  ]",
        "}",
    ]
    return "\n".join(lines) + "\n"


def build_corpus(
    cases: tuple[TraceCase, ...],
    traces_sha256: str,
    stimulus: Stimulus = Stimulus.STRING,
    index: SampleIndex = SampleIndex.SAMPLED,
    ternary_from: int | None = TERNARY_FROM,
) -> dict[str, str]:
    """Every generated file as ``<form>/<category>/<stem>/<case>.<ext>`` (forms ``sim``,
    ``formal``, ``traces/csv``, ``traces/wavedrom``) and ``manifest.json`` -> text.
    README and LICENSE are not generated."""
    files: dict[str, str] = {}
    entries: list[CaseEntry] = []
    for case in cases:
        entry = entry_for(case)
        entries.append(entry)
        if not entry.exported:
            continue
        base = f"{entry.category.value}/{_stem(case)}/{case.name}"
        form = stimulus_of(case, stimulus, ternary_from)
        if case.loop is None:  # a lasso has no simulation form (contract point 30)
            files[f"sim/{base}.sv"] = render_sim(case, form, index)
        files[f"formal/{base}.sv"] = render_formal(case, form)
        files[f"traces/csv/{base}.csv"] = render_csv(case)
        files[f"traces/wavedrom/{base}.json"] = render_wavedrom(case)
    files["manifest.json"] = render_manifest(
        tuple(entries), traces_sha256, stimulus, index, ternary_from
    )
    return dict(sorted(files.items()))


def _built(
    stimulus: Stimulus = Stimulus.STRING,
    index: SampleIndex = SampleIndex.SAMPLED,
    ternary_from: int | None = TERNARY_FROM,
) -> dict[str, str]:
    return build_corpus(tuple(load_all_cases()), traces_digest(), stimulus, index, ternary_from)


def _is_generated(relative: str) -> bool:
    """Whether ``relative`` has the path of a generated file: ``manifest.json``, or
    ``<form root>/<category>/<stem>/<file>`` with a known extension. Nothing else below a corpus
    root is ever read, compared or deleted (the root may hold the user's own files)."""
    if relative == "manifest.json":
        return True
    categories = {category.value for category in Category}
    for form in GENERATED_ROOTS:
        if relative.startswith(form + "/"):
            parts = relative[len(form) + 1 :].split("/")
            suffix = Path(relative).suffix
            return len(parts) == 3 and parts[0] in categories and suffix in GENERATED_EXTENSIONS
    return False


def _present(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_file() and relative not in HAND_WRITTEN and _is_generated(relative):
                found[relative] = path.read_bytes()
    return found


def write_corpus(
    root: Path = CORPUS_DIRECTORY,
    stimulus: Stimulus = Stimulus.STRING,
    index: SampleIndex = SampleIndex.SAMPLED,
    ternary_from: int | None = TERNARY_FROM,
) -> None:
    """Write ``build_corpus`` below ``root``; remove generated files that are no longer built.
    The committed corpus uses the committed values only (string stimulus, ``$sampled`` index,
    the ``TERNARY_FROM`` threshold)."""
    default = (
        stimulus is Stimulus.STRING
        and index is SampleIndex.SAMPLED
        and ternary_from == TERNARY_FROM
    )
    if root.resolve() == CORPUS_DIRECTORY.resolve() and not default:
        raise ValueError("the committed corpus is written with the committed values only")
    built = _built(stimulus, index, ternary_from)
    for key in set(_present(root)) - set(built):
        (root / key).unlink()
    for key, text in built.items():
        target = root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    for form in GENERATED_ROOTS:  # empty folders of the generated roots only
        base = root / form
        if base.is_dir():
            for directory in sorted((p for p in base.rglob("*") if p.is_dir()), reverse=True):
                if not any(directory.iterdir()):
                    directory.rmdir()


def check_corpus(root: Path = CORPUS_DIRECTORY) -> list[str]:
    """Differences between ``build_corpus`` and the files below ``root``: missing, stale, extra."""
    built = _built()
    present = _present(root)
    problems = [f"missing: {key}" for key in built if key not in present]
    problems += [f"extra: {key}" for key in present if key not in built]
    problems += [
        f"changed: {key}"
        for key, text in built.items()
        if key in present and present[key] != text.encode("utf-8")
    ]
    return problems


def _threshold(text: str) -> int | None:
    """The argument of ``--ternary-from``: a positive integer, or ``never``."""
    if text == "never":
        return None
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a positive integer or 'never'") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text!r} is not a positive integer or 'never'")
    return value


def main(argv: list[str] | None = None) -> int:
    """Command line: ``--write`` or ``--check``."""
    parser = argparse.ArgumentParser(description="Generate or check sva-ieee-corpus/.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the committed corpus")
    mode.add_argument("--check", action="store_true", help="list differences, exit 1 if any")
    parser.add_argument("--root", type=Path, default=CORPUS_DIRECTORY)
    parser.add_argument(
        "--stimulus",
        choices=[kind.value for kind in Stimulus],
        default=Stimulus.STRING.value,
        help="how the trace is written; ternary is on demand, with --write and another --root",
    )
    parser.add_argument(
        "--ternary-from",
        type=_threshold,
        default=TERNARY_FROM,
        metavar="N|never",
        help=f"write the cases of N samples or more in the ternary form (default {TERNARY_FROM}, "
        "the committed value); never: only --stimulus decides; another value is on demand, "
        "with --write and another --root",
    )
    parser.add_argument(
        "--sample-index",
        choices=[kind.value for kind in SampleIndex],
        default=SampleIndex.SAMPLED.value,
        help="how the simulation form gets its sample number; register is on demand",
    )
    args = parser.parse_args(argv)
    stimulus = Stimulus(args.stimulus)
    index = SampleIndex(args.sample_index)
    if (
        stimulus is not Stimulus.STRING
        or index is not SampleIndex.SAMPLED
        or args.ternary_from != TERNARY_FROM
    ):
        if not args.write:
            parser.error("the alternative forms need --write (only the default form is checked)")
        if args.root.resolve() == CORPUS_DIRECTORY.resolve():
            parser.error(
                "the alternative forms need another --root: the committed corpus is default"
            )
    if args.write:
        write_corpus(args.root, stimulus, index, args.ternary_from)
        return 0
    problems = check_corpus(args.root)
    for problem in problems:
        print(problem)
    if problems:
        print("run: uv run python tests/support/trace_suite.py --write")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
