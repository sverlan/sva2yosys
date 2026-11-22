# Monitor forms and lowering strategies

This document explains how a concurrent assertion is turned into a plain SystemVerilog monitor, and which form of monitor sva2yosys uses. A monitor remembers what is still pending from earlier samples ([its temporal state](monitors.md#the-temporal-state-by-case)); the form of the monitor is the way it stores that state. There are five forms. The check output (`form=`) and the header of each monitor name the form (see [`usage.md`](usage.md#monitor-forms---strategy---equivalence)).

- **Bounded properties: two strategies, three forms.** The page starts here, with the two ways to lower a bounded property: the **direct** strategy (online; the form `direct`, which is canonical) and the **delayed** strategy (lookback; the form `history`). The third form, `window`, is a compressed form of `direct`. See [The direct strategy](#the-direct-strategy-canonical), [The delayed strategy](#the-delayed-strategy-lookback) and [When sva2yosys uses which](#when-sva2yosys-uses-which).
- **Properties that need results per attempt** (nested implications, `not` of an implication, ...): the form `attempt`, in [The per-attempt machine](#the-per-attempt-machine).
- **Unbounded consequents:** the form `configurations`, in [Unbounded consequents: the configuration-set machine](#unbounded-consequents-the-configuration-set-machine). An unbounded antecedent keeps the form `direct` ([Unbounded antecedents](#unbounded-antecedents)).

All forms are **exact** representations of the same SVA semantics (see [`semantics.md`](semantics.md)). The semantics are the source of truth: a form is a lowering choice, and it may never change which traces pass or fail.

## Direct and delayed

The two strategies for bounded properties differ in two things:
- **what state** the monitor stores;
- **at which sample** a violation is reported.

| | Direct (online) | Delayed (lookback) |
|---|---|---|
| Stores | the live partial matches of each open obligation | a history of past sampled values |
| Decides | at every sample, as soon as the outcome is known | at a fixed *judgement point* after the trigger |
| Reports a violation | at the **earliest** sample at which no continuation can succeed | at the judgement point, which may be **later** |
| Role in sva2yosys | **canonical**; always available | an **optimization**, selected only when it reports at exactly the same sample as direct |

The project rule is **no intentional latency**. A delayed monitor that reports later than the direct one is not allowed, even though it would never miss a failure in an unbounded proof.

## Terms

- **Sample `k`**: the `k`-th sampling event of the property's clock, for example the `k`-th `posedge clk`. Values "at `k`" are the sampled values of that event.
- **Trigger**: a match of the antecedent of `|->`, ending at sample `k`. It launches an **obligation**: the consequent must match starting at `k` (for `|->`) or at `k+1` (for `|=>`).
- **Attempt / obligation age**: an obligation launched at `k` has age `j` at sample `k+j`.
- **Frontier**: the set of automaton positions (partial matches) still alive for one obligation.
- **Earliest failure**: the first sample at which an obligation's frontier becomes empty without the consequent having matched. From then on no continuation of the trace can satisfy it.

---

## The direct strategy (canonical)

The monitor keeps, for each open obligation, the frontier of its consequent. At every sample it computes three things combinationally:
- which frontier positions survive or advance on the current sampled values;
- whether some obligation has just **matched**, which discharges it;
- whether some obligation has just lost its **whole** frontier, which is a **failure, reported in this same sample**.

The next frontier is then registered for sample `k+1`.

**Compact DIRECT layout.** For a single guard-bearing chain or a contiguous Boolean delay window, the emitter stores each age's reachable letters consecutively in one packed pipeline register and computes only those letters and their transitions. An accepting obligation clears all of its successor bits, and a dead frontier fails at its current sample. This is exact at every sample, including after failures, for assert, assume and cover. Other automata use the dense DIRECT layout.

For fixed delays, fixed Boolean repetitions and Boolean delay windows, an age has at most two reachable letters and a fixed number of transitions, so state bits, assignments and operator terms grow linearly with the range. General product or nondeterministic sequences, and `history`, have no such guarantee. The conceptual `pend` example below uses one waiting bit per age; the emitted window pipelines keep the canonical reachable-letter bits, which Yosys can merge when they are identical. The tests compare both events with a dense reference emitter and with hand-derived sample rules.

Obligations launched at different samples are kept in separate **slots indexed by age**. Obligations launched at the same sample are identical and share one slot. Slots are needed because assertion satisfaction is *universal*: every obligation must succeed on its own (see Example 3).

**The examples and the generated monitors.** The examples below are small hand-written sketches of the idea, with readable names (`pend`, `w`, `a_h`) and the least state needed. They are not the text that sva2yosys emits. For the direct examples, a generated monitor has the same behaviour (it reports `fail` and `success` at the same samples; the tests compare the generated monitors with hand-derived tables like these), but its code is more verbose. To see it, run `sva2yosys` with `-o` on the property and open `sva2yosys_monitors.sv` ([`monitors.md`](monitors.md) describes what every monitor contains). The sketches correspond to it as follows:

| In the sketches | In the generated monitor |
|---|---|
| `a`, `b`, `c` | the condition wires `c<N>` over the ports `i<N>` |
| the trigger (`a` for `a |-> …`) | `trigger`, computed from the antecedent frontier `ant_q` |
| `pend`, `w`: the open obligations of each age | `pipe_q`: a packed register of the reachable letters of each age; `pipe<N>_h<k>`, `pipe<N>_accept` and `pipe<N>_fail` are the held letters, the acceptance and the failure of age `N`, and `pipe<N>_n<k>` the next-state bits |
| `fail` | `fail`, the OR of the `pipe<N>_fail` wires, gated by `!dis` |
| `always @* assert (!fail)` | the `g_check` block |
| (not shown) | `dis`, the `disable iff` condition, which clears the temporal state asynchronously (the sampled-value history keeps running) |

The register counts differ too. The generated monitor stores each age's reachable letters, so for `a |-> ##[1:3] b` it registers 5 bits where the sketch uses 3 `pend` bits (see the table in *Size of the canonical direct monitor* below); letters that are always equal, such as "`b` may match now" and "still waiting" before the last age, are merged by Yosys. The delayed examples are not generated monitors: they show why a delayed monitor would report some failures late (Example 2 again), which is not allowed; the emitted `history` form is described under [When sva2yosys uses which](#when-sva2yosys-uses-which).

### Example 1: `a |-> ##[1:3] b`

The obligation launched at `k` needs `b` at some sample in `k+1 … k+3`. The only position at every age is "still waiting", so each slot is one bit. `pend[j]` means "the obligation launched `j+1` samples ago is still open":

```systemverilog
logic [2:0] pend = 3'b000;
assign fail = pend[2] && !b;                          // age 3: last chance missed
always @(posedge clk) pend <= {pend[1] && !b, pend[0] && !b, a};
always @* assert (!fail);                             // multiclock off: same-step check
```

Trace (`b` arrives only at sample 4):

| sample `k` | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| `a` | 1 | 1 | 0 | 0 | 0 |
| `b` | 0 | 0 | 0 | 0 | 1 |
| `pend` before the sample (bits 2,1,0) | 000 | 001 | 011 | 110 | 100 |
| `fail` | 0 | 0 | 0 | **1** | 0 |

- The obligation launched at 0 can be satisfied at 1, 2 or 3. `b` is low at all three, so it **fails at sample 3**, the earliest possible point.
- The obligation launched at 1 is still open at sample 4 with age 3, and `b = 1` satisfies it.

This is the monitor used by the Yosys contract (`tests/functional_tests/yosys_contract/fixtures/monitor_window.sv`), where SBY reports the failure at step 3.

### Example 2: `a |-> b ##1 c`

An obligation can fail **at its trigger** (`b` low) or one sample later (`c` low after `b`). One slot of age 1 remembers "`b` seen, `c` due now":

```systemverilog
logic w = 1'b0;                                       // age-1 obligation waiting for c
assign fail = (a && !b) || (w && !c);
always @(posedge clk) w <= a && b;
```

| sample `k` | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| `a` | 1 | 0 | 1 | 0 |
| `b` | 0 | 0 | 1 | 0 |
| `c` | 0 | 0 | 0 | 1 |
| `w` before the sample | 0 | 0 | 0 | 1 |
| direct `fail` | **1** | 0 | 0 | 0 |

The obligation from sample 0 fails **at sample 0**. The one from sample 2 matches at sample 3.

### Example 3: why obligations of different ages are not merged

A tempting shortcut keeps a single frontier: one bit per automaton position, with every obligation merged into it. That is exact for *existential* contexts (an antecedent, a cover: "does some match exist?"). It is **wrong** for consequents, because a dead obligation can hide behind a live one.

Take `a |-> ##[1:2] b ##1 c`. Its positions are:
- `E1`: `b` may match now, or the wait continues;
- `E2`: `b` must match now;
- `EC`: `c` must match now.

| sample `k` | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| `a` | 1 | 1 | 0 | 0 | 0 |
| `b` | 0 | 1 | 0 | 1 | 0 |
| `c` | 0 | 0 | 0 | 0 | 1 |

- The obligation from sample 0 needs `b@1, c@2` or `b@2, c@3`. Both alternatives are gone at sample 2, so it **fails at 2**.
- The obligation from sample 1 needs `b@2, c@3` or `b@3, c@4`. It succeeds with `b@3, c@4`.

| after sample | merged frontier | per-age slots |
|---|---|---|
| 0 | {E1} | age 1: {E1} |
| 1 | {E1, E2, EC} | age 1 (from 1): {E1}; age 2 (from 0): {E2, EC} |
| 2 | {E2}: **not empty, failure missed** | age 2 (from 0): {} → **fail**; age 2 (from 1): {E2} |

At sample 2 the merged frontier still contains `E2`, but that `E2` belongs to the *younger* obligation. The merged representation cannot tell that the older obligation has no live position left. Per-age slots can, so the direct strategy uses them.

### Size of the canonical direct monitor

The canonical direct monitor is easy to check by inspection, but it isn't always small. A consequent whose obligations live at most `D` samples needs up to `D` slots, each with up to one bit per automaton position. Two things shrink it:
- **Position pruning.** Only `(age, position)` pairs that can be occupied are registered: slot `a` stores the letters reachable from the start letters in exactly `a` steps, and the antecedent register those reachable in one or more steps (`temporal.machine.slot_reach`, `antecedent_reach`).
  - The other bits are provably always 0 (by induction over `step`), so this is exact by construction and needs no equivalence proof.
  - The [Layer 2 tests](validation.md#3-the-evidence) check the invariant on generated properties and traces; [Layer 3](validation.md#3-the-evidence) checks the generated hardware against every trace table and expression.
  - It makes the direct monitor linear in the window length:

    | Property | Unpruned bits | Registered bits |
    |---|---|---|
    | `a \|-> ##[1:3] b` | 12 | 5 |
    | `a \|-> ##4 b` | 20 | 4 |
    | `a \|-> ##[1:32] b` | 1056 | 63 |
    | `a ##1 b \|-> c [*2:4] ##1 d` | 22 | 7 |
- **Representation optimization** (see below): counters, windows and shift registers. Each must be proven equal to the canonical monitor.

---

## The delayed strategy (lookback)

The monitor keeps a **history** of sampled values (shift registers) and judges each obligation once, at a fixed **judgement point** `J` samples after its trigger. It looks back over the stored values to see whether some match of the consequent existed.

### Example 1 again: `a |-> ##[1:3] b`

The judgement point is age 3. The monitor stores `a` three samples deep and `b` two samples deep:

```systemverilog
logic [2:0] a_h = '0;   // a_h[i] = a at k-1-i
logic [1:0] b_h = '0;   // b_h[i] = b at k-1-i
assign fail = a_h[2] && !b_h[1] && !b_h[0] && !b;      // a@(k-3), no b at k-2, k-1, k
always @(posedge clk) begin
    a_h <= {a_h[1:0], a};
    b_h <= {b_h[0], b};
end
```

On the trace of Example 1 this fails at **sample 3** too. For this property the judgement point coincides with the earliest failure, because an obligation can only fail at its last chance. Here the delayed monitor is **exact and latency-free**, but uses 5 state bits against the direct monitor's 3.

### Example 2 again: `a |-> b ##1 c`

The natural judgement point is age 1: `fail = a@(k-1) && !(b@(k-1) && c@k)`. On the trace of Example 2 it reports the failure of the obligation from sample 0 at **sample 1**, one sample after the direct monitor:

| sample `k` | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| direct `fail` | **1** | 0 | 0 | 0 |
| delayed `fail` | 0 | **1** | 0 | 0 |

That latency is not allowed. A BMC run of depth 1 (sample 0 only) misses this failure, and counterexamples get longer. A delayed monitor for this property would need an extra *early tap* (`a && !b` checked at the trigger), and at that point it is the direct monitor again.

### Example 4: `a |-> ##4 b`, where the strategies coincide

An obligation can fail only at age 4, and only one position exists per age. The direct monitor's slots form a 4-deep shift register of "still open" bits. Because nothing can discharge an obligation before age 4, that register holds exactly the delayed history of `a`:

```systemverilog
logic [3:0] a_h = '0;
assign fail = a_h[3] && !b;                            // a@(k-4) and no b now
always @(posedge clk) a_h <= {a_h[2:0], a};
```

Both strategies produce the same 4 bits and report at the same sample. This is the classic fixed-delay checker.

### Example 5: `a |-> ##[1:32] b`, a compressed direct form

The canonical direct monitor has 32 one-bit slots. Every open obligation is discharged by the same event (`b` at age ≥ 1), so the **oldest** open obligation is the only one that can fail next. An exact compressed form stores just that obligation's age:
- one bit, `open`, saying whether any obligation is open;
- a 6-bit counter `age` of the oldest open obligation.

Its updates are:
- `b` discharges every open obligation of age ≥ 1;
- `a` opens a new obligation, which becomes the oldest when none was open;
- `fail` = open, `age = 32`, and `!b`.

That is 7 state bits instead of 32. A delayed monitor would need 32 bits of `a` history plus a record of `b` over 31 samples. This kind of rewrite belongs to the optimizer (`optimize/forms.py`), not to the canonical compiler.

**Caveat: exact only up to the first failure.** When the oldest obligation fails, the counter has lost the age of the next-oldest one. The compressed form therefore agrees with the canonical machine **up to and including the first failure**, and not after it.

That is enough for `assert` and `assume`:
- a trace that violates an assertion is a counterexample at its first failure;
- a trace that violates an assumption is excluded from that point on.

It is **not** enough wherever later outcomes matter, such as a `cover` or `.triggered` built on the same obligations, or diagnostics that count failures. There, the optimizer must keep the canonical form.

### Example 6: `a |-> b ##[1:2] c`, a frontier inside a slot

The obligation fails at age 0 if `b` is low, and at age 2 if `c` was low at ages 1 and 2. Direct uses two one-bit slots, `w1` and `w2`; the failure logic is `(a && !b) || (w2 && !c)`. A delayed monitor judging at age 2 would report the age-0 failure two samples late, so it would need the early tap again. In general, delayed is latency-free only when **every way an obligation can fail is decided at the judgement point**.

---

## `disable iff` in both strategies

- **Direct:** the disable condition **asynchronously clears every slot**, and suppresses `fail` while it is active. The clear is asynchronous because IEEE `disable iff` uses current values, so a pulse between two clock edges must abort live obligations. This is verified in the Yosys contract, point 6.
- **Delayed:** the history alone cannot tell whether a disable happened during an obligation's lifetime. It needs an additional "disabled since trigger" history captured asynchronously, which grows the state. This is one more reason direct is canonical.

## Cover and vacuity

- **Direct:** a non-vacuous success is the sample at which some obligation's frontier accepts. `cover` of an implication counts only those. Vacuous successes (no trigger) are never covered.
- **Delayed:** a success is visible only at the judgement point, so delayed cover reports are subject to the same latency rule.

## When sva2yosys uses which

1. The temporal compiler always produces the **canonical direct** machine: per-age slots and per-slot frontiers, with no positions pruned beyond structurally impossible ones.
2. An optional optimization pass may replace parts of it with a **compressed direct** form (counter, window, shift register) or a **delayed** form, but only when both of the following hold:
   - (a) within its exactness scope (recorded per form, below), it reports every failure and every success at **the same sample** as the canonical machine;
   - (b) a formal **equivalence check** proves it. Both monitors are driven by the same free inputs in one SBY job, and `assert (fail_canonical == fail_optimized && success_canonical == success_optimized)` is proved with `abc pdr`.

   The equivalence is required over the whole trace whenever the outcomes are consumed after a failure. For a pure `assert`/`assume` it may be scoped to "up to and including the first failure", using a sticky `failed_before` register: `assert (failed_before || (fail and success equal))`. The selected scope is recorded with the optimization.
3. **Implemented forms** (`src/sva2yosys/optimize/forms.py`; generated by `codegen.systemverilog.monitor_module(machine, name, form)`):

   | Form | Applies to | Registered temporal bits | Exact at |
   |---|---|---|---|
   | `direct` | everything | pruned positions (`temporal.machine.slot_reach`) | every sample |
   | `window` | `T \|-> ##[m:n] b`, `1 <= m`, `b` one condition, after the compiler's normalisation (`a \|=> c` has trigger `a ##1 1` and the bounds of `c`; a sequence property has `T = 1`); not `cover`, not a negated consequent | `m + ceil(log2(n+1))` | every sample up to and including the first failure |
   | `history` | every machine with obligation slots, except a negated consequent (`not s`) | `slots x (1 + conditions the consequent reads)` | every sample |

   - **`window`.** It keeps the triggers of the last `m` samples and a counter holding the age of the oldest obligation that has reached its window. Every obligation inside its window waits for the same next `b`, so `b` discharges all of them at once, and the oldest one decides the next failure: `!b` at age `n`. When that oldest one fails, the age of the next oldest is lost, so the form is exact only up to the first failure. That is everything an `assert` or `assume` consumes; `cover` never uses it.
   - **`history`** (the *delayed frontier*). It keeps the triggers of the last `slots` samples and each consequent condition's value history over the same span; the history is never cleared, and disable clears the triggers. The frontier of each age is recomputed combinationally by running the automaton over that history. Failures are reported at the earliest failure, never at the window's end. It stores fewer bits when many positions are live at once (`a |-> ##[1:8] b ##[1:8] c ##[1:8] d`: 96 bits instead of 213), but its combinational logic is much larger than `direct`'s (see the measurements under rule 4), so it is only used when forced.
   - **How rule 2(b) is met.** sva2yosys runs no external tool, so it cannot prove each user property at generation time. Instead:
     - Each form is proven equivalent to `direct` within its exactness scope by SBY in the test suite (`tests/functional_tests/optimized_monitors`): parameter families, random properties, and mutants that must fail the check.
     - Every trace table runs through every applicable form (`tests/functional_tests/generated_monitors`, point 6).
     - With `--equivalence`, sva2yosys writes the proof job (`codegen.equivalence.equivalence_check`) for each optimised monitor it generates, so a user can prove their own monitors.

   **Hazard of late judgement.** A form that judges a failure later than the canonical machine is not just slower to report. If `disable iff` becomes true between the earliest failure and the late judgement, the failure is masked and lost, so a proof can pass that IEEE semantics would fail. Rule 2(a), same sample for every failure and success, excludes such forms. So does the convention that sampled-value history is never cleared by disable.
4. **Selection** (`optimize.forms.choose`, CLI `--strategy`):
   - `direct`, the default, turns optimisation off. It is the only form that reports every failure, so use it when every event matters, e.g. counting failures in simulation.
   - `auto` never picks `history`, and picks `window` only where it is expected to give less logic than `direct`: when `direct` needs at least `3 x window + 4` state bits (`optimize.forms.WINDOW_FACTOR`, `WINDOW_OFFSET`), e.g. `##[1:10]` and wider, `##[4:32]`; `direct` elsewhere. Registers alone are not the model size that matters. The window's counter costs a fixed amount of logic (an adder and comparators), so for short windows `direct` is smaller although it has more registers. `history` stores fewer bits but rebuilds every age's frontier from the stored history, so its logic grows with `slots x` (automaton size); in every measured case it is larger than `direct` and no faster to prove. The rule is a conservative fit to the gate counts below; `tests/functional_tests/optimized_monitors` (point 6, `--slow`, Yosys) checks on a family of windows that `auto` never picks more gates than `direct`, and that `history` has more gates than `direct`.
   - **Measurements.** *Size*: the monitor alone, Yosys 0.63, `prep; flatten; opt; techmap; opt -fast; aigmap; opt_clean; stat -json`: state bits (`state_bits`) / AIG gates (`$_AND_`, `$_NOT_`) / flip-flops left by Yosys / lines of SystemVerilog, without `disable iff`. Reproduced by the point-6 tests (`pytest --slow -s`, with `disable iff (rst)`, which adds gates to both forms). *Proof*: SBY `abc pdr` on a small design where the property holds (`b`, `c`, `d` delayed copies of `a`), one run each on a desktop PC, so indicative only and not reproduced by the tests.

     | Property | `direct` | `window` | `history` | `abc pdr`: direct / window / history |
     |---|---|---|---|---|
     | `a \|-> ##[1:4] b` | 7 / 43 / 4 / 120 | 4 / 72 / 4 / 58 | 8 / 99 / 7 / 137 | 0.6 / 0.6 / 0.6 s |
     | `a \|-> ##[1:16] b` | 31 / 199 / 16 / 312 | 6 / 119 / 6 / 58 | 32 / 1215 / 31 / 749 | 0.6 / 0.6 / 1.6 s |
     | `a \|-> ##[4:32] b` | 60 / 368 / 32 / 568 | 10 / 146 / 10 / 58 | 64 / 3724 / 60 / 2461 | 1.1 / 0.6 / 6.7 s |
     | `a \|-> b ##[1:4] c` | 7 / 50 / 4 / 125 | — | 12 / 110 / 11 / 144 | 0.6 / — / 0.6 s |
     | `a \|-> ##[1:4] b ##[1:4] c` | 26 / 236 / 22 / 189 | — | 24 / 976 / 21 / 284 | 0.6 / — / 0.6 s |
     | `a \|-> ##[1:8] b ##[1:8] c ##[1:8] d` | 213 / 2252 / 204 / 450 | — | 96 / 28900 / 90 / 1491 | 1.1 / — / 4.8 s |
     | `a ##1 b \|-> ##[2:6] c ##1 d[*1:3]` | 26 / 226 / 22 / 215 | — | 28 / 880 / 23 / 339 | 0.7 / — / 0.7 s |

     In the `##[1:n]` family without `disable iff`, `window` has fewer gates than `direct` from `n = 10` on (`##[1:8]`: 95 against 99; `##[1:10]`: 121 against 104); with `disable iff` from `n = 6`. `auto`'s rule switches at `n = 10`. The equivalence proofs (`--equivalence`) of the `history` monitors take 0.6 s to 8 s, except the three-range chain: 725 s.
   - `window` and `history` force that form wherever it applies (`direct` elsewhere).

   No form reports any event later than `direct`. Each monitor's header names its form and exactness scope.
5. The compiler emits the canonical direct machine with position pruning (`src/sva2yosys/temporal`): per sequence a *letter automaton*, a merged antecedent frontier, and `slots` per-age consequent frontiers. A letter is one sample of a match, i.e. a conjunction of conditions; `##0` fuses two letters into one; delays share one chain of always-true filler letters. `a |=> c` is compiled as `(a ##1 1) |-> c` (§16.12.7); `|=>` with an antecedent that admits an empty match is rejected. The per-sample `step` in `temporal/machine.py` is normative for the code generator.

## The per-attempt machine

Some properties need results per attempt rather than per obligation: nested implications, `not` of an implication, `or` of non-sequences, `if`/`else` with implication branches, covers of `and` and of implications whose antecedent has several match lengths ([`semantics.md`](semantics.md#92-attempt-results) §9.2 to §9.5). For them `temporal/attempts.py` builds a **per-attempt machine**: one instance of the root property per attempt age `0 … life(p) - 1`, each holding its own subattempts, compiled into a Boolean netlist (`temporal/netlist.py`: condition inputs, registers, AND/OR/NOT gates with hash-consing and constant folding). Only reachable state gets a register (a state bit that is constant 0 at an age is never registered) and registers outside the cone of the outputs are removed. The code generator emits the netlist mechanically: one wire per gate, one state vector cleared asynchronously by the disable condition, the same condition and history code as the canonical machine; the header says `form: attempt`.

**Selection** (`temporal.compile`, `compile_monitors`): an `assert`/`assume` is split into the conjuncts of a top-level `and` as before; a `cover` is never split. Each part uses the canonical machine when its shape is one that machine decides exactly (`machine_exact`: a sequence, `not s`, or an implication with consequent `s` or `not s`, and for a cover only when every antecedent match has the same length), and the per-attempt machine otherwise. The optimised forms apply to the canonical machine only: a per-attempt monitor is always `form: attempt`, whatever the strategy, and gets no equivalence job. Every shape the per-attempt machine supports reaches it (a `cover` only under the cover rule of [`semantics.md`](semantics.md#95-lowerings-machine-selection-and-the-cover-rule) §9.5); directives the canonical machine decides exactly keep it, so their monitors are byte-identical to before.

Model size (`attempt_size`: `life`, netlist registers, NOT/AND/OR gates) for some of the attempt-scope trace properties:

| Property | life | registers | gates |
|---|---|---|---|
| `a \|-> (b \|-> c)` | 1 | 0 | 6 |
| `not (a \|-> b)` | 1 | 0 | 4 |
| `if (e) (a \|-> b) else (c \|-> d)` | 1 | 0 | 17 |
| `a \|=> (b \|-> c)` | 2 | 1 | 8 |
| `(a \|-> b) or (c \|-> ##1 d)` | 2 | 5 | 22 |
| `not (a \|-> ##[1:2] b)` | 3 | 5 | 25 |
| `a \|-> (b \|-> ##2 c)` | 3 | 6 | 26 |
| `strong(a ##2 b) or weak(c ##2 d)` | 3 | 8 | 30 |
| `if (e) (a \|-> ##1 b) else (c \|-> ##2 d)` | 3 | 6 | 39 |
| `a ##[0:1] b \|-> (c \|-> ##1 d)` | 3 | 7 | 50 |
| cover `a ##[1:2] b \|-> c` | 3 | 4 | 25 |
| cover `(a \|-> b) and (c \|-> ##1 d)` | 2 | 6 | 25 |

An instance costs registers only for the ages at which it can still be pending, so properties decided at their first sample have no temporal register at all.

**Temporal operators.** `nexttime`, `always [m:n]` and `eventually [m:n]` get no machine construction of their own: `temporal.lower` rewrites them for their outcome before selection (`nexttime [n] p` to `(1 ##n 1) |-> p`, `always [m:n] p` to `(1 ##[m:n] 1) |-> p`, `eventually [m:n] p` to `not ((1 ##[m:n] 1) |-> not p)`, `[0]`/`[0:0]` to `p`; the strength is dropped, so weak and strong forms give the same monitor). The selection above then applies unchanged: `assert nexttime [n] s` and `assert always [m:n] s` are implications into a sequence and keep the canonical machine (and its optimised forms); a cover of `always [m:n] s` with `m < n` (several antecedent lengths), every `eventually` (a `not` of an implication) and every non-sequence operand use the per-attempt machine. The implicit window `1 ##[m:n] 1` is charged like any antecedent by the resource limits, so a huge window is rejected before construction (the selection itself only looks at range ends, `temporal.bounds`): `assert nexttime [1000000] b` exceeds the default expansion budget (16384 written letters) and is rejected with a located message, as is `a |-> ##1000000 b`. The lowered implication's nonvacuity is that of any offset, not the offset-ordered one of [`semantics.md`](semantics.md#92-attempt-results) §9.2; the cover rule keeps covers where this differs out.

Model size of some temporal-operator properties (canonical: `machine_size` temporal bits; per-attempt: `attempt_size`):

| Property | machine | size |
|---|---|---|
| `nexttime b`, `s_nexttime [2] b` | canonical | 1, 2 temporal bits |
| `always [1:2] b`, `always [2:3] b` | canonical | 2, 3 temporal bits |
| `always [0:1] (a \|-> b)` | attempt | life 2, 2 registers, 14 gates |
| `eventually [1:2] b` (either strength) | attempt | life 3, 4 registers, 16 gates |
| `eventually [0:1] (((a ##2 b) or d) \|-> c)` | attempt | life 4, 12 registers, 70 gates |
| `a \|-> s_eventually [1:3] b` | attempt | life 4, 9 registers, 46 gates |
| cover `always [1:3] b` | attempt | life 4, 7 registers, 34 gates |
| `always [0:8] (a \|-> ##1 b)` | attempt | life 10, 42 registers, 213 gates |

## Unbounded antecedents

The antecedent frontier of the canonical machine is already one registered letter set, merged over every attempt: an antecedent match launches the same consequent obligation whatever its start, so for `assert` and `assume` only the *set of samples where some attempt matches* matters. An unbounded antecedent therefore changes nothing but the antecedent automaton, which gets loops (`temporal/automaton.py`):

- `l ##[m:$] r`: after the end of `l`, `max(m, 1) - 1` mandatory filler letters, then one filler letter that is its own successor, and the start letters of `r` as exits of the last mandatory filler and of the looping one (`##[0:$]` adds the `##0` fusion). If `r` can match empty, the last mandatory filler (or the end of `l`) and the loop letter also end a match.
- `s[*m:$]`: `max(m, 1) - 1` copies of `s` chained with `##1`, then a looping copy whose final letters lead back to its start letters (`s[+]`); `[*0:$]` only adds the empty match.
- `b[->m:n]` is `(!b[*0:$] ##1 b)[*m:n]` and `b[=m:n]` is `b[->m:n] ##1 !b[*0:$]` (§16.9.2, `ir.sequence.goto_expansion`/`nonconsecutive_expansion`), built with the two rules above.

Example: `a ##[1:$] b |-> c` has the antecedent letters `a` (start), a looping `1` and `b` (accepting), 3 letters; its antecedent frontier register is 2 bits (the letters that follow a start letter), and it has no obligation slot (`c` is decided at the trigger sample): a `direct` monitor of 2 temporal bits. Sizes (`machine_size`): `b[->2] |-> c` 4 antecedent letters and 4 temporal bits; `a[*1:$] ##1 b |=> c` 3 and 3; `b[=2] ##1 c |-> d` 6 and 6; `always b` 4 letters, 2 bits; `always [2:$] (a |-> ##1 b)` 4 letters, 1 obligation slot, 4 bits.

Unbounded `always [m:$] p` is `(1 ##[m:$] 1) |-> p`, flattened into the implication when `p` is an implication (`w |-> (a |-> c)` is `(w ##0 a) |-> c`), so it is the same machine with a looping window antecedent (`always b`: the window fuses `1 ##0 1` with the loop; one trigger at every sample).

The optimised forms (`window`, `history`) do not apply: `state_bits` returns `None` for them, `--strategy auto` selects `direct`, and `monitor_module` refuses a forced optimised form with "the window form does not apply to an unbounded antecedent (direct only)". The slot machine is unchanged and still bounded: `obligation_slots` refuses an unbounded consequent, and validation rejects it. A `cover` with an unbounded part that is not one of the shapes of the covers of unbounded sequences (below) would need the per-attempt machine, which is bounded and never receives an unbounded part: `compile_attempt_machine` refuses it.

## Unbounded consequents: the configuration-set machine

A weak unbounded consequent has no age bound, so the slot machine cannot hold it, and merging all obligations into one frontier is wrong (Example 3). The configuration-set machine (`temporal/configurations.py`) merges obligations only when their **complete frontiers are identical**: identical frontiers fail, match and die at the same samples, so this is exact for every output. A *configuration* is such a frontier, a state of the subset construction of the consequent automaton. The monitor has one register bit per statically reachable configuration, `cfg_q[k]` = "some live obligation is in configuration `k`", plus the antecedent frontier of the canonical machine. At each sample every configuration (and configuration 0, the start frontier, when the antecedent triggers) computes its held letters, whether it matched (then it is discharged and `success` is set), whether it failed (no held letter has a successor and none matched), and which configuration its successor frontier equals; `cfg_d[m]` collects the transitions into `m`. `disable iff` clears everything.

Example: `a |-> b[*1:$] ##1 c` has the letters `b` (looping) and `c`, and two configurations: `{b}` (the start) and `{b, c}`. An obligation in `{b}` with `b` moves to `{b, c}`; in `{b, c}` it matches when `c` holds and otherwise moves to `{b, c}` again (when `b` holds) or fails. On `a = 1 1`, `b = 1 0`, `c = 0 1`, the obligation of sample 0 is in `{b, c}` at sample 1 and matches, while the one of sample 1 is in `{b}` and fails (b is low) in the same sample; a shared frontier `{b, c}` would let `c` satisfy both (`tests/functional_tests/reference_semantics/fixtures/unbounded_consequents.trace`). Model size (`config_size`): this property has 2 configurations and 0 antecedent bits (2 temporal bits); `a |-> ##[1:$] b` has 2 configurations and 2 temporal bits, `a |=> b[->2]` 3 configurations and 4 temporal bits (1 antecedent bit); `a |-> ##[1:$] b ##n c` has `2^n + 1` (the register is exponential in the tail: budget 256, so `n <= 7`).

The configurations are found statically by treating each group of letters with equal guards as independently satisfiable (an over-approximation: an unreachable configuration costs a register, never a verdict). The optimised forms do not apply (`auto` selects `direct`). `Budgets.configurations` bounds the count; the letters estimate is checked first, before any range is iterated.

**Covers of unbounded sequences.** No representation of its own: a `cover` of a strong unbounded sequence, or of an implication with a bounded single-length antecedent into one, compiles to the same `ConfigMachine` as the corresponding `assert` and consumes its `success` output: an obligation is discharged at the first sample at which a held letter is accepting, so each attempt is counted once, at its first match; an attempt whose frontier dies counts nothing; `disable iff` clears the registers and silences the output. Merging obligations of different attempts that are in identical configurations does not change this bit (they match at the same samples, and the cover reports one event per sample). With several antecedent lengths one attempt has several obligations that must all succeed, which this merge cannot express (the bounded case uses the per-attempt machine), so those and unbounded antecedents are rejected. Model size (`config_size`, hand-derived in `tests/functional_tests/temporal_machine/test_cover_unbounded_machine.py`): `cover property (a ##[1:$] b)` 2 configurations (`{a}`, `{loop, b}`) and 0 antecedent bits, `a |-> ##[1:$] b` 2 and 0, `a |=> b[*1:$] ##1 c` 2 and 1, `b[->2]` 3 and 0, `a[*2:$] ##1 b` 3 and 0, `b[=2] ##1 c` 6 and 0. A weak unbounded sequence (explicit `weak(...)`, or an `until`) is rejected in a cover: see [`docs/semantics.md`](semantics.md#96-unbounded-sequences-and-properties) §9.6.

**Weak `until` / `until_with`.** No representation of its own: `temporal.lower.until_sequence` writes `p until q` as the weak sequence `p[*0:$] ##1 q` and `p until_with q` as `p[*0:$] ##1 (p && q)` (the `&&` is an ordinary condition of the lowered property, so the first-sample rule and the condition table see it), so an until in a consequent, as the whole property or under `always` is a weak unbounded sequence and gets the configuration-set machine. `a |-> b until c` has one configuration (the loop letter `b` and the accepting letter `c` held together) and no antecedent bit; `a |=> b until c` one configuration and one antecedent bit; `always (a |-> b until c)` one configuration and two antecedent bits. The strong forms are `strong(p[*0:$] ##1 q)` (below).

**Strong liveness.** A strong unbounded consequent of an `assert` (`strong(a ##[1:$] b)`, `s_eventually [m:$] p` lowered to `strong(1[*m:$] ##1 p)`, `s_until`, `s_until_with`) compiles to the same `ConfigMachine` with its `liveness` field set (`temporal/liveness.py`). Its monitor text is the weak one plus one block inside `` `ifdef SVA2YOSYS_LIVE `` (no macro, no extra logic: the `prove` task sees the weak monitor exactly), `busy` being the liveness signal and the check `always @(posedge clk) assert property (s_eventually !busy);` under `CHECK`:

- **Pending bit (one configuration): no extra register.** `busy` is `cfg_q[0]`, the configuration register that the safety part already has. `a |-> s_eventually b`: 1 configuration, 1 register in total.
- **Witness (several configurations): one register per consequent letter**, `wit_q`, plus the free choice `pick = $anyseq` (a wire, not a register). `a |-> strong(##[1:$] b)` has 3 letters (the start, the loop filler, `b`), so 3 witness registers on top of its 2 configuration bits. The witness frontier is held by the same letter guards and successor logic as a configuration (`frontier.held`, `frontier.next_bits`), starts from the start frontier when `trigger & pick` at a sample where `busy` (any `wit_q` bit) was 0, ends on an accepting held letter and never re-picks in the sample it ends.

**Negated configuration machine.** `not S` of an unbounded sequence (the whole property, or the consequent of `|->`/`|=>`) compiles to the `ConfigMachine` of `S` with `negated = True`. The configurations, the registers and the step are those of `S` unchanged; only the two outputs swap: `success` is the OR of the configurations' failure wires (a dead obligation discharges `not S`) and `fail` the OR of their match wires (a match of `S` refutes it). The monitor of a non-negated machine is byte-identical. The liveness part is the block of *Strong liveness* above, unchanged: a `not` obligation is pending exactly while the `S` obligation is, so `busy` is the same pending bit or witness, and it is present exactly when `not S` is strong, that is when `S` is weak (`not (a ##[1:$] b)`: 2 configurations, witness; `not (a until b)`: 1 configuration, pending bit). `not strong(S)` has the swapped outputs and no liveness block. `never_fails` returns `False` for a negated machine.

The liveness part is checked in the SBY `live` task (`mode live`, `engines aiger suprove`, one read script `sva2yosys_read_live_<part>.ys` for each liveness part), the safety part in `prove`; a strong directive holds when `prove` and the `live` task of each of its parts pass ([`docs/usage.md`](usage.md#strong-properties-the-prove-and-the-live-task)).

**Fairness assumptions.** A strong `assume`, including `not` of a weak unbounded sequence, uses the same configuration machine and safety assume. With at most one reachable configuration its liveness kind is `PENDING`, and its live block emits `always @(posedge clk) assume property (s_eventually !busy);` under `CHECK`. This is an exact fairness constraint on all obligations (case L1 of [`semantics.md`](semantics.md#96-unbounded-sequences-and-properties) §9.6). Several configurations would need a witness that constrains only the selected obligation, so compilation rejects such assumptions. No new IR nodes or registers are needed. Fairness is enabled only in `live`; `prove` is conservative and reached `cover` results are provisional, with a warning on each accepted cover in a run containing a fairness assumption ([`usage.md`](usage.md#fairness-assumptions-and-covers)).

## Resource admission

All forms share the range-independent preflight of the written property and the incremental construction counters (`temporal.budgets`). Before RTL construction, `codegen.resources.check_emission` checks the selected form: it charges the compact `direct` layout for its reachable per-age positions, the dense `direct` layout for full frontiers, `history` for each replay, and the configuration and per-attempt monitors for their emitted structure. The emission counter is a conservative reference allowance, and planning work is counted separately. Frontier evaluation, dead-state discovery and per-attempt evaluation are linear in the positions and edges they visit: each automaton computes its incoming edges once, and the per-attempt machine caches structural frontiers by local age. Forcing `history` can therefore reject a property whose `direct` monitor fits. A rejection never changes temporal semantics or replaces `$` by a finite limit. Explicit policy overrides survive compilation and rewriting; the defaults are in [`usage.md`](usage.md#compiler-resource-limits---max-expansion---max-work---max-state-bits---max-emission) and the measured controls in [`validation.md`](validation.md#6-known-limits-of-the-evidence). Accounting does not change the text of an admitted monitor.

## Where the check is placed

The `assert (!fail)` statement itself depends on SBY's clock mode, not on the strategy (Yosys contract, point 7):
- **`multiclock off`**: `always @* assert (!fail);` reports in the same step as the sample. A clocked assert would report one step late.
- **`multiclock on`**: `always @(posedge clk) assert (!fail);` is evaluated only at sampling edges. A combinational assert would fire spuriously between edges.

**Constant conditions.** No machine prunes a constant-false condition: the automaton builds `Bool(Const 0)` as a letter guarded by the condition table's entry for the constant (`bounds` gives it length 1), so slot counts, `life`, configurations and the antecedent frontier treat it as any letter, and the generated monitor evaluates `1'b0` at that letter's sample. Only structural emptiness (a letter from which no accepting letter is reachable, e.g. an `intersect` of fixed lengths that never meet) is pruned. A constant-true condition keeps no guard (it holds at every sample). The `window` form still requires a non-constant final condition, so it is unaffected. A directive without a constant-false condition gives the same monitor text as before.

## Expression emission

All forms use the same Boolean-join and concatenation helpers: at most eight operands per group, with exactly sized unsigned combinational intermediates for wider groups. Local expression syntax stays within depth 32, independently of temporal ranges, which prevents parser-stack failures from large flat expressions. Concatenations keep their bit order and unsigned result, including signed singleton operands; an empty OR is zero and an empty AND is one. This changes neither the temporal registers nor the event timing.
