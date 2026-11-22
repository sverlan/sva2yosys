"""Sampling events of concurrent assertions (IEEE 1800 §14.12, §16.16)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sva2yosys.ir.expr import Expr, Signal


class Edge(Enum):
    """Clock edge of a sampling event."""

    POSEDGE = "posedge"
    NEGEDGE = "negedge"


@dataclass(frozen=True)
class Clock:
    """``@(edge signal iff enable)``; ``enable`` ``None`` means no ``iff`` gate.

    Only ``POSEDGE`` without ``enable`` is supported.
    """

    signal: Signal
    edge: Edge
    enable: Expr | None = None
