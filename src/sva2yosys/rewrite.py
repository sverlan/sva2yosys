"""Source rewriting: splice generated monitors into the verification files.

The inputs are split in two (``docs/architecture.md``, *Source rewriting*):

- **design files**, compiled for elaboration context and never modified;
- **verification files**, the files the user designates: SVA plus supporting code.

:func:`rewrite` produces the output texts. It is pure: it reads no file and runs no tool.

Verification files are written with
  - every concurrent assertion replaced, at its source range, by one instance of its
    generated monitor module (``__sva2yosys_p<N> __sva2yosys_i<N> (...)``), the ports
    connected to the signals' names as written at that place, and followed by a block
    comment quoting the assertion as written (whitespace folded; no newline);
  - ``property`` and ``sequence`` declarations, clocking blocks (``read_verilog`` reads
    none; code still using one then fails loudly downstream) and ``default disable iff``
    declarations removed;
  - every ``bind`` removed, and its instantiation inserted before ``endmodule`` of the
    target module (with the ``SLANG`` frontend instead every ``bind`` stays as written and
    no design file is copied, because ``read_slang`` applies it);
  - everything else verbatim. A replaced or removed range keeps its newlines, so every
    remaining character stays on its original line.

A design file is written only when a ``bind`` was lowered into one of its modules (never
with the ``SLANG`` frontend); the copy differs from the original by the inserted
instantiations only. The monitor modules
go to one separate generated file.

Rules (each violation is an error, and an error produces no output at all):

- an assertion in a verification file is converted and compiled (no rejection);
- an assertion in a verification file was elaborated (else it sits in an uninstantiated
  module or generate branch), is not produced by a macro and is inside a module (not an
  interface, program or checker);
- a file included by a verification file contains no SVA item (assertion, declaration,
  clocking block, bind, checker);
- every elaborated instance of one assertion gives the same monitor and the same port
  connections (otherwise, e.g., a parameter-dependent bound or width differs);
- every signal of a monitor is a plain identifier declared in the assertion's scope or
  an enclosing scope of the same module instance;
- verification files contain no ``checker`` declaration;
- a ``bind`` is a module bind (``bind <module> <instantiation>``) whose target is a module
  declared exactly once, written in an input file and not produced by a macro;
- output file names (base names) do not collide, and none is the monitors file name.

Design files are not translated. Any SVA item in one (an assertion, elaborated or not,
a declaration, a clocking block, a ``bind``, a checker; also through an ``include``)
would reach Yosys unchanged: ``read_verilog`` applies its own SVA semantics, or none, and
does not support ``bind`` (a syntax error since Yosys 0.70, silently ignored before).
Each such item is an error by default (``design_sva``), or a warning when the caller accepts
that explicitly.
"""

from __future__ import annotations

import re
from collections.abc import Sequence as Seq
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import PurePath

from sva2yosys.codegen.emit_configurations import live_part
from sva2yosys.codegen.equivalence import equivalence_check
from sva2yosys.codegen.render import block_comment, fold_whitespace
from sva2yosys.codegen.systemverilog import monitor_module, monitor_ports
from sva2yosys.ir.directive import (
    Directive,
    DirectiveKind,
    ParameterValue,
    uncomparable_message,
    uncomparable_parameters,
)
from sva2yosys.ir.expr import (
    Binary,
    BitSelect,
    Concat,
    Conditional,
    Const,
    Convert,
    Expr,
    FirstSample,
    RangeSelect,
    Replicate,
    Sampled,
    Signal,
    Unary,
)
from sva2yosys.ir.property import (
    Always,
    Eventually,
    Implication,
    Nexttime,
    Not,
    PropAnd,
    Property,
    PropIf,
    PropOr,
    SequenceProperty,
    Until,
)
from sva2yosys.ir.sequence import (
    Bool,
    Goto,
    NonConsecutive,
    Repeat,
    SeqAnd,
    SeqConcat,
    SeqIntersect,
    SeqOr,
    Sequence,
    Throughout,
    Within,
)
from sva2yosys.ir.source import SourceRange
from sva2yosys.optimize.forms import Form, Strategy, choose
from sva2yosys.passes.frontend import YosysFrontend, frontend_problem
from sva2yosys.slang.convert import Conversion, Skipped
from sva2yosys.temporal.budgets import DEFAULT_LIMITS, BudgetError, ResourceLimits
from sva2yosys.temporal.compile import (
    COVER_FAIRNESS_WARNING,
    CompileError,
    Monitor,
    compile_monitor,
    is_fairness_assumption,
    monitor_directives,
    never_fails_warning,
    validate_directive,
)
from sva2yosys.temporal.configurations import ConfigMachine
from sva2yosys.temporal.machine import Machine

MONITORS_FILE = "sva2yosys_monitors.sv"


def live_parts_warning(total: int, advice: str) -> str:
    """The warning at one of ``total`` (more than one) liveness parts of a design: SBY's ``live``
    mode decides a task by its first justice verdict, so the others could fail unseen. The same
    text in check mode and in ``-o`` mode, up to ``advice`` (``docs/usage.md``)."""
    return (
        f"{total} liveness parts in one `live` task would report only the "
        f"first verdict (SBY issue 378); {advice} (docs/usage.md)"
    )


