# Semantics

This document states the rules that sva2yosys implements. Every rule is backed by tests: the reference evaluator and trace fixtures (`tests/functional_tests/…`) and the Yosys contract (`tests/functional_tests/yosys_contract/CONTRACT.md`). Clause numbers refer to IEEE 1800-2017; the formal semantics are in its Annex F. For how these rules are mapped into SystemVerilog monitors, see [`strategies.md`](strategies.md).

## In short

A concurrent assertion says what must hold over a sequence of clock ticks. Take `assert property (@(posedge clk) req |-> ##[1:3] ack)`: whenever `req` is high, `ack` must be high one to three ticks later. This page defines exactly what that means, so that the generated monitors and the tools that check them agree with IEEE 1800 on every trace. The main ideas:

- **Ticks, not time.** A property is looked at only at the ticks of its clock, using the values just before the edge. In the formal flow each solver step is one tick ([§1](#1-sampling), [§4](#4-timing-table-normative)).
- **Attempts.** A new attempt of the property starts at every tick. Each attempt ends as *passed*, *failed* or *disabled*, or is still *pending* ([§9.2](#92-attempt-results)).
- **Failures are reported at once.** An attempt fails at the first tick at which no continuation of the trace can save it. A monitor reports it at that same tick, never later ([§3](#3-earliest-failure)).
- **Implication and vacuity.** `a |-> c` only asks something when `a` matches. If `a` never matches, the assertion holds vacuously, and a `cover` does not count it ([§2](#2-matches-triggers-obligations), [§5](#5-vacuity)).
- **Weak and strong.** By default an assertion can't fail just because the trace ends while it is still waiting, so `a |-> ##[1:$] b` can never fail. If `b` must really happen, write `strong(…)` or `s_eventually`: that is a liveness check, run as a separate SBY task ([§9.1](#91-rules), [§9.6](#96-unbounded-sequences-and-properties)).
- **No guessed bounds.** `$` is never replaced by a finite limit.
- **`disable iff` and history.** A true `disable iff` aborts the open attempts, while the history behind `$past` and `$rose` keeps running ([§7](#7-disable-iff), [§8](#8-sampled-value-functions)).
- **Covers** ask whether a non-vacuous success is reachable; each attempt is counted once, at its first match ([§6](#6-directives)).
- **Two-state values.** The model has no X or Z, so `$isunknown` is the constant 0, and anything that could produce an X from known values is rejected ([§1](#1-sampling), [§8a](#8a-isunknown-and-x-checks)).
- **Exact or rejected.** If the exact meaning of a construct can't be guaranteed, sva2yosys rejects it with a located message ([§10](#10-unsupported-input)).

The rules below are what the tests check the monitors against: hand-derived traces and an independent reference evaluator ([`validation.md`](validation.md)).

## Contents

- [1. Sampling](#1-sampling): sampled values, operand types, `inside`, two-state values;
- [2. Matches, triggers, obligations](#2-matches-triggers-obligations): sequences, composition, implication;
- [3. Earliest failure](#3-earliest-failure): at which sample a failure is reported, and what is never decided ahead of its sample;
- [4. Timing table](#4-timing-table-normative): the normative per-sample timing of a monitor;
- [5. Vacuity](#5-vacuity);
- [6. Directives](#6-directives): `assert`, `assume`, `cover`, `restrict`, procedural and `initial` assertions;
- [7. `disable iff`](#7-disable-iff);
- [8. Sampled-value functions](#8-sampled-value-functions) and [8a. `$isunknown` and X-checks](#8a-isunknown-and-x-checks);
- [9. Weak and strong, property operators](#9-weak-and-strong-property-operators): attempts and outcomes, the bounded operators, lowerings and the cover rule, and unbounded sequences with liveness and fairness;
- [10. Unsupported input](#10-unsupported-input).

## 1. Sampling

- A property is evaluated at the **sampling events** of its clock. The only accepted clock is `@(posedge clk)`, either explicit or from `default clocking` (§14.12, §16.16).
- Values used in a property are the **sampled values** at the event: the values just before the clock edge (§16.5.1).
- Sample `k` denotes the `k`-th sampling event. The first event is sample 0.
- **Integral operand types.** An integral value (`int`, vector, enum, packed struct or multi-dimensional packed array) is its bits, with the width and signedness of its canonical type (§6.11, §7.2, §7.4, §6.19); the operators of §11.4 apply to those bits. Whole values only: selects into multi-dimensional or struct values and member access are rejected.
- **Conversions (§11.8.2, §11.8.3, §6.24.1).** An operand that Slang converts to the type of its context (an operand of an arithmetic, bitwise or relational operator, or a branch of `?:`; Slang's kind `Propagated`) is extended by the sign of the context's type: a signed operand in an unsigned context is zero-extended (among the context-determined operands the context is unsigned as soon as one is), in a signed context it is sign-extended. Self-determined operands are not extended to the context: shift counts, the operands of a concatenation, reduction operands and the selector of `?:` (§11.6.1, §11.8.1). A cast (`8'(s)`, `signed'(u)`, kind `Explicit`) and the conversion of an actual argument to a typed formal (also `Explicit`) are assignments: the value is extended by the signedness of its source. Slang's `Implicit` kind was not observed in any accepted expression that was probed; the converter extends by the source's sign for every kind other than `Propagated`, and a `Conversion` without `sva2yosys_conversionKind` is a located internal export error.
- **Set membership `inside` (§11.4.13).** `e inside {i1, ..., [lo:hi], ...}` is 1 when some item matches: a value item when `e ==? item`, a range when `lo <= e` and `e <= hi` (a range with `lo > hi` is empty and matches nothing).
  - **Only uniform lists are accepted:** the left operand, every value item and every range bound must have one width and one signedness, each sized by itself. §11.4.13 does not say whether each comparison is sized on its own or all share one context (Table 11-21 has no row for `inside`), and tools read it differently ([`validation.md`](validation.md#5-interpretations-and-known-deviations), I11); for a uniform list every reading gives the same comparisons. Any other list is rejected with a located message, e.g. `v4 inside {1}` (`1` is a 32-bit signed integer: write `4'd1`).
  - **Wildcards.** In the two-state model the X/Z/`?` bits of a constant item are wildcards (the right operand of `==?`, §11.4.6), not unknown values, so such an item compares only its known bits; this is the only place where a constant with x/z bits is accepted. The result is never X: the "X when nothing matches and a comparison is X" case needs an X operand.
  - **`$` bounds.** A bound `$` is the lowest or highest value of the type of the left operand, so its comparison always holds (`[$:hi]` is `e <= hi`, `[lo:$]` is `lo <= e`); it is accepted when the left operand is not an arithmetic expression and the comparison has its signedness, else rejected, and it is rejected inside a sampled-value function.
  - **Conversions.** The operands follow the conversion rules of the bullet above.
  - **Sampled-value functions.** `inside` is rejected inside a sampled-value function and with a sampled-value function in an item: an X item is all wildcards (§11.4.6), so `v inside {$past(w)}` holds at the first sample, and the two-state `v == $past(w)` does not model that.
  - **Unbased unsized literals.** An unbased unsized literal (`'0`, `'1`) is one bit by itself, so it is accepted only in a list of 1-bit operands; `'x`/`'z` are rejected.
- **Two-state values.** Generated monitors and the reference evaluator compute two-state values, so no condition may produce X from known operands. The one operator that can is division (`/`, `%`) by zero (§11.4.2): IEEE's X makes the assertion fail, while a monitor would compute a defined value and could hide the failure. A divisor must therefore be a non-zero constant; anything else is rejected. (`$past` at the first sample is handled separately, §8.)

## 2. Matches, triggers, obligations

- A sequence **matches** over an interval of samples `[s, e]`. For `##N`, `##[N:M]`, `[*N]` and `[*N:M]`, see §16.7 and §16.9.2.
- `##0` **fuses** its operands: the right operand starts at the sample at which the left one ends. It is not a one-cycle step.
- **Composition (§16.9.5 to §16.9.10).** For operands starting at the same sample `s`:
  - `s1 or s2` matches over `[s, e]` when either operand does;
  - `s1 and s2` matches over `[s, max(e1, e2)]` for every pair of matches `[s, e1]` of `s1` and `[s, e2]` of `s2` (the operand that ends first waits);
  - `s1 intersect s2` matches over `[s, e]` when both operands match over `[s, e]`;
  - `s1 within s2` matches over `[s, e]` when `s2` does, and `s1` matches over some `[s', e']` with `s <= s' <= e' <= e` (also `s' = s` and `e' = e`);
  - `c throughout s` matches over `[s, e]` when `s` does and `c` holds at every sample of `[s, e]`; its empty match is the operand's.
  - **Restriction.** An operand of `and`, `intersect` or `within` that can match empty is rejected (IEEE's empty-match rules for them are not implemented). The operand of `throughout` may be nullable.
- `a |-> c`: every match of `a` ending at `e` **triggers** an obligation that `c` matches starting at `e`. `a |=> c` is `(a ##1 1) |-> c` (§16.12.7): the consequent starts at `e + 1`, and so is decided no earlier than there, also when it cannot match at all (`a |=> 1'b0` fails at `e + 1`; a `disable iff` true at `e + 1` disables the attempt). `|=>` with an antecedent that admits an empty match is rejected: `(a ##1 1) |-> c` would let the empty match trigger, which IEEE excludes, and `a |-> ##1 c` would decide an impossible consequent a tick early.
- **Cover of an implication.** An implication attempt succeeds only when the consequents of *all* its antecedent matches succeed (and the antecedent can match no more). The monitors' `success` output reports each successful obligation, which is the attempt's success only when an attempt has at most one obligation: so the canonical machine accepts a `cover` of an implication only when every match of its antecedent has the same length. The per-attempt machine handles the other shapes ([§9.4](#94-construction), [§9.5](#95-lowerings-machine-selection-and-the-cover-rule)).
- **Existential contexts:**
  - an antecedent: does some match end here?
  - a cover: does some match exist?
  - sequence matching itself: some path.
- **Universal context:** assertion satisfaction. *Every* triggered obligation must succeed on its own.
- Obligations triggered at the same sample are identical, so they are deduplicated. Obligations triggered at different samples are **never merged** in the canonical machine ([`strategies.md`](strategies.md#example-3-why-obligations-of-different-ages-are-not-merged), Example 3).

## 3. Earliest failure

- An obligation **fails at the first sample at which its frontier becomes empty** without the consequent having matched. A frontier is the set of live partial matches.
- The compiler prunes only **structurally** impossible letters: a letter is dropped only when no continuation of it can reach a match whatever the samples are, a fact of the sequence's shape and lengths alone. Example: the operands of an `intersect` whose fixed lengths never meet (`(a ##1 b) intersect c` has no match at all, whatever `a`, `b` and `c` are). Nothing that depends on the *value* of a condition, constants included, is decided ahead of its sample. Whether a continuation is reachable *in the design* is left to the formal engine.
- **"Alive" is defined operationally.** After sample `t`, a partial match is alive if some path through the sequence is consistent with the samples `0…t`. Samples after `t` are unknown, and **every Boolean condition evaluated on an unknown sample counts as satisfiable, `1'b0` included.**
- **Constant conditions are ordinary letters.**
  - A *constant* condition is a sequence Boolean that is an IR constant (`Const`): every expression that Slang can evaluate as a constant in the assertion's scope (literals, parameters, `-1`, `$bits`, …: the exporter asks Slang's constant evaluation) becomes a `Const`, and nothing else does. Anything else is non-constant, including sampled-value functions of constants such as `$rose(1'b0)`.
  - A constant-false condition is a letter whose condition is false: it is evaluated at its own sample, like every other condition, and refutes the partial matches that reach it there, and not before.
  - Reason (IEEE 1800 Annex F): a failure of a weak obligation is a *bad prefix*, a finite prefix that no continuation can repair; an unknown future sample satisfies every Boolean, so a path is refuted only when a *known* sample falsifies one of its letters. Deciding it ahead of time would report a failure at an earlier sample than IEEE does, and the attempt could be disabled (§7) or the trace constrained (`assume`) differently in between.
  - Example: `disable iff (d) a |-> ##1 1'b0` fails at the sample after `a`, not at `a`; if `d` holds there the attempt is disabled and nothing fails. `a |-> 1'b0` fails at `a`'s own sample (the constant is evaluated where it is tested), `a |-> ##[1:$] 1'b0` never fails (the filler loop of the unbounded delay stays alive for ever), and `a |=> 1'b0` fails one sample after `a` as before.
  - A constant-true condition is satisfied at every sample, known or unknown, so a letter whose only condition is `1'b1` holds unconditionally: dropping that guard in the automaton changes no decision sample (an equivalent form, not a pruning).
  - The same rule applies to the constants a goto or nonconsecutive repetition introduces (`1'b1[->2]`'s `!1'b1` is a false letter evaluated at its sample), to the conditions of `throughout`, to `if` and `until` conditions (evaluated at known samples only) and to antecedents (a constant-false antecedent letter is never held, so the antecedent never matches there).
  - The compiler doesn't reason about contradictions between conditions on the same future sample. For example, in `b ##0 !b` the fused sample is not considered impossible in advance; the obligation fails when that sample arrives.
  - The reference evaluator and the generated monitors implement this same rule, independently, so their failure samples agree exactly.
- Generated monitors report the failure **in the same sample**: no latency is added ([`strategies.md`](strategies.md)).

## 4. Timing table (normative)

For every sample `k` the generated monitor has this structure:

| Column | Kind | Meaning |
|---|---|---|
| `state[k]` | registered | frontiers of open obligations (per-age slots), sampled-value history; initial value at `k = 0` |
| `in[k]` | sampled inputs | the design signals sampled at event `k` |
| `next[k]` | combinational | frontiers after consuming `in[k]`, including obligations triggered at `k` |
| `match[k]`, `fail[k]` | combinational | a consequent matched at `k`; an obligation lost its frontier at `k` |
| `state[k+1]` | registered | `next[k]` (with disabled obligations removed) |

- `a |-> b`: the obligation triggered at `k` is checked with `in[k]`, so `fail[k] = a[k] && !b[k]`.
- `a |=> b`: the obligation triggered at `k` is checked with `in[k+1]`.
- The check is placed according to SBY's clock mode (Yosys contract, point 7):
  - **`multiclock off`**: `always @* assert (!fail);`. SBY reports the failure **at step `k`**.
  - **`multiclock on`**: `always @(posedge clk) assert (!fail);`, evaluated only at sampling edges.
- **sva2yosys generates the `multiclock off` form only, and its monitors require `multiclock off`.** There, every formal step is a sampling event of the (single) clock, so `fail`/`success` are only ever evaluated on sampled values. Under `multiclock on` the same monitor would be checked at every global step, including steps between clock edges. That gives spurious assertion failures and covers and, for `assume`, constraints on behaviour that the property does not sample, which is unsound. Every generated monitor states this requirement in its header comment.

## 5. Vacuity

- An implication whose antecedent never matches **succeeds vacuously**.
- A **non-vacuous success** is a sample at which a triggered obligation matches.
- The machine tracks both, so `cover`, `.triggered` and non-vacuity diagnostics can be built on it.

## 6. Directives

- `assert property (p)`: `assert (!fail)`. Every failure of `p` is a violation.
- `assume property (p)`: `assume (!fail)`. Traces on which `p` fails are excluded from the sample of the failure on. Because failures are reported at the earliest sample, the assumption constrains as early as the semantics allow.
- `cover property (p)` (§16.14.3):
  - for a sequence, each attempt is covered once, at the sample where its **first** match ends (later matches of the same attempt do not count); attempts covered at the same sample give one report. §16.14.3 counts successful attempts individually, which matters for simulation statistics; a formal `cover` (SBY cover mode) asks only whether some trace reaches a success, so the monitor's per-sample `success` bit is exact for it, and the number of attempts succeeding at one sample is not represented (true of every machine);
  - for an implication, covered only by a **non-vacuous** success;
  - an attempt cut off by `disable iff` never counts;
  - an unbounded part needs no liveness: a cover is a match on known samples (reachability). It is accepted for a **strong** unbounded sequence and for an implication with a single-length antecedent into one (§9.6, covers of unbounded sequences), and the first match of an attempt is its success there too; an explicit `weak(...)` or a weak `until` in a cover is rejected (§9.6, rejected covers).
- `restrict property` (16.14.4) is lowered as `assume property`, with the same supported properties and restrictions. IEEE gives it no action block and no simulation checking; the monitor contains an ordinary assumption, so it is a formal constraint only. A simulator that evaluates the rewritten output would check it.
- **Procedural concurrent assertions (§16.14.6).** A directive written in `always @(posedge clk)` or `always_ff @(posedge clk)` is accepted when:
  - its clock, written or inferred by Slang from the block's event control (§16.14.6.1), is that same event; the event is a single edge of a 1-bit signal without `iff`;
  - the path from the block to it goes only through `begin … end`, `if`/`else` (without `matches` or `&&&`) and plain `case` (not `casez`, `casex` or `case inside`). Loops, `fork`, timing controls and any other statement on the path are rejected;
  - every statement of the block falls through in zero time: assignments without task calls (functions are fine) or intra-assignment timing, declarations, null statements, concurrent assertions, and sequential blocks, `if` and `case` of those. A `disable`, a delay or wait, a loop, a task or system-task call anywhere in the block could skip or delay the assertion, or make the block miss clock edges, so its assertions are rejected;
  - the block is not the unbraced body of a generate construct (`for (…) always …`), where the monitor instance could not be placed in the same scope.

  Its **enabling condition** is the conjunction of the conditions on that path: `if (c)` gives `c`, `else` gives `!c`; a `case` item gives "the case expression equals one of its expressions, and none of an earlier item's" (two-state, so `===` is `==`); `default` gives "none of the items". When the condition is false the procedural code does not reach the directive, so there is no attempt at all: no failure, no success, not even a vacuous one. The directive is therefore lowered to the module-level `@(clk) en |-> p`, and an implication `a |-> b` to `(en ##0 a) |-> b` (same for `|=>`); the observable results are those of the procedural directive for `assert`, `assume` and `cover`: the same failures and the same non-vacuous successes (where `en` is false the lowered implication succeeds vacuously, which no directive observes). Its `disable iff` (explicit or the scope's default) is unchanged.
- **`initial` procedures (IEEE 1800-2017 §16.14.6).** An `initial` procedure runs once, at time 0; a concurrent assertion in it is queued then, matures in the Observed region of that time step, and starts **one evaluation attempt**: at that time step if its leading clock ticks there, otherwise at the next tick of its clock. In the synchronous formal model this is the **first tick** of the assertion's clock: the tick at which `$past` and the edge functions have no history yet (§8). The directive is lowered like any procedural one, with the enabling condition `first` (the IR node `FirstSample`): `@(clk) first |-> p`, and `(first ##0 a) |-> b` for an implication. A **path condition** (an `if`, `else` or `case` around the assertion) is **rejected** in an `initial` procedure: IEEE evaluates it when the procedure runs, at time 0 (§16.14.6.2), before the assertion's first clock tick, and a value that is stable at the tick says nothing about its value at time 0 (`initial if (en) assert property (@(posedge clk) 1'b0)` fails when `en` is high at time 0, even if it is low at the first tick). The message says to move the condition into the property (`assert property (@(posedge clk) en |-> p)`) after checking that it means the same. **Limitation:** since the lowered form is a property `if` on `first`, a `cover` of an unbounded sequence in an `initial` procedure (`initial cover property (@(posedge clk) a ##[1:$] b)`) is rejected with "unbounded ranges ($) are not supported yet", although the same cover is accepted at module level. So:
  - `assert`: only that attempt can fail. `x` counting `0, 1, 2, …` from 0: `initial assert property (@(posedge clk) always [0:9] x < 10)` holds (one attempt, ticks 0 to 9), while the same module-level assertion fails (the attempt of tick 1 sees `x = 10` at tick 10).
  - `cover`: reports the first non-vacuous match of that attempt, if any; there is no other attempt to cover.
  - `assume`: constrains the traces only through that attempt.
  - `disable iff`: a condition true at any tick of that attempt disables it (§7), and then the directive has no attempt left; it does not reset `first`, which stays 0 after the first tick.
  - Clock (§16.16): explicit in the assertion; inferred from the procedure when it has a single event control that meets §16.14.6 (`initial @(posedge clk) …`); else `default clocking`, "as if the assertion were instantiated immediately before the procedure". An assertion with none of these has no clock and is rejected.

  **Current values.** IEEE evaluates the enabling condition with the values *current* when the block runs (§16.14.6.2), whereas the lowered property samples it. They coincide unless the condition reads a value that changes at that clock edge before the block reads it. sva2yosys accepts a condition only if every signal it reads is *stable at the edge*, a deliberately narrow rule: a constant; a variable written only by nonblocking assignments in blocks triggered by exactly the assertion's clock edge, a single event (plus a declaration initializer): an update scheduled by another event of the same time slot, an asynchronous reset for instance, may precede the block; a top-level input other than the clock of the block; or an input port connected, through any number of instances, by plain names (no logic, no select) to one of these. Everything else is rejected: a blocking assignment (or `++`, `+=` …) in a clocked block, combinational logic (`always_comb`, `always @*`, an explicit level-sensitive list, a continuous assignment), output ports, subroutines, `initial` blocks, the clock itself, and an unconnected input port. Sampled-value functions in the condition are rejected too. (A top-level input is free in formal verification; in simulation it is whatever the testbench drives.)
- **What each directive consumes.** An `assert` or `assume` consumes the monitor's `fail` output up to and including its first failure: a trace that fails once fails, whatever follows. A `cover` consumes `success`. An optimised monitor form only has to be exact on what its directive consumes. The `window` form is exact up to and including the first failure, so it is used for `assert`/`assume` only ([`docs/strategies.md`](strategies.md#when-sva2yosys-uses-which), rule 3). The default direct form is exact at every sample.

## 7. `disable iff`

- `disable iff (d)`, explicit or from `default disable iff` (§16.15), is evaluated **asynchronously on current values**, not sampled values.
- **When `d` may change at the clock edge, it is rejected.** IEEE: an attempt is disabled if `d` is true at any time from the start of the attempt in the Observed region, inclusive, to its end, inclusive (§16.12). The Observed region follows the NBA region, so a register updated at edge `k` already has its new value when the attempt of tick `k` is checked, whereas the monitors use the values of the tick (before the edge). Both directions matter: a reset register rising at edge `k` disables that attempt (the monitors would report its failure: too strict for `assert`, too constraining for `assume`), and one falling at edge `k` enables it (the monitors would treat it as disabled and could hide a failure). The two agree only for signals that do not change at the edge.
  - So `d` may read only constants, top-level inputs (also through plain port connections), input ports connected to an expression without calls of such signals (a port connection is a continuous assignment, §23.3.3: `.req_chk_i(1'b1)`, `.rst_ni(rst_a && rst_b)`), and nets without delay whose only driver is one zero-delay continuous assignment, without calls, of such signals (`wire rst = !rst_n`).
  - Each of several identical instances is judged by its own port connections (Slang analyses the drivers of only one of them; the others take its drivers, read in their own instance). An input port that the module also drives itself is not followed to its connection.
  - Anything else, a register in particular, is rejected with a located message.
  - (Top-level inputs are exact in the synchronous `multiclock off` model, where an input changes at the steps; a simulation testbench could change one within a time slot.)
- While `d` is true:
  - every open obligation is **aborted**: its slots are cleared;
  - no new obligation is triggered;
  - no failure or success is reported.
- An aborted obligation never fails later.
- **Implementation:** an asynchronous clear of the monitor's **temporal state** on `d` (`always @(posedge clk or posedge dis)`): antecedent frontier and obligation slots. The **sampled-value history** (`$past`/edge-function registers and the first-sample `valid` bit) is **not** cleared: it keeps sampling on the property's clock, so the first sample after a disabled interval sees the true previous value. Precisely:
  - In `multiclock on`, a pulse of `d` strictly between two clock edges aborts live obligations; with a synchronous clear it would not (Yosys contract, point 6).
  - In `multiclock off`, signals cannot change between steps, so the asynchronous clear acts at the step in which `d` is true.
  - The guarantee extends to the granularity of SBY's steps; pulses shorter than a formal step don't exist in the model.

## 8. Sampled-value functions

- Supported: `$past(e)`, `$past(e, n)` (constant `n >= 1`), `$rose(e)`, `$fell(e)`, `$stable(e)` and `$changed(e)` (§16.9.3). They are evaluated relative to the **property's clock**, through dedicated history registers. Yosys's own `$past` is not used, because it refers to the global formal step.
- **First sample, edge functions (IEEE).** At the first sample of the property's clock there is no previous sample. `$rose`, `$fell`, `$stable` and `$changed` compare against the default sampled value X (§16.9.3), which gives deterministic results:
  - `$rose(e)` = LSB of `e` is 1;
  - `$fell(e)` = LSB of `e` is 0;
  - `$changed(e)` = 1;
  - `$stable(e)` = 0.
- **First sample, `$past` (IEEE, restricted).** IEEE makes `$past(e)` X at the first sample, and `$past(e, n)` X at the first `n` samples. A condition holds only if its value is known and non-zero.
  - With depth `n`, the rule below applies at each of the first `n` samples `k`. The check treats as X exactly the `$past` terms whose depth exceeds `k`, and requires that none of those samples can make the condition known true.
  - A condition does not hold during the first `d` samples, `d` being its largest depth (`passes.first_sample.gated_samples`). The monitor counts samples up to `d`; a 1-bit `valid` flag suffices when `d = 1`.
  - **Bit counts.** `$countones`, `$onehot` and `$onehot0` count X bits as 0 (§20.9), so e.g. `$onehot0($past(v))` is known true at the first sample. A bit count whose operand uses `$past` is therefore rejected.
  - sva2yosys evaluates no X. Instead, **a condition containing `$past` does not hold at the first sample.**
  - For this to equal IEEE, the condition must never be *known true* while `$past` is X. A static check enforces that: a condition is **rejected** if an operator could turn the unknown into a known true value. Examples: `$past(a) || b`, `$past(a) | v`, `$past(a) -> b`, `$past(a) ? b : c`, a `==`/`!=`/`===` where only some bits depend on `$past`, a reduction of a partly known vector, or `!` above `$past(a) && b`.
  - Accepted forms include `$past(e)` itself, arithmetic, relational, `==`/`!=` with a fully `$past`-dependent operand, `^`, `&&`, `&` and selects. For these, the IEEE value at the first sample is X or 0, so "does not hold" is exact.
  - The rules are listed in `src/sva2yosys/passes/first_sample.py`.
  - **The reference evaluator does evaluate X** (`tests/support/reference/values.py`, `holds`/`truth`). At the samples where some `$past` term has no history, it computes the condition with the X rules of IEEE clause 11.4, and the condition holds only when its value is known non-zero. For an accepted condition this is "does not hold", the same as the rule. For a rejected one, and for a generated negation such as the `!b` of `b[->1]`, it is the IEEE value: at the first sample `!(a && $past(b))` with `a = 0` is 1. An X `if`/`else` selector still selects the `else` branch (§9.5). The trace is two-valued, so X comes from `$past` alone.
  - IEEE itself fails some properties at the first sample: `a |-> b == $past(b)` with `a = 1` fails there. sva2yosys reports the same failure; a reset `disable iff` or a later consequent avoids it.
  - `$past` in a `disable iff` condition is rejected.
- **Generated code.** Sampled-value functions use history registers clocked by the property's sampling event. One init variable per property provides the first-sample results of the edge functions and gates the conditions containing `$past`: `valid` (0 at the first sample, 1 afterwards) when every depth is 1, otherwise a counter `seen` of elapsed samples, saturating at the largest depth `d`, a condition of depth `k` holding only when `seen >= k`. `$past(e, n)` reads the last stage of an `n`-stage history chain. Neither is cleared by `disable iff`. `FirstSample` (an `initial` assertion's enabling condition, §6) is `!valid`; the bit is emitted for every property. The history registers have no initial value, so formally their first-sample contents are arbitrary; no result may depend on them, and the [Layer 3](validation.md#3-the-evidence) tests check that on every first-sample rule. It is portable: Yosys's `$initstate` is rejected by `read_slang` and refers to the global formal step rather than the property's clock. Yosys's own `$past` is not used.
- **Default sampled value, rejected unless all X** (§16.5.1). IEEE uses the *default sampled value* of the operand before the first clock tick: for a static variable its declaration initializer, else its type's default (0 for `bit`, `int`; X for `logic`, `reg`, `integer`); X for a 4-state net; for an expression, the value computed from its operands' defaults, constants included. The rules above take the whole operand as X, which is exact only when that default is all X. sva2yosys therefore rejects every `$past`, `$rose`, `$fell`, `$stable` and `$changed` (at any depth, also in `disable iff`) whose operand is not all X by the abstract interpretation of `passes/first_sample.py` (`default_class`: signals are all X or known, constants are known): `a + 1` and `a[3:0]` over an uninitialised 4-state `a` are accepted; `bit a`, `int n`, `logic b = 1'b0`, `{1'b1, a}` and `a & 4'b0011` are rejected. The message names the cause and says that a `disable iff` does not help: use the sampled-value function only on 4-state signals without an initializer and without constants in the operand. The exporter classifies each signal (`sva2yosys_sampledDefault`). Three constructs hide a known default from the IR and are rejected by the converter inside a sampled operand: a cast to a 2-state type (`$fell(bit'(a))`; X becomes 0, §6.11.2; exporter field `sva2yosys_fourState` on every `Conversion`), a bit count (`$stable($countones(v))`; X bits count as 0, §20.9) and `$isunknown` (`$past(a | $isunknown(a))` defaults to X|1 = 1). See [`validation.md`](validation.md#5-interpretations-and-known-deviations), D1.
- `$rose` and `$fell` look at the LSB. Width and signedness come from Slang's types.
- Nested sampled-value functions (e.g. `$past($rose(a))`) are rejected.

## 8a. `$isunknown` and X-checks

- **Rule.** The model is two-state, so no value is X or Z, and `$isunknown(e)` (IEEE 1800 §20.9: 1 when any bit of `e` is X or Z) is the constant 0. That is exact for the model being proved. Passing the call to `read_verilog -formal` as written would not be: its `x` constants become free values, so `!$isunknown(a)` could fail even for a top-level input. The converter converts the operand (so an unsupported construct inside it is still rejected), checks it as validation checks every expression, and only then substitutes `Const(0)`. The check matters because the operand is discarded: a two-state model has no X values, but an operand can still *produce* X from known values, and that X is exactly what `$isunknown` detects. The one such operator, division or modulo by a value that may be zero (§1), is rejected there as everywhere else (`!$isunknown(a / b)` fails in IEEE whenever `b` is 0, so folding it to a skipped directive would hide failures); `$isunknown(a / 2)` folds to 0; the IR has no `$isunknown` node, so the temporal machines, the reference evaluator and the code generator only ever see the constant.
- **Constants.** A call Slang evaluates itself (a constant operand) reaches the converter as an ordinary constant: `$isunknown(4'b1x00)` is 1 and `$isunknown(4'b0001)` is 0, the IEEE values. They are not X-checks and are never skipped. A constant with x/z bits that is used as a value is still rejected (two-state model).
- **Folding.** In a directive that uses the call, `passes/x_checks.py` folds the Boolean skeleton of its expressions: `!$isunknown(s)` is 1, `b && !$isunknown(c)` is `b`, `a || $isunknown(c)` is `a`, `$isunknown(c) ? x : y` is `y`, and `->` / `<->` likewise. The value of a folded expression is that of the original on every input; shapes the folder does not recognise are kept.
- **Nothing left to check: skipped (X-check).** A directive is skipped, reported as `skipped`, given no monitor, removed from the rewritten file (a comment says why) and counted apart from accepted and rejected, when a **sound structural criterion** proves that nothing can be observed. It is applied only to a directive that used `$isunknown`, on the folded property, and proves:
  - for `assert`/`assume`: the property cannot fail. A sequence property of constant-true Booleans (with `##n`, repetitions of at least one, `and`/`or`) that is not strong; `a |-> c` where the antecedent can never match (a constant-false Boolean at a necessary position, `throughout` of a constant-false condition over a sequence without an empty match, a goto of one, ...) or `c` cannot fail; `and` when every operand cannot fail; `or` when one operand cannot; `not` of a constant-false Boolean; `if` with a constant condition and a branch that cannot fail, or two such branches; weak `nexttime`, `always`, `eventually` of such an operand; weak `until`/`until_with` with a constant-true left operand, or (`until`) a constant-true right operand. Every strong form is **not** proved;
  - for `cover`: no attempt can be both true and nonvacuous (§9.2). The property is never true (a sequence that is not weak and never matches; `and` with such an operand; `or` of such operands), or never nonvacuous (an implication whose antecedent never matches or whose consequent is never nonvacuous; `not`, `nexttime`, `always`, `eventually` of such an operand; `and`/`or` of such operands; `if` whose possible branches are such, a missing `else` being vacuous); or it is an implication whose consequent never succeeds, an `and` whose operands all never succeed, or an `if` whose possible branches never succeed. A vacuously true operand does not stop `and`/`or` from succeeding, since they are nonvacuous when either operand is: `cover property (($isunknown(s) |-> a) and b)` succeeds whenever `b` holds and is accepted with a warning. The criterion is checked against the reference evaluator on generated properties with constants (`tests/unit_tests/test_x_checks.py`);
  - a `disable iff` that folds to true: the directive is always disabled, whatever it is;
  - a procedural enabling condition (§6) that folds to false: the directive has no attempt, not even a vacuous one.

  The decision is made per elaborated instance, after parameters have folded, so the rewrite keeps a skipped site only for the parameter values of its instances and fails loudly for any other combination (`tests/functional_tests/rewrite/CONTRACT.md` point 9).

  For `assume` this is exact: the folded assumption constrains nothing. **If the criterion cannot prove that nothing remains, the directive is treated as mixed.** It never skips a directive that could fail: `a |-> $isunknown(s)` is `a |-> 1'b0`, which fails whenever `a` holds, and `cover property (v |-> !$isunknown(s))` succeeds with `v`.
- **Mixed: accepted with a warning.** A directive that keeps checks (`a |-> b && !$isunknown(c)`) is converted with the constants in place and carries a warning that its X part was dropped, located at the first call. It is exactly the directive without its X part (trace tables `isunknown.trace`, in every layer). In `disable iff`, a condition that folds to false never disables (the warning says so, and `disable` is removed); with a real part left (`r || $isunknown(c)`) it is `r`. A procedural enabling condition that folds to true is dropped with a warning; with a real part left it keeps that part.
- **Option.** `--x-checks=skip` (default) is the policy above; `--x-checks=reject` rejects every directive that uses `$isunknown`, with a located message (`convert(document, XChecks.REJECT)`).
- **First sample and sampled-value functions (decision).** In 4-state IEEE, `$past(x)` is X at the first samples (§8), so `$isunknown($past(x))` is 1 there; the two-state value 0 would be wrong exactly where it matters, and the first-sample rule of §8 (a `$past` condition does not hold) cannot express "X is detected". **`$isunknown` of an operand that uses `$past` is therefore rejected** with a located message, under both policies. `$isunknown` of `$rose`, `$fell`, `$stable`, `$changed` (always known, §16.9.3) or of an operand without sampled-value functions is folded. `$isunknown` *inside* a sampled-value operand (`$past(!$isunknown(c))`) is rejected by the converter before folding: its default sampled value is known (1 for an X operand, §8), which the folded constant would hide (conservatively: the verdict would often be exact; `tests/functional_tests/slang_convert/test_slang_convert_x_checks.py`).
- **Why this matches the corpora.** In AXI4 checkers the X-checks are asserts and assumes of the shape `!$isunknown(s)` and `v |-> !$isunknown(s)`; the corpus survey reports them as skipped.

## 9. Weak and strong, property operators

### 9.1 Rules

- Under §16.12.2, a sequence used as a property is **weak** in `assert` and `assume`, and **strong** in `cover`, unless `strong(...)` or `weak(...)` is written. A sequence used as a property must not admit an empty match (§16.12.2, §16.12.22); Slang already reports it (`SeqEmptyMatch`), and the converter keeps rejecting it.
- A weak sequence fails only if its frontier becomes empty. It never fails just because a trace ends with an obligation still open. Consequently `a |-> ##[1:$] b` **can never fail** as written; `a |-> strong(##[1:$] b)` or `s_eventually` is needed. sva2yosys warns about an `assert` or `assume` part that provably cannot fail (criterion in §9.6); the directive is still accepted and its monitor is unchanged.
- `$` is **never** replaced by a finite bound. Strong semantics are never derived by negating weak semantics unless the transformation is proven for the context.
- **Bounded properties need no liveness.** Every bounded operator has a finite deadline, so on infinite traces a bounded weak and strong property agree, and each lowers to a safety monitor checked by the existing flow (`abc pdr`, BMC). Strength only decides attempts still pending at the end of a finite *simulation*.
- **The end of a BMC run is not the end of a trace.** An attempt pending at SBY's last unrolled step means the depth was too small, never a failure. Generated hardware has only online verdicts. Only the reference evaluator has an explicit end-of-simulation step, tested separately: decided and disabled attempts keep their outcome; a pending sequence is true if weak and false if strong; `not` swaps its finalized operand; `and`, `or` and `if` combine the finalized operands; an implication combines its started subattempts as `and` (matches that would end after the trace do not exist, so with none started it is true).

### 9.2 Attempt results

Each attempt of a property (and of each subproperty) has:

- an **outcome**: `pending`, `true`, `false` or `disabled`. Once it is `true` or `false`, more samples cannot change it (prefix monotonicity). `disabled` is neither pass nor fail: `disable iff` aborts the attempt with all its subattempts (§16.12, Annex F), and no operator turns it into a failure or a success;
- whether it is **nonvacuous** (§16.14.8). Nonvacuity is a property of the attempt over the whole trace, independent of when its outcome is decided:
  - a sequence property is always nonvacuous;
  - `not p` exactly when `p` is;
  - `p and q` and `p or q` when either is (whether or not that operand decides the outcome);
  - `if (e) p` when `e` holds and `p` is;
  - `if (e) p else q` from the selected branch;
  - an implication when some antecedent match exists whose consequent attempt is nonvacuous;
  - `nexttime [n] p` when the `n`-th clock event exists and `p` is;
  - `always [m:n] p` when the attempt of `p` at some offset `i` is nonvacuous and every earlier offset of the range holds;
  - `eventually [m:n] p` when the attempt at some offset `i` is nonvacuous and no earlier offset holds.
  - **Unbounded forms (defined by the reference evaluator):** `always [m:$] p` and `s_eventually [m:$] p` (`always p` and `s_eventually p` are `[0:$]`) follow the same offset-ordered rules over the offsets `m, m+1, …`, and the offsets after the last known sample are unknown, so their nonvacuity is never known to be false; `p until q`, `p s_until q`, `p until_with q` and `p s_until_with q` (Boolean operands) are nonvacuous from their start sample. This is our reading of §16.14.8, which we have not found to give a rule that depends on which operand decides: the operands are Boolean expressions, i.e. sequences, which are nonvacuous whenever they are evaluated, and the first sample evaluates `q` (and `p` when `q` does not hold, or for `until_with`). Should a property operand be admitted later, the rule becomes "nonvacuous when either operand's attempt is nonvacuous".
  - The **nonvacuity sample** is the first sample at which it is known (e.g. `(a |-> b) or strong(1 ##2 c)` with `a` false is true at its start, and nonvacuous from its start through the sequence operand).
  - A cover counts an attempt at its decision sample if it is true and known nonvacuous by then; covers whose nonvacuity could become known only later are rejected (§9.5, cover rule);
- the **decision sample**: the sample at which the outcome became known. Monitors report it in that same sample (§3).

### 9.3 Operators (all bounded)

- `not p`: swaps `true` and `false`; `pending` and `disabled` are kept; strength is reversed (§16.12.3). For a bounded sequence `s`, `not s` fails at the first match of `s` and passes when `s` becomes impossible. `not (a |-> s)` passes at the first refuted consequent and fails when the implication holds, including vacuously when `a` never matches.
- `p and q`: `false` at the first `false` child; `true` when both are `true`, at the later decision. `p or q` is dual: `true` at the first `true` child; `false` when both are `false`, at the later decision (the earliest refuting prefix).
- `if (e) p [else q]`: `e` is sampled at the attempt start, and only the selected branch is evaluated; without `else` and with `e` false the attempt is `true` and vacuous.
- An implication with a property consequent (`a |-> (b |-> c)`, `a |-> not s`): every match of the antecedent starts a consequent attempt. The implication is `true` when every started attempt is `true` and no further antecedent match can occur, and `false` at the first `false` one.
- `nexttime [n]` / `s_nexttime [n]`, `always [m:n]` / `s_always [m:n]`, `eventually [m:n]` / `s_eventually [m:n]`: start subattempts at fixed offsets or over a bounded window, and combine them as `and` (`always`) or `or` (`eventually`). `nexttime [0] p`, `always [0:0] p` and `eventually [0:0] p` are `p`. At the end of a finite simulation an offset beyond the trace counts as true for the weak forms and false for the strong ones (`not` reverses the strength: `not s_always [m:n] p` is `eventually [m:n] not p`). The unbounded forms and the `until` family (§9.6) are:
  - `always [m:$] p` (`always p` is `m = 0`): `false` at the first `false` started offset, never `true` online (the window never completes); weak, so a pending attempt finalizes `true`. IEEE has no unbounded `s_always`.
  - `s_eventually [m:$] p`: `true` at the first `true` started offset, never `false` online; strong, so at the end of a simulation the offsets after the trace count `false`, and a pending attempt finalizes `false` unless some started offset finalizes `true` (a weak sequence operand that is pending does: `s_eventually (a ##1 b)` in an `assert` with `a` high at the last sample finalizes `true`). IEEE has no unbounded weak `eventually`.
  - `p until q`: at each sample `t` from the start, `true` if `q` holds at `t` (`p` is not needed at `t`), else `false` if `p` does not hold at `t`; neither happening is pending, which finalizes `true` for `until`/`until_with` and `false` for `s_until`/`s_until_with`. `p until_with q` is `true` where `p` and `q` hold and `false` where `p` does not hold, whether or not `q` does. On Boolean operands these are `weak(p[*0:$] ##1 q)` and `weak(p[*0:$] ##1 (p && q))`, strong for the `s_` forms; the node is kept as such (it needs no sequence automaton in the reference); the machines receive the weak forms as those sequences (§9.6, lowerings).
  - Unbounded sequences on a finite trace: `##[m:$]`, `[*m:$]`, `[->m:$]`, `[=m:$]` are matched by exploring every end up to a horizon of the known samples plus the fixed lengths of the sequence, which is not a bound on the property (an alive unbounded sequence is pending, never false). Goto `b[->m:n]` and nonconsecutive `b[=m:n]` are matched from their definitions (§16.9.2) by counting the samples at which `b` holds, each sample of the match being one where `b` or `!b` holds, not through the `(!b[*0:$] ##1 b)[*n]` expansion; the expansion is checked as a law (`tests/unit_tests/test_reference_unbounded.py`, on every trace of up to 5 samples of two signals). The reference tables are `unbounded.trace`, `liveness.trace` and `until.trace`.

### 9.4 Construction

Each subattempt belongs to exactly one parent attempt, so that two obligations of different parent attempts are **never merged**: a failing one would otherwise hide behind a live one, as in the merged-frontier counterexample of [`strategies.md`](strategies.md#example-3-why-obligations-of-different-ages-are-not-merged) (Example 3). The representation (`temporal/attempts.py`, `temporal/netlist.py`) is compositional rather than an enumeration of configurations: every bounded attempt lives at most `life(p)` samples, so the machine keeps one instance of the root property per attempt age, and each instance holds its own subattempts (an implication one consequent instance per trigger offset, `and`/`or` both operands with their decided results). The instances are built per age, so only reachable state is stored, as a Boolean netlist of registers and gates that the code generator emits mechanically.

- **Instances.** An instance is launched (a pulse at its start), runs while its state is non-zero, and is cleared when it decides; a decided `and`/`or`/implication also clears its remaining subattempts. Each instance reports per sample: decided true, decided false, still pending, and nonvacuity known true. An instance that could still be pending at its last age `life - 1` is a construction error, which the simulator reports (a structural self-check of `life`).
- **Nonvacuity** is tracked as "known true by this sample" while the instance runs: a sequence from its launch, `not` its operand's, `and`/`or` any operand's (latched at the operand's decision), `if` the selected branch's, an implication any started subattempt's (accumulated). A node with `launch_nv` (below) has the constant 1. Knowledge that would arrive only after a subattempt's decision is not tracked, because the subattempt is cleared then; the cover rule (§9.5) is a conservative sufficient condition that rejects every cover where that matters (and some where it does not, e.g. `(a |-> b) or (c |-> d)`), so an accepted cover's `success` is the reference's. Elsewhere the machine never claims a nonvacuity the reference does not know.
- **Disable** clears the whole temporal state asynchronously and suppresses the outputs, as for the canonical machine (§7).
- **Budgets.** Compilation is bounded by deterministic admission policies applied after Slang elaboration: the per-attempt budgets (`temporal.budgets.Budgets`) and the shared, configurable `ResourceLimits`. They are not time, RAM or solver guarantees. Exhaustion gives a located rejection, never an approximation; `$` remains unbounded. See [`architecture.md`](architecture.md#resource-limits) and [`usage.md`](usage.md#compiler-resource-limits---max-expansion---max-work---max-state-bits---max-emission).

### 9.5 Lowerings, machine selection and the cover rule

**Exact lowerings** (`temporal/lower.py`; checked against the reference, which evaluates the written property natively):

- `if (e) p` without `else` is `e |-> p`, fused into `p`'s antecedent when `p` is an implication. Without `else` and with an X condition there is no attempt, which the first-sample rule's forced-false guard gives exactly.
- `if (e) s1 else s2` with sequence branches is the sequence `(e ##0 s1) or (!e ##0 s2)`. The unselected branch dies at the attempt's first sample, so failures, successes, vacuity and strength are those of the selected branch. Two `not s` branches give `not` of that sequence.
- Other `if`/`else` (implication branches, mixed branches, branches of different strengths) stays `PropIf` and uses the per-attempt machine.
- An `if`/`else` whose condition uses `$past` is rejected, by the converter and independently by validation. At the first samples `$past` is X, which selects the `else` branch, whereas the first-sample rule (§8) would force both guards `e` and `!e` false.
- `nexttime [n] p` is `(1 ##n 1) |-> p`, `always [m:n] p` is `(1 ##[m:n] 1) |-> p`, and `eventually [m:n] p` is `not always [m:n] not p`.
  - These are exact for the outcome and, under the cover rule below, for nonvacuity.
  - `assert nexttime [n] s` and `assert always [m:n] s` lower to shapes the canonical machine decides exactly and keep it.
  - The lowered machine accumulates the nonvacuity of any offset. Outside the cover rule its `success` output (consumed only by covers) may therefore claim a nonvacuity the reference does not have (e.g. `eventually [0:1] (((a ##2 b) or d) |-> c)`); the cover rule rejects every cover where that matters.

**Conjunctions** (`temporal.compile.conjunct_directives`):

- A top-level property `and` of an `assert`/`assume` is compiled conjunct by conjunct. Each conjunct gets its own machine and monitor, asserting or assuming that conjunct; together they consume exactly the failures of the conjunction.
- This is exact up to and including the first failure (§6, "what each directive consumes"). Later failures of the conjunction are not claimed, which also lets every conjunct use any optimised form.
- An implication distributes over a consequent `and` (`a |-> (p and q)` is `(a |-> p) and (a |-> q)`), and so does a procedural enabling condition.
- A conjunction has **no `success`**: the attempt succeeds when both operands do, which is not the OR of their successes. A `cover` is therefore never split.

**Machine selection** (a typed compilation boundary, per directive part):

- the **canonical machine** when it decides the shape exactly. For a cover that is a sequence, `not s`, or an implication whose antecedent has a single match length and whose consequent is `s` or `not s` (§2, cover of an implication);
- the **configuration-set machine** for weak unbounded consequents and covers of strong unbounded sequences (§9.6);
- otherwise the **per-attempt machine** (§9.4).
- A directive keeps the first machine in this order that decides it. The optimised forms ([`strategies.md`](strategies.md)) apply to the canonical machine only, and not to its negated shapes (`not s`, and an implication into `not s`: the machine's `negated` flag swaps an obligation's verdicts).

**Accepted shapes.**

- For `assert` and `assume`: every property built from sequences, `not`, `and`, `or`, `if`/`else` and implications, nested at any depth, with any consequent.
- For `cover`: the same, when the cover rule holds.
- Every property `and`/`or` must have at least two operands.
- How the per-attempt machine decides these shapes:
  - `not` of an implication is true at the first refuted consequent and false when the implication holds, including vacuously;
  - `or` of non-sequences keeps explicit strengths, which change no online verdict.

**Lifetime.** `life(p) >= 1` bounds the samples an attempt of `p` can stay pending in the per-attempt machine:
- a sequence: its longest match (1 when it has none);
- `not`, `and`, `or` and `if`: the maximum over the operands;
- an implication: its longest antecedent end offset plus its consequent's `life`;
- `nexttime [n]`, `always [m:n]` and `eventually [m:n]`: `n` plus the operand's `life`.

**Cover rule.** A cover is accepted only if the nonvacuity of a true root attempt is known at its decision. The rule is evaluated on the property **as written**.

- **`launch_nv(p)`** means nonvacuous unconditionally, known at launch. It holds for:
  - a sequence;
  - `not` of such a property;
  - `and`/`or` with such an operand.

  The instance's nonvacuity is then the constant 1 from its launch.
- **Otherwise, nonvacuity known at the true / false decision:**

  | Node | At true | At false |
  |---|---|---|
  | sequence | yes | yes |
  | `not` | the operand's at false | the operand's at true |
  | `and` | both operands | `launch_nv` of either |
  | `or` | `launch_nv` of either | both operands |
  | implication | the consequent | `launch_nv` of the consequent |
  | `if` | both branches | both branches |
  | `nexttime` | the operand's | the operand's |
  | `always` | the operand's at true | `launch_nv` |
  | `eventually` | `launch_nv` | the operand's at false |
  | any `launch_nv` node | yes | yes |

  `nexttime`, `always` and `eventually` have `launch_nv` false.
- Rejected examples: `cover property ((a |-> b) or (c |-> d))`, where an operand can become nonvacuous only after the `or` is already true, and `cover ((a |-> b) or nexttime [2] 1'b1)`. The rule is conservative: it also rejects some covers where the late knowledge would not matter.
- Accepted covers include covers of `and`, of `not` of an implication, and of implications whose antecedent has several match lengths (per-attempt machine).

### 9.6 Unbounded sequences and properties

**Scope:**
- `##[m:$]` (`##[*]`, `##[+]`) and `[*m:$]` (`[*]`, `[+]`);
- goto `[->n]`/`[->m:n]`/`[->m:$]` and nonconsecutive `[=n]`/`[=m:n]`/`[=m:$]` repetition;
- `always` and `always [m:$]`; `s_eventually` and `s_eventually [m:$]`;
- `until`, `s_until`, `until_with`, `s_until_with`;
- strong liveness for `assert` and `not` of an unbounded sequence, both below; strong `assume` is accepted in the one-configuration class (fairness below).

`$` is never replaced by a bound. The reference evaluates every construct of the scope natively, on finite traces and on lasso traces (`tests/support/reference/lasso.py`: exact infinite-trace outcome and decision sample; nonvacuity is not provided on infinite traces).

**Semantic frame.**

- **Strength decides safety or liveness.** After strength resolution (§16.12.2 defaults; `not` reverses strength, §16.12.3), every weak obligation is a safety property: it fails only at a finite bad prefix (its frontier dies). Every strong obligation that can stay pending forever is a liveness property. Bounded obligations never need liveness (§9.1).
  - Strong and unbounded in `assert`/`assume`: `strong(a ##[1:$] b)`, `s_eventually p`, `p s_until q`, and `not s` for a weak unbounded `s`.
  - Example: `assert property (not (a ##[1:$] b))` fails on an infinite trace where `a` holds and `b` never does, since `not weak(s)` is strong.
- **`cover` never needs liveness:** a finite match satisfies it, so unbounded sequences in a cover are reachability, decided by the safety machinery.
- **Consequents that can never fail.** A weak unbounded consequent from whose reachable configurations the frontier can never become empty without a match (`a |-> ##[1:$] b`) is legal and accepted, and the CLI warns (§9.1). A `ConfigMachine` part of an `assert` or `assume` provably cannot fail when, in every configuration, a letter without guards (always held) is accepting or has a successor (`temporal.configurations.never_fails`): the step is monotone in the held letters, so the guard-free letters are the smallest held set any sample gives, and if even they keep the obligation alive or discharged no sample fails it. The test is conservative (every guard group is taken as independently satisfiable, and "all guards false" as possible even for correlated guards such as `b` and `!b`, so `a |-> b[->1]` is not proved although it never fails), and a machine without configurations can fail. A discharge by matching is not a failure: `a |-> b[*1:$]` can fail at once when `b` is false, so it gets no warning.
- **Sampled values on lasso traces.** Sampled values and the first-sample X rules depend on up to `d` earlier samples (`d` the largest sampled-value depth), so the sampled trace is periodic only from `|prefix| + d`. Cycle detection on (loop position, frontier) starts there, and decides "pending for ever" structurally, never by a guessed number of unrollings.

**Lowerings** (exact for the outcome, checked against the reference on traces). Operands are restricted to what the canonical machine and the configuration-set machine represent, since the per-attempt machine stays bounded.

- **`always`.** `always p` is `(1 ##[0:$] 1) |-> p` and `always [m:$] p` is `(1 ##[m:$] 1) |-> p`.
  - Supported `p` for `assert`/`assume`: a sequence, `not` of a bounded sequence, or an implication into one of those, flattened as `(w ##0 a) |-> c` for `p = a |-> c` (`|=>` as `(w ##0 a ##1 1) |-> c`). The failures of `x |-> (y |-> c)` and `(x ##0 y) |-> c` coincide.
  - Nonvacuity differs, so a `cover` of an unbounded `always` is rejected; other operands are rejected with a location.
- **`s_eventually` (`assert`, or `assume` when its fairness constraint is exact).** `s_eventually [m:$] p` (`s_eventually p` is `m = 0`) is supported when `p` is a Boolean, or a bounded sequence, whose truth at an offset is "it matches from there" (a weak and a strong bounded sequence agree on infinite traces). The machine uses the strong sequence `strong(1[*m:$] ##1 p)` (`temporal/lower.py`; the same matches as `1 ##[m:$] p`, 16.9.2.1, chosen because for `m = 0` its start frontier is a fixed point, so `s_eventually p` gets one configuration and the exact pending bit).
  - This is exact for the online verdicts and on infinite traces only. At the end of a finite simulation a pending weak bounded operand is true (`s_eventually weak(a ##1 b)` with one sample and `a = 1` is true, while the strong sequence has no match), so the reference evaluates `s_eventually` natively, with its own end-of-simulation step; monitors never finalize.
  - It is not lowered as `not always not p`: witnessing the inner obligations of `always not p` does not implement eventuality (with `b` false forever every inner `not b` obligation succeeds, while `s_eventually b` is false).
  - A weak unbounded sequence operand (true while pending forever) and non-sequence operands stay rejected.
- **`until`.** With Boolean `p` and `q`:
  - `p until q` is `weak(p[*0:$] ##1 q)`: true at the first tick where `q` holds, if `p` held at every earlier tick (`p[*0] ##1 q` is `q` at the first tick);
  - `p until_with q` is `weak(p[*0:$] ##1 (p && q))`;
  - `s_until` and `s_until_with` are the same sequences, strong (`strong(p[*0:$] ##1 q)`, `strong(p[*0:$] ##1 (p && q))`; accepted for `assert`).

  Property operands need unboundedly many per-attempt instances and are rejected.
- **Goto and nonconsecutive repetition.** `b[->n]` is `(!b[*0:$] ##1 b)[*n]` and `b[=n]` is `b[->n] ##1 !b[*0:$]` (§16.9.2). They are IR nodes that the reference evaluates natively, so the oracle does not share the desugaring with the product. Every condition a lowering or expansion introduces (the goto/nonconsecutive negations, `until_with`'s `p && q`) goes through the first-sample rule (§8).

**Representations.**

- **R1, unbounded antecedent (canonical machine).** The antecedent frontier is already one registered letter set merged across attempts. That is exact for `assert`/`assume`, since an antecedent match launches the same consequent obligation whatever its start. Only the automaton builder changes (loops).
- **R2, unbounded consequent: the configuration-set machine** (`temporal/configurations.py`, `ConfigMachine`).
  - A configuration is the complete frontier (letter set) of one obligation, a state of the consequent automaton's subset construction. The machine keeps one register per reachable configuration ("some live obligation is in it").
  - Obligations are merged only when their complete configurations are identical. That is exact for every output: identical configurations fail, match and die at the same samples, and `assert` consumes failures and `cover` first matches, both per-sample bits.
  - The configurations are found statically. The reachable set is the closure of the start frontier under the unions of the guard groups' successor sets: letters with equal guards are held together, a letter without guards always is, and a held set with an accepting letter discharges the obligation. Every letter condition is treated as independent, an over-approximation: an unreachable configuration costs a register, never a verdict.
  - Budget `Budgets.configurations` = 256. Measured: the generated monitor grows about 3x in text per doubling of the configurations, about 1 MB at 257 configurations and 3 MB at 513. For example, `a |-> ##[1:$] b ##8 c` has 257 configurations and is rejected, while `##7` has 129. Exceeding it, or the letters estimate (checked before any range is iterated), is a located rejection.
  - Directives with bounded consequents keep the slot machine.
- **R3, liveness of a strong `assert` (`temporal/liveness.py`, `codegen/emit_configurations.py`).** A strong unbounded consequent of an `assert` is a `ConfigMachine` with `liveness` set. Its **safety part** is the R2 machine unchanged (the frontier of an obligation dies: `fail`). Its **liveness part** is the check that the *busy* signal is not 1 at every sample from some point on, through the procedural form `always @(posedge clk) assert property (s_eventually !busy);` on a Boolean monitor signal, generated inside `` `ifdef SVA2YOSYS_LIVE `` so that without the macro the monitor is the weak machine's. A strong directive whose attempt launched at `t` stays pending for ever is false in the limit (16.12.2: a strong sequence with no match is false); in the liveness part that is exactly an obligation that never matches and whose frontier never dies. `busy` has two forms, chosen by the number of configurations:
  - **L1, pending bit** (one configuration: `s_eventually b`, `a |-> s_eventually b`, `b s_until c`, `always (a |-> s_eventually b)` with Boolean operands). `busy` is the configuration register itself, "some live obligation is in the configuration after this sample". **Exact:** every live obligation is in that one configuration, so at each sample they all succeed, all die or all stay, and an obligation launched at sample `t` is evaluated at `t` with the same letters as the older ones. Hence `busy` stays 1 from some sample on exactly when the obligations live at that sample stay pending for ever.
  - **L2, witness** (every other strong directive, e.g. `a |-> strong(##[1:$] b)`, `s_eventually [2:$] b`, `strong(b[->3])`). A free bit `pick` (`$anyseq`) chooses, at a sample where the witness was idle at the start of the sample, to follow the obligation launched there. The witness registers one bit per consequent letter and applies the obligation step of the configuration machine to that one obligation. It becomes idle when the obligation matches, when its frontier dies (a safety failure, reported by the safety part) or when `disable iff` holds, and it never re-picks in the sample in which it is freed (so `a |-> strong(##[1:$] b)` with `a = b = 1` at every sample, whose every obligation ends one sample after its launch, has `busy` 0 at every other sample). **Sound:** a witness busy for ever follows one obligation pending for ever. **Complete:** if an obligation launched at `t` stays pending for ever, the solver can keep `pick` 0 until `t` and pick it there.
  - **Two checks.** The safety part (failures when the frontier dies) stays the R2 machine and is checked in the `prove` task; the liveness part is checked in a separate `live` task (SBY `live` mode, suprove engine). `live` mode turns safety assertions into assumptions, so a safety failure would only remove traces and could make the liveness check vacuous. Examples: `a[*1:2] |-> strong(c ##[1:$] b)` with `(a, c, b) = (1,1,0), (1,0,0)` and then all-low (the attempt starting at sample 1 fails at a finite sample, and the obligation of the attempt at sample 0 stays pending); `p s_until q` with `p = q = 0` forever. A strong directive therefore gives two checks, a safety proof (`prove`) and a liveness proof (`live`), and holds only when both pass. The liveness signal is compared with the reference's verdicts only on lassos where no attempt fails at a finite sample.
  - **`assume`, fairness.** An assumption restricts traces to those on which its property holds (16.14.2). A strong assumption with at most one configuration (L1) has the same safety constraint `assume (!fail)` plus `always @(posedge clk) assume property (s_eventually !busy);` under `SVA2YOSYS_LIVE`. Yosys represents this as `FLAVOR "fair"`: `!busy` must recur infinitely often. By the L1 argument this is exact: safety and fairness together admit exactly the traces where no attempt is FALSE, at a finite sample or in the limit. Examples: `s_eventually b`, `a |-> s_eventually b`, `always (a |-> s_eventually b)`, `a s_until b`, `not (a until b)`. Zero configurations is also L1: `busy` is constant false and only safety constrains the trace.
  - **L2 assumptions are rejected by the compiler.** `a |-> strong(##[1:$] b)`, `s_eventually [2:$] b`, `strong(b[->2])` and `not (a ##[1:$] b)` need several configurations. A witness picks one obligation; as an assumption it would constrain only that obligation, weakening the required assumption. The located diagnostic retains `a strong unbounded property (liveness) in 'assume'` and explains the witness problem.
  - **Task boundaries.** Only `live` sees fairness, and needs at least one assertion. `prove` omits fairness and checks a superset of admitted traces: it is conservative (a failure may have no fair infinite extension; a pass remains valid). `cover` also omits fairness, so a reached cover is provisional: it can have no fair extension. Every accepted cover in a run with an accepted fairness assumption gets the located warning `this cover's result ignores the fairness assumptions (strong assume) of this run: a reached cover is provisional, see docs/usage.md`. See [`usage.md`](usage.md#fairness-assumptions-and-covers) for a separate live confirmation task.
  - **Boundary cases.** With `disable iff (d) req |-> s_eventually ack`, recurring sampled `d` aborts each obligation, whereas one initial pulse cannot discharge later requests. `$past(req) |-> s_eventually ack` never triggers at the first sample. Strong assumptions still undergo all sequence checks and the first-sample rule: `s_eventually ($past(ack) || ready)` is rejected. The required `multiclock off` sampling model and asynchronous clear are unchanged.
- **`not` of an unbounded sequence (`ConfigMachine.negated`).** A sequence written as a property in `assert`/`assume` is `weak(s)`: 16.12.2 makes the default depend on the directive only, so `not` does not reverse the default; `not` reverses the strength of its operand (16.12.3). One obligation of an unbounded `s` has three outcomes: it **matches**, it **dies** (the frontier is empty without a match) or it **stays pending for ever**. `weak(s)` is false exactly when the obligation dies, `strong(s)` exactly when it does not match. Hence for `not s`:
  - `not weak(s)` (default strength, explicit `weak(...)`, weak `until`/`until_with`): FALSE at the first match, TRUE where the obligation dies, **FALSE when it stays pending for ever**. It is a strong property: a safety part (a match fails) and a liveness part (no `not` obligation pending for ever; `live=pending|witness` as in R3).
  - `not strong(s)` (explicit `strong(...)`, `s_until`/`s_until_with`, `s_eventually [m:$] p` after its lowering): FALSE at the first match, TRUE where it dies, **TRUE when pending for ever**. It is a weak property, safety only (`not s_eventually b` is `always !b`).
  - **Negated configuration machine.** The configurations and the step are those of `s`; the events are swapped, as in the slot machine: a match (an accepting held letter) is a **failure**, death (no successor) is a **success**, and the obligation is discharged either way. The merge argument of R2 holds unchanged.
  - **Liveness part.** Identical to R3. L1 and L2 stay exact because a `not` obligation is pending exactly while the `s` obligation is; the witness is freed on a match (here a failure, reported by the safety part) or on death (here a success).
  - **`never_fails`** is `False` for a negated machine: it fails on a match, which the criterion does not analyse (conservative: no warning).
  - **`assume`.** `not strong(s)` is accepted (weak, safety only); `not weak(s)` is a strong assumption, accepted with at most one configuration (e.g. `not (a until b)`) and rejected otherwise. A `cover` of any `not` with an unbounded part stays rejected.
- **R4.** Unbounded parts where the per-attempt machine would be needed (nested implications, `not`/`or`/`if` of non-sequences) stay rejected.

**Accepted unbounded shapes** (the machines; everything else is a located rejection with the construct's message):

- **Unbounded antecedents (R1).**
  - Accepted: `assert`/`assume` implications (`|->`, `|=>`) whose antecedent contains `##[m:$]`, `[*m:$]`, goto or nonconsecutive repetition (any nesting with the supported sequence operators) and whose consequent is a bounded sequence or `not` of one.
  - Also unbounded `always p`/`always [m:$] p` with the operands above (also `a |-> always p`), all flattened into one implication.
  - A `|=>` antecedent that can match empty stays rejected (§2).
  - `sequence_bounds` reports `max_length` `None` with `min_length` set for an unbounded sequence; `obligation_slots` and the per-attempt machine refuse it.
  - The optimised forms do not apply: `--strategy auto` selects `direct`, and a forced `window`/`history` is refused with "does not apply to an unbounded antecedent".
- **Weak unbounded consequents (R2)**, for `assert`/`assume`:
  - an implication (`|->`, `|=>`; antecedent bounded or unbounded) whose consequent is a weak unbounded sequence (default strength or `weak(...)`);
  - a weak unbounded sequence as the whole property;
  - `always`/`always [m:$]` over those, and what `nexttime`, bounded `always` and a property `if` lower to such a form.
  - The optimised forms do not apply.
  - Constant conditions follow §3: `a |-> ##[1:$] 1'b0` is accepted and never fails.
- **Weak `until`/`until_with`** (Boolean operands), lowered as above and decided by the configuration-set machine wherever a weak unbounded sequence is accepted:
  - as the whole property;
  - as the consequent of `|->`/`|=>`, with a bounded or unbounded antecedent;
  - under `always`/`always [m:$]`;
  - in the forms `nexttime [n]` (at the top), bounded `always` and property `if` (with or without an `else` of a weak sequence) lower to.

  First samples and constants:
  - **`$past` operand.** The first-sample rule runs on `p`, `q` and `p && q`, so a `$past` operand never holds at the first sample, whichever position it is in. At the first sample, `p until q` therefore fails when `q` has `$past` and `p` does not hold. `until_with` fails when `p` has `$past`, also if `q` holds.
  - **Constants** need no special rule (§3):
    - `p until 1'b0` never fails while `p` holds;
    - `1'b0 until q` is decided at the first sample: `q`, else false;
    - `p until_with 1'b0` fails at the first sample where `p` does not hold;
    - `1'b0 until_with q` fails at the first sample.
- **Covers of unbounded sequences** (`passes.validate.cover_unbounded`, judged on the property **as written**, decided by the configuration-set machine, consuming `success`):
  - a sequence property with an unbounded part (`##[m:$]`, `[*m:$]`, goto, nonconsecutive, at any nesting) that is **strong**: the cover default (§16.12.2) or an explicit `strong(...)`;
  - an implication (`|->`, `|=>`) whose antecedent is bounded and has a single match length, and whose consequent is such a sequence property. "Single match length" means `min_length == max_length` over its non-empty matches; an empty match of a nullable antecedent is ignored, and an antecedent with no match counts as single-length, so nothing is ever covered.

  **Why this is exactly §6:**
  - **One count per attempt.** A cover counts an attempt once, at the sample where its first match ends. In the machine an attempt is one obligation in one configuration, and the obligation is discharged at the first sample at which a held letter is accepting. Later matches of the same attempt are never counted, and a frontier that dies reports nothing.
  - **Merging changes no count.** Obligations of different attempts are merged only when their configurations are identical, and identical configurations match at the same samples. So for every sample the bit "some attempt first-matches here" is the OR over the live configurations, whatever was merged.
  - **Implications.** With a single-length antecedent an attempt has exactly one trigger, so the success is a nonvacuous success (§9.2), and the cover rule holds for these shapes. With several antecedent lengths an attempt would succeed only when the consequents of all its antecedent matches do (§16.12.7), which merging obligations cannot see.
  - `disable iff` clears the state and suppresses the output at a sample where it holds, as for `assert`. A `$past` condition follows the first-sample rule (§8). A constant-false condition is an ordinary letter: `cover property (a ##[1:$] 1'b0)` is never covered.

  **Rejected covers, with located messages:**
  1. **An explicit `weak(...)` of an unbounded sequence**, also as an implication consequent: *a weak property can hold on an infinite trace without any finite success witness*. Counting matches only would silently make `weak(s)` mean `strong(s)`. Counting attempts that stay pending for ever is not a reachability question: no finite prefix witnesses it, so it needs a lasso argument, like liveness. A bounded weak sequence is unchanged (§9.1).
  2. **`until` and `until_with`**, which are weak, for the same reason.
  3. **An implication with an unbounded antecedent:** the success would be per antecedent match, which merging the antecedent frontier cannot count per attempt.
  4. **An unbounded consequent with an antecedent of several match lengths.**
  5. **Every other cover with an unbounded part** (`always`, `s_eventually`, `not`, `or`, `and`, `if`, `nexttime`, a nested implication, anything needing the per-attempt machine).

  Decidable with no extra machinery but not added:
  - `nexttime [n] s`, `if (e) s` without `else`, and a bounded `always [m:m]`;
  - `cover` of `not s` for a strong unbounded `s`, which falls under the weak rule (1).

**Still rejected**:
- in `assume`: a strong unbounded part that needs more than one configuration, including `not` of such a weak unbounded sequence (one configuration is accepted as a fairness constraint, §9.6; `assert` accepts strong unbounded parts and `not` of them, and covers accept strong ones);
- an until or other unbounded part under a shape that needs the per-attempt machine (`or`/`and` of non-sequences, nested implications, `a |-> nexttime (p until q)`; only an unbounded `always` flattens);
- unbounded parts in the per-attempt machine's shapes.

## 10. Unsupported input

Anything outside the supported subset (see [`supported-sva.md`](supported-sva.md)) is rejected with a located diagnostic, including:
- multiclock properties (`error: multiclock SVA properties are not supported yet`);
- `negedge` and `iff` clocks.

Nothing is passed through to Yosys silently. In particular:
- concurrent assertions are never left in the output;
- `bind` is never emitted for a frontend that does not support it: `read_verilog` rejects it since Yosys 0.70 and silently ignored it before (Yosys contract, point 9).
