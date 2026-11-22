"""Export the concurrent assertions of an elaborated design as JSON.

The node encoding is a **subset of Slang's native ``--ast-json`` format** (same
``kind`` names, keys and source-range keys; checked against the slang executable).
Keys that Slang does not write carry the prefix ``sva2yosys_``. The document as a
whole is an **internal interface** between this exporter and
:mod:`sva2yosys.slang.convert`, versioned in lockstep (``FORMAT_VERSION``): the
converter requires our own analyses (procedural paths, disable timing, ...), so the
native output alone cannot be converted. Version 2 added ``sva2yosys_disableIssue``
and the procedural keys.

Envelope (ours)::

    {"format": "sva2yosys-slang-ast", "formatVersion": 2, "slangVersion": "<pyslang>",
     "assertions": [<record>, ...], "sva2yosys_syntax": [<item>, ...],
     "sva2yosys_includes": [<include>, ...], "sva2yosys_packages": [<package>, ...],
     "sva2yosys_packageUses": [<package use>, ...]}

Package (ours): one per package declaration, in input order::

    {"name": <package name>, "sva2yosys_inputFile": <the input file declaring it,
     directly or through an include>}

Package use (ours): one per input file and package name it refers to (``import p::…``
or ``p::name``; a name used with ``::`` that is not a package, e.g. a class, is listed
too), in input order::

    {"name": <name before ::>, "sva2yosys_inputFile": <the input file>}

Include (ours): one per ``include`` directive of every input file, nested ones included,
in input order::

    {"sva2yosys_inputFile": <the input file being preprocessed>,
     "path": <the path as written>, "sva2yosys_resolved": <the file Slang opened>}

Syntax item (ours; the inventory used by source rewriting): one per SVA-related syntax
node of every syntax tree, elaborated or not, in tree order, then source order::

    {"kind": "ConcurrentAssertion" | "PropertyDeclaration" | "SequenceDeclaration"
             | "DefaultClocking" | "ClockingDeclaration" | "DefaultDisableDeclaration"
             | "BindDirective" | "CheckerDeclaration" | "ModuleDeclaration",
     <source range>, "sva2yosys_start": <offset>, "sva2yosys_end": <offset>,
     "sva2yosys_macro": <bool>, "sva2yosys_container": "none" | "module" | "interface"
             | "program" | "package" | "checker",
     "sva2yosys_inputFile": <the input file containing it, directly or by inclusion>,
     "name": <module name, or bind target as written; else "">,
     "sva2yosys_instanceBind": <bool>,
     "sva2yosys_insertAt": <offset of endmodule, or of a bind's instantiation, or (for an
             assertion in a procedural block) just after the outermost such block, -1 when
             that block is the unbraced body of a generate construct; else 0>,
     "sva2yosys_insertEnd": <end offset of a bind's instantiation; else 0>}

Offsets index the input text as given (code points, not Slang's byte offsets); for a
file that is not an input (an included file) they are byte offsets. Ranges exclude
leading trivia and include a statement label and the closing ``;``. For an
item produced by a macro expansion the source range is the expansion's location and the
offsets are not meaningful.

Record, one per concurrent assertion **per elaborated instance**, ordered by scope
path, then source position::

    {"kind": "ConcurrentAssertion", "assertionKind": "Assert" | "Assume" | ...,
     "name": "<label or empty>", "scope": "<hierarchical path of the enclosing scope>",
     <source range>, "propertySpec": <AssertionExpr>,
     "sva2yosys_procedural": <bool: inside a user-written procedural block>,
     (procedural records only:)
     "sva2yosys_procedureKind": "always" | "initial"  (an ``initial`` block gives
         the directive one attempt, at the first tick of its clock, IEEE 1800-2017
         16.14.6; every other block kind is "always" with a procedure issue),
     "sva2yosys_procedureClock": <TimingControl or null: the block's single SignalEvent;
         null for an ``initial`` block without an event control>,
     "sva2yosys_enable": [{"kind": "If", "expr", "negated"}
                          | {"kind": "Case", "expr", "items": [Expression, ...], "negated"}],
         the conditions on the path from the block to the assertion, outermost first,
     "sva2yosys_procedureIssue": <null, or why it cannot be lowered (docs/semantics.md §6)>,
     "sva2yosys_clock": <TimingControl or null: Slang's semantic leading clock>,
     "sva2yosys_defaultDisable": <Expression or null: the scope's default disable iff>,
     "sva2yosys_insideIssue": <present only when the design has an ``inside`` that Slang
                               may evaluate as a constant and that is not found among the
                               typed expressions (e.g. a sequence argument): a message; the
                               converter rejects the record>,
     "sva2yosys_constantInsides": <present only when not empty: the ``Inside`` expressions
                               that Slang evaluates as constants (parameters, generate
                               conditions, functions ...); the converter checks that each
                               list is uniform>,
     "sva2yosys_disableIssue": <null, or why the disable iff in effect (explicit, else the
             default) reads a signal that may change at the clock edge (docs/semantics.md §7)>,
     "sva2yosys_parameters": [{"name", "value"}, ...]: the parameters and genvars
         visible at the assertion, i.e. declared before it (inner ones hiding outer ones);
         ``value`` is an integer literal, or null for a non-integral value (string, real,
         X/Z), a type parameter, an interface port (it carries the connected
         instance's parameters), or a declaration in another compilation unit; for a
         parameter whose declaration fixes no width (``parameter P = 1``), also its
         ``"width"`` and ``"signed"``,
     "sva2yosys_dependencies": [name, ...] or null: the sorted names, among those of
         ``sva2yosys_parameters``, that the monitor of the assertion can depend on
         (:mod:`sva2yosys.slang.dependencies`); null when the analysis cannot classify some
         reference (every visible parameter then counts)},
     "sva2yosys_hiddenParameters": [name, ...]: the sorted names of the parameters the
         assertion depends on that are hidden at its position by another declaration of the
         same name (the converter rejects the record)}

Source range (on records, AssertionExpr, Expression and TimingControl nodes)::

    "source_file_start", "source_file_end", "source_line_start", "source_line_end",
    "source_column_start", "source_column_end"

Exported node kinds (native keys; any other kind is written as
``{"kind": <name>, "sva2yosys_unsupported": true, <source range>}`` and never dropped):

- AssertionExpr: ``Simple`` (``expr``, optional ``repetition`` = ``{kind, min, max}``),
  ``SequenceConcat`` (``elements`` = ``[{sequence, min, max}]``), ``Binary`` (``op``,
  ``left``, ``right``), ``Unary`` (``op``, ``expr``, optional ``min``/``max``),
  ``SequenceWithMatch`` (``expr``, optional ``repetition``, ``matchItems``: the form
  Slang uses for a repeated parenthesised sequence such as ``(b ##1 c)[*2]``),
  ``StrongWeak`` (``strength`` = ``Strong``/``Weak``, ``expr``), ``Conditional``
  (``condition``, ``ifExpr``, optional ``elseExpr``: a property ``if``),
  ``Clocking`` (``clocking``, ``expr``), ``DisableIff`` (``condition``, ``expr``).
  An unbounded ``max`` is ``"$"``.
- TimingControl: ``SignalEvent`` (``expr``, ``edge``, optional ``iff``).
- Expression (all with ``type``; ``sva2yosys_integral``, never absent: ``false`` when the type
  is not integral, else
  ``{"width", "signed", "msb", "lsb", "dims"[, "aggregate": true]}`` from the canonical type
  (a typedef is its target): ``msb``/``lsb`` are the bounds of the outermost packed
  dimension, else ``width-1``/``0``; ``dims`` is the number of packed dimensions (each packed
  array counts one, an enum or a packed struct/union counts one flat dimension, a scalar
  such as ``int``, ``byte`` or ``logic`` none); ``aggregate`` marks a packed struct or union
  (innermost element). ``false`` for unpacked arrays, ``real``, ``string``, ...;
  ``constant`` when Slang folded it, or when Slang's
constant evaluation (``ASTContext.tryEval``, in the scope containing the assertion) gives
an integer without X/Z bits, e.g. ``-1``, parameters, ``$bits``): ``NamedValue``
  and ``HierarchicalValue`` (``symbol`` = the name, ``sva2yosys_path`` = hierarchical
  path; a ``NamedValue`` also has ``sva2yosys_visibleAtSite``: its name, looked up from
  the scope containing the assertion, finds the same symbol; and ``sva2yosys_sampledDefault``:
  ``"x"`` for a 4-state net or an uninitialised 4-state variable, ``"known"`` otherwise,
  the class of its default sampled value, IEEE 1800 §16.5.1), ``IntegerLiteral`` and
  ``UnbasedUnsizedIntegerLiteral`` (``value``),
  ``UnaryOp`` (``op``, ``operand``), ``BinaryOp`` (``op``, ``left``, ``right``),
  ``ConditionalOp`` (``conditions`` = ``[{expr}]``, ``left``, ``right``),
  ``Conversion`` (``operand``, ``sva2yosys_fourState``: whether the target type is
  4-state; a cast to a 2-state type turns X into 0, IEEE 1800 §6.11.2;
  ``sva2yosys_conversionKind``: Slang's ``ConversionKind`` name, e.g. ``Propagated`` for
  the conversion of an operand to the type of its context, which extends by the sign of
  that type, §11.8.2),
  ``Inside`` (``left``, ``ranges`` = the set items in order, each an expression or a
  ``ValueRange``; Slang has converted the left operand and every item to one common type),
  ``ValueRange`` (``left``, ``right``: a range item ``[lo:hi]``; a ``$`` bound is an
  ``UnboundedLiteral``, which is unsupported),
  ``ElementSelect`` (``value``, ``selector``),
  ``RangeSelect`` (``selectionKind``, ``value``, ``left``, ``right``),
  ``Concatenation`` (``operands``), ``Replication`` (``count``, ``concat``), ``Call``
  of a system subroutine (``subroutine`` = name, ``arguments``), and
  ``AssertionInstance`` (``symbol`` = name, ``body``, ``isRecursiveProperty``) whose
  body is already argument-substituted by Slang.

Enumerations (``assertionKind``, ``op``, ``edge``, ``kind`` …) are written with Slang's
enumerator names, e.g. ``OverlappedImplication``, ``PosEdge``, ``LogicalNot``.
"""

from __future__ import annotations

import copy
import dataclasses
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import cast

import pyslang
from pyslang import analysis, ast, syntax

from sva2yosys.slang.dependencies import (
    DeclarationCache,
    EnclosingScope,
    assertion_dependencies,
)
from sva2yosys.slang.invoke import (
    Compiled,
    Severity,
    SlangDiagnostic,
    SlangError,
    SlangOptions,
    compile_texts,
    read_sources,
)

