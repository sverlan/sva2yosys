# Yosys/SBY target contract (opt-in)

These tests pin down the behaviour of the **free** Yosys formal flow that generated monitors rely on. They use hand-written fixtures only; no sva2yosys code generation is involved. A failure here means the toolchain no longer behaves as the code generator assumes.

They run only when the tool variables are set; otherwise they are skipped:
- `SVA2YOSYS_SBY`: an `sby` from the OSS CAD Suite, with yosys, the yices solver and the yosys-slang plugin next to it.
- `SVA2YOSYS_YOSYS`: a `yosys` from the same suite.

Frontends:
- **RV** = `read_verilog -formal -sv`;
- **RS** = `read_slang -D FORMAL` (yosys-slang plugin);
- **MX** = mixed: the monitor read by RS, the global-clock testbench read by RV, in the same Yosys session.

## Fixtures

- `monitor_window.sv`: the canonical slot monitor for `disable iff (dis) a |-> ##[1:3] b` at `@(posedge clk)`. It contains:
  - `pend[j]`: the obligation triggered `j+1` samples ago is still open;
  - a combinational `fail` checked by `always @* assert`;
  - a cover of an age-3 obligation being discharged;
  - an asynchronous clear on `dis`.
- `dut_delay.sv`: `b` copies `a` after `DELAY` cycles; `top_delay` connects the DUT and the monitor.
- `contract_rv.sby` and `contract_rs.sby`: the same tasks (`bmc_ok`, `bmc_bad`, `prove_ok`, `cover_d2`, `cover_d3`) through RV and RS.
- `liveness_canary.sv` / `.sby`: the liveness canary (points 11-14).
- `liveness_witness.sv` / `.sby`: the `$anyseq` modules and the witness with its looping test benches (points 17-19).
- `disable_monitors.sv`: the same window monitor with a synchronous clear (`mon_sync`) and an asynchronous clear (`mon_async`). Both check at `always @(posedge clk)`.
- `disable_tb.sv`: a fully determined `$global_clock` trace in which a disable pulse falls strictly between two clock edges. The trace provides:
  - posedges at global steps 2, 6, 10 and 14;
  - an obligation triggered at step 2;
  - `b` never high;
  - `dis` high only at step 4.
- `disable_between_edges.sby`: tasks `{sync_pulse, async_pulse, async_nopulse}_{rv, mx}`, run with `multiclock on`.
- `check_style.sv` / `.sby`: `a |-> b` checked by `always @*` (`comb`) or by `always @(posedge clk)` (`clkd`):
  - `sc_*` tasks use a single clock (`multiclock off`); `a && !b` holds at the sample of cycle 3.
  - `mc_*` tasks use `multiclock on`; `a && !b` holds only *between* edges.
- `bind_dut.sv`, `bind_mon.sv`: two instances of a module and a checker module with one assertion.
  - `bind_module.sv` binds the checker to every instance of the module.
  - `bind_instance.sv` binds it to the one instance `top.u3`.

## Contract

1. **Accepted syntax (RV and RS).** Both frontends accept the monitor subset:
   - registers with initial values;
   - `always @(posedge clk or posedge dis)` with an asynchronous clear;
   - `always @* assert/cover`;
   - `always @(posedge clk) assert`;
   - generate blocks.
2. **Same-step failure (RV and RS).** With `DELAY = 4`, `bmc_bad` fails at **step 3**: an obligation triggered at step 0 has its last chance at step 3. The combinational fail adds no cycle of latency.
3. **No false failure (RV and RS).** With `DELAY = 2`, `bmc_ok` passes at depth 12.
4. **Unbounded proof (RV and RS).** With `DELAY = 2`, `prove_ok` (abc pdr) passes. The slot encoding needs no helper invariants.
5. **Cover timing (RV and RS).**
   - With `DELAY = 2` the age-3 cover is unreachable, so `cover_d2` is FAIL with that cover reported unreached.
   - With `DELAY = 3` it is reached at **step 3** (`cover_d3` PASS).
6. **Asynchronous `disable iff` (RV and MX), `multiclock on`.**
   - The synchronous clear misses the between-edge pulse and fails at step 14 (`sync_pulse` FAIL).
   - The asynchronous clear aborts the obligation (`async_pulse` PASS).
   - Without the pulse, the asynchronous monitor fails at step 14 (`async_nopulse` FAIL).

   Generated monitors therefore clear their state asynchronously on the disable condition.
