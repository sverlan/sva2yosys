"""Test support: the mutation campaign (validation layer 6, ``docs/validation.md``).

A regression tool, not a pytest test (a campaign takes minutes)::

    uv run --env-file .env python tests/support/mutants.py [--report <file.json>]
        [--workdir <dir>] [--jobs <n>] [--no-baseline] [--keep] [--canary] [--list]
        [--external-tools <sel>] [--external-corpora <sel>] [mutant-id ...]

It measures how many deliberate, plausible semantic bugs in the product the default test tier
catches. The tests are run through the ``--env-file`` environment so that the opt-in tools
(Yosys, SBY) are available, exactly as in the normal default run.

Contract
--------
* **Mutants.** ``MUTANTS`` is the one fixed, reviewed list. A :class:`Mutant` is a precise
  textual replacement in one product source file (``path`` relative to ``src/sva2yosys``,
  the exact ``old`` text, which must occur exactly once, and the ``new`` text), the semantic
  ``bug`` it models in one sentence, the ``clause`` of IEEE 1800 (or the repository's
  ``docs/semantics.md``) the bug violates, and the ``tests`` (pytest paths or node ids,
  default tier only) that should catch it. ``id`` values are unique and are directory names.
  A mutant never weakens to make it die: when a mutant survives, either a test is added
  that kills it, or ``equivalent`` records the argument that the mutant does not change the
  observable behaviour (a mutant with ``equivalent`` set is still run, and must survive).
* **Application.** :func:`apply` edits a copy of the source tree, never the working tree. It
  raises :class:`InvalidMutantError` when the file is missing, ``old`` equals ``new``, or ``old``
  does not occur exactly once.
* **Isolation.** :func:`run` copies ``src``, ``tests``, ``docs``, ``examples``,
  ``pyproject.toml``, ``uv.lock``, ``README.md`` and ``.env`` (no caches) into
  ``<workdir>/<id>``, applies the mutant there and runs pytest with that directory as its
  working directory. The repository is installed editable (its ``src`` is on the interpreter's
  path through a ``.pth`` file); ``PYTHONPATH=<copy>/src`` precedes it, so the copy's package
  is the one imported. :data:`CANARY` (a copy whose package cannot be imported) checks this: its
  run must be ``invalid``, naming the canary error. The working directory is never modified;
  the work directory must be outside the repository.
* **External resources.** The default tier needs the tools to be strong, and a test run
  selects external resources explicitly (``tests/support/selection.py``), so the campaign
  does too: :func:`pytest_command` always passes ``--external-tools=<sel>`` and
  ``--external-corpora=<sel>``, taken from ``--external-tools`` / ``--external-corpora``
  (default ``all`` for both, so the documented campaign keeps its strength; ``none`` or a
  list narrows it). A selected resource whose variable is missing makes pytest stop with a
  usage error (exit status 4); :func:`run_pytest` raises :class:`SetupError` for it, and the
  command line reports a *setup failure* (exit status 2, nothing printed as verdicts), never
  an ``invalid`` or ``killed`` mutant.
* **Outcome.** The command is ``python -m pytest -q -p no:cacheprovider
  --junitxml=junit.xml --ignore=tests/unit_tests/test_mutants.py --external-tools=<sel>
  --external-corpora=<sel> <tests>`` (no ``-x``: every failing test is recorded) with a
  600-second limit; on the limit the whole process tree is killed and reaped.
  :func:`classify_result` reads the exit status and the JUnit file:
  exit 0 is ``survived``; a test *failure* is a kill, but any *error* (collection or setup: a
  broken import, a fixture that raises), a missing or unreadable result file, or an exit
  other than 0 and 1 is ``invalid``, whatever else failed (the mutant then says nothing about
  the tests); the limit is ``timeout``. A timed-out mutant is not counted as killed.
* **Behavioural and snapshot kills.** A failing test listed in :data:`SNAPSHOT_TESTS` (an
  explicit list of ``file::function``) compares generated or exported text with a frozen copy
  (byte identity, golden JSON): it shows that the output changed, not that a behaviour is
  wrong. Tests that run a frozen monitor through Yosys/SBY are behavioural. A mutant failing
  only such tests is ``snapshot-only``; one failing at least one other test is ``killed`` (the
  behavioural kill, the detail naming the first such test).
* **Verdict.** :func:`verdict` refines the outcome for the report: a survivor with
  ``equivalent`` is ``equivalent``; a killed mutant with ``equivalent`` is ``contradiction``
  (the argument is wrong); the other outcomes keep their name.
* **Baseline.** Before a campaign, one run of the unmutated copy over the union of all the
  mutants' tests must pass, so that a kill means a failure caused by the mutant
  (``--no-baseline`` skips it).
* **Report.** A table of every mutant (id, verdict, seconds, first killing test) and the
  totals; the behavioural kill rate is ``killed / (killed + snapshot-only + survived)`` with
  equivalent mutants excluded. The score measures this selected list of bugs, not the
  semantic coverage of the tests. ``--report`` writes the same as JSON (``summary`` and
  ``mutants``). The exit status is 1 when a mutant survives without an ``equivalent``
  argument, is snapshot-only, invalid, timed out, or is a contradiction, or when the
  baseline fails.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from xml.etree import ElementTree

if __package__ in (None, ""):  # run as a script: make the `support` package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support.selection import CORPORA, TOOLS, parse_selection

REPOSITORY = Path(__file__).resolve().parents[2]
"""The repository root (``tests/support/mutants.py`` is two levels below it)."""

TIMEOUT_SECONDS = 600
"""The wall-clock limit of one mutant's test run."""

_COPIED_DIRECTORIES = ("src", "tests", "docs", "examples")
_COPIED_FILES = ("pyproject.toml", "uv.lock", "README.md", ".env")
_NOT_COPIED = shutil.ignore_patterns(
    "__pycache__", ".hypothesis", ".pytest_cache", ".mypy_cache", ".ruff_cache", "*.pyc"
)


class InvalidMutantError(ValueError):
    """A mutant whose replacement does not apply to the source (see :func:`apply`)."""


@dataclass(frozen=True)
class Mutant:
    """One deliberate bug (module docstring).

    Attributes:
        id: Unique name; the work directory of its run.
        category: The kind of semantics it breaks (for the summary).
        path: The source file, relative to ``src/sva2yosys``.
        old: Exact text to replace; occurs exactly once in the file.
        new: The replacement.
        bug: The modelled bug, one sentence.
        clause: The IEEE 1800 clause (or ``docs/semantics.md`` section) it violates.
        tests: Pytest paths or node ids (relative to the repository root) that should catch it.
        equivalent: ``None``, or the argument that the mutant is observably equivalent.
    """

    id: str
    category: str
    path: str
    old: str
    new: str
    bug: str
    clause: str
    tests: tuple[str, ...]
    equivalent: str | None = None


class Status(Enum):
    """What running the tests on a mutated copy showed."""

    KILLED = "killed"
    SNAPSHOT_ONLY = "snapshot-only"
    SURVIVED = "survived"
    INVALID = "invalid"
    TIMEOUT = "timeout"


