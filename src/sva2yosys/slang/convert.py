"""Convert the Slang export (see ``export.py``) into the canonical SVA IR.

Only the supported subset is converted. Every assertion that uses anything else is
reported as a :class:`Rejection` naming the construct and its source position; it is
never dropped silently and never approximated. Callers must treat any rejection as an
error (unsupported concurrent assertions may not reach the output).

The supported subset:

- directives ``assert``, ``assume``, ``cover property``; ``restrict property`` normalizes
  to ``DirectiveKind.ASSUME`` for formal lowering (IEEE 1800-2017 16.14.4), with
  the same supported-property restrictions and preserved label/source position;
  also in ``always @(posedge clk)``
  under ``if``/``case`` conditions (lowered with their enabling condition,
  docs/semantics.md §6);
- one leading clock ``@(posedge <1-bit signal>)`` (explicit or ``default clocking``);
  any other clocking inside the property is rejected as multiclock;
- an optional ``disable iff`` at the top of the property, else the scope's default;
- ``|->`` and ``|=>`` with a sequence antecedent and a sequence consequent;
- sequences: Boolean expressions, ``##N``, ``##[N:M]``, ``[*N]``, ``[*N:M]``
  (bounded only), named sequences and properties (Slang substitutes their arguments);
- expressions over packed two-state-representable signals: unary and binary operators
  except ``**`` and wildcard equality, ``?:``, conversions, constant bit and part
  selects, concatenation, replication, ``$countones``/``$onehot``/``$onehot0``
  (lowered to sums of bits), and ``$past(e)``, ``$past(e, n)`` (constant
  ``n >= 1``), ``$rose``, ``$fell``,
  ``$stable``, ``$changed`` (one argument, not nested); constants without x/z bits;
- ``$isunknown``: the constant 0 in the two-state model; a directive that
  checks nothing afterwards is :class:`Skipped`, one with checks left carries a
  :class:`ConversionWarning` (docs/semantics.md §8a).
- ``e inside {...}``: an OR over the items (``slang/inside.py``,
  docs/semantics.md §1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import (
    Directive,
    DirectiveKind,
    ParameterValue,
    uncomparable_message,
    uncomparable_parameters,
)
from sva2yosys.ir.expr import (
    Binary,
    BinaryOp,
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
    SampledDefault,
    SampledFunction,
    Signal,
    Unary,
    UnaryOp,
    ValueType,
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
    Strength,
    Until,
    conjuncts,
)
from sva2yosys.ir.sequence import (
    TRUE,
    Bool,
    Delay,
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
from sva2yosys.passes.validate import expression_problems
from sva2yosys.passes.x_checks import (
    fold_expr,
    fold_property,
    never_fails,
    never_succeeds,
    truth,
)
from sva2yosys.slang.export import FORMAT, FORMAT_VERSION, UNSUPPORTED_KEY, JsonObject, JsonValue
from sva2yosys.slang.inside import dollar_problem, type_mismatch, wildcard_constant


@dataclass(frozen=True)
class Rejection:
    """An assertion that is not converted.

    Attributes:
        scope: Hierarchical scope of the assertion.
        name: Its label, or ``""``.
        message: What is not supported, e.g. ``"multiclock SVA properties are not
            supported yet"``.
        source: Position of the offending construct (or of the assertion).
    """

    scope: str
    name: str
    message: str
    source: SourceRange | None


class XChecks(Enum):
    """What happens to ``$isunknown`` (docs/semantics.md §8a)."""

    SKIP = "skip"
    """Evaluate it as the constant 0 (the model is two-state); a directive that checks
    nothing afterwards is :class:`Skipped`, one with checks left gets a
    :class:`ConversionWarning`."""

    REJECT = "reject"
    """Reject every directive that uses it, with a located message."""


@dataclass(frozen=True)
class Skipped:
    """A directive that checks nothing once ``$isunknown`` is evaluated as 0: it gets no
    monitor (docs/semantics.md §8a).

    Attributes:
        scope: Hierarchical scope of the assertion.
        name: Its label, or ``""``.
        kind: assert, assume or cover.
        reason: Why nothing is left, e.g. ``"X-check: this assert can never fail"``.
        source: Where the directive is written (the range the rewrite replaces).
        parameters: The parameters and genvars visible at the directive, as in
            :attr:`Directive.parameters`: the skip holds for these values only (a
            parameter may have folded a condition), so the rewrite guards on them.
        dependencies: The names among ``parameters`` that the skipped directive can depend on,
            as :attr:`Directive.dependencies` (``None``: unknown, every parameter counts).
    """

    scope: str
    name: str
    kind: DirectiveKind
    reason: str
    source: SourceRange
    parameters: tuple[tuple[str, ParameterValue | None], ...] = ()
    dependencies: frozenset[str] | None = None


@dataclass(frozen=True)
class ConversionWarning:
    """A converted directive whose ``$isunknown`` part was dropped but that still checks
    something (docs/semantics.md §8a).

    Attributes:
        scope: Hierarchical scope of the assertion.
        name: Its label, or ``""``.
        message: What was dropped and what it does to the directive.
        source: The first ``$isunknown`` call.
        directive: Where the directive is written.
    """

    scope: str
    name: str
    message: str
    source: SourceRange | None
    directive: SourceRange


@dataclass(frozen=True)
class Conversion:
    """Result of converting a document: converted directives, rejected assertions and
    skipped X-checks, each in document order, and the warnings of the converted
    directives."""

    directives: tuple[Directive, ...]
    rejections: tuple[Rejection, ...]
    skipped: tuple[Skipped, ...] = ()
    warnings: tuple[ConversionWarning, ...] = ()


class FormatError(ValueError):
    """The document is not a supported ``sva2yosys-slang-ast`` document."""


def convert(document: JsonObject, x_checks: XChecks = XChecks.SKIP) -> Conversion:
    """Convert every assertion record of ``document``.

    Args:
        document: A document produced by :func:`sva2yosys.slang.export.export_design`.
        x_checks: How ``$isunknown`` is treated (:class:`XChecks`).

    Returns:
        The converted directives and the rejections.

    Raises:
        FormatError: Wrong ``format``/``formatVersion`` or a malformed record.
    """
    if document.get("format") != FORMAT or document.get("formatVersion") != FORMAT_VERSION:
        raise FormatError(
            f"expected format {FORMAT!r} version {FORMAT_VERSION}, got "
            f"{document.get('format')!r} version {document.get('formatVersion')!r}"
        )
    records = document.get("assertions")
    if not isinstance(records, list):
        raise FormatError("'assertions' must be a list")
    directives: list[Directive] = []
    rejections: list[Rejection] = []
    skipped: list[Skipped] = []
    warnings: list[ConversionWarning] = []
    for value in records:
        record = _object(value, "assertion record")
        scope = _string(record, "scope")
        name = _string(record, "name")
        converter = _Converter(record, x_checks)
        try:
            directive = converter.directive(scope, name)
        except _RejectError as reject:
            source = _range(reject.node) or _range(record)
            rejections.append(Rejection(scope, name, reject.message, source))
        except _SkipError as skip:
            where = _range(record)
            if where is None:
                raise FormatError(f"assertion {scope}.{name} has no source range") from None
            kind = _DIRECTIVES[_string(record, "assertionKind")]
            parameters = _parameters(record)
            dependencies = _dependencies(record)
            if hidden := _hidden_parameters(record):
                rejections.append(Rejection(scope, name, _hidden_message(hidden), where))
            elif bad := uncomparable_parameters(parameters, dependencies):
                rejections.append(Rejection(scope, name, uncomparable_message(bad), where))
            else:
                skipped.append(
                    Skipped(scope, name, kind, skip.reason, where, parameters, dependencies)
                )
        else:
            if hidden := _hidden_parameters(record):
                rejections.append(Rejection(scope, name, _hidden_message(hidden), directive.source))
                continue
            if bad := uncomparable_parameters(directive.parameters, directive.dependencies):
                # the monitor of each elaboration is selected by comparing the parameters
                # visible at the assertion; output generation refuses these
                rejections.append(
                    Rejection(scope, name, uncomparable_message(bad), directive.source)
                )
                continue
            directives.append(directive)
            if converter.dropped is not None:
                message, where = converter.dropped
                warnings.append(ConversionWarning(scope, name, message, where, directive.source))
    return Conversion(tuple(directives), tuple(rejections), tuple(skipped), tuple(warnings))


# --- JSON access ------------------------------------------------------------------------


def _object(value: JsonValue, what: str) -> JsonObject:
    if not isinstance(value, dict):
        raise FormatError(f"{what} must be an object")
    return value


def _parameters(record: JsonObject) -> tuple[tuple[str, ParameterValue | None], ...]:
    """The ``sva2yosys_parameters`` of an assertion record, as ``(name, value)`` pairs."""
    return tuple(
        (_string(entry, "name"), _parameter_value(entry))
        for entry in (
            _object(item, "parameter")
            for item in _list(record.get("sva2yosys_parameters", []), "parameters")
        )
    )


def _hidden_parameters(record: JsonObject) -> tuple[str, ...]:
    """The ``sva2yosys_hiddenParameters`` of an assertion record, sorted (absent: none)."""
    names: set[str] = set()
    for item in _list(record.get("sva2yosys_hiddenParameters", []), "hidden parameters"):
        if not isinstance(item, str):
            raise FormatError("a hidden parameter name must be a string")
        names.add(item)
    return tuple(sorted(names))


def _hidden_message(names: tuple[str, ...]) -> str:
    return (
        f"{', '.join(names)} is used by this assertion but hidden at its position by another "
        "declaration of the same name; the monitor of each elaboration is selected by comparing "
        "the parameters visible at the assertion, so this is not supported"
    )


def _dependencies(record: JsonObject) -> frozenset[str] | None:
    """The ``sva2yosys_dependencies`` of an assertion record: the names the monitor can depend
    on, or ``None`` (unknown: every visible parameter counts) when the field is null or absent."""
    value = record.get("sva2yosys_dependencies")
    if value is None:
        return None
    names: set[str] = set()
    for item in _list(value, "dependencies"):
        if not isinstance(item, str):
            raise FormatError("a dependency name must be a string")
        names.add(item)
    return frozenset(names)


def _parameter_value(entry: JsonObject) -> ParameterValue | None:
    """A ``sva2yosys_parameters`` entry's value; ``None`` when it cannot be compared."""
    literal = entry.get("value")
    if not isinstance(literal, str):
        return None
    width, signed = entry.get("width"), entry.get("signed")
    if isinstance(width, int) and isinstance(signed, bool):
        return ParameterValue(literal, ValueType(width, signed))
    return ParameterValue(literal)