type JsonValue = bool | int | str | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]

FORMAT = "sva2yosys-slang-ast"
FORMAT_VERSION = 2
UNSUPPORTED_KEY = "sva2yosys_unsupported"

_PROBE_LABEL = "__sva2yosys_default_disable_"


def export_design(sources: Sequence[Path], options: SlangOptions | None = None) -> JsonObject:
    """Compile ``sources`` and export their concurrent assertions.

    The default ``disable iff`` of each scope is resolved by Slang itself through a
    second compilation of the same sources in which each ``default disable iff (e);``
    is replaced by an assertion with an explicit ``disable iff (e)`` (pyslang exposes
    no accessor for it).

    Args:
        sources: SystemVerilog files.
        options: Compilation options (defaults: automatic top, no defines).

    Returns:
        The JSON document described in the module docstring.

    Raises:
        SlangError: The sources do not compile.
    """
    return export_texts(read_sources(sources), options)


def export_texts(
    sources: Sequence[tuple[str, str]], options: SlangOptions | None = None
) -> JsonObject:
    """Like :func:`export_design` for in-memory ``(name, text)`` sources."""
    resolved = options if options is not None else SlangOptions()
    compiled = compile_texts(sources, resolved)
    defaults = _default_disables(sources, resolved, compiled)
    records, context = _collect(compiled)
    document = _document(records, defaults, context, compiled)
    document["sva2yosys_syntax"] = _inventory(compiled)
    document["sva2yosys_includes"] = _includes(compiled)
    document["sva2yosys_packages"] = _packages(compiled)
    document["sva2yosys_packageUses"] = _package_uses(compiled)
    return document


def export_compiled(compiled: Compiled) -> JsonObject:
    """Export an already compiled design, without default ``disable iff`` resolution.

    Every record's ``sva2yosys_defaultDisable`` is ``null``.
    """
    records, context = _collect(compiled)
    document = _document(records, {}, context, compiled)
    document["sva2yosys_syntax"] = _inventory(compiled)
    document["sva2yosys_includes"] = _includes(compiled)
    document["sva2yosys_packages"] = _packages(compiled)
    document["sva2yosys_packageUses"] = _package_uses(compiled)
    return document


# --- syntax inventory ------------------------------------------------------------------

_ASSERTION_STATEMENTS = frozenset(
    {
        syntax.SyntaxKind.AssertPropertyStatement,
        syntax.SyntaxKind.AssumePropertyStatement,
        syntax.SyntaxKind.CoverPropertyStatement,
        syntax.SyntaxKind.CoverSequenceStatement,
        syntax.SyntaxKind.RestrictPropertyStatement,
        syntax.SyntaxKind.ExpectPropertyStatement,
    }
)

_CONTAINERS = {
    syntax.SyntaxKind.ModuleDeclaration: "module",
    syntax.SyntaxKind.InterfaceDeclaration: "interface",
    syntax.SyntaxKind.ProgramDeclaration: "program",
    syntax.SyntaxKind.PackageDeclaration: "package",
    syntax.SyntaxKind.CheckerDeclaration: "checker",
}


def _packages(compiled: Compiled) -> list[JsonValue]:
    """The ``sva2yosys_packages`` entries (module docstring)."""
    packages: list[JsonValue] = []
    trees = compiled.compilation.getSyntaxTrees()
    for name, tree in zip(compiled.texts, trees, strict=False):

        def visit(node: syntax.SyntaxNode, name: str = name) -> None:
            if node.kind == syntax.SyntaxKind.PackageDeclaration and isinstance(
                node, syntax.ModuleDeclarationSyntax
            ):
                packages.append({"name": node.header.name.valueText, "sva2yosys_inputFile": name})

        tree.root.visit(visit)
    return packages


def _package_uses(compiled: Compiled) -> list[JsonValue]:
    """The ``sva2yosys_packageUses`` entries (module docstring)."""
    uses: list[JsonValue] = []
    trees = compiled.compilation.getSyntaxTrees()
    for name, tree in zip(compiled.texts, trees, strict=False):
        names: list[str] = []

        def visit(node: syntax.SyntaxNode, names: list[str] = names) -> None:
            if isinstance(node, syntax.PackageImportItemSyntax):
                names.append(node.package.valueText)
            elif (
                isinstance(node, syntax.ScopedNameSyntax)
                and node.separator.valueText == "::"
                and isinstance(node.left, syntax.IdentifierNameSyntax)
            ):
                names.append(node.left.identifier.valueText)

        tree.root.visit(visit)
        uses.extend({"name": used, "sva2yosys_inputFile": name} for used in dict.fromkeys(names))
    return uses


def _includes(compiled: Compiled) -> list[JsonValue]:
    """The ``sva2yosys_includes`` entries (module docstring)."""
    sm = compiled.source_manager
    includes: list[JsonValue] = []
    trees = compiled.compilation.getSyntaxTrees()
    for name, tree in zip(compiled.texts, trees, strict=False):
        for directive in tree.getIncludeDirectives():
            resolved = sm.getFileName(pyslang.SourceLocation(directive.buffer.id, 0))
            includes.append(
                {
                    "sva2yosys_inputFile": name,
                    "path": directive.path,
                    "sva2yosys_resolved": resolved,
                }
            )
    return includes


def _inventory(compiled: Compiled) -> list[JsonValue]:
    """The ``sva2yosys_syntax`` items of every syntax tree (module docstring)."""
    sm = compiled.source_manager
    items: list[JsonValue] = []
    encoded: dict[str, bytes] = {}

    def offset(location: pyslang.SourceLocation) -> int:
        """Slang's byte offset as an index into the decoded input text."""
        name = sm.getFileName(location)
        text = compiled.texts.get(name)
        if text is None:
            return location.offset  # not an input file (an include): bytes
        data = encoded.setdefault(name, text.encode("utf-8"))
        return len(data[: location.offset].decode("utf-8", errors="ignore"))

    def item(node: syntax.SyntaxNode, kind: str, container: str, **extra: JsonValue) -> None:
        source_range = node.sourceRange
        macro = sm.isMacroLoc(source_range.start) or sm.isMacroLoc(source_range.end)
        start = sm.getFullyExpandedLoc(source_range.start)
        end = sm.getFullyExpandedLoc(source_range.end)
        root = start
        while sm.isIncludedFileLoc(root):
            root = sm.getIncludedFrom(root.buffer)
        items.append(
            {
                "kind": kind,
                "source_file_start": sm.getFileName(start),
                "source_file_end": sm.getFileName(end),
                "source_line_start": sm.getLineNumber(start),
                "source_line_end": sm.getLineNumber(end),
                "source_column_start": sm.getColumnNumber(start),
                "source_column_end": sm.getColumnNumber(end),
                "sva2yosys_start": 0 if macro else offset(source_range.start),
                "sva2yosys_end": 0 if macro else offset(source_range.end),
                "sva2yosys_macro": macro,
                "sva2yosys_container": container,
                "sva2yosys_inputFile": sm.getFileName(root),
                "name": "",
                "sva2yosys_instanceBind": False,
                "sva2yosys_insertAt": 0,
                "sva2yosys_insertEnd": 0,
                **extra,
            }
        )

    def walk(
        node: syntax.SyntaxNode,
        container: str,
        procedure_end: int = 0,
        parent: syntax.SyntaxKind | None = None,
    ) -> None:
        kind = node.kind
        inner = container
        if isinstance(node, syntax.ProceduralBlockSyntax) and procedure_end == 0:
            # An instance inserted after the block must stay in the block's scope: not so
            # when the block is the unbraced body of a generate construct (-1).
            braced = parent in (
                syntax.SyntaxKind.ModuleDeclaration,
                syntax.SyntaxKind.GenerateBlock,
            )
            procedure_end = offset(node.sourceRange.end) if braced else -1
        if isinstance(node, syntax.ModuleDeclarationSyntax):
            inner = _CONTAINERS.get(kind, container)
            if kind == syntax.SyntaxKind.ModuleDeclaration:
                item(
                    node,
                    "ModuleDeclaration",
                    container,
                    name=node.header.name.valueText,
                    sva2yosys_insertAt=offset(node.endmodule.location),
                )
        elif isinstance(node, syntax.CheckerDeclarationSyntax):
            inner = "checker"
            item(node, "CheckerDeclaration", container)
        elif kind in _ASSERTION_STATEMENTS:
            item(node, "ConcurrentAssertion", container, sva2yosys_insertAt=procedure_end)
        elif isinstance(node, syntax.PropertyDeclarationSyntax):
            item(node, "PropertyDeclaration", container)
        elif isinstance(node, syntax.SequenceDeclarationSyntax):
            item(node, "SequenceDeclaration", container)
        elif isinstance(node, syntax.DefaultDisableDeclarationSyntax):
            item(node, "DefaultDisableDeclaration", container)
        elif isinstance(node, syntax.ClockingDeclarationSyntax):
            default = node.globalOrDefault.valueText == "default"
            item(node, "DefaultClocking" if default else "ClockingDeclaration", container)
        elif kind == syntax.SyntaxKind.DefaultClockingReference:
            item(node, "DefaultClocking", container)
        elif isinstance(node, syntax.BindDirectiveSyntax):
            instantiation = node.instantiation.sourceRange
            item(
                node,
                "BindDirective",
                container,
                name=str(node.target).strip(),
                sva2yosys_instanceBind=(
                    # The stubs declare targetInstances non-optional; it is None without
                    # a ``: inst, …`` list.
                    node.target.kind != syntax.SyntaxKind.IdentifierName
                    or cast("object", node.targetInstances) is not None
                ),
                sva2yosys_insertAt=offset(instantiation.start),
                sva2yosys_insertEnd=offset(instantiation.end),
            )
        for child in cast("Iterable[object]", node):
            if isinstance(child, syntax.SyntaxNode):
                walk(child, inner, procedure_end, kind)

    for tree in compiled.compilation.getSyntaxTrees():
        walk(tree.root, "none")
    return items


# --- collection ----------------------------------------------------------------------


@dataclass(frozen=True)
class _Record:
    """One exported assertion and the scopes enclosing it within its instance."""

    json: JsonObject
    chain: tuple[str, ...]
    position: tuple[str, int, int]
    key: tuple[int, int] = (0, 0)
    analyzed: bool = False
    site: ast.Scope | None = None


