# sva-ieee-corpus

Small, self-contained SystemVerilog Assertion cases with hand-derived expected results, for checking SVA implementations: simulators, formal tools and lowering tools.

Each case is one concurrent assertion, a short input trace and the result the IEEE 1800 semantics give on that trace. The traces and expectations are written by hand in the `.trace` files of the [sva2yosys](../README.md) repository (`tests/functional_tests/reference_semantics/fixtures/`); the files here are generated from them and must not be edited. No result of any tool is used as an expectation.

## Quick start

1. Pick a case: `formal/<category>/<trace-stem>/<case>.sv` for a formal tool or `sim/<category>/<trace-stem>/<case>.sv` for a simulator. `manifest.json` lists every case.
2. Run the file in the tool under test, bounded to the case's `samples` samples.
3. For a finite case (`loop` is null in the manifest): compare the samples at which the tool reports a failure (or a cover success) with `failures` (or `successes`). Check `scope` first: for `first-failure` only the first failure is specified.
4. For a lasso case (94 cases: `loop` is set and there is no `sim` file): a run bounded to `samples` checks only the written prefix against `failures` and `successes`. The expected result on the infinite trace is `infinite.outcomes`, which a bounded run cannot validate: use a tool that decides infinite traces.
5. A difference is a finding to examine, not automatically a bug in the tool: check the case's `class` and `clauses` first.

The sections below explain the layout, the timing conventions and the manifest.

## Layout

```text
manifest.json                                    every case: id, category, expectations, class, clauses
sim/<category>/<trace-stem>/<case>.sv            simulation form
formal/<category>/<trace-stem>/<case>.sv         formal form
traces/csv/<category>/<trace-stem>/<case>.csv    the trace and the expected rows, one line per sample
traces/wavedrom/<category>/<trace-stem>/<case>.json   the same as a WaveDrom waveform
```

A case has the same `<category>/<trace-stem>/<case>` path under each root, so its other forms are easy to find. `<trace-stem>` groups cases by construct (`until`, `goto`, `sequence_and`, ...). All 661 cases are here, in five categories (the `<category>` folder): `bounded` (490: a finite assertion checked on a finite trace), `cover` (62: a `cover property`), `assume` (19: an `assume property`), `initial` (11: a directive in an `initial` procedure, one attempt at the first tick) and `live` (79: an assertion on an infinite repeating trace). A *lasso* case, in any category (94 cases, `"loop"` is set in the manifest), has an infinite trace: the written samples, then the samples from `loop` on repeating forever. It has no simulation file, because a simulation can only run a prefix of an infinite trace; its formal file drives the trace with a phase counter that jumps back to the loop sample.

Every file of a case is complete by itself; there is no shared file, include or data file.

## One case

Both SV forms define `module sva_case` with a clock input `clk`. The input trace is inside the module as one string per signal that looks like the row of the table, decoded by plain bit selects on a sample counter:

```systemverilog
//   a : 1 . . 1 .
//   b : . . . 1 .
wire [71:0] ROW_a = "1 . . 1 .";
```

- One-bit rows: `.` is 0 and `1` is 1. Wide rows: uppercase hexadecimal digits (`ceil(width/4)` per sample), zero written `0`.
- **Sample `k`** is the value sampled at the `k`-th rising edge of `clk`, counting from 0. Values change only after the sampling edge.
- Only samples `0 .. N-1` (`N` = `samples` in the manifest) have expected results. The last values are held afterwards only to keep the design defined; bound your check to `N` samples. A pending attempt at the end is not an online failure; what finalizes false when the simulation ends is `final_fail` in the manifest.
- All values are two-state.

**Formal form** (`formal/`): the driver and the property only, `p: <assert|assume|cover> property (@(posedge clk) ...)` (`initial p: ...` for an `initial` case). Give your tool the clock and a bound of `N` samples; the tool's own configuration is yours.

**Simulation form** (`sim/`): the same module plus an output `done`, the same assertion `p` carrying action blocks that print `SVA_FAIL <case id> sample=<k>` or `SVA_PASS <case id> sample=<k>`, (a `cover` prints `SVA_COVER <case id> sample=<k>` for each match instead), and a top module `tb` with the clock. Compile the one file with `tb` as top. Pass actions also fire on vacuous success unless the simulator disables them (`$assertvacuousoff`, IEEE 1800-2017 20.12), so for an `attempt`-scope case the `SVA_PASS` lines are a superset of its non-vacuous `successes` (see the `successes` row below).

