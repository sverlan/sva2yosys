# Targeted interpretation observations

Measured with **Verilator 5.053 devel rev
v5.052-651-g92ac4051b (mod)** (an earlier measurement with 5.047 differed for
seven probes: I5–I10 and D1 below say how) and with **EBMC 6.1 (ebmc-6.1)**. These are
observations of these builds, within each probe's limits. The standard and the
hand-derived reference cases remain the semantic basis. These observations do not
change the accepted-language boundary or the monitor semantics.

The row names refer to [validation §5](validation.md#5-interpretations-and-known-deviations).
Fixtures and exact event expectations are under
[`tests/functional_tests/external/interpretations/`](../tests/functional_tests/external/interpretations/).
The manifest states each objective and its limitation. An event's integer is the
zero-based clock sample except for the Boolean results in I11. Action ordering
within one sample is disregarded; event multiplicity is retained.

| Row | Native Verilator observation | What it establishes | EBMC column |
|---|---|---|---|
| I1 | Both `a \|=> 0` and `a \|-> ##1 0` fail at sample 1 after the sample-0 trigger; true-consequent controls hold | Compatible for these bounded constant cases; no claim about arbitrary contradictions or unbounded constants | Existing allowed BMC constant cases and implication calibration controls agree on opposite finite validity results; no failure-time comparison |
| I2 | Rising disable: failures 1, 2; falling disable: failure 3 | Different from post-update evaluation, which gives rising failure 1 and falling failures 2, 3. Internal register disables remain rejected by sva2yosys | Excluded: disable timing is outside calibration |
| I3 | `$past(a)` fails at 0; the negated history control fails at 1 | Compatible predicate outcomes on this trace; two-state zero initialization cannot establish all-X prehistory semantics | Excluded: sampled history |
| I4 | After a disabled sample, the inverted retained-history condition fails at 2 and the positive condition holds | Compatible with history surviving disable on this trace | Excluded: disable/history |
| I5 | Implication cover actions occur at 2–5 (not at 0–1, where the antecedent is false); Boolean conjunction cover actions at 2–5; impossible cover has no action | Implication-cover actions are nonvacuous on this trace (the 5.047 build also fired at 0–1). One trace and one operator: it is no evidence for other operators | Excluded: implication-cover nonvacuity |
| I6 | Cover of `a ##[1:2] b`: one action, at sample 1, for the single attempt (`a` is true only at sample 0); the second endpoint at sample 2 gives no second action | Consistent with one cover action per attempt, at its first match. One attempt only: no claim for several attempts or for `cover sequence` (the 5.047 build rejected `##[1:2]`) | Not comparable: calibrated reachability cannot distinguish counts |
| I7 | `not (a ##[1:$] b)` with `b` never true: no failure in the six samples | Consistent with `not` of a sequence that never matches holding. A finite run shows neither the strength nor infinite behavior (the 5.047 build rejected the unbounded delay) | Excluded: unbounded negated sequences |
| I8 | Rejects bare `strong(1'b1 ##[1:$] b)` with `Unsupported: strong property in assert`; after `\|->` the `strong(...)` form is a syntax error (`unexpected strong`), seen by hand and not pinned: the harness records only explicit unsupported diagnostics | Unsupported syntax; no finite-end finalization observation | Not comparable: BMC depth is not end of simulation |
| I9 | Stable gate: failures at samples 2, 3 only; blocking-updated gate: failures at every sample 0–5 | Agrees with procedural enabling on this fixture: the stable gate permits only samples 2, 3 (the 5.047 build failed at every sample for both labels). The blocking-updated condition is outside sva2yosys's accepted stable-condition boundary | Excluded: procedural assertion context |
| I10 | Property `if ($past(selector)) a else b` with `a` and `b` false: failure at every sample 0–5 (the 5.047 build rejected property `if` as unsupported) | Both branches fail, so the run cannot show which branch a missing-history selector takes. Remains open and rejected by sva2yosys | Excluded: unknown values/history |
| I11 | Mixed-list results: fill 0, arithmetic 0, sign 1; uniform controls: hit 1, miss 0 | Reproduces this tool's mixed sizing behavior; does not choose an IEEE interpretation. Mixed operands remain rejected | Excluded: `inside` sizing |
| D1 | `$stable(bit)` holds at sample 0 (`stable_zero 0` denotes sample 0); `$past(initialized)`, with a declaration initializer of 1, holds at sample 0 | Both compatible with the default-sampled-value rule (an initialized signal's default is its initializer; the 5.047 build failed that `$past` at 0). These operands remain rejected by sva2yosys | Excluded: default sampled values/history |

The execution control contains a holding tautology, an assertion failing only at
sample 2 and its inverse failing at 0, 1, 3, 4, 5. Missing assertion execution,
unexpected actions or a missing terminal marker fail the test. All native sources
also elaborate through Slang; native syntax rejection is not inferred from an
ill-formed fixture. Build errors, runtime failures and timeouts are infrastructure
failures, never accepted observations.

## Reproduction

Copy the documented Verilator configuration from [`.env.example`](../.env.example)
into the local `.env`: executable, MSYS shell, runtime root containing
`include/verilated.h`, and build directories containing the existing C++ compiler
and make. Paths and temporary environment overrides are passed to the configured
shell positionally. No additional software is installed or searched for by tests.
The Windows build uses the configured MSYS shell; EBMC independently uses its
configured command, which may run through WSL. No distribution name is assumed.

```sh
# Local metadata, pin and well-formedness checks; compiled probes are unselected.
uv run pytest tests/functional_tests/external/interpretations
# One compiled simulator control plus the finite EBMC controls.
uv run --env-file .env pytest --external-tools=verilator,ebmc tests/functional_tests/external/interpretations
# All native topic probes, including the explicit unsupported rows.
uv run --env-file .env pytest --external-tools=verilator,ebmc --extended tests/functional_tests/external/interpretations
```

Every simulator comparison checks the exact version and the SHA-256 of the SV
sources and manifest before building. A changed version or digest fails with a
reclassification message. EBMC comparisons use the differential tier's existing
trace digest, version pin, per-engine allow-list and opposite calibrated controls;
Spot is not needed by this BMC-only column. Builds and logs stay in pytest's
temporary directory. Checked-in expectations pin observations, not agreement with
the standard. Reclassification requires inspection and review.
