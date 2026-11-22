# SVA checked examples

<!-- The code blocks and the expected-outcome lines are checked against the source files and
the catalog. Do not edit a code block by hand: change its source file and run
`uv run python tests/support/documentation_examples.py --write`. -->

Complete, runnable examples for the constructs described in the
[SVA reference](sva-reference.md); each topic below matches a section of it. The
[status matrix](supported-sva.md) says which constructs are supported.

**Each code block is copied from the linked source file by a script, and the test suite
checks that the block matches the file and that the compiler gives the stated outcome, so
the examples cannot drift from the compiler.**

These complete sources are checked in both CLI modes by the
[documentation example tests](../tests/functional_tests/documentation_examples/test_sva_reference_examples.py).
The catalog records expected compiler outcomes; **accepted** means the compiler
supports the property, not that the design satisfies it. The four behavioral controls
also run through SBY to distinguish a proof, an assertion failure, a reached cover
and the safety/liveness results of a strong assertion.

For an example with source `FILE` and top `TOP`, run
`uv run sva2yosys FILE --top TOP` to check it, then add `-o BUILD` to generate.
The binding example also needs `-y examples/handshake/rtl`; the strict X-check
example needs `--x-checks=reject`. Paths are relative to the repository root.
Expected exits, counts, warning fragments and diagnostics are
checked against the catalog as well as actual CLI output.

Maintain an example by changing its source and hand-derived catalog expectation,
then run `uv run python tests/support/documentation_examples.py --write` to refresh
marked snippets and run `uv run pytest tests/functional_tests/documentation_examples`.
This checks documentation accuracy; the [semantic evidence](validation.md) remains
the basis for language support. The examples do not replace the trace corpus.

## defaults

Default clock and disable, argument-free named declarations, assertion and cover.

[Source](../tests/functional_tests/documentation_examples/fixtures/defaults.sv); top `top`.

<!-- sva-expectation: defaults -->
Check exit: 0. Generation exit: 0. Directives: 2 accepted, 0 skipped, 0 rejected.

<!-- sva-example: defaults -->
```systemverilog
module top(input logic clk, rst_n, a, b);
    default clocking @(posedge clk); endclocking
    default disable iff (!rst_n);
    sequence response; ##[1:3] b; endsequence
    property handshake; a |-> response; endproperty
    check_response: assert property (handshake);
    reached: cover property (a ##1 b);
endmodule
```
<!-- /sva-example -->

## procedural

Enabling conditions in edge-clocked procedural blocks are preserved.

[Source](../tests/functional_tests/documentation_examples/fixtures/procedural.sv); top `top`.

<!-- sva-expectation: procedural -->
Check exit: 0. Generation exit: 0. Directives: 2 accepted, 0 skipped, 0 rejected.

<!-- sva-example: procedural -->
```systemverilog
module top(input logic clk, en, a, b);
    always @(posedge clk) begin
        if (en) check_enabled: assert property (a |=> b);
    end
    always_ff @(posedge clk) begin
        case (en)
            1'b1: reached: cover property (a ##1 b);
            default: ;
        endcase
    end
endmodule
```
<!-- /sva-example -->

## initial

One attempt at the first clock tick, rather than an attempt at every tick.

[Source](../tests/functional_tests/documentation_examples/fixtures/initial.sv); top `top`.

<!-- sva-expectation: initial -->
Check exit: 0. Generation exit: 0. Directives: 1 accepted, 0 skipped, 0 rejected.

<!-- sva-example: initial -->
```systemverilog
module top(input logic clk, a, b);
    initial once: assert property (@(posedge clk) a |=> b);
endmodule
```
<!-- /sva-example -->

## integral

Whole integral and packed values, ordinary vector selects and signed expressions.

[Source](../tests/functional_tests/documentation_examples/fixtures/integral.sv); top `top`.

<!-- sva-expectation: integral -->
Check exit: 0. Generation exit: 0. Directives: 3 accepted, 0 skipped, 0 rejected.

