"""Evaluate IR expressions, sequences and properties on a finite trace.

Conventions (``docs/semantics.md``):

- Sample ``k`` is the ``k``-th sampling event. A match of a sequence over samples
  ``s … k`` *ends* at ``k``; an empty match ends "before" its start and never satisfies
  a property.
- Sequence matching is Annex F tight satisfaction: ``r1 ##1 r2`` concatenates (either side
  may be empty), ``r1 ##0 r2`` fuses one sample (neither side empty), ``##n`` with
  ``n >= 1`` is ``##1`` followed by ``n - 1`` arbitrary samples, ``r[*0]`` is the empty
  match.
- **Alive / earliest failure.** With knowledge of samples ``0 … t``, a partial match is
  alive if a path is consistent with them while every Boolean condition on a later
  sample counts as satisfiable, constants (``1'b0``) included: a constant is evaluated
  at its own sample like any condition (IEEE 1800 Annex F). An obligation
  *fails* at the first sample ``t`` after which no non-empty match is possible, and
  *passes* at the first sample at which a non-empty match ends. Neither by the end of the
  trace: *pending*.
- **Attempts.** An attempt starts at every sample. For ``a |-> c`` each match of ``a``
  ending at ``e`` creates an obligation for ``c`` starting at ``e``. ``a |=> c`` is
  ``(a ##1 1) |-> c`` (§16.12.7): its obligation starts at ``e + 1`` (an antecedent
  admitting an empty match is not evaluated: the product rejects it).
  For a sequence property the attempt itself is the obligation.
- **disable iff.** An obligation is disabled when the condition holds at any sample
  from its attempt's start to its own decision (or to the end of the trace when it is
  pending); a disabled obligation reports nothing. An attempt is disabled at the first
  such sample in ``[start, decision]``. (This is what a monitor that clears its state on
  the condition observes: obligations decided before the condition rose are reported.)
- **Reports.** ``failures`` and ``successes`` are the samples at which some non-disabled
  obligation fails or passes; an antecedent's empty match never triggers.
- **Unbounded ranges (1C).** ``##[m:$]``, ``[*m:$]``, ``[->m:$]`` and ``[=m:$]`` are matched
  on a finite trace by exploring every end up to a horizon of ``known + 2 + sequence_states``
  half-open positions, where ``sequence_states`` is a structural bound of the automaton (a
  pumping argument, see there: if a match can complete after the known samples, one
  completes within that many samples). This is not a bound on the property:
  ``$`` stays unbounded, and an unbounded sequence that is alive is pending, never false.
  Goto ``b[->m:n]`` and nonconsecutive ``b[=m:n]`` repetition are matched from their
  definitions by counting the samples at which ``b`` holds (every sample of the match is
  either one at which ``b`` holds or one at which ``!b`` holds), not through the
  ``(!b[*0:$] ##1 b)[*n]`` expansion.
- **not** (§16.12.3): an obligation of ``not s`` passes where ``s`` can no longer match
  and fails at the first sample where ``s`` matches (the verdicts of ``s`` swapped; a
  pending or disabled obligation stays so). Its strength is the reverse of ``s``'s.
- **End of a simulation** (:func:`finalize`, apart from the online verdicts): an
  obligation still pending fails if its sequence is strong and does not if it is weak
  (§16.12.2; ``DEFAULT`` is weak in ``assert``/``assume``, strong in ``cover``). A BMC
  bound is not the end of a trace: monitors never finalize.

The evaluator is split by concern, and this module re-exports its names (importers keep
using ``support.reference.evaluate``):

- :mod:`support.reference.values`: values of expressions, ``holds``, ``EvaluationError``;
- :mod:`support.reference.matching`: sequence matching (``_Matcher``) and its state bounds;
- :mod:`support.reference.obligations`: the obligation model (``evaluate``, ``finalize``);
- :mod:`support.reference.engine`: the per-attempt engine (``attempt_results`` and friends).

It imports IR types only: never ``sva2yosys.temporal``, ``optimize`` or ``codegen``
(``tests/unit_tests/test_package_boundaries.py``).
"""

from __future__ import annotations

from support.reference.engine import (
    AttemptEvaluation,
    AttemptResult,
    Outcome,
    attempt_results,
    evaluate_attempts,
    finalize_attempts,
)
from support.reference.matching import (
    _Matcher,
    _propertysequence_states,
    match_ends,
    sequence_states,
)
from support.reference.obligations import (
    Attempt,
    Evaluation,
    Finalization,
    Obligation,
    Verdict,
    _nullable,
    evaluate,
    finalize,
)
from support.reference.values import EvaluationError, holds, value

__all__ = [
    "Attempt",
    "AttemptEvaluation",
    "AttemptResult",
    "Evaluation",
    "EvaluationError",
    "Finalization",
    "Obligation",
    "Outcome",
    "Verdict",
    "_Matcher",
    "_nullable",
    "_propertysequence_states",
    "attempt_results",
    "evaluate",
    "evaluate_attempts",
    "finalize",
    "finalize_attempts",
    "holds",
    "match_ends",
    "sequence_states",
    "value",
]