class ItemKind(Enum):
    """SVA-related syntax found in the sources."""

    ASSERTION = "assertion"  # a concurrent assert/assume/cover/restrict/expect statement
    PROPERTY = "property"  # property declaration
    SEQUENCE = "sequence"  # sequence declaration
    DEFAULT_CLOCKING = "default clocking"
    CLOCKING = "clocking block"  # any other clocking declaration
    DEFAULT_DISABLE = "default disable iff"
    BIND = "bind"
    CHECKER = "checker"  # checker declaration
    MODULE = "module"  # module declaration (a possible bind target)


class Container(Enum):
    """The declaration enclosing an item."""

    NONE = "none"  # compilation unit
    MODULE = "module"
    INTERFACE = "interface"
    PROGRAM = "program"
    PACKAGE = "package"
    CHECKER = "checker"


@dataclass(frozen=True)
class SyntaxItem:
    """One item of the syntax inventory (the exporter's ``sva2yosys_syntax``).

    Attributes:
        kind: What it is.
        source: Where it is written: its file and range including a statement label and
            the closing ``;``. For an item produced by a macro expansion, the location of
            the expansion.
        start: Offset of the range start in the file text.
        end: Offset of the range end (exclusive).
        macro: The item comes from a macro expansion; ``start``/``end`` are then not a
            range of the file text.
        container: The enclosing declaration.
        input_file: The input file containing the item, directly or through an
            ``include`` (equal to ``source.file`` for an item written in an input file).
        name: ``MODULE``: the module name; ``BIND``: the target as written.
        instance_bind: ``BIND``: the target names instances (a scoped name or a target
            instance list).
        insert_at: ``MODULE``: offset of ``endmodule``; ``BIND``: start offset of the
            instantiation; ``ASSERTION`` inside a procedural block: offset just after the
            outermost such block, -1 when that block is the unbraced body of a generate
            construct (else 0).
        insert_end: ``BIND``: end offset of the instantiation (after its ``;``).
    """

    kind: ItemKind
    source: SourceRange
    start: int
    end: int
    macro: bool
    container: Container
    input_file: str = ""
    name: str = ""
    instance_bind: bool = False
    insert_at: int = 0
    insert_end: int = 0


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"  # a skipped X-check


@dataclass(frozen=True)
class Diagnostic:
    """An error or warning of the rewrite, with its location when it has one."""

    severity: Severity
    source: SourceRange | None
    message: str

    def __str__(self) -> str:
        where = f"{self.source}: " if self.source is not None else ""
        return f"{where}{self.severity.value}: {self.message}"


class FileRole(Enum):
    VERIFICATION = "verification"
    DESIGN_COPY = "design copy"


@dataclass(frozen=True)
class OutputFile:
    """A rewritten input file.

    Attributes:
        source: The input file name, as given to Slang.
        name: The output base name (that of the input).
        role: A rewritten verification file, or a design file copy with lowered binds.
        text: The output text.
    """

    source: str
    name: str
    role: FileRole
    text: str


@dataclass(frozen=True)
class Rewrite:
    """The output of a successful rewrite.

    Attributes:
        files: The verification files and design copies, in input order.
        monitors: Text of :data:`MONITORS_FILE`.
        warnings: Warnings, in source order (also the dropped ``$isunknown`` parts of
            accepted directives, docs/semantics.md §8a).
        skipped: One note per skipped X-check site (:class:`~sva2yosys.slang.convert.Skipped`),
            in source order: its statement is replaced by a comment, no monitor exists.
        forms: Per monitor module name, the form of its temporal state.
        equivalence: Per optimised monitor module name, when requested, the files of its
            equivalence check (:func:`sva2yosys.codegen.equivalence.equivalence_check`).
        live: Some monitor has a liveness part (a strong unbounded ``assert`` or a fairness
            ``assume``, inside ``ifdef SVA2YOSYS_LIVE_<part>``): the CLI then also writes the
            read script of the ``live`` task (``docs/usage.md``).
        live_monitors: The monitor modules of ``assert`` directives with a liveness part, in
            order (a property ``and`` has one per part): the CLI writes one read script for each,
            because SBY's ``live`` mode decides a task by its first justice verdict.
        fair: The design has a fairness ``assume`` (switched on by ``SVA2YOSYS_LIVE_FAIR``).
    """

    files: tuple[OutputFile, ...]
    monitors: str
    warnings: tuple[Diagnostic, ...]
    forms: dict[str, Form] = field(default_factory=dict)
    equivalence: dict[str, dict[str, str]] = field(default_factory=dict)
    skipped: tuple[Diagnostic, ...] = ()
    live: bool = False
    live_monitors: tuple[str, ...] = ()
    fair: bool = False


class RewriteError(Exception):
    """The rewrite was refused; ``diagnostics`` holds every error (and the warnings)."""

    def __init__(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        super().__init__("\n".join(str(d) for d in diagnostics))
        self.diagnostics = diagnostics


_KINDS = {
    "ConcurrentAssertion": ItemKind.ASSERTION,
    "PropertyDeclaration": ItemKind.PROPERTY,
    "SequenceDeclaration": ItemKind.SEQUENCE,
    "DefaultClocking": ItemKind.DEFAULT_CLOCKING,
    "ClockingDeclaration": ItemKind.CLOCKING,
    "DefaultDisableDeclaration": ItemKind.DEFAULT_DISABLE,
    "BindDirective": ItemKind.BIND,
    "CheckerDeclaration": ItemKind.CHECKER,
    "ModuleDeclaration": ItemKind.MODULE,
}

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def syntax_items(document: object) -> tuple[SyntaxItem, ...]:
    """The syntax inventory of an exported document (its ``sva2yosys_syntax`` list).

    Raises:
        ValueError: The document has no well-formed inventory.
    """
    if not isinstance(document, dict) or not isinstance(document.get("sva2yosys_syntax"), list):
        raise ValueError("the document has no sva2yosys_syntax inventory")
    items: list[SyntaxItem] = []
    for raw in document["sva2yosys_syntax"]:
        try:
            items.append(_item(raw))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"malformed syntax item {raw!r}: {error}") from error
    return tuple(items)