<!-- sva-example: integral -->
```systemverilog
module top(input logic clk, input int count,
           input logic [3:0] v, input logic signed [7:0] s);
    typedef enum logic [1:0] {IDLE, BUSY, DONE} state_t;
    state_t state;
    typedef struct packed {logic a; logic [2:0] b;} record_t;
    record_t record;
    types: assert property (@(posedge clk) count >= 0 && state != DONE);
    whole: assert property (@(posedge clk) record == v);
    operators: assert property (@(posedge clk)
        v[2] && v[3:2] == 2'b10 && (s >>> 1) <= s);
endmodule
```
<!-- /sva-example -->

## sampled

History depth and edge functions; all operands here are uninitialised four-state inputs.

[Source](../tests/functional_tests/documentation_examples/fixtures/sampled.sv); top `top`.

<!-- sva-expectation: sampled -->
Check exit: 0. Generation exit: 0. Directives: 4 accepted, 0 skipped, 0 rejected.

<!-- sva-example: sampled -->
```systemverilog
module top(input logic clk, a, b, input logic [3:0] v);
    past_one: assert property (@(posedge clk) $past(a) |-> b);
    past_two: assert property (@(posedge clk) $past(a, 2) |-> b);
    rising: assert property (@(posedge clk) $rose(a) |=> b);
    other_edges: assert property (@(posedge clk)
        $stable(v) || $changed(a) || $fell(b));
endmodule
```
<!-- /sva-example -->

## functions

Population count and one-hot constraints without sampled operands.

[Source](../tests/functional_tests/documentation_examples/fixtures/functions.sv); top `top`.

<!-- sva-expectation: functions -->
Check exit: 0. Generation exit: 0. Directives: 3 accepted, 0 skipped, 0 rejected.

<!-- sva-example: functions -->
```systemverilog
module top(input logic clk, input logic [3:0] v);
    population: assert property (@(posedge clk) $countones(v) <= 2);
    one: assert property (@(posedge clk) $onehot(v));
    zero_or_one: assume property (@(posedge clk) $onehot0(v));
endmodule
```
<!-- /sva-example -->

## inside

Uniform sized items, a wildcard item and an open range bound.

[Source](../tests/functional_tests/documentation_examples/fixtures/inside.sv); top `top`.

<!-- sva-expectation: inside -->
Check exit: 0. Generation exit: 0. Directives: 3 accepted, 0 skipped, 0 rejected.

<!-- sva-example: inside -->
```systemverilog
module top(input logic clk, input logic [3:0] v);
    uniform: assert property (@(posedge clk) v inside {4'd1, [4'd3:4'd7]});
    masked_item: cover property (@(posedge clk) v inside {4'b10??});
    open_bound: assert property (@(posedge clk) v inside {[$:4'd7]});
endmodule
```
<!-- /sva-example -->

## bounded-sequences

Same-sample and delayed concatenation, ranges and empty-capable repetition.

[Source](../tests/functional_tests/documentation_examples/fixtures/bounded-sequences.sv); top `top`.

<!-- sva-expectation: bounded-sequences -->
Check exit: 0. Generation exit: 0. Directives: 4 accepted, 0 skipped, 0 rejected.

<!-- sva-example: bounded-sequences -->
```systemverilog
module top(input logic clk, a, b, c);
    delays: assert property (@(posedge clk) a |-> b ##0 c ##2 b);
    ranges: assert property (@(posedge clk) a |=> ##[0:2] b);
    repeats: assert property (@(posedge clk) a |-> b[*2:3] ##1 c);
    empty: assert property (@(posedge clk) a |-> b[*0:2] ##1 c);
endmodule
```
<!-- /sva-example -->

## sequence-composition

Sequence union, conjunction, intersection, containment and throughout.

[Source](../tests/functional_tests/documentation_examples/fixtures/sequence-composition.sv); top `top`.

<!-- sva-expectation: sequence-composition -->
Check exit: 0. Generation exit: 0. Directives: 5 accepted, 0 skipped, 0 rejected.

