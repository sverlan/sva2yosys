"""Hand-written trace tables (``*.trace`` fixtures) for the semantic tests.

Format, one or more cases per file::

    # comment (whole line, or "#" plus a space after the content)
    == case_name
    property: a |-> ##[1:3] b        SystemVerilog property text (required)
    kind: assert                     assert | assume | cover (default assert)
    disable: !rst_n                  disable iff condition (optional)
    procedure: initial               place the directive in an ``initial`` procedure (the only
                                     allowed value; optional): the directive has one attempt,
                                     at the first tick of its clock (IEEE 1800-2017 16.14.6)
    signals: a b v:4                 declared signals, name or name:width (optional;
                                     undeclared row names are 1-bit signals)
    sample : 0 1 2 3 4               optional; must count 0, 1, 2, ...
    a      : 1 1 . . .               signal values per sample ("." is 0)
    b      : . . . 1 .
    fail   : . . . 1 .               expected: 1 where a failure is reported
    pass   : . . . 1 .               expected: 1 where a non-vacuous success is reported
    pending: 0                       expected number of distinct obligation starts still
                                     pending at the end (optional)
    final-fail: 0                    expected number of distinct obligation starts that fail
                                     when the simulation ends there (strong and still
                                     pending; optional)
    scope: first-failure             the expectations hold up to and including the first
                                     failure only (a property "and"; optional). ``fail`` then
                                     marks that failure alone, and ``pass`` is not allowed.
    scope: attempt                   per-attempt results (``docs/semantics.md`` 9.2): ``fail``
                                     marks the decision sample of every FALSE attempt, ``pass``
                                     that of every TRUE attempt whose nonvacuity is known true
                                     at that sample; ``pending`` is the number of pending
                                     attempts and ``final-fail`` the number of pending attempts
                                     that finalize FALSE. Allows the two rows below.
    outcome: T t F f U X p d         (attempt scope) one token per start sample: T/t TRUE
                                     nonvacuous/vacuous, F/f FALSE nonvacuous/vacuous, U/X TRUE/
                                     FALSE with nonvacuity still unknown at the end, p pending,
                                     d disabled
    decided: 0 1 . 3                 (attempt scope) the decision (or disable) sample of every
                                     attempt, "." while pending
    support: oracle                  the production pipeline rejects the property: only the
                                     reference evaluator runs the case (optional)
    rejected-by: first sample        (needs ``support: oracle``) a fragment of the message
                                     with which the pipeline rejects the property; the
                                     pipeline tests check that the rejection contains it,
                                     verbatim (optional; non-empty)
    loop: 2                         (attempt scope) the case is also a lasso: the written
                                     samples 2 ... last repeat forever, so the infinite trace
                                     is samples 0, 1 followed by (2 ... last) forever. The
                                     other rows keep describing the written samples as a
                                     finite trace. 0 <= k < number of samples.
    inf-outcome: T F D               (needs ``loop``) the exact outcome of the attempt started
                                     at each written sample on the infinite trace: T TRUE,
                                     F FALSE, D disabled. Never pending: an attempt that no
                                     finite prefix decides is TRUE if weak, FALSE if strong.
    inf-decided: 3 . 4               (needs ``loop``) the sample at which the attempt is
                                     decided online (or disabled), "." when no finite prefix
                                     decides it (the outcome holds only in the limit)
    clause: 16.9.2 16.12.7           (required) the IEEE 1800 clauses that decide the verdict:
                                     space- or comma-separated items, each ``16.9.2`` style
                                     (digits and dots) or ``Annex F``
    class: normative                 (required) normative | implementation-defined |
                                     must-reject | interpretation I<n>[, I<m>...]. An
                                     interpretation names the rows of ``docs/validation.md``
                                     section 5 on which the verdict depends; must-reject (an
                                     illegal property) needs ``support: oracle``
    origin: discharge mutant  where the case came from (optional free text; every case
                                     of ``counterexamples.trace`` has one)

All rows of a case have the same number of samples. ``fail`` and ``pass`` default to
"never" when absent. The names ``clk``, ``t``, ``success`` and ``p`` are reserved (the
generated test benches and the directive's label use them) and cannot be signals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class TraceFileError(ValueError):
    """A trace file is malformed; the message names the file and line."""


class CaseClass(Enum):
    """How a case's verdict is grounded (the ``class:`` key)."""

    NORMATIVE = "normative"
    INTERPRETATION = "interpretation"
    IMPLEMENTATION_DEFINED = "implementation-defined"
    MUST_REJECT = "must-reject"