def _sampled(expr: Expr) -> frozenset[SampledFunction]:
    """The sampled-value functions used in ``expr``."""
    match expr:
        case Sampled(function=function, operand=operand):
            return frozenset({function}) | _sampled(operand)
        case Signal() | Const() | FirstSample():
            return frozenset()
        case Unary(operand=operand) | Convert(operand=operand):
            return _sampled(operand)
        case Binary(left=left, right=right):
            return _sampled(left) | _sampled(right)
        case Conditional(condition=condition, then=then, otherwise=otherwise):
            return _sampled(condition) | _sampled(then) | _sampled(otherwise)
        case BitSelect(value=inner) | RangeSelect(value=inner) | Replicate(value=inner):
            return _sampled(inner)
        case Concat(parts=parts):
            return frozenset().union(*(_sampled(part) for part in parts))


def _disjuncts(prop: Property) -> tuple[Property, ...]:
    """The operands of ``prop`` as a property ``or`` (nested ones flattened)."""
    if isinstance(prop, PropOr):
        return prop.operands
    return (prop,)


def _enabled(enable: Expr, prop: Property) -> Property:
    """A procedural directive with enabling condition ``enable`` as a module-level one
    (docs/semantics.md §6): no attempt where ``enable`` is false."""
    match prop:
        case Implication(antecedent=antecedent, consequent=consequent, overlapping=overlapping):
            return Implication(
                SeqConcat(Bool(enable), Delay(0, 0), antecedent), consequent, overlapping
            )
        case PropAnd(operands=operands):
            return PropAnd(tuple(_enabled(enable, operand) for operand in operands))
        case (
            SequenceProperty()
            | Not()
            | PropOr()
            | PropIf()
            | Nexttime()
            | Always()
            | Eventually()
            | Until()
        ):
            # if (enable) p: lowering fuses it into p's antecedent when p becomes an
            # implication (temporal.lower), as for an implication here
            return PropIf(enable, prop, None)


def _optional_string(record: JsonObject, key: str) -> str | None:
    """``record[key]``, which must be present and be a string or null."""
    if key not in record:
        raise FormatError(f"assertion record lacks {key}")
    value = record[key]
    if value is not None and not isinstance(value, str):
        raise FormatError(f"{key} is not a string or null")
    return value