Simulators and formal tools support different subsets of SVA. A case your tool cannot build is a limitation to note, not a defect of the corpus; a case it builds and answers differently needs the check described below. Some tools also evaluate assertions before the non-blocking updates of the time slot instead of in the Observed region, as the standard defines (IEEE 1800-2017 4.4, 16.12). For such a tool the results can be incorrect for some supported cases, in particular those with a `disable iff` condition and the sample number an action block sees, even though it builds them; the expected results follow the standard.

## Timing conventions

**Two reading rules of the standard.** An assertion reads signals in two different ways:

- *Sampled values* (IEEE 1800-2017 16.5.1, 4.4.2.1). The expressions of a sequence or property are evaluated over sampled values: for a time slot after time 0, the values in the Preponed region, that is, the settled values from just before the clock event. At a rising edge of `clk` an assertion sees what the signals held *just before* the edge, not what they change to at it. (The special rule for time 0 does not come into play: the first rising edge is after time 0.)
- *Current values* (16.12, 16.5). A `disable iff` condition is **not sampled**: it is evaluated with the current values of its variables, from the start of the attempt in the Observed region, which follows the non-blocking updates of the time slot. It sees what the signals hold *just after* the edge.

**What a row means.** The tables give one value per signal and sample, and that value is meant for both readings: at rising edge `k` the property samples row `k`, and a `disable iff` condition currently holds row `k`. A driver that moves to row `k + 1` right after the edge, which is how a driver is usually written, cannot give both to the same signal: the property would see row `k`, but the condition row `k + 1`, and an attempt that should fail at sample `k` could be disabled by a reset that comes only at sample `k + 1`. In all the cases of the tables the condition and the property read *different* signals, so the generated files present each kind in its own way, all on `posedge clk`:

- a signal that the property reads shows row `k` just before rising edge `k` (the counter `tick` advances with a non-blocking assignment after each edge, so row `k + 1` appears after edge `k`);
- a signal that only the `disable iff` condition reads is selected by a saved copy of the counter, `tick_prev` (`phase_prev` for an infinite trace), which holds the counter as it was before the last edge. After the non-blocking updates of rising edge `k`, that is where the condition reads, it shows row `k`. In the file such a signal is declared from `row_idx_prev`, the others from `row_idx`, and a comment names the signals.

The generator refuses a case in which one signal is read by both, because a rising-edge driver cannot present it correctly (none of the cases is).

**Sample `k`** is that row: the set of values of the signals at the `k`-th rising edge of `clk`, counting from 0. Only samples `0 .. N-1` (`N` = `samples` in the manifest) have expected results. A `fail` or `pass` at sample `k` is the decision reported at that edge.

**The SystemVerilog files.** In the simulation form the testbench starts `clk` at 0 and toggles it every 5 time units, so the rising edges are at times 5, 15, 25, ...:

| Time | Event | `tick` | Property signals | `disable iff` signals |
|---|---|---|---|---|
| 0 to 5 | `clk` low | 0 | row 0 | row 0 |
| 5 | rising edge 0: the property samples row 0; then the non-blocking updates | 1 after the edge | row 1 after the edge | row 0 after the edge (`tick_prev` = 0), where the condition reads |
| 15 | rising edge 1: samples row 1; then the updates | 2 | row 2 | row 1 (`tick_prev` = 1) |
| ... | the same at every edge | ... | ... | ... |
| edge `N-1` | samples row `N-1`; then the updates | `N` | the last row is held | row `N-1` |
| right after | `done` rises, `$finish` follows one time unit later | | | |

The action blocks print the sample number as `$sampled(tick)`, the counter's Preponed value, which is `k` at edge `k` whatever order a simulator runs the counter update and the action block in. The formal form has the same counters and signals without a testbench: the tool supplies `clk` and each of its rising edges is one sample.

**The CSV.** Row `k` holds the values of the signals at rising edge `k`: for a signal the property reads, the value just before the edge; for a signal only the `disable iff` condition reads, the value just after it. A CSV does not say when a signal changes between two edges. To drive your own design from it, the simplest way that is right for both readings is to keep every signal stable through the whole time slot of rising edge `k` (apply row `k` before the edge, move to row `k + 1` later, for example at the falling edge). Do not simply update every signal with a non-blocking assignment at the rising edge: a `disable iff` condition would then see row `k + 1`. A lasso case has two more columns: `repeat` (1 from the loop sample on) and `outcome` (the infinite outcome of the attempt started at that sample). On row `k`, `fail` is 1 if the assertion reports a failure at that edge and `pass` is 1 for a non-vacuous success at that edge.

