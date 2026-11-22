# Formal designs contract (end to end, opt-in)

These tests run small designs through the whole flow as a user would. They need SBY (`SVA2YOSYS_SBY`) and are skipped unless the run selects it (`--external-tools=sby` or `all`).

1. `sva2yosys PROPS -y RTL --top TOP -o BUILD` (the CLI, in process).
2. SBY reads the result with `script sva2yosys_read.ys` (`read_verilog -formal -sv`) and runs `prep -top TOP`.

## Designs

- `examples/handshake` (the shipped example, run with its own `handshake.sby`): a request/acknowledge handshake with bound properties (hold, response within 1–3 samples, ack only with req) and a cover.
- `fixtures/delays`: two instances of a parametrised delay line (`N = 1`, `N = 3`). Its bound properties check each bit's latency `in[i] |-> ##N out[i]` in a generate loop, so the rewrite selects one of four monitor variants per (instance, bit) with a `generate if`. A shared cover goes with them.
- `fixtures/pipe3`: a three-stage pipeline with reset. Its properties use supporting logic (a delay line of the expected data), a reset assertion that is not disabled by reset, and a cover over a repetition.

- `fixtures/fsm_enum`: a request/acknowledge FSM whose state is a package enum (`typedef enum logic [1:0] {IDLE, BUSY, DONE} state_t`, 4-state, no initial value; BMC starts from any state). Its properties compare the state with enum members (`state == IDLE && req |=> state == BUSY`, `state == DONE |=> state == IDLE`, `$past(state) == BUSY && state == DONE |-> $past(ack)`) and a cover `IDLE ##1 BUSY ##1 DONE`. The RTL uses scoped names (`fsm_pkg::IDLE`) because Yosys does not read a package import in a module.

- `fixtures/tied_reset`: three identical instances of a stage cleared by `effective_rst_n = rst_src_ni && rst_dst_ni` (OpenTitan `prim_sync_reqack`); `u2` ties `rst_src_ni` to `1'b1` (as OpenTitan ties `req_chk_i`). Bound properties `disable iff (!effective_rst_n) d |=> q` and the cover `d ##1 q` read the internal net in every instance. By hand: an attempt at `k` that is not disabled has `effective_rst_n` high at `k` and `k+1`, so `q` at `k+1` is `d` at `k` and the assertion holds; without the `disable iff` a reset at `k` would make it fail. Each cover is reached at step 1 (`d` and the resets high at 0).

- `fixtures/initial_counter`: a free-running five-bit counter `x` with the initial value 0 and `initial` assertions in its bound property module (IEEE 1800-2017 16.14.6): `initial a_below_ten: assert property (@(posedge clk) always [0:9] x < 10)` and `initial c_three: cover property (@(posedge clk) x == 0 ##3 x == 3)`. An `initial` assertion has one attempt, at the first tick of its clock (step 0).

## Contract

1. **Correct designs prove.**
   - Every assertion of the correct design proves with `abc pdr`.
   - Every cover is reached at the sample derived by hand from the RTL: `c_done` at step 3, `c_burst` at step 5, `c_out` at steps 1 (`N = 1`) and 3 (`N = 3`), `c_cycle` at step 2.
2. **Mutants fail at the earliest possible sample.** Each mutant is the design with one bug, found first through `-y` (it shadows the correct file). Under BMC (depth 10) the run fails, and the first reported failing step is the earliest sample at which any input sequence can violate a property, as derived by hand from the RTL:

   | Design | Mutant | Bug | First failure |
   |---|---|---|---|
   | handshake | `slow_ack` | ack four samples after req rises | 4 |
   | handshake | `drops_req` | req withdrawn before any ack | 3 |
   | pipe3 | `short` | two stages instead of three | 3 |
   | pipe3 | `reset_keeps_last` | reset does not clear the last valid bit | 3 |
   | delays | `long` | every delay line one sample too long | 1 |
   | fsm_enum | `done_to_busy` | `DONE` returns to `BUSY` instead of `IDLE` | 1 |
   | tied_reset | `late_reset` | the stage is cleared by a registered copy of the reset (no initial value) | 1 |
   | initial_counter | `starts_at_one` | the counter starts at 1 instead of 0 | 9 |

   `tied_reset`, by hand: BMC may start with the registered copy low; an attempt at step 0 with `d` and both resets high then sees `q` low at step 1. A `|=>` obligation is decided no earlier than the next sample, so the earliest failure is step 1.

   `initial_counter`, by hand: `x` is `k` at step `k` (the counter starts at 0 and increments at every tick, five bits, no wrap before step 31). The only attempt of `a_below_ten` starts at step 0 and checks `x < 10` at steps 0 to 9, which hold (`x = 0 .. 9`): the assertion proves with `abc pdr`. The same assertion at module level would also be attempted at step 1 and see `x = 10` at step 10, so it would fail: only the single attempt makes it hold. The cover `c_three` has its attempt at step 0, where `x == 0` holds, and matches at step 3 (`x == 3`): reached at step 3. In the mutant `x` is `k + 1` at step `k`, so the attempt sees `x = 10` at step 9, the first step at which `x < 10` fails; no attempt or step before it can fail (`x` is 1 .. 9 at steps 0 .. 8), so the earliest failing step is 9 (BMC depth 10 covers steps 0 to 9).

   `fsm_enum`, by hand: the state has no initial value, so BMC may start in `DONE` at step 0; `a_finish` then expects `IDLE` at step 1 but the mutant has `BUSY`. No property can fail at step 0 (every obligation is on the next tick or on a `$past` that is X at the first tick), so the earliest failure is step 1. The correct design's cover `c_cycle` is reached at step 2: `IDLE` at tick 0, `BUSY` at tick 1 (needs `req` at tick 0), `DONE` at tick 2 (needs `ack` at tick 1); three different states need two transitions.

