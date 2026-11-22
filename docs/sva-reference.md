# SVA language reference

This reference describes the language that sva2yosys can lower into monitors for
Yosys/SymbiYosys. Clause numbers refer to IEEE 1800-2017. The
[status matrix](supported-sva.md) distinguishes tested support from syntax that is
only parsed or has code generation without the full semantic and generated-code
evidence. The restrictions below apply to `assert`, `assume` and `cover`; acceptance
of one directive kind does not imply acceptance of the others.

**Complete, runnable examples are on a separate page, [SVA checked examples](sva-examples.md)**:
one for each family of constructs below, with its expected compiler outcome.

[Semantics](semantics.md) specifies sampling, verdicts and strength;
[validation](validation.md) describes the evidence and interpretations;
[usage](usage.md) describes commands and generated files. The
[traceability index](traceability.md) maps constructs and IEEE clauses to the
hand-derived cases.

Inline property fragments and forms in this reference are **illustrative grammar**,
not complete executable designs (for those, see the
[checked examples](sva-examples.md)). In them, `a`, `b`, `c`, `e`, `p` and `q` stand for
operands of the stated kind; bounds are elaboration-time constants satisfying the
stated restrictions. A supported form still requires valid declarations, a clock,
an accepted binding context and sufficient compiler resources.

## Contents