7. **Check style depends on the clock mode.** Neither style is right in both modes:

   | Task | Mode | Style | Result |
   |---|---|---|---|
   | `sc_comb` | `multiclock off` | `always @*` | FAIL at **step 3**: same step, correct |
   | `sc_clk` | `multiclock off` | `always @(posedge clk)` | FAIL at **step 4**: reported one step late |
   | `mc_comb` | `multiclock on` | `always @*` | FAIL at **step 4**: a spurious failure between edges |
   | `mc_clk` | `multiclock on` | `always @(posedge clk)` | PASS: checked only at sampling edges, correct |

   Generated code therefore uses `always @*` checks for `multiclock off` and `always @(posedge clk)` checks for `multiclock on`.
8. **`$global_clock` is RV-only.** RS rejects `always @($global_clock)` ("no global clocking is available"), and yosys-slang does not lower an IEEE `global clocking` block either. Generated monitors never use `$global_clock`; testbenches that need it are read by RV (MX for the code under test).
9. **`bind`.**
   - RS honours `bind` to a module (one checker per instance: 2 `$check` cells) and `bind` to a single instance (1 cell).
   - RV rejects both with a syntax error (Yosys 0.70; Yosys 0.63 silently ignored them: no error and no `$check` cell).

   Generated output therefore never relies on `bind` when the target frontend is RV.
11. **The liveness form (RV).** The only concurrent-SVA form generated monitors may emit for liveness is procedural, on a Boolean monitor signal, in `read_verilog -formal -sv`:
    - `always @(posedge clk) assert property (s_eventually x);` gives one `$check` cell with `FLAVOR "live"`;
    - `always @(posedge clk) assume property (s_eventually x);` gives one with `FLAVOR "fair"`;
    - the module-level clocked form `assert property (@(posedge clk) s_eventually x);` is a syntax error.
12. **SBY `live` mode (suprove, through `SVA2YOSYS_SBY_LIVE`).** With `mode live` and `engines aiger suprove`:
    - a true liveness property passes (`live_pass`: a free-running 2-bit counter reaches 0 again);
    - a false one fails (`live_fail`: `x` is never true);
    - an assumed `s_eventually` is a fairness constraint: `fair_without` (free input, `assert s_eventually in`) fails and `fair_with` (the same plus the assumption) passes;
    - fairness is not an invariant: with a toggling signal `t` assumed `s_eventually t` and a never-true liveness assertion, the task FAILs (`live_fair_toggle`; reading the assumption as `always t` would leave no trace and pass vacuously);
    - a design with only an assumption and no assertion is an SBY ERROR (nothing to check), so a monitor-only fairness design needs an assertion.
13. **Safety assertions in `live` mode are assumptions.** Each form is shown alone, against a control without it:
    - control: the false liveness property alone fails (`live_fail`, `live_conc_control`);
    - an immediate `always @*` assertion that is false on every infinite trace makes it pass vacuously (`live_vacuous`);
    - so does a procedural concurrent Boolean `assert property` that is false on every infinite trace, with no immediate assertion (`live_conc_vacuous`);
    - a false safety assertion (both forms) next to a true liveness property leaves the task PASS with no failed assertion (`live_mixed`).

    Safety and liveness of one directive are therefore checked in separate tasks: a combined `live` task would hide safety failures and can prove false liveness.
