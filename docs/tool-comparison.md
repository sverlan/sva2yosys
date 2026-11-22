# SVA flow comparison

This page compares sva2yosys with other ways of checking SVA in the Yosys ecosystem.
The matrix is a compact orientation aid: its cells describe specific evidence, not
interchangeable guarantees of complete language support. The sections after it
record that evidence. The historical measurements were made with Yosys 0.63; the six
maintained frontend probes were re-measured with Yosys 0.70 (first measured with 0.63).
The interpretation observations are in [`interpretation-probes.md`](interpretation-probes.md).
This page makes no claim about the latest release of any tool and gives no correctness ranking.

## Comparison matrix

The sections after this matrix record versions,
commands, sources, observations and limits.

**Marks.** **✓** = supported in the stated scope, **✗** = rejected,
**◐** = partial/qualified (see the short comment), **❔** = unknown or untested
here; it does not mean rejection. A checkmark for sva2yosys covers only its
[documented subset](sva-reference.md); the [status matrix](supported-sva.md#matrix) is authoritative.

**Yosys+Verific was not run: entries are inferred from public documentation and source code.**
In that column only, a mark in parentheses, **(✓)**, **(✗)** or **(◐)**, means *inferred, not executed*; ❔ means not established.
Each cell links to its audit entry.

**Executed evidence** for the other columns:
- the first frontend probes (Yosys 0.63), the native survey of trace cases and the EBMC feature study;
- the six maintained frontend probes, re-measured with Yosys 0.70 (the yosys-slang column of the first two rows changed);
- 47 additional probe families, checked with the Yosys 0.63 plugin and not re-measured: a ✗ in the yosys-slang column of any other row is that older observation.

See the [remaining-cell probes](#additional-measurements-yosys-063-plugin) and
[historical frontend evidence](#historical-frontend-measurements).

**Reading a cross.** A cross records rejection of the measured source form: native Yosys often
rejects its explicit clock before reaching the operator. It does not establish
that the operator is rejected in every context. EBMC cells include
[capability probes](#historical-ebmc-capability-measurements)
as well as calibrated bounded/lasso checks; acceptance is not an IEEE-equivalence
claim or an arbitrary unbounded proof.

| Construct | sva2yosys | Native Yosys | yosys-slang | Yosys+Verific | EBMC |
|---|---|---|---|---|---|
| Module-level explicit positive-edge property | ✓ | ✗ | ◐ Boolean (0.70) | [(✓)](#v01) | ✓ Boolean operands |
| Procedural positive-edge property | ◐ stable enable | ◐ Boolean | ◐ Boolean (0.70) | [❔](#v02) | ◐ Boolean; enabling unverified |
| Default clocking/disable; named sequence/property | ◐ see reference | ✗ | ✗ | [(✓)](#v03) | ◐ defaults/declarations; disable ineffective |
| Negative edge; clock `iff` enable | ✗ | ◐ procedural negedge ✓; `iff` ✗ | ✗ | [(✓)](#v04) | ◐ negedge checks ✓; `iff` ✗ |
| `disable iff` | ◐ stable subset | ✗ | ✗ | [(✓)](#v05) | ◐ timing differs |
| `$past`, `$rose`, `$fell`, `$stable`, `$changed` | ◐ default-value limits | ◐ Boolean context | ✗ | [(✓)](#v06) | ◐ history/default limits |
| Fixed/bounded delay and consecutive repetition | ✓ | ✗ | ✗ | [(✓)](#v07) | ◐ ranges need BMC |
| Unbounded delay/consecutive repetition | ◐ see reference | ✗ | ✗ | [(◐) nullable limit](#v08) | ◐ BMC accepts; plain BDD rejects |
| Goto/nonconsecutive repetition | ◐ see reference | ✗ | ✗ | [(✓)](#v09) | ◐ BMC; pending-range differences |
| Sequence `or`, `and`, `intersect`, `within`, `throughout` | ◐ empty-match limits | ✗ | ✗ | [(✓)](#v10) | ◐ BMC; pending-match differences |
| `first_match` | ✗ | ✗ | ✗ | [(✓)](#v11) | ◐ BMC; plain BDD rejects |
| Sequence property, negation, `\|->`, `\|=>` | ◐ see reference | ✗ | ✗ | [(◐) patterns](#v12) | ◐ BMC; BDD nonoverlap/negation limits |
| Boolean `until`, `until_with` | ◐ assert/assume | ✗ | ✗ | [(◐) consequent](#v13) | ◐ BMC; plain BDD rejects |
| Boolean `s_until`, `s_until_with` | ◐ see reference | ✗ | ✗ | [(◐) shared weak path](#v14) | ◐ BMC; plain BDD lacks `s_until` |
| `strong`, `weak` | ◐ see reference | ✗ | ✗ | [❔](#v15) | ◐ unbounded forms reject plain BDD |
| Property `and`, `or` | ◐ see reference | ✗ | ✗ | [(◐) sequence-like](#v16) | ◐ BMC; BDD temporal-`or` limit |
| Property `if`/`else`; nested implication | ◐ bounded operands | ✗ | ✗ | [❔](#v17) | ◐ BMC/BDD Boolean forms |
| `nexttime`, `s_nexttime` | ◐ see reference | ✗ | ✗ | [(◐) sequence-only](#v18) | ✓ Boolean operands (BMC/BDD) |
| Bounded `always`, `s_always`, `eventually`, `s_eventually` | ◐ see reference | ✗ | ✗ | [❔](#v19) | ◐ eventuality forms need BMC |
| Unbounded `always` | ◐ operand limits | ✗ | ✗ | [(◐) consequent](#v20) | ◐ BDD; Büchi recurrence mismatch |
| Unbounded `s_eventually` | ◐ separate live/fairness | ◐ Boolean live cell | ✗ | [(◐) live/fair path](#v21) | ◐ BDD Boolean; Büchi recurrence mismatch |
| `#-#`, `#=#` | ✗ | ✗ both, assert/cover | ✗ both, assert/cover | [(◐) cover path](#v22) | ◐ BMC both; BDD cover limit |
| Abort operators; local variables | ✗ | ✗ | ✗ | [❔](#v23) | ◐ aborts accepted; locals rejected |
| `seq.triggered` | ✗ | ✗ | ✗ | [(✓)](#v24) | ✗ |
| Multiclock property | ✗ | ✗ | ✗ | [(✗)](#v25) | ◐ BMC admission; domains unverified |
| `inside`, enum/struct/int values, members, calls | ◐ values/inside; no members/calls | ◐ `inside` ✗; enum/struct/int/calls ✓ | ✗ | [❔](#v26) | ◐ BMC; BDD struct crash |
| `$isunknown` | ◐ folded 0; warnings/skips | ◐ constants ✓; input X limits | ✗ | [❔](#v27) | ◐ literal controls ✓; general X unverified |
| Several design clocks (SBY `multiclock on` for Yosys flows) | ✗ | ✓ independent Boolean checks | ✗ | [❔](#v28) | ◐ Boolean checks; domains unverified |
| `restrict property` | ◐ as `assume`; formal only | ◐ procedural ✓; explicit clock ✗ | ✗ | [(◐) assumption path](#v29) | ◐ module ✓; procedural crash |

This table is not an exhaustive grammar test. Where a row groups several forms,
the comment names the checked form or qualified subset. In particular,
syntax acceptance, bounded validity and unbounded proof are different evidence.
An unmentioned alternative in a qualified cell is not implicitly accepted;
the evidence page records the exact tested forms and any unverified scope.
Verilator's finite simulation observations are discussed separately in
[Simulation: accepted syntax and observation objectives differ](#simulation-accepted-syntax-and-observation-objectives-differ).

## What we learned from other tools and corpora

The matrix above says what each flow accepts. This section collects what cross-checking with other free tools and public corpora taught us, with links to the detailed evidence. The tools that sva2yosys depends on (Slang, Yosys) are covered in [`architecture.md`](architecture.md#slang-frontend-findings); EBMC, Verilator and the corpora are not dependencies, only oracles and points of comparison.

**EBMC** (a free model checker that reads SVA natively; engines: bounded model checking, BDD, and a Büchi route with Spot).
- Its semantics differ from the standard in several places, which is why those areas are excluded from our comparisons by name and not compared: `disable iff` timing (it reads internal registers before the edge's update and only at the attempt's start), values before the first tick, gated `$past`, pending matches of goto ranges, `intersect` and `within`. Its Büchi route checks recurrence as the CTL formula `AF AG` and refutes true recurrence properties, so we never use it for recurrence.
- The engine is part of the result. BMC accepts goto and nonconsecutive forms and delay ranges, plain BDD refuses several of them (first-match, nonoverlapping implication, temporal negation and `or`, weak and strong `until`: `1 s_until b` is unsupported while `1 s_until_with b` is refuted), a struct counterexample crashed BDD, and a bounded pass of a strong obligation is not a proof. See [EBMC: bounds and engines](#ebmc-bounds-and-engines-are-part-of-the-result) and the [capability measurements](#historical-ebmc-capability-measurements).
- Results: on the EBMC regression suite 391 of 749 property lines agree with the upstream answers and none disagree without a hand-derived reason ([layer 9](validation.md#layer-9-ebmc-known-answers)); on the hand-derived trace tables, 236 comparisons show no disagreement, with two errors of EBMC's own frontend on an `if` branch that is an unparenthesized sequence ([layer 10](validation.md#layer-10-the-ebmc-and-spot-differential)).

**Verilator** (a free simulator, used for finite simulation observations).
- Its observations are recorded as observations, never as oracles. Fourteen native probes cover the interpretation rows I1–I11 and D1; thirteen execute and one reports unsupported syntax (`strong` in an `assert`). Seven results changed between the 5.047 and 5.053 builds, so each is pinned to its version.
- Examples: a `disable iff` that reads a register is rejected, because the monitors use the values before the edge while IEEE evaluates the condition after the edge's updates; Verilator's own evaluation differs from that post-update reading (I2), recorded as a separate observation; its implication-cover actions fire only where the antecedent is true on the probed trace (I5); and on mixed-width `inside` lists it uses a common width but keeps each item's signedness (I11). See [Simulation: accepted syntax and observation objectives differ](#simulation-accepted-syntax-and-observation-objectives-differ) and [`interpretation-probes.md`](interpretation-probes.md).

**Native Yosys and yosys-slang** (on the probed sources and builds, Yosys 0.63 and 0.70). `read_verilog -formal -sv` accepts no property with an explicit clock event and, in a clocked `always` block, only Boolean conditions and unbounded `s_eventually`; `read_slang` accepts the Boolean subset and rejects implication, delays and `eventually` as unsupported SVA. They differ on `bind` (a syntax error in the first since Yosys 0.70, applied by the second). See [Native syntax depends on context](#native-syntax-depends-on-context), [The same sources through yosys-slang](#the-same-sources-through-yosys-slang) and the [frontend findings](architecture.md#yosys-frontend-findings).

**Corpora.**
- Known answers: of the 16 files of the Yosys SVA tests (32 variants), 24 variants give the upstream verdict under SBY (11 pass, 13 fail), one deviation is not caused by sva2yosys, one is rejected and six do not apply. The SBY examples are checked against their own expected results. The corpora and their pinned commits are in [`validation.md`](validation.md#corpora).
- What real code uses: the acceptance survey of five public corpora (2132 directives: 1072 accepted, 720 rejected with a located message, 340 skipped as X-checks) shows which constructs matter in practice. The most frequent rejection reasons are parameters that a monitor cannot be keyed on (a struct, string, real or type parameter; 260), indexed part selects (256), a `disable iff` condition that reads a register (44), `disable iff` below the top of a property (26), member access (26) and `inside` lists with non-uniform widths or signedness (25). These counts describe the surveyed corpora, which are not a representative sample.

**How the findings are used.** A disagreement with another tool is never read as our error: it is derived by hand from IEEE 1800. It then becomes one of three things: an exclusion by name from a comparison, an interpretation row ([`validation.md`](validation.md#5-interpretations-and-known-deviations) §5), or a counterexample trace case.

## Builds and evidence categories

In the [support matrix](supported-sva.md#status-levels), the **Slang** column means the pyslang elaboration/
export adapter, not the yosys-slang plugin. Its marks concern retained frontend
structure: they do not promise that conversion or monitor generation accepts it.
The [boundary fixture](../tests/functional_tests/slang_frontend/fixtures/support_boundaries.sv)
records the distinction. The cover-rule row is `N/A` because it is a semantic
admission rule. First-match and explicitly clocked past have export gaps; several
other unsupported compiler features have complete relevant exports. Simple
package-qualified calls also pass check/generation probes, which is code-generation
evidence rather than complete semantic/generated-code support.

| Flow | Build or source | Evidence |
|---|---|---|
| sva2yosys + Yosys/SBY | Current checked-out compiler; pyslang 12.0.0 | Project semantic/generated-code tests and the [language reference](sva-reference.md) |
| Native Yosys | `Yosys 0.70+286 (git sha1 63eecf3dd, Release, GNU /usr/bin/x86_64-w64-mingw32-g++ 15.2.1)` (six maintained probes, native SBY controls); the historical measurements and the 47 additional families used `Yosys 0.63+173 (git sha1 66306a8ca-dirty, x86_64-w64-mingw32-g++ 13.2.1 -O3)` | Historical construct/context probes and 477-case survey (0.63); six maintained probes (0.70) |
| SBY | `v0.70-9-g79fc9ed` from OSS CAD Suite 20261031 (historical controls: `v0.63-4-gab5bf1c`, 20260325) | Native Boolean controls through ABC PDR; project monitor checks use the documented safety/live workflows |
| yosys-slang | Bundled OSS CAD Suite plugin; the 0.70 binary is separately pinned by SHA-256 below | Historical probes and 47 additional families (0.63 binary, all rejected); six maintained sources re-measured with the 0.70 binary: three Boolean sources accepted, three rejected; ignore-assertions control elaborates with zero checks |
| Yosys+Verific | Public Yosys importer `0ad867ab51111cd5b083b28f934c9a764cca33f3`; SBY documentation `79fc9eddca5e33a28cc2f2cd19c9825efd9a7255` | **Not run; entries are inferred from public documentation and source code**, audited below |
| EBMC | `6.1 (ebmc-6.1)` | Historical and additional capability probes; calibrated [differential tests](../tests/functional_tests/external/ebmc_differential/CONTRACT.md), with engine-specific limits |
| Verilator | `5.053 devel rev v5.052-651-g92ac4051b (mod)` (the earlier observations used 5.047) | Separate finite [simulation observations](interpretation-probes.md) |

The compact matrix uses **✓** for supported in the stated scope, **✗** for
rejected, **◐** for partial/qualified support, and **❔** for unknown or untested.
Its short comments identify the relevant subset. These signs describe capability,
not the kind of evidence: this page records whether a cell comes from execution,
bounded checking, a lasso, public documentation or source inspection.
In the Yosys+Verific column only, marks are parenthesized, **(✓)**, **(✗)**, **(◐)**:
inferred from public documentation or source, never executed.
For the source audit below, `D` means documentation-only, `S` importer source,
and `NM` not established by those materials. These are not executed results.
A sva2yosys checkmark always refers to its [current restrictions](sva-reference.md).

The SBY documentation describes a particular commercially supplied frontend
integration, not every build containing a Verific license. The public importer
is only part of that flow. Its source does not establish what proprietary
elaboration produces for every source construct. No commercial tool was run,
and these entries are not used as a semantic oracle or compiler-design evidence.

## Native syntax depends on context

The following are the exact checked sources. All six elaborate through Slang and
pass both sva2yosys check and generation modes: one accepted directive, zero
skipped/rejected, exit 0. A successful lowering is not itself a proof.

The native command is `read_verilog -formal -sv FILE; hierarchy -top top; proc`.
Accepted cases additionally require exactly one `$check` with the stated flavor.
The tautology is proved by ABC PDR; the free-input assertion has a counterexample.

<!-- sva-example: boolean-pass -->
```systemverilog
module top(input logic clk, a);
  always @(posedge clk) checked: assert property (a || !a);
endmodule
```
<!-- /sva-example -->

Native: accepted, flavor `assert`; SBY `prove`: **PASS**.

<!-- sva-example: boolean-fail -->
```systemverilog
module top(input logic clk, a);
  always @(posedge clk) checked: assert property (a);
endmodule
```
<!-- /sva-example -->

Native: accepted, flavor `assert`; SBY `prove`: **FAIL** (`a` can be false).

<!-- sva-example: explicit-clock -->
```systemverilog
module top(input logic clk, a);
  checked: assert property (@(posedge clk) a);
endmodule
```
<!-- /sva-example -->

Native: rejected, syntax diagnostic `unexpected '@'`.
The Boolean control above isolates the change of assertion context.

## Temporal syntax is a separate admission question

These procedural forms retain the accepted clock context and isolate the operator.

<!-- sva-example: implication -->
```systemverilog
module top(input logic clk, a, b);
  always @(posedge clk) checked: assert property (a |-> b);
endmodule
```
<!-- /sva-example -->

Native: rejected, syntax diagnostic `unexpected '>'`.

<!-- sva-example: delay -->
```systemverilog
module top(input logic clk, a, b);
  always @(posedge clk) checked: assert property (a ##1 b);
endmodule
```
<!-- /sva-example -->

Native: rejected, syntax diagnostic `unexpected '#'`.
These two cases do not establish every native frontend grammar restriction.

<!-- sva-example: eventually -->
```systemverilog
module top(input logic clk, a);
  always @(posedge clk) checked: assert property (s_eventually a);
endmodule
```
<!-- /sva-example -->

Native: accepted, flavor `live`. This probe checks admission and cell generation,
not the infinite-trace verdict. Opposite liveness/fairness behavioral controls
are in the [Yosys contract](../tests/functional_tests/yosys_contract/CONTRACT.md),
points 11–14. Thus the native frontend's limitations should not be summarized as
“no concurrent SVA.” sva2yosys emits a monitor for supported temporal constructs
and gives strong obligations a separate [live task](usage.md#strong-properties-the-prove-and-the-live-task).

## The same sources through yosys-slang

The configured Yosys installation's plugin was checked on the same six sources
(Yosys 0.70+286, where `read_slang` is also built into Yosys; the tests still
load the plugin file explicitly). **Three are accepted** with one assertion cell each: the two
Boolean procedural assertions (`boolean-pass`, `boolean-fail`) and the Boolean assertion with an
explicit clock (`explicit-clock`). Opposite SBY controls back the acceptance of the first two
(`boolean-pass` proved, `boolean-fail` refuted), so it is not a parse success alone.
**Three are rejected** (`implication`, `delay`, `eventually`) with a diagnostic that names the
source line but no column: `FILE:LINE: ERROR: encountered unsupported SVA feature`.
The first measurement, with the Yosys 0.63 plugin (SHA-256
`e081852a88d3c9357c677f7f8d7d7074d0f634ecaf3d82cbfe8033ef7b9e623c`), rejected all six with a
located `SVA unsupported` diagnostic. The historical observations below cover additional
forms. They retain the 0.63 plugin; the six maintained cases do not
rerun or classify that whole set, so a rejection there is not a claim about 0.70.

The current plugin's SHA-256 is
`f0da835f9b07ad2ce74c63ba168c7b84780da590aff80bdbb0a50e1c1a873d68` (Windows build). The Linux build
of the same release (`Yosys 0.70+286 (git sha1 63eecf3dd, Release, Clang /usr/bin/clang++ 21.1.8)`)
has the plugin `9db09219931d851901ef2d23071b7b80462472f35f11353a115b6b478b8b977b`; the tests use
the pin of their platform, and the observations are those of the same source revision.
The [pin](../tests/functional_tests/external/tool_comparison/fixtures/native.json)
locates it relative to the configured Yosys installation and guards its bytes
separately from the host executable's version. Tests explicitly load that binary.
The reproduction commands are:

```text
plugin -i "PLUGIN_FILE"
read_slang -D FORMAL --top top FILE
```

As a positive elaboration control, the Boolean source is also read with
`--ignore-assertions`; hierarchy/proc must then contain zero `$check` cells.
That option removes the verification directives, so successful elaboration with
it is not support for the property. Plugin-load errors and unrelated failures
do not satisfy the located-unsupported diagnostic check.

## Additional measurements (Yosys 0.63 plugin)

The remaining executable-tool gaps were checked with **47 source families, each
with `CHECK=1` and `CHECK=0` controls**. Standalone Slang
`12.0.0+545f88b2e` accepted all 94 sources. Each then ran through native Yosys,
the pinned yosys-slang plugin, EBMC BMC6 and EBMC BDD. The 15 accepted native
families additionally ran through SBY/ABC PDR with opposite controls: **15 PASS,
15 FAIL**, after guarding the initial sample of history expressions. These are
capability measurements; they do not expand the calibrated differential oracle.

The source template is:

```systemverilog
module top(input bit clk, clk2, a);
  localparam bit CHECK = 1; // Repeat with 0.
  // DECLARATIONS
  // DIRECTIVE
endmodule
```

Commands (substitute the configured executable and plugin paths):

```text
slang --lint-only --top top probe.sv
yosys -p "read_verilog -formal -sv probe.sv; hierarchy -check -top top; proc; write_json checks.json"
yosys -p "plugin -i PLUGIN_FILE; read_slang -D FORMAL --top top probe.sv; hierarchy -check -top top; proc; write_json checks.json"
ebmc --systemverilog --top top --bound 6 probe.sv
ebmc --systemverilog --top top --bdd probe.sv
```

Native acceptance required retained verification cells in the JSON netlist.
Every plugin source was rejected: mostly located `SVA unsupported`, with an
unimplemented-declaration diagnostic for the named sequences/triggered probes.
Default clocking and procedural `iff` also have located timing diagnostics.
No successful elaboration with ignored assertions is counted as acceptance.
All native rejections below were syntax errors, not launch failures.

### Clocks, declarations and restrictions

Unless stated otherwise, insert the shown assertion at module level.
`A(P)` below abbreviates `assert property (@(posedge clk) P);` only in this table.
EBMC “pair works” means bounded pass/refutation in BMC and proof/refutation in
BDD for the two controls, not complete support for the feature's IEEE semantics.

| Declarations / directive | Native Yosys | EBMC BMC6 / BDD |
|---|---|---|
| `assert property (@(negedge clk) CHECK);` | rejected at `@` | pair works / pair works |
| `always @(negedge clk) assert property (CHECK);` | accepted; SBY PASS/FAIL | pair works / pair works |
| `assert property (@(posedge clk iff 1'b1) CHECK);` | rejected at `@` | both reject `iff` syntax |
| `assert property (@(posedge clk iff !CHECK) 0);` | rejected at `@` | both reject `iff` syntax |
| `always @(posedge clk iff 1'b1) assert property (CHECK);` | rejected | both reject `iff` syntax |
| `default clocking cb @(posedge clk); endclocking` then `assert property (CHECK);` | rejected at `default` | pair works / pair works |
| `default disable iff (CHECK);` then `A(0)` | rejected at `default` | **both controls refuted / both controls refuted** |
| `A(disable iff (CHECK) 0)` | rejected at `@` | pair works / pair works |
| `sequence s; CHECK ##1 CHECK; endsequence` then `A(s)` | declaration rejected | pair works / pair works |
| `property p; CHECK; endproperty` then `A(p)` | declaration rejected | pair works / pair works |
| `sequence s; 1; endsequence` then `A(s.triggered == CHECK)` | declaration rejected | conversion rejection / conversion rejection |
| `assert property (@(posedge clk) 1 \|=> @(posedge clk2) CHECK);` | rejected at `@` | bounded pair works / unsupported |
| `restrict property (@(posedge clk) a);` then `always @(posedge clk) assert property (a == CHECK);` | rejected at `@` | assumption pair works / assumption pair works |
| `always @(posedge clk) restrict property (a);` then `always @(posedge clk) assert property (a == CHECK);` | accepted; assume + assert; SBY PASS/FAIL | **invariant crash / invariant crash** |
| `always @(posedge clk) assert property (CHECK);` and the same on `clk2` | two retained checks; SBY PASS/FAIL with `multiclock on` | Boolean pairs work / Boolean pairs work |

The default-disable positive control should mask the false assertion, but EBMC
refutes it; the otherwise identical inline-disable control behaves as expected.
This is a measured default-disable limitation in addition to the historical
post-NBA timing disagreement. Module-level restrict is reported as `ASSUMED`
and constrains the following assertion; its procedural form crashes in both
EBMC engines (`verilog_expr.h`, `to_verilog_assume_statement` precondition).

The multiclock constant checks establish admission and distinguish true from
false operands, but **do not establish correct clock-domain semantics in EBMC**.
Its displayed property drops clock notation. The SBY experiment concerns two
independent native Boolean checks; it says nothing about multiclock temporal SVA.
Its complete configuration is:

```text
[options]
mode prove
multiclock on

[engines]
abc pdr

[script]
read_verilog -formal -sv probe.sv
prep -top top

[files]
probe.sv
```

Other native controls use the same configuration with `multiclock on` omitted.

### Expressions inside verification directives

Each expression below is the body of
`always @(posedge clk) assert property (EXPRESSION);`.
All these forms are rejected by the plugin **in assertion context**; this does
not classify its support for the same expression in ordinary RTL.

| Declarations / expression | Native Yosys | EBMC BMC6 / BDD |
|---|---|---|
| `(3 inside {[1:4]}) == CHECK` | rejected | pair works / pair works |
| `typedef enum logic [1:0] {A=1, B=2} e_t; e_t v=A;` / `(v==A) == CHECK` | accepted; SBY PASS/FAIL | pair works / pair works |
| `typedef struct packed {logic [3:0] field;} s_t; s_t v=4'b1010;` / `(v==4'b1010) == CHECK` | accepted; SBY PASS/FAIL | pair works / positive proved, **negative crashes** |
| Same struct / `(v.field==4'b1010) == CHECK` | accepted; SBY PASS/FAIL | pair works / positive proved, **negative crashes** |
| `int v=7;` / `(v==7) == CHECK` | accepted; SBY PASS/FAIL | pair works / pair works |
| `function automatic logic f(input logic v); f=v; endfunction` / `f(CHECK)` | accepted; SBY PASS/FAIL | pair works / pair works |
| `!$isunknown(4'b1010) == CHECK` | accepted; SBY PASS/FAIL | pair works / pair works |
| `$isunknown(1'bx) == CHECK` | accepted; SBY PASS/FAIL | pair works / pair works |

The struct BDD failures are invariant crashes in `trans_trace_netlist.cpp`,
`bitstring_to_expr`, during counterexample production, not syntax rejection.
The known/X literal controls do not establish general four-state input semantics;
in particular they do not overturn the historical native-input `$isunknown`
counterexample below.

All five sampled functions were also checked separately with declarations
`bit seen=0; always @(posedge clk) seen<=1;` and assertion body
`!seen || ((F(a) || !F(a)) == CHECK)`, for each of `$past`, `$rose`, `$fell`,
`$stable`, `$changed`. Native retains a check and gives SBY PASS/FAIL; both EBMC
engines give the expected pairs. These controls establish Boolean-context
admission after a history sample, not first-sample defaults or complete sampled
semantics. Unguarded initial experiments failed native SBY's positive control
because history registers were unconstrained initially; the final controls
deliberately exclude that sample rather than claiming default-value agreement.

### Grouped temporal rows and followed-by operators

The following additional inherited-clock assertion bodies were each rejected
by native Yosys and the plugin: `1 s_until_with CHECK`, `s_nexttime [2] CHECK`,
`always [1:2] CHECK`, `s_always [1:2] CHECK`, `eventually [1:2] CHECK`,
`s_eventually [1:2] CHECK`, and all four `accept_on(0) CHECK`,
`reject_on(0) CHECK`, `sync_accept_on(0) CHECK`, `sync_reject_on(0) CHECK`.
EBMC BMC gives expected pairs for all; BDD does too except both bounded
eventuality forms are unsupported. The false abort conditions only check
admission and their neutral behavior, not asynchronous abort scheduling.

The property-local form
`property p; int x; (1,x=1) |=> (x == CHECK); endproperty` followed by `A(p)`
is rejected by all three frontends (syntax/declaration/SVA diagnostics).

**Both `#-#` and `#=#` were tested in assert and cover directives, with both
explicit module-level clocks and procedural inherited clocks.** Native Yosys
rejects the former context at `@` and the latter at `#`; the plugin rejects all
with located `SVA unsupported`. Thus neither operator is implicitly accepted.
The bodies are `1 #-# CHECK` and `1 #=# CHECK`:

| Directive/body | EBMC BMC6 | EBMC BDD |
|---|---|---|
| assert, either operator | expected bounded/refuted pair | expected proved/refuted pair |
| cover, `#-#` | reachable / not reached through bound 6 | reachable / unreachable |
| cover, `#=#` | reachable / not reached through bound 6 | unsupported for both controls |

The Yosys+Verific column remains **not run**, with its separate source-audit
qualification about the cover path. None of these free-tool executions change
the evidence category of that column.

### Engine limitations versus calibration coverage

The compact EBMC cells name language forms and concrete engine or semantic
limits. For example, a delay range requiring BMC, plain BDD refusing a first-match
probe, and a struct counterexample crashing BDD are capability observations.
“Compared on a calibrated lasso” instead describes our evidence coverage; it
does not mean EBMC accepts only a particular lasso-shaped language subset.
The allow-lists and finite/infinite objectives remain in the EBMC sections below.

Three further paired probes used the same template and commands. Native Yosys
and the plugin reject all three forms:

| Directive body/context | EBMC BMC6 | EBMC plain BDD |
|---|---|---|
| Procedural `assert property (1 \|-> (1 \|-> CHECK));` | expected bounded pass/refutation | expected proof/refutation |
| Explicit-clock `strong(1 ##[1:$] CHECK)` | bounded pass for both values | unsupported for both |
| Explicit-clock `weak(1 ##[1:$] CHECK)` | bounded pass for both values | unsupported for both |

The false strong operand is an infinite obligation that BMC does not discharge;
its bounded pass must not be promoted to proof. Waiting indefinitely in the weak
control is permitted. These outcomes are different from syntax rejection and
explain the unbounded engine qualifications in the matrix. The existing Büchi
strong-sequence checks give additional evidence through a different route;
they do not make plain BDD accept these sources.

Likewise, the historical BMC controls accept goto/nonconsecutive forms, whereas
pending ranged continuations can differ from our IEEE-derived expectations.
Intersection/within pending-match differences are semantic limits, distinct from
the exclusion of those operators from our oracle allow-list. Plain BDD rejects
the recorded nonoverlapping implication, temporal negation/or, weak-until and
strong-until examples, while accepting the documented Boolean and other controls.
For example, `1 s_until b` is unsupported in plain BDD, whereas `1 s_until_with b`
is refuted by that engine. The source bodies and per-engine results above identify the tested contexts;
engine labels are not assertions about every possible operand or simplification.

## Historical frontend measurements

The first frontend comparison executed **38 construct probes and ten
procedural-form probes** through both frontends in the configured OSS CAD Suite
(Yosys 0.63). These are records of an earlier execution, not inferences, and the
historical inputs have not been executed again. The historical plugin was not
separately hashed, so the plugin hash of the additional measurements must not be
retroactively assigned to it.

The 38 sources used this template, with `BODY` replaced by the expressions below:

```systemverilog
module t(input clk, input a, input b);
  p: assert property (@(posedge clk) BODY);
endmodule
```

Commands were `read_verilog -formal -sv FILE; prep -top t; stat` and
`plugin -i slang; read_slang -D FORMAL FILE; prep -top t; stat`.
Native Yosys rejected all 37 inline bodies at `@`. The plugin rejected all 37
with `SVA unsupported`. This establishes rejection of those source forms, **not
independent native grammar tests for operators after the rejected clock**.
Grouped bodies below are separate inputs, separated by semicolons.

| Construct | Exact inline bodies tested (Yosys 0.63) |
|---|---|
| Boolean / sampled functions | `a`; `$past(a) \|-> b`; `$rose(a) \|-> b` |
| Implication | `a \|-> b`; `a \|=> b` |
| Delay | `a ##1 b`; `a ##[1:3] b`; `a ##[1:$] b` |
| Consecutive repetition | `a[*2] ##1 b`; `a[*1:3] ##1 b`; `a[*1:$] ##1 b` |
| Goto / nonconsecutive | `a[->2] \|-> b`; `a[=2] ##1 b` |
| Sequence composition | `(a ##1 b) or (b ##1 a)`; `(a ##1 b) and (b ##2 a)` |
| Intersection / containment | `(a ##[1:3] b) intersect (b[*3])`; `(a ##1 b) within (b[*4])` |
| Throughout / first match | `a throughout (b ##2 b)`; `first_match(a ##[1:3] b) \|-> b` |
| Negation | `not (a ##1 b)` |
| Property composition | `(a \|-> b) and (b \|-> a)`; `(a \|-> b) or (b \|-> a)` |
| Branching / nesting | `if (a) (b \|-> a) else (a \|-> b)`; `a \|-> (b \|-> a)` |
| Nexttime | `nexttime [2] a` |
| Always | `always [1:3] a`; `a \|-> always b` |
| Eventuality | `s_eventually [1:3] a`; `a \|-> s_eventually b` |
| Until | `a until b`; `a s_until b`; `a until_with b` |
| Strength | `strong(a ##[1:3] b)`; `weak(a ##1 b)` |
| Disable / followed-by / abort | `disable iff (b) a \|-> b`; `a #-# b`; `accept_on (b) a ##1 a` |

The 38th source declared `sequence s1; a ##1 b; endsequence` before
`assert property (@(posedge clk) s1)`. Native Yosys rejected the declaration at
`;`; the plugin reported an unimplemented feature. That result does not classify
every named declaration, default clocking or default disable form.

The ten procedural sources instead used
`always @(posedge clk) assert property (BODY);` in the same module:

| BODY | Native Yosys | yosys-slang |
|---|---|---|
| `a` | accepted | `SVA unsupported` |
| `$past(a) && b` | accepted | `SVA unsupported` |
| `a \|-> b` | rejected at `>` | `SVA unsupported` |
| `a \|=> b` | rejected at `TOK_BIT_OR_ASSIGN` | `SVA unsupported` |
| `a ##1 b` | rejected at `#` | `SVA unsupported` |
| `a \|-> ##[1:3] b` | rejected at `>` | `SVA unsupported` |
| `a[*2]` | rejected at `*` | `SVA unsupported` |
| `not a` | rejected at `TOK_PRIMITIVE` | `SVA unsupported` |
| `s_eventually a` | accepted | `SVA unsupported` |
| `a until b` | rejected at `TOK_ID` | `SVA unsupported` |

The separate **native survey** used 477 accepted trace cases in four
forms: module-level explicit clock, procedural plus explicit property clock,
module-level without the explicit clock, and procedural inherited clock.
The first two rejected all 477. The latter two accepted 14 and 16 respectively;
they also **removed `disable iff`**, so acceptance does not apply to the original
disabled property. The 16 procedural acceptances comprised three Boolean cases
(including `$fell(a) || $stable(b) || !$changed(c)`) and 13 Boolean
`s_eventually` cases, one with `$rose(b)`. This corroborates the Boolean-context
qualification, without establishing arbitrary temporal use of sampled functions.

The **`$isunknown` probe** was accepted by native Yosys and lowered
through `=== 1'bx` / `=== 1'bz` tests. With a four-bit input `a` and uninitialized
register `r` assigned `r <= a` on each positive edge, ABC PDR refuted both
procedural assertions `!$isunknown(a)` and `!$isunknown(r)` at step 1. The X
constants become unconstrained in that formal flow: parsing this function does
not provide a simulation-style detector of unknown input values. This is a
qualified measured result, rather than either syntax rejection or unknown support.

## Historical EBMC capability measurements

The **EBMC 6.1 feature study** retained individual sources, hand-derived
expectations and engine logs. Capability evidence from that study is broader than
the calibrated differential subset. Excluding a feature from an IEEE semantic
oracle does not erase evidence that the tool accepts and checks a representative.
The compact table uses qualified signs for these measurements, not a complete
IEEE-conformance checkmark. They have not been rerun since.

For the inline examples below, the study used this common design:

```systemverilog
module main(input bit clk);
  integer n = 0;
  always @(posedge clk) if (n < 7) n <= n + 1;
  wire a = (n == 0);
  wire b = 1'b0;
  wire c = (n == 2);
  p: assert property (@(posedge clk) BODY);
endmodule
```

Each semicolon-separated body below is a separate probe. The command was
`ebmc --systemverilog --top main --bound 6 FILE`; the BDD column used
`ebmc --systemverilog --top main --bdd FILE`. `REFUTED` means a counterexample was found,
`bounded` means only proved up to bound 6, and `PROVED` is the BDD verdict.
`unsupported` is an engine restriction after frontend acceptance, not a syntax
rejection. A refuted deliberately false assertion is the expected control result.

| BODY / tested feature | BMC6 | BDD |
|---|---|---|
| `n!=2` | REFUTED | REFUTED |
| `1 ##2 b`; `b[*2]` | REFUTED | REFUTED |
| `1 ##[1:2] b`; `b[*1:2]`; `b[+]` | REFUTED | unsupported |
| `strong(1 ##[1:$] b)`; `b[->2]`; `b[=2]` | bounded | unsupported |
| `(1 ##1 b) and (1 ##1 1)` | REFUTED | REFUTED |
| `(1 ##1 b) or (1 ##2 b)` | REFUTED | unsupported |
| `(1 ##1 b) intersect (1 ##1 1)` | REFUTED | unsupported |
| `(1 ##1 b) within (1 ##2 1)` | REFUTED | unsupported |
| `b throughout (1 ##1 1)`; `first_match(1 ##[1:2] b)` | REFUTED | unsupported |
| `1 \|-> b`; `not 1`; `b and 1`; `b or b`; `if (1) b else 1` | REFUTED | REFUTED |
| `1 \|=> b` | REFUTED | unsupported |
| `(nexttime b) and (s_nexttime b)` | REFUTED | REFUTED |
| `(nexttime b) or (s_nexttime b)`; `not (nexttime 1)` | REFUTED | unsupported |
| `nexttime b`; `s_nexttime b`; `always b` | REFUTED | REFUTED |
| `always [1:2] b`; `s_always [1:2] b` | REFUTED | REFUTED |
| `eventually [1:2] b`; `s_eventually [1:2] b` | REFUTED | unsupported |
| `s_eventually b`; `1 s_until_with b` | bounded | REFUTED |
| `1 until b`; `1 until_with b`; `1 s_until b` | bounded | unsupported |
| `strong(1 ##1 b)`; `weak(1 ##1 b)` | REFUTED | REFUTED |
| `accept_on(0) b`; `reject_on(0) b`; `sync_accept_on(0) b`; `sync_reject_on(0) b` | REFUTED | REFUTED |
| `accept_on(1) b` | bounded | PROVED |
| `reject_on(1) 1` | REFUTED | REFUTED |
| `1 #-# b`; `1 #=# b` | REFUTED | REFUTED |
| `disable iff (n==2) n!=2` | bounded | PROVED |
| `!$isunknown(4'b1010)` | bounded | PROVED |

Other measured forms and the qualifications needed for their compact cells:

- **Contexts/declarations:** procedural `always @(posedge clk) assert property
  (n!=2)` and `default clocking cb @(posedge clk); endclocking` with
  `assert property(n!=2)` were refuted by BMC and BDD. Named no-argument sequence
  `s` containing `1 ##1 b` was refuted by both. Named property `pp` containing
  `1 |=> b` was refuted by BMC but unsupported by BDD; a property formal `x`
  was accepted by BMC, whereas tested sequence-formal spellings were rejected.
  These are specific declaration forms, not complete argument support.
- **Disable:** both inline disable above and `default disable iff (n==2)` with
  `n!=2` produced bounded/PROVED results. The hand-derived IEEE post-NBA result
  is failure, so the cell states a timing difference. The false-disable control
  `disable iff (0) n!=2` was refuted. Syntax acceptance does not settle scheduling.
- **History:** `$past(n)` and `$past(n,2)` with startup guards were bounded at six
  transitions; their BDD runs timed out at the study's 12-second cutoff. The
  counter saturates, so these guards do not make the properties globally true.
  `$rose(c)`, `$fell(c)`, `$stable(c)` and `$changed(c)` controls were bounded by
  BMC and proved by BDD. Separate gated-`$past` and first-sample `$rose` controls
  disagreed with hand-derived expectations. `$sampled` was rejected.
- **Locals/triggered:** a property-local `integer x` with `(1,x=n)` was rejected
  by both engines' frontend; `!s.triggered` for `sequence s; 1 ##1 1; endsequence`
  was also rejected. The abort controls above establish specific accepted abort
  forms, not local-variable support or complete asynchronous scheduling semantics.
- **Expressions:** separate enum, packed-struct, function-call and `inside`
  frontend controls were refuted as expected by BMC; BDD also refuted the enum,
  function and `inside` controls but rejected the struct control. This does not
  classify every type/member/call combination. The `$isunknown` control above has
  a known two-state constant and establishes no X/Z-sensitive semantics.
- **Unbounded constructs:** a bounded pass is not proof of an infinite obligation.
  The study also observed permissive acceptance of the illegal standalone empty
  sequence `b[*]`. Neither that acceptance nor rejection of illegal unbounded
  `eventually b` / `s_always b` is counted as legal-feature support. Calibrated
  lasso routes below supply separate, narrower infinite-trace evidence.

## EBMC: bounds and engines are part of the result

The [allow-list](../tests/functional_tests/external/ebmc_differential/fixtures/allow_list.txt)
defines the comparable shapes. BMC covers selected finite assertions/sequences,
bounded repetition, implication, bounded negation, property `if`/`else`, and
Boolean `until`/`s_until`. It checks validity within the written samples; a pending
strong obligation at the bound is not an end-of-simulation failure. These results
do not establish earliest-failure timing or unbounded proof.

The lasso routes separately admit selected recurrence, strong-sequence,
strong-until and eventuality shapes. Plain BDD and `--bdd --buechi` have different
allow-lists; the latter requires the pinned Spot 2.16.1 translator. Recurrence
is excluded from the Buechi route. Read the engine and controls for each row;
do not generalize one route's outcome to the others.

The [finite interpretation controls](../tests/functional_tests/external/interpretations/test_ebmc_column.py)
include opposite valid/invalid constant and implication cases. Disable timing,
sampled history, X/default values, mixed `inside` sizing, procedural context and
implication-cover nonvacuity are outside that calibration. Their capability
measurements above remain valid observations, with the stated restrictions;
they do not enlarge the semantic-oracle allow-list. The separate
external EBMC regression corpus contains upstream known answers; it is not a
native EBMC execution and is not substituted for one here.

## Simulation: accepted syntax and observation objectives differ

Verilator's finite simulation observations answer a different question from the
formal tools above, and they are recorded row by row in
[`interpretation-probes.md`](interpretation-probes.md), which owns the versions,
sources, observed events, controls and limitations (a summary is in
[What we learned from other tools and corpora](#what-we-learned-from-other-tools-and-corpora)).
Finite simulation, event counts, bounded validity, formal reachability and
infinite-trace proof answer different questions, so an observation of one kind is
never read as a result of another.

## Yosys+Verific source audit

**Not run; every entry below is inferred from public documentation and source
code.** Each ID is the evidence target of exactly one compact-matrix cell.
`NM` marks a construct or source context that the inspected material does not
establish.

The pinned sources are:

- [D: SBY supported syntax and integration qualifications][vd].
- [S: Yosys sequence/property importer][vs].
- [I: Yosys directive dispatch][vi].

| ID | Basis and qualification |
|---|---|
| <a id="v01"></a>V01 | **D:** [clocking and directives][vd-clock]; explicit positive-edge concurrent property. |
| <a id="v02"></a>V02 | **NM:** procedural source enabling is not established by the inspected [syntax list][vd]. |
| <a id="v03"></a>V03 | **D:** [declarations/defaults][vd-decl]; listed convenience forms. |
| <a id="v04"></a>V04 | **D:** [clocking][vd-clock]; negative edge and clock-enable forms listed. |
| <a id="v05"></a>V05 | **D:** [reset syntax][vd-clock]; no executed scheduling equivalence claim. |
| <a id="v06"></a>V06 | **D:** [sampled functions][vd-expr]; listed functions only. |
| <a id="v07"></a>V07 | **D:** [bounded concatenation/repetition][vd-seq]. |
| <a id="v08"></a>V08 | **D/S:** [unbounded forms][vd-seq]; [nullable repetition restriction][vs-repeat] in this importer. |
| <a id="v09"></a>V09 | **D:** [goto/nonconsecutive repetition][vd-seq]. |
| <a id="v10"></a>V10 | **D:** [sequence composition][vd-seq]. |
| <a id="v11"></a>V11 | **D/S:** [first-match syntax][vd-seq] and [dedicated matching path][vs-first]. |
| <a id="v12"></a>V12 | **D:** [property patterns][vd-prop]; optional negation with sequence implications. |
| <a id="v13"></a>V13 | **D:** [until patterns][vd-prop]; expression right operand and listed implication contexts. |
| <a id="v14"></a>V14 | **D/S:** syntax listed; [strong and weak branches are shared][vs-until]. Source observation only, not an executed divergence. |
| <a id="v15"></a>V15 | **NM:** no complete strong/weak source-to-importer contract established by [D][vd-prop] or [S][vs-property]. |
| <a id="v16"></a>V16 | **S:** [sequence-like and/or paths][vs-and]; arbitrary property operands not established. |
| <a id="v17"></a>V17 | **NM:** arbitrary property branches/nested implications not established by [D][vd-prop] or [S][vs-property]. |
| <a id="v18"></a>V18 | **S:** [equal-bound nexttime path][vs-next]; sequence operand, not arbitrary properties. |
| <a id="v19"></a>V19 | **NM:** complete bounded always/eventually behavior not established by [the inspected paths][vs-until]. |
| <a id="v20"></a>V20 | **S:** [always as an implication consequent][vs-until]; other contexts not established. |
| <a id="v21"></a>V21 | **S:** [eventuality detection][vs-eventually] and [live/fair emission][vs-live]; cover rejected there, overlapping multicycle exactness not established. |
| <a id="v22"></a>V22 | **S:** [followed-by dispatch][vs-property] guarded by cover mode; other modes not established. |
| <a id="v23"></a>V23 | **NM:** full abort/local-variable support not established; [match-item diagnostic][vs-match] alone does not classify every form. |
| <a id="v24"></a>V24 | **D:** [sequence-triggered expressions][vd-expr]. |
| <a id="v25"></a>V25 | **D:** [mixed-domain properties explicitly excluded][vd-clock]. |
| <a id="v26"></a>V26 | **NM:** the grouped inside/types/members/calls claim exceeds [documented expression coverage][vd-expr]. |
| <a id="v27"></a>V27 | **NM:** unknown-value semantics not established by [D][vd-expr] or the [SVA importer][vs]. |
| <a id="v28"></a>V28 | **NM:** [mixed-domain property exclusion][vd-clock] does not establish support for several independent clocks in a whole SBY design. |
| <a id="v29"></a>V29 | **S:** [directive dispatch][vi] places restrict in the assumption collection; [the import loop][vi-import] processes that collection as assumptions. |

No comparison of implementation strategies is made here: it is unnecessary for a
capability overview and is not an argument for this compiler's architecture.
Absence from a documentation list is not proof of rejection. An importer branch
also does not establish that every proprietary frontend version generates it.

## Reproduction and maintenance

Configure existing tools in [`.env`](../.env.example); no tools are installed or
found on PATH by these tests. Run from the repository root:

```sh
# Local catalog, source/snippet freshness and evidence-link checks.
uv run pytest tests/functional_tests/external/tool_comparison
# Both frontends on the same six sources, plus Boolean/control checks.
uv run --env-file .env pytest --external-tools=yosys,sby tests/functional_tests/external/tool_comparison
# Existing simulator topics and calibrated finite EBMC controls.
uv run --env-file .env pytest --external-tools=verilator,ebmc --extended tests/functional_tests/external/interpretations
# All calibrated EBMC/Spot trace comparisons, including lasso controls.
uv run --env-file .env pytest --external-tools=ebmc,spot --slow tests/functional_tests/external/ebmc_differential
```

The native [pin](../tests/functional_tests/external/tool_comparison/fixtures/native.json)
guards exact Yosys version and source/catalog digest. Native acceptance requires
cell generation; a rejection requires the source-specific syntax diagnostic.
The [catalog](../tests/functional_tests/external/tool_comparison/fixtures/examples.json)
pins both CLI modes, and every displayed native source is freshness-checked.
Unexpected infrastructure errors fail tests. Tool outputs stay in temporary dirs.

The six maintained sources are regression-checked; the historical measurements
and additional 47-family campaign are evidence records, not additional
maintained test families. Retain
their tool versions and context when updating a cell. The ❔ symbol is reserved for a genuinely
unestablished case, not merely a case outside the differential oracle's allow-list.

Update a compact cell and its detailed evidence together. Changed inputs/builds
require deliberate reclassification, never automatic capture of new expected
answers. For source-only entries, re-audit the cited revision and restrictions;
do not replace “not run” with an executed claim without actual controls.

[vd]: https://github.com/YosysHQ/sby/blob/79fc9eddca5e33a28cc2f2cd19c9825efd9a7255/docs/source/verific.rst
[vd-decl]: https://github.com/YosysHQ/sby/blob/79fc9eddca5e33a28cc2f2cd19c9825efd9a7255/docs/source/verific.rst#L38-L54
[vd-expr]: https://github.com/YosysHQ/sby/blob/79fc9eddca5e33a28cc2f2cd19c9825efd9a7255/docs/source/verific.rst#L60-L71
[vd-seq]: https://github.com/YosysHQ/sby/blob/79fc9eddca5e33a28cc2f2cd19c9825efd9a7255/docs/source/verific.rst#L73-L106
[vd-prop]: https://github.com/YosysHQ/sby/blob/79fc9eddca5e33a28cc2f2cd19c9825efd9a7255/docs/source/verific.rst#L108-L128
[vd-clock]: https://github.com/YosysHQ/sby/blob/79fc9eddca5e33a28cc2f2cd19c9825efd9a7255/docs/source/verific.rst#L130-L141
[vs]: https://github.com/YosysHQ/yosys/blob/0ad867ab51111cd5b083b28f934c9a764cca33f3/frontends/verific/verificsva.cc
[vs-repeat]: https://github.com/YosysHQ/yosys/blob/0ad867ab51111cd5b083b28f934c9a764cca33f3/frontends/verific/verificsva.cc#L1157-L1240
[vs-first]: https://github.com/YosysHQ/yosys/blob/0ad867ab51111cd5b083b28f934c9a764cca33f3/frontends/verific/verificsva.cc#L1262-L1277
[vs-next]: https://github.com/YosysHQ/yosys/blob/0ad867ab51111cd5b083b28f934c9a764cca33f3/frontends/verific/verificsva.cc#L1279-L1297
[vs-and]: https://github.com/YosysHQ/yosys/blob/0ad867ab51111cd5b083b28f934c9a764cca33f3/frontends/verific/verificsva.cc#L1421-L1460
[vs-match]: https://github.com/YosysHQ/yosys/blob/0ad867ab51111cd5b083b28f934c9a764cca33f3/frontends/verific/verificsva.cc#L1044-L1055
[vs-property]: https://github.com/YosysHQ/yosys/blob/0ad867ab51111cd5b083b28f934c9a764cca33f3/frontends/verific/verificsva.cc#L1596-L1718
[vs-until]: https://github.com/YosysHQ/yosys/blob/0ad867ab51111cd5b083b28f934c9a764cca33f3/frontends/verific/verificsva.cc#L1644-L1674
[vs-eventually]: https://github.com/YosysHQ/yosys/blob/0ad867ab51111cd5b083b28f934c9a764cca33f3/frontends/verific/verificsva.cc#L1541-L1593
[vs-live]: https://github.com/YosysHQ/yosys/blob/0ad867ab51111cd5b083b28f934c9a764cca33f3/frontends/verific/verificsva.cc#L1773-L1807
[vi]: https://github.com/YosysHQ/yosys/blob/0ad867ab51111cd5b083b28f934c9a764cca33f3/frontends/verific/verific.cc#L2083-L2088
[vi-import]: https://github.com/YosysHQ/yosys/blob/0ad867ab51111cd5b083b28f934c9a764cca33f3/frontends/verific/verific.cc#L2353-L2357
