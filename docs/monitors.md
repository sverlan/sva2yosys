# The generated monitors

This page shows, briefly, what the monitor that sva2yosys generates for an assertion looks like, and what its temporal state does. The reasons for each representation, with worked examples, are in [`strategies.md`](strategies.md); the rules of the semantics are in [`semantics.md`](semantics.md); how to read a monitor next to your own assertion is in [Reading a monitor](#reading-a-monitor), below.

Every concurrent assertion is replaced by an instance of a **monitor module** `__sva2yosys_p<N>` (the instance is `__sva2yosys_i<N>`), written with all the others to `sva2yosys_monitors.sv`. A monitor is plain synthesizable SystemVerilog: registers, `assign`s and one check statement. It is checked at every clock edge of the assertion, with the values sampled at that edge.

## What every monitor has

```systemverilog
// sva2yosys monitor: assert a_resp
// source: props.sv:12:5
// as written: a_resp: assert property (a |-> ##[1:3] b);
// clock: posedge clk
// disable iff: rst
// form: direct (exact at every sample)
module __sva2yosys_p0 #(parameter bit CHECK = 1'b1) (
    input  logic clk,
    input  logic [0:0] i0,  // rst  (one port per design signal the assertion reads)
    input  logic [0:0] i1,  // a
    input  logic [0:0] i2,  // b
    output logic fail,      // the assertion fails at this sample
    output logic success    // a non-vacuous success at this sample (covers)
);
    logic valid = 1'b0;                   // 0 at the first sample, 1 afterwards
    always @(posedge clk) valid <= 1'b1;

    // conditions: one wire per expression, with its source as a comment
    logic [0:0] e1;  assign e1 = i1;      // a
    logic c1;        assign c1 = (|e1);   // the condition as a Boolean
    logic dis;       assign dis = c0;     // the disable iff condition

    // temporal state: depends on the case, see below. Cleared by dis, asynchronously.

    assign fail    = !dis & (...);        // exact, and already suppressed while disabled
    assign success = !dis & (...);

    generate
        if (CHECK) begin : g_check
            always @* assert (!fail);     // assume (!fail) for an assume, cover (success) for a cover
        end
    endgenerate
endmodule
```

- **Header.** Comments before the module name the directive and its label, its source position (`// source:`), the assertion as written (`// as written:`), the effective clock and `disable iff` condition after defaults, and the form (`// form:`).
- **Ports.** `clk`, one input `i<k>` per design signal (in order of first use; the comment names it) and the outputs `fail` and `success`. The names are generic on purpose: one monitor serves all the elaborated instances of its assertion that give the same monitor (when parameter values require different monitors, the rewrite selects a variant at the assertion site, see [Source rewriting](architecture.md#source-rewriting)), so it cannot carry the names of one of them. A signal can also be a part-select, a struct member, a hierarchical path or another expression with no plain name, and a name taken from the design could collide with the monitor's own `clk`, `fail`, `success`, `valid` or `CHECK`. Generic names avoid all of that, and keep the monitor text identical for identical properties.
- **Conditions.** Each expression of the property is a wire `e<N>`, typed as in the SystemVerilog source so that sizing never changes a value; a comment gives the expression. `c<k>` is its truth value (`|e`).
- **History.** `valid` (0 at the first sample, 1 afterwards) and the registers behind `$past`, `$rose`, `$fell`, `$stable` and `$changed` are *never* cleared by `disable iff`, because the sampled values continue while the assertion is disabled (IEEE 1800-2017 16.12). The history registers have no initial value; `valid` masks them at the first sample, so their first-sample results never depend on them.
- **`disable iff`.** All the temporal state is cleared asynchronously by `dis` (`always @(posedge clk or posedge dis)`), so a disable between two edges would also cancel the pending obligations. In the formal model used here (SBY `multiclock off`) signals change only at the steps, so the clear acts at the step in which the condition is true ([`semantics.md`](semantics.md#7-disable-iff) §7). `fail` and `success` are zero while `dis` holds.
- **The check.** One statement, `always @*`, so that a failure is reported at the same step as the sample that causes it (`multiclock off`; see [`strategies.md`](strategies.md#where-the-check-is-placed)).
- **`CHECK`.** A parameter, default 1, that decides whether the check statement exists. With `CHECK = 1` the monitor is an assertion (or assumption, or cover). With `CHECK = 0` the statement is not generated and the monitor only drives `fail` and `success`. The instances written by `-o` use the default. Set it to 0 only to read the outputs yourself: the `--equivalence` jobs do (their miter compares the outputs of two monitors, neither of which may assert), and the confirmation of a cover under fairness assumptions needs it for the other assertion monitors ([`usage.md`: Fairness assumptions and covers](usage.md#fairness-assumptions-and-covers)). `CHECK` is the current way of separating the checking from the observing of a monitor, and it may change in a future version (for example by splitting a monitor into a part that drives `fail` and `success` and a part that checks): do not rely on it in scripts of your own that must keep working across upgrades.

## The temporal state, by case

To decide at each sample whether the assertion fails or succeeds, a monitor must remember what happened at earlier samples. This memory is the **temporal state**: registers that record, for every *obligation* still open (an antecedent match waiting for its consequent), where it is in the consequent. What the state looks like depends on the property. A bounded consequent has a finite number of ages and positions to track. An unbounded one has no maximum age, so it needs a different representation. Some shapes, such as nested implications, must follow every attempt separately.

sva2yosys therefore has several **forms** of monitor. The form of each monitor is named in its header (`// form:`) and printed by `sva2yosys` in check mode (`form=`). The table gives, for each kind of property, the form, what its registers hold, and where the reasoning is explained. For bounded properties the form can be chosen with [`--strategy`](usage.md#monitor-forms---strategy---equivalence); for the others there is no choice. The sections below describe the main cases, then cover and `initial`.

| Case | Form (the `// form:` line) | State | Where explained |
|---|---|---|---|
| bounded properties (the default) | `direct` | one bit per (age, position) of the obligations | below, [`strategies.md`](strategies.md#the-direct-strategy-canonical) |
| the same, with `--strategy window` or `history` | `window`, `history` | a counter of the oldest obligation, or a shift register of the sampled values | [`strategies.md`](strategies.md#the-delayed-strategy-lookback) |
| nested implications, `if`/`else`, `or` of properties, `not` of an implication, … | `attempt` | one bit per (age, path, part, index) of the property tree | [`strategies.md`](strategies.md#the-per-attempt-machine), [`semantics.md`](semantics.md#9-weak-and-strong-property-operators) §9 |
| unbounded sequences (`##[1:$]`, `[*1:$]`, `until`, …) | `configurations` | one bit per reachable *frontier* of the consequent | below |
| strong properties (`s_eventually`, `strong(...)`, …) | `configurations` + a live block | the same, plus `busy` | below |
| `cover` | any of the above | `success` instead of `fail` | below |

### Bounded: `direct`

An *obligation* is created when the antecedent matches. It then moves through the letters of the consequent; each sample it is either accepted (the consequent matched), failed (no letter can follow) or still alive.

```text
trigger  = antecedent matches now
state    = for every age a in 0..max_length:  a set of positions the obligation of age a may be in
each sample, for every age a:
    accept(a) = some position of age a holds its condition and ends the consequent
    next(a+1) = positions reached from the positions of age a that hold, if not accepted
    fail(a)   = age a has obligations, none accepted, none alive afterwards
state   <= next                      (cleared by dis)
fail     = OR of fail(a)             success = OR of accept(a)
```

Obligations of different ages are kept apart (a failure at age 2 does not hide a success at age 3), which is why the state grows with the number of reachable (age, position) pairs and not with the number of ways to match. Pruned positions are never registered. The generated text uses compact layouts of this state (for example a packed chain or shift register) where the reachable positions allow it; `sva2yosys` without `-o` shows the form and its size. See [`strategies.md`](strategies.md#example-1-a---13-b), Example 1 to Example 3.

### Unbounded: `configurations`

When the consequent has no upper bound there is no maximum age. The monitor then keeps one bit per **configuration**: a set of consequent positions that an obligation can be in. All obligations in the same configuration behave identically, so one bit is enough.

```text
cfg_q[k]   = some live obligation is in configuration k
act(k)     = cfg_q[k] | (trigger, for the start configuration)
accept(k)  = act(k) and the consequent can end here
fail(k)    = act(k) and not accepted and no successor letter holds
cfg_d[m]   = set by every configuration whose successor frontier is exactly m
fail       = OR of fail(k)         success = OR of accept(k)       (cleared by dis)
```

A weak unbounded consequent such as `a |-> ##[1:$] b` can keep a live match for ever, so `fail` never rises; the tool warns "this assert can never fail". Only the strong forms below turn pending obligations into failures.

### Strong properties and liveness

A strong unbounded consequent (`s_eventually b`, `strong(a ##[1:$] b)`, `s_until`, …) uses the same configurations, plus one block that exists only when the monitor's own macro is defined (`SVA2YOSYS_LIVE_p0` for the monitor `__sva2yosys_p0`; `SVA2YOSYS_LIVE` defines it for every monitor, and so does `SVA2YOSYS_LIVE_FAIR` for a strong `assume`):

```systemverilog
`ifdef SVA2YOSYS_LIVE
`define SVA2YOSYS_LIVE_p0
`endif
`ifdef SVA2YOSYS_LIVE_p0
    logic busy;                                  // an obligation is pending
    assign busy = cfg_q[0];                      // or |wit_q for several configurations
    generate if (CHECK) begin : g_live
        always @(posedge clk) assert property (s_eventually !busy);   // assume property for a strong assume
    end endgenerate
`endif
```

- With one configuration, `busy` is the configuration register. With several, the monitor follows one obligation chosen by the free value `pick = $anyseq` (a *witness*): `wit_q` is its frontier, and `busy` is `|wit_q`.
- The assertion `s_eventually !busy` says that the pending state does not last for ever. A strong `assume` is written `assume property` and becomes a **fairness constraint** (`FLAVOR "fair"`).
- Without the macro the text is the weak machine, which is what the `prove` task of SBY reads (the safety part). The `live` task reads the file with the macro of the monitor's own liveness part switched on. How to run the two kinds of task is in [`usage.md`](usage.md#strong-properties-the-prove-and-the-live-task), *Strong properties*.
- The liveness block uses `assert property (s_eventually …)` and `$anyseq`, which only `read_verilog -formal -sv` accepts: `--yosys-frontend slang` rejects such an assertion ([`usage.md`](usage.md#binds-and-the-yosys-frontend---yosys-frontend), *Binds and the Yosys frontend*).

### Cover

A `cover` uses the same machines and reads `success` instead of `fail`: `cover (success)`, true at the samples where a non-vacuous match completes. A vacuous success (no antecedent match) is not covered. A cover of an unbounded strong sequence is the same configurations machine as the assertion.

### `initial`

An assertion in an `initial` procedure has one attempt, at the first sample: its enabling condition is `!valid`.

## Reading a monitor

Open `sva2yosys_monitors.sv`. A monitor takes its design signals through ports named `i0`, `i1`, … in order of first use, and its internal condition wires are named `e0`, `e1`, …. To map them back to your assertion:

- the header of each module quotes your assertion as written, its source position, its clock and its `disable iff` condition, and names the form;
- the comment after each port names the signal it stands for (`input logic [0:0] i1,  // level`);
- the `assign` line of each condition wire ends with a comment giving the expression as written over your signal names (`assign e4 = e1 && e3;  // (level && (!previous_level))`); a comment longer than 80 characters is cut with `...`.

The comments never change the behaviour of the monitor. Run `sva2yosys` without `-o` to see, for each assertion, the form, the number of slots and the state bits it needs (`bits=`), before generating anything.