<!-- sva-example: sequence-composition -->
```systemverilog
module top(input logic clk, a, b, c);
    alternatives: assert property (@(posedge clk) (a ##1 b) or c);
    both: assert property (@(posedge clk) (a ##1 b) and (a ##2 c));
    same_end: assert property (@(posedge clk) (a ##1 b) intersect (b ##1 c));
    contained: cover property (@(posedge clk) b within (a ##2 c));
    invariant: assert property (@(posedge clk) a throughout (b ##1 c));
endmodule
```
<!-- /sva-example -->

## property-composition

Property negation, composition, branch selection, nesting and a supported cover.

[Source](../tests/functional_tests/documentation_examples/fixtures/property-composition.sv); top `top`.

<!-- sva-expectation: property-composition -->
Check exit: 0. Generation exit: 0. Directives: 6 accepted, 0 skipped, 0 rejected.

<!-- sva-example: property-composition -->
```systemverilog
module top(input logic clk, a, b, c, d);
    negation: assert property (@(posedge clk) not (a ##1 b));
    conjunction: assert property (@(posedge clk) (a |-> b) and (c |=> d));
    disjunction: assert property (@(posedge clk) (a |-> b) or (c |=> d));
    selected: assert property (@(posedge clk) if (a) (b |-> c) else (c |=> d));
    nested: assert property (@(posedge clk) a |-> (b |=> c));
    nonvacuous: cover property (@(posedge clk) (a |-> b) and (c |=> d));
endmodule
```
<!-- /sva-example -->

## bounded-properties

Finite deadlines for weak and strong temporal operators.

[Source](../tests/functional_tests/documentation_examples/fixtures/bounded-properties.sv); top `top`.

<!-- sva-expectation: bounded-properties -->
Check exit: 0. Generation exit: 0. Directives: 6 accepted, 0 skipped, 0 rejected.

<!-- sva-example: bounded-properties -->
```systemverilog
module top(input logic clk, a, b);
    next_weak: assert property (@(posedge clk) nexttime [2] (a |-> b));
    next_strong: assert property (@(posedge clk) s_nexttime a);
    all_weak: assert property (@(posedge clk) always [0:2] a);
    all_strong: assert property (@(posedge clk) s_always [1:2] a);
    some_weak: assert property (@(posedge clk) eventually [0:2] a);
    some_strong: assert property (@(posedge clk) s_eventually [1:2] (a |-> b));
endmodule
```
<!-- /sva-example -->

## unbounded-antecedents

Unbounded antecedents and a flattenable unbounded always.

[Source](../tests/functional_tests/documentation_examples/fixtures/unbounded-antecedents.sv); top `top`.

<!-- sva-expectation: unbounded-antecedents -->
Check exit: 0. Generation exit: 0. Directives: 3 accepted, 0 skipped, 0 rejected.

<!-- sva-example: unbounded-antecedents -->
```systemverilog
module top(input logic clk, a, b, c);
    delay: assert property (@(posedge clk) a ##[1:$] b |-> c);
    repetition: assume property (@(posedge clk) a[*1:$] ##1 b |=> c);
    ongoing: assert property (@(posedge clk) always (a |-> ##2 b));
endmodule
```
<!-- /sva-example -->

## weak-unbounded

A missing deadline is not an eventuality requirement. The no_deadline assertion never fails and warns; the other obligations can fail.

[Source](../tests/functional_tests/documentation_examples/fixtures/weak-unbounded.sv); top `top`.

<!-- sva-expectation: weak-unbounded -->
Check exit: 0. Generation exit: 0. Directives: 6 accepted, 0 skipped, 0 rejected.
Warning fragment: `can never fail`.

<!-- sva-example: weak-unbounded -->
```systemverilog
module top(input logic clk, a, b, c);
    no_deadline: assert property (@(posedge clk) a |-> ##[1:$] b);
    repeated: assert property (@(posedge clk) a |-> b[*1:$] ##1 c);
    goto: assume property (@(posedge clk) a |=> b[->2]);
    nonconsecutive: assert property (@(posedge clk) a |-> b[=2] ##1 c);
    until_exclusive: assert property (@(posedge clk) a until b);
    until_inclusive: assume property (@(posedge clk) a until_with b);
endmodule
```
<!-- /sva-example -->

## unbounded-covers

These covers count finite nonvacuous matches, including the supported single-length antecedent form.