SNAPSHOT_TESTS = frozenset(
    {
        "tests/functional_tests/slang_frontend/test_slang_frontend.py::test_export_matches_golden",
        "tests/functional_tests/generated_monitors/test_compact_direct.py"
        "::test_dense_oracle_retains_its_pre_compaction_bytes",
        "tests/functional_tests/temporal_machine/test_constants_machine.py"
        "::test_monitors_of_directives_without_a_constant_false_condition_are_byte_identical",
        "tests/unit_tests/test_dense_slots.py::test_dense_oracle_keeps_its_frozen_bytes",
        "tests/functional_tests/reference_semantics/test_traceability_page.py"
        "::test_committed_page_equals_the_generated_one",
    }
)
"""The tests (``file::function``, without parameters) that compare generated or exported text
with a frozen copy: the golden JSON exports and the byte or digest comparisons of monitors.
Tests that run a frozen monitor through Yosys/SBY (equivalence proofs) are behavioural."""


def _is_snapshot(classname: str, name: str) -> bool:
    """Whether the JUnit test case (``classname``, ``name``) is in :data:`SNAPSHOT_TESTS`."""
    return f"{classname.replace('.', '/')}.py::{name.split('[')[0]}" in SNAPSHOT_TESTS


@dataclass(frozen=True)
class Outcome:
    """The result of one mutant's run: its status and, for a killed mutant, the first failing
    behavioural test; for a snapshot-only one, the first failing snapshot test; for an invalid
    one, the reason. ``snapshots`` lists the failing snapshot tests of a killed mutant."""

    status: Status
    detail: str = ""
    seconds: float = 0.0
    snapshots: tuple[str, ...] = ()


# --- the list ---------------------------------------------------------------------------

_TM = "tests/functional_tests/temporal_machine"
_AUTOMATON = "tests/unit_tests/test_temporal_automaton.py"
_STEP = "tests/unit_tests/test_temporal_step.py"
_GM = "tests/functional_tests/generated_monitors"
_OM = "tests/functional_tests/optimized_monitors"
_SC = "tests/functional_tests/slang_convert"
_FIRST = "tests/unit_tests/test_first_sample.py"
_XCHECKS = "tests/unit_tests/test_x_checks.py"
_VALIDATE = "tests/unit_tests/test_validate.py"
_LOWER = "tests/unit_tests/test_lower.py"
_LIVENESS = "tests/unit_tests/test_liveness.py"
_RS = "tests/functional_tests/reference_semantics"
_REWRITE = "tests/functional_tests/rewrite"
_FRONTEND = "tests/functional_tests/slang_frontend"

_SEQUENCE_TESTS = (_AUTOMATON, _TM)
_MACHINE_TESTS = (_STEP, _TM)