def _document(
    records: list[_Record], defaults: dict[str, JsonObject], context: _Context, compiled: Compiled
) -> JsonObject:
    ordered = sorted(records, key=lambda r: (r.chain[-1], r.position))
    root = compiled.compilation.getRoot()
    for record in ordered:
        for scope in reversed(record.chain):
            if scope in defaults:
                record.json["sva2yosys_defaultDisable"] = copy.deepcopy(defaults[scope])
                break
        _mark_visibility(record)
        record.json["sva2yosys_disableIssue"] = _disable_issue(record.json, root, context)
    assertions: list[JsonValue] = [record.json for record in ordered]
    return {
        "format": FORMAT,
        "formatVersion": FORMAT_VERSION,
        "slangVersion": version("pyslang"),
        "assertions": assertions,
    }


@dataclass
class _Context:
    exporter: _Exporter
    manager: analysis.AnalysisManager
    records: list[_Record]
    source_manager: pyslang.SourceManager
    root: ast.RootSymbol
    # Per signal (hierarchical path): why it is not stable at a clock edge, or "".
    stability: dict[str, str] = dataclasses.field(default_factory=dict)
    # Instance bodies by instance path (pyslang gives no symbol for a parent scope).
    bodies: dict[str, ast.InstanceBodySymbol] = dataclasses.field(default_factory=dict)
    # The default clocking / default disable declarations of each scope, found once per export.
    declarations: DeclarationCache = dataclasses.field(default_factory=dict)


def _collect(compiled: Compiled) -> tuple[list[_Record], _Context]:
    manager = analysis.AnalysisManager()
    manager.analyze(compiled.compilation)
    context = _Context(
        _Exporter(compiled.source_manager),
        manager,
        [],
        compiled.source_manager,
        compiled.compilation.getRoot(),
    )
    for top in compiled.compilation.getRoot().topInstances:
        context.bodies[top.hierarchicalPath] = top.body
        _walk_scope(top.body, (top.hierarchicalPath,), context, enclosing=(top.body,))
    issue, constants = _inside_issue(compiled, context.exporter)
    for record in context.records:
        if issue is not None:
            record.json["sva2yosys_insideIssue"] = issue
        if constants:
            record.json["sva2yosys_constantInsides"] = list(constants)
    return _resolve_twins(context.records), context


_K = syntax.SyntaxKind
_INSIDE_PURE_EXPRESSIONS = frozenset(
    {
        _K.ParenthesizedExpression,
        _K.ConditionalExpression,
        _K.ConditionalPredicate,
        _K.ConcatenationExpression,
        _K.InsideExpression,
        *(
            getattr(_K, f"{name}Expression")
            for name in (
                "Add", "Subtract", "Multiply", "Divide", "Mod", "Power", "BinaryAnd",
                "BinaryOr", "BinaryXor", "BinaryXnor", "Equality", "Inequality",
                "CaseEquality", "CaseInequality", "WildcardEquality", "WildcardInequality",
                "GreaterThan", "GreaterThanEqual", "LessThan", "LessThanEqual", "LogicalAnd",
                "LogicalOr", "LogicalImplication", "LogicalEquivalence", "LogicalShiftLeft",
                "LogicalShiftRight", "ArithmeticShiftLeft", "ArithmeticShiftRight",
                "UnaryPlus", "UnaryMinus", "UnaryBitwiseNot", "UnaryBitwiseAnd",
                "UnaryBitwiseOr", "UnaryBitwiseXor", "UnaryBitwiseNand", "UnaryBitwiseNor",
                "UnaryBitwiseXnor", "UnaryLogicalNot",
            )
        ),
    }
)  # fmt: skip
"""Run-time operators: their operands are evaluated at every sample, never folded to a
constant that replaces them (unlike a replication count, a select range or a cast width)."""
_INSIDE_RUNTIME_CALLS = frozenset(
    {"$past", "$rose", "$fell", "$stable", "$changed", "$countones", "$onehot", "$onehot0",
     "$isunknown", "$signed", "$unsigned"}
)  # fmt: skip
"""System functions whose first argument is a run-time operand (``$past``'s depth is not)."""
_INSIDE_ANCHORS = frozenset({_K.SimpleSequenceExpr, _K.SimplePropertyExpr, _K.DisableIff})
"""A Boolean operand of an assertion: the converter sees it as an expression."""
_INSIDE_ASSERTION_ROOTS = frozenset(
    {
        _K.AssertPropertyStatement,
        _K.AssumePropertyStatement,
        _K.CoverPropertyStatement,
        _K.CoverSequenceStatement,
        _K.RestrictPropertyStatement,
        _K.ExpectPropertyStatement,
        _K.SequenceDeclaration,
        _K.PropertyDeclaration,
    }
)
_INSIDE_ABOVE_ANCHOR_FLAGGED = frozenset(
    {_K.OrderedArgument, _K.NamedArgument, _K.EqualsAssertionArgClause}
)
"""Above a Boolean operand: an argument or a default argument of a sequence or property
instance, which the body may use as a bound."""
_INSIDE_RUNTIME_ROOTS = frozenset(
    {
        _K.ContinuousAssign,
        _K.AlwaysBlock,
        _K.AlwaysCombBlock,
        _K.AlwaysFFBlock,
        _K.AlwaysLatchBlock,
        _K.InitialBlock,
        _K.FinalBlock,
        _K.TaskDeclaration,
    }
)
"""Design code evaluated at run time: Yosys reads it, its values are signals, not constants
folded into an assertion. (A port connection is parsed as a property expression, so an
``inside`` in one is checked like a constant one.)"""
_HOLDER_SUFFIXES = (
    "Declaration",
    "DeclarationStatement",
    "Type",
    "Dimension",
    "DimensionSpecifier",
)
_INSIDE_CONSTANT_HOLDERS = (
    frozenset(
        kind
        for kind in syntax.SyntaxKind.__members__.values()
        if kind.name.endswith(_HOLDER_SUFFIXES)
    )
    - _INSIDE_RUNTIME_ROOTS
)
"""Every declaration, type and dimension (a static classification of Slang's syntax kinds by
name, so that a new kind of declaration is covered too): inside design code, the constants
Slang evaluates there (a procedural ``localparam``, an enum value, a width) may be read by a
procedural assertion."""


def _runtime_argument(argument: syntax.SyntaxNode) -> bool:
    """Whether ``argument`` is the first argument of a run-time system function."""
    arguments = argument.parent
    call = arguments.parent
    if not isinstance(call, syntax.InvocationExpressionSyntax):
        return False
    name = str(call.left).strip()
    first = next(iter(cast("syntax.ArgumentListSyntax", arguments).parameters), None)
    return name in _INSIDE_RUNTIME_CALLS and first is argument


def _inside_trusted(node: syntax.SyntaxNode) -> bool:
    """Whether ``node`` (an ``inside``) needs no check here: either the converter sees it as
    an expression (:func:`_in_assertion_operand`), or it is in design code evaluated at run
    time (:func:`_in_runtime_code`), which Yosys reads and which folds into no assertion."""
    return _in_assertion_operand(node) or _in_runtime_code(node)


def _in_runtime_code(node: syntax.SyntaxNode) -> bool:
    """Whether ``node`` lies in design code evaluated at run time: a run-time root is reached
    without crossing a declaration, type or dimension (whose constants a procedural assertion
    may read) or the width of a size cast, and the node is not inside an assertion (where
    only :func:`_in_assertion_operand` decides)."""
    child = node
    parent = cast("syntax.SyntaxNode | None", node.parent)
    while parent is not None:
        kind = parent.kind
        if (
            kind in _INSIDE_CONSTANT_HOLDERS
            or kind in _INSIDE_ASSERTION_ROOTS
            or kind in _INSIDE_ANCHORS
            or (isinstance(parent, syntax.CastExpressionSyntax) and parent.left is child)
        ):
            return False
        if kind in _INSIDE_RUNTIME_ROOTS:
            return True
        child = parent
        parent = cast("syntax.SyntaxNode | None", parent.parent)
    return False


def _in_assertion_operand(node: syntax.SyntaxNode) -> bool:
    """Whether the converter sees ``node`` (an ``inside``) as an expression: it is reached
    from a Boolean operand of an assertion through run-time operators only, and above that
    operand no argument or default argument of a sequence or property is crossed before an
    assertion or its declaration. Anything else, including any kind of syntax not listed
    here, is not trusted (an allow-list: a construct forgotten here is checked, never
    trusted)."""
    child = node
    # the stubs declare ``parent`` non-optional; it is None above the root
    parent = cast("syntax.SyntaxNode | None", node.parent)
    while parent is not None and parent.kind not in _INSIDE_ANCHORS:
        kind = parent.kind
        if kind in (_K.OrderedArgument, _K.ArgumentList):
            if kind == _K.OrderedArgument and not _runtime_argument(parent):
                return False
        elif kind == _K.InvocationExpression:
            if not isinstance(child, syntax.ArgumentListSyntax):
                return False
        elif kind not in _INSIDE_PURE_EXPRESSIONS:
            return False
        child = parent
        parent = cast("syntax.SyntaxNode | None", parent.parent)
    if parent is None:
        return False
    if (
        parent.kind == _K.SimpleSequenceExpr
        and child is not cast("syntax.SimpleSequenceExprSyntax", parent).expr
    ):
        return False  # a repetition range
    while parent is not None:
        if parent.kind in _INSIDE_ABOVE_ANCHOR_FLAGGED:
            return False
        if parent.kind in _INSIDE_ASSERTION_ROOTS:
            return True
        parent = cast("syntax.SyntaxNode | None", parent.parent)
    return False


