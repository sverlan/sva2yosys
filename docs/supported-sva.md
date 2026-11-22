# Supported SVA

This is the input subset of SystemVerilog Assertions that sva2yosys accepts, and the status of each construct. Clause numbers refer to IEEE 1800-2017.

A construct is **supported** only when it has semantic tests **and** generated-code tests (status TESTED or higher). Being parsed by Slang is not enough. A lower status is not a claim of supported semantics; some declaration forms have code generation without the complete TESTED evidence. What the tests establish, and where a verdict rests on our reading of the standard, is in [`validation.md`](validation.md).

## Status levels

| Status | Meaning |
|---|---|
| NOT PARSED | the sva2yosys frontend does not convert it; input using it is rejected |
| PARSED | converted to the SVA IR; still rejected by validation |
| SEMANTICS IMPLEMENTED | reference evaluator and temporal machine implement it; trace tests pass |
| CODEGEN IMPLEMENTED | SystemVerilog monitors are generated for it |
| TESTED | semantic tests + generated-code tests (Yosys/SBY) pass |
| DIFFERENTIAL TESTED | additionally checked by property-based comparison of evaluator, machine and generated monitor |

A row marked *rejected* is refused with a located diagnostic. NOT PARSED and PARSED say how far the frontend gets before the refusal; the reason is given at the row's reference link.

