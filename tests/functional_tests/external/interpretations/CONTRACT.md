# Targeted free-tool interpretation observations

1. **Purpose.** Every I1-I11 and D1 row in `docs/validation.md` has an explicit
   comparison objective and limitation. These are versioned observations, not an
   oracle that overrides the standard or the in-repo reference evaluator.
2. **Controls.** A fast simulator fixture contains both a holding assertion and a
   deliberately failing assertion, with explicit action events and end marker.
   Removing assertion execution or flipping an expected event must fail a test.
   Topic fixtures contain distinguishing positive/negative controls where the tool
   supports that objective. Rejected syntax cannot establish semantic agreement.
3. **Sampling.** Inputs change away from clock edges. Events report the original
   sample index (or a clearly named scalar/count), not simulator scheduling order.
   Initialization is stated. Unknown-prehistory/four-state and infinite-trace claims
   are not inferred from a two-state finite simulation. Nonvacuity, coverage-point
   counts and per-attempt counts are distinct objectives.
4. **No emulation.** Unsupported operators/observation APIs are recorded with their
   diagnostics or a model limit. A handwritten replacement cannot count as a native
   independent-tool result. Unexpected build/runtime failures, timeouts, missing
   completion markers and wrong observations fail the run.
5. **Explicit resources.** Verilator tests request `verilator_tool`, selected through
   `--external-tools=verilator` (or all), with explicit executable, build shell,
   runtime root and build PATH configuration. A selected resource with invalid
   configuration stops the run. No installation, PATH search or hard-coded host
   path occurs. Relative configuration paths are resolved before changing working
   directories. Ambient-only build tools cannot rescue missing configured build
   dependencies. Every build/log/executable is under pytest tmp_path.
6. **Pinning.** Exact Verilator version and SHA-256 of all SV/manifest inputs are
   recorded with observations. Session guards required by every comparison reject
   changed inputs/tools before running a probe, with a reclassification message.
   The source-digest guard has a fast local representative. A different version is
   a failure, not a skip or a newly accepted expectation.
7. **EBMC.** The EBMC column uses existing trace cases, allow-list selections and
   per-engine calibration through the differential helper. Its version remains
   pinned to 6.1; Spot is required only by a selected Buechi route. Finite validity
   is not earliest-failure timing, reachability is not a cover count, and excluded
   disable/history/X/sizing behavior is not compared. Positive/negative existing
   calibrated controls accompany measured validity; no allow-list expansion.
8. **Tiers.** Local manifest/parser/pinning tests and one small compiled simulation
   representative run in default (tool opt-in). Broader compilation matrices are
   extended when they take more than a few seconds; retain a fast representative
   for every contract. Unsupported-syntax and model-limit rows stay visible.
9. **Reproduction.** Fixtures, commands, versions and observed events/diagnostics are
   checked in with public documentation. Changed classifications are reviewed.
   No product semantics or supported-language boundary changes in this area.
