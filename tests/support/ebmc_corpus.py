"""Test support: the EBMC regression suite as a known-answer corpus.

The corpus is ``regression/verilog/SVA`` of https://github.com/diffblue/hw-cbmc (tag
``ebmc-6.1``, BSD-3-Clause), fetched by ``tests/support/corpora.py`` (entry ``ebmc``) and
located by ``SVA2YOSYS_EBMC_TESTS``. Each case is a ``.desc`` file (EBMC's test description)
next to the design ``.sv`` it names, which holds both the RTL and its properties. This module
reads the descriptions and drives sva2yosys and SBY on the designs; the area test
(``tests/functional_tests/external/ebmc_corpus``) and the draft command below use it::

    uv run --env-file .env python tests/support/ebmc_corpus.py --draft <out.tsv> [--jobs N]

EBMC's answers encode EBMC's own semantics; nothing here treats them as ground truth. They are
compared with ours and every difference is classified by hand elsewhere.

Contract
--------
* ``parse_desc`` is pure. Line 1 is the tag (``CORE``, ``KNOWNBUG``, ...; later words are
  EBMC test-selection labels such as ``buechi`` and are kept apart), line 2 the design file,
  line 3 the options, then regular-expression lines of expected output up to a line ``--``
  (the lines after it, which must *not* appear, are ignored). From the options:
  ``bound`` (``--bound N``), ``top`` (``--module`` or ``--top``; default ``main``), ``engine``
  (``bdd``, ``k-induction`` or ``ic3`` when given, else ``bmc``), ``buechi`` (``--buechi``),
  ``options`` (all tokens). ``exit_code`` is the ``^EXIT=n$`` line, if any. A *verdict line*
  has the shape ``[<scope>.<label>] <property text>: <STATUS>``, written as a regular expression
  (a leading ``^``, escaped ``\\[ \\] \\. \\( \\$ \\*`` and a trailing ``$`` are undone, and
  only as far as needed to read the label and the status). Each yields an :class:`Expected`:
  ``scope`` (the module, text before the first dot), ``label`` (the rest, ``None`` for the
  wildcard ``[.*]``), ``kind`` (``COVER`` when the property text starts with ``cover ``,
  ``ASSUME`` when the status is ``ASSUMED``, else ``ASSERT``), ``status``
  (``PROVED``, ``PROVED_UP_TO_BOUND`` with ``bound`` = N, ``REFUTED`` -- also ``REFUTED up to
  bound N``, with ``bound`` = N, a cover not reached within N --, or ``OTHER``: any other
  status, ``ASSUMED``, ``INCONCLUSIVE``, an engine's refusal, ... : no known answer). The
  status ``PROVED .*`` (either way) is ``PROVED`` without bound. Other lines (``EXIT``,
  ``SIGNAL``, trace and diagnostic lines) are not verdicts.
* ``adapt_clock`` is pure and conservative. EBMC's unclocked concurrent assertions have no
  clock in IEEE 1800-2017 16.16; the adapter (test side only, sva2yosys is never changed)
  gives them one: only when the ``top`` module has a concurrent assertion without a clocking
  event of its own (none of ``@`` in its text, a named property declared without ``@``, and no
  event control earlier in its statement, and none on a ``begin ... end`` block that an
  event control governs, ``always @(...) begin ... end``, IEEE 1800-2017 16.16 c), no
  ``default clocking``, and no other module has concurrent assertions, it appends
  ``default clocking @(posedge <c>); endclocking`` before the module's ``endmodule`` (a default
  clocking is visible in the whole module; at the header it would precede the declaration
  of the clock), where ``<c>`` is the module's ``clk`` when it
  declares one (port, wire, reg, logic), else a new ``wire __ebmc_clk;`` added with it. In
  every other shape the text is returned unchanged. Returns the text and whether it changed.
* ``skip_reason`` is pure: a reason string for a case that is not compared, else ``None``:
  the tag is not ``CORE``; EBMC exits with status 2 (it rejects the input); an option tests
  EBMC itself (output formats, ``--aig``, ``--smv*``, ...); several clock events in the source
  (given as ``source``); no verdict with a known answer.
* ``plan_runs`` is pure: SBY runs per verdict. By engine and answer: an unbounded engine
  (``bdd``, ``k-induction``, ``ic3``), or an answer without a bound and no ``--bound``, is a
  ``PROVE`` run (``abc pdr``); a bounded answer, or ``--bound`` with a bounded engine, is a
  ``BMC`` run at depth bound + 1 (EBMC's bound counts transitions, SBY's depth states); a cover
  is a ``COVER`` run at depth bound + 1 (41 when it has no bound); a verdict from ``--buechi``
  or about a liveness operator also gets a ``LIVE`` run (the runner drops it when the directive
  has no liveness part). Verdicts without a known answer, and assumptions, have no run. Runs are
  grouped by mode and depth.
* ``classify`` is pure: the provisional class of one verdict against our outcome:
  ``agrees``, ``disagrees``, ``bound-gap`` (EBMC proved only up to a bound, our unbounded
  liveness run fails; or EBMC claims, without a bound, that a cover is never reached while our
  check is bounded to ``DEFAULT_COVER_DEPTH`` -- a reached cover is a witness and ``agrees``),
  or ``skipped``/``rejected``/``error`` as the outcome says.
* ``choose_top`` is pure: the case's top module: ``--module``/``--top`` if the source declares
  it, else the scope of the verdict lines, else ``main`` (each only if declared), else the
  first declared module that nobody instantiates.
* ``run_case`` is not pure. It copies and adapts the design into a work directory, runs the
  sva2yosys command line in process (check mode, then generation with ``-o``; the design file
  is passed as the verification file, ``--top`` is the top module), and returns one
  :class:`PropertyResult` per verdict. A directive is mapped to its verdict by its label (EBMC
  writes block names into it, ours does not: ``main.blk.p0`` is ``main.p0``); EBMC's unlabeled
  ones (``assert.N``, or the wildcard) by order. Immediate assertions are not directives: their
  verdicts have no match. When some directives are rejected and the others are accepted,
  the rejected ``assert``/``cover`` statements are blanked out of the source (never an
  ``assume``/``restrict``, whose removal would change the others' verdicts; ``drop_directives``)
  so that the others run (``isolated``); a case that cannot be isolated is rejected as a whole.
  The monitor sites of a directive come from the generated monitors file; each SBY run
  uses its own script (generated files by relative name, so that a WSL ``sby`` finds them).
  SBY reports only the assertions failing at the earliest failing step: the run is repeated for
  the others with the monitors of the failed ones switched off (``CHECK`` set to 0 in their
  instances). A failure of an assertion that is not one of our monitors (the design's own
  immediate assertion) is an ``error`` for the verdicts it leaves undecided. A failure without
  a monitor name (liveness, or a design without signals, where Yosys fails to convert the
  trace) is attributed by running the verdicts one by one, only when the design has no
  immediate assertion of its own, else it is an ``error``. A
  directive with a liveness part also gets the ``live`` run (the safety flow alone cannot see
  it); its SBY ``live`` run needs the sby-live tool.
* Nothing here imports pyslang; ``src/`` never imports this module.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from support.toolchain import Tool

RUNNABLE_TAG = "CORE"
DEFAULT_COVER_DEPTH = 41
"""Depth of a cover run whose answer carries no bound (EBMC's unbounded engines)."""
PROVE_TIMEOUT = 90.0
RUN_TIMEOUT = 120.0

_ENGINE_FLAGS = {"--bdd": "bdd", "--k-induction": "k-induction", "--ic3": "ic3"}
_EBMC_ITSELF = frozenset(
    {
        "--aig",
        "--smv",
        "--smv-word-level",
        "--dimacs",
        "--json-ui",
        "--xml-ui",
        "--vcd",
        "--waveform",
        "--show-properties",
        "--show-parse",
        "--show-trans",
        "--verilog",
        "--property",
        "--neural-engine",
        "--random-seed",
    }
)
_LIVENESS_TEXT = re.compile(r"s_eventually|s_until|s_always|eventually|strong\(|\bstrong\b")


class Kind(Enum):
    """What a verdict line is about."""

    ASSERT = "assert"
    ASSUME = "assume"
    COVER = "cover"


class Status(Enum):
    """EBMC's answer, reduced to what can be compared."""

    PROVED = "PROVED"
    PROVED_UP_TO_BOUND = "PROVED_UP_TO_BOUND"
    REFUTED = "REFUTED"
    OTHER = "OTHER"


@dataclass(frozen=True)
class Expected:
    """One expected verdict line of a ``.desc`` file."""

    scope: str | None
    """The module in the line's ``[scope.label]``; ``None`` for the wildcard ``[.*]``."""
    label: str | None
    """The label after the module (``p0``, ``blk.p0``, ``assert.2``); ``None`` for ``[.*]``."""
    kind: Kind
    status: Status
    bound: int | None
    """N of ``up to bound N``, else ``None``."""
    text: str
    """The property text of the line (regular-expression escapes undone)."""
    raw_status: str
    """The status text after the last ``: `` (escapes undone)."""

    @property
    def qualified(self) -> str | None:
        """``scope.label``, or ``None`` for the wildcard."""
        return None if self.scope is None or self.label is None else f"{self.scope}.{self.label}"

    @property
    def known(self) -> bool:
        """Whether the verdict has an answer to compare with."""
        return self.status is not Status.OTHER and self.kind is not Kind.ASSUME


@dataclass(frozen=True)
class Desc:
    """A parsed ``.desc`` file."""

    name: str
    tag: str
    tag_words: tuple[str, ...]
    design: str
    options: tuple[str, ...]
    bound: int | None
    top: str
    engine: str
    buechi: bool
    exit_code: int | None
    verdicts: tuple[Expected, ...]


class RunMode(Enum):
    """The SBY flow of a run."""

    BMC = "bmc"
    PROVE = "prove"
    COVER = "cover"
    LIVE = "live"


@dataclass(frozen=True)
class Run:
    """One SBY run: its flow, depth (bmc and cover) and the verdicts it answers (indices into
    :attr:`Desc.verdicts`)."""

    mode: RunMode
    depth: int | None
    verdicts: tuple[int, ...]

    @property
    def text(self) -> str:
        return self.mode.value if self.depth is None else f"{self.mode.value}@{self.depth}"


# --- the description ------------------------------------------------------------------------

_VERDICT_HEAD = re.compile(r"^\^?\\\[(?P<label>.*?)\\?\] ?(?P<rest>.*)$")
_STATUS = re.compile(r": (?P<word>[A-Z][A-Z_]{3,})\b(?P<tail>.*)$")
_BOUND = re.compile(r"up to bound (\d+)")
_ESCAPES = re.compile(r"\\(.)")


def _unescape(text: str) -> str:
    return _ESCAPES.sub(r"\1", text)


def _parse_verdict(line: str) -> Expected | None:
    head = _VERDICT_HEAD.match(line)
    if head is None:
        return None
    label_text = head.group("label")
    rest = head.group("rest")
    if rest.endswith("$"):
        rest = rest[:-1]
    matches = list(_STATUS.finditer(rest))
    if not matches:
        return None
    last = matches[-1]
    text = _unescape(rest[: last.start()])
    word = last.group("word")
    tail = last.group("tail")
    raw = _unescape(f"{word}{tail}").strip()
    bound_match = _BOUND.search(tail)
    bound = int(bound_match.group(1)) if bound_match else None
    if word == "PROVED":
        status = Status.PROVED_UP_TO_BOUND if bound is not None else Status.PROVED
    elif word == "REFUTED":
        status = Status.REFUTED
    else:
        status = Status.OTHER
    if word == "ASSUMED":
        kind = Kind.ASSUME
    elif text.startswith("cover "):
        kind = Kind.COVER
    else:
        kind = Kind.ASSERT
    scope: str | None
    label: str | None
    if label_text == ".*":
        scope, label = None, None
    else:
        qualified = _unescape(label_text)
        scope, _, label = qualified.partition(".")
        if not label:
            scope, label = None, qualified
    return Expected(scope, label, kind, status, bound, text, raw)


def parse_desc(text: str, name: str = "") -> Desc:
    """Parse a ``.desc`` file (see the module contract); ``name`` is the case's name."""
    lines = text.splitlines()
    words = tuple(lines[0].split()) if lines else ()
    design = lines[1].strip() if len(lines) > 1 else ""
    options = tuple(lines[2].split()) if len(lines) > 2 else ()
    bound: int | None = None
    top = "main"
    engine = "bmc"
    for index, token in enumerate(options):
        following = options[index + 1] if index + 1 < len(options) else ""
        if token == "--bound" and following.isdigit():
            bound = int(following)
        elif token in ("--module", "--top") and following:
            top = following
        elif token in _ENGINE_FLAGS:
            engine = _ENGINE_FLAGS[token]
    verdicts: list[Expected] = []
    exit_code: int | None = None
    for line in lines[3:]:
        if line == "--":
            break
        exit_match = re.fullmatch(r"\^EXIT=(\d+)\$", line)
        if exit_match:
            exit_code = int(exit_match.group(1))
            continue
        verdict = _parse_verdict(line)
        if verdict is not None:
            verdicts.append(verdict)
    return Desc(
        name,
        words[0] if words else "",
        words[1:],
        design,
        options,
        bound,
        top,
        engine,
        "--buechi" in options,
        exit_code,
        tuple(verdicts),
    )


# --- the clock adapter ----------------------------------------------------------------------

_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_DIRECTIVE = re.compile(r"\b(assert|assume|cover|restrict)\s+(?:property|sequence)\s*\(")
_CLOCK_DECLARATION = re.compile(r"\b(?:input|output|inout|wire|reg|logic|bit)\b[^;]*?\bclk\b")
_DEFAULT_CLOCKING = re.compile(r"\bdefault\s+clocking\b")
_ENDMODULE = re.compile(r"\bendmodule\b")


def _blank_comments(text: str) -> str:
    return _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _balanced(text: str, open_index: int) -> int | None:
    """Index of the parenthesis closing the one at ``open_index``."""
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _module_span(clean: str, top: str) -> tuple[int, int] | None:
    header = re.search(rf"\bmodule\s+(?:automatic\s+|static\s+)?{re.escape(top)}\b", clean)
    if header is None:
        return None
    end = _ENDMODULE.search(clean, header.end())
    return None if end is None else (header.start(), end.start())


_BEGIN_END = re.compile(r"\b(begin|end)\b")
_EVENT_BEFORE_BEGIN = re.compile(r"@\s*\([^;]*\)\s*$", re.DOTALL)


def _in_event_controlled_block(clean: str, start: int, module_start: int) -> bool:
    """Whether the assertion at ``start`` sits inside a ``begin ... end`` block that an event
    control (``always @(...)``, IEEE 1800-2017 16.16 c) governs, possibly through enclosing
    blocks. Only an event control written right before the ``begin`` (and an optional block
    label) counts; any other shape is not recognised."""
    depth = 0
    for match in reversed(list(_BEGIN_END.finditer(clean, module_start, start))):
        if match.group(1) == "end":
            depth += 1
        elif depth > 0:
            depth -= 1
        else:
            before = re.sub(r":\s*\w+\s*$", "", clean[module_start : match.start()]).rstrip()
            if _EVENT_BEFORE_BEGIN.search(before):
                return True
    return False


def _is_clocked(clean: str, start: int, module_start: int, argument: str) -> bool:
    if "@" in argument:
        return True
    if _in_event_controlled_block(clean, start, module_start):
        return True
    previous = max(clean.rfind(";", module_start, start), module_start)
    if "@" in clean[previous:start]:
        return True
    reference = re.match(r"\s*(\w+)\s*(?:\(.*\))?\s*$", argument, re.DOTALL)
    if reference is not None:
        declaration = re.search(
            rf"\bproperty\s+{re.escape(reference.group(1))}\b(.*?)\bendproperty\b",
            clean,
            re.DOTALL,
        )
        if declaration is not None and "@" in declaration.group(1):
            return True
    return False


def adapt_clock(source: str, top: str) -> tuple[str, bool]:
    """Give the unclocked concurrent assertions of module ``top`` a default clocking (see the
    module contract). Returns the new text and whether it changed it."""
    clean = _blank_comments(source)
    span = _module_span(clean, top)
    if span is None:
        return source, False
    start, end = span
    module = clean[start:end]
    if _DEFAULT_CLOCKING.search(module):
        return source, False
    unclocked = False
    for match in _DIRECTIVE.finditer(clean):
        if not start <= match.start() < end:
            return source, False  # assertions of another module: unexpected shape
        close = _balanced(clean, match.end() - 1)
        if close is None:
            return source, False
        argument = clean[match.end() : close]
        if not _is_clocked(clean, match.start(), start, argument):
            unclocked = True
    if not unclocked:
        return source, False
    if _CLOCK_DECLARATION.search(module):
        clock, declaration = "clk", ""
    else:
        clock, declaration = "__ebmc_clk", "  wire __ebmc_clk;\n"
    insertion = f"{declaration}  default clocking @(posedge {clock}); endclocking\n"
    return source[:end] + insertion + source[end:], True


_MODULE = re.compile(r"^\s*module\s+(?:automatic\s+|static\s+)?(\w+)", re.MULTILINE)


def choose_top(desc: Desc, source: str) -> str:
    """The top module of a case. EBMC takes ``--module``/``--top`` when given, else the module
    its answers are named after (the scope of the verdict lines), else ``main``; ours is the
    first of these that ``source`` declares, otherwise the declared module nobody else
    instantiates (the first of them), otherwise :attr:`Desc.top`."""
    clean = _blank_comments(source)
    declared = _MODULE.findall(clean)
    given = any(option in ("--module", "--top") for option in desc.options)
    scopes = [v.scope for v in desc.verdicts if v.scope is not None]
    named = max(set(scopes), key=scopes.count) if scopes else None
    for candidate in (desc.top if given else None, named, desc.top):
        if candidate is not None and candidate in declared:
            return candidate
    roots = [
        name
        for name in declared
        if not re.search(rf"\b{re.escape(name)}\b\s*(?:#\s*\([^;]*?\)\s*)?\w+\s*\(", clean)
    ]
    return roots[0] if roots else desc.top


# --- skipping and planning ------------------------------------------------------------------

_CLOCK_EVENT = re.compile(r"@\s*\(\s*((?:posedge|negedge)\s+[\w.\[\]]+|[\w.\[\]]+)\s*\)")


def skip_reason(desc: Desc, source: str | None = None) -> str | None:
    """Why the case is not compared, or ``None`` (see the module contract)."""
    if desc.tag != RUNNABLE_TAG:
        return f"tag {desc.tag}: EBMC itself does not pass it"
    if desc.exit_code == 2:
        return "EBMC rejects the input (EXIT=2)"
    for option in desc.options:
        if option in _EBMC_ITSELF:
            return f"option {option} tests EBMC itself"
    if source is not None:
        clocks = {
            re.sub(r"\s+", " ", m.group(1)) for m in _CLOCK_EVENT.finditer(_blank_comments(source))
        }
        clocks = {c for c in clocks if not c.startswith("*")}
        if len(clocks) > 1:
            return "several clock events: " + ", ".join(sorted(clocks))
    if not any(v.known for v in desc.verdicts):
        return "no property with a known answer"
    return None


def unbounded_cover(desc: Desc, verdict: Expected) -> bool:
    """Whether the cover's answer carries no bound (EBMC's unbounded engine, or no bound at
    all), so that the cover is run at the default depth."""
    if verdict.kind is not Kind.COVER:
        return False
    bound = verdict.bound if verdict.bound is not None else desc.bound
    return bound is None or desc.engine != "bmc"


def plan_runs(desc: Desc) -> tuple[Run, ...]:
    """The SBY runs of a case, grouped by mode and depth (see the module contract)."""
    groups: dict[tuple[RunMode, int | None], list[int]] = {}

    def add(mode: RunMode, depth: int | None, index: int) -> None:
        groups.setdefault((mode, depth), []).append(index)

    for index, verdict in enumerate(desc.verdicts):
        if not verdict.known:
            continue
        bounded_engine = desc.engine == "bmc"
        if verdict.kind is Kind.COVER:
            bound = verdict.bound if verdict.bound is not None else desc.bound
            if bound is None or unbounded_cover(desc, verdict):
                add(RunMode.COVER, DEFAULT_COVER_DEPTH, index)
            else:
                add(RunMode.COVER, bound + 1, index)
            continue
        if not bounded_engine:
            add(RunMode.PROVE, None, index)
        elif verdict.bound is not None:
            add(RunMode.BMC, verdict.bound + 1, index)
        elif verdict.status is Status.REFUTED and desc.bound is not None:
            add(RunMode.BMC, desc.bound + 1, index)
        else:
            add(RunMode.PROVE, None, index)
        if desc.buechi or _LIVENESS_TEXT.search(verdict.text):
            add(RunMode.LIVE, None, index)
    return tuple(Run(mode, depth, tuple(ix)) for (mode, depth), ix in groups.items())


# --- classification -------------------------------------------------------------------------

REACHED = "reached"
UNREACHED = "unreached"
PASS = "pass"
FAIL = "fail"
NO_DIRECTIVE = "no-directive"


@dataclass(frozen=True)
class Outcome:
    """What we found for one verdict."""

    result: str
    """``pass``/``fail`` (assert), ``reached``/``unreached`` (cover), ``rejected``, ``error``,
    ``no-directive`` or ``skipped``."""
    detail: str = ""
    """Run flow that decided (``bmc@21``), a rejection fragment, or an error text."""
    failed_in: tuple[RunMode, ...] = ()
    """The flows in which an assert failed."""


def classify(expected: Expected, outcome: Outcome, unbounded: bool = False) -> str:
    """The provisional class of one verdict against our outcome (module contract);
    ``unbounded`` tells that a cover's answer carries no bound (:func:`unbounded_cover`)."""
    result = outcome.result
    if result == "rejected":
        return f"rejected: {outcome.detail}"
    if result == "error":
        return f"error: {outcome.detail}"
    if result == "skipped":
        return f"skipped: {outcome.detail}"
    if result == NO_DIRECTIVE:
        return "skipped: no concurrent directive of that name (immediate assertion?)"
    if not expected.known:
        return f"skipped: no known answer ({expected.raw_status})"
    if expected.kind is Kind.COVER:
        want = REACHED if expected.status is Status.PROVED else UNREACHED
        if result != want:
            return "disagrees"
        # (a reached cover is a witness whatever the engine; "never reached" is only checked to
        # the default depth)
        return "bound-gap" if unbounded and want == UNREACHED else "agrees"
    if expected.status is Status.REFUTED:
        return "agrees" if result == FAIL else "disagrees"
    # PROVED, PROVED_UP_TO_BOUND
    if result == PASS:
        return "agrees"
    if (
        expected.status is Status.PROVED_UP_TO_BOUND
        and outcome.failed_in
        and set(outcome.failed_in) == {RunMode.LIVE}
    ):
        return "bound-gap"
    return "disagrees"


# --- running --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Directive:
    """One directive of the check mode's report."""

    label: str
    kind: str
    status: str
    """``ok``, ``rejected`` or ``skipped``."""
    message: str
    """The rejection or skip message (empty for ``ok``)."""
    live: bool
    """Whether the ``ok`` line reports a liveness part."""
    line: int = 0
    """Line of a rejected or skipped directive's location (0 for ``ok``)."""
    column: int = 0


_OK_LINE = re.compile(r"^ok\s+(\S+)\s+(assert|assume|cover)\b(.*)$")
_BAD_LINE = re.compile(r"^(rejected|skipped)\s+(\S.*?):(\d+):(\d+)\s+(\S+): (.*)$")


def parse_check(stdout: str) -> tuple[Directive, ...]:
    """The directives of the check mode's output, in report order."""
    directives: list[Directive] = []
    for line in stdout.splitlines():
        ok = _OK_LINE.match(line)
        if ok is not None:
            directives.append(Directive(ok.group(1), ok.group(2), "ok", "", "live=" in ok.group(3)))
            continue
        bad = _BAD_LINE.match(line)
        if bad is not None:
            directives.append(
                Directive(
                    bad.group(5),
                    "",
                    bad.group(1),
                    bad.group(6),
                    False,
                    int(bad.group(3)),
                    int(bad.group(4)),
                )
            )
    return tuple(directives)


_MONITOR_HEADER = re.compile(r"^// sva2yosys monitor: (\w+) (.+)$")
_MONITOR_MODULE = re.compile(r"^module __sva2yosys_p(\d+)")


@dataclass(frozen=True)
class Site:
    """A monitor site of the monitors file."""

    label: str
    """The header comment's label: unqualified at the top level (``p0``), ``(unnamed)``
    without a name."""
    number: int
    kind: str
    """``assert``, ``assume`` or ``cover``."""


def parse_monitors(monitors: str) -> tuple[Site, ...]:
    """The monitor sites of the monitors file, in file order (a site split in conjunct monitors
    ``_c0``, ``_c1`` is listed once)."""
    sites: dict[int, Site] = {}
    header: tuple[str, str] | None = None
    for line in monitors.splitlines():
        found = _MONITOR_HEADER.match(line)
        if found is not None:
            header = (found.group(1), found.group(2).strip())
            continue
        module = _MONITOR_MODULE.match(line)
        if module is not None and header is not None:
            number = int(module.group(1))
            sites.setdefault(number, Site(header[1], number, header[0]))
            header = None
    return tuple(sites.values())


def assign_sites(
    directives: Sequence[Directive], monitors: Sequence[Site]
) -> tuple[frozenset[int], ...]:
    """The monitor sites of each directive, in the order of ``directives``: a named directive
    owns the sites whose header label is its name or a dotted suffix of its label; the k-th
    unnamed directive owns the k-th unnamed site."""
    unnamed_sites = [s.number for s in monitors if s.label.endswith("(unnamed)")]
    result: list[frozenset[int]] = []
    rank = 0
    for directive in directives:
        if directive.label.endswith("(unnamed)"):
            result.append(frozenset(unnamed_sites[rank : rank + 1]))
            rank += 1
            continue
        owned = {
            s.number
            for s in monitors
            if not s.label.endswith("(unnamed)")
            and (directive.label == s.label or directive.label.endswith("." + s.label))
        }
        result.append(frozenset(owned))
    return tuple(result)


_SITE = re.compile(r"__sva2yosys_i(\d+)")
_UNNAMED = re.compile(r"(?:.*\.)?(?:assert|assume|cover|restrict)\.(\d+)")


def sites_of(names: Sequence[str]) -> frozenset[int]:
    """The monitor site numbers named by SBY cell names."""
    found: set[int] = set()
    for name in names:
        match = _SITE.search(name)
        if match is not None:
            found.add(int(match.group(1)))
    return frozenset(found)


def match_verdicts(verdicts: Sequence[Expected], directives: Sequence[Directive]) -> dict[int, int]:
    """Map verdict indices to indices into ``directives``: by qualified label (or by scope and
    last component: our labels leave out the names of blocks); EBMC's
    ``assert.N`` to our N-th unnamed directive; its wildcard ``[.*]`` to the next directive of
    the same kind not matched otherwise, in order. Unmatched verdicts are absent."""
    by_label: dict[str, int] = {}
    unnamed: list[int] = []
    for index, directive in enumerate(directives):
        if directive.label.endswith("(unnamed)"):
            unnamed.append(index)
        else:
            by_label.setdefault(directive.label, index)
    matched: dict[int, int] = {}
    for index, verdict in enumerate(verdicts):
        qualified = verdict.qualified
        if qualified is None:
            continue
        # (our labels do not contain the names of blocks: main.blk.p0 is main.p0)
        collapsed = f"{verdict.scope}.{(verdict.label or '').rsplit('.', 1)[-1]}"
        found = next((c for c in (qualified, collapsed) if c in by_label), None)
        if found is not None:
            matched[index] = by_label[found]
            continue
        numbered = _UNNAMED.fullmatch(qualified)
        if numbered is not None and 0 < int(numbered.group(1)) <= len(unnamed):
            matched[index] = unnamed[int(numbered.group(1)) - 1]
    used = set(matched.values())
    for index, verdict in enumerate(verdicts):
        if verdict.qualified is not None:
            continue
        for candidate, directive in enumerate(directives):
            if candidate not in used and directive.kind in ("", verdict.kind.value):
                matched[index] = candidate
                used.add(candidate)
                break
    return matched


_STATEMENT = re.compile(r"\b(?:assert|assume|cover|restrict)\s+(?:property|sequence)\b")
_CONSTRAINT = re.compile(r"\b(?:assume|restrict)\s+property\b")
_BLOCK_WORD = re.compile(r"\b(?:begin|end|endmodule|module|endproperty|endsequence)\b")


def drop_directives(source: str, locations: Sequence[tuple[int, int]]) -> str | None:
    """``source`` without the statements containing the given ``(line, column)`` positions (a
    rejected directive is reported at a point inside it): from just after the previous ``;`` to
    the ``;`` ending the directive, blanked. ``None`` when a statement is not a plain
    concurrent assertion (it contains a block keyword) or no end is found."""
    clean = _blank_comments(source)
    line_starts = [0]
    for match in re.finditer(r"\n", clean):
        line_starts.append(match.end())
    spans: list[tuple[int, int]] = []
    for line, column in locations:
        if not 0 < line <= len(line_starts):
            return None
        position = line_starts[line - 1] + column - 1
        begin = clean.rfind(";", 0, position) + 1
        depth = 0
        end = -1
        for index in range(begin, len(clean)):
            if clean[index] == "(":
                depth += 1
            elif clean[index] == ")":
                depth -= 1
            elif clean[index] == ";" and depth <= 0 and index >= position:
                end = index + 1
                break
        if end < 0:
            return None
        statement = clean[begin:end]
        if _STATEMENT.search(statement) is None or _BLOCK_WORD.search(statement):
            return None
        if _CONSTRAINT.search(statement):
            return None  # dropping an assumption would change the others' verdicts
        spans.append((begin, end))
    text = source
    for begin, end in sorted(set(spans), reverse=True):
        text = text[:begin] + re.sub(r"[^\n]", " ", text[begin:end]) + text[end:]
    return text


@dataclass(frozen=True)
class PropertyResult:
    """Our outcome next to EBMC's for one verdict line."""

    expected: Expected
    runs: str
    """The planned runs of the verdict, e.g. ``bmc@21+live``."""
    outcome: Outcome
    cls: str
    """The provisional class (:func:`classify`)."""


@dataclass(frozen=True)
class CaseResult:
    """The result of :func:`run_case`."""

    desc: Desc
    adapted: bool
    skipped: str | None
    message: str
    """Case-level message (compile error, tool trouble) or empty."""
    results: tuple[PropertyResult, ...]
    isolated: bool = False
    """Whether rejected directives were dropped from the source so that the others could run."""


@dataclass(frozen=True)
class Tools:
    """The tools of a run."""

    sby: Tool
    sby_live: Tool | None = None
    """The ``sby`` with the suprove engine (liveness), if available."""


_SBY_TEXT = """[tasks]
{task}

[options]
mode {mode}
{depth}
[engines]
{engine}

[script]
{reads}
prep -top {top}

[files]
{files}
"""


def sby_text(run_mode: RunMode, depth: int | None, top: str, files: Sequence[str]) -> str:
    """The ``.sby`` file of one run; ``files`` are relative paths, read by their base names."""
    mode, engine = {
        RunMode.BMC: ("bmc", "smtbmc yices"),
        RunMode.PROVE: ("prove", "abc pdr"),
        RunMode.COVER: ("cover", "smtbmc yices"),
        RunMode.LIVE: ("live", "aiger suprove"),
    }[run_mode]
    define = " -D SVA2YOSYS_LIVE" if run_mode is RunMode.LIVE else ""
    reads = "\n".join(f"read_verilog -formal -sv{define} {Path(f).name}" for f in files)
    return _SBY_TEXT.format(
        task=mode,
        mode=mode,
        depth="" if depth is None else f"depth {depth}\n",
        engine=engine,
        reads=reads,
        top=top,
        files="\n".join(files),
    )


def _fragment(message: str) -> str:
    from support.survey import normalise_reason

    return normalise_reason(re.sub(r"^.*?: error: ", "", message).strip())


def _cli(arguments: list[str]) -> tuple[int, str, str]:
    from sva2yosys.cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = main(arguments)
        except SystemExit as stop:
            status = stop.code if isinstance(stop.code, int) else 2
    return status, out.getvalue(), err.getvalue()


_IMMEDIATE = re.compile(r"\b(?:assert|assume)\s*(?:#\s*0\s*|final\s*)?\(")


def has_immediate_assertions(source: str) -> bool:
    """Whether ``source`` has an immediate (or deferred) ``assert``/``assume`` of its own: it
    stays active in the SBY runs and could be the source of an unnamed failure."""
    return _IMMEDIATE.search(_blank_comments(source)) is not None


_INSTANCE = re.compile(r"(__sva2yosys_p(\d+)(?:_c\d+)?) (__sva2yosys_i\d+(?:_c\d+)?) \(")


def _variant(build: Path, design: str, disabled: set[int], dest: Path) -> list[str]:
    """The files of a run, relative to the work directory: the generated ones, or, when some
    assertion monitors are to be switched off, copies in ``dest`` whose instances of those
    monitors set ``CHECK`` to 0."""
    if not disabled:
        return ["build/sva2yosys_monitors.sv", f"build/{design}"]
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(build / "sva2yosys_monitors.sv", dest / "sva2yosys_monitors.sv")

    def switch_off(match: re.Match[str]) -> str:
        if int(match.group(2)) not in disabled:
            return match.group(0)
        return f"{match.group(1)} #(.CHECK(1'b0)) {match.group(3)} ("

    text = (build / design).read_text(encoding="utf-8")
    (dest / design).write_text(_INSTANCE.sub(switch_off, text), encoding="utf-8", newline="\n")
    return [f"{dest.name}/sva2yosys_monitors.sv", f"{dest.name}/{design}"]


def _first_error(err: str) -> str:
    return next((line for line in err.splitlines() if "error" in line), err.strip())


def run_case(desc: Desc, corpus_dir: Path, workdir: Path, tools: Tools) -> CaseResult:
    """Run one case (see the module contract)."""
    from support.toolchain import SbyStatus, ToolError, run_sby

    source = (corpus_dir / desc.design).read_text(encoding="utf-8")
    reason = skip_reason(desc, source)
    if reason is not None:
        return CaseResult(desc, False, reason, "", ())
    desc = replace(desc, top=choose_top(desc, source))
    adapted_text, adapted = adapt_clock(source, desc.top)
    workdir.mkdir(parents=True, exist_ok=True)
    design = workdir / desc.design
    design.write_text(adapted_text, encoding="utf-8", newline="\n")
    count = len(desc.verdicts)
    runs = list(plan_runs(desc))
    isolated = False

    def label(index: int) -> str:
        return "+".join(r.text for r in runs if index in r.verdicts)

    def everything(outcome_for: dict[int, Outcome], message: str = "") -> CaseResult:
        results = tuple(
            PropertyResult(
                v, label(i), outcome_for[i], classify(v, outcome_for[i], unbounded_cover(desc, v))
            )
            for i, v in enumerate(desc.verdicts)
        )
        return CaseResult(desc, adapted, None, message, results, isolated)

    status, out, err = _cli([str(design), "--top", desc.top])
    directives = parse_check(out)
    if status not in (0, 1) or (status == 1 and not directives):
        first = _first_error(err)
        kind = "rejected" if status == 1 else "error"
        detail = _fragment(first) if first else f"exit status {status}"
        return everything({i: Outcome(kind, detail) for i in range(count)}, first)
    if not directives:
        return everything({i: Outcome(NO_DIRECTIVE) for i in range(count)})
    matched = match_verdicts(desc.verdicts, directives)
    outcomes: dict[int, Outcome] = {}
    for index in range(count):
        directive = directives[matched[index]] if index in matched else None
        if directive is None:
            outcomes[index] = Outcome(NO_DIRECTIVE)
        elif directive.status == "rejected":
            outcomes[index] = Outcome("rejected", _fragment(directive.message))
        elif directive.status == "skipped":
            outcomes[index] = Outcome("skipped", _fragment(directive.message))
    accepted = [d for d in directives if d.status == "ok"]
    rejected = [d for d in directives if d.status == "rejected"]
    if rejected:
        # Run the accepted directives alone: drop the rejected statements (best effort).
        reduced = (
            drop_directives(adapted_text, [(d.line, d.column) for d in rejected])
            if accepted
            else None
        )
        again: tuple[Directive, ...] = ()
        if reduced is not None:
            design.write_text(reduced, encoding="utf-8", newline="\n")
            status, out, err = _cli([str(design), "--top", desc.top])
            again = parse_check(out)
        if (
            reduced is None
            or status != 0
            or [d.label for d in again] != [d.label for d in accepted]
        ):
            blocked = _fragment(rejected[0].message)
            for index, verdict in enumerate(desc.verdicts):
                if index not in outcomes:
                    outcomes[index] = (
                        Outcome("rejected", f"case: {blocked}")
                        if verdict.known
                        else Outcome("skipped", "no known answer")
                    )
            return everything(outcomes)
        isolated = True
    build = workdir / "build"
    status, out, err = _cli([str(design), "--top", desc.top, "-o", str(build)])
    if status != 0:
        first = _first_error(err)
        for index in range(count):
            outcomes.setdefault(index, Outcome("rejected", f"on generation: {_fragment(first)}"))
        return everything(outcomes, first)
    monitors = parse_monitors((build / "sva2yosys_monitors.sv").read_text(encoding="utf-8"))
    ok_indices = [j for j, d in enumerate(directives) if d.status == "ok"]
    sites = dict(zip(ok_indices, assign_sites(accepted, monitors), strict=True))
    failed: dict[int, list[RunMode]] = {}
    reached: dict[int, bool] = {}
    problems: dict[int, str] = {}
    for index in range(count):
        if index not in outcomes and not sites.get(matched[index]):
            outcomes[index] = Outcome("error", "no monitor site found for the directive")
    assert_sites = {s.number for s in monitors if s.kind == "assert"}
    # A directive with a liveness part also gets the liveness flow, whatever the planned runs.
    in_live = {i for r in runs if r.mode is RunMode.LIVE for i in r.verdicts}
    extra = tuple(
        i
        for i, v in enumerate(desc.verdicts)
        if v.known
        and v.kind is Kind.ASSERT
        and i in matched
        and directives[matched[i]].live
        and i not in in_live
    )
    if extra:
        runs.append(Run(RunMode.LIVE, None, extra))
    attempt = 0
    own_assertions = has_immediate_assertions(adapted_text)
    for run in runs:
        remaining = [i for i in run.verdicts if i not in outcomes]
        if run.mode is RunMode.LIVE:
            remaining = [i for i in remaining if directives[matched[i]].live]
        tool = tools.sby_live if run.mode is RunMode.LIVE else tools.sby
        if remaining and tool is None:
            for i in remaining:
                problems[i] = "no sby-live tool"
            continue
        # SBY decides a live task by its first justice verdict (SBY issue 378), so every
        # liveness verdict runs alone, the other monitors switched off (a directive with several
        # liveness parts, e.g. a conjunction, still keeps them together: no corpus case needs more).
        groups = (
            [[i] for i in remaining]
            if run.mode is RunMode.LIVE
            else [remaining]
            if remaining
            else []
        )
        while groups and tool is not None:
            # SBY reports only the assertions failing at the earliest failing step: run again
            # for the others, with the monitors of the failed ones switched off. A failure
            # without a monitor name (liveness, or a trivial design) is attributed by running
            # the group's verdicts one by one.
            group = groups.pop(0)
            attempt += 1
            enabled = set().union(*(sites.get(matched[i], frozenset()) for i in group))
            disabled = set() if run.mode is RunMode.COVER else assert_sites - enabled
            files = _variant(build, desc.design, disabled, workdir / f"v{attempt}")
            sby_file = workdir / f"run{attempt}.sby"
            sby_file.write_text(sby_text(run.mode, run.depth, desc.top, files), encoding="utf-8")
            try:
                result = run_sby(
                    tool,
                    sby_file,
                    run.mode.value,
                    workdir / f"work{attempt}",
                    timeout=PROVE_TIMEOUT if run.mode is RunMode.PROVE else RUN_TIMEOUT,
                )
            except ToolError as error:
                for i in group:
                    problems[i] = str(error).splitlines()[0][:80]
                continue
            # (the trace conversion of Yosys fails on designs without signals: the engine's
            # verdict is still FAIL)
            engine_failed = result.status is SbyStatus.FAIL or (
                result.status is SbyStatus.ERROR and "returned FAIL" in result.log
            )
            if not engine_failed and result.status is not SbyStatus.PASS:
                for i in group:
                    problems[i] = f"{run.text}: {result.status.value}"
                continue
            if run.mode is RunMode.COVER:
                reached_sites = sites_of([hit.name for hit in result.reached_covers])
                for i in group:
                    reached[i] = bool(sites.get(matched[i], frozenset()) & reached_sites)
                continue
            if not engine_failed:
                continue
            names = [hit.name for hit in result.failed_assertions]
            failed_sites = sites_of(names)
            foreign = [name for name in names if not sites_of([name])]
            newly = [i for i in group if sites.get(matched[i], frozenset()) & failed_sites]
            if not newly:
                if foreign or (not names and own_assertions):
                    # only the design's own assertion fails, or the failure has no name and
                    # the design has such assertions: the failure cannot be attributed
                    what = foreign[0] if foreign else "(unnamed failure)"
                    for i in group:
                        problems[i] = f"failure of a non-monitor assertion {what}"
                    continue
                if len(group) > 1:
                    groups[:0] = [[i] for i in group]
                    continue
                newly = group
            for i in newly:
                failed.setdefault(i, []).append(run.mode)
            rest = [i for i in group if i not in newly]
            if foreign:
                # the design's own assertion fails too: it blocks the others' verdicts
                for i in rest:
                    problems[i] = f"failure of a non-monitor assertion {foreign[0]}"
            elif rest:
                groups.insert(0, rest)
    for index, verdict in enumerate(desc.verdicts):
        if index in outcomes:
            continue
        if not verdict.known:
            outcomes[index] = Outcome("skipped", "no known answer")
        elif index in problems:
            outcomes[index] = Outcome("error", problems[index])
        elif verdict.kind is Kind.COVER:
            outcomes[index] = Outcome(REACHED if reached.get(index) else UNREACHED, label(index))
        elif index in failed:
            outcomes[index] = Outcome(FAIL, label(index), tuple(failed[index]))
        else:
            outcomes[index] = Outcome(PASS, label(index))
    return everything(outcomes)


# --- the draft ------------------------------------------------------------------------------

TSV_HEADER = ("desc", "label", "ebmc", "run", "adapted/isolated", "ours", "class", "detail")


def _ebmc_text(expected: Expected) -> str:
    bound = "" if expected.bound is None else f" {expected.bound}"
    return f"{expected.kind.value} {expected.status.value}{bound}"


def rows_of(result: CaseResult) -> list[tuple[str, ...]]:
    """The draft lines of a case result (module docstring)."""
    name = result.desc.name
    if result.skipped is not None:
        return [(name, "-", "-", "-", "-", "-", f"skipped: {result.skipped}", "")]
    rows: list[tuple[str, ...]] = []
    for r in result.results:
        label = r.expected.qualified or "[.*]"
        rows.append(
            (
                name,
                label,
                _ebmc_text(r.expected),
                r.runs or "-",
                ("adapted" if result.adapted else "") + (" isolated" if result.isolated else ""),
                r.outcome.result,
                r.cls,
                r.outcome.detail or result.message[:120],
            )
        )
    return rows


def _draft_worker(args: tuple[str, str, str, str, str | None]) -> tuple[str, list[tuple[str, ...]]]:
    """Process-pool entry: one ``.desc`` file; returns plain tuples (picklable)."""
    corpus, desc_name, work, sby, sby_live = args
    from support.toolchain import Tool

    started = time.monotonic()
    path = Path(corpus) / desc_name
    desc = parse_desc(path.read_text(encoding="utf-8"), path.stem)
    tools = Tools(Tool("SVA2YOSYS_SBY", (sby,)), None if sby_live is None else _live(sby_live))
    workdir = Path(work) / path.stem
    try:
        result = run_case(desc, Path(corpus), workdir, tools)
        rows = rows_of(result)
    except Exception as error:  # a draft must not stop on one case
        rows = [
            (path.stem, "-", "-", "-", "-", "-", f"error: {type(error).__name__}", str(error)[:200])
        ]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    elapsed = f"{time.monotonic() - started:.1f}"
    return desc_name, [(*row, elapsed) if i == 0 else row for i, row in enumerate(rows)]


def _live(command: str) -> Tool:
    from support.toolchain import Tool

    return Tool("SVA2YOSYS_SBY_LIVE", tuple(command.split()))


def _init_worker(tests_dir: str) -> None:
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)