[Source](../tests/functional_tests/documentation_examples/fixtures/unbounded-covers.sv); top `top`.

<!-- sva-expectation: unbounded-covers -->
Check exit: 0. Generation exit: 0. Directives: 4 accepted, 0 skipped, 0 rejected.

<!-- sva-example: unbounded-covers -->
```systemverilog
module top(input logic clk, a, b, c);
    eventual_match: cover property (@(posedge clk) a ##[1:$] b);
    repeated: cover property (@(posedge clk) a[*2:$] ##1 b);
    goto: cover property (@(posedge clk) b[->2]);
    nonconsecutive: cover property (@(posedge clk) a |-> b[=2] ##1 c);
endmodule
```
<!-- /sva-example -->

## strong-forms

Strong assertions require both safety and liveness. Negation reverses the effective strength.

[Source](../tests/functional_tests/documentation_examples/fixtures/strong-forms.sv); top `top`.

<!-- sva-expectation: strong-forms -->
Check exit: 0. Generation exit: 0. Directives: 6 accepted, 0 skipped, 0 rejected.
Warning fragment: `5 liveness parts in one `live` task would report only the first verdict`.
Warning fragment: `5 liveness parts in one `live` task would report only the first verdict`.
Warning fragment: `5 liveness parts in one `live` task would report only the first verdict`.
Warning fragment: `5 liveness parts in one `live` task would report only the first verdict`.
Warning fragment: `5 liveness parts in one `live` task would report only the first verdict`.

<!-- sva-example: strong-forms -->
```systemverilog
module top(input logic clk, a, b);
    sequence_live: assert property (@(posedge clk) strong(a ##[1:$] b));
    eventual_live: assert property (@(posedge clk) s_eventually b);
    until_live: assert property (@(posedge clk) a s_until b);
    inclusive_live: assert property (@(posedge clk) a s_until_with b);
    negated_weak: assert property (@(posedge clk) not (a until b));
    negated_strong: assume property (@(posedge clk) not strong(a ##[1:$] b));
endmodule
```
<!-- /sva-example -->

## fairness

These strong assumptions have one configuration. The reached cover result ignores fairness and is provisional.

[Source](../tests/functional_tests/documentation_examples/fixtures/fairness.sv); top `top`.

<!-- sva-expectation: fairness -->
Check exit: 0. Generation exit: 0. Directives: 3 accepted, 0 skipped, 0 rejected.
Warning fragment: `provisional`.

<!-- sva-example: fairness -->
```systemverilog
module top(input logic clk, a, b);
    eventually_b: assume property (@(posedge clk) s_eventually b);
    until_b: assume property (@(posedge clk) a s_until b);
    provisional: cover property (@(posedge clk) a ##1 b);
endmodule
```
<!-- /sva-example -->

## variants

Elaborated parameter and genvar combinations select monitor variants.

[Source](../tests/functional_tests/documentation_examples/fixtures/variants.sv); top `top`.

<!-- sva-expectation: variants -->
Check exit: 0. Generation exit: 0. Directives: 4 accepted, 0 skipped, 0 rejected.

<!-- sva-example: variants -->
```systemverilog
module stage #(parameter N = 1)(input logic clk, a, b);
    for (genvar i = 0; i < 2; i++) begin: checks
        response: assert property (@(posedge clk) a |-> ##(N+i) b);
    end
endmodule
module top(input logic clk, a, b);
    stage #(.N(1)) short_delay(.*);
    stage #(.N(2)) long_delay(.*);
endmodule
```
<!-- /sva-example -->

## binding

The existing handshake example attaches a property module to the design by module bind.

[Source](../examples/handshake/props/handshake_props.sv); top `handshake`.

<!-- sva-expectation: binding -->
Check exit: 0. Generation exit: 0. Directives: 4 accepted, 0 skipped, 0 rejected.

