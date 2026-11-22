"""Concurrent verification directives (IEEE 1800 §16.14)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from sva2yosys.ir.clock import Clock
from sva2yosys.ir.expr import Expr, ValueType
from sva2yosys.ir.property import Property
from sva2yosys.ir.source import SourceRange


class DirectiveKind(Enum):
    """The verification directive."""

    ASSERT = "assert"
    ASSUME = "assume"
    COVER = "cover"


@dataclass(frozen=True)
class ParameterValue:
    """The value of a parameter or genvar in one elaborated instance.

    Attributes:
        literal: The value as a SystemVerilog integer literal.
        implicit_type: The parameter's width and signedness when its declaration fixes
            neither (``parameter P = 1``: the type comes from the value, so another
            elaboration may give another type with an equal value); ``None`` when the
            declaration fixes the type (``parameter int N``, a packed range, a genvar).
    """

    literal: str
    implicit_type: ValueType | None = None


@dataclass(frozen=True)
class Directive:
    """One concurrent assertion of one elaborated instance.

    Attributes:
        kind: assert, assume or cover.
        name: The statement label, or ``""``.
        scope: Hierarchical path of the enclosing scope.
        clock: The sampling event (explicit, or from ``default clocking``).
        disable: The ``disable iff`` condition (explicit, else the scope default), or
            ``None``.
        property: The property, without clocking and ``disable iff``.
        source: Where the directive is written.
        parameters: The parameters, type parameters and genvars visible at the
            directive (declared before it), as ``(name, value)`` pairs sorted by name; the
            value is ``None`` when it cannot be compared (a non-integral value, a type
            parameter, a declaration whose order relative to the directive is unknown).
            They key the monitor of each elaborated instance.
        dependencies: The names, among ``parameters``, that the monitor can depend on:
            the parameters, type parameters and genvars referenced by the property, the
            ``disable iff`` condition, the clock and the enabling condition, or by the declared
            types of the signals it reads. ``None`` means unknown: every visible parameter counts.
            Only these key the monitor of an elaborated instance; the others cannot change it.
    """

    kind: DirectiveKind
    name: str
    scope: str
    clock: Clock
    disable: Expr | None
    property: Property
    source: SourceRange
    parameters: tuple[tuple[str, ParameterValue | None], ...] = ()
    dependencies: frozenset[str] | None = None


PARAMETER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
"""The parameter names a monitor selection can write (a plain identifier; not an escaped one)."""


def uncomparable_parameters(
    parameters: tuple[tuple[str, ParameterValue | None], ...],
    dependencies: frozenset[str] | None = None,
) -> tuple[str, ...]:
    """The names, sorted, of the parameters of ``parameters`` that cannot key the monitor of an
    elaborated instance: a value that is not an integer (``None``: a ``$``, string, real or
    type parameter, an interface port) or a name that is not a plain identifier. The monitor
    of each elaboration is selected by comparing integral parameters, so a directive that has
    one in view cannot be lowered, whichever way it is processed (check and generation use
    this one rule). Only the names in ``dependencies`` count when it is given (a parameter
    the monitor cannot depend on does not key it); ``None`` counts every name."""
    return tuple(
        sorted(
            {
                name
                for name, value in parameters
                if (dependencies is None or name in dependencies)
                and (value is None or PARAMETER_NAME.fullmatch(name) is None)
            }
        )
    )


def uncomparable_message(names: tuple[str, ...]) -> str:
    """The located-error text for ``names`` (the result of :func:`uncomparable_parameters`)."""
    return (
        f"{', '.join(names)} is visible at this assertion but cannot be compared "
        "(a string, real or type parameter, an interface port, an escaped name); the "
        "monitor of each elaboration is selected by comparing integral parameters, so "
        "this is not supported"
    )