def draft(
    corpus: Path,
    out: Path,
    work: Path,
    sby: str,
    sby_live: str | None,
    jobs: int,
    only: Sequence[str] = (),
) -> int:
    """Run every ``.desc`` of ``corpus`` and write the draft table to ``out``."""
    names = sorted(p.name for p in corpus.glob("*.desc"))
    if only:
        names = [n for n in names if n.removesuffix(".desc") in only]
    tests_dir = str(Path(__file__).resolve().parents[1])
    arguments = [(str(corpus), n, str(work), sby, sby_live) for n in names]
    started = time.monotonic()
    collected: dict[str, list[tuple[str, ...]]] = {}
    with ProcessPoolExecutor(
        max_workers=jobs, initializer=_init_worker, initargs=(tests_dir,)
    ) as pool:
        for done, (name, rows) in enumerate(pool.map(_draft_worker, arguments), 1):
            collected[name] = rows
            if done % 20 == 0 or done == len(names):
                print(f"{done}/{len(names)} cases, {time.monotonic() - started:.0f} s", flush=True)
    lines = ["\t".join((*TSV_HEADER, "seconds"))]
    for name in names:
        for row in collected[name]:
            lines.append("\t".join(cell.replace("\t", " ").replace("\n", " ") for cell in row))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out} in {time.monotonic() - started:.0f} s")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Command line: ``--draft <out.tsv> [--jobs N]`` (the corpus, SBY and sby-live come from
    the environment variables of ``.env``)."""
    import os

    parser = argparse.ArgumentParser(description="Draft table of sva2yosys against EBMC's answers.")
    parser.add_argument("--draft", type=Path, required=True, help="output TSV file")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--only", nargs="*", default=(), help="case names (default: all)")
    parser.add_argument(
        "--work", type=Path, default=Path(tempfile.gettempdir()) / "ebmc-draft-work"
    )
    arguments = parser.parse_args(argv)
    tests_dir = str(Path(__file__).resolve().parents[1])
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    corpus = os.environ.get("SVA2YOSYS_EBMC_TESTS", "")
    sby = os.environ.get("SVA2YOSYS_SBY", "")
    if not corpus or not sby:
        parser.error("SVA2YOSYS_EBMC_TESTS and SVA2YOSYS_SBY must be set (uv run --env-file .env)")
    live = os.environ.get("SVA2YOSYS_SBY_LIVE", "").strip() or None
    return draft(
        Path(corpus), arguments.draft, arguments.work, sby, live, arguments.jobs, arguments.only
    )


if __name__ == "__main__":
    sys.exit(main())