**The WaveDrom files.** The same data in the usual textbook drawing: signals change at a rising edge, and the cell of sample `k` ends at rising edge `k` that samples it, so the value an edge samples is the one on its left. The clock is a low cell followed by one rising edge per cell, with the rising edges on the grid lines, and the last edge, which only closes the last cell, is not drawn. The labels in the middle of the cells are the sample numbers. A signal that only the `disable iff` condition reads is drawn one cell later, because the condition reads the value right after the edge; a footer line names such signals.

**One case in the three views** (`a = 1 0 0 1`, `b = 0 1 0 0`, a non-vacuous success at sample 1 and a failure at sample 2, no `disable iff`):

```text
CSV                                  WaveDrom
sample,a,b,fail,pass                 clk  l p . .     rising edges 0, 1, 2 on the grid lines
0,1,0,0,0                            a    1 0 . 1     the cell of sample k ends at edge k
1,0,1,0,1                            b    0 1 0 .
2,0,0,1,0                            fail 0 . 1 0
3,1,0,0,0                            pass 0 1 0 .

SV         row strings  "1 . . 1" and ". 1 . ."; the property samples row k at the k-th rising edge
```

## Expected results

`manifest.json` has one line per case:

| Field | Meaning |
|---|---|
| `id` | `<trace-stem>/<case>` |
| `failures` | sample indices at which the assertion reports a failure |
| `successes` | sample indices of non-vacuous success. With `scope` `attempt` they are successes of whole evaluation attempts, which an action block reports. With `default` or `first-failure` the table may record the success of an obligation (a sub-property endpoint) that a native assertion does not report online (for example the true `b` of `always b` at each sample, or an endpoint of the consequent of an attempt that fails later), so compare `SVA_PASS` or a tool's success report with `successes` for `attempt` scope only |
| `scope` | `default`, `first-failure` (only the first failure is specified; the later ones are not) or `attempt` (the decision sample of every false attempt) |
| `class` | `normative`, `implementation-defined` or `interpretation` |
| `interpretations` | for an `interpretation` case, the rows `I<n>` of the reading it depends on |
| `clauses` | the IEEE 1800 clauses that decide the verdict |
| `support_oracle` | the sva2yosys project does not lower the property (the case is still valid SVA) |
| `loop`, `sim` | `loop` is the sample at which a lasso case repeats (null otherwise); `sim` is false exactly when there is no simulation file |
| `infinite` | lasso cases: `outcomes`, one of `T` true, `F` false, `D` disabled per start sample, for the attempt started there, on the infinite trace; `decided`, the sample at which it is decided online, or null |
| `attempts` | cases with per-attempt results: `outcomes` per start sample (`T`/`t` true non-vacuous/vacuous, `F`/`f` false non-vacuous/vacuous, `U`/`X` true/false with non-vacuity unknown at the end, `p` pending, `d` disabled), `decided`, and `pending` and `final_fail` when the table gives them (the end-of-simulation results: attempts pending at the end, and pending attempts that finalize false, which a strong operator does; they are present even when the table has no `outcome` row, with `outcomes` and `decided` then null). A directive in an `initial` procedure has one native attempt, the one at sample 0, so its `outcomes` and `decided` have one entry |

For a `cover` case `successes` are the samples at which the cover succeeds non-vacuously and `failures` is empty. The pass statement of a cover runs once for each successful evaluation attempt, vacuous ones included (IEEE 1800-2017 16.14.3), so the `SVA_COVER` lines also include vacuous successes unless the simulator disables the vacuous pass action (`$assertvacuousoff`, 20.12); a formal tool that treats a vacuous success as reaching the cover can reach it at other samples as well. For an `assume` case a violation is a failure in simulation; in a formal tool a replay that violates the assumption makes the run infeasible from that sample on, which is not a proof. For a lasso case `failures`, `successes`, `pending` and `final_fail` describe the written samples read as a finite trace; the expectation on the infinite trace is `infinite`. `infinite` gives the outcome of the attempt started at each *written* sample. The assertion also starts an attempt at every later tick of the infinite trace, so its overall verdict on that trace is not the combination of those outcomes (for example `lasso/past_depth_matches_once_from_the_prefix` has outcomes `T T`, but the attempts started from sample 3 on never see a true `$past(b,3)` again and fail in the limit): compare per start sample, or restrict the launch.

The `class` matters when you compare a tool: `normative` cases follow directly from the standard text; `interpretation` cases depend on a reading of the standard, and tools differ there (see [`docs/validation.md`](../docs/validation.md) for the readings and why); a different answer from your tool is a finding to examine, not automatically a bug.