<!-- sva-example: binding -->
```systemverilog
// Properties of the handshake, attached to every handshake instance by bind.
module handshake_props (
    input logic clk,
    input logic rst,
    input logic req,
    input logic ack
);
    default clocking @(posedge clk);
    endclocking
    default disable iff (rst);

    // A request stays up until it is acknowledged.
    a_hold: assert property (req && !ack |=> req);

    // Every request is acknowledged within one to three samples.
    a_resp: assert property ($rose(req) |-> ##[1:3] ack);

    // An acknowledge only answers a pending request.
    a_ack_with_req: assert property (ack |-> req);

    // A complete handshake happens.
    c_done: cover property ($rose(req) ##[1:3] ack);
endmodule

bind handshake handshake_props u_props (.clk(clk), .rst(rst), .req(req), .ack(ack));
```
<!-- /sva-example -->

## x-checks

One pure X-check is skipped; the mixed assertion keeps its real check and warns.

[Source](../tests/functional_tests/documentation_examples/fixtures/x-checks.sv); top `top`.

<!-- sva-expectation: x-checks -->
Check exit: 0. Generation exit: 0. Directives: 1 accepted, 1 skipped, 0 rejected.
Warning fragment: `the X part of this assert is dropped`.

<!-- sva-example: x-checks -->
```systemverilog
module top(input logic clk, a, input logic [3:0] v);
    only_x: assert property (@(posedge clk) !$isunknown(v));
    mixed: assert property (@(posedge clk) a && !$isunknown(v));
endmodule
```
<!-- /sva-example -->

## reject-x-checks

The same source under the strict X-check policy rejects both directives.

[Source](../tests/functional_tests/documentation_examples/fixtures/x-checks.sv); top `top`.

<!-- sva-expectation: reject-x-checks -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 2 rejected.
Diagnostic fragment: `$isunknown`.

<!-- sva-example: reject-x-checks -->
```systemverilog
module top(input logic clk, a, input logic [3:0] v);
    only_x: assert property (@(posedge clk) !$isunknown(v));
    mixed: assert property (@(posedge clk) a && !$isunknown(v));
endmodule
```
<!-- /sva-example -->

## reject-disable

The register changes at the edge; its post-update disable value cannot be sampled faithfully by the current monitor.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-disable.sv); top `top`.

<!-- sva-expectation: reject-disable -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `disable iff`.

<!-- sva-example: reject-disable -->
```systemverilog
module top(input logic clk, rst_n, a, b);
    logic registered_reset;
    always_ff @(posedge clk) registered_reset <= !rst_n;
    wrong_region: assert property (@(posedge clk) disable iff (registered_reset) a |=> b);
endmodule
```
<!-- /sva-example -->

## reject-initial

The surrounding condition is evaluated at time zero, which differs from moving it to the first clock tick.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-initial.sv); top `top`.

<!-- sva-expectation: reject-initial -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `initial`.

<!-- sva-example: reject-initial -->
```systemverilog
module top(input logic clk, en, a);
    initial if (en) check_once: assert property (@(posedge clk) a);
endmodule
```
<!-- /sva-example -->

## reject-past-default

A bit input has a known-zero default, so the all-X history implementation is inapplicable.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-past-default.sv); top `top`.

<!-- sva-expectation: reject-past-default -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `before the first clock tick is not unknown`.

<!-- sva-example: reject-past-default -->
```systemverilog
module top(input logic clk, input bit a);
    default_zero: assert property (@(posedge clk) $past(a));
endmodule
```
<!-- /sva-example -->

## reject-past-first

The current value can make this condition known true before history exists.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-past-first.sv); top `top`.

<!-- sva-expectation: reject-past-first -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `first sample`.

<!-- sva-example: reject-past-first -->
```systemverilog
module top(input logic clk, a);
    first_unknown: assert property (@(posedge clk) a || $past(a));
endmodule
```
<!-- /sva-example -->

## reject-gated-past

The current history implementation does not support gated sampling.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-gated-past.sv); top `top`.

<!-- sva-expectation: reject-gated-past -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `$past`.

<!-- sva-example: reject-gated-past -->
```systemverilog
module top(input logic clk, a, en);
    gated: assert property (@(posedge clk) $past(a, 1, en));
endmodule
```
<!-- /sva-example -->

## reject-negedge

Only the supported positive-edge clock form is accepted.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-negedge.sv); top `top`.