14. **A `prove` task ignores liveness checks.** `abc pdr` on a design with a false `s_eventually` check passes when the safety assertions hold (`prove_unguarded`) and fails on the safety assertion alone when it is false (`prove_guarded_bad`). With the liveness check unguarded, a false safety assertion is still reported, at its own location (`prove_unguarded_bad`). Wrapping the liveness check in `` `ifdef SVA2YOSYS_LIVE `` and defining it only in the `live` task (`read_verilog -formal -sv -D SVA2YOSYS_LIVE`) gives the same safety verdicts in the `prove` task, and the `live` task (`live_guarded`) then fails on the liveness check.
15. **The suprove command runs through WSL.** The work directory is passed to a WSL command as `/mnt/<drive>/...` (`path_for_tool` in `tests/support/toolchain.py`); the working directory itself is translated by WSL.
16. **No files in the source tree.** Every run writes only to its `tmp_path` work directory.
17. **`$anyseq` is a free value at every step (RV).** In `read_verilog -formal -sv`, `wire pick = $anyseq;` (1 bit, inside a module) takes an arbitrary value at every step, independently of earlier steps:
    - in a `prove` task (abc pdr), an assertion that `pick` is constantly 0 fails (`prove_anyseq_safety`);
    - in a `live` task (suprove), `s_eventually !pick` fails, because the choice sequence "always 1" violates it (`live_anyseq`).
18. **`$anyseq` under `` `ifdef SVA2YOSYS_LIVE `` leaves the `prove` task unaffected.** In `guarded_anyseq` the `$anyseq` wire and a liveness check on it exist only under the define. The `prove` task (no define) passes on the safety assertion alone (`prove_anyseq_guarded`); the `live` task with `-D SVA2YOSYS_LIVE` fails on the liveness check (`live_anyseq_guarded`).
19. **The witness pattern for liveness.** A witness register `busy_q` (initial value 0) and `pick = $anyseq` follow one request of `req`/`ack`: with `busy_q` 0 the witness starts following when `req && pick && !ack` holds (a request answered in its own sample never makes it busy); while busy it ends when `ack` holds; it never re-picks in the sample it ends. The check is `` `ifdef SVA2YOSYS_LIVE `` `always @(posedge clk) assert property (s_eventually !busy_q);`. `req` and `ack` are driven by a deterministic looping ROM test bench (counter `t`: `0 .. N-1`, then `K .. N-1` forever). In `live` tasks:
    - every request answered one sample later (`req` at t0, `ack` at t1) passes (`live_wit_answered`);
    - a request never answered after some point (`ack` stays 0 forever) fails: the witness follows it and stays busy (`live_wit_unanswered`);
    - `req` and `ack` both high in every sample passes: each request is answered in its own sample, the witness is never busy (`live_wit_same_sample`);
    - `req` in every sample and `ack` in every second sample passes: every request is answered within one sample, and the witness ends at the sample of the `ack` without re-picking (`live_wit_next_sample`);
    - the mutant that may re-pick in the sample it ends fails on the same stimulus: it stays busy forever (`live_wit_repick_mutant`). This shows that the no-re-pick rule is what makes the previous task pass.

**Scheduling.** Default retains native correct/failing safety and true/false liveness controls. The complete capability matrix runs with `--extended` (or `--slow`), with all original expected statuses and assertions unchanged.

20. **What `read_slang` does with the forms of generated monitors** (`--yosys-frontend slang`; opt-in, `extended`). These pin the limits that `docs/usage.md` states, so that a newer OSS CAD Suite that lifts one fails a test and tells us when the limitation can go:
    - the procedural `assert property (s_eventually x)` of the liveness form (point 11) is an error, "SVA unsupported" (`ignore all assertions with '--ignore-assertions'`); `read_verilog` accepts it (point 11);
    - the call `$anyseq` (points 17 to 19) is an error, "unknown system name"; the attribute form `(* anyseq *) wire w;` is accepted;
    - a register read before its declaration (a generated monitor reads its sampled-value history registers before declaring them) is an error by default and accepted with `--allow-use-before-declare`;
    - by default the design is flattened into the top module; with `--best-effort-hierarchy` the modules that contain formal properties stay (`dut$top.u2`, `mon$top.u2.u_mon`, … for the bind fixtures), so that failing checks keep their instance paths.
    The fixtures are `slang_anyseq_call.sv`, `slang_anyseq_attr.sv` and `slang_use_before.sv` (one module each: Slang compiles every module of a file, whatever the top).

21. **The `smtbmc` engine and a wide constant read with a variable index** (found by the SVA IEEE corpus; opt-in, `extended`). The fixture `wide_string.sv` reads a 520-bit string constant with a variable index (the string form of a trace of 33 samples) and asserts that the only `1` of the row, at sample 5, is never seen. Bounded model checking to depth 10 gives:
    - **on Windows** the SBY default, `smtbmc yices`, stops with `RecursionError: maximum recursion depth exceeded` (status ERROR, no failed assertion): the engine's `--unroll` expansion nests about a thousand `concat` terms. This pins the limit, so that a Yosys that lifts it fails the test and tells us. The limit is Windows-only: `yosys-smtbmc` raises Python's recursion limit to 65536 and its stack to 128 MB only on POSIX (`smtio.py`, `if os.name == "posix"`), and on Linux the same run completes and reports the failed assertion (checked up to 400 samples). The tests expect the error on Windows and the failure elsewhere;
    - `smtbmc --nounroll yices`, `smtbmc z3` (which SBY runs without `--unroll`) and `smtbmc yices` with `techmap` and `opt -fast` after `prep` all report the failed assertion at step 6 (the same for the three; the only 1 of the row is at sample 5);
    - `smtbmc boolector` and `smtbmc bitwuzla` behave like the default, `--nounroll` or not: `yosys-smtbmc` always expands for them.
    The workarounds are in `docs/usage.md` (*If SBY stops with a `RecursionError`*).