def _inside_issue(
    compiled: Compiled, exporter: _Exporter
) -> tuple[str | None, tuple[JsonObject, ...]]:
    """The ``inside`` expressions of the design that Slang may evaluate as constants.

    Slang evaluates constant expressions (bounds, parameters and their overrides, default
    and actual arguments that a sequence uses as a bound, generate conditions, widths,
    constant functions ...) before the converter sees them (docs/semantics.md §1). An
    ``inside`` that :func:`_inside_trusted` accepts reaches the converter as an expression.
    Every other one, also in design code, is looked up among the typed expressions of the
    design (the AST, and the conditions of generate blocks) and returned exported, so that
    the converter checks that its list is uniform; one that is not found there (e.g. a
    default argument of a sequence, a cast width) gives the issue, a message located at the
    first such ``inside``, which rejects every record."""
    found: list[pyslang.SourceLocation] = []

    def visit(node: syntax.SyntaxNode) -> None:
        if node.kind == syntax.SyntaxKind.InsideExpression and not _inside_trusted(node):
            found.append(node.sourceRange.start)

    for tree in compiled.compilation.getSyntaxTrees():
        tree.root.visit(visit)
    if not found:
        return None, ()
    typed: dict[tuple[int, int], list[ast.InsideExpression]] = {}

    def collect(node: object) -> None:
        if isinstance(node, ast.InsideExpression):
            # the node's own range: its ``syntax`` may include enclosing parentheses
            typed.setdefault(_key(node.sourceRange.start), []).append(node)
        elif isinstance(node, ast.GenerateBlockSymbol):
            condition = cast("ast.Expression | None", node.conditionExpression)
            if condition is not None:
                condition.visit(collect)

    compiled.compilation.getRoot().visit(collect)
    constants: list[JsonObject] = []
    missing: list[pyslang.SourceLocation] = []
    for location in found:
        nodes = typed.get(_key(location), [])
        if not nodes:
            missing.append(location)
        constants.extend(exporter.expr(node) for node in nodes)
    if not missing:
        return None, tuple(constants)
    sm = compiled.source_manager
    first = min(missing, key=_key)
    where = f"{sm.getFileName(first)}:{sm.getLineNumber(first)}"
    return (
        f"'inside' in a constant position that sva2yosys cannot analyse (first at {where}, "
        "e.g. an argument of a sequence or property, the width of a cast or a dimension of "
        "a type) is not supported: Slang "
        "evaluates it as a constant before the converter can check that its list is uniform"
    ), tuple(constants)


def _resolve_twins(records: list[_Record]) -> list[_Record]:
    """Give each instance the leading clock that Slang's analysis computed for its twin.

    Slang's analysis reports an assertion for only one of several instances whose
    bodies are identical. The other instances take that instance's clock, with the
    instance path rewritten to their own. Their exported properties must be
    identical under the same rewrite; otherwise the export fails rather than guess.
    """
    twins = {_twin_key(record): record for record in records if record.analyzed}
    for record in records:
        twin = twins.get(_twin_key(record))
        if record.analyzed or twin is None:
            continue
        source, target = twin.chain[0], record.chain[0]
        if _retarget(twin.json["propertySpec"], source, target) != record.json["propertySpec"]:
            message = (
                f"{target}: cannot determine the clock of {record.json['name'] or 'an assertion'}"
                f" (instance differs from {source}, the one analyzed by Slang)"
            )
            diagnostic = SlangDiagnostic(
                Severity.ERROR, "InconsistentInstanceAnalysis", message, None
            )
            raise SlangError((diagnostic,), f"error: {message}")
        record.json["sva2yosys_clock"] = _retarget(twin.json["sva2yosys_clock"], source, target)
    return records


def _mark_visibility(record: _Record) -> None:
    """Set ``sva2yosys_visibleAtSite`` on every ``NamedValue`` of the record: whether its
    name, looked up from the scope that contains the assertion, finds the same symbol.

    Names in default clocking, default disable and declared properties and sequences
    resolve where those are declared; a generate scope around the assertion may declare
    the same name for something else.
    """
    site = record.site

    def visit(node: JsonValue) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("kind") == "NamedValue":
            name = node.get("symbol")
            visible = False
            if site is not None and isinstance(name, str):
                # The stubs declare a Symbol; the lookup gives None for an unknown name.
                found = cast("ast.Symbol | None", site.lookupName(name))
                visible = found is not None and found.hierarchicalPath == node.get("sva2yosys_path")
            node["sva2yosys_visibleAtSite"] = visible
        for value in node.values():
            visit(value)

    for key in (
        "propertySpec",
        "sva2yosys_clock",
        "sva2yosys_defaultDisable",
        "sva2yosys_procedureClock",
        "sva2yosys_enable",
    ):
        visit(record.json.get(key))


def _twin_key(record: _Record) -> tuple[tuple[int, int], tuple[str, ...]]:
    """The assertion's source position and its generate scopes relative to the instance:
    the iterations of a generate loop share a source position."""
    instance = record.chain[0]
    return record.key, tuple(scope[len(instance) :] for scope in record.chain[1:])


def _retarget(node: JsonValue, source: str, target: str) -> JsonValue:
    """Deep copy of ``node`` with symbol paths inside instance ``source`` moved to ``target``."""
    if isinstance(node, list):
        return [_retarget(item, source, target) for item in node]
    if not isinstance(node, dict):
        return node
    copied: JsonObject = {}
    for name, value in node.items():
        if (
            name == "sva2yosys_path"
            and isinstance(value, str)
            and (value == source or value.startswith(source + "."))
        ):
            copied[name] = target + value[len(source) :]
        else:
            copied[name] = _retarget(value, source, target)
    return copied


def _sampled_default(symbol: ast.Symbol) -> str:
    """The class of ``symbol``'s default sampled value (IEEE 1800 §16.5.1): ``"x"`` for a
    4-state net, or a 4-state variable without a declaration initializer; ``"known"`` for a
    2-state type (default 0) or a variable with an initializer."""
    if not isinstance(symbol, ast.ValueSymbol):
        return "known"
    if not symbol.type.isFourState:
        return "known"
    if isinstance(symbol, ast.NetSymbol):
        return "x"
    return "x" if cast("object", symbol.initializer) is None else "known"


def _integral(value_type: ast.Type) -> JsonObject | None:
    """The ``sva2yosys_integral`` description of an expression type (module docstring), or
    ``None`` when the type is not integral (unpacked arrays, ``real``, ``string``, ...).

    Everything is read from the canonical type, so a typedef is its target. ``dims`` counts
    the packed dimensions: each packed array contributes one, and an enum, packed struct or
    packed union leaf contributes one (a flat vector); the predefined integer types and
    ``logic``/``bit``/``reg`` without a range have none. ``aggregate`` is set when the
    innermost element is a packed struct or union.
    """
    canonical = value_type.canonicalType
    if not canonical.isIntegral:
        return None
    width = int(canonical.bitWidth)
    dims = 0
    leaf = canonical
    msb, lsb = width - 1, 0
    while isinstance(leaf, ast.PackedArrayType):
        if dims == 0:
            msb, lsb = int(leaf.range.left), int(leaf.range.right)
        dims += 1
        leaf = leaf.elementType.canonicalType
    aggregate = isinstance(leaf, (ast.PackedStructType, ast.PackedUnionType))
    if aggregate or isinstance(leaf, ast.EnumType):
        dims += 1
    json: JsonObject = {
        "width": width,
        "signed": bool(canonical.isSigned),
        "msb": msb,
        "lsb": lsb,
        "dims": dims,
    }
    if aggregate:
        json["aggregate"] = True
    return json


def _members(scope: ast.Symbol) -> Iterable[ast.Symbol]:
    """The members of a scope symbol.

    pyslang's stubs declare instance bodies, generate blocks and arrays as plain
    ``Symbol``; at runtime they are iterable scopes.
    """
    return cast("Iterable[ast.Symbol]", scope)


@dataclass(frozen=True)
class _Parameter:
    """A parameter, type parameter or genvar (a parameter of each generate iteration)."""

    literal: str | None  # integer literal, or None for a non-integral value or a type
    location: pyslang.SourceLocation  # where it is declared
    implicit_type: tuple[int, bool] | None = None  # (width, signed) when taken from the value


# Per name, the declarations of the enclosing scopes, outermost first.
type _Parameters = dict[str, tuple[_Parameter, ...]]


def _walk_scope(
    scope: ast.Symbol,
    chain: tuple[str, ...],
    context: _Context,
    parameters: _Parameters | None = None,
    enclosing: tuple[EnclosingScope, ...] = (),
) -> None:
    # ``enclosing``: the scope symbols from this one outwards to the instance body (the
    # dependency analysis cannot reach them from a Slang scope).
    # An inner declaration hides an outer one, but only after it: each assertion picks
    # the innermost declaration that precedes it (``_visible_parameters``).
    visible = dict(parameters or {})
    for member in _members(scope):
        if isinstance(member, ast.ParameterSymbol):
            entry = _Parameter(
                _integral_literal(member.value),
                member.location,
                (member.type.bitWidth, member.type.isSigned) if _type_from_value(member) else None,
            )
        elif isinstance(member, (ast.TypeParameterSymbol, ast.InterfacePortSymbol)):
            # An interface port brings the parameters of whatever interface instance is
            # connected, which no lexical parameter keys (``##(p.N)``): not comparable.
            entry = _Parameter(None, member.location)
        else:
            continue
        visible[member.name] = (*visible.get(member.name, ()), entry)
    for member in _members(scope):
        if isinstance(member, (ast.InstanceSymbol, ast.CheckerInstanceSymbol)):
            if isinstance(member, ast.InstanceSymbol):
                context.bodies[member.hierarchicalPath] = member.body
            _walk_scope(member.body, (member.hierarchicalPath,), context, enclosing=(member.body,))
        elif isinstance(member, (ast.InstanceArraySymbol, ast.GenerateBlockArraySymbol)):
            _walk_scope(member, chain, context, visible, (member, *enclosing))
        elif isinstance(member, ast.GenerateBlockSymbol):
            if not member.isUninstantiated:
                _walk_scope(
                    member,
                    (*chain, member.hierarchicalPath),
                    context,
                    visible,
                    (member, *enclosing),
                )
        elif isinstance(member, ast.ProceduralBlockSymbol):
            _procedural_block(member, chain, context, visible, enclosing)


def _type_from_value(parameter: ast.ParameterSymbol) -> bool:
    """Whether the declaration fixes no width: an implicit type without a packed range
    (``parameter P = 1``, ``parameter signed P = 1``), so the value decides it."""
    type_syntax = parameter.declaredType.typeSyntax
    return isinstance(type_syntax, syntax.ImplicitTypeSyntax) and len(type_syntax.dimensions) == 0


_INTEGRAL = re.compile(r"-?\d+|\d+'s?[bdhoBDHO][0-9a-fA-F_]+")


def _integral_literal(value: object) -> str | None:
    """``value`` as a SystemVerilog integer literal, or ``None`` (strings, reals, X/Z)."""
    text = str(value)
    return text if _INTEGRAL.fullmatch(text) else None