@dataclass(frozen=True)
class TraceCase:
    """One case of a trace file.

    Attributes:
        file: The file name.
        line: Line of the ``==`` header.
        name: Case name.
        property: SystemVerilog property text.
        kind: ``assert``, ``assume`` or ``cover``.
        disable: ``disable iff`` condition text, or ``None``.
        procedure: ``"initial"`` when the directive is in an ``initial`` procedure, else
            ``None``.
        widths: Width of every signal, in declaration order.
        samples: Signal values per sample.
        fails: Samples with an expected failure.
        passes: Samples with an expected non-vacuous success.
        pending: Expected number of pending obligation starts at the end, or ``None``
            when not checked.
        final_fail: Expected number of obligation starts failing at the end of the
            simulation, or ``None`` when not checked.
        first_failure_only: The expectations are scoped to "up to and including the first
            failure": only the earliest failure is checked, and successes are not.
        attempt_scope: The expectations are per attempt (``scope: attempt``).
        oracle_only: ``support: oracle``: the production pipeline must reject the property.
        rejected_by: The ``rejected-by`` header: a fragment of the pipeline's rejection
            message, or ``None``. Only set on an oracle-only case.
        outcomes: The ``outcome`` row (one token of ``TtFfUXpd`` per sample), or ``None``.
        decisions: The ``decided`` row (a sample or ``None`` per sample), or ``None`` when
            the row is absent.
        loop: The ``loop`` header (index of the first repeated sample), or ``None`` for a
            case that is only a finite trace.
        inf_outcomes: The ``inf-outcome`` row (one token of ``TFD`` per written sample), or
            ``None``.
        inf_decisions: The ``inf-decided`` row (a sample or ``None`` per written sample), or
            ``None`` when the row is absent.
        clause: The ``clause`` items (IEEE 1800 clause numbers or ``Annex X``), in order.
        case_class: The ``class`` of the case.
        interpretations: The interpretation ids (``I1``, ...) of an ``interpretation`` class,
            empty for every other class.
        origin: The ``origin`` header, or ``None``.
    """

    file: str
    line: int
    name: str
    property: str
    kind: str
    disable: str | None
    widths: dict[str, int]
    samples: tuple[dict[str, int], ...]
    fails: frozenset[int]
    passes: frozenset[int]
    pending: int | None
    final_fail: int | None = None
    first_failure_only: bool = False
    attempt_scope: bool = False
    oracle_only: bool = False
    rejected_by: str | None = None
    outcomes: tuple[str, ...] | None = None
    decisions: tuple[int | None, ...] | None = None
    loop: int | None = None
    inf_outcomes: tuple[str, ...] | None = None
    inf_decisions: tuple[int | None, ...] | None = None
    clause: tuple[str, ...] = ()
    case_class: CaseClass = CaseClass.NORMATIVE
    interpretations: tuple[str, ...] = ()
    origin: str | None = None
    procedure: str | None = None


# A comment is "#" followed by a space or the end of the line, so SVA's "##" is kept.
_COMMENT = re.compile(r"(?:^|\s)#(?=\s|$)")
_HEADERS = (
    "property",
    "kind",
    "disable",
    "procedure",
    "signals",
    "pending",
    "final-fail",
    "scope",
    "support",
    "rejected-by",
    "loop",
    "clause",
    "class",
    "origin",
)
_CLAUSE = re.compile(r"\d+(\.\d+)*|Annex [A-Z]")
_INTERPRETATION = re.compile(r"interpretation (I\d+(, ?I\d+)*)")
_RESERVED = ("clk", "t", "success", "p")
_KINDS = ("assert", "assume", "cover")
_EXPECTATIONS = ("fail", "pass")
_ATTEMPT_ROWS = ("outcome", "decided")
_INF_ROWS = ("inf-outcome", "inf-decided")
_OUTCOME_TOKENS = "TtFfUXpd"
_INF_TOKENS = "TFD"