<!-- sva-expectation: reject-negedge -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `negedge clocks are not supported yet`.

<!-- sva-example: reject-negedge -->
```systemverilog
module top(input logic clk, a);
    falling_clock: assert property (@(negedge clk) a);
endmodule
```
<!-- /sva-example -->

## reject-first-match

Choosing the first match is not implemented.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-first-match.sv); top `top`.

<!-- sva-expectation: reject-first-match -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `FirstMatch`.

<!-- sva-example: reject-first-match -->
```systemverilog
module top(input logic clk, a, b);
    first_only: assert property (@(posedge clk) first_match(a ##[1:2] b));
endmodule
```
<!-- /sva-example -->

## reject-empty-product

The product operator does not support an empty-capable operand.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-empty-product.sv); top `top`.

<!-- sva-expectation: reject-empty-product -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `empty`.

<!-- sva-example: reject-empty-product -->
```systemverilog
module top(input logic clk, a, b);
    empty_product: assert property (@(posedge clk) a[*0:2] intersect b);
endmodule
```
<!-- /sva-example -->

## reject-inside

Use a sized 4-bit item (4'd1) to make this list uniform; the unsized integer is a different type.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-inside.sv); top `top`.

<!-- sva-expectation: reject-inside -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `not all of one width and signedness`.

<!-- sva-example: reject-inside -->
```systemverilog
module top(input logic clk, input logic [3:0] v);
    mixed_width: assert property (@(posedge clk) v inside {1});
endmodule
```
<!-- /sva-example -->

## reject-cover

A true result does not yet determine nonvacuity under the conservative cover rule.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-cover.sv); top `top`.

<!-- sva-expectation: reject-cover -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `nonvacuity is not known`.

<!-- sva-example: reject-cover -->
```systemverilog
module top(input logic clk, a, b, c, d);
    unknown_nonvacuity: cover property (@(posedge clk) (a |-> b) or (c |-> d));
endmodule
```
<!-- /sva-example -->

## reject-fairness

A witness across several configurations would weaken a strong assumption, so this shape is rejected.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-fairness.sv); top `top`.

<!-- sva-expectation: reject-fairness -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `(liveness) in 'assume'`.

<!-- sva-example: reject-fairness -->
```systemverilog
module top(input logic clk, a, b);
    several_configurations: assume property (@(posedge clk) a |-> strong(##[1:$] b));
endmodule
```
<!-- /sva-example -->

## reject-member

Whole packed values are supported; selecting a struct member is deferred.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-member.sv); top `top`.

<!-- sva-expectation: reject-member -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `member`.

<!-- sva-example: reject-member -->
```systemverilog
module top(input logic clk);
    struct packed {logic a; logic b;} value;
    member: assert property (@(posedge clk) value.a);
endmodule
```
<!-- /sva-example -->

## reject-history-selector

An unknown history selector can select else before the history exists.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-history-selector.sv); top `top`.

<!-- sva-expectation: reject-history-selector -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `$past`.

<!-- sva-example: reject-history-selector -->
```systemverilog
module top(input logic clk, a, b, c);
    unknown_branch: assert property (@(posedge clk) if ($past(a)) b else c);
endmodule
```
<!-- /sva-example -->

## reject-division

A variable divisor can be zero; the two-state model cannot represent that unknown result.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-division.sv); top `top`.

<!-- sva-expectation: reject-division -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `divisor`.

<!-- sva-example: reject-division -->
```systemverilog
module top(input logic clk, input logic [3:0] a, b);
    variable_divisor: assert property (@(posedge clk) (a / b) == 4'd1);
endmodule
```
<!-- /sva-example -->

## frontend-error

Slang rejects the undeclared signal before directive classification, so there is no summary.

[Source](../tests/functional_tests/cli/fixtures/broken.sv); top `broken`.

<!-- sva-expectation: frontend-error -->
Check exit: 1. Generation exit: 1. Directives: frontend error; no summary.
Diagnostic fragment: `missing_signal`.

<!-- sva-example: frontend-error -->
```systemverilog
// Does not compile: undeclared identifier.
module broken (input logic clk);
    p: assert property (@(posedge clk) missing_signal);
endmodule
```
<!-- /sva-example -->

