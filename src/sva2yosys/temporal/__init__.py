"""Temporal compilation: IR directives to canonical monitor machines (docs/strategies.md).

``bounds`` analyses sequence durations, ``automaton`` turns sequences into letter
automata, ``compile`` builds a :class:`~sva2yosys.temporal.machine.Machine` for a
directive, and ``machine`` defines the normative per-sample step. The machine is plain
data: the code generator implements ``step`` in hardware, and the tests run it on traces
(``tests/support/simulate.py``) against the independent reference evaluator
(``tests/support/reference``).
"""