3. **The shipped example works as documented.** The example's `handshake.sby` (both tasks) passes on the generated `build/`, and its README commands are the ones the test runs.
4. **An unforeseen parameter value fails loudly.** When the rewritten `delays` design is elaborated with `N = 2`, a value Slang never saw, BMC fails on a fallback assertion of the rewrite's `__sva2yosys_p<N>_unexpected` branches (the assertion's and the cover's, both guarded by `N`; identified by source position), rather than the assertion silently disappearing.

5. **The `slang` frontend gives the same proofs and covers** (`--yosys-frontend slang`, `docs/usage.md`). The designs of point 1, generated with `--yosys-frontend slang`, are read by SBY with the written `sva2yosys_read.ys` (`plugin -i slang`, one `read_slang` command with `--allow-use-before-declare`, the original design files, the `bind` statements kept): every assertion proves with `abc pdr`, and every cover is reached at the same step as in point 1.
6. **The `slang` frontend gives the same failures.** The mutants of point 2, generated with `--yosys-frontend slang`, fail under BMC at the same earliest sample, and the failing assertions are named by the same instance paths as with the default frontend (`--best-effort-hierarchy` keeps them), up to the two frontends' own naming of generate blocks: `read_verilog` writes the block as an escaped identifier and adds a `genblk` level for the `if` that the rewrite generates, `read_slang` does neither.
7. **The limitation is real**. The files written for the `slang` frontend, read by `read_verilog -formal -sv` instead, are rejected: SBY reports an ERROR (a syntax error at the `bind`; Yosys 0.63 dropped the bound property module and reported PASS for the correct design, for its mutant and with no cover reached). This is why the option writes its own read script and why `docs/usage.md` says the files must be read with it.

8. **Other solvers give the same answers** (`extended`). The same designs and mutants, generated once, checked with other engines than the ones of points 1 and 2:
    - every correct design proves with `smtbmc z3` (k-induction, a different algorithm from `abc pdr`) and its covers are reached at the same steps with `smtbmc z3`;
    - every mutant fails under BMC with `smtbmc z3` and with `smtbmc bitwuzla` at the same earliest sample as with `smtbmc yices`.
    The limits of the engines themselves (the `RecursionError` of the unrolling) are `yosys_contract` point 21. `aiger avy` and `abc bmc3` are not used: with these designs the first stops with an error and the second does not take the `prove` mode.

**Scheduling.** Points 5 to 7 (the `slang` frontend, through SBY) cost about 20 seconds together and run with `--extended` (or `--slow`): the `slang` flow is checked on request and before a release, not at every run. The tool-free contract points of the frontend (`cli` 23, `rewrite` 23) run by default.

**Scheduling.** One correct design and one mutant run by default, retaining proof/cover/failing-step controls. The other design parameters run with `--extended` or `--slow`, unchanged.

## Original educational examples

Canonical sources live in `examples/pulse`, `examples/counter` and `examples/fifo`.
Local checks lower both normal and `EXAMPLE_BUG` sources through check/generation
without external tools. Expected directive counts are four, four and six.
Tool checks use the shipped SBY recipes in temporary directories: correct designs
prove and reach all covers; the buggy variant has a bounded counterexample.
Do not infer these expected outcomes from captured tool results.

- **Pulse:** a sampled rising level produces exactly one registered pulse at the
  next sample; holding the level high produces no further pulses. The deliberate
  bug follows the level instead of detecting its rising edge.
- **Counter:** a two-bit count increments modulo four when enabled, holds when
  disabled and clears on reset. A cover observes a full 0,1,2,3,0 cycle. The bug
  increments by two, contradicting the one-step assertion.
- **FIFO:** a two-entry, eight-bit ring queue exposes occupancy/full/empty and
  its oldest word. Pushes at full and pops at empty are ignored. At full, push
  is ignored even if a pop happens on the same tick. A shift-based reference
  model independently records accepted words; assertions check flags, count and
  head data. Covers observe a round trip and fill/drain. The bug flips output
  bit zero, violating order/data preservation on the first nonempty sample.

Inputs and reset are unconstrained. Declaration initializers specify initial
state for both RTL and reference logic; reset may occur later. Non-reset behavior
checks use only the reset input for disable; reset checks are not disabled by it.
The local CLI representatives are default; tool pairs are extended until their
scope/runtime is measured. No existing assertion or large-search count changes.
