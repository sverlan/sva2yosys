# Validation

What an accepted assertion guarantees, the evidence behind that guarantee, and the points where it rests on our reading of IEEE 1800. Clause numbers refer to IEEE 1800-2017; the rules themselves are in [`semantics.md`](semantics.md), the accepted subset in [`supported-sva.md`](supported-sva.md).

## 1. What "accepted" guarantees

sva2yosys gives each concurrent assertion one of three outcomes:

- **rejected**, with a located message;
- **skipped**, for an X-check: after `$isunknown` is evaluated in the two-state model, nothing is left that could be observed ([`semantics.md`](semantics.md#8a-isunknown-and-x-checks) §8a);
- **accepted**: one or more monitors are generated (a top-level property `and` of an `assert` or `assume` gives one monitor per conjunct).

For an accepted `assert` or `assume`, the monitors' `fail` outputs are 1 at exactly the samples where IEEE 1800 makes an attempt of the property fail, at the earliest sample at which the failure is certain ([`semantics.md`](semantics.md#3-earliest-failure) §3), up to and including the first failure. For an accepted `cover`, the `success` output is 1 exactly where an attempt succeeds non-vacuously. An `assert` with an unbounded strong part (`s_eventually`, `strong(...)` of an unbounded sequence, `s_until`, `not` of a weak unbounded sequence) also has a liveness part, checked by a separate SBY `live` task ([`usage.md`](usage.md), *Strong properties*); it holds only when both tasks pass. Bounded strong properties need no `live` task (they have a finite deadline), and a `cover` of a strong unbounded sequence is reachability.

This holds within the model of §2; §5 lists the readings of the standard it rests on and the operands rejected to keep it exact (D1). Rejecting a property is always allowed; silently checking a different property is the failure the whole validation is designed to exclude.

Not guaranteed by sva2yosys: that a proof finishes (BMC depth, solver time and memory), and the correctness of the tools it relies on (§4).

## 2. The model

These are modelling choices, stated as assumptions. Each is documented in [`semantics.md`](semantics.md) or [`usage.md`](usage.md).

- **Two-state values** (§1). No signal is X or Z, and `$isunknown` of a non-constant operand is 0; a call that Slang evaluates as a constant keeps its IEEE value (`$isunknown(4'b1x00)` is 1). A directive left with nothing to check is skipped and reported (§8a). Operators that could produce X from known values (division by a possibly zero divisor) are rejected.
- **One clock, `@(posedge clk)`, under SBY `multiclock off`** (§1, §4). Every formal step is a sampling event, and every clock of the design ticks at every step: the design is modelled as single-clock, so clock-domain crossings are not checked.
- **A synchronous environment.** Inputs change only at formal steps. The exactness of `disable iff` conditions on top-level inputs (§7) and of procedural enabling conditions on inputs (§6) relies on it.
- **A formal `cover` is reachability** (§6). It asks whether some trace reaches a success; how many attempts succeed at one sample is not represented.
- **The end of a BMC run is not the end of a trace** (§9.1). An attempt still pending at the last step means the depth was too small, never a failure.
- **Liveness is a separate task** (§9.6): safety in the `prove` task, liveness in the `live` task, because SBY's `live` mode turns safety assertions into assumptions.
- **A strong `assume` is a safety constraint plus a fairness constraint**, accepted only in the class where it is exact (at most one configuration). Its safety part (`assume (!fail)`) applies in every task; only the `live` task sees its fairness part; `prove` is conservative, and a reached `cover` in a run with such an assumption is provisional and warned about (§9.6).

## 3. The evidence

The evidence is arranged in ten **layers**. Each layer checks the generated monitors, or something they depend on, against an *oracle*: a source of expected answers that is as independent of the code as we could make it. The layers fall into four groups:

- **Layers 1 and 2: what the right answer is.** An evaluator written directly from the standard (1) and verdicts derived by hand sample by sample (2) must agree on every trace case. They are the oracles for the layers that follow.
- **Layers 3 to 5: the compiler's output against that answer.** The compiled temporal machines (3), the emitted SystemVerilog read by Yosys and run by SBY (4), and the optimised monitor forms against the direct monitor (5).
- **Layer 6: the tests themselves.** Deliberately wrong versions of the product must be caught, and every counterexample found becomes a permanent case.
- **Layers 7 to 10: checks from outside.** Third-party test suites with known answers (7 and 9), a cross-check of our exporter against the Slang executable (8), and the trace tables replayed in another model checker (10).

The table gives, for each layer, what it checks, its oracle, how independent that oracle is, and its size. Layers 6, 9 and 10 have a subsection after the table.

| Layer | What it checks | Oracle | Independence | Size |
|---|---|---|---|---|
| 1. Reference evaluator | The verdict of every trace case: failures, successes, vacuity, decision samples, end-of-simulation and infinite-trace outcomes | An implementation of the rules of [`semantics.md`](semantics.md), following Annex F and clause 16 (`tests/support/reference`), with real X values at the first samples | Its own matching algorithm; it shares the frontend (Slang export and conversion to the IR) and the IR with the compiler, and a boundary test forbids it from importing the lowering and the machines | 680 tests (`reference_semantics`) |
| 2. Hand-derived trace tables | The same verdicts, written by hand sample by sample, on finite traces and on lasso traces (`prefix · loop^ω`); each case states its IEEE 1800 clauses and its class (normative, interpretation I<n> of §5, or implementation-defined) | The tables, derived by hand from the standard, and required to agree with layer 1 | Derived from the standard, not from the code's output; the authors share the reading of the standard used in the code (§6) | 661 cases in 39 files |
| 3. Temporal machines | The compiled machines against layers 1 and 2: every accepted table case, generated properties (Hypothesis), and exhaustively all short traces | Layers 1 and 2 | A different algorithm (automata, configuration sets) from the evaluator (direct matching) | 2416 tests (`temporal_machine`) |
| 4. Generated monitors under SBY | The emitted SystemVerilog, read by Yosys and run by SBY on every accepted table case, including lassos for liveness | Layers 1 and 2 | Checks the actual output in the actual tool flow | 1395 tests (`generated_monitors`), 68 (`yosys_contract`) |
| 5. Equivalence proofs | Each optimised monitor form equals the direct monitor (`abc pdr`) within its stated scope, for the properties of the contract and 25 generated ones | The direct monitor, itself compiler output | Internal consistency only: it does not check the direct monitor against the standard | 45 tests (`optimized_monitors`); users can run it on their own properties (`--equivalence`) |
| 6. Mutants and counterexamples | Deliberately wrong variants are caught; every counterexample found in development becomes a permanent case, its expected result derived again by hand | Layers 1–4 | Tests the tests | 65 mutants; see [the mutation campaign](#layer-6-the-mutation-campaign) |
| 7. External corpus | The Yosys project's own [SVA tests](https://github.com/YosysHQ/yosys/tree/ba8e952/tests/sva) | Upstream pass/fail expectations | Written by a third party | 16 files, 32 variants (as written and with `FAIL` defined): 24 with the upstream verdict under SBY (11 pass, 13 fail), 1 deviation noted in the table and not caused by sva2yosys (`extnets`, which has no concurrent SVA), 1 rejected, 6 not applicable |
| 8. Exporter cross-check | Our JSON export of the concurrent assertions is a subset of the `--ast-json` output of the `slang` executable, on the contract's fixtures | Native Slang JSON | Checks our exporter only: the executable is the same Slang version as pyslang, and the custom `sva2yosys_` fields are not compared | `slang_frontend` contract, point 15 |
| 9. External known-answer corpus: EBMC | The expected verdicts of the [EBMC regression suite](https://github.com/diffblue/hw-cbmc/tree/2a3263c/regression/verilog/SVA) (`regression/verilog/SVA`, tag `ebmc-6.1`) compared with the verdicts of our monitors under SBY; EBMC itself is not run | The `.desc` files' expected answers (verdicts only, never failing steps), classified by hand per property | Written by a third party with its own tool and its own semantics | 294 cases, 749 property lines; see [EBMC known answers](#layer-9-ebmc-known-answers) |
| 10. Differential: EBMC and Spot on the trace tables | The hand-derived trace tables of layer 2 replayed as EBMC designs: whether the property fails within the written samples, or, on a lasso, whether each attempt of the infinite trace fails; trace validity only, never failure times | An independent implementation (EBMC 6.1, Spot 2.16.1) reading the same IEEE text, on an allow-list of calibrated constructs, each with a positive and a negative control | A different tool and code base, but its semantics are not ground truth: the areas where it differs from the standard are excluded by name | 661 trace cases, 236 comparisons; see [the differential tier](#layer-10-the-ebmc-and-spot-differential) |

The trace tables of layer 2 include 148 cases from clauses 16.5 to 16.18, re-expressed and cited by clause and figure, each tagged with its evidence kind: 18 reproduce a figure's sampled inputs and outcome, 12 follow a sentence of the standard that states the outcome, and 118 are variants or traces built from a prose rule, with hand-derived rows.

**How the evidence is organised.** Each layer has a written contract (`tests/functional_tests/<area>/CONTRACT.md`) that its tests enforce. The figures on this page are those of version 0.1.0. [`test-matrix.md`](test-matrix.md) is a **generated report** of the collected tests of each area, per tier and per external tool or corpus (which tests need which tool or corpus); it is regenerated when tests change (a test fails when it is stale), so it is the authoritative count. At the time of writing it lists 10377 tests: 7865 in the default tier, 403 extended and 2109 slow. A full run (`--slow`, every tool and corpus selected) executes all of them.

[Targeted interpretation observations](interpretation-probes.md) add 14 native
Verilator probes for I1–I11 and D1 (Verilator 5.053): thirteen execute and one reports unsupported syntax.
Their controls, exact events and tool/input pins are reproducible; differences and
model limits are recorded rather than treated as agreement. The EBMC column
reuses four calibrated finite-validity cases from layer 10, without extending its
allow-list or comparing failure times.

The hand-derived trace tables of layer 2 are also published as the [SVA IEEE corpus](../sva-ieee-corpus/README.md): each of the 661 cases as native SVA with a driver, in a simulation form and a formal form, with the expected result taken from the table and never from a tool. It is generated and committed; the default test run fails on a stale corpus, every file elaborates under Slang (extended tier), and a sample is lowered by sva2yosys and run under SBY ([how](../sva-ieee-corpus/README.md#checking-the-corpus-with-yosys-and-sby)). It records no verdict of any third-party tool as an expectation.

For every construct of the supported-construct matrix, [`traceability.md`](traceability.md) (generated) lists the trace cases of layers 1-4 that exercise it, the IEEE clauses they cite, their classes and the layers that run them.

Supporting checks: 3534 unit tests of single modules (collected, all tiers); 352 numbered contract points in 19 contracts; an output dump compared byte for byte across refactorings; and an acceptance survey of five public corpora with a committed baseline: 2132 directives (1072 accepted, 720 rejected with a located message, 340 skipped as X-checks), plus 83 jobs that failed before reporting any directive. AssertionBench, a sixth corpus compared in aggregate, has 149157 accepted directives and 76 jobs with a tool error.

Coverage of the last full run before the release of version 0.1.0 (`--slow --cov`, every tool and corpus selected): `src/sva2yosys` 95.37% combined (lines 96.97%, branches 91.92%) and the helpers and oracles in `tests/support` 88.12% combined (lines 89.91%, branches 83.79%).

### Layer 6: the mutation campaign

`tests/support/mutants.py` holds 65 deliberate, plausible bugs in the product, in 12 categories: delays and repetitions, goto and nonconsecutive repetition, sequence composition, implication and negation, strength and liveness, `disable iff`, first sample and sampled values, constants and X-checks, cover and nonvacuity, conversion, code generation, exporter and source rewrite. Each is a single textual replacement applied to a temporary copy of the repository and run against a narrow set of default-tier tests, without stopping at the first failure. A kill needs a test *failure* (an error, such as a broken import, makes the mutant invalid), and a failure of a frozen-text (byte-identity) test does not count as behavioural.

Result of the last full campaign: 64 killed behaviourally and 1 equivalent (a signed literal emitted unsigned: every constant is assigned to a wire declared with the IR type, so the bits and the signedness the operators see are the same); none snapshot-only, survived, invalid or timed out. Along the way the campaign found 12 rules that only the `slow` tables checked, each now covered by a hand-derived default-tier test. The score measures this selected list of bugs, not the semantic coverage of the tests. To reproduce: `uv run --env-file .env python tests/support/mutants.py --jobs 8 --report <file.json>` (about 17 minutes in all: 2 for the baseline, the rest for the mutants). Counterexamples found in development are kept in `counterexamples.trace`.

### Layer 9: EBMC known answers

The corpus is the [EBMC regression suite](https://github.com/diffblue/hw-cbmc/tree/2a3263c/regression/verilog/SVA) (`regression/verilog/SVA`, tag `ebmc-6.1`). Its expected answers are compared with the verdicts of our monitors under SBY: bounded and refuted answers with `bmc`, unbounded proofs with `prove` (`abc pdr`), liveness with the suprove flow, covers with `cover`. The result of the classification (294 cases, 749 property lines): 391 agree; 9 agree on the bound only (strong sequences that fail only in the liveness run, 16.12.2, and covers that EBMC's unbounded answer claims are never reached while ours are unreached to depth 41, an unbounded proof of unreachability not being attempted); 1 excluded (EBMC's pending `intersect`; our verdict is the hand-derived one); 254 rejected with a located message; 94 skipped (60 whole cases: EBMC rejects the input, a `KNOWNBUG` tag, an option testing EBMC, or no known answer).

What layer 9 proves and does not prove. An agreement means that two independent tools, built on different semantics code, give the same verdict on the same design; it is evidence against gross errors, not a proof, and it covers only what the suite exercises (mostly Boolean and small sequence/implication examples). EBMC's answers encode **EBMC's own semantics**, so a disagreement is never read as our error: it is derived by hand from IEEE 1800 and becomes a counterexample trace case (here none was needed) or an `excluded` line with the reason. The areas where EBMC's semantics are known to differ from the standard are excluded from the comparison by construction or by classification: `disable iff` timing (EBMC reads internal registers before the edge's update and only at the attempt's start), values before the first tick, gated `$past`, pending matches of goto ranges, `intersect` and `within`. The one excluded line (`pending_intersect1`) is covered by the trace case `intersect_of_goto_2_and_goto_3_never_matches`, which the reference evaluator, the temporal machine and the generated monitors all agree with. Liveness verdicts run one directive per SBY `live` task (SBY decides such a task by its first verdict, issue 378); a directive with several liveness parts keeps them together, so the harness cannot separate them (no corpus case has that shape). The clock adapter (an unclocked assertion gets a `default clocking`, the test side only) reproduces EBMC's reading of an unclocked assertion, which IEEE 1800-2017 16.16 does not define. Contract: `tests/functional_tests/external/ebmc_corpus/CONTRACT.md`.

### Layer 10: the EBMC and Spot differential

The result: 661 trace cases, of which 225 are compared and 436 are not (each with its reason). There are 236 comparisons in 11 allow-list shapes: `bmc` 199 (197 agree, 2 are errors of EBMC's frontend on an `if` branch that is an unparenthesized sequence), `bdd` 10 and `buechi` 27 (all agree); there is no disagreement. The `bmc` route uses `--bound`, the `bdd` route `--bdd`, and the `buechi` route `--bdd --buechi` with Spot's `ltl2tgba`.

What layer 10 shows and does not show. It reuses the hand-derived tables of layer 2, so an agreement is a second, independent confirmation of the expected verdict of a case, by a tool whose code shares nothing with the reference evaluator, the machines or the monitors; it does not test sva2yosys's output directly (layers 3 and 4 do). Only what the allow-list admits is compared: Boolean expressions, fixed delays and bounded ranges, bounded consecutive repetition, implication, `not`, `if`/`else` and `until` through the bounded engine; `always s_eventually` and `s_eventually` through plain `--bdd`; strong sequences, `s_until`, `s_eventually always` and `s_eventually` (each attempt alone) through the Buechi route. What EBMC reads differently from the standard (the EBMC study) is excluded by name, not compared: `disable iff`, `$past` and the edge functions, `$isunknown` and X/Z, `inside`, goto and nonconsecutive repetition with ranges, `intersect`, `within` and `throughout` over pending matches, unbounded strength under `not`, `initial` procedures, fairness assumptions and covers of implications (vacuity). The Buechi route is never used for recurrence (`always s_eventually`): EBMC checks Buechi acceptance as the CTL formula `AF AG` and refutes true recurrence properties. The comparison is of trace validity, never of failure times: for a finite case the bound is the number of written samples minus one, so EBMC sees exactly the written samples and not the held suffix. Every allow-list shape has a positive and a negative control that agree, and a flipped control must disagree, so a tool that agrees with everything fails. A disagreement is never read as our error: it is derived by hand from IEEE 1800 and becomes a counterexample case or an exclusion with the reason. The table records the tool versions and a digest of the trace files, so a changed trace file forces a new classification. Contract: `tests/functional_tests/external/ebmc_differential/CONTRACT.md`.

## 4. Trusted, not checked here

- **Slang (pyslang)**: parsing, elaboration, constant evaluation and type information. Layer 8 checks only our export of its results.
- **Yosys** reading plain SystemVerilog (`read_verilog -formal -sv`) and **SBY** with its solvers. The Yosys contract (`yosys_contract`) pins the behaviours the monitors rely on, but does not verify the tools.
- **The standard's text.** Where it needs interpretation, §5 says so.

## 5. Interpretations and known deviations

Points where a verdict rests on our reading of IEEE 1800, rather than on text that admits only one reading. Each is pinned by tests; a change of reading is a semantic change, made only deliberately.

[Targeted interpretation probes](interpretation-probes.md) record native Verilator
observations for every row below and a calibrated EBMC finite-validity column.
They distinguish compatible results, differences, unsupported syntax and model
limits; none changes the accepted-language boundary.

| # | Topic | Clauses | Our reading | Alternative | Pinned by |
|---|---|---|---|---|---|
| I1 | Constants and unknown future samples | Annex F | A failure is a prefix that violates the property even when every later sample is Annex F's ⊤ letter, which satisfies every Boolean, `1'b0` included. So a condition is refuted only at its own sample: `a \|-> ##1 1'b0` fails one sample after `a`, `a \|-> ##[1:$] 1'b0` never fails, and in `b ##0 !b` the fused sample fails when it arrives, not before ([`semantics.md`](semantics.md#3-earliest-failure) §3) | Decide constants and contradictions ahead of their sample, which fails earlier | `constants.trace` |
| I2 | `disable iff` timing | 16.12, 16.15 | The condition is evaluated in the Observed region, after the clock edge's updates, so a condition that can change at the edge (e.g. a register) is rejected rather than checked with the wrong value (§7) | Sample it like other values | `slang_frontend` contract, point 23 (the abort itself: `disable.trace`) |
| I3 | `$past` at the first samples | 16.9.3, 11.4 | `$past` has no history there; a condition holds only if its value is known and non-zero. Conditions where an unknown value could become a known true value are rejected, so "does not hold" is exact for every accepted condition; operands whose default sampled value is not all X are rejected (D1) (§8) | — | `sampled.trace`; `tests/unit_tests/test_first_sample.py` |
| I4 | Sampled-value history and `disable iff` | 16.9.3, 16.15 | `disable iff` aborts attempts but not the history of `$past` and the edge functions (§7) | Reset the history on disable | `interactions.trace`, `disable.trace` |
| I5 | Nonvacuity | 16.14.8 | Per operator as in §9.2. It affects covers only; the `until` rule (nonvacuous from the start sample) affects no accepted directive yet, since covers of the `until` family are rejected | Rules depending on which operand decides | `attempts.trace`, `attempts_cover.trace`, `until.trace` |
| I6 | One cover per attempt | 16.14.3 | An attempt is covered once, at its first match | Count every match | `interactions.trace`, `attempts_cover.trace` |
| I7 | `not` and the default strength | 16.12.2, 16.12.3 | A sequence is weak by default in `assert`; `not` reverses strength, so `not s` of an unbounded `s` is strong and gets a liveness check (§9.6) | `not` keeps it weak | `not_liveness.trace`; `temporal_machine` contract, points 61–64 |
| I8 | End of a finite simulation | 16.12.2, Annex F | A pending sequence is true if weak and false if strong; property operators combine their finalized operands (`not` swaps, an `s_eventually` with a pending weak operand is true). Applied only by the reference evaluator; generated monitors have online verdicts only (§9.1) | — | `reference_semantics` contract, point 5 (`final-fail:`); `liveness.trace` |
| I9 | Procedural assertions | 16.14.6 | Lowered with their enabling path condition as `en \|-> p`; accepted only when every signal of the condition is stable at the clock edge, so sampled and current values coincide (§6) | Evaluate current values in general | `slang_frontend` contract, point 10; `slang_convert` contract, point 4 |
| I10 | **Open:** an unknown condition of property `if`/`else` | 16.12.6 | The reference evaluator lets an X selector (from `$past` at the first samples) select `else`, as an unknown condition is not true | Neither branch: a vacuous attempt | No accepted property depends on it: an `if`/`else` whose condition uses `$past` is rejected (§9.5) |
| I11 | Sizing of the comparisons of `inside` | 11.4.13, 11.6.1, 11.8.2 | Not decided: only lists whose left operand, items and range bounds have one width and one signedness are accepted, where every reading gives the same comparisons; the others are rejected (§1) | Each comparison sized on its own (as an equality or relational operator), or all operands in one common context (as the items of a `case`, §12.5). Tools differ on lists of mixed operands: Slang (pyslang 12) uses one common context; Verilator 5.047 uses a common width but keeps each item's signedness; Yosys 0.63's native frontend and Icarus Verilog do not accept `inside` | `slang_convert` contract, point 16; `inside.trace` |
| **D1** | **The default sampled value (a former deviation, now a rejection)** | 16.5.1, 16.9.3 | Before the first clock tick, `$past` and the edge functions use the *default sampled value* of their operand. For a static variable it is its declaration initializer, else its type's default (Table 6-7: 0 for `bit`, `int`; X for `logic`, `reg`, `integer`); for a 4-state net it is X; for an expression it is computed from the defaults of its operands, constants included. sva2yosys takes the whole operand as X at the first samples, which is exact only when that default is all X. Every other operand is rejected with a located message: 2-state or initialised signals (with `bit a`, IEEE makes `$stable(a)` hold at sample 0), constant parts that keep the whole operand from defaulting to all X (`$past({1'b1, a})` defaults to `2'b1x`, which is true; `$past(a + 1)` stays all X and is accepted), and, inside the operand, casts to a 2-state type, bit counts and `$isunknown`, which would otherwise hide a known default | IEEE's rule, handled exactly | Rejected, not handled exactly; found by a review against the standard, and no accepted corpus directive was affected (§6) |

Conservative rejections (e.g. the cover rule, covers of implications with unbounded antecedents, empty operands of `and`/`intersect`/`within`) are not interpretations: they are listed with their messages in [`supported-sva.md`](supported-sva.md).

## 6. Known limits of the evidence

- **Common-mode risk.** Layers 1–4 share one reading of the standard and the same frontend (Slang export and conversion), and layer 5 shares the direct monitor; an error in any of these would be consistent across the layers. D1 was found exactly this way, by a review against the standard's text and not by a test. The interpretations are therefore listed (§5). Of the 148 trace cases from clauses 16.5 to 16.18, only the 30 that reproduce a figure or follow a sentence that states the outcome carry verdicts that the standard itself shows or states; the other 118 are derived by hand from a prose rule, and a reading shared with the code's authors can be common to both. Targeted native Verilator observations and a calibrated EBMC column are in [`interpretation-probes.md`](interpretation-probes.md); unsupported syntax and two-state or finite model limits leave several questions untested. The mutation score checks sensitivity to deliberate implementation faults only. Yosys's native SVA support offers little to compare with: it accepts no property with an explicit clock event and, in a clocked `always` block, only Boolean conditions and unbounded `s_eventually` ([`tool-comparison.md`](tool-comparison.md#native-syntax-depends-on-context)), so bounded temporal operators cannot be compared with it.
- **Bounded searches.** Generated and exhaustive comparisons cover short traces and small properties; larger cases rest on the hand-derived tables. The equivalence proofs cover the optimised forms of selected properties, against the direct monitor only.
- **Expression depth.** The generated expressions are bounded groups of at most eight operands (structural depth at most 32), because a deep expression overflowed the Yosys parser stack. A dump audit of 64,407 generated assignment expressions found a conservative local depth of at most 12. An accepted `a[*1:1000] |-> b` passes loading, hierarchy checking, `proc`, `prep`, `async2sync` and `opt` at antecedent ranges 1000 and 4096, and SBY with ABC PDR (checked with Yosys 0.63). At range 4096 the SMTBMC/Yices path completes Yosys preparation, but the external `smtio.py` parser hits Python's recursion limit, so no claim is made that every engine handles arbitrarily wide state vectors.
- **Resource limits are controls, not capacity claims.** The defaults of the `--max-*` options were set from measurements: delay, repetition, delay-window and antecedent-repetition families at N=4096 are admitted (the window uses 8191 temporal bits, the largest measured work charge was about 67 million visits and the largest emission allowance 110609). Admission differs from proof feasibility: with free overlapping requests, exact-delay holding properties passed ABC PDR at N=16 and 256, but at N=4096 they and their bounded mutant and cover tasks hit the 120-second measurement limit. These are timings on one machine; no solver peak-memory measurement was made.
- **The external corpus is small** (16 files) and mostly exercises the subset that Yosys itself supports.

## 7. Reproducing the evidence

### Test runs

A test run uses only local resources unless it names external tools and corpora. Every test has three independent requirements: a cost tier (default, `--extended`, `--slow`, by its markers), the external tools it needs and the external corpora it needs (by the fixtures it requests). [`test-matrix.md`](test-matrix.md) is the generated table of these per test area.

| Run | Command |
|---|---|
| Local, quick | `uv run pytest` (or a file or directory) |
| Validation | `uv run --env-file .env pytest --external-tools=all --external-corpora=all` |
| More tests | the validation command with `--extended` (generated searches, broader tool matrices) or `--slow` (every test, long) |
| Coverage | the validation command with `--slow --cov --cov-report=term-missing` |

```sh
uv sync
cp .env.example .env                       # then set the tool and corpus paths
```

- **Switches select, variables locate.** `--external-tools` takes `yosys`, `sby`, `sby-live` (an `sby` with the suprove engine, for liveness), `slang` (the `slang` executable, same version as pyslang), `ebmc` (EBMC, for the differential tier), `spot` (the directory of Spot's `ltl2tgba`, which EBMC's Buechi route finds on its PATH), `verilator` (Verilator, for the interpretation probes; its four variables are described in [`.env.example`](../.env.example)), or `all`/`none` (the default); `--external-corpora` takes `yosys-sva`, `ebmc`, `sby-examples`, or `all`/`none`. Their locations are environment variables (`SVA2YOSYS_YOSYS`, `SVA2YOSYS_SBY`, `SVA2YOSYS_SBY_LIVE`, `SVA2YOSYS_SLANG`, `SVA2YOSYS_EBMC`, `SVA2YOSYS_LTL2TGBA`, `SVA2YOSYS_VERILATOR`, `SVA2YOSYS_VERILATOR_SHELL`, `SVA2YOSYS_VERILATOR_ROOT`, `SVA2YOSYS_VERILATOR_BUILD_PATH`, `SVA2YOSYS_YOSYS_SVA_TESTS`, `SVA2YOSYS_EBMC_TESTS`, `SVA2YOSYS_SBY_EXAMPLES`), never looked up on `PATH`. A selected resource whose variable is unset or invalid stops the run with an error, so a validation run never silently loses its tool tests.
- **Every run says what it left out:** the end of the run counts the tests skipped per tier, per unselected tool and per unselected corpus.

### Regression tools

These are scripts, not tests; their contracts are in their module docstrings.

- **Corpus fetcher** (`uv run python tests/support/corpora.py <dest> [--latest] [--update] [name ...]`): clones each corpus below at its pinned commit (partial, sparse where useful, detached). An existing clone is kept unless `--update` is given; a clone with local changes is never updated. The corpora are never committed.
- **Acceptance survey** (`uv run python tests/support/survey.py <corpora-root> <corpus ...> --baseline`; run in two parts, `axi sv-tests cc ot wb2axip` in about 9 minutes and `ab` (AssertionBench) in about 10; a run naming corpora compares only those; `--jobs <n>` runs the jobs of a corpus in parallel without changing the results): runs sva2yosys over the survey corpora and records, per directive, whether it is accepted, rejected (with the message's category) or skipped as an X-check, and its state size; per corpus, monitor sizes and time. Two committed baselines turn it into a regression check: `tests/support/survey_baseline.json` (counts, the most frequent rejection categories, sizes) and `tests/support/survey_directives.tsv` (one line per directive), so `--baseline` names every directive whose result changed. An intended change is committed with `--update-baseline` and reviewed. AssertionBench (149k directives) is compared in aggregate only, because its per-directive file would grow from 0.3 MB to 17 MB. The survey shows that sva2yosys copes with real designs (no crash, located rejections, sizes); it does not check that an accepted assertion's verdict is right: only corpora with known answers do (layers 7, 9 and 10).
- **Output dump** (`uv run python tests/support/dump.py <outdir>`, then `--compare <dirA> <dirB>`): every generated output of the checked-in fixtures; identical before and after a change that claims no behaviour change.
- **Mutation campaign** (`uv run --env-file .env python tests/support/mutants.py [--jobs <n>] [--report <file.json>] [mutant-id ...]`, about 17 minutes with 8 jobs): applies each mutant of a fixed list (a deliberate product bug: one textual replacement, with the IEEE clause it violates) to a copy of the source tree and runs the tests that should catch it. A mutant is *killed* only by a failing behavioural test; one caught only by byte-identity (snapshot) tests is reported apart, any test *error* makes it invalid (never a kill), and a mutant documented as equivalent must survive. The campaign selects all external tools and corpora unless told otherwise (`--external-tools`, `--external-corpora`); one that is selected but unavailable is a setup failure, not a verdict. Its result is section 3, layer 6.
- **Test matrix** (`uv run python tests/support/suite_matrix.py --write`): regenerates [`test-matrix.md`](test-matrix.md).

### Corpora

| Corpus | Source | Pinned commit | Licence | Use |
|---|---|---|---|---|
| Yosys SVA tests | [`YosysHQ/yosys`](https://github.com/YosysHQ/yosys), [`tests/sva`](https://github.com/YosysHQ/yosys/tree/ba8e952/tests/sva) | `ba8e952` | ISC | known answers: `tests/functional_tests/external/yosys_corpus` (`--external-corpora=yosys-sva`) |
| EBMC regression suite | [`diffblue/hw-cbmc`](https://github.com/diffblue/hw-cbmc), [`regression/verilog/SVA`](https://github.com/diffblue/hw-cbmc/tree/2a3263c/regression/verilog/SVA) | `2a3263c` (tag `ebmc-6.1`) | BSD-3-Clause | known answers: `tests/functional_tests/external/ebmc_corpus` (`--external-corpora=ebmc`) |
| SymbiYosys examples and tests | [`YosysHQ/sby`](https://github.com/YosysHQ/sby), [`docs/examples`](https://github.com/YosysHQ/sby/tree/79fc9ed/docs/examples) and [`tests`](https://github.com/YosysHQ/sby/tree/79fc9ed/tests) | `79fc9ed` | ISC | known answers: `tests/functional_tests/external/sby_examples` (`--external-corpora=sby-examples`) |
| SVA-AXI4-FVIP | [`YosysHQ-GmbH/SVA-AXI4-FVIP`](https://github.com/YosysHQ-GmbH/SVA-AXI4-FVIP) | `250f1ff` | ISC | survey |
| sv-tests | [`chipsalliance/sv-tests`](https://github.com/chipsalliance/sv-tests) | `f3f572c` | ISC; tests carry their own headers | survey |
| common_cells | [`pulp-platform/common_cells`](https://github.com/pulp-platform/common_cells) | `e73baae` | Solderpad Hardware License 0.51 | survey |
| wb2axip | [`ZipCPU/wb2axip`](https://github.com/ZipCPU/wb2axip) | `2e8d3bc` | Apache 2.0 (README; file headers vary) | survey |
| OpenTitan | [`lowRISC/opentitan`](https://github.com/lowRISC/opentitan), ten `hw/ip/*/rtl` folders | `cfcefae` | Apache 2.0 | survey |
| AssertionBench | [`achieve-lab/assertion_data_for_LLM`](https://github.com/achieve-lab/assertion_data_for_LLM) ([paper](https://arxiv.org/abs/2406.18627): Pulavarthi, Nandal, Dan, Pal, *AssertionBench: A Benchmark to Evaluate Large-Language Models for Assertion Generation*, NAACL 2025 Findings) | `07840fb` | no repository licence; the paper is CC BY-SA 4.0 (arXiv) and CC BY 4.0 (NAACL Findings); the designs come from OpenCores under their own licences | acceptance survey only (recipe `ab`: one job per design folder with a `bindings.sva`); fetched for the survey and never redistributed; the verdicts of its commercial formal tool are never used |

Considered and not used:

- **sv-tests as a source of verdicts.** The suite is not comparable for verdicts: in chapter 16, 3 `should_fail` files whose answer is a testbench simulation outcome, 26 UVM simulation tests, and the rest parse or elaborate checks. It stays a survey corpus (acceptance only).
- **HWMCC** ([Hardware Model Checking Competition](https://hwmcc.github.io/)). Compiled AIGER and BTOR2 models, with no SVA source.

Each table of expected results records the corpus commit, a digest of the files it classifies and the tool versions it was classified with, and refuses another revision: the digest is checked once, and every test of the area that uses the corpus then fails with one message instead of comparing against a different corpus (`tests/functional_tests/external/CONTRACT.md`).

## 8. Development method

The development is AI-assisted, and the work is shared between the human author and the AI. The human author plans and validates each step and rereads the code and the semantic decisions; the AI implements the code and writes the tests. Independent AI reviews of the plan and of the code are used as an additional check; they do not replace the human steps.

### What protects against errors

Most of the code is written by an AI, so correctness cannot rest on the author of the code. It rests on how the work is organized. Passing tests is not a proof, because tests and code can share a mistaken reading of the standard, but the following measures are built into the process and can be checked in the repository:

- **A property is not silently weakened.** An unsupported construct is rejected with a located diagnostic, and an unbounded `$` is never replaced by a finite limit. Rejecting a property is acceptable; proving a different property than the one written is not.
- **Contract before code.** Each component is developed in the order interface, contract, tests, implementation. The intended behavior and its limits are written down before the code exists, so the tests are not fitted to the code.
- **Expected results come from the standard.** The test cases give the inputs and the expected outcome at every sample (timing, reset, failure, non-vacuous cover). The expected outcomes are derived by hand from [IEEE 1800](https://standards.ieee.org/ieee/1800/6700/) and never copied from the output of sva2yosys or of another tool.
- **Two separate implementations of the semantics.** The same cases are checked against a reference evaluator written independently of the compiler, and against the generated monitors.
- **The generated code is checked by the real flow.** The tests run the output through Yosys and SBY and check what it does (proof and cover results), not the text it contains.
- **Other people's tests and adversarial checks.** The output is compared with public corpora (layers 7 and 9 of §3 and the corpora of §7), with EBMC in a differential test (layer 10), and with generated combinations of properties and traces. A mutation campaign plants deliberate bugs in the compiler and requires the test suite to detect each of them (layer 6).
- **Every counterexample becomes a test.** A bug found by a review, a probe or a mutant is added as a test with a hand-derived result, in the same change as its fix.
- **Narrow dependencies.** Only the `slang` package uses the Slang API; the rest of the compiler works on typed internal structures. The only runtime dependency is `pyslang`.
- **The limits are documented.** This page states what an accepted assertion guarantees (§1), the evidence for it (§3), and the readings of the standard adopted where tools diverge (§5).
