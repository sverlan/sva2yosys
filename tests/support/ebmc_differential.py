"""Test support: the EBMC differential tier.

An independent implementation (EBMC 6.1, with Spot's ``ltl2tgba`` for its Buechi route) reads the
same IEEE 1800 text on a calibrated subset of the hand-derived trace cases of
``tests/functional_tests/reference_semantics/fixtures``. This module selects those cases,
turns each into an EBMC design that replays the trace, runs EBMC and compares **trace
validity** with the case's table, never failure times. The area test
(``tests/functional_tests/external/ebmc_differential``) and the draft command use it::

    uv run --env-file .env python tests/support/ebmc_differential.py --draft <out.tsv> [--jobs N]

EBMC's answers are never ground truth (``tests/functional_tests/external/CONTRACT.md`` 5): a
difference is derived by hand from IEEE 1800 and either corrects our table (a counterexample
case) or adds an exclusion with its reason. The study that fixed the exclusions is private
(EBMC's reading of ``disable iff``, sampled-value functions, pending matches, ...); the
checked-in allow-list (``fixtures/allow_list.txt`` of the area) is its result.

Contract
--------
* **Engines.** :class:`Engine` is ``BMC`` (``--bound``; finite and bounded properties),
  ``BDD`` (``--bdd``; unbounded, for the properties that engine accepts) or ``BUECHI``
  (``--bdd --buechi``, which needs Spot's ``ltl2tgba`` on the PATH of EBMC's process). ``BDD``
  and ``BUECHI`` only compare cases with a ``loop`` (a lasso: the infinite trace).
* **Allow-list.** :func:`parse_allow_list` reads the checked-in text (format in the file
  header). Nothing is compared unless the list admits it. :func:`select` returns, for a case,
  either one :class:`Selection` per engine that compares it or one that says why it is
  **not compared**. In order: an explicit ``skip`` line (its reason); an ``exclude`` line
  whose slug is the reason; the explicit ``include`` lines and the first ``== shape`` of each
  engine whose kind, vocabulary and ``requires`` admit the case; otherwise ``unlisted-shape``.
  A shape admits a case when the case's kind is among the shape's kinds, the case is a lasso
  for ``BDD`` / ``BUECHI``, every token of the property (:func:`property_tokens`) is in the
  shape's vocabulary, when the shape has a ``requires`` list at least one of its tokens is
  among the case's, and none of the ``forbids`` tokens is. The tokens of a property are: its
  words other than the case's signals; the operators ``|->`` and ``|=>``; ``##N``,
  ``##[m:n]``, ``##[m:$]``; ``[*N]``, ``[*m:n]``,
  ``[*m:$]`` (also ``[*]`` and ``[+]``); ``[->N]``, ``[->m:n]``, ``[->m:$]`` and the same
  for ``[=``; ``[n]``, ``[m:n]``, ``[m:$]`` after ``always``, ``s_always``, ``eventually``,
  ``s_eventually``, ``nexttime``, ``s_nexttime``; and any other punctuation character that
  is not Boolean (so a bit select, a concatenation or a set makes a property unlisted).
  Digits and sized literals are not tokens. The pseudo tokens ``@disable`` (a ``disable iff``),
  ``@procedure`` (an ``initial`` procedure), ``@assume`` (kind ``assume``), ``@cover`` (kind
  ``cover``), ``@xz-literal`` (an ``x``, ``z`` or ``?`` digit in a literal), ``@recurrence``
  (``always`` before ``s_eventually`` in the text) and ``@eventually-always`` (``s_eventually``
  directly followed by ``always``) exist for the ``exclude``, ``requires`` and ``forbids`` lines;
  they are not part of the vocabulary check.
* **Design.** :func:`design_source` is a module ``top`` with an input ``clk``, a counter and
  one wire per signal that is a case-table lookup on it. A finite case (``BMC``) uses a
  saturating sample counter ``tick`` (sample ``k`` is ``tick == k``; after the written
  samples the last values hold) and one directive ``p`` of the case's kind and property,
  clocked by ``@(posedge clk)``. A lasso case (``BDD``, ``BUECHI``) uses a ``phase`` counter
  that wraps from the last written sample to the case's ``loop`` sample, so the signals follow
  the infinite trace; ``BDD`` has the one directive ``p``; ``BUECHI`` has, for every
  written sample ``k``, ``start_k: assert property (@(posedge clk) (tick == k) |-> (<property>))``
  where ``tick`` counts the first visits (saturating after the written samples), so that each
  attempt is checked alone and the implicit ``always`` that EBMC adds to a directive does not
  turn the property into a recurrence.
* **Command.** :func:`ebmc_options` gives the arguments after the design path:
  ``--top top --bound B`` with ``B`` = written samples - 1 for ``BMC`` (EBMC's bound counts
  transitions, so states ``0 .. B`` are exactly the written samples: a failure within the
  written samples and nothing of the held suffix), ``--top top --bdd`` and
  ``--top top --bdd --buechi``. :func:`run_job` runs it with the work directory as working
  directory; the design path is spelled for the tool by ``path_for_tool`` (WSL). For
  ``BUECHI`` the directory of ``ltl2tgba`` is put first on ``PATH`` of the subprocess only
  (for WSL, a shell inside Linux prepends the translated directory before executing EBMC;
  this does not depend on importing Windows ``PATH``).
* **Answers.** :func:`parse_ebmc_output` reads the ``** Results:`` lines
  ``[top.<label>] <property>: <STATUS>`` into ``{label: STATUS}``. For an ``assert``:
  ``REFUTED`` is a failure, ``PROVED`` and ``PROVED up to bound N`` a pass, anything else
  (``INCONCLUSIVE``, ``UNSUPPORTED...``, ``REFUTED up to bound N``) is *not decided*. For a
  ``cover``: ``PROVED`` is reached and ``REFUTED up to bound N`` (also ``REFUTED``) not
  reached, anything else is not decided.
* **Expected.** :func:`expected_verdicts` gives what the case's table says, per label, and
  never a failure time. ``BMC`` assert: ``p`` fails iff the case has a ``fail`` mark in the
  written samples; ``BMC`` cover: ``p`` is reached iff the case has a ``pass`` mark. ``BDD``:
  ``p`` fails iff an ``inf-outcome`` is ``F`` (the raw directive covers every attempt of the
  infinite trace). ``BUECHI``: ``start_k`` fails iff ``inf-outcome[k]`` is ``F``, passes if
  ``T``; a ``D`` (disabled) attempt is not compared.
* **Classes.** :func:`judge` returns ``agrees`` (every compared label equal),
  ``disagrees`` (some label differs; the detail lists them), ``not compared`` (EBMC did not
  decide a label, or timed out: ``engine-undecided: ...``) or ``error`` (no result lines:
  EBMC failed on the design; the detail is its last line). There is no automatic
  reclassification.
* **Digest.** :func:`traces_digest` is the SHA-256 of every ``*.trace`` file of a directory
  (name and content, line ends normalised to LF, in name order), the fixture-digest rule of
  ``tests/functional_tests/external/CONTRACT.md`` 3 applied to this repository's own traces.
* **Versions.** :func:`ebmc_version` (``6.1 (ebmc-6.1)``) and :func:`spot_version`
  (``2.16.1``) read the tools' own ``--version`` output.
* **Draft.** ``--draft`` runs every selected (case, engine) with threads and writes a TSV
  (case, engine, shape, class, detail, ours, ebmc, seconds, property); it never classifies.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

if __package__ in (None, ""):  # run as a script: make the `support` package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support import trace_driver
from support.toolchain import (
    ENV_EBMC,
    ENV_LTL2TGBA,
    LTL2TGBA,
    Tool,
    ToolError,
    path_for_tool,
    run_tool,
    tool_directory_from_env,
    tool_from_env,
)
from support.tracefile import TraceCase, load_cases

TRACE_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "functional_tests" / "reference_semantics" / "fixtures"
)
"""The hand-derived trace tables."""

AGREES = "agrees"
DISAGREES = "disagrees"
NOT_COMPARED = "not compared"
ERROR = "error"
CLASSES = (AGREES, DISAGREES, NOT_COMPARED, ERROR)


class Engine(Enum):
    """EBMC engines used by the tier (the value is the name used in the allow-list)."""

    BMC = "bmc"
    BDD = "bdd"
    BUECHI = "buechi"


# --- the allow-list ------------------------------------------------------------------------


@dataclass(frozen=True)
class Exclusion:
    """An ``exclude <slug> <group> ...`` line: a case is excluded when its tokens contain
    every token of at least one group (tokens of a group are joined with ``+``)."""

    slug: str
    groups: tuple[frozenset[str], ...]


@dataclass(frozen=True)
class Shape:
    """A ``== name`` block of the allow-list: one calibrated family of properties."""

    name: str
    engine: Engine
    kinds: frozenset[str]
    vocabulary: frozenset[str]
    requires: frozenset[str]
    forbids: frozenset[str]
    positive: str
    negative: str
    note: str


@dataclass(frozen=True)
class AllowList:
    """The parsed allow-list."""

    exclusions: tuple[Exclusion, ...]
    shapes: tuple[Shape, ...]
    skips: dict[str, str]
    includes: dict[tuple[Engine, str], str]


class AllowListError(ValueError):
    """The allow-list text is malformed; the message names the line."""


def parse_allow_list(text: str) -> AllowList:
    """Parse the allow-list.

    Lines (``#`` starts a comment when at the start of the line or after a space)::

        exclude <slug> <group> [<group> ...]    # tokens joined by '+'
        skip <case> <reason ...>
        include <engine> <case> <reason ...>
        == <shape>
        engine: bmc | bdd | buechi
        kinds: assert cover
        vocabulary: <token> ...
        requires: <token> ...  # optional: the case must have at least one of them
        forbids: <token> ...   # optional: the case must have none of them
        positive: <case>      # a case the shape admits, with no failure/not reached, that agrees
        negative: <case>      # a case the shape admits, with a failure/reached, that agrees
        note: <free text>

    A case is named ``<trace file stem>:<case name>`` (case names are unique per file only).

    Raises:
        AllowListError: Unknown line, missing key, a bad engine, or a duplicate shape.
    """
    exclusions: list[Exclusion] = []
    skips: dict[str, str] = {}
    includes: dict[tuple[Engine, str], str] = {}
    blocks: list[tuple[str, dict[str, str]]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = re.split(r"(?:^|\s)#(?:\s|$)", raw, maxsplit=1)[0].strip()
        if not line:
            continue
        where = f"allow list line {number}"
        if line.startswith("=="):
            blocks.append((line[2:].strip(), {}))
        elif line.startswith("exclude "):
            parts = line.split()
            if len(parts) < 3:
                raise AllowListError(f"{where}: 'exclude <slug> <group> ...'")
            groups = tuple(frozenset(group.split("+")) for group in parts[2:])
            exclusions.append(Exclusion(parts[1], groups))
        elif line.startswith("skip "):
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                raise AllowListError(f"{where}: 'skip <case> <reason>'")
            skips[parts[1]] = parts[2]
        elif line.startswith("include "):
            parts = line.split(maxsplit=3)
            if len(parts) < 4:
                raise AllowListError(f"{where}: 'include <engine> <case> <reason>'")
            includes[(_engine(parts[1], where), parts[2])] = parts[3]
        elif blocks and ":" in line:
            key, _, value = line.partition(":")
            blocks[-1][1][key.strip()] = value.strip()
        else:
            raise AllowListError(f"{where}: unknown line {line!r}")
    shapes: list[Shape] = []
    for name, keys in blocks:
        for required in ("engine", "kinds", "vocabulary", "positive", "negative"):
            if required not in keys:
                raise AllowListError(f"shape {name!r}: missing {required!r}")
        if any(shape.name == name for shape in shapes):
            raise AllowListError(f"duplicate shape {name!r}")
        shapes.append(
            Shape(
                name=name,
                engine=_engine(keys["engine"], f"shape {name!r}"),
                kinds=frozenset(keys["kinds"].split()),
                vocabulary=frozenset(keys["vocabulary"].split()),
                requires=frozenset(keys.get("requires", "").split()),
                forbids=frozenset(keys.get("forbids", "").split()),
                positive=keys["positive"],
                negative=keys["negative"],
                note=keys.get("note", ""),
            )
        )
    return AllowList(tuple(exclusions), tuple(shapes), skips, includes)


def _engine(text: str, where: str) -> Engine:
    try:
        return Engine(text)
    except ValueError:
        raise AllowListError(f"{where}: unknown engine {text!r}") from None


# --- the tokens of a property --------------------------------------------------------------

_LITERAL = re.compile(r"\d*'[sS]?[bBdDhHoO][0-9a-fA-FxXzZ_?]+")
_XZ_DIGITS = re.compile(r"\d*'[sS]?[bBdDhHoO][0-9a-fA-F_]*[xXzZ?]")
_DELAY = re.compile(r"##(?:\[(\d+):(\d+|\$)\]|\d+)")
_REPETITION = re.compile(r"\[(\*|->|=)(\d*)(?::(\d+|\$))?\]|\[\+\]")
_RANGE = re.compile(r"\[\d+(?::(\d+|\$))?\]")
_WORD = re.compile(r"\$?[A-Za-z_][A-Za-z0-9_$]*")
_OPERATOR = re.compile(r"\|->|\|=>")
_RANGED_WORDS = frozenset(
    {"always", "s_always", "eventually", "s_eventually", "nexttime", "s_nexttime"}
)
_BOOLEAN_PUNCTUATION = set("()!&|^~=<>+-*/%?:,. 	")


def property_tokens(case: TraceCase) -> frozenset[str]:
    """The tokens of ``case``'s property and the pseudo tokens (see the module contract)."""
    text = case.property
    tokens: set[str] = set()
    if case.disable is not None:
        tokens.add("@disable")
    if case.procedure is not None:
        tokens.add("@procedure")
    if case.kind == "assume":
        tokens.add("@assume")
    if case.kind == "cover":
        tokens.add("@cover")
    if _XZ_DIGITS.search(text):
        tokens.add("@xz-literal")
    if re.search(r"\balways\b.*\bs_eventually\b", text):
        tokens.add("@recurrence")
    if re.search(r"\bs_eventually\s+always\b", text):
        tokens.add("@eventually-always")
    last_word = ""
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if (match := _LITERAL.match(text, index)) is not None:
            index = match.end()
            continue
        if (match := _DELAY.match(text, index)) is not None:
            if match.group(1) is None:
                tokens.add("##N")
            else:
                tokens.add("##[m:$]" if match.group(2) == "$" else "##[m:n]")
            index = match.end()
            continue
        if (match := _REPETITION.match(text, index)) is not None:
            tokens.add(_repetition_token(match))
            index = match.end()
            continue
        if last_word in _RANGED_WORDS and (match := _RANGE.match(text, index)) is not None:
            bound = match.group(1)
            tokens.add("[n]" if bound is None else "[m:$]" if bound == "$" else "[m:n]")
            index = match.end()
            last_word = ""
            continue
        if (match := _WORD.match(text, index)) is not None:
            if match.group() not in case.widths:
                tokens.add(match.group())
            last_word = match.group()
            index = match.end()
            continue
        last_word = ""
        if (match := _OPERATOR.match(text, index)) is not None:
            tokens.add(match.group())
            index = match.end()
            continue
        if not (char.isdigit() or char in _BOOLEAN_PUNCTUATION):
            tokens.add(char)
        index += 1
    return frozenset(tokens)


def _repetition_token(match: re.Match[str]) -> str:
    """``[*N]``, ``[*m:n]``, ``[*m:$]``, ``[->N]``, ... (``[*]`` and ``[+]`` are ``[*m:$]``)."""
    operator, low, high = match.group(1), match.group(2), match.group(3)
    if operator is None:  # [+]
        return "[*m:$]"
    if high is None:
        return "[*m:$]" if operator == "*" and not low else f"[{operator}N]"
    return f"[{operator}m:{'$' if high == '$' else 'n'}]"


# --- selecting the cases -------------------------------------------------------------------


@dataclass(frozen=True)
class Selection:
    """What the allow-list decides for one case on one engine.

    Attributes:
        case: The case.
        engine: The engine, or ``None`` when the case is not compared.
        shape: The admitting shape (``explicit`` for an ``include`` line), or ``-``.
        reason: Why the case is not compared (an exclusion slug, ``unlisted-shape``, or the
            text of a ``skip`` line); empty when it is compared.
    """

    case: TraceCase
    engine: Engine | None
    shape: str
    reason: str

    @property
    def key(self) -> str:
        """The name used in the expectations table: ``<trace file stem>:<case name>``."""
        return case_key(self.case)


def case_key(case: TraceCase) -> str:
    """The name of a case in the allow-list and the expectations table."""
    return f"{Path(case.file).stem}:{case.name}"


def select(case: TraceCase, allow: AllowList) -> tuple[Selection, ...]:
    """The selections of ``case`` (module contract, *Allow-list*)."""
    key = case_key(case)
    if key in allow.skips:
        return (Selection(case, None, "-", allow.skips[key]),)
    tokens = property_tokens(case)
    for exclusion in allow.exclusions:
        if any(group <= tokens for group in exclusion.groups):
            return (Selection(case, None, "-", exclusion.slug),)
    selections: list[Selection] = []
    for engine in Engine:
        if (engine, key) in allow.includes:
            selections.append(Selection(case, engine, "explicit", ""))
            continue
        if engine is not Engine.BMC and case.loop is None:
            continue
        for shape in allow.shapes:
            if (
                shape.engine is engine
                and case.kind in shape.kinds
                and _plain(tokens) <= shape.vocabulary
                and (not shape.requires or bool(tokens & shape.requires))
                and not tokens & shape.forbids
            ):
                selections.append(Selection(case, engine, shape.name, ""))
                break
    if not selections:
        return (Selection(case, None, "-", "unlisted-shape"),)
    return tuple(selections)


def _plain(tokens: frozenset[str]) -> frozenset[str]:
    """The tokens without the pseudo tokens (the vocabulary check ignores those)."""
    return frozenset(token for token in tokens if not token.startswith("@"))


def load_all_cases(directory: Path = TRACE_DIRECTORY) -> list[TraceCase]:
    """Every case of every ``*.trace`` file of ``directory``, in file and line order."""
    return [case for path in sorted(directory.glob("*.trace")) for case in load_cases(path)]


def traces_digest(directory: Path = TRACE_DIRECTORY) -> str:
    """SHA-256 of the ``*.trace`` files of ``directory`` (module contract, *Digest*)."""
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.trace")):
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(path.name.encode() + b"\0" + content + b"\0")
    return digest.hexdigest()


# --- the design and the command ------------------------------------------------------------


def design_source(case: TraceCase, engine: Engine) -> str:
    """The EBMC design that replays ``case`` (module contract, *Design*)."""
    samples = len(case.samples)
    width = trace_driver.counter_width(samples)
    lines = [
        f"// {case.file}:{case.line} {case.name}",
        "module top(input logic clk);",
    ]
    if engine is Engine.BMC:
        lines.extend(trace_driver.counter_lines(case))
        selector = "tick"
    else:
        assert case.loop is not None, "a BDD or Buechi design needs a lasso case"
        lines.extend(trace_driver.phase_lines(case))
        selector = "phase"
        if engine is Engine.BUECHI:
            lines.extend(trace_driver.counter_lines(case))
    lines.extend(trace_driver.stimulus_lines(case, selector))
    if engine is Engine.BUECHI:
        for k in range(samples):
            lines.append(
                f"  start_{k}: assert property (@(posedge clk) "
                f"(tick == {width}'d{k}) |-> ({case.property}));"
            )
    else:
        lines.append(f"  {trace_driver.directive_line(case, include_disable=False)}")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def ebmc_options(case: TraceCase, engine: Engine) -> list[str]:
    """The EBMC arguments after the design path (module contract, *Command*)."""
    match engine:
        case Engine.BMC:
            return ["--top", "top", "--bound", str(len(case.samples) - 1)]
        case Engine.BDD:
            return ["--top", "top", "--bdd"]
        case Engine.BUECHI:
            return ["--top", "top", "--bdd", "--buechi"]


# --- answers and verdicts ------------------------------------------------------------------

_RESULT = re.compile(r"^\[top\.([A-Za-z_][A-Za-z0-9_]*)\] .*: ([A-Z].*?)\s*$")


def parse_ebmc_output(text: str) -> dict[str, str]:
    """``{label: STATUS}`` from the ``** Results:`` lines of EBMC's output (empty when there
    is no such section)."""
    results: dict[str, str] = {}
    in_results = False
    for line in text.splitlines():
        if line.startswith("** Results:"):
            in_results = True
            continue
        if in_results and (match := _RESULT.match(line.strip())):
            results[match.group(1)] = match.group(2)
    return results


def observed_verdict(kind: str, status: str) -> str | None:
    """EBMC's status as a verdict: ``fail`` / ``pass`` for an assert, ``reached`` /
    ``unreached`` for a cover, ``None`` when EBMC did not decide."""
    if kind == "cover":
        if status == "PROVED":
            return "reached"
        return "unreached" if status.startswith("REFUTED") else None
    if status == "REFUTED":
        return "fail"
    return "pass" if status.startswith("PROVED") else None


def expected_verdicts(case: TraceCase, engine: Engine) -> dict[str, str]:
    """What the case's table says, per label (module contract, *Expected*)."""
    match engine:
        case Engine.BMC if case.kind == "cover":
            return {"p": "reached" if case.passes else "unreached"}
        case Engine.BMC:
            return {"p": "fail" if case.fails else "pass"}
        case Engine.BDD:
            assert case.inf_outcomes is not None, "a lasso case has inf-outcome"
            return {"p": "fail" if "F" in case.inf_outcomes else "pass"}
        case Engine.BUECHI:
            assert case.inf_outcomes is not None, "a lasso case has inf-outcome"
            return {
                f"start_{k}": "fail" if token == "F" else "pass"
                for k, token in enumerate(case.inf_outcomes)
                if token in "TF"
            }


@dataclass(frozen=True)
class Judgement:
    """The class of one comparison and what the two sides said."""

    cls: str
    detail: str
    ours: str
    ebmc: str


def judge(case: TraceCase, engine: Engine, statuses: dict[str, str], tail: str) -> Judgement:
    """Compare EBMC's ``statuses`` with the case's table (module contract, *Classes*).

    ``tail`` is the last line of EBMC's output, the detail of an ``error``.
    """
    expected = expected_verdicts(case, engine)
    ours = " ".join(f"{label}={verdict}" for label, verdict in expected.items())
    if not statuses:
        return Judgement(ERROR, tail, ours, "-")
    theirs = " ".join(f"{label}={statuses.get(label, 'missing')}" for label in expected)
    undecided: list[str] = []
    differing: list[str] = []
    for label, verdict in expected.items():
        status = statuses.get(label)
        observed = observed_verdict(case.kind, status) if status is not None else None
        if observed is None:
            undecided.append(f"{label}: {status if status is not None else 'missing'}")
        elif observed != verdict:
            differing.append(f"{label}: ours {verdict}, ebmc {observed}")
    if differing:
        return Judgement(DISAGREES, "; ".join(differing), ours, theirs)
    if undecided:
        return Judgement(NOT_COMPARED, "engine-undecided: " + "; ".join(undecided), ours, theirs)
    return Judgement(AGREES, "", ours, theirs)


# --- running EBMC --------------------------------------------------------------------------


def run_job(
    ebmc: Tool,
    spot_dir: Path | None,
    case: TraceCase,
    engine: Engine,
    workdir: Path,
    *,
    timeout: float = 60.0,
) -> Judgement:
    """Write the design of ``case`` for ``engine`` into ``workdir``, run EBMC on it and
    judge the answer. A timeout is ``not compared engine-undecided: timeout``.

    Raises:
        ValueError: ``engine`` is ``BUECHI`` and ``spot_dir`` is ``None``.
    """
    if engine is Engine.BUECHI and spot_dir is None:
        raise ValueError("the Buechi engine needs the directory of ltl2tgba")
    workdir.mkdir(parents=True, exist_ok=True)
    design = workdir / "design.sv"
    design.write_text(design_source(case, engine), encoding="utf-8", newline="\n")
    args = [*ebmc.command, path_for_tool(ebmc, design), *ebmc_options(case, engine)]
    prefix = str(spot_dir) if engine is Engine.BUECHI and spot_dir is not None else None
    expected = expected_verdicts(case, engine)
    ours = " ".join(f"{label}={verdict}" for label, verdict in expected.items())
    try:
        completed = run_tool(ebmc, args, workdir, timeout, "ebmc", path_prefix=prefix)
    except ToolError as error:
        if "timed out" in str(error):
            return Judgement(NOT_COMPARED, "engine-undecided: timeout", ours, "-")
        return Judgement(ERROR, str(error), ours, "-")
    output = completed.stdout + completed.stderr
    lines = [line for line in output.splitlines() if line.strip()]
    return judge(case, engine, parse_ebmc_output(output), lines[-1] if lines else "no output")


def ebmc_version(ebmc: Tool, workdir: Path) -> str:
    """EBMC's version text (``6.1 (ebmc-6.1)``), the last non-blank line of ``--version``."""
    completed = run_tool(ebmc, [*ebmc.command, "--version"], workdir, 60.0, "ebmc --version")
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise ToolError(f"{ebmc.variable}: ebmc --version printed nothing")
    return lines[-1]


def spot_command(ebmc: Tool, spot_dir: Path) -> list[str]:
    """The command running Spot's ``ltl2tgba`` the way EBMC's process sees it: the same WSL
    prefix as ``ebmc`` (through ``-e``) when EBMC is run through WSL, else the file itself."""
    executable = spot_dir / LTL2TGBA
    if Path(ebmc.command[0]).stem.lower() != "wsl":
        return [str(executable)]
    prefix = list(ebmc.command[:1])
    for flag in ("-e", "--exec"):
        if flag in ebmc.command:
            prefix = list(ebmc.command[: ebmc.command.index(flag) + 1])
            break
    return [*prefix, path_for_tool(ebmc, executable)]


def spot_version(ebmc: Tool, spot_dir: Path, workdir: Path) -> str:
    """Spot's version (``2.16.1``) from ``ltl2tgba --version``.

    Raises:
        ToolError: The output has no ``(spot) <version>`` line.
    """
    command = [*spot_command(ebmc, spot_dir), "--version"]
    completed = run_tool(ebmc, command, workdir, 60.0, "ltl2tgba --version")
    found = re.search(r"\(spot\) (\S+)", completed.stdout)
    if found is None:
        raise ToolError(f"ltl2tgba --version printed no version: {completed.stdout!r}")
    return found.group(1)


# --- the draft -----------------------------------------------------------------------------

TSV_HEADER = ("case", "engine", "shape", "class", "detail", "ours", "ebmc", "seconds", "property")


def _draft_row(
    ebmc: Tool, spot_dir: Path | None, selection: Selection, work: Path, timeout: float
) -> tuple[str, ...]:
    case = selection.case
    if selection.engine is None:
        return (
            selection.key,
            "-",
            "-",
            NOT_COMPARED,
            selection.reason,
            "-",
            "-",
            "0",
            case.property,
        )
    started = time.monotonic()
    judgement = run_job(
        ebmc,
        spot_dir,
        case,
        selection.engine,
        work / selection.engine.value / re.sub(r"[^A-Za-z0-9_.-]", "_", selection.key),
        timeout=timeout,
    )
    return (
        selection.key,
        selection.engine.value,
        selection.shape,
        judgement.cls,
        judgement.detail,
        judgement.ours,
        judgement.ebmc,
        f"{time.monotonic() - started:.1f}",
        case.property,
    )


def draft(
    ebmc: Tool,
    spot_dir: Path | None,
    allow: AllowList,
    out: Path,
    work: Path,
    *,
    jobs: int = 4,
    timeout: float = 30.0,
    only: Sequence[str] = (),
) -> int:
    """Run every selected (case, engine) and write the provisional table to ``out``."""
    selections = [
        selection
        for case in load_all_cases()
        for selection in select(case, allow)
        if not only or selection.key in only
    ]
    started = time.monotonic()
    rows: list[tuple[str, ...]] = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [
            pool.submit(_draft_row, ebmc, spot_dir, selection, work, timeout)
            for selection in selections
        ]
        for done, future in enumerate(futures, 1):
            rows.append(future.result())
            if done % 25 == 0 or done == len(futures):
                print(f"{done}/{len(futures)} rows, {time.monotonic() - started:.0f} s", flush=True)
    lines = ["\t".join(TSV_HEADER)]
    for row in rows:
        lines.append("\t".join(cell.replace("\t", " ").replace("\n", " ") for cell in row))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        counts[(row[1], row[3])] = counts.get((row[1], row[3]), 0) + 1
    for (engine, cls), count in sorted(counts.items()):
        print(f"{engine:8} {cls:13} {count}")
    print(f"wrote {out} in {time.monotonic() - started:.0f} s")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Command line: ``--draft <out.tsv>`` (EBMC and Spot come from ``.env``)."""
    parser = argparse.ArgumentParser(description="Draft table of sva2yosys against EBMC.")
    parser.add_argument("--draft", type=Path, required=True, help="output TSV file")
    parser.add_argument(
        "--allow-list",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "functional_tests"
        / "external"
        / "ebmc_differential"
        / "fixtures"
        / "allow_list.txt",
    )
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--only", nargs="*", default=(), help="case keys (default: all)")
    parser.add_argument("--work", type=Path, default=Path(tempfile.gettempdir()) / "ebmc-diff-work")
    arguments = parser.parse_args(argv)
    ebmc = tool_from_env(ENV_EBMC, os.environ, command=True)
    root = Path(__file__).resolve().parents[2]
    spot_dir = tool_directory_from_env(ENV_LTL2TGBA, os.environ, root, LTL2TGBA)
    if ebmc is None or spot_dir is None:
        parser.error(f"{ENV_EBMC} and {ENV_LTL2TGBA} must be set (uv run --env-file .env)")
    allow = parse_allow_list(arguments.allow_list.read_text(encoding="utf-8"))
    return draft(
        ebmc,
        spot_dir,
        allow,
        arguments.draft,
        arguments.work,
        jobs=arguments.jobs,
        timeout=arguments.timeout,
        only=tuple(arguments.only),
    )


if __name__ == "__main__":
    sys.exit(main())