def _procedural_block(
    block: ast.ProceduralBlockSymbol,
    chain: tuple[str, ...],
    context: _Context,
    parameters: _Parameters,
    enclosing: tuple[EnclosingScope, ...],
) -> None:
    # A module-level assertion lives in an implicit procedural block whose syntax is the
    # assertion member itself; anything else was written by the user (always, initial ...).
    procedural = block.syntax.kind != syntax.SyntaxKind.ConcurrentAssertionMember
    clocks: dict[tuple[int, int], ast.TimingControl | None] = {}
    for analyzed in context.manager.getAnalyzedAssertions(block):
        node = analyzed.astNode
        if isinstance(node, ast.ConcurrentAssertionStatement):
            clocks[_key(node.sourceRange.start)] = analyzed.semanticLeadingClock
    found: list[ast.ConcurrentAssertionStatement] = []

    def visit(node: object) -> None:
        if isinstance(node, ast.ConcurrentAssertionStatement):
            found.append(node)

    block.visit(visit)
    procedure = _Procedure.of(block) if procedural else None
    for statement in found:
        key = _key(statement.sourceRange.start)
        record = context.exporter.record(
            statement, chain, procedural=procedural, clock=clocks.get(key), site=block.parentScope
        )
        if procedure is not None:
            record.json.update(procedure.export(statement, context, clocks.get(key)))
        visible = _visible_parameters(
            parameters, statement.sourceRange.start, context.source_manager
        )
        record.json["sva2yosys_parameters"] = visible
        names = frozenset(
            str(entry["name"]) for entry in visible if isinstance(entry, dict) and "name" in entry
        )
        analysis = assertion_dependencies(statement, block, names, enclosing, context.declarations)
        dependent = analysis.names
        record.json["sva2yosys_dependencies"] = None if dependent is None else [*sorted(dependent)]
        record.json["sva2yosys_hiddenParameters"] = [*sorted(analysis.hidden)]
        context.records.append(
            dataclasses.replace(record, key=key, analyzed=key in clocks, site=block.parentScope)
        )


# --- procedural assertions (docs/semantics.md §6) ---------------------------------------


@dataclass(frozen=True)
class _Condition:
    """One condition on the path from a procedural block to an assertion: ``expr`` itself
    (an ``if``) or ``expr`` equal to one of ``items`` (a ``case`` item), maybe negated."""

    expr: ast.Expression
    items: tuple[ast.Expression, ...] | None
    negated: bool


_BLOCK_NAMES = {
    ast.ProceduralBlockKind.Initial: "initial",
    ast.ProceduralBlockKind.Final: "final",
    ast.ProceduralBlockKind.AlwaysComb: "always_comb",
    ast.ProceduralBlockKind.AlwaysLatch: "always_latch",
}
_CASE_NAMES = {
    ast.CaseStatementCondition.WildcardJustZ: "casez",
    ast.CaseStatementCondition.WildcardXOrZ: "casex",
    ast.CaseStatementCondition.Inside: "case inside",
}
_LOOPS = (
    ast.ForLoopStatement,
    ast.RepeatLoopStatement,
    ast.WhileLoopStatement,
    ast.DoWhileLoopStatement,
    ast.ForeverLoopStatement,
    ast.ForeachLoopStatement,
)


_INITIAL_PATH_ISSUE = (
    "a condition around a concurrent assertion in an initial procedure is evaluated when the "
    "procedure runs at time 0, not at the assertion's first clock tick (IEEE 1800-2017 "
    "16.14.6.2); not supported: move the condition into the property (e.g. "
    "`assert property (@(posedge clk) en |-> p)`) after checking it means the same"
)


@dataclass(frozen=True)
class _Procedure:
    """What a user procedural block means for the assertions written in it.

    Attributes:
        clock: The block's event control when it is a single clock edge without ``iff``.
        issue: Why no assertion of the block can be lowered, or ``""``.
        paths: Per assertion (source position): its path conditions, or why its path
            cannot be lowered.
    """

    clock: ast.SignalEventControl | None
    issue: str
    paths: dict[tuple[int, int], tuple[_Condition, ...] | str]
    initial: bool = False

    @staticmethod
    def of(block: ast.ProceduralBlockSymbol) -> _Procedure:
        kind = block.procedureKind
        initial = kind == ast.ProceduralBlockKind.Initial
        clock: ast.SignalEventControl | None = None
        body = block.body
        # The statement after the block's own event control, if it has one.
        inner = body
        issue = ""
        if isinstance(body, ast.TimedStatement):
            timing = body.timing
            if (
                isinstance(timing, ast.SignalEventControl)
                and timing.edge in (ast.EdgeKind.PosEdge, ast.EdgeKind.NegEdge)
                and cast("object", timing.iffCondition) is None
            ):
                clock = timing
            inner = body.stmt
        if not initial and kind not in (
            ast.ProceduralBlockKind.Always,
            ast.ProceduralBlockKind.AlwaysFF,
        ):
            issue = (
                f"a concurrent assertion in an {_BLOCK_NAMES.get(kind, kind.name)} block is not "
                "supported (only in always or always_ff with a single clock edge, or initial)"
            )
        elif initial and isinstance(body, ast.TimedStatement) and clock is None:
            issue = (
                "a concurrent assertion in an initial block whose event control is not a "
                "single clock edge without iff (a delay, an event list ...) is not supported"
            )
        elif not initial and clock is None:
            issue = (
                "a procedural concurrent assertion needs its block's event control to be a "
                "single clock edge without iff, as in always @(posedge clk)"
            )
        if not issue and not _falls_through(inner):
            # A delay, wait, loop, disable or task call anywhere in the block can skip
            # or delay the assertion, or make the block miss clock edges.
            issue = (
                "a procedural concurrent assertion in a block whose statements may not fall "
                "through in zero time (a delay, wait, loop, disable or task call) is not "
                "supported"
            )
        paths: dict[tuple[int, int], tuple[_Condition, ...] | str] = {}
        if not issue:
            _walk_path(inner, (), paths)
        return _Procedure(clock, issue, paths, initial)

    def export(
        self,
        statement: ast.ConcurrentAssertionStatement,
        context: _Context,
        leading: ast.TimingControl | None,
    ) -> JsonObject:
        """The procedural keys of ``statement``; ``leading`` is the clock Slang resolves
        for it (the stability of the path conditions is judged against the block's own
        event control, else, for an ``initial`` block, against that clock)."""
        exporter = context.exporter
        path = self.paths.get(_key(statement.sourceRange.start), ())
        issue = self.issue or (path if isinstance(path, str) else "")
        conditions = () if isinstance(path, str) else path
        if not issue and self.initial and conditions:
            # The condition is evaluated when the procedure runs, at time 0, not at the
            # assertion's first clock tick (IEEE 1800-2017 16.14.6.2).
            issue = _INITIAL_PATH_ISSUE
        stable_against = self.clock
        if stable_against is None and isinstance(leading, ast.SignalEventControl):
            stable_against = leading
        if not issue and self.clock is not None:
            clock = None if stable_against is None else _clock_root(stable_against, context)
            for condition in conditions:
                read = [condition.expr, *(condition.items or ())]
                reason = _instability(read, clock, context)
                if reason:
                    issue = reason
                    break
        enable: list[JsonValue] = []
        for condition in conditions:
            entry: JsonObject = {
                "kind": "If" if condition.items is None else "Case",
                "expr": exporter.expr(condition.expr),
                "negated": condition.negated,
            }
            if condition.items is not None:
                entry["items"] = [exporter.expr(item) for item in condition.items]
            enable.append(entry)
        return {
            "sva2yosys_procedureKind": "initial" if self.initial else "always",
            "sva2yosys_procedureClock": exporter.timing(self.clock)
            if self.clock is not None
            else None,
            "sva2yosys_enable": enable,
            "sva2yosys_procedureIssue": issue or None,
        }


def _calls(expression: ast.Expression) -> bool:
    """Whether ``expression`` calls a task or system task (functions complete in zero
    time and cannot disable the caller's blocks)."""
    calls: list[object] = []

    def visit(node: object) -> None:
        if (
            isinstance(node, ast.CallExpression)
            and node.subroutineKind != ast.SubroutineKind.Function
        ):
            calls.append(node)

    expression.visit(visit)
    return bool(calls)


def _falls_through(statement: ast.Statement) -> bool:
    """Whether ``statement`` always completes in zero time and continues with the next
    statement: assignments without task calls or intra-assignment timing, null statements,
    concurrent assertions, and sequential blocks, ``if`` and ``case`` of those."""
    match statement:
        case ast.EmptyStatement() | ast.ConcurrentAssertionStatement():
            return True
        case ast.VariableDeclStatement():
            initializer = cast("ast.Expression | None", statement.symbol.initializer)
            return initializer is None or not _calls(initializer)
        case ast.ExpressionStatement():
            expression = statement.expr
            if not isinstance(expression, ast.AssignmentExpression):
                return False
            if cast("object", expression.timingControl) is not None:
                return False
            return not _calls(expression)
        case ast.BlockStatement():
            return statement.blockKind == ast.StatementBlockKind.Sequential and _falls_through(
                statement.body
            )
        case ast.StatementList():
            return all(_falls_through(inner) for inner in statement.list)
        case ast.ConditionalStatement():
            other = cast("ast.Statement | None", statement.ifFalse)
            return _falls_through(statement.ifTrue) and (other is None or _falls_through(other))
        case ast.CaseStatement():
            default = cast("ast.Statement | None", statement.defaultCase)
            return all(_falls_through(item.stmt) for item in statement.items) and (
                default is None or _falls_through(default)
            )
        case _:
            return False


def _walk_path(
    statement: ast.Statement,
    conditions: tuple[_Condition, ...],
    paths: dict[tuple[int, int], tuple[_Condition, ...] | str],
) -> None:
    """Record, for each assertion under ``statement``, its path conditions or an issue."""

    def refuse(what: str) -> None:
        def visit(node: object) -> None:
            if isinstance(node, ast.ConcurrentAssertionStatement):
                paths[_key(node.sourceRange.start)] = (
                    f"a procedural concurrent assertion inside {what} is not supported"
                )

        statement.visit(visit)

    if isinstance(statement, ast.ConcurrentAssertionStatement):
        paths[_key(statement.sourceRange.start)] = conditions
    elif isinstance(statement, ast.BlockStatement):
        if statement.blockKind == ast.StatementBlockKind.Sequential:
            _walk_path(statement.body, conditions, paths)
        else:
            refuse("a fork")
    elif isinstance(statement, ast.StatementList):
        for inner in statement.list:
            _walk_path(inner, conditions, paths)
    elif isinstance(statement, ast.ConditionalStatement):
        branches = statement.conditions
        if len(branches) != 1 or cast("object", branches[0].pattern) is not None:
            refuse("an if with matches or &&&")
            return
        expr = branches[0].expr
        _walk_path(statement.ifTrue, (*conditions, _Condition(expr, None, False)), paths)
        if statement.ifFalse is not None:
            _walk_path(statement.ifFalse, (*conditions, _Condition(expr, None, True)), paths)
    elif isinstance(statement, ast.CaseStatement):
        if statement.condition != ast.CaseStatementCondition.Normal:
            refuse(f"a {_CASE_NAMES.get(statement.condition, statement.condition.name)} statement")
            return
        earlier: tuple[ast.Expression, ...] = ()
        for item in statement.items:
            own = tuple(item.expressions)
            path = (*conditions, _Condition(statement.expr, own, False))
            if earlier:
                path = (*path, _Condition(statement.expr, earlier, True))
            _walk_path(item.stmt, path, paths)
            earlier = (*earlier, *own)
        default = cast("ast.Statement | None", statement.defaultCase)
        if default is not None:
            rest = (_Condition(statement.expr, earlier, True),) if earlier else ()
            _walk_path(default, (*conditions, *rest), paths)
    elif isinstance(statement, _LOOPS):
        refuse("a loop")
    elif isinstance(statement, ast.TimedStatement):
        refuse("a timing control")
    else:
        refuse(f"a {statement.kind.name} statement")