MUTANTS: tuple[Mutant, ...] = (
    # --- delays, repetitions, goto and nonconsecutive repetition ------------------------
    Mutant(
        id="delay-window-upper-excluded",
        category="delays and repetitions",
        path="temporal/automaton.py",
        old="delays = range(max(low, 1), high + 1)",
        new="delays = range(max(low, 1), high)",
        bug="a bounded delay ##[m:n] reaches n - 1 samples, not n",
        clause="IEEE 1800 16.7 (cycle delay ranges)",
        tests=_SEQUENCE_TESTS,
    ),
    Mutant(
        id="delay-unbounded-minimum-plus-one",
        category="delays and repetitions",
        path="temporal/automaton.py",
        old="        first = max(low, 1)\n        fillers",
        new="        first = max(low, 0) + 1\n        fillers",
        bug="##[m:$] waits m + 1 samples before the right operand can start",
        clause="IEEE 1800 16.7 (unbounded cycle delay)",
        tests=_SEQUENCE_TESTS,
    ),
    Mutant(
        id="delay-zero-fusion-dropped",
        category="delays and repetitions",
        path="temporal/automaton.py",
        old=(
            "        if low == 0:\n            self._fuse(left, right, start, finals)\n"
            "        return _Fragment(start, finals, nullable)"
        ),
        new=(
            "        if low < 0:\n            self._fuse(left, right, start, finals)\n"
            "        return _Fragment(start, finals, nullable)"
        ),
        bug="##[0:n] loses its ##0 alternative (the fused sample)",
        clause="IEEE 1800 16.7 (##0 fuses the operands)",
        tests=_SEQUENCE_TESTS,
    ),
    Mutant(
        id="repeat-upper-excluded",
        category="delays and repetitions",
        path="temporal/automaton.py",
        old="for count in range(1, high + 1):",
        new="for count in range(1, high):",
        bug="s[*m:n] stops at n - 1 repetitions",
        clause="IEEE 1800 16.9.2 (consecutive repetition)",
        tests=_SEQUENCE_TESTS,
    ),
    Mutant(
        id="repeat-lower-exclusive",
        category="delays and repetitions",
        path="temporal/automaton.py",
        old="            if count >= low:",
        new="            if count > low:",
        bug="s[*m:n] needs at least m + 1 repetitions",
        clause="IEEE 1800 16.9.2 (consecutive repetition)",
        tests=_SEQUENCE_TESTS,
    ),
    Mutant(
        id="repeat-unbounded-extra-copy",
        category="delays and repetitions",
        path="temporal/automaton.py",
        old="        for _ in range(max(low, 1) - 1):\n            copy = self.sequence(inner)",
        new="        for _ in range(max(low, 1)):\n            copy = self.sequence(inner)",
        bug="s[*m:$] needs m + 1 repetitions",
        clause="IEEE 1800 16.9.2 (unbounded consecutive repetition)",
        tests=_SEQUENCE_TESTS,
    ),
    Mutant(
        id="goto-ignores-the-gaps",
        category="goto and nonconsecutive repetition",
        path="ir/sequence.py",
        old="Repeat(Bool(_negated(node.condition)), 0, None), Delay(1, 1), Bool(node.condition)",
        new="Repeat(Bool(Const(1, BIT)), 0, None), Delay(1, 1), Bool(node.condition)",
        bug="b[->n] expands to (1[*0:$] ##1 b)[*n], so b may hold in the gaps and the match "
        "no longer ends at the n-th occurrence",
        clause="IEEE 1800 16.9.2 (goto repetition)",
        tests=_SEQUENCE_TESTS,
    ),
    Mutant(
        id="nonconsecutive-tail-required",
        category="goto and nonconsecutive repetition",
        path="ir/sequence.py",
        old="        Delay(1, 1),\n        Repeat(Bool(_negated(node.condition)), 0, None),\n    )",
        new="        Delay(1, 1),\n        Repeat(Bool(_negated(node.condition)), 1, None),\n    )",
        bug="b[=n] needs at least one more sample after the n-th occurrence",
        clause="IEEE 1800 16.9.2 (nonconsecutive repetition)",
        tests=_SEQUENCE_TESTS,
    ),
    # --- sequence composition ----------------------------------------------------------
    Mutant(
        id="intersect-built-as-and",
        category="sequence composition",
        path="temporal/automaton.py",
        old='return self._product(self.sequence(left), self.sequence(right), "intersect")',
        new='return self._product(self.sequence(left), self.sequence(right), "and")',
        bug="intersect lets the operands end at different samples",
        clause="IEEE 1800 16.9.6 (intersect)",
        tests=_SEQUENCE_TESTS,
    ),
    Mutant(
        id="and-ended-operand-dropped",
        category="sequence composition",
        path="temporal/automaton.py",
        old="            if idle and position in fragment.finals:",
        new='            if idle and position in fragment.finals and mode == "within":',
        bug="in s1 and s2 the operand that ended first no longer waits for the other",
        clause="IEEE 1800 16.9.5 (sequence and)",
        tests=_SEQUENCE_TESTS,
    ),
    Mutant(
        id="and-ends-at-the-earlier-end",
        category="sequence composition",
        path="temporal/automaton.py",
        old=(
            "                if ended(first, a)\n                and ended(second, b)\n"
            "                and (a, b) != (_DONE, _DONE)"
        ),
        new=(
            "                if (ended(first, a) or ended(second, b))\n"
            "                and (a, b) != (_DONE, _DONE)"
        ),
        bug="a match of s1 and s2 ends where the first operand ends, not the later one",
        clause="IEEE 1800 16.9.5 (the end point is the later of the two)",
        tests=_SEQUENCE_TESTS,
    ),
    Mutant(
        id="within-inner-pinned-to-the-start",
        category="sequence composition",
        path="temporal/automaton.py",
        old="starts = {(a, b) for a in {_BEFORE} | first.start for b in second.start}",
        new="starts = {(a, b) for a in first.start for b in second.start}",
        bug="the inner sequence of within must start with the outer one",
        clause="IEEE 1800 16.9.10 (within)",
        tests=_SEQUENCE_TESTS,
    ),
    Mutant(
        id="throughout-first-sample-only",
        category="sequence composition",
        path="temporal/automaton.py",
        old=(
            "                        self._charge(len(self._next[letter]))\n"
            "                        stack.extend(self._next[letter])\n"
            "                for letter in seen:"
        ),
        new=(
            "                        self._charge(len(self._next[letter]))\n"
            "                for letter in seen:"
        ),
        bug="the throughout condition is required at the first sample of the match only",
        clause="IEEE 1800 16.9.9 (throughout)",
        tests=_SEQUENCE_TESTS,
    ),
    Mutant(
        id="and-bound-takes-the-shorter-end",
        category="sequence composition",
        path="temporal/bounds.py",
        old="SequenceBounds(False, low, _later(first.max_length, second.max_length))",
        new="SequenceBounds(False, low, _earlier(first.max_length, second.max_length))",
        bug="the longest match of s1 and s2 is taken as the shorter operand's, so an "
        "obligation outlives its slots",
        clause="IEEE 1800 16.9.5 (sequence and)",
        tests=_SEQUENCE_TESTS,
    ),
    # --- implication, negation, obligations --------------------------------------------
    Mutant(
        id="nonoverlapping-implication-built-overlapping",
        category="implication and negation",
        path="temporal/compile.py",
        old="            return SeqConcat(antecedent, Delay(1, 1), TRUE), inner.sequence, negated",
        new="            return antecedent, inner.sequence, negated",
        bug="a |=> c is built as a |-> c",
        clause="IEEE 1800 16.12.7 (implication)",
        tests=_MACHINE_TESTS,
    ),
    Mutant(
        id="negation-outputs-not-swapped",
        category="implication and negation",
        path="temporal/machine.py",
        old="    if machine.negated:\n        success, failure = failure, success",
        new="    if False:\n        success, failure = failure, success",
        bug="not s: a match of s counts as success and a dead obligation as failure",
        clause="IEEE 1800 16.12.3 (not)",
        tests=_MACHINE_TESTS,
    ),
    Mutant(
        id="negation-outputs-not-swapped-configurations",
        category="implication and negation",
        path="temporal/configurations.py",
        old="    if machine.negated:  # not s",
        new="    if False:  # not s",
        bug="not s of an unbounded sequence: a match counts as success and a dead frontier "
        "as failure",
        clause="IEEE 1800 16.12.3 (not)",
        tests=_MACHINE_TESTS,
    ),
    Mutant(
        id="negation-not-swapped-in-slot-monitor",
        category="implication and negation",
        path="codegen/emit_slots.py",
        old="    if machine.negated:  # not s",
        new="    if False:  # not s",
        bug="the generated monitor of not s reports a match as success",
        clause="IEEE 1800 16.12.3 (not)",
        tests=(_GM,),
    ),
    Mutant(
        id="negation-not-swapped-in-configuration-monitor",
        category="implication and negation",
        path="codegen/emit_configurations.py",
        old="    if machine.negated:  # not s",
        new="    if False:  # not s",
        bug="the generated monitor of not s (unbounded) reports a match as success",
        clause="IEEE 1800 16.12.3 (not)",
        tests=(_GM,),
    ),
    Mutant(
        id="passed-obligation-keeps-running",
        category="implication and negation",
        path="temporal/machine.py",
        old="            success = True\n            continue\n        following",
        new="            success = True\n        following",
        bug="an obligation that matched is not discharged: when it has no continuation it "
        "is also counted as a failure",
        clause="IEEE 1800 16.12.7 (the consequent holds once it has a match)",
        tests=_MACHINE_TESTS,
    ),
    Mutant(
        id="antecedent-drops-later-endpoints",
        category="implication and negation",
        path="temporal/machine.py",
        old="        next_antecedent = _successors(antecedent, held)",
        new=(
            "        next_antecedent = _successors(\n"
            "            antecedent,\n"
            "            frozenset(i for i in held if not antecedent.letters[i].accepting),\n"
            "        )"
        ),
        bug="an antecedent attempt stops at its first match, so its later endpoints do not "
        "trigger the consequent",
        clause="IEEE 1800 16.12.7 (each match of the antecedent starts the consequent)",
        tests=_MACHINE_TESTS,
    ),
    Mutant(
        id="obligation-slots-one-short",
        category="implication and negation",
        path="temporal/bounds.py",
        old="return 0 if bounds.max_length is None else max(0, bounds.max_length - 1)",
        new="return 0 if bounds.max_length is None else max(0, bounds.max_length - 2)",
        bug="the machine registers one obligation age too few",
        clause="IEEE 1800 16.12.7 (a consequent of any length)",
        tests=_MACHINE_TESTS,
    ),
    Mutant(
        id="always-window-shifted",
        category="implication and negation",
        path="temporal/lower.py",
        old="            return _implication(window(low, high), inner, True)\n"
        "        case Eventually",
        new="            return _implication(window(low, high), inner, False)\n"
        "        case Eventually",
        bug="always [m:n] p is lowered with |=>, checking p one sample late",
        clause="IEEE 1800 16.12.11 (always)",
        tests=(_LOWER, _TM),
    ),
    # --- strength, liveness and until --------------------------------------------------
    Mutant(
        id="strong-checked-as-weak",
        category="strength and liveness",
        path="temporal/compile.py",
        old=(
            "    if directive.kind is DirectiveKind.COVER or "
            "not strong_unbounded_part(directive.property):"
        ),
        new="    if True:",
        bug="a strong unbounded sequence gets no liveness check, as if it were weak",
        clause="IEEE 1800 16.12.2 (strong sequences)",
        tests=(_TM, _LIVENESS),
    ),
    Mutant(
        id="strong-until-built-weak",
        category="strength and liveness",
        path="temporal/lower.py",
        old="            strength = Strength.STRONG if prop.strong else Strength.WEAK\n",
        new="            strength = Strength.WEAK\n",
        bug="s_until is lowered as a weak sequence, so a pending right operand is not "
        "checked at the end",
        clause="IEEE 1800 16.12.12 (until)",
        tests=(_LOWER, _TM),
    ),
    Mutant(
        id="liveness-pending-bit-constant-zero",
        category="strength and liveness",
        path="codegen/emit_configurations.py",
        old='            source = "cfg_q[0]" if machine.configurations else "1\'b0"',
        new='            source = "1\'b0"',
        bug="the busy bit of the pending-bit liveness check is stuck at 0, so no obligation "
        "stays pending for ever",
        clause="IEEE 1800 16.12.2 (strong: a sequence with no match is false)",
        tests=(_GM,),
    ),
    Mutant(
        id="liveness-kind-threshold",
        category="strength and liveness",
        path="temporal/liveness.py",
        old="return Liveness.PENDING if configurations <= 1 else Liveness.WITNESS",
        new="return Liveness.PENDING if configurations <= 2 else Liveness.WITNESS",
        bug="the pending bit is used for two configurations, where obligations can end apart",
        clause="IEEE 1800 16.12.2 (strong: a sequence with no match is false)",
        tests=(_LIVENESS, _TM, _GM),
    ),
    Mutant(
        id="witness-picks-without-trigger",
        category="strength and liveness",
        path="temporal/liveness.py",
        old="        if not (events.triggered and pick and machine.configurations):",
        new="        if not (pick and machine.configurations):",
        bug="the liveness witness follows an obligation at a sample where none was launched",
        clause="IEEE 1800 16.12.2 (strong: a sequence with no match is false)",
        tests=(_LIVENESS, _TM),
    ),
    Mutant(
        id="until-with-built-as-until",
        category="strength and liveness",
        path="temporal/lower.py",
        old="        condition = Binary(BinaryOp.LOGICAL_AND, prop.left, prop.right, BIT)",
        new="        condition = prop.right",
        bug="p until_with q ends at q alone, without requiring p at that sample",
        clause="IEEE 1800 16.12.12 (until_with)",
        tests=(_LOWER, _TM),
    ),
    Mutant(
        id="until-needs-the-left-operand-once",
        category="strength and liveness",
        path="temporal/lower.py",
        old="SeqConcat(Repeat(Bool(prop.left), 0, None), Delay(1, 1), Bool(condition))",
        new="SeqConcat(Repeat(Bool(prop.left), 1, None), Delay(1, 1), Bool(condition))",
        bug="p until q fails when q already holds at the first sample",
        clause="IEEE 1800 16.12.12 (until)",
        tests=(_LOWER, _TM),
    ),
    # --- disable iff -------------------------------------------------------------------
    Mutant(
        id="disable-keeps-the-state",
        category="disable iff",
        path="temporal/machine.py",
        old="        return initial_state(machine), StepEvents(",
        new="        return state, StepEvents(",
        bug="disable iff suppresses the events of its sample but leaves running attempts "
        "alive for the next sample",
        clause="IEEE 1800 16.12.1 (disable iff aborts the attempts)",
        tests=_MACHINE_TESTS,
    ),
    Mutant(
        id="disable-keeps-the-configurations",
        category="disable iff",
        path="temporal/configurations.py",
        old="        return initial_config_state(), StepEvents(",
        new="        return state, StepEvents(",
        bug="disable iff leaves the live obligations of an unbounded consequent alive",
        clause="IEEE 1800 16.12.1 (disable iff aborts the attempts)",
        tests=_MACHINE_TESTS,
    ),
    Mutant(
        id="disable-fail-not-suppressed",
        category="disable iff",
        path="codegen/emit_slots.py",
        old='    lines.append(f"    assign fail = !dis & ({any_fail});")',
        new='    lines.append(f"    assign fail = ({any_fail});")',
        bug="the monitor reports a failure at a sample where disable iff holds",
        clause="IEEE 1800 16.12.1 (disable iff)",
        tests=(_GM,),
    ),
    # --- first sample, $past, sampled values -------------------------------------------
    Mutant(
        id="past-gate-one-sample-long",
        category="first sample and sampled values",
        path="temporal/machine.py",
        old="        if elapsed < machine.past_depth[condition]:",
        new=(
            "        if machine.past_depth[condition] > 0 "
            "and elapsed <= machine.past_depth[condition]:"
        ),
        bug="a condition using $past (positive history depth) stays false one sample longer "
        "than the history is unknown",
        clause="IEEE 1800 16.9.3 ($past before the first samples is the default value)",
        tests=_MACHINE_TESTS,
    ),
    Mutant(
        id="past-gate-valid-dropped",
        category="first sample and sampled values",
        path="codegen/core.py",
        old='            if depth == 1:\n                value = f"valid & {value}"',
        new='            if depth == 1:\n                value = f"{value}"',
        bug="the generated monitor does not gate a $past condition at the first sample",
        clause="IEEE 1800 16.9.3 ($past before the first sample)",
        tests=(_GM,),
    ),
    Mutant(
        id="past-gate-seen-off-by-one",
        category="first sample and sampled values",
        path="codegen/core.py",
        old='value = f"(seen >= {width}\'d{depth}) & {value}"',
        new='value = f"(seen > {width}\'d{depth}) & {value}"',
        bug="a condition with $past(e, n) is gated for n + 1 samples in the monitor",
        clause="IEEE 1800 16.9.3 ($past(e, n) before n samples)",
        tests=(_GM,),
    ),
    Mutant(
        id="past-history-one-stage-short",
        category="first sample and sampled values",
        path="codegen/core.py",
        old="        for k in range(1, depth + 1):",
        new="        for k in range(1, depth):",
        bug="$past(e, n) is read through n - 1 history registers",
        clause="IEEE 1800 16.9.3 ($past with a number of ticks)",
        tests=(_GM,),
    ),
    Mutant(
        id="past-first-sample-class-off-by-one",
        category="first sample and sampled values",
        path="passes/first_sample.py",
        old="return _ALL_X if function is SampledFunction.PAST and sample < depth else _CLEAN",
        new="return _ALL_X if function is SampledFunction.PAST and sample <= depth else _CLEAN",
        bug="$past(e, n) is treated as unknown at sample n as well",
        clause="IEEE 1800 16.9.3 ($past with a number of ticks)",
        tests=(_FIRST, _SC),
    ),
    Mutant(
        id="default-sampled-value-of-initialised-signal",
        category="first sample and sampled values",
        path="passes/first_sample.py",
        old="            return _ALL_X if default is SampledDefault.X else _ANY",
        new="            return _ALL_X",
        bug="a signal with an initialiser or a 2-state type is taken to default to X "
        "before the first tick",
        clause="IEEE 1800 16.5.1 (default sampled values)",
        tests=(_FIRST, _SC),
    ),
    Mutant(
        id="zero-extension-of-unknown-stays-unknown",
        category="first sample and sampled values",
        path="passes/first_sample.py",
        old="        return _ALL_X if signed else _SOME_X",
        new="        return _ALL_X",
        bug="a zero-extended unknown $past is classified as entirely unknown, so a "
        "condition that is 0 at the first sample is accepted as unknown",
        clause="IEEE 1800 11.8.2 (extension), 16.9.3",
        tests=(_FIRST, _SC),
    ),
    Mutant(
        id="rose-ignores-the-first-sample",
        category="first sample and sampled values",
        path="codegen/core.py",
        old='return f"{current}[0] & ~(valid & {previous}[0])"',
        new='return f"{current}[0] & ~{previous}[0]"',
        bug="$rose reads the unconstrained history register at the first sample",
        clause="IEEE 1800 16.9.3 ($rose, default sampled value)",
        tests=(_GM,),
    ),
    Mutant(
        id="stable-ignores-the-first-sample",
        category="first sample and sampled values",
        path="codegen/core.py",
        old='return f"valid & ({current} == {previous})"',
        new='return f"({current} == {previous})"',
        bug="$stable reads the unconstrained history register at the first sample",
        clause="IEEE 1800 16.9.3 ($stable, default sampled value)",
        tests=(_GM,),
    ),
    Mutant(
        id="initial-first-sample-always-true",
        category="first sample and sampled values",
        path="codegen/core.py",
        old='return self._wire(expr, "!valid")',
        new='return self._wire(expr, "1\'b1")',
        bug="the enabling condition of an initial-block assertion is true at every tick, so "
        "the assertion is attempted at every tick as at module level",
        clause="IEEE 1800 16.14.6 (an initial procedure's assertion has one attempt)",
        tests=(f"{_GM}/test_generated_monitors.py::test_initial_directives_through_the_monitor",),
    ),
    # --- constants, X-checks, covers ---------------------------------------------------
    Mutant(
        id="constant-false-letter-always-holds",
        category="constants and X-checks",
        path="temporal/automaton.py",
        old="            case Bool(expr=Const(value=value)) if value != 0:",
        new="            case Bool(expr=Const(value=value)) if True:",
        bug="a constant-false Boolean in a sequence is treated as true",
        clause="IEEE 1800 16.9.1 (a Boolean holds if its value is true)",
        tests=_SEQUENCE_TESTS,
    ),
    Mutant(
        id="x-fold-implication-wrong-short-circuit",
        category="constants and X-checks",
        path="passes/x_checks.py",
        old=(
            "            if tl is False or tr is True:\n"
            "                return _bit(True, value_type)"
        ),
        new=(
            "            if tl is False or tr is False:\n"
            "                return _bit(True, value_type)"
        ),
        bug="a -> b folds to true when b is constant false",
        clause="IEEE 1800 11.4.7 (logical implication)",
        tests=(_XCHECKS, _SC),
    ),
    Mutant(
        id="x-fold-empty-repetition-never-matches",
        category="constants and X-checks",
        path="passes/x_checks.py",
        old="            return minimum >= 1 and never_matches(inner)",
        new="            return never_matches(inner)",
        bug="s[*0:n] of a never-matching s is taken to never match, forgetting its empty match",
        clause="IEEE 1800 16.9.2 (s[*0] is the empty match)",
        tests=(_XCHECKS, _SC),
    ),
    Mutant(
        id="x-check-strong-sequence-never-fails",
        category="constants and X-checks",
        path="passes/x_checks.py",
        old="            return strength is not Strength.STRONG and _always_matches(sequence)",
        new="            return _always_matches(sequence)",
        bug="a strong sequence is skipped as unfailable because its Booleans are constants, "
        "although a trace may end before it matches",
        clause="IEEE 1800 16.12.2 (strong sequences)",
        tests=(_XCHECKS, _SC),
    ),
    Mutant(
        id="cover-accepts-undecidable-nonvacuity",
        category="cover and nonvacuity",
        path="temporal/nonvacuity.py",
        old="    if nv_at_true(prop):\n        return None",
        new="    if True:\n        return None",
        bug="a cover whose nonvacuity is unknown when it succeeds is accepted",
        clause="IEEE 1800 16.14.8 (nonvacuous evaluation)",
        tests=(_TM, _VALIDATE),
    ),
    Mutant(
        id="cover-counts-later-matches",
        category="cover and nonvacuity",
        path="codegen/emit_slots.py",
        old="""following = f"age{age}_accept ? {width}'d0 : age{age}_next\"""",
        new="""following = f"age{age}_next\"""",
        bug="an obligation that matched keeps running, so a cover counts a second match "
        "of the same attempt",
        clause="IEEE 1800 16.14.3 (cover sequence: first match), 16.14.8",
        tests=(_GM,),
    ),
    Mutant(
        id="branch-nonvacuity-ignores-launch",
        category="cover and nonvacuity",
        path="temporal/attempts.py",
        old="        return inst.live if launch_nv(prop) else inst.nonvacuous",
        new="        return inst.nonvacuous",
        bug="the nonvacuity of a branch known at launch is read from the branch's own later flag",
        clause="IEEE 1800 16.14.8 (nonvacuous evaluation)",
        tests=(_TM,),
    ),
    Mutant(
        id="implication-true-before-antecedent-ends",
        category="cover and nonvacuity",
        path="temporal/attempts.py",
        old="true = nl.and_((live, nl.not_(false), nl.not_(more), nl.not_(pending_subs)))",
        new="true = nl.and_((live, nl.not_(false), nl.not_(pending_subs)))",
        bug="an implication attempt is decided true while its antecedent can still match later",
        clause="IEEE 1800 16.12.7 (implication)",
        tests=(_TM,),
    ),
    Mutant(
        id="if-else-both-branches-launched",
        category="cover and nonvacuity",
        path="temporal/attempts.py",
        old="            else_launch = nl.and_((launch, nl.not_(condition)))",
        new="            else_launch = nl.and_((launch, condition))",
        bug="the else branch of if (e) is launched when e holds",
        clause="IEEE 1800 16.12.6 (if-else)",
        tests=(_TM,),
    ),
    # --- conversion --------------------------------------------------------------------
    Mutant(
        id="propagated-conversion-always-zero-extends",
        category="conversion",
        path="slang/convert.py",
        old=(
            "                    and not value_type.signed\n"
            "                    and value_type.width"
        ),
        new="                    and value_type.width",
        bug="a signed operand is zero-extended even in a signed context",
        clause="IEEE 1800 11.8.2 (sign extension in a signed expression)",
        tests=(_SC,),
    ),
    Mutant(
        id="leading-delay-range-dropped",
        category="conversion",
        path="slang/convert.py",
        old="sequence if (minimum, maximum) == (0, 0) else SeqConcat(TRUE, delay, sequence)",
        new="sequence if minimum == 0 else SeqConcat(TRUE, delay, sequence)",
        bug="a leading ##[0:n] is read as no delay",
        clause="IEEE 1800 16.7 (leading cycle delay)",
        tests=(_SC, _TM),
    ),
    # --- code generation ---------------------------------------------------------------
    Mutant(
        id="emit-less-equal-as-less",
        category="code generation",
        path="codegen/core.py",
        old='    BinaryOp.LESS_THAN_EQUAL: "<=",',
        new='    BinaryOp.LESS_THAN_EQUAL: "<",',
        bug="a <= b is emitted as a < b",
        clause="IEEE 1800 11.4.4 (relational operators)",
        tests=(_GM,),
    ),
    Mutant(
        id="emit-signed-literal-unsigned",
        category="code generation",
        path="codegen/core.py",
        old='    signed = "s" if value_type.signed else ""',
        new='    signed = ""',
        bug="a signed constant is emitted as an unsigned literal",
        clause="IEEE 1800 5.7.1 (integer literal constants), 11.8.1",
        tests=(_GM,),
        equivalent=(
            "every constant is assigned to a wire declared with the IR type "
            "(`logic signed [w-1:0] eN`, codegen/core.py Shell._wire), and the literal has the "
            "same width and bit pattern signed or not, so the wire holds the same bits and "
            "operators only ever see the wire's declared signedness"
        ),
    ),
    Mutant(
        id="antecedent-starts-only-once",
        category="code generation",
        path="codegen/frontier.py",
        old='held(expressions, "ant", antecedent, f"(ant_q | {start(expressions, antecedent)})")',
        new='held(expressions, "ant", antecedent, "ant_q")',
        bug="the monitor starts no new antecedent attempt at each sample",
        clause="IEEE 1800 16.12.7 (an attempt at every clock tick)",
        tests=(_GM,),
    ),
    Mutant(
        id="configuration-trigger-not-launched",
        category="code generation",
        path="codegen/emit_configurations.py",
        old='            extra = " | trigger" if k == 0 else ""',
        new='            extra = ""',
        bug="a trigger does not launch an obligation in the start configuration",
        clause="IEEE 1800 16.12.7 (implication)",
        tests=(_GM,),
    ),
    Mutant(
        id="configuration-accept-keeps-moving",
        category="code generation",
        path="codegen/emit_configurations.py",
        old="expressions.conjunction([f'!accept{k}', *terms])",
        new="expressions.conjunction([*terms])",
        bug="an obligation that matched is carried to its successors as well",
        clause="IEEE 1800 16.12.7 (the consequent holds once it has a match)",
        tests=(_GM,),
    ),
    Mutant(
        id="attempt-and-emitted-as-or",
        category="code generation",
        path="codegen/emit_attempts.py",
        old=(
            "            case NAnd(operands=operands):\n"
            "                value = shell.expressions.conjunction("
        ),
        new=(
            "            case NAnd(operands=operands):\n"
            "                value = shell.expressions.disjunction("
        ),
        bug="the per-attempt monitor emits an AND gate of its netlist as an OR",
        clause="IEEE 1800 16.12 (property evaluation)",
        tests=(_GM,),
    ),
    Mutant(
        id="attempt-success-not-suppressed",
        category="code generation",
        path="codegen/emit_attempts.py",
        old='    lines.append(f"    assign success = !dis & ({ref(netlist.success)});")',
        new='    lines.append(f"    assign success = ({ref(netlist.success)});")',
        bug="the per-attempt monitor reports a success at a sample where disable iff holds",
        clause="IEEE 1800 16.12.1 (disable iff), 16.14.8",
        tests=(_GM,),
    ),
    Mutant(
        id="rewrite-monitor-ports-crossed",
        category="exporter and source rewrite",
        path="rewrite.py",
        old="for i, n in enumerate(monitor_ports(local_machine)))",
        new="for i, n in enumerate(reversed(monitor_ports(local_machine))))",
        bug="the instance of a monitor is connected to the design signals in reverse order",
        clause="docs/architecture.md (source rewriting: monitor connection)",
        tests=(_REWRITE,),
    ),
    Mutant(
        id="export-integral-width-plus-one",
        category="exporter and source rewrite",
        path="slang/export.py",
        old='    json: JsonObject = {\n        "width": width,',
        new='    json: JsonObject = {\n        "width": width + 1,',
        bug="the exporter reports every integral type one bit too wide",
        clause="IEEE 1800 6.11 (integral types)",
        tests=(_FRONTEND, _SC),
    ),
    Mutant(
        id="export-initialised-signal-defaults-to-x",
        category="exporter and source rewrite",
        path="slang/export.py",
        old='    return "x" if cast("object", symbol.initializer) is None else "known"',
        new='    return "x"',
        bug="the exporter reports a variable with a declaration initialiser as defaulting to X",
        clause="IEEE 1800 16.5.1 (default sampled values)",
        tests=(_FRONTEND, _SC),
    ),
    Mutant(
        id="window-first-age-off-by-one",
        category="code generation",
        path="codegen/emit_slots.py",
        old=""" : (win_t[{low - 1}] ? {bits}'d{low} : {bits}'d0);",""",
        new=""" : (win_t[{low - 1}] ? {bits}'d{low + 1} : {bits}'d0);",""",
        bug="the window form starts the oldest obligation one age late",
        clause="IEEE 1800 16.7 (cycle delay ranges)",
        tests=(_OM,),
    ),
)