## restrict

A restriction constrains the traces; the assertion is then proved.

[Source](../tests/functional_tests/documentation_examples/fixtures/restrict.sv); top `top`.

<!-- sva-expectation: restrict -->
Check exit: 0. Generation exit: 0. Directives: 2 accepted, 0 skipped, 0 rejected.

<!-- sva-example: restrict -->
```systemverilog
module top(input logic clk, a, b);
    scenario: restrict property (@(posedge clk) a |=> b);
    checked: assert property (@(posedge clk) a |=> b);
endmodule
```
<!-- /sva-example -->

## proof-cover

SBY proves the assertion and reaches the cover at sample 1.

[Source](../tests/functional_tests/documentation_examples/fixtures/proof-cover.sv); top `top`.

<!-- sva-expectation: proof-cover -->
Check exit: 0. Generation exit: 0. Directives: 2 accepted, 0 skipped, 0 rejected.

<!-- sva-example: proof-cover -->
```systemverilog
module top(input logic clk);
    logic q = 1'b0;
    always_ff @(posedge clk) q <= 1'b1;
    holds: assert property (@(posedge clk) q == q);
    reachable: cover property (@(posedge clk) q);
endmodule
```
<!-- /sva-example -->

## assertion-failure

SBY fails the assertion at sample 0, despite successful CLI acceptance and generation.

[Source](../tests/functional_tests/documentation_examples/fixtures/assertion-failure.sv); top `top`.

<!-- sva-expectation: assertion-failure -->
Check exit: 0. Generation exit: 0. Directives: 1 accepted, 0 skipped, 0 rejected.

<!-- sva-example: assertion-failure -->
```systemverilog
module top(input logic clk);
    logic q = 1'b0;
    always_ff @(posedge clk) q <= 1'b1;
    fails_initially: assert property (@(posedge clk) q);
endmodule
```
<!-- /sva-example -->

## strong-pass

Safety proves, and liveness proves because q becomes high after one clock tick.

[Source](../tests/functional_tests/documentation_examples/fixtures/strong-pass.sv); top `top`.

<!-- sva-expectation: strong-pass -->
Check exit: 0. Generation exit: 0. Directives: 1 accepted, 0 skipped, 0 rejected.

<!-- sva-example: strong-pass -->
```systemverilog
module top(input logic clk);
    logic q = 1'b0;
    always_ff @(posedge clk) q <= 1'b1;
    eventual: assert property (@(posedge clk) s_eventually q);
endmodule
```
<!-- /sva-example -->

## strong-fail

Safety proves but liveness fails: q remains low forever, so the strong assertion does not hold.

[Source](../tests/functional_tests/documentation_examples/fixtures/strong-fail.sv); top `top`.

<!-- sva-expectation: strong-fail -->
Check exit: 0. Generation exit: 0. Directives: 1 accepted, 0 skipped, 0 rejected.

<!-- sva-example: strong-fail -->
```systemverilog
module top(input logic clk);
    logic q = 1'b0;
    always_ff @(posedge clk) q <= 1'b0;
    eventual: assert property (@(posedge clk) s_eventually q);
endmodule
```
<!-- /sva-example -->

## reject-past-int

The current frontend classifies an `int` operand's default as known zero; the
sampled-default check therefore rejects this form conservatively. This is a
frontend limitation, rather than a claim that IEEE `int` is a two-state type.
Use an uninitialised four-state operand whose default the frontend reports as
all X, after checking its width and signedness preserve the intended property.

[Source](../tests/functional_tests/documentation_examples/fixtures/reject-past-int.sv); top `top`.

<!-- sva-expectation: reject-past-int -->
Check exit: 1. Generation exit: 1. Directives: 0 accepted, 0 skipped, 1 rejected.
Diagnostic fragment: `before the first clock tick is not unknown`.

<!-- sva-example: reject-past-int -->
```systemverilog
module top(input logic clk, input int count);
    frontend_default: assert property (@(posedge clk) $past(count) > 0);
endmodule
```
<!-- /sva-example -->
