# Coverage matrix: semantic dimensions × trace cases

Every dimension that the reference semantics must get right is listed with the cases that pin it down. A dimension with no case is a gap. Case names are `file::case` under `fixtures/`.

## Dimensions

| Dimension | Cases |
|---|---|
| Pass in the trigger sample | `implication::p01_pass_each_sample`, `fusion_empty::zero_delay_range` |
| Failure in the trigger sample | `implication::p01_fail_in_trigger_sample`, `chains::p06_fail_in_trigger_sample`, `fusion_empty::fusion_in_the_same_sample` |
| `\|=>` starts one sample later | `implication::p02_nonoverlapping_and_pending_at_end` |
| Earliest failure at the last chance | `delays::p04_delay5_earliest_failure_and_pending`, `delays::p05_fail_at_last_chance`, `chains::p06_window_missed` |
| Latest valid match | `delays::p05_latest_valid_match`, `chains::p06_latest_match` |
| A later path matches after an earlier one dies | `chains::p07_second_path_matches` |
| Failure when the last alive path dies | `chains::p07_fail_when_last_path_dies`, `overlap::strategies_example_3` |
| Overlapping attempts of different ages | `delays::p05_overlapping_attempts_of_different_ages`, `repetition::p09_overlapping`, `overlap::strategies_example_3` |
| Several antecedent endpoints from one start | `overlap::several_antecedent_endpoints_from_one_start` |
| Consecutive repetition: exact count, range, gap, too short | `repetition::*` |
| `##0` fusion | `fusion_empty::fusion_in_the_same_sample` |
| `##[0:N]` | `fusion_empty::zero_delay_range` |
| Empty repetition inside a sequence | `fusion_empty::empty_repetition_between_delays` |
| "Alive" rule: constant false on a future sample is evaluated at that sample | `fusion_empty::constant_false_on_a_future_sample_fails_at_that_sample`, `constants::*` |
| Constant-false antecedent, consequent or cover sequence (never held, or failing at its own sample) | `fusion_empty::constant_false_antecedent_never_triggers`, `fusion_empty::constant_false_consequent_fails_on_every_trigger`, `fusion_empty::constant_false_cover_is_never_reached` |
| "Alive" rule: no early detection of future contradictions | `fusion_empty::contradiction_on_a_future_sample_fails_when_it_arrives` |
| `$rose` antecedent | `sampled::p10_rose_antecedent` |
| First-sample X rules (`$rose`, `$fell`, `$stable`, `$changed`) | `sampled::rose_at_first_sample_uses_x_default`, `sampled::first_sample_fell_stable_changed`, `sampled::fell_at_first_sample_uses_x_default` |
| `$past` at the first sample: antecedent does not trigger | `sampled::past_antecedent_does_not_hold_at_the_first_sample` |
| Sequence `and` (later end), `intersect` (same end), `within` (containment), `throughout` (every sample) | `composition::*` |
| Sequence `or`: in the consequent, in the antecedent, failure only when both branches are dead | `or::*` |
| `$countones`, `$onehot`, `$onehot0` (lowered to sums of bits), also of `$past` | `bitcount::*` |
| `$past(e, n)`: X at the first `n` samples, then the value `n` samples back; mixed depths | `sampled::past_depth_two_antecedent`, `sampled::past_depth_two_consequent`, `sampled::past_mixed_depths` |
| `$past` at the first sample: consequent fails (as in IEEE) | `sampled::past_consequent_fails_at_the_first_sample` |
| `$past` evaluated later in attempt 0 | `sampled::past_used_later_in_attempt_0` |
| `$past` of a vector, zero-extended comparison | `sampled::past_of_a_vector` |
| Disable at launch | `disable::p11_disable_at_launch`, `disable::disable_masks_an_immediate_failure`, `disable::disable_masks_an_immediate_success`, `disable::disable_masks_a_cover_match` |
| Disable during progress | `disable::p11_disable_during_progress` |
| Disable at the would-be failure sample | `disable::p11_disable_at_the_would_be_failure` |
| Disable after a decision | `disable::p11_disable_after_the_failure`, `disable::p11_success_before_disable_is_reported` |
| New attempts after a disable | `disable::p11_attempts_after_a_disable`, `interactions::disable_then_immediate_trigger` |
| Disable keeps sampled-value history | `interactions::disable_keeps_past_history`, `interactions::disable_keeps_edge_history` |
| Acceptance discharges the whole obligation (longer paths dropped) | `interactions::acceptance_discharges_longer_paths` |
| Same-sample triggers from several antecedent starts | `interactions::same_sample_triggers_from_two_starts` |
| `\|=>` with a fused consequent | `interactions::nonoverlapping_with_fused_consequent` |
| Empty branch at the consequent start | `interactions::empty_branch_at_consequent_start` |
| Repetition of a concatenation / of a nullable sequence | `interactions::repetition_of_a_concatenation*`, `interactions::repetition_of_a_nullable_sequence` |
| Obligation pending at the end of the trace | `implication::p02_nonoverlapping_and_pending_at_end`, `delays::p04_delay5_earliest_failure_and_pending`, `sampled::p10_rose_antecedent`, `cover_pending::open_obligation_at_the_end` |
| Antecedent still open at the end (no obligation yet) | `cover_pending::antecedent_still_open_at_the_end` |
| Cover: one report per sample | `cover_pending::cover_sequence_ends_once_per_sample` |
| Cover: first match of each attempt only | `interactions::cover_counts_the_first_match_of_each_attempt` |
| Cover: non-vacuous successes only | `cover_pending::cover_implication_counts_non_vacuous_successes_only` |
| Vacuous attempts report nothing | every implication case with samples where `a` is low; per-attempt verdicts in `tests/unit_tests/test_reference_evaluate.py` |
| Unbounded delay and repetition: decisions, weak versus strong pending, antecedent never decided | `unbounded::delay_unbounded_consequent_decides`, `unbounded::weak_unbounded_consequent_pending_finalizes_true`, `unbounded::strong_unbounded_consequent_pending_finalizes_false`, `unbounded::antecedent_unbounded_stays_pending_even_when_consequents_pass`, `unbounded::repeat_*` |
| Goto and nonconsecutive repetition | `unbounded::goto_*`, `unbounded::nonconsecutive_*`, `unbounded::law_*` |
| Unbounded `always`, `s_eventually`; weak and strong operands at the end | `liveness::always_*`, `liveness::s_eventually_*`, `liveness::law_*` |
| The `until` family | `until::*` |
| Weak `until` / `until_with` in the machines: `q` at the first sample, `p` false before `q`, both false, `until_with` with `q` and not `p`, overlapping attempts, `disable iff` at the deciding sample, `$past` in each operand position, constant operands | `until_machine::*` |
| Infinite (lasso) traces: pending for ever is weak TRUE / strong FALSE, never a guessed bound | `lasso::past_depth_*`, `lasso::s_eventually_b_*`, `lasso::until_never_b_is_true_when_weak`, `lasso::s_until_never_b_is_false_when_strong`, `lasso::weak_unbounded_consequent_never_fails`, `lasso::cover_defaults_to_strong`, `lasso::repetition_longer_than_the_written_trace`, `lasso::goto_count_spans_several_periods` |
| Lasso: sampled-value warm-up, attempts started in the loop, windows over all offsets | `lasso::past_depth_matches_in_a_one_sample_loop`, `lasso::rose_across_the_loop`, `lasso::always_*`, `lasso::always_eventually_*`, `lasso::overlapping_triggers_with_a_rare_b` |
| Lasso: triggers, `not`, `disable iff` | `lasso::several_triggers_one_never_satisfied`, `lasso::unbounded_antecedent_keeps_a_true_attempt_open`, `lasso::not_*`, `lasso::disable_*` |