def _instability(
    expressions: list[ast.Expression], clock: tuple[str, str] | None, context: _Context
) -> str:
    """Why a signal read by ``expressions`` may change at the clock edge before a
    procedural block reads it, or ``""`` (docs/semantics.md §6). ``clock`` is the path of
    the signal the block's event ultimately reads (``None`` if unknown)."""
    for symbol in _reads(expressions):
        reason = _unstable(symbol, clock, context)
        if reason:
            return (
                f"the enabling condition of this procedural assertion reads {symbol.name}, "
                f"which is not stable at the clock edge ({reason}): its value when the block "
                "runs may differ from its sampled value"
            )
    return ""


def _reads(nodes: Sequence[ast.Expression | ast.Symbol]) -> list[ast.ValueSymbol]:
    found: list[ast.ValueSymbol] = []

    def visit(node: object) -> None:
        if isinstance(node, (ast.NamedValueExpression, ast.HierarchicalValueExpression)):
            found.append(node.symbol)

    for node in nodes:
        node.visit(visit)
    return found


def _has(flags: object, flag: analysis.DriverFlags) -> bool:
    return bool(int(cast("int", flags)) & int(flag))


def _clock_root(clock: ast.SignalEventControl, context: _Context) -> tuple[str, str] | None:
    """The event as (path of the signal it reads, followed up through plain port
    connections; edge); ``None`` when it is not a plain signal without ``iff``."""
    expression = clock.expr
    if not isinstance(expression, ast.NamedValueExpression):
        return None
    if cast("object", clock.iffCondition) is not None:
        return None
    root = _root(expression.symbol, context)
    if root.symbol is None:
        return None
    return root.symbol.hierarchicalPath, clock.edge.name


@dataclass(frozen=True)
class _Root:
    """Where a signal's value comes from: ``symbol``, reached through plain input-port
    connections; ``top_input`` when it is an input port of a top-level instance."""

    symbol: ast.ValueSymbol | None
    top_input: bool = False
    reason: str = ""  # why the chain cannot be followed
    # An input port connected to an expression (``symbol`` is then ``None``, ``reason``
    # set): a continuous assignment of it (IEEE 1800-2017 §23.3.3). Only the disable iff
    # check follows it; the other callers keep the rejection.
    expression: ast.Expression | None = None
    port: str = ""  # the path of that port


def _twin(symbol: ast.ValueSymbol, context: _Context) -> tuple[ast.ValueSymbol, str, str] | None:
    """For a signal of an instance whose drivers Slang did not analyse (one analysis per
    identical body), the same signal in the analysed instance, with the path of that
    instance and of the signal's own enclosing instance; ``None`` when there is none."""
    path = symbol.hierarchicalPath
    prefix = path
    while "." in prefix:
        prefix = prefix.rpartition(".")[0]
        body = context.bodies.get(prefix)
        if body is None:
            continue
        instance = cast("ast.InstanceSymbol | None", body.parentInstance)
        canonical = None if instance is None else instance.canonicalBody
        if canonical is None:
            continue
        canonical_instance = cast("ast.InstanceSymbol | None", canonical.parentInstance)
        if canonical_instance is None:
            return None
        canonical_prefix = canonical_instance.hierarchicalPath
        found = cast(
            "ast.Symbol | None",
            context.root.lookupName(canonical_prefix + path[len(prefix) :]),
        )
        if isinstance(found, ast.ValueSymbol):
            return found, canonical_prefix, prefix
        return None
    return None


def _drivers(symbol: ast.ValueSymbol, context: _Context) -> list[analysis.ValueDriver]:
    """The drivers of ``symbol``; for an instance Slang did not analyse, those of the
    same signal in its twin (the flags and kinds are those of the body)."""
    drivers = list(context.manager.getDrivers(symbol))
    if drivers:
        return drivers
    twin = _twin(symbol, context)
    return [] if twin is None else list(context.manager.getDrivers(twin[0]))


def _input_port(
    symbol: ast.ValueSymbol, context: _Context
) -> tuple[ast.InstanceBodySymbol, ast.PortSymbol] | None:
    """The instance body and port of which ``symbol`` is the internal signal, for an input
    port (found structurally: Slang does not analyse every one of identical instances)."""
    body = context.bodies.get(symbol.hierarchicalPath.rpartition(".")[0])
    if body is None:
        return None
    for port in body.portList:
        internal = cast("ast.Symbol | None", getattr(port, "internalSymbol", None))
        if (
            isinstance(port, ast.PortSymbol)
            and internal is not None
            and internal.hierarchicalPath == symbol.hierarchicalPath
            and port.direction == ast.ArgumentDirection.In
        ):
            return body, port
    return None


def _root(symbol: ast.ValueSymbol, context: _Context) -> _Root:
    """Follow an input port connected to a plain name (no logic, no select) up to the
    signal it reads."""
    for _ in range(256):
        found = _input_port(symbol, context)
        if found is None:
            return _Root(symbol)
        body, port = found
        path = symbol.hierarchicalPath
        drivers = _drivers(symbol, context)
        if any(not _has(d.flags, analysis.DriverFlags.InputPort) for d in drivers):
            return _Root(None, reason=f"{path} is an input port with other drivers")
        instance = cast("ast.InstanceSymbol | None", body.parentInstance)
        if instance is None or "." not in instance.hierarchicalPath:
            return _Root(symbol, top_input=True)
        connection = cast("ast.PortConnection | None", instance.getPortConnection(port))
        connected = None if connection is None else connection.expression
        if connected is None:
            return _Root(None, reason=f"{path} is an unconnected input port")
        if not isinstance(connected, ast.NamedValueExpression):
            return _Root(
                None,
                reason=f"{path} is connected to an expression, not a plain signal",
                expression=connected,
                port=path,
            )
        symbol = connected.symbol
    return _Root(None, reason=f"{symbol.hierarchicalPath}: port chain too long")


def _unstable(symbol: ast.ValueSymbol, clock: tuple[str, str] | None, context: _Context) -> str:
    """``""`` when ``symbol`` keeps its sampled value until the procedural code of the
    clock edge runs: a constant, a top-level input other than the clock, or a variable
    written only by nonblocking assignments in blocks triggered by exactly the assertion's
    clock edge (or an input port
    connected by plain names to one of these). Anything else, including combinational
    logic and continuous assignments, is a reason (memoised)."""
    if not isinstance(symbol, (ast.VariableSymbol, ast.NetSymbol)):
        return ""  # a parameter, genvar, enum value ...
    root = _root(symbol, context)
    if root.symbol is None:
        return root.reason
    path = root.symbol.hierarchicalPath
    key = f"{path}@{clock}"
    if key not in context.stability:
        context.stability[key] = _root_reason(root, clock, context)
    return context.stability[key]


def _root_reason(root: _Root, clock: tuple[str, str] | None, context: _Context) -> str:
    assert root.symbol is not None
    symbol = root.symbol
    path = symbol.hierarchicalPath
    if root.top_input:
        if clock is None:
            return f"{path} is an input and the block's clock is not a plain signal"
        if path == clock[0]:
            return f"{path} is the clock of the block"
        return ""  # free in formal verification
    if isinstance(symbol, ast.NetSymbol) and cast("object", symbol.initializer) is not None:
        return f"{path} is driven by a continuous assignment"
    drivers = list(context.manager.getDrivers(symbol))
    if not drivers:
        return f"{path} has no analysed driver"
    for driver in drivers:
        if _has(driver.flags, analysis.DriverFlags.Initializer):
            continue
        holder = driver.containingSymbol
        if driver.kind == analysis.DriverKind.Continuous:
            return f"{path} is driven by a continuous assignment or a port"
        if not isinstance(holder, ast.ProceduralBlockSymbol):
            return f"{path} is written by a subroutine"
        if not _edge_triggered(holder):
            kind = _BLOCK_NAMES.get(holder.procedureKind, "always")
            return f"{path} is written in a combinational or {kind} block"
        if not _only_nonblocking(holder, path):
            return f"{path} is written by a blocking assignment in a clocked block"
        # Its nonblocking update must come from the same event as the assertion's block:
        # an update scheduled by another event of the same time slot (e.g. one that also
        # produces the clock) may precede the block.
        body = holder.body
        timing = body.timing if isinstance(body, ast.TimedStatement) else None
        if (
            clock is None
            or not isinstance(timing, ast.SignalEventControl)
            or _clock_root(timing, context) != clock
        ):
            return (
                f"{path} is written in a block triggered by another event than the "
                "assertion's clock"
            )
    return ""


def _edge_triggered(block: ast.ProceduralBlockSymbol) -> bool:
    """``always`` or ``always_ff`` whose event control is a clock edge, or a list of
    edges (an asynchronous reset)."""
    if block.procedureKind not in (
        ast.ProceduralBlockKind.Always,
        ast.ProceduralBlockKind.AlwaysFF,
    ):
        return False
    body = block.body
    if not isinstance(body, ast.TimedStatement):
        return False
    timing = body.timing
    events: list[object]
    if isinstance(timing, ast.SignalEventControl):
        events = [timing]
    elif isinstance(timing, ast.EventListControl):
        events = list(timing.events)
    else:
        return False
    return all(
        isinstance(event, ast.SignalEventControl) and event.edge.name in ("PosEdge", "NegEdge")
        for event in events
    )


_INCREMENTS = (
    ast.UnaryOperator.Preincrement,
    ast.UnaryOperator.Predecrement,
    ast.UnaryOperator.Postincrement,
    ast.UnaryOperator.Postdecrement,
)