CANARY = Mutant(
    id="canary-package-does-not-import",
    category="canary",
    path="__init__.py",
    old='"""sva2yosys: lower',
    new='raise RuntimeError("canary: the mutated copy was imported")\n"""sva2yosys: lower',
    bug="the package cannot be imported",
    clause="none",
    tests=("tests/unit_tests/test_ir.py",),
)
"""A mutant that must come out ``invalid`` with the canary message: it proves that the copy's
package, not the working tree's, is the one the tests import."""


# --- application ------------------------------------------------------------------------


def apply(mutant: Mutant, source_root: Path) -> None:
    """Edit the file of ``mutant`` under ``source_root`` (the directory holding the package
    ``sva2yosys``, i.e. a copy's ``src``).

    Raises:
        InvalidMutantError: The file is missing, ``old`` equals ``new``, or ``old`` does not occur
            exactly once.
    """
    target = source_root / "sva2yosys" / mutant.path
    if not target.is_file():
        raise InvalidMutantError(f"{mutant.id}: no file {mutant.path}")
    if mutant.old == mutant.new:
        raise InvalidMutantError(f"{mutant.id}: the replacement changes nothing")
    text = target.read_bytes().decode("utf-8")
    count = occurrences(mutant, text)
    if count != 1:
        raise InvalidMutantError(f"{mutant.id}: the old text occurs {count} times in {mutant.path}")
    old, new = _in_line_endings(mutant, text)
    target.write_bytes(text.replace(old, new).encode("utf-8"))