- [Signals and expressions](#signals-and-expressions)
  - [Integral types and conversions](#integral-types-and-conversions)
  - [Expressions that could produce unknown values](#expressions-that-could-produce-unknown-values)
  - [Sampled-value functions](#sampled-value-functions)
  - [Bit counts and X checks](#bit-counts-and-x-checks)
  - [Set membership: `inside`](#set-membership-inside)
- [Clocking and disable](#clocking-and-disable)
  - [Sampling events](#sampling-events)
  - [`disable iff` and `default disable iff`](#disable-iff-and-default-disable-iff)
- [Directives and procedural contexts](#directives-and-procedural-contexts)
  - [`assert`, `assume` and `cover`](#assert-assume-and-cover)
  - [Concurrent directives in clocked procedures](#concurrent-directives-in-clocked-procedures)
  - [Concurrent directives in `initial`](#concurrent-directives-in-initial)
- [Sequences](#sequences)
  - [Delays, bounded repetition and empty matches](#delays-bounded-repetition-and-empty-matches)
  - [Sequence composition](#sequence-composition)
  - [Unbounded delays and repetition](#unbounded-delays-and-repetition)
  - [Unbounded assertion and assumption contexts](#unbounded-assertion-and-assumption-contexts)
  - [Unbounded covers](#unbounded-covers)
- [Property operators](#property-operators)
  - [Sequences and implications](#sequences-and-implications)
  - [Property negation, conjunction and disjunction](#property-negation-conjunction-and-disjunction)
  - [Property conditionals](#property-conditionals)
  - [Bounded temporal operators](#bounded-temporal-operators)
  - [Bounded cover rule](#bounded-cover-rule)
  - [`until` and `until_with`](#until-and-until_with)
  - [Unbounded `always` and `s_eventually`](#unbounded-always-and-s_eventually)
- [Strength, liveness and fairness](#strength-liveness-and-fairness)
- [Declarations and binding](#declarations-and-binding)
  - [Named sequences and properties](#named-sequences-and-properties)
  - [Module binds and parameter variants](#module-binds-and-parameter-variants)
  - [Verification-file boundaries](#verification-file-boundaries)
- [Resource admission and unsupported input](#resource-admission-and-unsupported-input)
- [Checked examples](#checked-examples)

## Signals and expressions

### Integral types and conversions

Boolean expressions over design signals are supported. Whole integral values may
be `int`, `integer`, `byte`, `shortint` or `longint`, including unsigned forms;
`logic`, `bit` or `reg` vectors; typedefs and enums; packed structs/unions; or
multi-dimensional packed arrays. Packed aggregates are treated as flat bit vectors
when compared, combined or passed to supported sampled-value functions.

The canonical elaborated type determines width and signedness. An enum member is
a constant of its enum's width. An enum with the default `int` base is 32-bit
and signed. The current frontend classifies `int` and default-base enums as
having known sampled defaults, so sampled functions on them are conservatively
rejected; this is a frontend acceptance limit, not a claim that IEEE defines
`int` as two-state. A `logic`-based enum without an initializer has an all-X
default and can satisfy the [sampled-value restriction](#sampled-value-functions).
Plain-vector semantic cases and the
[enum formal design](../tests/functional_tests/formal_designs/fixtures/fsm_enum)
provide the corresponding evidence.

Struct member access (`s.f`), bit/part selects into a packed struct/union or a
multi-dimensional packed array, unpacked arrays, `real` and `string` operands are
rejected. Whole-value support does not imply member or element support. Ordinary
single-dimensional vectors support constant in-range bit and part selects;
variable selects are not supported.

Supported expression operators include unary arithmetic, logical/bitwise negation
and reductions; arithmetic `+`, `-`, `*`, `/`, `%`; bitwise operations; equality,
case equality and relational comparisons; logical `&&`, `||`, `->`, `<->`;
shifts; `?:`; concatenation and positive-count replication; and supported integral
conversions. The two-state model makes ordinary and case equality agree on known
inputs. Context-determined widening uses the context's signedness, whereas explicit
casts and assignment-like conversions extend by the source's signedness.
Self-determined operands, such as shift counts and concatenation parts, retain
their own sizing. See [sampling and conversions](semantics.md#1-sampling) for the
precise rules; the
[signed-expression counterexample](../tests/functional_tests/reference_semantics/fixtures/counterexamples.trace)
records why a signed source in an unsigned context must be zero-extended.

### Expressions that could produce unknown values

The monitor model is two-state. Division and modulo therefore require a non-zero
constant divisor: `a / 2` is supported, while `a / b` and `a % 0` are rejected.
IEEE division by zero produces X, which a two-state computation must not silently
replace with a defined result. The restriction also applies inside an operand
that will later be discarded, such as `$isunknown(a / b)`.

Zero-count replication inside a concatenation, for example `{s, {0{a}}}`, is
rejected with a located unsupported `void` operand-type diagnostic before code
generation. It does not become a zero-width monitor operand. Constants containing
X/Z are rejected as values; the exception is a wildcard item of
[`inside`](#set-membership-inside). An expression Slang can evaluate as a constant
can be accepted as its elaborated constant; this does not provide general support
for arbitrary user function calls.

### Sampled-value functions

`$past(e)`, `$past(e, n)` with constant `n >= 1`, `$rose(e)`, `$fell(e)`,
`$stable(e)` and `$changed(e)` use the property's clock (§16.9.3). `$past(e, n)`
uses an `n`-stage history chain. `$rose` and `$fell` test the least significant
bit; `$stable` and `$changed` compare the whole value. History continues through
`disable iff`: disabling aborts temporal attempts and does not reset sampling.

The operand's **default sampled value must be all X** (§16.5.1). This admits
uninitialised four-state signals, and expressions whose defaults remain all X,
such as `$past(a + 1)` or `$past(a[3:0])` for a suitable `logic` vector `a`.
It rejects two-state signals such as `bit`, declaration
initialisers (`logic a = 1'b0`), and partially known defaults such as
`$past({1'b1, a})` or `$past(a & 4'b0011)`. A `disable iff` does not remove this
restriction. Use a four-state uninitialised operand whose default is all X.
The current frontend additionally classifies `int` and default-base enums as
having known defaults and conservatively rejects sampled functions on them;
the [int history example](sva-examples.md#reject-past-int) pins that acceptance limitation.
An uninitialised `integer` operand is classified as all X and is accepted.
Inside a sampled operand, casts to a two-state type, bit counts and `$isunknown`
are rejected because they can hide a known default. This is the rejection that
replaced the former default-value deviation described in
[validation, D1](validation.md#5-interpretations-and-known-deviations).

At sample 0, comparison with that all-X default gives `$rose(e)` equal to the
current LSB, `$fell(e)` equal to its inverse, `$changed(e) = 1` and
`$stable(e) = 0`. A condition containing `$past` does not hold until all the
history it needs exists: for maximum depth `n`, during the first `n` samples.
The compiler admits this treatment only if it is exact under IEEE's X rules.

Accepted condition families include `$past(e)` alone, arithmetic and relational
forms, equality/inequality with a fully past-dependent operand, XOR, conjunction,
bitwise AND and selects, subject to the static first-sample check. A condition is
rejected if missing history could become a **known true** value. Examples include
`$past(a) || b`, `$past(a) | v`, `$past(a) -> b`, `$past(a) ? b : c`, equality
over partially past-dependent bits, reductions of partly known vectors, and
`!($past(a) && b)`. With mixed depths the check applies at every initial sample,
not just sample 0. See [sampled-value semantics](semantics.md#8-sampled-value-functions)
and [sampled trace cases](../tests/functional_tests/reference_semantics/fixtures/sampled.trace).

Gated or explicitly clocked `$past` forms (`$past(e, n, gate)`,
`$past(e, n, , clk)`), nested sampled functions such as `$past($rose(a))`, and
`$past` in `disable iff` are rejected. A property `if` with `else` rejects a
selector using `$past`; see [property conditionals](#property-conditionals).
Goto/nonconsecutive repetition checks its condition in both polarities, including
the negation introduced by expansion. Thus `(a && $past(b))[->1]` is rejected even
though the positive condition can pass the first-sample check. The
[counterexamples](../tests/functional_tests/reference_semantics/fixtures/counterexamples.trace)
record both goto and nonconsecutive cases.

### Bit counts and X checks

`$countones`, `$onehot` and `$onehot0` (§20.9) lower to a balanced integer sum of
the operand's bits, followed by `== 1` or `<= 1` where appropriate. An operand
using `$past` is rejected: IEEE counts X bits as zero, which can make a result
known true before history exists. Bit counts inside sampled operands are also
rejected under the default-value rule.

For a nonconstant two-state operand, `$isunknown(e)` is zero. The operand is
checked before it is discarded, so unsupported expressions do not become accepted
through this function. A call that Slang evaluates as a constant retains its IEEE
value: `$isunknown(4'b1x00)` is 1 and `$isunknown(4'b0001)` is 0. Such elaborated
constant calls are not marked as X-checks.

Under the default `--x-checks=skip` policy, a directive using a nonconstant
`$isunknown` is folded and then classified:

- **Skipped (X-check):** a sound structural check proves that no observable check
  remains. It gets no monitor and is removed from the rewritten file. For
  `assert`/`assume` this means no possible failure; for `cover`, no possible true,
  nonvacuous success. An always-disabled directive or a procedural directive with
  a false enabling condition can also be skipped.
- **Accepted with a warning:** checks remain, and the warning reports the dropped
  X part. For example, `a |-> b && !$isunknown(c)` keeps the `b` check. A disable
  condition that folds to false never disables; a remaining real part is kept.
  The same principle applies to procedural enabling conditions.
- **Rejected:** the operand or context is unsupported, or the policy is
  `--x-checks=reject`.

Skipping is conservative: failure to prove that nothing remains means the
directive is treated as mixed. `a |-> $isunknown(s)` becomes `a |-> 1'b0` and
can fail. `cover property (v |-> !$isunknown(s))` can succeed nonvacuously.
A vacuous operand does not stop a property conjunction from covering:
`cover property (($isunknown(s) |-> a) and b)` can cover with `b`.
The [X-check counterexamples](../tests/functional_tests/reference_semantics/fixtures/counterexamples.trace)
and [folding semantics](semantics.md#8a-isunknown-and-x-checks) explain the exact
skip criterion and per-instance treatment after parameter folding.

`$isunknown` of an operand containing `$past` is rejected under both policies:
before history exists it would be 1, not the model's folded zero. `$isunknown`
of `$rose`, `$fell`, `$stable` or `$changed` can fold because their results are
known. `$isunknown` inside any sampled-value operand is rejected before folding.

### Set membership: `inside`

`e inside {i1, [lo:hi], ...}` (§11.4.13) accepts **uniform** integral lists:
the left operand, every item and every range bound must have the same
self-determined width and signedness. Write `v4 inside {4'd1}` rather than
`v4 inside {1}`; the latter mixes a 4-bit unsigned value with a 32-bit signed
integer and is rejected with a located diagnostic naming both types.
An unbased unsized `'0` or `'1` is one bit by itself, so it is accepted only in
a one-bit list. Mixed-list sizing is deliberately rejected because tools diverge;
see [validation, I11](validation.md#5-interpretations-and-known-deviations).

Membership lowers to OR over the items. A known value item is equality; a range
is `lo <= e && e <= hi`, so an inverted range matches nothing; an empty list is
zero. A sized constant item's X/Z/`?` bits are wildcards (`==?`, §11.4.6),
implemented by masking those bits on both sides. This is the sole exception to
rejecting X/Z constants as values. `'x` and `'z` items, non-integral/unpacked-array
items, and X/Z bits in range bounds are rejected.

A `$` range endpoint means the lowest/highest value of the left operand's type,
so the corresponding comparison is dropped. It is rejected for an arithmetic
left operand. An `inside` within a sampled-value operand, or a sampled-value
function within a membership item, is rejected: initial-sample X bits would act
as wildcards rather than ordinary unknown values. This also excludes `$` or
wildcard membership inside sampled operands.

The restriction extends beyond assertions when Slang could constant-fold an
`inside` before sva2yosys sees it. Nonuniform membership in a parameter or override,
a procedural `localparam`, a declaration, a generate condition, a function, a port
connection, or a select/replication within an assertion rejects **every directive
in the design**, located at that membership expression. Membership in a bound,
sequence/property argument or default argument, size-cast width or type dimension
cannot be checked and also rejects every directive. Runtime design expressions
(continuous assignments, procedural statements and immediate assertions) are left
to Yosys rather than checked by this restriction.

The [inside trace cases](../tests/functional_tests/reference_semantics/fixtures/inside.trace)
run through the semantic and generated-code layers; exhaustive 3- and 4-bit cases
also compare the lowering with an independent membership evaluator. See
[sampling and membership](semantics.md#1-sampling) for the model.

## Clocking and disable

### Sampling events

`@(posedge clk)` and `default clocking` are supported (§14.12, §16.16).
Values in properties are sampled immediately before the edge; sample 0 is the
first event. `@(negedge clk)` and clocks with `iff`, such as
`@(posedge clk iff en)`, are not converted. Multiclock properties are rejected
with `multiclock SVA properties are not supported yet`.

Generated monitors require SBY **`multiclock off`**, the default. Every formal
step is then a sample. Checking them under `multiclock on` can observe between
sampling events and introduce spurious failures, covers or assumption constraints.
Every clock of the design ticks at every formal step in this model; clock-domain
crossings are not checked. See the
[timing table](semantics.md#4-timing-table-normative).

### `disable iff` and `default disable iff`

Both explicit and default disable conditions are supported (§16.12, §16.15).
Disable uses current values in the Observed region, after edge updates, whereas
property operands use pre-edge sampled values. Conditions that can change at the
clock edge, including registers, are rejected to preserve this distinction.

Accepted conditions read constants, top-level inputs, plain input-port connections
to those signals, input ports connected to call-free expressions of them (including
a constant connection or `rst_a && rst_b`), and nets with one zero-delay continuous
assignment of such signals without calls. Each identical instance is checked in
its own connection context. A locally driven input port is not followed through
its external connection. A delayed or multiply driven net is outside this subset.

When disable holds, all open attempts are aborted, no new attempt starts and no
failure or success is reported. Sampled history is preserved. The guarantee is at
the granularity of formal steps; sub-step pulses do not exist in this model.
See [disable semantics](semantics.md#7-disable-iff) and the
[disable cases](../tests/functional_tests/reference_semantics/fixtures/disable.trace).
An internal register reset is future work; this reference does not promise it for
a release. Default disable declarations in includes or macro expansions are
rejected at export as `UnsupportedDefaultDisable`; declaration/binding context
can impose [additional restrictions](#declarations-and-binding).

## Directives and procedural contexts

### `assert`, `assume` and `cover`

`assert property`, `assume property` and `cover property` (§16.14) are tested,
within each construct's directive-specific limits. An assertion consumes failures; an assumption excludes a trace from
its earliest failure onward. A cover consumes true **nonvacuous** attempts:
an implication whose antecedent never matches cannot cover vacuously.

`restrict property` (§16.14.4) is lowered exactly as `assume property`: the same
supported properties, clocks, `disable iff`, procedural and `initial` contexts, the same
fairness for supported strong forms, and the same located rejections (including a
strong form that needs a witness). It keeps its label and source position. IEEE 1800
gives it no action block and no simulation checking, so it is a formal scenario
constraint; the generated monitor does not reproduce that distinction. It contains an
ordinary assumption, so a simulator that evaluates the rewritten output checks the
restriction (stricter than the standard), instead of ignoring it. The output is a
formal artifact for Yosys and SBY, and no simulation workflow is supported.

Example: [restrict](sva-examples.md#restrict).

A sequence attempt covers once, at its first match. A disabled attempt never
covers. Several attempts first matching at one sample share one success bit;
the monitor represents formal reachability, not simulation attempt-count
statistics. Covers of property operators follow the
[bounded cover rule](#bounded-cover-rule); unbounded covers follow their own
[shape restrictions](#unbounded-covers). See [directive semantics](semantics.md#6-directives).

### Concurrent directives in clocked procedures

A concurrent directive in `always @(posedge clk)` or `always_ff` is lowered to a
module-level monitor with the enabling path condition (§16.14.6). For example,
the illustrative form `if (en) assert property (a |-> b)` becomes a property with
antecedent `(en ##0 a)`. When the condition is false there is no procedural attempt;
the lowered implication's vacuous success is unobservable to these directives.

The block's event must be the same as the directive's explicit or inferred clock:
one edge of a one-bit signal, without `iff`. The path may contain `begin/end`,
`if`/`else` without `matches` or `&&&`, and plain `case`. Case enables respect
item priority, and `default` means no item matched. `casez`, `casex`, `case inside`,
loops, `fork`, and timing controls on the path are rejected.

Every statement in the block must fall through in zero time. Assignments without
task calls or intra-assignment timing, declarations, null statements, concurrent
assertions, and sequential blocks/`if`/`case` made from these are permitted.
A delay, wait, `disable`, loop, task/system-task call or other non-fall-through
statement anywhere in the block rejects its directives. Ordinary function calls
in surrounding assignments do not by themselves prevent fall-through.
`always_comb`, `always_latch`, and an unbraced procedural body of a generate
construct are rejected.

Every signal in the enabling condition must be stable at the edge. Accepted
sources are constants, variables written by nonblocking assignments at that same
edge, top-level inputs other than the clock, and plain port connections to those
sources. This stability rule differs from the stricter disable rule. See
[procedural semantics](semantics.md#6-directives) and the
[frontend contract](../tests/functional_tests/slang_frontend/CONTRACT.md).

### Concurrent directives in `initial`

An `assert`, `assume` or `cover property` in `initial` has exactly **one attempt**,
starting at the first tick of its clock (§16.14.6). A first-sample enable is
fused into its antecedent; later clock ticks cannot start another attempt.
For a counter starting at zero, the illustrative property
`initial assert property (@(posedge clk) always [0:9] x < 10)` holds on that
one attempt, whereas its module-level counterpart can fail on a later attempt.

The clock may be explicit, inferred from a single event control
(`initial @(posedge clk) ...`) or supplied by `default clocking`. Missing all
three is rejected with a diagnostic referring to §16.16. A disable at the first
tick leaves no attempt; a later disable aborts the sole running attempt. Neither
disable nor later clocks restart the first-sample enable. `$past` has no history
at that first tick.

A surrounding `if`, `else` or `case` is rejected: IEEE evaluates this path condition
when the procedure runs at time zero, which is different from sampling it at the
first clock tick. Moving the condition into the property (`en |-> p`) is a possible
rewrite **only after checking that it means the same**. Loops, `fork`, delays and
non-fall-through statements are rejected as in clocked procedures. `final` blocks
are rejected by Slang. A cover of an unbounded sequence in `initial` is currently
rejected with `unbounded ranges ($) are not supported yet`, even when the same
module-level cover is accepted. See the
[initial cases](../tests/functional_tests/reference_semantics/fixtures/initial.trace).

## Sequences

### Delays, bounded repetition and empty matches

`##N`, `##0`, `##[N:M]`, `##[0:N]`, `[*N]`, `[*N:M]`, `[*0]` and `[*0:N]`
are tested (§16.7, §16.9). Bounds are nonnegative constants with the lower bound
no greater than the upper bound. Delay zero fuses the endpoints of its operands;
it does not advance one sample. Repetition zero admits an empty match, which is
not an ordinary Boolean sampled at some tick. Composition must preserve the
[empty-match rules](semantics.md#2-matches-triggers-obligations).

An operand of sequence `and`, `intersect` or `within` that admits an empty match
is rejected. A nullable operand of `throughout` is accepted and keeps its empty
match. A nonoverlapping implication `|=>` rejects an antecedent that admits an
empty match; silently inserting a one-tick delay does not preserve IEEE's empty
antecedent rule. These restrictions concern the allowed syntax and structure,
even when a design might prevent a particular match.

### Sequence composition

The supported sequence operators have distinct interval meanings:

| Form (illustrative grammar) | Match requirement |
|---|---|
| `s1 or s2` | Either sequence matches from the common start |
| `s1 and s2` | Both match; the shorter waits for the later endpoint |
| `s1 intersect s2` | Both match with the same start and endpoint |
| `s1 within s2` | The inner match lies within the outer interval, including its endpoints |
| `e throughout s` | `e` holds at every sample of the sequence's match |

`or` combines automata by union; `and`, `intersect` and `within` use product
automata, and `throughout` conjoins its condition into each letter. Two sequences
joined by property-level `or` or `and` retain these sequence meanings. Empty
operands have the restrictions above. `first_match` and `seq.triggered` are not
converted. See [match semantics](semantics.md#2-matches-triggers-obligations),
[composition cases](../tests/functional_tests/reference_semantics/fixtures/composition.trace)
and the nullable-throughout
[counterexample](../tests/functional_tests/reference_semantics/fixtures/counterexamples.trace).

### Unbounded delays and repetition

`##[N:$]`, `##[*]`, `##[+]`, `[*]`, `[+]` and `[*N:$]` are supported in the
contexts below. `$` stays unbounded; it is never replaced by a guessed finite
limit. `##[*]`/`##[+]` abbreviate unbounded delay ranges, and `[*]`/`[+]`
abbreviate zero-or-more/one-or-more consecutive repetitions.

Goto `[->N]`, `[->N:M]`, `[->N:$]` and nonconsecutive `[=N]`, `[=N:M]`,
`[=N:$]` repetition take **Boolean conditions**, not general sequences.
Even a bounded occurrence count permits an unbounded wait. Goto counts successive
occurrences with intervening nonmatches; nonconsecutive repetition also permits
trailing nonmatches. For an exact count, their definitions are
`(!b[*0:$] ##1 b)[*N]` and `b[->N] ##1 !b[*0:$]` (§16.9.2).
The reference counts natively; the compiler expands these definitions.
The first-sample check applies to `b` and the introduced `!b`.

Constant conditions such as `1'b1[=1]` or `1'b1[->2]` are accepted. The false
letters their expansions introduce are evaluated **at their own samples**,
not discarded before those samples. Likewise, a future `1'b0` or contradiction
such as `b ##0 !b` cannot make a failure occur prematurely. This matters if
disable intervenes. See [earliest failure](semantics.md#3-earliest-failure) and
the [constant/goto counterexamples](../tests/functional_tests/reference_semantics/fixtures/counterexamples.trace).

### Unbounded assertion and assumption contexts

For `assert` and `assume`, an unbounded antecedent followed by a bounded sequence
or its `not` uses the canonical direct machine. The antecedent may combine the
supported unbounded sequence forms through sequence operators. Its loops do not
require a finite bound. A consequent with property structure outside the supported
unbounded lowering, for example `nexttime c`, bounded `always`, or a nested
implication after an unbounded antecedent, is rejected; a diagnostic can state
`an unbounded antecedent needs a bounded sequence (or not of one) as consequent`.
This canonical-machine restriction does not exclude the separately supported
unbounded-sequence consequents below.

A **weak unbounded sequence** is accepted as the entire property or the consequent
of `|->`/`|=>`, with either a bounded or an unbounded antecedent. Examples include
`a |-> ##[1:$] b`, `a |-> b[*1:$] ##1 c`, and `a |=> b[->2]`.
It is also accepted under unbounded `always` and in the top-level `nexttime`,
bounded `always`, and property-`if` forms that lower to such a sequence obligation.
These use the configuration-set machine, which merges only **identical complete
frontiers**, never arbitrary overlapping attempts. One register represents each
reachable configuration; the default configuration-count budget is 256.
Both this form and unbounded antecedents use their direct/configuration lowering
regardless of the CLI's strategy request; window/history optimization does not
apply. A low-level API request to emit an inapplicable optimized form is rejected.
See
[lowering strategies](strategies.md) and
[unbounded semantics](semantics.md#96-unbounded-sequences-and-properties).

In `assert` and `assume`, a sequence property is weak by default. A weak
unbounded consequent may stay pending forever without failure. When the compiler
can prove it cannot fail it accepts it with a warning, leaving the monitor
unchanged. `a |-> ##[1:$] b`, including `a |-> ##[1:$] 1'b0`, is such a case.
The warning analysis is conservative; absence of a warning does not prove that
the property can fail. To require a finite match eventually, use an appropriate
`strong(...)` or `s_eventually` form and run its
[safety and liveness tasks](#strength-liveness-and-fairness).

### Unbounded covers

A `cover` accepts a strong unbounded sequence property, either by the cover's
default strength or explicit `strong(...)`. Examples of the illustrative forms
are `cover property (a ##[1:$] b)`, `cover property (a[*2:$] ##1 b)`,
`cover property (b[->2])` and `cover property (b[=2] ##1 c)`.
It also accepts `|->`/`|=>` with a **bounded, single-match-length antecedent**
and such a consequent, for example `cover property (a |-> ##[1:$] b)` or
`cover property (a |=> b[*1:$] ##1 c)`.

Single length is judged over nonempty matches; an antecedent with no match is
admitted but cannot cover. The independent nullable-`|=>` restriction still
applies. The configuration-set monitor counts each attempt's first finite match,
and `disable iff` suppresses that sample's output. `cover property
(a ##[1:$] 1'b0)` never covers. Matching provides a finite reachability witness,
so these covers need no liveness task.

The following covers are rejected, judged on the property **as written**:

- Explicit `weak(...)` of an unbounded sequence, including an implication into
  one. A weak property can hold forever without a match; counting only finite
  matches would change it into the strong property.
- Weak `until`/`until_with`, for that same reason, and strong until forms under
  the current supported-shape rule.
- Implications with an unbounded antecedent, or several antecedent match lengths
  into an unbounded consequent. One implication attempt must satisfy every
  triggered consequent; individual match events cannot count its success.
- Every other unbounded cover shape: unbounded `always`, `s_eventually`, `not`,
  property `or`/`and`, `if`, `nexttime`, and nested implications. Some reducible
  forms are still rejected deliberately; decidability alone is not support.

These are located rejections, including forms parsed by the reference evaluator.
See [unbounded cover cases](../tests/functional_tests/reference_semantics/fixtures/unbounded_covers.trace)
and [the precise shape rule](semantics.md#96-unbounded-sequences-and-properties).

## Property operators

### Sequences and implications

A sequence may be used as a property. `a |-> p` starts a consequent attempt for
every nonempty antecedent match, at that match's end; `a |=> p` starts it one
sample later (§16.12.7). Nonoverlapping implication is `(a ##1 1) |-> p`, with
the nullable-antecedent rejection described above. Even an impossible consequent
of `|=>` must wait until the following sample to fail, and can be disabled then.

Bounded nested implications and other property consequents are tested. Each
antecedent match starts its own consequent attempt; the implication succeeds only
when all started attempts succeed and no further antecedent match can occur.
Where a consequent is a property conjunction, implication distributes over it.
The canonical machine handles supported simple sequence forms; the per-attempt
machine handles the remaining bounded shapes without merging different attempt
ages. An unbounded part that would require that bounded per-attempt machine is
rejected. See [property construction](semantics.md#94-construction).

### Property negation, conjunction and disjunction

`not` of a bounded sequence is accepted as the whole property and as an implication
consequent: it fails on the first match and succeeds when the sequence can no
longer match. This uses the direct machine with exchanged success/failure outputs;
optimized forms do not apply to these negated shapes. Bounded `not` of an
implication or another supported property uses the per-attempt machine.
`not (a |-> s)` succeeds on a refuted consequent and fails when the implication
holds, including vacuously. `not` reverses strength at finite finalization and
on infinite traces; unbounded cases are described under
[strength and liveness](#strength-liveness-and-fairness).

A property `p and q` of non-sequences is true at the later true decision and false
at the first false one. At the top of an assertion or assumption, conjunction is
split into one monitor per conjunct, including a consequent conjunction after
distribution. Their failures reproduce the conjunction **up to and including its
first failure**; no later-failure claim is made. Enabling conditions distribute
over conjuncts too.

Covers are never split: their conjunction needs one per-attempt machine to know
when both operands hold, with nonvacuity from either operand. Conjunction nested
under `not`, `or`, or `if`/`else` also uses the per-attempt machine. Property
`or` of non-sequences becomes true on the first true operand and false when both
are false. Explicit strengths are retained, although they change no online
verdict for bounded obligations. A property-level `and`/`or` of sequences keeps
the sequence operator's meaning. See
[bounded operators](semantics.md#93-operators-all-bounded) and the
[attempt cases](../tests/functional_tests/reference_semantics/fixtures/attempts.trace).

### Property conditionals

`if (e) p` without `else` lowers to `e |-> p`, fused into an existing implication
antecedent where possible. With sequence branches, `if (e) s1 else s2` lowers
to `(e ##0 s1) or (!e ##0 s2)`; two negated sequence branches give `not` of that
sequence. Implication branches, mixed sequence/property branches and branches
with different strengths use the per-attempt machine. Only the branch selected
at the attempt's start contributes its verdict and nonvacuity.

A selector containing `$past` with `else` is rejected: missing history is X,
which would select `else`, while forcing both lowered guards false would lose
that branch. The reference evaluator's X-selector interpretation is recorded as
[validation, I10](validation.md#5-interpretations-and-known-deviations); no accepted
property depends on it. Other unsupported branch shapes retain their ordinary
located validation diagnostics. See
[conditional lowering](semantics.md#95-lowerings-machine-selection-and-the-cover-rule).

### Bounded temporal operators

`nexttime [n]`, `s_nexttime [n]` (default `n = 1`), `always [m:n]`,
`s_always [m:n]`, `eventually [m:n]` and `s_eventually [m:n]` accept constant
`0 <= m <= n`, including zero offsets (§16.12). Their operands can be any
supported bounded property, with nesting; the independent unbounded-shape rules
still apply if an operand has an unbounded part.

The lowering uses `(1 ##n 1) |-> p` for `nexttime [n] p`,
`(1 ##[m:n] 1) |-> p` for `always [m:n] p`, and
`not always [m:n] not p` for `eventually [m:n] p`. Offset `[0]` or `[0:0]`
reduces to the operand. Assertions of `nexttime [n] s` and `always [m:n] s`
with suitable sequence operands keep the canonical machine; the other bounded
shapes use the per-attempt machine.

Weak and strong versions have identical online monitors with finite deadlines.
At the **end of a finite simulation**, the reference evaluator treats an offset
beyond the trace as true for a weak operator and false for a strong one, with
offset-ordered nonvacuity. Generated monitors do not perform that finalization.
A BMC depth limit is not the end of a trace and never turns pending attempts into
failures. See [finite boundaries](semantics.md#91-rules).

### Bounded cover rule

A bounded property cover is accepted only if its nonvacuity is known when an
attempt becomes true (§16.14.8). Otherwise the located diagnostic is
`a cover of this property is not supported: its nonvacuity is not known when an attempt succeeds`.
The rule is sufficient and conservative: it rejects some properties that could
be handled exactly with additional analysis.

Rejected illustrative forms include
`cover property ((a |-> b) or (c |-> d))`,
`cover property (not (a |-> (b |-> c)))`,
`cover property (eventually [0:1] (((a ##2 b) or d) |-> c))`, and
`cover property ((a |-> b) or nexttime [2] 1'b1)`.
Accepted families include covers of suitable property conjunctions, negated
implications and implications with several bounded antecedent match lengths.
The latter use per-attempt results rather than counting each consequent's success
as the implication's success. See
[cover rule and lowerings](semantics.md#95-lowerings-machine-selection-and-the-cover-rule)
and [cover cases](../tests/functional_tests/reference_semantics/fixtures/attempts_cover.trace).

### `until` and `until_with`

All four forms accept **Boolean operands only**; a sequence or property operand
is rejected by the converter. Weak `p until q` and `p until_with q` lower to
`p[*0:$] ##1 q` and `p[*0:$] ##1 (p && q)` respectively. Strong `s_until` and
`s_until_with` add strong sequence strength to the same forms. Inclusive
`until_with` requires `p` at the deciding `q` sample too (§16.12).

Weak forms are tested for `assert` and `assume` as a whole property, an implication
consequent with a bounded or unbounded antecedent, under `always`/`always [m:$]`,
and in the top-level nexttime/bounded-always/property-if forms that lower to a
supported weak sequence. Strong forms are tested for assertions and for
one-configuration fairness assumptions. All until covers are rejected; for weak
forms the diagnostic includes `a cover of 'until' is not supported`.
An until inside a shape needing per-attempt results, such as a nested implication,
property `or`/`and`, or `a |-> nexttime (p until q)`, is rejected with a message
such as `'until' is not supported yet`.

The generated `p && q` and both operands pass the first-sample check. Consequently
`p until q` can fail immediately when `q` has unavailable `$past` and `p` is
false; `until_with` fails when `p` has unavailable history even if `q` holds.
Constants retain their ordinary sampled meanings: `p until 1'b0` never fails
while `p` holds; `1'b0 until q` is decided immediately by `q`; `p until_with
1'b0` fails when `p` first fails; `1'b0 until_with q` fails immediately.

`not (a until b)` is strong and needs assertion liveness or an exact fairness
assumption; `not (a s_until b)` is weak and is tested for assertion/assumption
safety only. See [until and unbounded semantics](semantics.md#96-unbounded-sequences-and-properties)
and [until machine cases](../tests/functional_tests/reference_semantics/fixtures/until_machine.trace).

### Unbounded `always` and `s_eventually`

Unbounded `always p` and `always [m:$] p` are supported for assertions and
assumptions in shapes that flatten into supported sequence obligations.
`always [m:$] p` is `(1 ##[m:$] 1) |-> p`. With a bounded sequence, its
negation or an implication into one, flattening combines nested antecedents:
`w |-> (a |-> c)` becomes `(w ##0 a) |-> c`; a nonoverlapping inner implication
adds `##1 1`. These forms can appear at the top or as an implication consequent,
including `a |-> always p`. Supported weak/strong unbounded consequent forms can
also appear under `always` as described above. Their failures agree with the
nested form; nonvacuity does not, so unbounded-always covers are rejected.
Other operands receive `unbounded 'always' (no upper bound) is not supported yet`.

`s_eventually p` and `s_eventually [m:$] p` accept a Boolean or a bounded
sequence operand. They lower to `strong(1[*m:$] ##1 p)`, with `m = 0` for the
unqualified form. Assertions are tested as the entire property, an implication
consequent or under `always`. Its safety part never fails; its liveness part
requires a future match. The simple `s_eventually p` uses one pending bit;
larger forms can need a witness. Assumptions accept only the exact
one-configuration fairness class. A property operand or a cover is rejected.
`not s_eventually p` is weak and tested for assertion/assumption safety only;
for Boolean `p` it has the meaning of `always !p`.

Unbounded `s_always` and `eventually` are rejected by the converter: IEEE permits
the `$` upper endpoint for `always` and `s_eventually`, not those two forms.
See [unbounded semantics](semantics.md#96-unbounded-sequences-and-properties).

## Strength, liveness and fairness

`strong(...)` and `weak(...)` take sequences (§16.12.2). Both are tested for
bounded sequences. They have the same online verdicts; only the reference
evaluator's finite-simulation finalization fails pending strong obligations.
An assertion/assumption sequence is weak by default; a cover sequence is strong
by default. Inserting `not` does not change that default but **reverses the
operand's strength** (§16.12.3).

Strong unbounded assertion forms include `strong(a ##[1:$] b)`, supported
strong consequents, `s_eventually`, `s_until` and `s_until_with`. They have a
configuration-set **safety part** plus a **liveness part** asserting that no
obligation remains pending forever. The ordinary SBY `prove` task checks safety;
a separate `live` task uses `mode live`, `engines aiger suprove`, the generated
`sva2yosys_read_live_<part>.ys` scripts (one for each liveness part) and their
`SVA2YOSYS_LIVE_<part>` macros. The directive holds only when **the `prove` task and the `live` task of each part pass**.
See [liveness commands](usage.md#strong-properties-the-prove-and-the-live-task) and
[the exact construction](semantics.md#96-unbounded-sequences-and-properties).

Negating an unbounded sequence exchanges its configuration machine's match and
failure outputs. `not weak(s)`, including the default `not s` and negated weak
until, is strong: assertions need liveness as well as the exchanged safety
outputs. For example, `assert property (not (a ##[1:$] b))` also fails in the
limit if `a` holds and `b` never does. `not strong(s)`, including negated strong
until or supported `s_eventually`, is weak, accepted for assertions/assumptions
with safety only. Unbounded negated covers remain rejected.

A strong assumption is tested only when its configuration machine has **at most
one reachable configuration** (the one-configuration class; [semantics](semantics.md#96-unbounded-sequences-and-properties) calls it L1). Its live
block is the exact fairness assumption `s_eventually !busy`. Examples include
`s_eventually b`, `a |-> s_eventually b`, `a s_until b` and `not (a until b)`.
An assumption requiring several configurations (L2 there) is rejected by compilation:
a witness would constrain only a selected obligation and weaken the assumption.
Thus `assume property (not (a ##[1:$] b))` is rejected despite the corresponding
assertion being supported.

Fairness is active only in the `live` task. Safety proofs omit it and are
conservative. Covers reached while fairness assumptions are present are
**provisional**, with a located warning on accepted covers: the cover task does
not establish extendibility to a fair infinite execution. Explicit weak
unbounded covers and all other unsupported unbounded shapes stay rejected.
See [fairness cases](../tests/functional_tests/reference_semantics/fixtures/fairness.trace)
and [negation cases](../tests/functional_tests/reference_semantics/fixtures/not_liveness.trace).

## Declarations and binding

### Named sequences and properties

Named sequences and properties without arguments are tested. Declarations with
arguments are **CODEGEN IMPLEMENTED**, a lower evidence status than TESTED:
Slang substitutes the actual arguments into their bodies, the converter consumes
those bodies, and the rewrite removes the declarations. Do not read Slang's
ability to elaborate an instance as complete semantic/generated-code support for
every argument form.

Simple package-qualified sequence/property calls also have **CODEGEN IMPLEMENTED**
evidence: their substituted bodies are exported, and check/generation probes
succeed. This is not TESTED support for general package binding or argument forms.
`checker ... endchecker` and hierarchical references are not converted.
A checker declaration in a verification
file is also rejected by the rewrite. A reference whose binding at the assertion
site differs from its original binding is rejected because monitors connect
signals by name. Examples include a generate block shadowing a name from a
module-level property/default disable, and checker formals.

### Module binds and parameter variants

`bind <module> <instantiation>` in a verification file is tested. It becomes an
explicit instance in a generated copy of the target module's file because
`read_verilog` does not support `bind` (a syntax error since Yosys 0.70; it was silently ignored before). Instance binds, including
`bind m: top.u ...` and `bind top.u ...`, are rejected by the rewrite: partial
binding would need a renamed module copy. See [binding usage](usage.md#binds-and-the-yosys-frontend---yosys-frontend).

Parameter-dependent bounds are tested when all instances have the same value.
Different per-instance bounds or widths are also tested: each variant gets its
own monitor module, selected by `generate if` comparisons of every parameter and
genvar declared before the assertion. Untyped parameters are also compared by
width and signedness; an unrecognized combination fails the proof loudly.
Generate-loop assertions, including genvar-dependent selects such as `v[i]`,
are tested, with variants selected by genvar.

A string, real or type parameter, a parameter whose value is `$`
(`parameter upper = $;`), a struct-typed parameter, an escaped parameter name or an
interface port that the assertion **depends on** (it is used in the property, the
`disable iff` condition or the clock, or in the type or width of a signal the
assertion reads, directly or through typedefs, named properties and local functions) is
rejected, in check mode and in generation alike: variant selection requires comparable
integral values, and `read_verilog` cannot read `parameter string`. One that the
assertion cannot depend on (for example a configuration parameter `cfg` used only in
an enclosing generate `if` that decides whether the assertion exists, or a type
parameter that no signal it reads uses) does not matter. When the analysis cannot tell
(a hierarchical reference, an interface port, a checker), every visible parameter
counts. A literal `$` in a range (`##[0:$]`) is not a parameter and is supported. Check-only mode still
does not exercise every rewrite restriction; successful generation is required
before presenting a property as usable with the generated verification flow.

### Verification-file boundaries

Assertions inside interfaces or programs, assertions from macro expansions, and
any SVA item in a file included by a verification file are rejected by the rewrite.
`default disable iff` in includes/macros has the export rejection described above.
Clocking blocks, default or otherwise, are removed from rewritten verification
files because `read_verilog` accepts none of them.

SVA in design files that were not supplied as verification files is **not
translated**. Generation normally errors; `--allow-design-sva` changes this to a
warning and does not translate it. This applies to assertions, declarations and
binds. See [generation options](usage.md#generation--o-dir---allow-design-sva) and the
[rewrite contract](../tests/functional_tests/rewrite/CONTRACT.md).

## Resource admission and unsupported input

Language support is subject to deterministic admission limits. Shared preflight
and incremental accounting limit written/created expansion, construction/planning
visits, bounded slot-machine temporal bits and the selected layout's emission
allowance. Per-attempt letter/instance/register/gate limits and configuration-count
limits remain independent. Sampled-value history is checked separately from
temporal bits. Reaching any limit gives a located rejection, never an approximation
or a finite replacement for `$`.

Compact bound-4096 controls remain admitted; that is not a guarantee for every
property with that bound, or for solver time or memory. The bound-2044 repetition
control reaches the independent emission allowance. Forced history layouts charge
their replay work and can be rejected when direct lowering fits. See
[resource limits](usage.md#compiler-resource-limits---max-expansion---max-work---max-state-bits---max-emission)
and [resource accounting](strategies.md#resource-admission).

Unsupported syntax, unsupported context, two-state/default-value restrictions,
unbounded per-attempt shapes and resource exhaustion are different reasons for
rejection. A located diagnostic identifies the failing construct; there is no
fallback that silently drops a temporal obligation. `first_match`,
`seq.triggered`, clock extensions, multiclock properties, checker support,
hierarchical/member access, abort operators such as `accept_on`/`reject_on`,
followed-by operators `#-#`/`#=#` and local assertion variables are future work,
with no release commitment here.

Generated monitors are accepted by `read_verilog -formal -sv` and
`read_slang -D FORMAL`; the surrounding rewritten design must fit the selected
frontend too. See [frontend findings](architecture.md#yosys-frontend-findings).
The [validation account](validation.md) distinguishes internal semantic evidence,
generated-code checks, independent calibrated observations and known gaps. For
the pinned external corpus's per-case acceptance and expected answers, see the
[Yosys-corpus expectations](../tests/functional_tests/external/yosys_corpus/fixtures/expectations.txt).
At pinned Yosys revision `ba8e952`, its 32 variants record 25 accepted (24 with
the upstream BMC verdict; `extnets` differs from the upstream expectation exactly
as plain Yosys does on that build), one X-constant rejection and six skips (VHDL
and a simulation-only X-valued test). The unbounded-antecedent/weak-until
case `a ##[*] b |=> c until d` proves with its assumptions and fails without them.

## Checked examples

Complete sources with their expected compiler outcomes are on a separate page,
[SVA checked examples](sva-examples.md): one example for each family of constructs
above (defaults, procedural contexts, `initial`, integral types, sampled values,
`inside`, sequence and property composition, bounded and unbounded forms, strong
forms and fairness, variants, binding, X-checks and the rejected forms). They are
checked in both CLI modes by the test suite, so they stay in step with the compiler.