def load_cases(path: Path) -> list[TraceCase]:
    """Parse every case of ``path``.

    Raises:
        TraceFileError: Unknown header, missing property, rows of different lengths,
            a bad value, a ``sample`` row that does not count from 0, or duplicate names.
    """
    builders: list[_Builder] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = _COMMENT.split(raw, 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        where = f"{path.name}:{number}"
        if line.startswith("=="):
            builders.append(_Builder(path.name, number, line[2:].strip()))
            continue
        if not builders:
            raise TraceFileError(f"{where}: line outside a case (start one with '== name')")
        builders[-1].add(line, where)
    cases = [builder.build() for builder in builders]
    seen: set[str] = set()
    for case in cases:
        if case.name in seen:
            raise TraceFileError(f"{path.name}:{case.line}: duplicate case name {case.name!r}")
        seen.add(case.name)
    return cases


def sv_source(case: TraceCase, module: str = "tc") -> str:
    """A module declaring ``clk`` and the case's signals as inputs and the case's
    directive, labelled ``p``, clocked by ``@(posedge clk)``. With ``procedure: initial`` the
    directive is inside an ``initial`` block.

    The signal of row ``x`` therefore has the hierarchical path ``<module>.x``.
    """
    ports = ["input logic clk"]
    for name, width in case.widths.items():
        ports.append(f"input logic {name}" if width == 1 else f"input logic [{width - 1}:0] {name}")
    disable = f"disable iff ({case.disable}) " if case.disable is not None else ""
    directive = f"p: {case.kind} property (@(posedge clk) {disable}{case.property});"
    if case.procedure is not None:
        directive = f"initial {directive}"
    return f"module {module}({', '.join(ports)});\n  {directive}\nendmodule\n"


class _Builder:
    def __init__(self, file: str, line: int, name: str) -> None:
        self.file = file
        self.line = line
        self.name = name
        self.headers: dict[str, str] = {}
        self.rows: dict[str, list[str]] = {}
        self.where: dict[str, str] = {}

    def add(self, line: str, where: str) -> None:
        key, separator, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not separator:
            raise TraceFileError(f"{where}: expected 'name: value'")
        if key in _HEADERS:
            if key in ("clause", "class", "origin") and key in self.headers:
                raise TraceFileError(f"{where}: duplicate {key!r} header")
            self.headers[key] = value
        elif key.isidentifier() or key in _INF_ROWS:
            self.rows[key] = value.split()
        else:
            raise TraceFileError(f"{where}: unknown header {key!r}")
        self.where[key] = where

    def _error(self, key: str, message: str) -> TraceFileError:
        return TraceFileError(f"{self.where.get(key, f'{self.file}:{self.line}')}: {message}")

    def build(self) -> TraceCase:
        if "property" not in self.headers:
            raise TraceFileError(f"{self.file}:{self.line}: case {self.name!r} has no property")
        for key in ("clause", "class"):
            if key not in self.headers:
                raise TraceFileError(f"{self.file}:{self.line}: case {self.name!r} has no {key}")
        clause = self._clause()
        case_class, interpretations = self._class()
        origin = self.headers.get("origin")
        if origin == "":
            raise self._error("origin", "'origin' needs a text")
        kind = self.headers.get("kind", "assert")
        if kind not in _KINDS:
            raise self._error("kind", f"kind must be one of {_KINDS}")
        widths: dict[str, int] = {}
        for item in self.headers.get("signals", "").split():
            name, _, width_text = item.partition(":")
            width = int(width_text) if width_text else 1
            if width < 1:
                raise self._error("signals", f"bad width for {name!r}")
            widths[name] = width
        procedure = self.headers.get("procedure")
        if procedure not in (None, "initial"):
            raise self._error("procedure", "procedure must be 'initial'")
        scope = self.headers.get("scope")
        if scope not in (None, "first-failure", "attempt"):
            raise self._error("scope", "scope must be 'first-failure' or 'attempt'")
        if scope == "first-failure" and ("pass" in self.rows or kind == "cover"):
            raise self._error("scope", "a first-failure case has no 'pass' row and is not a cover")
        support = self.headers.get("support")
        if support not in (None, "oracle"):
            raise self._error("support", "support must be 'oracle'")
        if case_class is CaseClass.MUST_REJECT and support is None:
            raise self._error("class", "'class: must-reject' needs 'support: oracle'")
        rejected_by = self.headers.get("rejected-by")
        if rejected_by is not None and support is None:
            raise self._error("rejected-by", "'rejected-by' needs 'support: oracle'")
        if rejected_by == "":
            raise self._error("rejected-by", "'rejected-by' needs a message fragment")
        for row in _ATTEMPT_ROWS:
            if row in self.rows and scope != "attempt":
                raise self._error(row, f"the {row!r} row needs 'scope: attempt'")
        loop = self._loop(scope)
        for row in _INF_ROWS:
            if row in self.rows and loop is None:
                raise self._error(row, f"the {row!r} row needs a 'loop' header")
        lengths = {len(values) for values in self.rows.values()}
        if len(lengths) > 1:
            raise TraceFileError(
                f"{self.file}:{self.line}: rows of case {self.name!r} differ in length"
            )
        length = lengths.pop() if lengths else 0
        if "sample" in self.rows and self.rows["sample"] != [str(i) for i in range(length)]:
            raise self._error("sample", "the sample row must count 0, 1, 2, ...")
        signal_rows = [
            key
            for key in self.rows
            if key not in ("sample", *_EXPECTATIONS, *_ATTEMPT_ROWS, *_INF_ROWS)
        ]
        for name in signal_rows:
            widths.setdefault(name, 1)
        for name in widths:
            if name in _RESERVED:
                raise self._error(
                    name if name in self.rows else "signals",
                    f"{name!r} is reserved (test benches use it) and cannot be a signal",
                )
        samples = tuple(
            {
                name: self._value(
                    name, self.rows[name][i] if name in self.rows else ".", widths[name]
                )
                for name in widths
            }
            for i in range(length)
        )
        if loop is not None and not 0 <= loop < length:
            raise self._error("loop", f"loop {loop} is outside the {length} written samples")
        pending_text = self.headers.get("pending")
        return TraceCase(
            file=self.file,
            line=self.line,
            name=self.name,
            property=self.headers["property"],
            kind=kind,
            disable=self.headers.get("disable"),
            widths=widths,
            samples=samples,
            fails=self._marks("fail", length),
            passes=self._marks("pass", length),
            pending=int(pending_text) if pending_text is not None else None,
            final_fail=int(final_text) if (final_text := self.headers.get("final-fail")) else None,
            first_failure_only=scope == "first-failure",
            attempt_scope=scope == "attempt",
            oracle_only=support is not None,
            rejected_by=rejected_by,
            outcomes=self._outcomes(),
            decisions=self._decisions(),
            loop=loop,
            inf_outcomes=self._inf_outcomes(),
            inf_decisions=self._decisions("inf-decided"),
            clause=clause,
            case_class=case_class,
            interpretations=interpretations,
            origin=origin,
            procedure=procedure,
        )

    def _clause(self) -> tuple[str, ...]:
        text = self.headers["clause"]
        # Items are separated by spaces or commas; "Annex F" is one item.
        words = text.replace(",", " ").split()
        items: list[str] = []
        index = 0
        while index < len(words):
            if words[index] == "Annex" and index + 1 < len(words):
                items.append(f"Annex {words[index + 1]}")
                index += 2
            else:
                items.append(words[index])
                index += 1
        if not items:
            raise self._error("clause", "'clause' needs at least one clause")
        for item in items:
            if _CLAUSE.fullmatch(item) is None:
                raise self._error("clause", f"bad clause {item!r} (use 16.9.2 or 'Annex F')")
        return tuple(items)

    def _class(self) -> tuple[CaseClass, tuple[str, ...]]:
        text = self.headers["class"]
        for plain in (
            CaseClass.NORMATIVE,
            CaseClass.IMPLEMENTATION_DEFINED,
            CaseClass.MUST_REJECT,
        ):
            if text == plain.value:
                return plain, ()
        match = _INTERPRETATION.fullmatch(text)
        if match is None:
            raise self._error(
                "class",
                f"bad class {text!r} (normative, implementation-defined, must-reject "
                "or interpretation I<n>[, I<m>...])",
            )
        return CaseClass.INTERPRETATION, tuple(match.group(1).replace(",", " ").split())

    def _loop(self, scope: str | None) -> int | None:
        if "loop" not in self.headers:
            return None
        if scope != "attempt":
            raise self._error("loop", "a 'loop' needs 'scope: attempt'")
        text = self.headers["loop"]
        if not text.isdigit():
            raise self._error("loop", f"bad loop {text!r} (a sample index)")
        return int(text)

    def _inf_outcomes(self) -> tuple[str, ...] | None:
        if "inf-outcome" not in self.rows:
            return None
        for token in self.rows["inf-outcome"]:
            if len(token) != 1 or token not in _INF_TOKENS:
                raise self._error("inf-outcome", f"bad outcome {token!r} (use one of TFD)")
        return tuple(self.rows["inf-outcome"])

    def _outcomes(self) -> tuple[str, ...] | None:
        if "outcome" not in self.rows:
            return None
        for token in self.rows["outcome"]:
            if len(token) != 1 or token not in _OUTCOME_TOKENS:
                raise self._error("outcome", f"bad outcome {token!r} (use one of TtFfUXpd)")
        return tuple(self.rows["outcome"])

    def _decisions(self, key: str = "decided") -> tuple[int | None, ...] | None:
        if key not in self.rows:
            return None
        decisions: list[int | None] = []
        for token in self.rows[key]:
            if token == ".":
                decisions.append(None)
            elif token.isdigit():
                decisions.append(int(token))
            else:
                raise self._error(key, f"bad decision {token!r} (a sample or '.')")
        return tuple(decisions)

    def _value(self, key: str, text: str, width: int) -> int:
        if text == ".":
            return 0
        try:
            value = int(text, 0)
        except ValueError:
            raise self._error(key, f"row {key!r}: bad value {text!r}") from None
        if not 0 <= value < 1 << width:
            raise self._error(key, f"row {key!r}: value {text} does not fit {width} bit(s)")
        return value

    def _marks(self, key: str, length: int) -> frozenset[int]:
        marks: set[int] = set()
        for index, text in enumerate(self.rows.get(key, [])):
            if text == "1":
                marks.add(index)
            elif text not in (".", "0"):
                raise self._error(key, f"bad value {text!r} (use 1 or .)")
        return frozenset(marks)