def _list(value: JsonValue, what: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise FormatError(f"{what} must be a list")
    return value


def _string(node: JsonObject, key: str) -> str:
    value = node.get(key)
    if not isinstance(value, str):
        raise FormatError(f"'{key}' must be a string in {node.get('kind')!r}")
    return value


def _sampled_default(node: JsonObject) -> SampledDefault:
    """The ``sva2yosys_sampledDefault`` of a ``NamedValue``; a missing or unknown value is an
    export error, never read as X."""
    value = node.get("sva2yosys_sampledDefault")
    for default in SampledDefault:
        if value == default.value:
            return default
    raise _RejectError(
        f"internal export error: 'sva2yosys_sampledDefault' of '{node.get('symbol')}' is "
        f"missing or unknown ({value!r})",
        node,
    )


def _child(node: JsonObject, key: str) -> JsonObject:
    return _object(node.get(key), f"'{key}' of {node.get('kind')!r}")


def _kind(node: JsonObject) -> str:
    return _string(node, "kind")


def _range(node: JsonObject) -> SourceRange | None:
    try:
        return SourceRange(
            _string(node, "source_file_start"),
            _int(node, "source_line_start"),
            _int(node, "source_column_start"),
            _int(node, "source_line_end"),
            _int(node, "source_column_end"),
        )
    except FormatError:
        return None


def _int(node: JsonObject, key: str) -> int:
    value = node.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise FormatError(f"'{key}' must be an integer in {node.get('kind')!r}")
    return value


def _strip_ranges(node: JsonValue) -> JsonValue:
    """``node`` without source ranges, for structural comparison."""
    if isinstance(node, list):
        return [_strip_ranges(item) for item in node]
    if isinstance(node, dict):
        return {k: _strip_ranges(v) for k, v in node.items() if not k.startswith("source_")}
    return node


class _RejectError(Exception):
    def __init__(self, message: str, node: JsonObject) -> None:
        super().__init__(message)
        self.message = message
        self.node = node


def _default_known(what: str, reason: str, node: JsonObject) -> _RejectError:
    """The rejection of a construct inside a sampled operand whose default is not X."""
    return _RejectError(
        f"the value of {what} before the first clock tick is not unknown (IEEE 1800 "
        f"§16.5.1: {reason}); sva2yosys does not support it inside a sampled-value function",
        node,
    )


class _SkipError(Exception):
    """The directive checks nothing once ``$isunknown`` is evaluated as 0."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- tables -------------------------------------------------------------------------------

_DIRECTIVES = {
    "Assert": DirectiveKind.ASSERT,
    "Assume": DirectiveKind.ASSUME,
    "Restrict": DirectiveKind.ASSUME,
    "CoverProperty": DirectiveKind.COVER,
}
_UNSUPPORTED_DIRECTIVES = {
    "CoverSequence": "cover sequence",
    "Expect": "expect",
}

_UNARY = {
    "Plus": UnaryOp.PLUS,
    "Minus": UnaryOp.MINUS,
    "BitwiseNot": UnaryOp.BITWISE_NOT,
    "LogicalNot": UnaryOp.LOGICAL_NOT,
    "BitwiseAnd": UnaryOp.REDUCE_AND,
    "BitwiseOr": UnaryOp.REDUCE_OR,
    "BitwiseXor": UnaryOp.REDUCE_XOR,
    "BitwiseNand": UnaryOp.REDUCE_NAND,
    "BitwiseNor": UnaryOp.REDUCE_NOR,
    "BitwiseXnor": UnaryOp.REDUCE_XNOR,
}

_BINARY = {
    "Add": BinaryOp.ADD,
    "Subtract": BinaryOp.SUBTRACT,
    "Multiply": BinaryOp.MULTIPLY,
    "Divide": BinaryOp.DIVIDE,
    "Mod": BinaryOp.MOD,
    "BinaryAnd": BinaryOp.BITWISE_AND,
    "BinaryOr": BinaryOp.BITWISE_OR,
    "BinaryXor": BinaryOp.BITWISE_XOR,
    "BinaryXnor": BinaryOp.BITWISE_XNOR,
    "Equality": BinaryOp.EQUALITY,
    "Inequality": BinaryOp.INEQUALITY,
    "CaseEquality": BinaryOp.CASE_EQUALITY,
    "CaseInequality": BinaryOp.CASE_INEQUALITY,
    "LessThan": BinaryOp.LESS_THAN,
    "LessThanEqual": BinaryOp.LESS_THAN_EQUAL,
    "GreaterThan": BinaryOp.GREATER_THAN,
    "GreaterThanEqual": BinaryOp.GREATER_THAN_EQUAL,
    "LogicalAnd": BinaryOp.LOGICAL_AND,
    "LogicalOr": BinaryOp.LOGICAL_OR,
    "LogicalImplication": BinaryOp.LOGICAL_IMPLICATION,
    "LogicalEquivalence": BinaryOp.LOGICAL_EQUIVALENCE,
    "LogicalShiftLeft": BinaryOp.LOGICAL_SHIFT_LEFT,
    "LogicalShiftRight": BinaryOp.LOGICAL_SHIFT_RIGHT,
    "ArithmeticShiftLeft": BinaryOp.ARITHMETIC_SHIFT_LEFT,
    "ArithmeticShiftRight": BinaryOp.ARITHMETIC_SHIFT_RIGHT,
}

_BIT_COUNTS = ("$countones", "$onehot", "$onehot0")
_INT = ValueType(32, signed=True)
BIT = ValueType(1)

_SAMPLED = {
    "$past": SampledFunction.PAST,
    "$rose": SampledFunction.ROSE,
    "$fell": SampledFunction.FELL,
    "$stable": SampledFunction.STABLE,
    "$changed": SampledFunction.CHANGED,
}

# Slang's assertion operator names, spelled as in SystemVerilog for messages.
_ASSERTION_OPS = {
    "And": "and",
    "Or": "or",
    "Intersect": "intersect",
    "Within": "within",
    "Throughout": "throughout",
    "Iff": "iff",
    "Until": "until",
    "SUntil": "s_until",
    "UntilWith": "until_with",
    "SUntilWith": "s_until_with",
    "Implies": "implies",
    "OverlappedFollowedBy": "#-#",
    "NonOverlappedFollowedBy": "#=#",
    "Not": "not",
    "NextTime": "nexttime",
    "SNextTime": "s_nexttime",
    "Always": "always",
    "SAlways": "s_always",
    "Eventually": "eventually",
    "SEventually": "s_eventually",
}

_UNTIL_OPS = {
    "Until": (False, False),
    "SUntil": (True, False),
    "UntilWith": (False, True),
    "SUntilWith": (True, True),
}
"""The ``until`` family by Slang op name: ``(strong, inclusive)``."""

_TEMPORAL_OPS = {
    "NextTime": "nexttime",
    "SNextTime": "nexttime",
    "Always": "always",
    "SAlways": "always",
    "Eventually": "eventually",
    "SEventually": "eventually",
}
"""The unary temporal property operators, by Slang op name, with their family."""

_SIZED = re.compile(r"(-)?(\d+)'(s)?([bodhBODH])([0-9a-fA-F_xXzZ?]+)")
_UNSIZED = re.compile(r"-?\d+")
_BASES = {"b": 2, "o": 8, "d": 10, "h": 16}

_MULTICLOCK = "multiclock SVA properties are not supported yet"


@dataclass(frozen=True)
class _Packed:
    """A packed integral type with its declared range ``[left:right]``."""

    type: ValueType
    left: int
    right: int
    dims: int = 0
    """Number of packed dimensions (an enum or packed struct counts one flat dimension)."""
    aggregate: bool = False
    """A packed struct or union."""

    def position(self, index: int) -> int | None:
        """Bit position (0 = least significant) of element ``index``, if in range."""
        low, high = min(self.left, self.right), max(self.left, self.right)
        if not low <= index <= high:
            return None
        return index - self.right if self.left >= self.right else self.right - index


def _packed(node: JsonObject) -> _Packed:
    """The packed integral type of an expression node, from the exporter's
    ``sva2yosys_integral`` (never parsed from the type string)."""
    text = _string(node, "type")
    if "sva2yosys_integral" not in node:
        raise _RejectError(
            f"internal export error: 'sva2yosys_integral' of type '{text}' is missing", node
        )
    description = node["sva2yosys_integral"]
    if description is False:
        raise _RejectError(f"unsupported type '{text}' (only packed integral types)", node)
    fields = _object(description, "'sva2yosys_integral'")
    width, left, right = _int(fields, "width"), _int(fields, "msb"), _int(fields, "lsb")
    signed = fields.get("signed")
    if not isinstance(signed, bool) or width < 1:
        raise _RejectError("internal export error: malformed 'sva2yosys_integral'", node)
    return _Packed(
        ValueType(width, signed),
        left,
        right,
        _int(fields, "dims"),
        fields.get("aggregate") is True,
    )


def _selectable(node: JsonObject) -> _Packed:
    """The packed type of a value that a bit or part select reads: one packed dimension of
    bits. Elements of a multi-dimensional packed array and bits of a packed struct need a
    different position map and are rejected."""
    packed = _packed(node)
    if packed.dims > 1 or packed.aggregate:
        raise _RejectError(
            "selects into multi-dimensional packed arrays or packed structs are not supported yet",
            node,
        )
    return packed


def _constant(text: str, value_type: ValueType, node: JsonObject) -> Const:
    sized = _SIZED.fullmatch(text)
    if sized is not None:
        digits = sized.group(5).replace("_", "")
        if re.search(r"[xXzZ?]", digits):
            raise _RejectError(f"constant {text} contains x/z bits (two-state model)", node)
        value = int(digits, _BASES[sized.group(4).lower()])
        if sized.group(1):
            value = -value
    elif _UNSIZED.fullmatch(text):
        value = int(text)
    else:
        raise _RejectError(f"unsupported constant '{text}'", node)
    return Const(value % (1 << value_type.width), value_type)


def _has_sampled_call(node: JsonValue) -> bool:
    """Whether ``node`` contains a call of a sampled-value function."""
    if isinstance(node, dict):
        if node.get("kind") == "Call" and node.get("subroutine") in _SAMPLED:
            return True
        return any(_has_sampled_call(child) for child in node.values())
    if isinstance(node, list):
        return any(_has_sampled_call(child) for child in node)
    return False


def _is_unbounded(node: JsonObject) -> bool:
    """Whether a range bound is the unbounded literal ``$`` (under its conversions)."""
    while _kind(node) == "Conversion":
        node = _child(node, "operand")
    return _kind(node) == "UnboundedLiteral"


class _Converter:
    """Converts one assertion record."""

    def __init__(self, record: JsonObject, x_checks: XChecks = XChecks.SKIP) -> None:
        self._record = record
        self._clock_json: JsonValue = None
        self._x_checks = x_checks
        self._unknown: list[SourceRange | None] = []
        self._validated: set[tuple[int, bool]] = set()
        self.dropped: tuple[str, SourceRange | None] | None = None
        """After :meth:`directive`: the warning of a directive that used ``$isunknown`` and
        was converted (message, first call), else ``None``."""

    def directive(self, scope: str, name: str) -> Directive:
        record = self._record
        kind_name = _string(record, "assertionKind")
        if kind_name in _UNSUPPORTED_DIRECTIVES:
            raise _RejectError(f"{_UNSUPPORTED_DIRECTIVES[kind_name]} is not supported yet", record)
        if kind_name not in _DIRECTIVES:
            raise _RejectError(f"unsupported directive '{kind_name}'", record)
        procedural = record.get("sva2yosys_procedural") is True
        initial = record.get("sva2yosys_procedureKind") == "initial"
        if procedural:
            issue = _optional_string(record, "sva2yosys_procedureIssue")
            if issue is not None:
                raise _RejectError(issue, record)
        inside_issue = record.get("sva2yosys_insideIssue")  # present only when there is one
        if isinstance(inside_issue, str):
            raise _RejectError(inside_issue, record)
        # `inside` expressions that Slang evaluates as constants (parameters, generate
        # conditions ...): accepted only when uniform, where every reading agrees
        for constant in _list(record.get("sva2yosys_constantInsides", []), "constant insides"):
            self._inside_uniform(_object(constant, "constant inside"))
        clock = self._clock(record)
        # A missing or malformed timing analysis must not pass silently.
        disable_issue = _optional_string(record, "sva2yosys_disableIssue")
        if disable_issue is not None:
            raise _RejectError(disable_issue, record)
        if procedural:
            if "sva2yosys_procedureClock" not in record or "sva2yosys_enable" not in record:
                raise FormatError("a procedural assertion record lacks its procedure analysis")
            kind = record.get("sva2yosys_procedureKind")
            if kind not in ("always", "initial"):
                raise FormatError("a procedural assertion record lacks its procedure kind")
            event = record.get("sva2yosys_procedureClock")
            # An initial block without an event control takes its clock from the assertion
            # itself or from default clocking (IEEE 1800-2017 16.14.6, 16.16).
            if event is None:
                differs = kind == "always"
            else:
                differs = _strip_ranges(_object(event, "event")) != self._clock_json
            if differs:
                raise _RejectError(
                    "the clock of this procedural concurrent assertion differs from the "
                    f"event of its {kind} block",
                    record,
                )
        spec = self._strip_clocks(_child(record, "propertySpec"))
        before = len(self._unknown)
        disable: Expr | None = None
        if _kind(spec) == "DisableIff":
            disable = self._expr(_child(spec, "condition"))
            spec = self._strip_clocks(_child(spec, "expr"))
        else:
            default = record.get("sva2yosys_defaultDisable")
            if default is not None:
                disable = self._expr(_object(default, "default disable"))
        in_disable = len(self._unknown)
        source = _range(record)
        if source is None:
            raise FormatError(f"assertion {scope}.{name} has no source range")
        parameters = _parameters(record)
        prop = self._property(spec)
        in_property = len(self._unknown)
        enable = self._enable(record, initial) if procedural else None
        if len(self._unknown) > before:
            disable, prop, enable = self._without_x(
                _DIRECTIVES[kind_name],
                (disable, prop, enable),
                (in_disable > before, in_property > in_disable, len(self._unknown) > in_property),
            )
        if enable is not None:
            prop = _enabled(enable, prop)
        return Directive(
            _DIRECTIVES[kind_name],
            name,
            scope,
            clock,
            disable,
            prop,
            source,
            parameters,
            _dependencies(record),
        )

    def _without_x(
        self,
        kind: DirectiveKind,
        parts: tuple[Expr | None, Property, Expr | None],
        used: tuple[bool, bool, bool],
    ) -> tuple[Expr | None, Property, Expr | None]:
        """The ``disable iff`` condition, property and enabling condition of a directive
        that used ``$isunknown`` (as the booleans of ``used`` say, for each part), folded
        (:mod:`sva2yosys.passes.x_checks`); sets :attr:`dropped`.

        Raises:
            _SkipError: Nothing is left to observe (docs/semantics.md §8a).
        """
        disable, prop, enable = parts
        word = kind.value
        notes: list[str] = []
        if disable is not None and used[0]:
            disable = fold_expr(disable)
            always = truth(disable)
            if always is True:
                raise _SkipError(
                    "X-check: the disable iff condition is always true ($isunknown evaluated "
                    f"as 0), so this {word} is always disabled"
                )
            if always is False:
                disable = None
                notes.append("the disable iff condition is always false, so it never disables")
        if enable is not None and used[2]:
            enable = fold_expr(enable)
            always = truth(enable)
            if always is False:
                raise _SkipError(
                    "X-check: the enabling condition is always false ($isunknown evaluated "
                    f"as 0), so this {word} is never reached"
                )
            if always is True:
                enable = None
                notes.append("the enabling condition is always true")
        if used[1]:
            prop = fold_property(prop)
            nothing = never_succeeds(prop) if kind is DirectiveKind.COVER else never_fails(prop)
            if nothing:
                effect = {
                    DirectiveKind.ASSERT: "can never fail",
                    DirectiveKind.ASSUME: "constrains nothing",
                    DirectiveKind.COVER: "can never succeed",
                }[kind]
                raise _SkipError(
                    f"X-check: $isunknown is evaluated as 0 (two-state model), so this {word} "
                    f"{effect}"
                )
        message = (
            f"$isunknown is evaluated as 0 (two-state model): the X part of this {word} is "
            "dropped, the rest is still checked"
        ) + "".join(f"; {note}" for note in notes)
        self.dropped = (message, self._unknown[0])
        return disable, prop, enable

    def _enable(self, record: JsonObject, initial: bool) -> Expr | None:
        """The conjunction of a procedural assertion's path conditions, or ``None``. For an
        ``initial`` block it starts with ``FirstSample``: the directive has one attempt, at
        the first tick of its clock (IEEE 1800-2017 16.14.6; docs/semantics.md §6)."""
        terms: list[Expr] = [FirstSample(BIT)] if initial else []
        for value in _list(record.get("sva2yosys_enable", []), "enable"):
            entry = _object(value, "enable entry")
            subject = self._expr(_child(entry, "expr"))
            term = subject
            if _string(entry, "kind") == "Case":
                equalities = [
                    Binary(BinaryOp.EQUALITY, subject, self._expr(_object(item, "case item")), BIT)
                    for item in _list(entry.get("items"), "case items")
                ]
                term = equalities[0]
                for equality in equalities[1:]:
                    term = Binary(BinaryOp.LOGICAL_OR, term, equality, BIT)
            if entry.get("negated") is True:
                term = Unary(UnaryOp.LOGICAL_NOT, term, BIT)
            if _sampled(term):
                raise _RejectError(
                    "a sampled-value function in the enabling condition of a procedural "
                    "concurrent assertion is not supported",
                    entry,
                )
            terms.append(term)
        if not terms:
            return None
        enable = terms[0]
        for term in terms[1:]:
            enable = Binary(BinaryOp.LOGICAL_AND, enable, term, BIT)
        return enable

    # --- clock ------------------------------------------------------------------------

    def _clock(self, record: JsonObject) -> Clock:
        value = record.get("sva2yosys_clock")
        if value is None:
            raise _RejectError(
                "the assertion has no clock: give it a clock event, an enclosing event "
                "control or a default clocking (IEEE 1800-2017 16.16)",
                record,
            )
        clock = _object(value, "clock")
        self._clock_json = _strip_ranges(clock)
        if _kind(clock) != "SignalEvent":
            raise _RejectError(f"unsupported clocking event '{_kind(clock)}'", clock)
        edge = _string(clock, "edge")
        if edge == "NegEdge":
            raise _RejectError("negedge clocks are not supported yet", clock)
        if edge != "PosEdge":
            raise _RejectError(f"clock edge '{edge}' is not supported", clock)
        if clock.get("iff") is not None:
            raise _RejectError("clocks with an iff condition are not supported yet", clock)
        signal = self._expr(_child(clock, "expr"))
        if not isinstance(signal, Signal) or signal.type.width != 1:
            raise _RejectError("the clock must be a 1-bit signal", clock)
        return Clock(signal, Edge.POSEDGE)

    def _strip_clocks(self, node: JsonObject) -> JsonObject:
        """Remove clocking equal to the leading clock; any other clock is multiclock."""
        while _kind(node) == "Clocking":
            if _strip_ranges(node.get("clocking")) != self._clock_json:
                raise _RejectError(_MULTICLOCK, node)
            node = _child(node, "expr")
        return node

    # --- properties and sequences ------------------------------------------------------

    def _property(self, node: JsonObject) -> Property:
        node = self._strip_clocks(node)
        kind = _kind(node)
        if kind == "Binary":
            op = _string(node, "op")
            if op in ("OverlappedImplication", "NonOverlappedImplication"):
                antecedent = self._sequence(_child(node, "left"))
                consequent = self._property(_child(node, "right"))
                overlapping = op == "OverlappedImplication"

                return Implication(antecedent, consequent, overlapping)
            if op == "And":
                left = self._property(_child(node, "left"))
                right = self._property(_child(node, "right"))
                if (
                    isinstance(left, SequenceProperty)
                    and isinstance(right, SequenceProperty)
                    and left.strength is Strength.DEFAULT
                    and right.strength is Strength.DEFAULT
                ):
                    # two sequences: the sequence 'and' (§16.9.5), as a sequence property
                    return SequenceProperty(self._sequence(node))
                return PropAnd(conjuncts(left) + conjuncts(right))
            if op == "Or":
                left = self._property(_child(node, "left"))
                right = self._property(_child(node, "right"))
                if (
                    isinstance(left, SequenceProperty)
                    and isinstance(right, SequenceProperty)
                    and left.strength is Strength.DEFAULT
                    and right.strength is Strength.DEFAULT
                ):
                    # two sequences: the sequence 'or' (§16.9.7), as a sequence property
                    return SequenceProperty(self._sequence(node))
                return PropOr(_disjuncts(left) + _disjuncts(right))
            if op in ("Intersect", "Within", "Throughout"):
                return SequenceProperty(self._sequence(node))
            if op in _UNTIL_OPS:
                return self._until(node, op)
            raise _RejectError(
                f"property operator '{_ASSERTION_OPS.get(op, op)}' is not supported yet", node
            )
        if kind == "Unary" and _string(node, "op") == "Not":
            return Not(self._property(_child(node, "expr")))
        if kind == "Unary":
            op = _string(node, "op")
            if op in _TEMPORAL_OPS:
                return self._temporal(node, op)
            raise _RejectError(
                f"property operator '{_ASSERTION_OPS.get(op, op)}' is not supported yet", node
            )
        if kind == "Conditional":
            return self._conditional(node)
        if kind == "StrongWeak":
            strengths = {"Strong": Strength.STRONG, "Weak": Strength.WEAK}
            written = _string(node, "strength")
            if written not in strengths:
                raise FormatError(f"unknown sequence strength {written!r}")
            strength = strengths[written]
            return SequenceProperty(self._sequence(_child(node, "expr")), strength)
        if kind == "Simple" and "repetition" not in node:
            inner = _child(node, "expr")
            if _kind(inner) == "AssertionInstance" and inner.get("type") == "property":
                return self._property(_child(inner, "body"))
        return SequenceProperty(self._sequence(node))

    def _temporal(self, node: JsonObject, op: str) -> Property:
        """``nexttime``/``always``/``eventually`` and their strong forms (§16.12.10, §16.12.11,
        §16.12.13). ``nexttime`` takes one count (1 without a range). ``always`` without a
        range is ``always [0:$]``, and ``s_eventually`` without a range is
        ``s_eventually [0:$]``; ``$`` is kept as an unbounded upper bound (``None``). IEEE
        allows ``$`` only for the weak ``always`` and the strong ``s_eventually``, and
        requires a range for ``s_always`` and ``eventually``; the others are rejected."""
        name = _ASSERTION_OPS[op]
        strong = op.startswith("S")
        family = _TEMPORAL_OPS[op]
        has_range = "min" in node
        unbounded = node.get("max") == "$"
        if family == "nexttime":
            if unbounded:
                raise _RejectError(f"'{name}' takes a single count, not a range", node)
        elif (unbounded or not has_range) and (family == "always") == strong:
            # s_always, eventually
            raise _RejectError(
                f"'{name}' without an upper bound is not allowed (IEEE 1800 §16.12.11, "
                "§16.12.13): give a range [m:n]",
                node,
            )
        low = _int(node, "min") if has_range else (1 if family == "nexttime" else 0)
        high: int | None
        if unbounded or (not has_range and family != "nexttime"):
            high = None
        else:
            high = _int(node, "max") if "max" in node else low
        operand = self._property(_child(node, "expr"))
        if family == "nexttime":
            if high != low:
                raise _RejectError(f"'{name}' takes a single count, not a range", node)
            return Nexttime(low, strong, operand)
        if high is not None and low > high:
            raise _RejectError(f"the range [{low}:{high}] of '{name}' is empty", node)
        if family == "always":
            return Always(low, high, strong, operand)
        return Eventually(low, high, strong, operand)

    def _until(self, node: JsonObject, op: str) -> Property:
        """``until``, ``s_until``, ``until_with`` and ``s_until_with`` (§16.12.12) with
        Boolean operands; property and sequence operands are rejected."""
        name = _ASSERTION_OPS[op]
        strong, inclusive = _UNTIL_OPS[op]
        left = self._until_operand(_child(node, "left"), name)
        right = self._until_operand(_child(node, "right"), name)
        return Until(left, right, strong, inclusive)

    def _until_operand(self, node: JsonObject, name: str) -> Expr:
        node = self._strip_clocks(node)
        if _kind(node) == "Simple" and "repetition" not in node:
            inner = _child(node, "expr")
            if _kind(inner) != "AssertionInstance":
                return self._expr(inner)
        raise _RejectError(
            f"the operands of '{name}' must be Boolean expressions "
            "(sequence and property operands are not supported yet)",
            node,
        )

    def _conditional(self, node: JsonObject) -> Property:
        """A property ``if (e) p [else q]`` (§16.12.6), preserved as
        :class:`~sva2yosys.ir.property.PropIf`; :func:`~sva2yosys.temporal.lower.lower`
        rewrites the supported shapes."""
        condition = self._expr(_child(node, "condition"))
        then = self._property(_child(node, "ifExpr"))
        other = node.get("elseExpr")
        if other is None:
            return PropIf(condition, then, None)
        if SampledFunction.PAST in _sampled(condition):
            # $past is X at the first samples, which selects the else branch, while the
            # first-sample rule would force both e and !e false (docs/semantics.md §8).
            raise _RejectError(
                "a property if/else whose condition uses $past is not supported", node
            )
        return PropIf(condition, then, self._property(_object(other, "else branch")))

    def _sequence(self, node: JsonObject) -> Sequence:
        node = self._strip_clocks(node)
        if node.get(UNSUPPORTED_KEY) is True:
            raise _RejectError(f"unsupported construct '{_kind(node)}'", node)
        match _kind(node):
            case "Simple":
                inner = _child(node, "expr")
                if _kind(inner) == "AssertionInstance":
                    base = self._sequence(_child(inner, "body"))
                else:
                    base = Bool(self._expr(inner))
                repetition = node.get("repetition")
                if repetition is None:
                    return base
                return self._repeat(base, _object(repetition, "repetition"), node)
            case "SequenceConcat":
                return self._concat(node)
            case "SequenceWithMatch":
                items = node.get("matchItems")
                if not isinstance(items, list):
                    raise FormatError("'matchItems' must be a list")
                if items:
                    raise _RejectError(
                        "sequence match items (local variable assignments) are not supported yet",
                        node,
                    )
                base = self._sequence(_child(node, "expr"))
                repetition = node.get("repetition")
                if repetition is None:
                    return base
                return self._repeat(base, _object(repetition, "repetition"), node)
            case "Binary":
                op = _string(node, "op")
                if op in ("Or", "And", "Intersect", "Within"):
                    left = self._sequence(_child(node, "left"))
                    right = self._sequence(_child(node, "right"))
                    match op:
                        case "Or":
                            return SeqOr(left, right)
                        case "And":
                            return SeqAnd(left, right)
                        case "Intersect":
                            return SeqIntersect(left, right)
                        case _:
                            return Within(left, right)
                if op == "Throughout":
                    condition = _child(node, "left")
                    if _kind(condition) != "Simple" or "repetition" in condition:
                        raise _RejectError(
                            "the left operand of throughout must be a condition", node
                        )
                    return Throughout(
                        self._expr(_child(condition, "expr")), self._sequence(_child(node, "right"))
                    )
                raise _RejectError(
                    f"sequence operator '{_ASSERTION_OPS.get(op, op)}' is not supported yet", node
                )
            case "Unary":
                op = _string(node, "op")
                raise _RejectError(
                    f"operator '{_ASSERTION_OPS.get(op, op)}' is not supported yet", node
                )
            case "DisableIff":
                raise _RejectError("disable iff is only supported at the top of a property", node)
            case other:
                raise _RejectError(f"unsupported construct '{other}'", node)

    def _repeat(self, base: Sequence, repetition: JsonObject, node: JsonObject) -> Sequence:
        kind = _string(repetition, "kind")
        minimum, maximum = self._bounds(repetition)
        if kind in ("Nonconsecutive", "GoTo"):
            if not isinstance(base, Bool):
                raise _RejectError(
                    "goto and nonconsecutive repetition apply to a Boolean expression", node
                )
            if kind == "GoTo":
                return Goto(base.expr, minimum, maximum)
            return NonConsecutive(base.expr, minimum, maximum)
        return Repeat(base, minimum, maximum)

    def _concat(self, node: JsonObject) -> Sequence:
        elements = node.get("elements")
        if not isinstance(elements, list) or not elements:
            raise FormatError("'elements' must be a non-empty list")
        result: Sequence | None = None
        for value in elements:
            element = _object(value, "element")
            sequence = self._sequence(_child(element, "sequence"))
            minimum, maximum = self._bounds(element)
            delay = Delay(minimum, maximum)
            if result is None:
                result = (
                    sequence if (minimum, maximum) == (0, 0) else SeqConcat(TRUE, delay, sequence)
                )
            else:
                result = SeqConcat(result, delay, sequence)
        assert result is not None
        return result

    @staticmethod
    def _bounds(node: JsonObject) -> tuple[int, int | None]:
        """The ``min`` and ``max`` of a repetition or delay; ``$`` is ``None``."""
        if node.get("max") == "$":
            return _int(node, "min"), None
        return _int(node, "min"), _int(node, "max")

    # --- expressions -------------------------------------------------------------------

    def _expr(self, node: JsonObject, *, in_sampled: bool = False) -> Expr:
        kind = _kind(node)
        if node.get(UNSUPPORTED_KEY) is True:
            raise _RejectError(f"unsupported construct '{kind}'", node)
        packed = _packed(node)
        value_type = packed.type
        constant = node.get("constant")
        if isinstance(constant, str):
            # Slang folded the node as a whole; an 'inside' in it is evaluated in Slang's common
            # type, so it gets the same checks as an unfolded one (rejected where that type is
            # not the one the standard gives each comparison)
            self._check_folded_inside(node, in_sampled)
            return _constant(constant, value_type, node)
        match kind:
            case "NamedValue":
                if node.get("sva2yosys_visibleAtSite") is False:
                    raise _RejectError(
                        f"'{_string(node, 'symbol')}' means something else where the assertion "
                        "is written (a nearer scope declares the same name); generated monitors "
                        "connect signals by name",
                        node,
                    )
                return Signal(
                    _string(node, "sva2yosys_path"),
                    value_type,
                    _sampled_default(node),
                )
            case "HierarchicalValue":
                raise _RejectError("hierarchical references are not supported yet", node)
            case "IntegerLiteral" | "UnbasedUnsizedIntegerLiteral":
                return _constant(_string(node, "value"), value_type, node)
            case "UnaryOp":
                op = _string(node, "op")
                if op not in _UNARY:
                    raise _RejectError(f"operator '{op}' is not supported", node)
                return Unary(
                    _UNARY[op],
                    self._expr(_child(node, "operand"), in_sampled=in_sampled),
                    value_type,
                )
            case "BinaryOp":
                op = _string(node, "op")
                if op not in _BINARY:
                    raise _RejectError(f"operator '{op}' is not supported yet", node)
                return Binary(
                    _BINARY[op],
                    self._expr(_child(node, "left"), in_sampled=in_sampled),
                    self._expr(_child(node, "right"), in_sampled=in_sampled),
                    value_type,
                )
            case "ConditionalOp":
                conditions = node.get("conditions")
                if not isinstance(conditions, list) or len(conditions) != 1:
                    raise _RejectError("conditional with patterns or several conditions", node)
                condition = _child(_object(conditions[0], "condition"), "expr")
                return Conditional(
                    self._expr(condition, in_sampled=in_sampled),
                    self._expr(_child(node, "left"), in_sampled=in_sampled),
                    self._expr(_child(node, "right"), in_sampled=in_sampled),
                    value_type,
                )
            case "Conversion":
                four_state = node.get("sva2yosys_fourState")
                if not isinstance(four_state, bool):
                    raise _RejectError(
                        "internal export error: 'sva2yosys_fourState' of a conversion is "
                        "missing or not a boolean",
                        node,
                    )
                if in_sampled and not four_state:
                    raise _default_known(
                        "a cast to a 2-state type", "the cast turns X into 0", node
                    )
                kind_name = node.get("sva2yosys_conversionKind")
                if not isinstance(kind_name, str):
                    raise _RejectError(
                        "internal export error: 'sva2yosys_conversionKind' of a conversion "
                        "is missing or not a string",
                        node,
                    )
                operand = self._expr(_child(node, "operand"), in_sampled=in_sampled)
                if (
                    kind_name == "Propagated"
                    and operand.type.signed
                    and not value_type.signed
                    and value_type.width > operand.type.width
                ):
                    # 11.8.2: an operand is sign-extended only when the propagated type is
                    # signed; here it is unsigned, so the extension is by zeros.
                    operand = Convert(operand, ValueType(operand.type.width, signed=False))
                return Convert(operand, value_type)
            case "ElementSelect":
                return self._bit_select(node, value_type, in_sampled)
            case "RangeSelect":
                return self._range_select(node, value_type, in_sampled)
            case "Concatenation":
                operands = node.get("operands")
                if not isinstance(operands, list):
                    raise FormatError("'operands' must be a list")
                parts = tuple(
                    self._expr(_object(o, "operand"), in_sampled=in_sampled) for o in operands
                )
                return Concat(parts, value_type)
            case "Replication":
                count = self._constant_int(_child(node, "count"), "replication count")
                return Replicate(
                    count, self._expr(_child(node, "concat"), in_sampled=in_sampled), value_type
                )
            case "Call":
                return self._sampled(node, value_type, in_sampled)
            case "Inside":
                return self._inside(node, in_sampled)
            case other:
                raise _RejectError(f"unsupported expression '{other}'", node)

    def _check_folded_inside(self, node: JsonValue, in_sampled: bool) -> None:
        """Apply the checks of :meth:`_inside` to every ``inside`` below ``node``."""
        if isinstance(node, dict):
            if node.get("kind") == "Inside":
                key = (id(node), in_sampled)
                if key in self._validated:
                    return
                self._inside(node, in_sampled)
                self._validated.add(key)
            for child in node.values():
                self._check_folded_inside(child, in_sampled)
        elif isinstance(node, list):
            for child in node:
                self._check_folded_inside(child, in_sampled)

    def _inside(self, node: JsonObject, in_sampled: bool) -> Expr:
        """``e inside { items }`` (IEEE 1800-2017 11.4.13) as an OR over the items: ``e == item``
        for a value item (``(e & m) == (item & m)`` when a constant item has X, Z or ``?``
        bits, which are wildcards: the only place where such a constant is accepted),
        ``lo <= e && e <= hi`` for a range. Only a uniform list is accepted (one natural width
        and signedness, :func:`type_mismatch`), where every reading of 11.4.13 sizes the
        comparisons alike; the whole list is checked before any other item rule."""
        if in_sampled:
            raise _RejectError(
                "'inside' inside a sampled-value function is not supported: its default "
                "sampled value (X, or 1 for a wildcard item, 16.5.1, 11.4.13) is not the "
                "value the two-state lowering would give",
                node,
            )
        left_node = _child(node, "left")
        items = _list(node.get("ranges"), "'ranges'")
        _packed(left_node)  # a non-integral left operand is rejected here
        for entry in items:
            if _has_sampled_call(entry):
                raise _RejectError(
                    "a sampled-value function in an item of 'inside' is not supported: an X "
                    "item is all wildcards (11.4.6), so `v inside {$past(w)}` holds at the "
                    "first sample, which `v == $past(w)` does not",
                    _object(entry, "inside item"),
                )
        self._inside_uniform(node)
        left = self._expr(left_node, in_sampled=in_sampled)
        result: Expr = Const(0, BIT)
        for index, entry in enumerate(items):
            item = _object(entry, "inside item")
            term = (
                self._inside_range(left_node, left, item, in_sampled)
                if _kind(item) == "ValueRange"
                else self._inside_value(left_node, left, item, in_sampled)
            )
            result = term if index == 0 else Binary(BinaryOp.LOGICAL_OR, result, term, BIT)
        return result

    def _inside_uniform(self, node: JsonObject) -> None:
        """Reject ``node`` (an ``Inside``) unless its left operand, value items and finite
        range bounds share one natural width and signedness (contract point 16)."""
        left_node = _child(node, "left")
        for entry in _list(node.get("ranges"), "'ranges'"):
            item = _object(entry, "inside item")
            if _kind(item) != "ValueRange":
                self._inside_check(left_node, item)
                continue
            for key in ("left", "right"):
                bound = _child(item, key)
                if not _is_unbounded(bound):
                    self._inside_check(left_node, bound)

    def _inside_check(self, left_node: JsonObject, other: JsonObject) -> None:
        if other.get("sva2yosys_integral") is False:
            raise _RejectError(
                "an unpacked array or other non-integral item of 'inside' is not supported", other
            )
        try:
            problem = type_mismatch(left_node, other)
        except ValueError as error:
            raise _RejectError(f"internal export error in 'inside': {error}", other) from error
        if problem is not None:
            raise _RejectError(f"unsupported 'inside': {problem}", other)

    def _inside_value(
        self, left_node: JsonObject, left: Expr, item: JsonObject, in_sampled: bool
    ) -> Expr:
        self._inside_check(left_node, item)
        # Slang folds a literal with X/Z bits only when it is converted; a bare literal keeps
        # its digits in "value"
        constant = item.get("constant")
        if not isinstance(constant, str) and _kind(item) == "IntegerLiteral":
            constant = item.get("value")
        wildcard = wildcard_constant(constant) if isinstance(constant, str) else None
        if wildcard is None:
            return Binary(BinaryOp.EQUALITY, left, self._expr(item, in_sampled=in_sampled), BIT)
        if _has_sampled_call(left_node):
            raise _RejectError(
                "a wildcard item of 'inside' with a sampled-value function in the left operand "
                "is not supported: a wildcard bit matches the X of the first sample",
                item,
            )
        mask = ((1 << wildcard.width) - 1) & ~wildcard.wildcard
        value_type = left.type
        return Binary(
            BinaryOp.EQUALITY,
            Binary(BinaryOp.BITWISE_AND, left, Const(mask, value_type), value_type),
            Const(wildcard.pattern & mask, value_type),
            BIT,
        )

    def _inside_range(
        self, left_node: JsonObject, left: Expr, item: JsonObject, in_sampled: bool
    ) -> Expr:
        bounds: list[Expr | None] = []
        for key in ("left", "right"):
            bound = _child(item, key)
            if _is_unbounded(bound):
                # 11.4.13: `$` is the lowest or highest value of the left operand's type, so
                # the comparison with it always holds (never a finite limit is substituted)
                problem = dollar_problem(left_node)
                if problem is not None:
                    raise _RejectError(f"unsupported '$' bound of 'inside': {problem}", bound)
                bounds.append(None)
                continue
            self._inside_check(left_node, bound)
            bounds.append(self._expr(bound, in_sampled=in_sampled))
        low, high = bounds
        terms: list[Expr] = []
        if low is not None:
            terms.append(Binary(BinaryOp.LESS_THAN_EQUAL, low, left, BIT))
        if high is not None:
            terms.append(Binary(BinaryOp.LESS_THAN_EQUAL, left, high, BIT))
        if not terms:
            return Const(1, BIT)
        return (
            terms[0] if len(terms) == 1 else Binary(BinaryOp.LOGICAL_AND, terms[0], terms[1], BIT)
        )

    def _sampled(self, node: JsonObject, value_type: ValueType, in_sampled: bool) -> Expr:
        name = _string(node, "subroutine")
        if name in _BIT_COUNTS:
            return self._bit_count(node, name, value_type, in_sampled)
        if name == "$isunknown":
            return self._is_unknown(node, value_type, in_sampled)
        if name not in _SAMPLED:
            raise _RejectError(f"system function {name} is not supported yet", node)
        if in_sampled:
            raise _RejectError("nested sampled-value functions are not supported yet", node)
        arguments = node.get("arguments")
        allowed = 2 if name == "$past" else 1
        if not isinstance(arguments, list) or not 1 <= len(arguments) <= allowed:
            detail = (
                "a gating or clocking argument" if name == "$past" else "more than one argument"
            )
            raise _RejectError(f"{name} with {detail} is not supported yet", node)
        operand = self._expr(_object(arguments[0], "argument"), in_sampled=True)
        depth = 1
        if len(arguments) == 2:
            depth = self._constant_int(_object(arguments[1], "argument"), "the $past depth")
            if depth < 1:
                raise _RejectError(f"$past depth {depth} must be at least 1", node)
        return Sampled(_SAMPLED[name], operand, value_type, depth)

    def _is_unknown(self, node: JsonObject, value_type: ValueType, in_sampled: bool) -> Expr:
        """``$isunknown(e)`` (§20.9) in the two-state model: the constant 0 (docs/semantics.md
        §8a). The operand is converted, so an unsupported construct in it is rejected, and
        checked as validation checks every expression before it is discarded: an operand
        that can be X from known values (a divisor that may be zero) is rejected, since that
        X is what ``$isunknown`` detects. An operand using ``$past`` is rejected: ``$past``
        is X at the first samples (§8), where the 4-state ``$isunknown`` is 1."""
        arguments = node.get("arguments")
        if not isinstance(arguments, list) or len(arguments) != 1:
            raise _RejectError("$isunknown takes one argument", node)
        if in_sampled:
            raise _default_known("$isunknown", "it is 1 for an X operand", node)
        operand = self._expr(_object(arguments[0], "argument"), in_sampled=in_sampled)
        problems = expression_problems(operand, inside_sampled=in_sampled)
        if problems:
            raise _RejectError(f"$isunknown of an unsupported operand: {problems[0]}", node)
        if SampledFunction.PAST in _sampled(operand):
            raise _RejectError(
                "$isunknown of an operand using $past is not supported: $past is X at the "
                "first samples (IEEE 1800 §16.9.3), which the two-state model does not "
                "represent, so the first sample could not be told from a later one",
                node,
            )
        if self._x_checks is XChecks.REJECT:
            raise _RejectError(
                "$isunknown is not supported with --x-checks=reject (in the two-state model it "
                "is the constant 0)",
                node,
            )
        self._unknown.append(_range(node))
        return Const(0, value_type)

    def _bit_count(
        self, node: JsonObject, name: str, value_type: ValueType, in_sampled: bool
    ) -> Expr:
        """``$countones(v)`` as the sum of ``v``'s bits (an ``int``); ``$onehot(v)`` as
        ``$countones(v) == 1``; ``$onehot0(v)`` as ``$countones(v) <= 1`` (§20.9)."""
        arguments = node.get("arguments")
        if not isinstance(arguments, list) or len(arguments) != 1:
            raise _RejectError(f"{name} takes one argument", node)
        if in_sampled:
            raise _default_known(name, "it counts X bits as 0", node)
        operand = self._expr(_object(arguments[0], "argument"), in_sampled=in_sampled)
        if SampledFunction.PAST in _sampled(operand):
            # IEEE counts X bits as 0, so e.g. $onehot0($past(v)) is known true at the
            # first sample, whereas the first-sample rule would force it false.
            raise _RejectError(f"{name} of an operand using $past is not supported yet", node)
        bits: list[Expr] = [
            Convert(BitSelect(operand, index, BIT), _INT) for index in range(operand.type.width)
        ]
        while len(bits) > 1:  # a balanced sum: depth log2(width)
            pairs = [
                Binary(BinaryOp.ADD, x, y, _INT)
                for x, y in zip(bits[::2], bits[1::2], strict=False)
            ]
            bits = pairs + ([bits[-1]] if len(bits) % 2 else [])
        (count,) = bits
        match name:
            case "$countones":
                return count if value_type == _INT else Convert(count, value_type)
            case "$onehot":
                return Binary(BinaryOp.EQUALITY, count, Const(1, _INT), value_type)
            case _:
                return Binary(BinaryOp.LESS_THAN_EQUAL, count, Const(1, _INT), value_type)

    def _bit_select(self, node: JsonObject, value_type: ValueType, in_sampled: bool) -> Expr:
        value_node = _child(node, "value")
        selector = _child(node, "selector")
        if not isinstance(selector.get("constant"), str):
            raise _RejectError("variable bit selects are not supported yet", selector)
        index = self._constant_int(selector, "bit index")
        position = _selectable(value_node).position(index)
        if position is None:
            raise _RejectError(f"bit index {index} is out of range", selector)
        return BitSelect(self._expr(value_node, in_sampled=in_sampled), position, value_type)

    def _range_select(self, node: JsonObject, value_type: ValueType, in_sampled: bool) -> Expr:
        if _string(node, "selectionKind") != "Simple":
            raise _RejectError("indexed part selects are not supported yet", node)
        value_node = _child(node, "value")
        packed = _selectable(value_node)
        left = packed.position(self._constant_int(_child(node, "left"), "part-select bound"))
        right = packed.position(self._constant_int(_child(node, "right"), "part-select bound"))
        if left is None or right is None:
            raise _RejectError("part select is out of range", node)
        return RangeSelect(
            self._expr(value_node, in_sampled=in_sampled),
            max(left, right),
            min(left, right),
            value_type,
        )

    def _constant_int(self, node: JsonObject, what: str) -> int:
        constant = node.get("constant")
        if not isinstance(constant, str):
            raise _RejectError(f"{what} must be a constant", node)
        self._check_folded_inside(node, False)
        value_type = _packed(node).type
        pattern = _constant(constant, value_type, node).value
        if value_type.signed and pattern >= 1 << (value_type.width - 1):
            pattern -= 1 << value_type.width
        return pattern