def _in_line_endings(mutant: Mutant, text: str) -> tuple[str, str]:
    """``old`` and ``new`` with the line endings of ``text`` (a checkout may use CRLF)."""
    if "\r\n" not in text:
        return mutant.old, mutant.new
    return mutant.old.replace("\n", "\r\n"), mutant.new.replace("\n", "\r\n")


def occurrences(mutant: Mutant, text: str) -> int:
    """How many times the ``old`` text of ``mutant`` occurs in the source ``text``, whatever
    its line endings."""
    return text.count(_in_line_endings(mutant, text)[0])


# --- running ----------------------------------------------------------------------------

_ERROR_LINE = re.compile(r"^E\s+(.*\S)", re.MULTILINE)


def classify_result(returncode: int, junit: str | None, output: str) -> Outcome:
    """The outcome of a finished pytest run (module docstring, *Outcome*): ``junit`` is the
    text of the JUnit file, or ``None`` when pytest wrote none."""
    if returncode == 0:
        return Outcome(Status.SURVIVED)
    error = _ERROR_LINE.search(output)
    reason = error.group(1) if error else f"pytest exit status {returncode}"
    if returncode != 1 or junit is None:
        return Outcome(Status.INVALID, reason)
    try:
        cases = ElementTree.fromstring(junit).iter("testcase")
        failed: list[str] = []
        snapshots: tuple[str, ...] = ()
        for case in cases:
            classname, name = case.get("classname", ""), case.get("name", "")
            identifier = f"{classname}::{name}"
            if case.find("error") is not None:
                return Outcome(Status.INVALID, f"error in {identifier}")
            if case.find("failure") is not None:
                failed.append(identifier)
                if _is_snapshot(classname, name):
                    snapshots = (*snapshots, identifier)
    except ElementTree.ParseError:
        return Outcome(Status.INVALID, "unreadable JUnit result")
    if not failed:
        return Outcome(Status.INVALID, "exit status 1 without a failing test")
    behavioural = [t for t in failed if t not in snapshots]
    if behavioural:
        return Outcome(Status.KILLED, behavioural[0], snapshots=snapshots)
    return Outcome(Status.SNAPSHOT_ONLY, snapshots[0], snapshots=snapshots)


