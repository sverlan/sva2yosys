"""Frozen byte contract of support.dense_slots (post-6a, before compaction)."""

import hashlib

import pytest

from support.dense_slots import module
from support.monitor_text import without_wire_comments
from sva2yosys.ir.clock import Clock, Edge
from sva2yosys.ir.directive import Directive, DirectiveKind
from sva2yosys.ir.expr import BIT, Signal
from sva2yosys.ir.property import SequenceProperty
from sva2yosys.ir.sequence import Bool
from sva2yosys.ir.source import SourceRange
from sva2yosys.temporal.automaton import Automaton, Letter
from sva2yosys.temporal.machine import Machine


@pytest.mark.parametrize(
    ("automaton", "slots", "digest"),
    [
        (
            Automaton((Letter((0,), (), True),), (0,), False),
            0,
            "8785d71836c737b8cc52d8e0bf330ad59e09cd854b70efd22921813aa95ff5f4",
        ),
        (
            Automaton((Letter((), (1,), False), Letter((0,), (), True)), (0,), False),
            1,
            "15b6d2d5c34e67c6c9a2604617f36fba1c021b2628b9f52f8bca447acaa21385",
        ),
    ],
)
def test_dense_oracle_keeps_its_frozen_bytes(automaton: Automaton, slots: int, digest: str) -> None:
    # Snapshot origin: frozen dense emission from an earlier commit; digests re-recorded when the
    # monitor header gained the comment lines // as written:, // clock:, // disable iff:.
    signal = Signal("m.a", BIT)
    directive = Directive(
        DirectiveKind.ASSERT,
        "p",
        "m",
        Clock(Signal("m.clk", BIT), Edge.POSEDGE),
        None,
        SequenceProperty(Bool(signal)),
        SourceRange("unit.sv", 1, 1, 1, 2),
    )
    machine = Machine(directive, (signal,), (0,), None, None, automaton, slots)
    text = without_wire_comments(module(machine, "mon"))
    assert hashlib.sha256(text.encode()).hexdigest() == digest