If your tool cannot read the string rows (a string literal in a net, a part-select with a variable index), the same corpus can be built with the trace written as a chain of conditional expressions:

```text
uv run python tests/support/trace_suite.py --write --stimulus ternary --root <empty directory>
```

**String or ternary?** The two forms describe the same trace. The string form writes each signal's row as a string literal and reads it with a part-select whose index is the sample counter: the rows are visible in the file and compact. The ternary form writes the trace as a chain of conditional expressions on the counter: no string and no variable part-select, which some tools cannot read. **The committed corpus uses the string form for every case of fewer than 30 samples and the ternary form for the two longest** (`ieee_sequences/ieee_16_7_req_range_4_to_32_latest` and `..._too_early_and_too_late`, 33 and 34 samples); `manifest.json` says `"ternary-from": 30`. The reason is a limit of the Yosys flow that we measured (Yosys 0.63, `read_verilog`, SBY with `smtbmc`): a 520-bit string read with a variable index becomes a netlist whose SMT form nests about a thousand terms, and on Windows SBY's own engine (`yosys-smtbmc`) stops with a recursion error on it whatever the property (on POSIX systems it raises Python's recursion limit, and the string form runs through); the ternary form of the same cases checks correctly. For typical traces the ternary form also gives a smaller netlist (about 85% fewer wire bits and a 50 to 70% smaller SMT model at 6 to 14 samples; the number of cells is about the same). A flow that reads the string rows natively does not need it. To write the whole corpus in the ternary form, or another threshold, build it on demand: `--stimulus ternary`, or `--ternary-from N` (`never` for string rows only). To check the corpus with Yosys and SBY, see [Checking the corpus with Yosys and SBY](#checking-the-corpus-with-yosys-and-sby).

If your simulator does not support `$sampled` in assertion action blocks (the simulation form prints the sample number with `$sampled(tick)`), build the variant that copies the counter at each falling edge instead; the options combine:

```text
uv run python tests/support/trace_suite.py --write --sample-index register --root <empty directory>
```

The committed files are always the default form (string rows below 30 samples, `$sampled`); the variants are not part of the repository.

## Checking the corpus with Yosys and SBY

This is how the sva2yosys project checks the formal files in its own tests (opt-in test tiers, with SBY selected): `sva2yosys formal/<category>/<stem>/<case>.sv --top sva_case -o build` lowers the file, then SBY runs `mode bmc` to depth `samples` for an assertion or an `initial` assertion, or `mode cover` for a cover, with `script sva2yosys_read.ys` and `prep -top sva_case`. The first failing sample must equal the manifest's. The cases that sva2yosys does not take are the ones marked `support_oracle` and the ones whose `disable iff` reads a signal. An `assume` case has no assertion for SBY to check by itself. A `.sby` file for one case; the `[files]` entry stages the read script that `sva2yosys` wrote into `build/`:

```ini
[tasks]
bmc

[options]
mode bmc
depth <samples>

[engines]
smtbmc yices

[script]
script sva2yosys_read.ys
prep -top sva_case

[files]
build/sva2yosys_read.ys
```

For a lasso case this checks only the written prefix; its infinite outcome needs a tool that decides infinite traces.

**On Windows**, the two longest traces (30 samples or more) are written as conditionals in the committed corpus because their string form stops SBY's engine with a `RecursionError` under `read_verilog`. `yosys-smtbmc` raises Python's recursion limit only on POSIX systems, so on Linux and macOS the string form runs through. If you prefer string rows everywhere on Windows, build the corpus with `--ternary-from never` and put `smtbmc --nounroll yices` (or `smtbmc z3`) in the `[engines]` section of your `.sby` file: the error comes from the engine's `--unroll` expansion, which SBY turns on for every solver except `z3`; `boolector` and `bitwuzla` always expand and cannot check those two cases in the string form. Adding `techmap` and `opt -fast` after `prep` also avoids the error, and shrinks the SMT model of large or wide cases (about half for the long traces); for small cases it makes the model slightly larger and the run slower, so it is not a general optimisation. The error and its workarounds are described in [`docs/usage.md`](../docs/usage.md#if-sby-stops-with-a-recursionerror).

## Regeneration (contributors)

```text
uv run python tests/support/trace_suite.py --write    # regenerate from the .trace files
uv run python tests/support/trace_suite.py --check    # list stale, missing or extra files
```

A change to a `.trace` file or to the generator must be committed together with the regenerated corpus; the default test run fails otherwise.

## License

MIT; see [LICENSE](LICENSE). The corpus quotes no text of the IEEE standard: only clause numbers and the property texts of the cases.