def _item(raw: object) -> SyntaxItem:
    if not isinstance(raw, dict):
        raise TypeError("not an object")

    def integer(key: str) -> int:
        value = raw[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{key} is not an integer")
        return value

    def text(key: str) -> str:
        value = raw[key]
        if not isinstance(value, str):
            raise TypeError(f"{key} is not a string")
        return value

    def flag(key: str) -> bool:
        value = raw[key]
        if not isinstance(value, bool):
            raise TypeError(f"{key} is not a boolean")
        return value

    source = SourceRange(
        text("source_file_start"),
        integer("source_line_start"),
        integer("source_column_start"),
        integer("source_line_end"),
        integer("source_column_end"),
    )
    return SyntaxItem(
        kind=_KINDS[text("kind")],
        source=source,
        start=integer("sva2yosys_start"),
        end=integer("sva2yosys_end"),
        macro=flag("sva2yosys_macro"),
        container=Container(text("sva2yosys_container")),
        input_file=text("sva2yosys_inputFile"),
        name=text("name"),
        instance_bind=flag("sva2yosys_instanceBind"),
        insert_at=integer("sva2yosys_insertAt"),
        insert_end=integer("sva2yosys_insertEnd"),
    )


def rewrite(
    sources: Seq[tuple[str, str]],
    verification: frozenset[str],
    items: Seq[SyntaxItem],
    conversion: Conversion,
    design_sva: Severity = Severity.ERROR,
    strategy: Strategy = Strategy.DIRECT,
    equivalence: bool = False,
    *,
    limits: ResourceLimits = DEFAULT_LIMITS,
    frontend: YosysFrontend = YosysFrontend.VERILOG,
) -> Rewrite:
    """Rewrite the verification files (see the module docstring).

    Args:
        sources: Every input ``(name, text)``, in input order, as compiled by Slang.
        verification: The names of the verification files (a subset of ``sources``).
        items: The syntax inventory of the same compilation.
        conversion: The converted assertions of the same compilation.
        design_sva: The severity of an SVA item in a design file.
        strategy: How each monitor's form is chosen (``optimize.forms.choose``; the
            default ``DIRECT`` turns optimisation off).
        equivalence: Also give the equivalence check of each optimised monitor.
        frontend: The Yosys frontend that will read the output. ``VERILOG`` lowers the
            binds into design copies; ``SLANG`` keeps them (no design copy) and refuses
            a directive with a liveness part (``passes.frontend``).

    Raises:
        RewriteError: A rule is violated.
    """
    return _Rewriter(
        sources,
        verification,
        items,
        conversion,
        design_sva,
        strategy,
        equivalence,
        limits,
        frontend,
    ).run()


type _Site = tuple[str, int, int]  # file, line, column of an assertion


def _site(source: SourceRange) -> _Site:
    return (source.file, source.line, source.column)


def _contains(outer: SourceRange, inner: SourceRange) -> bool:
    return (
        outer.file == inner.file
        and (outer.line, outer.column) <= (inner.line, inner.column)
        and (inner.line, inner.column) < (outer.end_line, outer.end_column)
    )


@dataclass(frozen=True)
class _Edit:
    start: int
    end: int
    text: str


class _Rewriter:
    def __init__(
        self,
        sources: Seq[tuple[str, str]],
        verification: frozenset[str],
        items: Seq[SyntaxItem],
        conversion: Conversion,
        design_sva: Severity,
        strategy: Strategy,
        equivalence: bool,
        limits: ResourceLimits,
        frontend: YosysFrontend,
    ) -> None:
        self._design_sva = design_sva
        self._frontend = frontend
        self._limits = limits
        self._strategy = strategy
        self._want_equivalence = equivalence
        self._forms: dict[str, Form] = {}
        self._live = False
        self._fair = False
        # (module, qualified name, source, elaborated instances) of each assert liveness part
        self._parts: list[tuple[str, str, SourceRange | None, int]] = []
        self._covers: dict[tuple[SourceRange | None, str], None] = {}
        self._equivalence: dict[str, dict[str, str]] = {}
        self._order = [name for name, _ in sources]
        self._texts = dict(sources)
        self._verification = verification
        self._items = sorted(items, key=lambda i: (self._rank(i.source.file), i.start))
        self._conversion = conversion
        self._errors: list[Diagnostic] = []
        self._warnings: list[Diagnostic] = []
        self._skipped: list[Diagnostic] = []
        self._edits: dict[str, list[_Edit]] = {name: [] for name in self._order}

    def _rank(self, file: str) -> int:
        return self._order.index(file) if file in self._order else len(self._order)

    def _position(self, source: SourceRange | None) -> tuple[int, int, int]:
        """A sort key in source order (file rank, line, column); no source last."""
        if source is None:
            return (len(self._order) + 1, 0, 0)
        return (self._rank(source.file), source.line, source.column)

    def _error(self, source: SourceRange | None, message: str) -> None:
        self._errors.append(Diagnostic(Severity.ERROR, source, message))

    def _warning(self, source: SourceRange | None, message: str) -> None:
        self._warnings.append(Diagnostic(Severity.WARNING, source, message))

    # --- driver ------------------------------------------------------------------------

    def run(self) -> Rewrite:
        assertions = self._check_items()
        rejected = self._rejections(assertions)
        monitors = self._assertions(assertions, rejected)
        self._binds()
        self._part_warnings()
        if self._fair:  # covers do not see the fairness constraints
            for source, qualified in self._covers:
                self._warning(source, f"{qualified}: {COVER_FAIRNESS_WARNING}")
            self._warnings.sort(key=lambda d: self._position(d.source))
        files = self._outputs()
        if self._errors:
            raise RewriteError(tuple(self._errors + self._warnings))
        header = (
            "// generated by sva2yosys; do not edit\n"
            "// monitor modules for the rewritten verification files;\n"
            "// read with read_verilog -formal -sv; requires SBY 'multiclock off'\n"
        )
        return Rewrite(
            files,
            header + "".join("\n" + m for m in monitors),
            tuple(self._warnings),
            dict(self._forms),
            dict(self._equivalence),
            tuple(self._skipped),
            self._live,
            tuple(name for name, _, _, _ in self._parts),
            self._fair,
        )

    # --- items -------------------------------------------------------------------------

    def _check_items(self) -> list[SyntaxItem]:
        """Check the items of verification files; return their translatable assertions."""
        assertions: list[SyntaxItem] = []
        for item in self._items:
            file = item.source.file
            if file not in self._texts:
                if item.input_file not in self._verification:
                    self._design_item(item)
                elif item.kind is not ItemKind.MODULE:
                    self._error(
                        item.source,
                        f"{item.kind.value} in a file included by verification file "
                        f"{item.input_file} cannot be rewritten",
                    )
                continue
            if file not in self._verification:
                self._design_item(item)
                continue
            if item.kind is ItemKind.CHECKER:
                self._error(
                    item.source, "checker declarations in verification files are not supported yet"
                )
                continue
            if item.kind is ItemKind.MODULE:
                continue
            if item.macro:
                self._error(
                    item.source,
                    f"{item.kind.value} produced by a macro expansion cannot be rewritten",
                )
                continue
            match item.kind:
                case ItemKind.ASSERTION:
                    if item.container is not Container.MODULE:
                        self._error(
                            item.source,
                            f"concurrent assertions inside a {item.container.value} are not "
                            "supported (a monitor instance needs a module scope)",
                        )
                    else:
                        assertions.append(item)
                case ItemKind.BIND | ItemKind.CHECKER | ItemKind.MODULE:
                    pass  # binds: see _binds; the others are handled above
                case (
                    ItemKind.PROPERTY
                    | ItemKind.SEQUENCE
                    | ItemKind.DEFAULT_CLOCKING
                    | ItemKind.CLOCKING
                    | ItemKind.DEFAULT_DISABLE
                ):
                    self._edits[file].append(_Edit(item.start, item.end, self._blank(item)))
        return assertions

    def _design_item(self, item: SyntaxItem) -> None:
        if item.kind is ItemKind.MODULE:
            return
        message = (
            f"{item.kind.value} in design file {item.input_file} is not translated and would "
            "reach Yosys unchanged (read_verilog applies its own SVA semantics, if any, and "
            "ignores bind, or since Yosys 0.70 stops at it); move it to a verification file, "
            "or accept this with --allow-design-sva"
        )
        if self._design_sva is Severity.ERROR:
            self._error(item.source, message)
        else:
            self._warning(item.source, message)

    def _blank(self, item: SyntaxItem) -> str:
        """The line breaks of the item's text, so that later text keeps its lines."""
        text = self._texts[item.source.file][item.start : item.end]
        return "".join(c for c in text if c in "\r\n")

    # --- assertions --------------------------------------------------------------------

    def _rejections(self, assertions: list[SyntaxItem]) -> set[_Site]:
        """Report rejections; return the assertion sites they belong to."""
        rejected: set[_Site] = set()
        for rejection in self._conversion.rejections:
            qualified = f"{rejection.scope}.{rejection.name or '(unnamed)'}"
            source = rejection.source
            owner = next(
                (a for a in assertions if source is not None and _contains(a.source, source)),
                None,
            )
            if owner is not None:
                rejected.add(_site(owner.source))
            elif source is not None and source.file not in self._verification:
                # Not inside a translated assertion and not in a verification file: a
                # design-file assertion (possibly included), reported with its syntax item.
                continue
            self._error(source, f"{qualified}: {rejection.message}")
        return rejected

    def _assertions(self, assertions: list[SyntaxItem], rejected: set[_Site]) -> list[str]:
        groups: dict[_Site, list[Directive]] = {}
        for directive in self._conversion.directives:
            groups.setdefault(_site(directive.source), []).append(directive)
        skips: dict[_Site, list[Skipped]] = {}
        for skip in self._conversion.skipped:
            skips.setdefault(_site(skip.source), []).append(skip)
        monitors: list[str] = []
        sites = 0
        skipped_sites = 0
        for item in assertions:
            site = _site(item.source)
            if site in rejected:
                continue
            found = groups.get(site)
            skipped = skips.get(site)
            if skipped is not None:
                if found is not None:
                    self._error(
                        item.source,
                        "the elaborated instances of this assertion differ: some are X-checks "
                        f"that check nothing ({skipped[0].reason}) and are skipped, others "
                        "are not",
                    )
                else:
                    self._skip(item, skipped, skipped_sites)
                    skipped_sites += 1
                continue
            if found is None:
                self._error(
                    item.source,
                    "assertion not elaborated (it is in an uninstantiated module or generate "
                    "branch)",
                )
                continue
            spliced = self._splice(item, found, sites)
            sites += 1
            if spliced is not None:
                monitors.extend(spliced)
                self._dropped(site)
        return monitors

    def _skip(self, item: SyntaxItem, skipped: list[Skipped], number: int) -> None:
        """Remove the skipped X-check ``item`` (docs/semantics.md §8a), whose elaborated
        instances are ``skipped``; one note.

        The skip holds for the parameter values of those instances only (a parameter may
        have folded a condition, as in ``if (K) !$isunknown(a) else b``). Without visible
        parameters, a comment replaces the directive: inside a procedural block after a
        null statement (so that ``if (c) …;`` stays valid), elsewhere in a generate block
        (so that an unbraced generate body stays valid). With parameters, the comment is
        selected by a ``generate if`` on their values, and any other combination falls
        through to a failing branch, as for a monitor site."""
        first = skipped[0]
        reason = first.reason.removeprefix("X-check: ")
        comment = f"/* sva2yosys: skipped (X-check): {reason} */"
        base = f"__sva2yosys_x{number}"
        name_sets = {tuple(name for name, _ in s.parameters) for s in skipped}
        if len(name_sets) != 1:
            self._error(
                item.source, "the elaborated instances of this assertion see different parameters"
            )
            return
        names = _compared(list(next(iter(name_sets))), skipped)
        if names:
            conditions = self._selection(item, names, [skipped])
            if conditions is None:
                return
            self._place(
                item,
                f"if ({conditions[0]}) begin : {base}_skip {comment} end "
                f"else begin : {base}_unexpected"
                " /* sva2yosys: the X-check was skipped for other parameter values */"
                " always @* assert (1'b0); end",
            )
        elif item.insert_at != 0:
            edits = self._edits[item.source.file]
            edits.append(_Edit(item.start, item.end, "; " + comment + self._blank(item)))
        else:
            # one statement under an unbraced generate if/else or loop (see _splice)
            self._place(item, f"if (1'b1) begin : {base}_skip {comment} end else begin end")
        qualified = f"{first.scope}.{first.name or '(unnamed)'}"
        self._skipped.append(
            Diagnostic(Severity.NOTE, first.source, f"{qualified}: skipped (X-check): {reason}")
        )

    def _dropped(self, site: _Site) -> None:
        """The warnings of the converted directives of ``site``, once per message."""
        seen: set[str] = set()
        for warning in self._conversion.warnings:
            if _site(warning.directive) != site or warning.message in seen:
                continue
            seen.add(warning.message)
            qualified = f"{warning.scope}.{warning.name or '(unnamed)'}"
            self._warning(warning.source or warning.directive, f"{qualified}: {warning.message}")

    def _splice(
        self, item: SyntaxItem, directives: list[Directive], number: int
    ) -> list[str] | None:
        """Replace the assertion ``item`` by its monitor instance(s); return the monitors.

        A property ``and`` gets one monitor (and instance) per conjunct, each asserting or
        assuming its own conjunct: together they fail exactly when the conjunction does
        (``docs/semantics.md`` §9.3).

        Instances that give the same monitor share it. When they differ (a parameter-
        dependent bound or width, a generate index), each variant gets its own module and
        the site selects one with a ``generate if`` on the parameters and genvars whose
        values differ between the instances; a parameter combination no instance had
        (e.g. another elaboration of the same module) fails loudly instead of losing the
        assertion.
        """
        for directive in directives:
            try:
                validate_directive(directive, limits=self._limits)
            except CompileError as error:
                self._error(
                    item.source, f"{directive.scope}.{directive.name or '(unnamed)'}: {error}"
                )
                return None
        split = [monitor_directives(d) for d in directives]
        count = len(split[0])
        if any(len(parts) != count for parts in split):
            self._error(item.source, "the elaborated instances of this assertion differ in shape")
            return None
        monitors: list[str] = []
        texts: list[str] = []
        # The assertion as written (its statement label included), from the source text.
        written = fold_whitespace(self._texts[item.source.file][item.start : item.end])
        for index in range(count):
            suffix = "" if count == 1 else f"_c{index}"
            site = self._site_monitor(
                item,
                [parts[index] for parts in split],
                f"__sva2yosys_p{number}{suffix}",
                f"__sva2yosys_i{number}{suffix}",
                (index, count),
                written,
            )
            if site is None:
                return None
            texts.append(site[0])
            monitors.extend(site[1])
        text = " ".join(texts)
        if count > 1:
            # One statement under an unbraced generate if/else or loop, and one scope. The
            # empty else binds this if's own else, so an enclosing unbraced if keeps its else.
            text = f"if (1'b1) begin : __sva2yosys_p{number}_and {text} end else begin end"
        # A block comment: other code may follow on the same line.
        self._place(item, f"{text} {block_comment(written)}")
        return monitors

    def _site_monitor(
        self,
        item: SyntaxItem,
        directives: list[Directive],
        base: str,
        instance: str,
        part: tuple[int, int],
        written: str,
    ) -> tuple[str, list[str]] | None:
        """The instance text and monitor modules of one conjunct (``directives`` are its
        elaborated instances; ``part`` is its index and the number of conjuncts;
        ``written`` is the assertion's text as written, for the monitor headers); ``None``
        after an error. A conjunct that can never fail is warned about once per message."""
        never_fail: set[str] = set()
        groups: dict[tuple[str, str, Form], list[Directive]] = {}
        machines: dict[tuple[str, str, Form], Monitor] = {}
        for directive in directives:
            qualified = f"{directive.scope}.{directive.name or '(unnamed)'}"
            try:
                machine = compile_monitor(directive, limits=self._limits)
            except CompileError as error:
                self._error(item.source, f"{qualified}: {error}")
                return None
            problem = frontend_problem((machine,), self._frontend)
            if problem is not None:
                self._error(item.source, f"{qualified}: {problem}")
                return None
            mapping: dict[str, str] = {}
            for path in (directive.clock.signal.path, *monitor_ports(machine)):
                local = _local_name(path, directive.scope)
                clash = local in mapping.values() and mapping.get(path) != local
                if local is None or clash:
                    self._error(
                        item.source,
                        f"{qualified}: signal {path} is not a plain identifier of the "
                        "assertion's scope or an enclosing scope of the same instance",
                    )
                    return None
                mapping[path] = local
            # The monitor of the directive as written at the site: signals named as there,
            # no instance path. Instances that agree on it share one monitor.
            local_directive = _localize(directive, mapping)
            try:
                local_machine = compile_monitor(local_directive, limits=self._limits)
                if directive.kind is DirectiveKind.COVER:
                    self._covers[(item.source, qualified)] = None
                message = never_fails_warning(local_machine, *part)
                if message is not None and message not in never_fail:
                    never_fail.add(message)
                    self._warning(item.source, f"{qualified}: {message}")
                ports = "".join(f", .i{i}({n})" for i, n in enumerate(monitor_ports(local_machine)))
                connections = (
                    f"(.clk({local_directive.clock.signal.path}){ports}, .fail(), .success());"
                )
                form = (
                    choose(local_machine, self._strategy)
                    if isinstance(local_machine, Machine)
                    else Form.DIRECT
                )
                key = (
                    monitor_module(local_machine, base, form, limits=self._limits, written=written),
                    connections,
                    form,
                )
            except (CompileError, BudgetError) as error:
                self._error(item.source, f"{qualified}: {error}")
                return None
            groups.setdefault(key, []).append(directive)
            machines[key] = local_machine
        variants = list(groups)
        everyone = [d for key in variants for d in groups[key]]
        name_sets = {tuple(name for name, _ in d.parameters) for d in everyone}
        if len(name_sets) != 1:
            self._error(
                item.source,
                "the elaborated instances of this assertion see different parameters",
            )
            return None
        names = _compared(list(next(iter(name_sets))), everyone)
        if not names:
            # Nothing parametrises this site: every elaboration gives the same monitor.
            if len(variants) != 1:
                self._error(
                    item.source,
                    f"the {len(everyone)} elaborated instances of this assertion need "
                    "different monitors, but no parameter tells them apart",
                )
                return None
            ((monitor, connections, form),) = variants
            self._record(base, form, machines[variants[0]], len(everyone), everyone[0])
            return f"{base} {instance} {connections}", [monitor]
        conditions = self._selection(item, names, [groups[key] for key in variants])
        if conditions is None:
            return None
        monitors: list[str] = []
        branches: list[str] = []
        for index, key in enumerate(variants):
            _, connections, form = key
            name = base if len(variants) == 1 else f"{base}_v{index}"
            monitors.append(
                monitor_module(machines[key], name, form, limits=self._limits, written=written)
            )
            self._record(name, form, machines[key], len(groups[key]), groups[key][0])
            branches.append(
                f"if ({conditions[index]}) begin : {base}_g{index} "
                f"{name} {instance} {connections} end"
            )
        unexpected = (
            f"else begin : {base}_unexpected"
            " /* sva2yosys: no monitor was generated for these parameter values */"
            " always @* assert (1'b0); end"
        )
        return " else ".join(branches) + " " + unexpected, monitors

    def _place(self, item: SyntaxItem, text: str) -> None:
        """Put the instance ``text`` of the assertion ``item`` in place of it, or, for an
        assertion in a procedural block (which cannot hold an instance), leave a null
        statement there and insert the instance right after the outermost block."""
        edits = self._edits[item.source.file]
        if item.insert_at < 0:
            self._error(
                item.source,
                "a procedural concurrent assertion in an always block that is the unbraced "
                "body of a generate construct is not supported: wrap the block in begin ... end",
            )
        elif item.insert_at:
            edits.append(_Edit(item.start, item.end, ";" + self._blank(item)))
            edits.append(_Edit(item.insert_at, item.insert_at, " " + text))
        else:
            edits.append(_Edit(item.start, item.end, text + self._blank(item)))

    def _part_warnings(self) -> None:
        """Warn at each liveness part when a ``live`` task would hold several of them: SBY
        decides the task by its first justice verdict, so the others could fail unseen.
        One monitor shared by several elaborated instances is several parts, and no define
        tells its instances apart."""
        total = sum(instances for _, _, _, instances in self._parts)
        if total < 2:
            return
        for name, qualified, source, instances in self._parts:
            script = f"sva2yosys_read_live_{live_part(name)}.ys"
            if instances == 1:
                advice = f"check this part alone with {script}"
            else:
                advice = (
                    f"its {instances} elaborated instances share one monitor, which no define "
                    "separates: check them in tasks of their own, e.g. one instance in each design"
                )
            self._warning(source, f"{qualified}: {live_parts_warning(total, advice)}")

    def _record(
        self, name: str, form: Form, machine: Monitor, instances: int, original: Directive
    ) -> None:
        self._forms[name] = form
        if isinstance(machine, ConfigMachine) and machine.liveness is not None:
            self._live = True
            directive = machine.directive
            if directive.kind is DirectiveKind.ASSERT:
                qualified = f"{original.scope}.{original.name or '(unnamed)'}"
                self._parts.append((name, qualified, directive.source, instances))
        self._fair = self._fair or is_fairness_assumption(machine)
        if self._want_equivalence and form is not Form.DIRECT and isinstance(machine, Machine):
            try:
                self._equivalence[name] = equivalence_check(machine, name, form)
            except BudgetError as error:
                self._error(machine.directive.source, str(error))

    def _selection(
        self,
        item: SyntaxItem,
        names: list[str],
        variants: Seq[Seq[Directive | Skipped]],
    ) -> list[str] | None:
        """Per variant, a constant condition that holds exactly for the values that
        *every* parameter visible at the site (``names``) had in its instances, so that
        any other elaboration falls through to the failing branch; ``None`` (after an
        error) when a visible parameter is not integral or the values do not determine
        the variant."""
        everyone = [d for group in variants for d in group]
        # the rule of the conversion: a directive with such a parameter does not reach
        # this point; the check stays as a guard for directives built another way
        opaque = sorted(
            {
                n
                for d in everyone
                for n in uncomparable_parameters(d.parameters, d.dependencies)
                if n in names
            }
        )
        if opaque:
            self._error(item.source, uncomparable_message(tuple(opaque)))
            return None
        owner: dict[tuple[str, ...], int] = {}
        for index, group in enumerate(variants):
            for directive in group:
                values = dict(directive.parameters)
                combo = tuple(_match(name, values[name]) for name in names)
                if owner.setdefault(combo, index) != index:
                    owner[combo] = -1
        if -1 in owner.values():
            self._error(
                item.source,
                f"the {len(everyone)} elaborated instances of this assertion need different "
                "monitors, and their parameters and genvars do not tell them apart",
            )
            return None
        conditions: list[str] = []
        for index in range(len(variants)):
            combos = sorted(combo for combo, owned in owner.items() if owned == index)
            conditions.append(" || ".join(f"({' && '.join(combo)})" for combo in combos))
        return conditions

    # --- binds -------------------------------------------------------------------------

    def _binds(self) -> None:
        modules: dict[str, list[SyntaxItem]] = {}
        for item in self._items:
            if item.kind is ItemKind.MODULE:
                modules.setdefault(item.name, []).append(item)
        for item in self._items:
            file = item.source.file
            if item.kind is not ItemKind.BIND or file not in self._verification or item.macro:
                continue
            if item.instance_bind:
                self._error(
                    item.source,
                    f"instance bind to {item.name} is not supported yet; bind the module type",
                )
                continue
            if self._frontend is YosysFrontend.SLANG:
                continue  # read_slang applies the bind: the statement stays as written
            targets = modules.get(item.name, [])
            if len(targets) != 1:
                self._error(
                    item.source,
                    f"bind target '{item.name}' is not a module declared once in the inputs "
                    "(binds into interfaces and programs are not supported)",
                )
                continue
            (target,) = targets
            if target.macro or target.source.file not in self._texts:
                self._error(
                    item.source,
                    f"bind target module '{item.name}' is produced by a macro or written in "
                    "an included file",
                )
                continue
            text = self._texts[file][item.insert_at : item.insert_end]
            self._edits[file].append(_Edit(item.start, item.end, self._blank(item)))
            target_text = self._texts[target.source.file]
            line_start = target_text.rfind("\n", 0, target.insert_at) + 1
            if target_text[line_start : target.insert_at].strip():
                insertion = _Edit(target.insert_at, target.insert_at, f"\n{text}\n")
            else:
                insertion = _Edit(line_start, line_start, f"{text}\n")
            self._edits[target.source.file].append(insertion)

    # --- output ------------------------------------------------------------------------

    def _outputs(self) -> tuple[OutputFile, ...]:
        files: list[OutputFile] = []
        for name in self._order:
            edits = self._edits[name]
            if name in self._verification:
                role = FileRole.VERIFICATION
            elif edits:
                role = FileRole.DESIGN_COPY
            else:
                continue
            text = _apply(self._texts[name], edits)
            files.append(OutputFile(name, PurePath(name.replace("\\", "/")).name, role, text))
        seen: dict[str, str] = {}
        for output in files:
            if output.name == MONITORS_FILE:
                self._error(
                    None, f"{output.source}: an input file must not be named {MONITORS_FILE}"
                )
            elif output.name in seen:
                self._error(
                    None,
                    f"output files {seen[output.name]} and {output.source} have the same name "
                    f"{output.name}",
                )
            seen.setdefault(output.name, output.source)
        return tuple(files)


def _compared(names: list[str], directives: Seq[Directive | Skipped]) -> list[str]:
    """The parameters of ``names`` (all visible at a site) that select the monitor of an
    elaboration: those some directive of the site can depend on; every one when any
    directive's dependencies are unknown. A parameter outside the union cannot change the
    monitor of any instance, so it is not compared."""
    union: set[str] = set()
    for directive in directives:
        if directive.dependencies is None:
            return names
        union |= directive.dependencies
    return [name for name in names if name in union]


def _apply(text: str, edits: list[_Edit]) -> str:
    pieces: list[str] = []
    cursor = 0
    for edit in sorted(edits, key=lambda e: (e.start, e.end)):
        if edit.start < cursor:
            raise AssertionError(f"overlapping edits at offset {edit.start}")
        pieces.append(text[cursor : edit.start])
        pieces.append(edit.text)
        cursor = edit.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _local_name(path: str, scope: str) -> str | None:
    """The name of ``path`` as written in ``scope``, if it is a plain identifier declared
    in ``scope`` or an enclosing scope."""
    parent, dot, name = path.rpartition(".")
    if not dot or _IDENTIFIER.fullmatch(name) is None:
        return None
    if scope != parent and not scope.startswith(parent + "."):
        return None
    return name


def _localize(directive: Directive, mapping: dict[str, str]) -> Directive:
    """``directive`` with its signals renamed by ``mapping`` and no scope. A signal that
    ``mapping`` lacks is one the monitor does not read (e.g. ``a`` in ``a[*0] ##0 b``, an
    empty match that never matches), so it is no port and its name never reaches the output:
    it keeps its path."""

    def expr(node: Expr) -> Expr:
        match node:
            case Signal(path=path):
                return replace(node, path=mapping.get(path, path))
            case Const() | FirstSample():
                return node
            case Unary(operand=operand) | Convert(operand=operand) | Sampled(operand=operand):
                return replace(node, operand=expr(operand))
            case Binary(left=left, right=right):
                return replace(node, left=expr(left), right=expr(right))
            case Conditional(condition=condition, then=then, otherwise=otherwise):
                return replace(
                    node, condition=expr(condition), then=expr(then), otherwise=expr(otherwise)
                )
            case BitSelect(value=inner) | RangeSelect(value=inner) | Replicate(value=inner):
                return replace(node, value=expr(inner))
            case Concat(parts=parts):
                return replace(node, parts=tuple(expr(part) for part in parts))

    def sequence(node: Sequence) -> Sequence:
        match node:
            case Bool(expr=inner):
                return Bool(expr(inner))
            case SeqConcat(left=left, right=right):
                return replace(node, left=sequence(left), right=sequence(right))
            case Repeat(sequence=inner):
                return replace(node, sequence=sequence(inner))
            case Goto(condition=condition) | NonConsecutive(condition=condition):
                return replace(node, condition=expr(condition))
            case SeqOr(left=left, right=right):
                return SeqOr(sequence(left), sequence(right))
            case SeqAnd(left=left, right=right):
                return SeqAnd(sequence(left), sequence(right))
            case SeqIntersect(left=left, right=right):
                return SeqIntersect(sequence(left), sequence(right))
            case Within(inner=inner, outer=outer):
                return Within(sequence(inner), sequence(outer))
            case Throughout(condition=condition, sequence=inner):
                return Throughout(expr(condition), sequence(inner))

    def prop(node: Property) -> Property:
        match node:
            case SequenceProperty(sequence=body, strength=strength):
                return SequenceProperty(sequence(body), strength)
            case Implication(antecedent=antecedent, consequent=consequent):
                return replace(node, antecedent=sequence(antecedent), consequent=prop(consequent))
            case Not(operand=operand):
                return Not(prop(operand))
            case Nexttime() | Always() | Eventually():
                return replace(node, operand=prop(node.operand))
            case Until(left=left, right=right):
                return replace(node, left=expr(left), right=expr(right))
            case PropAnd(operands=operands):
                return PropAnd(tuple(prop(operand) for operand in operands))
            case PropOr(operands=operands):
                return PropOr(tuple(prop(operand) for operand in operands))
            case PropIf(condition=condition, then=then, otherwise=otherwise):
                return PropIf(
                    expr(condition),
                    prop(then),
                    prop(otherwise) if otherwise is not None else None,
                )

    clock = directive.clock
    signal = clock.signal
    return replace(
        directive,
        scope="",
        clock=replace(clock, signal=replace(signal, path=mapping[signal.path])),
        disable=expr(directive.disable) if directive.disable is not None else None,
        property=prop(directive.property),
    )


def _match(name: str, value: ParameterValue | None) -> str:
    """The constant condition that ``name`` has ``value``: its value, and also its width
    and signedness when the declaration leaves them to the value (``P == 1`` alone also
    holds for ``P = 8'b1``). ``(P - P) - 1`` is negative exactly when ``P`` is signed."""
    assert value is not None
    text = f"{name} == {value.literal}"
    if value.implicit_type is not None:
        width, signed = value.implicit_type.width, value.implicit_type.signed
        text += f" && $bits({name}) == {width} && ((({name} - {name}) - 1) < 0) == 1'b{int(signed)}"
    return text