def verdict(mutant: Mutant, outcome: Outcome) -> str:
    """The report label of ``outcome`` (module docstring, *Verdict*)."""
    if mutant.equivalent is not None:
        if outcome.status is Status.SURVIVED:
            return "equivalent"
        if outcome.status in (Status.KILLED, Status.SNAPSHOT_ONLY):
            return "contradiction"
    return outcome.status.value


def copy_repository(destination: Path, root: Path = REPOSITORY) -> None:
    """Copy the files a test run needs from ``root`` to the new directory ``destination``."""
    destination.mkdir(parents=True)
    for name in _COPIED_DIRECTORIES:
        if (root / name).is_dir():
            shutil.copytree(root / name, destination / name, ignore=_NOT_COPIED)
    for name in _COPIED_FILES:
        if (root / name).is_file():
            shutil.copy2(root / name, destination / name)


def _kill_tree(process: subprocess.Popen[str]) -> None:
    """Kill ``process`` and all its descendants."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    process.kill()


def run_tree(
    command: Sequence[str],
    cwd: Path,
    environment: dict[str, str],
    timeout: float,
) -> tuple[int, str]:
    """Run ``command``; return its exit status and its output. On ``timeout`` the whole
    process tree is killed and reaped before ``subprocess.TimeoutExpired`` is raised."""
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        start_new_session=sys.platform != "win32",
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        process.communicate()
        raise
    return process.returncode, output


class SetupError(RuntimeError):
    """The test run could not start because the requested external resources are missing
    (pytest's usage error). It says nothing about any mutant."""


def pytest_command(
    tests: Sequence[str], external_tools: str = "all", external_corpora: str = "all"
) -> list[str]:
    """The pytest command of one run (module docstring, *Outcome*), with the explicit
    external-resource selection."""
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-rfE",
        "--junitxml=junit.xml",
        "--ignore=tests/unit_tests/test_mutants.py",
        f"--external-tools={external_tools}",
        f"--external-corpora={external_corpora}",
        *tests,
    ]