def _only_nonblocking(block: ast.ProceduralBlockSymbol, path: str) -> bool:
    """Whether ``block`` writes the signal ``path`` only with nonblocking assignments
    (and writes it at all). Every signal named anywhere in a left-hand side counts as
    written (``{q, r} = …``, ``q[i] = …``; conservatively also an index)."""
    writes: list[bool] = []

    def targets(expression: ast.Expression) -> set[str]:
        return {symbol.hierarchicalPath for symbol in _reads([expression])}

    def visit(node: object) -> None:
        if isinstance(node, ast.AssignmentExpression) and path in targets(node.left):
            writes.append(bool(node.isNonBlocking))
        elif (
            isinstance(node, ast.UnaryExpression)
            and node.op in _INCREMENTS
            and path in targets(node.operand)
        ):
            writes.append(False)

    block.visit(visit)
    return bool(writes) and all(writes)


# --- disable iff timing (docs/semantics.md §7) --------------------------------------------


def _disable_issue(record: JsonObject, root: ast.RootSymbol, context: _Context) -> str | None:
    """Why the disable iff condition in effect (an explicit one, else the scope's default)
    may change at the clock edge, or ``None``. IEEE evaluates it with the values of the
    Observed region, after the edge's nonblocking updates (§16.12); the monitors use the
    values of the tick, which are the same only for signals that do not change at the
    edge."""
    conditions: list[JsonValue] = []

    def find(node: JsonValue) -> None:
        if isinstance(node, list):
            for item in node:
                find(item)
        elif isinstance(node, dict):
            if node.get("kind") == "DisableIff":
                conditions.append(node.get("condition"))
            for value in node.values():
                find(value)

    find(record.get("propertySpec"))
    if not conditions and record.get("sva2yosys_defaultDisable") is not None:
        conditions.append(record["sva2yosys_defaultDisable"])
    paths: list[str] = []

    def collect(node: JsonValue) -> None:
        if isinstance(node, list):
            for item in node:
                collect(item)
        elif isinstance(node, dict):
            path = node.get("sva2yosys_path")
            if node.get("kind") in ("NamedValue", "HierarchicalValue") and isinstance(path, str):
                paths.append(path)
            for value in node.values():
                collect(value)

    collect(conditions)
    for path in paths:
        symbol = cast("ast.Symbol | None", root.lookupName(path))
        if not isinstance(symbol, ast.ValueSymbol):
            if symbol is None:
                return f"the disable iff condition reads {path}, which cannot be analysed"
            continue  # a parameter, enum value ...
        reason = _disable_reason(symbol, context, set())
        if reason:
            return (
                f"the disable iff condition reads {path}, which may change at the clock edge "
                f"({reason}); IEEE evaluates disable iff after the edge's updates (§16.12), "
                "which generated monitors do not model yet: use a top-level input or a "
                "continuous function of top-level inputs"
            )
    return None


def _disable_reason(symbol: ast.ValueSymbol, context: _Context, active: set[str]) -> str:
    """``""`` when ``symbol`` cannot change at a clock edge: a constant, a top-level input
    (also through port connections), or a net whose only driver is one zero-delay
    continuous assignment, without calls, of such signals (acyclic). An input port
    connected to an expression is a continuous assignment of it (§23.3.3). Slang gives no
    drivers for all but one of identical instances: the drivers of the analysed twin are
    used, with the signals they read mapped to the instance's own."""
    if not isinstance(symbol, (ast.VariableSymbol, ast.NetSymbol)):
        return ""
    root = _root(symbol, context)
    if root.symbol is None:
        if root.expression is None:
            return root.reason
        key = f"disable:{root.port}"
        if key in context.stability:
            return context.stability[key]
        if root.port in active:
            return f"{root.port} is part of a combinational cycle"
        reason = ""
        if _has_call(root.expression):
            reason = f"{root.port} is connected to an expression with a function call"
        else:
            for read in _reads([root.expression]):
                reason = _disable_reason(read, context, active | {root.port})
                if reason:
                    break
        context.stability[key] = reason
        return reason
    if root.top_input:
        return ""
    source = root.symbol
    path = source.hierarchicalPath
    key = f"disable:{path}"
    if key in context.stability:
        return context.stability[key]
    if path in active:
        return f"{path} is part of a combinational cycle"
    drivers = list(context.manager.getDrivers(source))
    twin = None
    if not drivers:
        twin = _twin(source, context)
        if twin is not None:
            drivers = list(context.manager.getDrivers(twin[0]))
    expressions: list[ast.Expression] = []
    initializer = (
        cast("ast.Expression | None", source.initializer)
        if isinstance(source, ast.NetSymbol)
        else None
    )
    if initializer is not None and not drivers:
        expressions = [initializer]
    elif initializer is None and len(drivers) == 1:
        holder = drivers[0].containingSymbol
        if (
            drivers[0].kind == analysis.DriverKind.Continuous
            and isinstance(holder, ast.ContinuousAssignSymbol)
            and cast("object", holder.delay) is None
            and isinstance(holder.assignment, ast.AssignmentExpression)
        ):
            expressions = [holder.assignment.right]
    if isinstance(source, ast.NetSymbol) and cast("object", source.delay) is not None:
        reason = f"{path} is a net with a delay"
    elif not expressions:
        reason = (
            f"{path} is not a top-level input or a single continuous assignment of "
            "top-level inputs (e.g. a register)"
        )
    elif any(_has_call(expression) for expression in expressions):
        reason = f"{path} is computed with a function call"
    else:
        reason = ""
        for read in _reads(expressions):
            if twin is not None and drivers:
                mapped = _own_symbol(read, twin[1], twin[2], context)
                if mapped is None:
                    reason = f"{path} reads {read.hierarchicalPath}, outside its instance"
                    break
                read = mapped
            reason = _disable_reason(read, context, active | {path})
            if reason:
                break
    context.stability[key] = reason
    return reason


def _own_symbol(
    read: ast.ValueSymbol, canonical: str, own: str, context: _Context
) -> ast.ValueSymbol | None:
    """The signal of the instance ``own`` that corresponds to ``read``, a signal of its
    twin ``canonical``; ``None`` when ``read`` is not inside that instance."""
    path = read.hierarchicalPath
    if not path.startswith(canonical + "."):
        return None
    found = cast(
        "ast.Symbol | None",
        context.root.lookupName(own + path[len(canonical) :]),
    )
    return found if isinstance(found, ast.ValueSymbol) else None


def _has_call(expression: ast.Expression) -> bool:
    calls: list[object] = []

    def visit(node: object) -> None:
        if isinstance(node, ast.CallExpression):
            calls.append(node)

    expression.visit(visit)
    return bool(calls)


def _visible_parameters(
    parameters: _Parameters, site: pyslang.SourceLocation, sm: pyslang.SourceManager
) -> list[JsonValue]:
    """Per name, the innermost declaration that precedes ``site`` in its compilation
    unit (includes expanded), as ``{"name", "value"[, "width", "signed"]}``; names
    declared only later are not visible there. When the order of a declaration is
    unknown (another compilation unit), the name's value is null, so that the rewrite
    refuses to select by it rather than guess."""
    visible: list[JsonValue] = []
    for name, declarations in sorted(parameters.items()):
        order = [sm.isBeforeInCompilationUnit(d.location, site) for d in declarations]
        if None in order:
            visible.append({"name": name, "value": None})
            continue
        before = [d for d, is_before in zip(declarations, order, strict=True) if is_before]
        if before:
            chosen = before[-1]
            entry: JsonObject = {"name": name, "value": chosen.literal}
            if chosen.implicit_type is not None:
                entry["width"], entry["signed"] = chosen.implicit_type
            visible.append(entry)
    return visible


def _key(location: pyslang.SourceLocation) -> tuple[int, int]:
    return (location.buffer.id, location.offset)


# --- node export ---------------------------------------------------------------------