## The eleven specification properties

| Property | Cases |
|---|---|
| p01 `a \|-> b` | `implication::p01_*` |
| p02 `a \|=> b` | `implication::p02_*` |
| p03 `a \|-> ##1 b` | `delays::p03_delay1` |
| p04 `a \|-> ##5 b` | `delays::p04_*` |
| p05 `a \|-> ##[1:5] b` | `delays::p05_*` |
| p06 `a \|-> b ##[1:3] c` | `chains::p06_*` |
| p07 `a \|-> b ##[1:3] c ##1 d` | `chains::p07_*` |
| p08 `a \|-> b[*3]` | `repetition::p08_*` |
| p09 `a \|-> b[*2:5]` | `repetition::p09_*` |
| p10 `$rose(a) \|-> ##[1:3] b` | `sampled::p10_rose_antecedent` |
| p11 `disable iff (!rst_n) a \|-> ##3 b` | `disable::p11_*` |

## Not covered by trace tables (by design)

- **Asynchronous disable pulses between clock edges.** A trace has one value per sample, so this can't be expressed. It is covered by the Yosys contract (`tests/functional_tests/yosys_contract`, point 6).
- **A property sequence that admits an empty match.** Slang rejects it (`SeqEmptyMatch`).
- **Conditions rejected by the first-sample check** (e.g. `$past(a) || b`). They never reach evaluation; see `tests/unit_tests/test_first_sample.py`.