def run_pytest(
    copy: Path,
    tests: Sequence[str],
    external_tools: str = "all",
    external_corpora: str = "all",
) -> Outcome:
    """Run pytest in ``copy`` against the copy's package and classify the result.

    Raises:
        SetupError: pytest refused to start (exit status 4: a selected tool or corpus is
            unavailable, or the selection is malformed).
    """
    environment = dict(os.environ)
    path = str(copy / "src")
    environment["PYTHONPATH"] = (
        path + os.pathsep + environment["PYTHONPATH"] if "PYTHONPATH" in environment else path
    )
    junit = copy / "junit.xml"
    junit.unlink(missing_ok=True)
    command = pytest_command(tests, external_tools, external_corpora)
    returncode, output = run_tree(command, copy, environment, TIMEOUT_SECONDS)
    if returncode == 4:
        raise SetupError(output.strip()[-1500:])
    return classify_result(returncode, read_junit(junit), output)


def read_junit(path: Path) -> str | None:
    """The text of the JUnit file, or ``None`` when it is absent or unreadable (then
    :func:`classify_result` makes the mutant invalid instead of aborting the campaign)."""
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except (OSError, UnicodeError):
        return None


def _inside(path: Path, root: Path) -> bool:
    return path.resolve() == root.resolve() or root.resolve() in path.resolve().parents


def run(
    mutant: Mutant,
    workdir: Path,
    *,
    keep: bool = False,
    root: Path = REPOSITORY,
    external_tools: str = "all",
    external_corpora: str = "all",
) -> Outcome:
    """Run the tests of ``mutant`` on a mutated copy of ``root`` in ``<workdir>/<id>``
    (module docstring, *Isolation* and *Outcome*)."""
    if _inside(workdir, root):
        raise ValueError("the work directory must be outside the repository")
    copy = workdir / mutant.id
    if copy.exists():
        shutil.rmtree(copy, ignore_errors=True)
    started = time.monotonic()
    try:
        copy_repository(copy, root)
        try:
            apply(mutant, copy / "src")
        except InvalidMutantError as error:
            return Outcome(Status.INVALID, str(error), time.monotonic() - started)
        try:
            outcome = run_pytest(copy, mutant.tests, external_tools, external_corpora)
        except subprocess.TimeoutExpired:
            return Outcome(Status.TIMEOUT, f"over {TIMEOUT_SECONDS} s", time.monotonic() - started)
        return replace(outcome, seconds=time.monotonic() - started)
    finally:
        if not keep:
            shutil.rmtree(copy, ignore_errors=True)


def baseline(
    workdir: Path,
    mutants: Sequence[Mutant],
    root: Path = REPOSITORY,
    external_tools: str = "all",
    external_corpora: str = "all",
) -> Outcome:
    """Run the union of the tests of ``mutants`` on an unmutated copy: ``survived`` means
    they all pass."""
    tests = list(dict.fromkeys(test for mutant in mutants for test in mutant.tests))
    copy = workdir / "baseline"
    if copy.exists():
        shutil.rmtree(copy, ignore_errors=True)
    started = time.monotonic()
    try:
        copy_repository(copy, root)
        try:
            outcome = run_pytest(copy, tests, external_tools, external_corpora)
        except subprocess.TimeoutExpired:
            return Outcome(Status.TIMEOUT, f"over {TIMEOUT_SECONDS} s", time.monotonic() - started)
        return replace(outcome, seconds=time.monotonic() - started)
    finally:
        shutil.rmtree(copy, ignore_errors=True)