class _Exporter:
    """Converts pyslang nodes into the native-subset JSON encoding."""

    def __init__(self, source_manager: pyslang.SourceManager) -> None:
        self._sm = source_manager
        self._context: ast.ASTContext | None = None

    def record(
        self,
        statement: ast.ConcurrentAssertionStatement,
        chain: tuple[str, ...],
        *,
        procedural: bool,
        clock: ast.TimingControl | None,
        site: ast.Scope | None = None,
    ) -> _Record:
        # Constant folding evaluates in the scope that contains the assertion.
        self._context = ast.ASTContext(site, ast.LookupLocation.max) if site is not None else None
        source_range = self._range(statement.sourceRange)
        json: JsonObject = {
            "kind": "ConcurrentAssertion",
            "assertionKind": statement.assertionKind.name,
            "name": _label(statement),
            "scope": chain[-1],
            **source_range,
            "propertySpec": self.assertion(statement.propertySpec),
            "sva2yosys_procedural": procedural,
            "sva2yosys_clock": self.timing(clock) if clock is not None else None,
            "sva2yosys_defaultDisable": None,
        }
        position = (
            str(source_range["source_file_start"]),
            int(str(source_range["source_line_start"])),
            int(str(source_range["source_column_start"])),
        )
        return _Record(json, chain, position)

    def assertion(self, node: ast.AssertionExpr) -> JsonObject:
        json: JsonObject = {"kind": node.kind.name}
        if node.syntax is not None:
            json.update(self._range(node.syntax.sourceRange))
        if isinstance(node, ast.SimpleAssertionExpr):
            json["expr"] = self.expr(node.expr)
            repetition = node.repetition
            if repetition is not None:
                json["repetition"] = {"kind": repetition.kind.name, **_bounds(repetition.range)}
        elif isinstance(node, ast.SequenceConcatExpr):
            json["elements"] = [
                {"sequence": self.assertion(element.sequence), **_bounds(element.delay)}
                for element in node.elements
            ]
        elif isinstance(node, ast.BinaryAssertionExpr):
            json["op"] = node.op.name
            json["left"] = self.assertion(node.left)
            json["right"] = self.assertion(node.right)
        elif isinstance(node, ast.SequenceWithMatchExpr):
            json["expr"] = self.assertion(node.expr)
            repetition = node.repetition
            if repetition is not None:
                json["repetition"] = {"kind": repetition.kind.name, **_bounds(repetition.range)}
            json["matchItems"] = [self.expr(item) for item in node.matchItems]
        elif isinstance(node, ast.UnaryAssertionExpr):
            json["op"] = node.op.name
            json["expr"] = self.assertion(node.expr)
            if node.range is not None:
                json.update(_bounds(node.range))
        elif isinstance(node, ast.ConditionalAssertionExpr):
            json["condition"] = self.expr(node.condition)
            json["ifExpr"] = self.assertion(node.ifExpr)
            otherwise = cast("ast.AssertionExpr | None", node.elseExpr)
            if otherwise is not None:
                json["elseExpr"] = self.assertion(otherwise)
        elif isinstance(node, ast.StrongWeakAssertionExpr):
            json["strength"] = node.strength.name
            json["expr"] = self.assertion(node.expr)
        elif isinstance(node, ast.ClockingAssertionExpr):
            json["clocking"] = self.timing(node.clocking)
            json["expr"] = self.assertion(node.expr)
        elif isinstance(node, ast.DisableIffAssertionExpr):
            json["condition"] = self.expr(node.condition)
            json["expr"] = self.assertion(node.expr)
        else:
            json[UNSUPPORTED_KEY] = True
        return json

    def timing(self, node: ast.TimingControl) -> JsonObject:
        json: JsonObject = {"kind": node.kind.name, **self._range(node.sourceRange)}
        if isinstance(node, ast.SignalEventControl):
            json["expr"] = self.expr(node.expr)
            json["edge"] = node.edge.name
            if node.iffCondition is not None:
                json["iff"] = self.expr(node.iffCondition)
        else:
            json[UNSUPPORTED_KEY] = True
        return json

    def expr(self, node: ast.Expression) -> JsonObject:
        json: JsonObject = {
            "kind": node.kind.name,
            **self._range(node.sourceRange),
            "type": str(node.type),
        }
        integral = _integral(node.type)
        json["sva2yosys_integral"] = False if integral is None else integral
        if isinstance(node, (ast.NamedValueExpression, ast.HierarchicalValueExpression)):
            json["symbol"] = node.symbol.name
            json["sva2yosys_path"] = node.symbol.hierarchicalPath
            if isinstance(node, ast.NamedValueExpression):
                json["sva2yosys_sampledDefault"] = _sampled_default(node.symbol)
        elif isinstance(node, (ast.IntegerLiteral, ast.UnbasedUnsizedIntegerLiteral)):
            json["value"] = str(node.value)
        elif isinstance(node, ast.UnaryExpression):
            json["op"] = node.op.name
            json["operand"] = self.expr(node.operand)
        elif isinstance(node, ast.BinaryExpression):
            json["op"] = node.op.name
            json["left"] = self.expr(node.left)
            json["right"] = self.expr(node.right)
        elif isinstance(node, ast.ConditionalExpression):
            conditions: list[JsonValue] = []
            for condition in node.conditions:
                if condition.pattern is not None:
                    json[UNSUPPORTED_KEY] = True
                conditions.append({"expr": self.expr(condition.expr)})
            json["conditions"] = conditions
            json["left"] = self.expr(node.left)
            json["right"] = self.expr(node.right)
        elif isinstance(node, ast.ConversionExpression):
            json["operand"] = self.expr(node.operand)
            json["sva2yosys_fourState"] = bool(node.type.isFourState)
            json["sva2yosys_conversionKind"] = node.conversionKind.name
        elif isinstance(node, ast.ElementSelectExpression):
            json["value"] = self.expr(node.value)
            json["selector"] = self.expr(node.selector)
        elif isinstance(node, ast.RangeSelectExpression):
            json["selectionKind"] = node.selectionKind.name
            json["value"] = self.expr(node.value)
            json["left"] = self.expr(node.left)
            json["right"] = self.expr(node.right)
        elif isinstance(node, ast.ConcatenationExpression):
            json["operands"] = [self.expr(operand) for operand in node.operands]
        elif isinstance(node, ast.ReplicationExpression):
            json["count"] = self.expr(node.count)
            json["concat"] = self.expr(node.concat)
        elif isinstance(node, ast.InsideExpression):
            json["left"] = self.expr(node.left)
            json["ranges"] = [self.expr(item) for item in node.rangeList]
        elif isinstance(node, ast.ValueRangeExpression):
            json["left"] = self.expr(node.left)
            json["right"] = self.expr(node.right)
        elif isinstance(node, ast.CallExpression) and node.isSystemCall:
            json["subroutine"] = node.subroutineName
            json["arguments"] = [self.expr(argument) for argument in node.arguments]
        elif isinstance(node, ast.AssertionInstanceExpression):
            json["symbol"] = node.symbol.name
            json["body"] = self.assertion(node.body)
            json["isRecursiveProperty"] = node.isRecursiveProperty
        else:
            json[UNSUPPORTED_KEY] = True
        # The stubs declare a ConstantValue; it is None when Slang did not fold the node.
        constant = cast("pyslang.ConstantValue | None", node.constant)
        if constant is None and self._context is not None:
            constant = _folded(self._context.tryEval(node))
        if constant is not None:
            json["constant"] = str(constant)
        return json

    def _range(self, source_range: pyslang.SourceRange) -> JsonObject:
        sm = self._sm
        start = sm.getFullyExpandedLoc(source_range.start)
        end = sm.getFullyExpandedLoc(source_range.end)
        return {
            "source_file_start": sm.getFileName(start),
            "source_file_end": sm.getFileName(end),
            "source_line_start": sm.getLineNumber(start),
            "source_line_end": sm.getLineNumber(end),
            "source_column_start": sm.getColumnNumber(start),
            "source_column_end": sm.getColumnNumber(end),
        }


def _folded(value: pyslang.ConstantValue) -> pyslang.ConstantValue | None:
    """A constant evaluation result usable as an IR constant: a non-empty integer
    without X or Z bits; otherwise ``None`` (the expression stays unfolded)."""
    # An unset result is falsy (``empty()`` does not mean that).
    if not value or not isinstance(value.value, pyslang.SVInt) or value.hasUnknown():
        return None
    return value


def _bounds(sequence_range: ast.SequenceRange) -> JsonObject:
    maximum = sequence_range.max
    return {"min": sequence_range.min, "max": "$" if maximum is None else maximum}


def _label(statement: ast.ConcurrentAssertionStatement) -> str:
    statement_syntax = statement.syntax
    label = getattr(statement_syntax, "label", None)
    if label is None:
        return ""
    return str(label.name.valueText)


# --- default disable iff ---------------------------------------------------------------


@dataclass(frozen=True)
class _Splice:
    """Where a default disable expression sits in the original and in the probe text."""

    file: str
    original_line: int
    original_column: int
    probe_line: int
    probe_column: int


def _default_disables(
    sources: Sequence[tuple[str, str]], options: SlangOptions, compiled: Compiled
) -> dict[str, JsonObject]:
    """Map scope path to its default ``disable iff`` expression, bound by Slang."""
    trees = compiled.compilation.getSyntaxTrees()
    declarations: list[list[syntax.DefaultDisableDeclarationSyntax]] = []
    for tree in trees:
        found: list[syntax.DefaultDisableDeclarationSyntax] = []

        def visit(
            node: syntax.SyntaxNode,
            found: list[syntax.DefaultDisableDeclarationSyntax] = found,
        ) -> None:
            if isinstance(node, syntax.DefaultDisableDeclarationSyntax):
                found.append(node)

        tree.root.visit(visit)
        declarations.append(found)
    if not any(declarations):
        return {}

    sm = compiled.source_manager
    splices: dict[str, _Splice] = {}
    probe_sources: list[tuple[str, str]] = []
    counter = 0
    for (name, text), found in zip(sources, declarations, strict=True):
        pieces: list[str] = []
        cursor = 0
        probe_length = 0
        data = text.encode("utf-8")

        def index(location: pyslang.SourceLocation, data: bytes = data) -> int:
            """Slang's byte offset as an index into ``text``."""
            return len(data[: location.offset].decode("utf-8", errors="ignore"))

        for declaration in sorted(found, key=lambda d: d.sourceRange.start.offset):
            start = declaration.sourceRange.start
            if sm.isMacroLoc(start) or sm.getFileName(start) != name:
                # The probe can only splice text of the input file itself; without the
                # probe the default would be lost, which changes the assertions' meaning.
                where = sm.getFullyExpandedLoc(start)
                message = (
                    f"{sm.getFileName(where)}:{sm.getLineNumber(where)}: a default disable "
                    "iff in an included file or a macro expansion is not supported"
                )
                diagnostic = SlangDiagnostic(
                    Severity.ERROR, "UnsupportedDefaultDisable", message, None
                )
                raise SlangError((diagnostic,), f"error: {message}")
            expression = declaration.expr
            begin = index(declaration.sourceRange.start)
            end = index(declaration.sourceRange.end)
            expr_begin = index(expression.sourceRange.start)
            expr_end = index(expression.sourceRange.end)
            label = f"{_PROBE_LABEL}{counter}"
            counter += 1
            prefix = f"{label}: assert property (disable iff ("
            pieces.append(text[cursor:begin])
            probe_length += begin - cursor
            probe_expr_offset = probe_length + len(prefix)
            replacement = f"{prefix}{text[expr_begin:expr_end]}) 1'b1);"
            pieces.append(replacement)
            probe_length += len(replacement)
            cursor = end
            probe_text_so_far = "".join(pieces)
            original_line, original_column = _line_column(text, expr_begin)
            probe_line, probe_column = _line_column(probe_text_so_far, probe_expr_offset)
            splices[label] = _Splice(name, original_line, original_column, probe_line, probe_column)
        pieces.append(text[cursor:])
        probe_sources.append((name, "".join(pieces)))

    probe = compile_texts(probe_sources, options)
    defaults: dict[str, JsonObject] = {}
    for record in _collect(probe)[0]:
        label_value = record.json["name"]
        if not isinstance(label_value, str) or label_value not in splices:
            continue
        spec = record.json["propertySpec"]
        assert isinstance(spec, dict)
        condition = spec["condition"]
        assert isinstance(condition, dict)
        _remap(condition, splices[label_value])
        defaults[record.chain[-1]] = condition
    return defaults


def _line_column(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    column = offset - (text.rfind("\n", 0, offset) + 1) + 1
    return line, column


def _remap(node: JsonValue, splice: _Splice) -> None:
    """Move probe-text positions inside the spliced expression back to the original."""
    if isinstance(node, list):
        for item in node:
            _remap(item, splice)
        return
    if not isinstance(node, dict):
        return
    for side in ("start", "end"):
        file_key, line_key, column_key = (
            f"source_file_{side}",
            f"source_line_{side}",
            f"source_column_{side}",
        )
        line = node.get(line_key)
        column = node.get(column_key)
        if node.get(file_key) == splice.file and isinstance(line, int) and isinstance(column, int):
            offset = line - splice.probe_line
            node[line_key] = splice.original_line + offset
            if offset == 0:
                node[column_key] = column - splice.probe_column + splice.original_column
    for value in node.values():
        _remap(value, splice)