The column **Slang** records confirmed elaboration and export of the relevant
structure through the project's pyslang 12 adapter (✓), or an export/binding gap
(◐). **❔** means that frontend evidence has not been established; **N/A** means
the row is a semantic rule with no separate frontend question. These marks do
not establish converter, monitor or semantic support: read the **Status** column
separately. See [`architecture.md`](architecture.md#slang-frontend-findings)
and the [frontend boundary fixture](../tests/functional_tests/slang_frontend/fixtures/support_boundaries.sv).

Detailed operand, shape, directive, warning and binding restrictions are in the
[SVA language reference](sva-reference.md). The matrix is an index: a TESTED row
with a qualification covers only the subset described at its reference link.
Future work has no release commitment here. The [comparison with other flows](tool-comparison.md#comparison-matrix) distinguishes
measured observations from documented/source-only claims and unmeasured cases.

## Resource admission

Support remains subject to deterministic limits, with located rejection rather
than approximation. See [resource admission](sva-reference.md#resource-admission-and-unsupported-input)
for the categories, [usage](usage.md#compiler-resource-limits---max-expansion---max-work---max-state-bits---max-emission) for defaults and overrides, and [validation](validation.md#6-known-limits-of-the-evidence) for the measurements behind the defaults.

## Matrix

Semantic traces, machine comparisons and generated monitors under Yosys/SBY back
the TESTED entries; [validation](validation.md) explains the limits of that evidence.
Generated monitors require SBY `multiclock off`. Directive kind and operand shape
remain independent restrictions, especially for unbounded properties and covers.

### Directives (§16.14)

| Construct | Details | Slang | Status |
|---|---|---|---|
| `assert property` | [Reference](sva-reference.md#assert-assume-and-cover) | ✓ | TESTED |
| `assume property` | [Reference](sva-reference.md#assert-assume-and-cover) | ✓ | TESTED |
| `cover property` | [Reference](sva-reference.md#assert-assume-and-cover) | ✓ | TESTED; nonvacuous success only |
| Concurrent assertions in `always @(posedge clk)` / `always_ff` (procedural, §16.14.6) | [Reference](sva-reference.md#concurrent-directives-in-clocked-procedures) | ✓ (clock inferred by Slang) | TESTED; restricted procedural context |
| Concurrent assertions in `initial` procedures (§16.14.6) | [Reference](sva-reference.md#concurrent-directives-in-initial) | ✓ (clock inferred by Slang) | TESTED; one attempt; unbounded-cover limitation |
| `restrict property` | [Reference](sva-reference.md#assert-assume-and-cover) | ✓ directive retained | TESTED; lowered as `assume` (formal only) |

### Clocking and reset (§14.12, §16.15, §16.16)

| Construct | Details | Slang | Status |
|---|---|---|---|
| `@(posedge clk)` | [Reference](sva-reference.md#sampling-events) | ✓ | TESTED |
| `default clocking` | [Reference](sva-reference.md#sampling-events) | ✓ (via `semanticLeadingClock`) | TESTED |
| `disable iff (e)` | [Reference](sva-reference.md#disable-iff-and-default-disable-iff) | ✓ | TESTED; edge-stable inputs/expressions only |
| `default disable iff` | [Reference](sva-reference.md#disable-iff-and-default-disable-iff) | ◐ no pyslang accessor; probe workaround | TESTED; export/context restrictions |
| `@(negedge clk)` | [Reference](sva-reference.md#sampling-events) | ✓ edge retained | NOT PARSED |
| `@(posedge clk iff en)` / `@(negedge clk iff en)` | [Reference](sva-reference.md#sampling-events) | ✓ edge/condition retained | NOT PARSED |
| Multiclock properties | [Reference](sva-reference.md#sampling-events) | ✓ distinct clocks retained | rejected |

### Boolean expressions and sampled-value functions (§16.6, §16.9.3)

| Construct | Details | Slang | Status |
|---|---|---|---|
| Boolean expressions over design signals | [Reference](sva-reference.md#signals-and-expressions) | ✓ | TESTED |
| Packed integral operand types as whole values | [Reference](sva-reference.md#integral-types-and-conversions) | ✓ | TESTED; whole values only |
| `$past(e)` (one argument); conditions that could be known true at the first sample are rejected, and so are `$past` and the edge functions on operands whose default sampled value (IEEE 1800 §16.5.1: 2-state types, initialised variables, constants in the operand) is not all X (`docs/semantics.md` §8) | [Reference](sva-reference.md#sampled-value-functions) | ✓ | TESTED; first-sample/default restrictions |
| `$rose`, `$fell`, `$stable`, `$changed` | [Reference](sva-reference.md#sampled-value-functions) | ✓ | TESTED; default restrictions |
| `$past(e, n)` (constant `n >= 1`) | [Reference](sva-reference.md#sampled-value-functions) | ✓ | TESTED; first-sample/default restrictions |
| gated or clocked `$past` (`$past(e, n, gate)`, `$past(e, n, , clk)`) | [Reference](sva-reference.md#sampled-value-functions) | ◐ gate exported; explicit clock has export stubs | rejected |
| `$countones`, `$onehot`, `$onehot0` | [Reference](sva-reference.md#bit-counts-and-x-checks) | ✓ | TESTED; no past-dependent operand |
| `$isunknown(e)` (§20.9) | [Reference](sva-reference.md#bit-counts-and-x-checks) | ✓ | TESTED; folded, skipped or warned; reject policy available |
| `e inside { i1, [lo:hi], ... }` (§11.4.13) | [Reference](sva-reference.md#set-membership-inside) | ✓ | TESTED; uniform lists and context restrictions |
| `seq.triggered` | [Reference](sva-reference.md#sequence-composition) | ✓ call/sequence body retained | NOT PARSED |
| Nested sampled-value functions | [Reference](sva-reference.md#sampled-value-functions) | ✓ calls retained | rejected |
| `/` and `%` with a divisor that is not a non-zero constant (division by zero gives X, `docs/semantics.md` §1) | [Reference](sva-reference.md#expressions-that-could-produce-unknown-values) | ✓ | rejected |
| Zero-count replication inside a concatenation (§11.4.12.1), e.g. `{s, {0{a}}}` | [Reference](sva-reference.md#expressions-that-could-produce-unknown-values) | ✓ | rejected; unsupported void operand |

### Sequences (§16.7, §16.9)

| Construct | Details | Slang | Status |
|---|---|---|---|
| `##N`, `##0` (fusion) | [Reference](sva-reference.md#delays-bounded-repetition-and-empty-matches) | ✓ | TESTED |
| `##[N:M]`, `##[0:N]` | [Reference](sva-reference.md#delays-bounded-repetition-and-empty-matches) | ✓ | TESTED |
| `##[N:$]`, `##[*]`, `##[+]` | [Reference](sva-reference.md#unbounded-delays-and-repetition) | ✓ | TESTED in listed assert/assume/cover shapes; other contexts PARSED, rejected |
| `[*N]`, `[*N:M]`, `[*0]`, `[*0:N]` | [Reference](sva-reference.md#delays-bounded-repetition-and-empty-matches) | ✓ | TESTED |
| `[*]`, `[+]`, `[*N:$]` | [Reference](sva-reference.md#unbounded-delays-and-repetition) | ✓ | TESTED in listed shapes; other covers PARSED, rejected |
| `[=N]`, `[=N:M]`, `[=N:$]` (nonconsecutive) | [Reference](sva-reference.md#unbounded-delays-and-repetition) | ✓ | TESTED; Boolean condition; listed shapes, others PARSED/rejected |
| `[->N]`, `[->N:M]`, `[->N:$]` (goto) | [Reference](sva-reference.md#unbounded-delays-and-repetition) | ✓ | TESTED; Boolean condition; listed shapes, others PARSED/rejected |
| sequence `or` | [Reference](sva-reference.md#sequence-composition) | ✓ | TESTED |
| sequence `and`, `intersect`, `within`, `throughout` | [Reference](sva-reference.md#sequence-composition) | ✓ | TESTED; empty-match restrictions |
| `first_match` | [Reference](sva-reference.md#sequence-composition) | ◐ located unsupported export stub | NOT PARSED |

### Properties (§16.12)

| Construct | Details | Slang | Status |
|---|---|---|---|
| Sequence as property | [Reference](sva-reference.md#sequences-and-implications) | ✓ | TESTED; strength and unbounded restrictions |
| `\|->`, `\|=>` | [Reference](sva-reference.md#sequences-and-implications) | ✓ | TESTED; nullable nonoverlapping antecedent rejected |
| `not` | [Reference](sva-reference.md#property-negation-conjunction-and-disjunction) | ✓ | TESTED bounded and listed unbounded forms |
| property `and` of non-sequences (`p and q`, §16.12.5) | [Reference](sva-reference.md#property-negation-conjunction-and-disjunction) | ✓ | TESTED; cover never split |
| property `if (e) p [else q]` | [Reference](sva-reference.md#property-conditionals) | ✓ | TESTED; past-dependent else selector rejected |
| property `or` (of non-sequences); implication with a property consequent (nested implication, `a \|-> not (b \|-> c)`, `a \|-> (p or q)`, …) | [Reference](sva-reference.md#sequences-and-implications) | ✓ | TESTED bounded; unbounded per-attempt forms rejected |
| **Cover rule** for `cover` of these properties | [Reference](sva-reference.md#bounded-cover-rule) | N/A | accepted only when nonvacuity is known at success |
| `nexttime [n]`, `s_nexttime [n]` (default 1, `[0]` included); `always [m:n]`, `s_always [m:n]`, `eventually [m:n]`, `s_eventually [m:n]` (constant `0 <= m <= n`) | [Reference](sva-reference.md#bounded-temporal-operators) | ✓ | TESTED; cover and unbounded-operand restrictions |
| `until`, `until_with` | [Reference](sva-reference.md#until-and-until_with) | ✓ | TESTED assert/assume, Boolean operands; cover rejected |
| `s_until`, `s_until_with` | [Reference](sva-reference.md#until-and-until_with) | ✓ | TESTED assert; exact fairness assume; cover rejected |
| `strong(...)`, `weak(...)` | [Reference](sva-reference.md#strength-liveness-and-fairness) | ✓ | TESTED bounded; unbounded by directive/shape; L1 assume tested, L2 rejected |
| unbounded `always p`, `always [m:$] p` | [Reference](sva-reference.md#unbounded-always-and-s_eventually) | ✓ | TESTED assert/assume in flattenable shapes; cover rejected |
| unbounded `s_eventually p`, `s_eventually [m:$] p` | [Reference](sva-reference.md#unbounded-always-and-s_eventually) | ✓ | TESTED assert; L1 assume; cover/property operand rejected |

### Declarations and structure (§16.8, §16.12, §17, §23.11)

| Construct | Details | Slang | Status |
|---|---|---|---|
| `sequence … endsequence` with arguments | [Reference](sva-reference.md#named-sequences-and-properties) | ✓ (bodies argument-substituted) | CODEGEN IMPLEMENTED with arguments; without arguments TESTED |
| `property … endproperty` with arguments | [Reference](sva-reference.md#named-sequences-and-properties) | ✓ (bodies argument-substituted) | CODEGEN IMPLEMENTED with arguments; without arguments TESTED |
| Package-defined sequences/properties | [Reference](sva-reference.md#named-sequences-and-properties) | ✓ substituted bodies retained | CODEGEN IMPLEMENTED; simple qualified forms only |
| `checker … endchecker` with arguments | [Reference](sva-reference.md#named-sequences-and-properties) | ◐ assertions located in the checker declaration | NOT PARSED; rewrite rejection too |
| `bind <module> <instantiation>` in a verification file | [Reference](sva-reference.md#module-binds-and-parameter-variants) | ✓ | TESTED |
| Instance binds (`bind m: top.u …`, `bind top.u …`) | [Reference](sva-reference.md#module-binds-and-parameter-variants) | ✓ | rejected by rewrite |
| Hierarchical references | [Reference](sva-reference.md#named-sequences-and-properties) | ✓ | NOT PARSED |
| Parameter-dependent bounds, same value in all instances | [Reference](sva-reference.md#module-binds-and-parameter-variants) | ✓ | TESTED |
| Parameter-dependent bounds or widths, differing per instance | [Reference](sva-reference.md#module-binds-and-parameter-variants) | ✓ | TESTED; integral variant selection |
| Assertions in generate loops | [Reference](sva-reference.md#module-binds-and-parameter-variants) | ✓ | TESTED |
| A `$`-valued, string, real, type or struct parameter, an escaped parameter name or an interface port that a parametrised assertion depends on (visible but unused ones are fine) | [Reference](sva-reference.md#module-binds-and-parameter-variants) | ✓ | rejected, in check mode and in generation |
| Assertions inside interfaces or programs, or from macro expansions; any SVA item in a file included by a verification file | [Reference](sva-reference.md#verification-file-boundaries) | ✓ | rejected by rewrite |
| `default disable iff` in an included file or a macro expansion | [Reference](sva-reference.md#verification-file-boundaries) | ◐ (probe cannot place it) | rejected at export: UnsupportedDefaultDisable |
| A name that means something else where the assertion is written (e.g. a module-level `property` or `default disable iff` asserted inside a generate block that redeclares one of its names; checker formals) | [Reference](sva-reference.md#named-sequences-and-properties) | ✓ (site lookup) | rejected by converter |
| Clocking blocks in verification files | [Reference](sva-reference.md#verification-file-boundaries) | ✓ | removed |
| SVA in design files (not given as verification files): assertions, declarations, binds | [Reference](sva-reference.md#verification-file-boundaries) | ✓ | not translated; generation error, or warning with override |

## Comparison with other Yosys SVA flows

The comparison of sva2yosys with native Yosys, yosys-slang, Yosys+Verific and EBMC,
construct by construct, is at the top of the [comparison page](tool-comparison.md#comparison-matrix).