# --- reporting --------------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    """One mutant's line of the report."""

    id: str
    category: str
    path: str
    bug: str
    clause: str
    tests: tuple[str, ...]
    equivalent: str | None
    verdict: str
    detail: str
    seconds: float
    snapshots: tuple[str, ...] = ()


def record(mutant: Mutant, outcome: Outcome) -> Record:
    """The report line of ``mutant`` with ``outcome``."""
    return Record(
        id=mutant.id,
        category=mutant.category,
        path=mutant.path,
        bug=mutant.bug,
        clause=mutant.clause,
        tests=mutant.tests,
        equivalent=mutant.equivalent,
        verdict=verdict(mutant, outcome),
        detail=outcome.detail,
        seconds=round(outcome.seconds, 1),
        snapshots=outcome.snapshots,
    )


def summary(records: Sequence[Record]) -> dict[str, object]:
    """Totals per verdict, per category, and the kill rate (module docstring, *Report*)."""
    counts = Counter(r.verdict for r in records)
    decided = counts["killed"] + counts["snapshot-only"] + counts["survived"]
    rate = counts["killed"] / decided if decided else 1.0
    per_category: dict[str, Counter[str]] = {}
    for r in records:
        per_category.setdefault(r.category, Counter())[r.verdict] += 1
    return {
        "mutants": len(records),
        "verdicts": dict(sorted(counts.items())),
        "behavioural_kill_rate": round(rate, 4),
        "categories": {name: dict(sorted(c.items())) for name, c in sorted(per_category.items())},
    }


def clean(records: Sequence[Record]) -> bool:
    """Whether a campaign needs no action: every mutant killed or equivalent."""
    return all(r.verdict in ("killed", "equivalent") for r in records)


def format_table(records: Sequence[Record]) -> str:
    """A fixed-width table of the records."""
    width = max((len(r.id) for r in records), default=2)
    lines = [f"{'id':<{width}}  {'verdict':<13} {'s':>6}  first failing test"]
    for r in records:
        extra = f"  (+{len(r.snapshots)} snapshot)" if r.snapshots else ""
        lines.append(f"{r.id:<{width}}  {r.verdict:<13} {r.seconds:>6.1f}  {r.detail}{extra}")
    return "\n".join(lines)


def check_list(mutants: Sequence[Mutant]) -> list[str]:
    """Problems of a list of mutants: duplicate ids, empty fields, a test path that does not
    exist under the repository."""
    problems: list[str] = []
    for identifier, count in Counter(m.id for m in mutants).items():
        if count > 1:
            problems.append(f"duplicate id {identifier}")
    for m in mutants:
        for name in ("id", "category", "path", "bug", "clause"):
            if not getattr(m, name):
                problems.append(f"{m.id}: empty {name}")
        if not m.tests:
            problems.append(f"{m.id}: no tests")
        for test in m.tests:
            if not (REPOSITORY / test.split("::")[0]).exists():
                problems.append(f"{m.id}: no test path {test}")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    """Command line (module docstring); exit status 1 unless the campaign is clean."""
    parser = argparse.ArgumentParser(
        prog="mutants.py", description="Mutation campaign over the product semantics."
    )
    parser.add_argument("ids", nargs="*", help="mutant ids to run (default: all)")
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "sva2yosys-mutants",
        help="where the copies are made (outside the repository)",
    )
    parser.add_argument("--jobs", type=int, default=1, help="mutants run in parallel")
    parser.add_argument("--no-baseline", action="store_true", help="skip the unmutated run")
    parser.add_argument("--keep", action="store_true", help="keep each mutated copy")
    parser.add_argument("--canary", action="store_true", help="run the import canary only")
    parser.add_argument(
        "--external-tools",
        default="all",
        help="external tools the mutant runs use: all (default), none, or a comma-separated list",
    )
    parser.add_argument(
        "--external-corpora",
        default="all",
        help="external corpora the mutant runs use: all (default), none, or a list",
    )
    parser.add_argument("--list", action="store_true", help="list the mutants and exit")
    arguments = parser.parse_args(argv)
    known = {m.id: m for m in MUTANTS}
    unknown = [name for name in arguments.ids if name not in known]
    if unknown:
        parser.error(f"unknown mutant {unknown}")
    chosen = [known[name] for name in arguments.ids] if arguments.ids else list(MUTANTS)
    if arguments.list:
        for m in chosen:
            print(f"{m.id}  [{m.category}]  {m.bug}")
        return 0
    try:
        parse_selection(arguments.external_tools, (t.name for t in TOOLS), "--external-tools")
        parse_selection(arguments.external_corpora, (c.name for c in CORPORA), "--external-corpora")
    except ValueError as error:
        parser.error(str(error))
    tools, corpora = arguments.external_tools, arguments.external_corpora
    workdir = arguments.workdir.resolve()
    if _inside(workdir, REPOSITORY):
        parser.error("--workdir must be outside the repository")
    workdir.mkdir(parents=True, exist_ok=True)
    if arguments.canary:
        try:
            outcome = run(
                CANARY, workdir, keep=arguments.keep, external_tools=tools, external_corpora=corpora
            )
        except SetupError as error:
            print(f"setup failure (no verdict): {error}", file=sys.stderr)
            return 2
        good = outcome.status is Status.INVALID and "canary" in outcome.detail
        print(f"canary: {outcome.status.value} ({outcome.detail}) -> {'ok' if good else 'BROKEN'}")
        return 0 if good else 1
    problems = check_list(chosen)
    if problems:
        parser.error("; ".join(problems))
    if not arguments.no_baseline:
        try:
            base = baseline(workdir, chosen, external_tools=tools, external_corpora=corpora)
        except SetupError as error:
            print(f"setup failure (no verdict): {error}", file=sys.stderr)
            return 2
        if base.status is not Status.SURVIVED:
            print(f"baseline failed: {base.status.value} {base.detail}", file=sys.stderr)
            return 1
        print(f"baseline passed in {base.seconds:.0f} s")
    try:
        with ThreadPoolExecutor(max_workers=max(1, arguments.jobs)) as pool:
            outcomes = list(
                pool.map(
                    lambda m: run(
                        m,
                        workdir,
                        keep=arguments.keep,
                        external_tools=tools,
                        external_corpora=corpora,
                    ),
                    chosen,
                )
            )
    except SetupError as error:
        print(f"setup failure (no verdict): {error}", file=sys.stderr)
        return 2
    records = [record(m, o) for m, o in zip(chosen, outcomes, strict=True)]
    print(format_table(records))
    totals = summary(records)
    print(json.dumps(totals, indent=2))
    if arguments.report is not None:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        payload = {"summary": totals, "mutants": [asdict(r) for r in records]}
        arguments.report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0 if clean(records) else 1


if __name__ == "__main__":
    sys.exit(main())
