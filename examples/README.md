# Small verification examples

These examples show a design, its SVA properties and the lowering/SBY workflow.
Sources are kept here and reused by tests rather than copied into test fixtures.

Start with [Your first proof](../docs/getting-started.md) for installation
and a guided pulse example, including an expected counterexample.

| Example | What it demonstrates |
|---|---|
| [Pulse](pulse/README.md) | Registered rising-edge detection and pulse width |
| [Counter](counter/README.md) | Enabled modulo counting, reset and wraparound |
| [Handshake](handshake/README.md) | Request hold, bounded acknowledgement and a complete transaction |
| [FIFO](fifo/README.md) | Two-entry occupancy, flags and data order |

All four examples are original and contain no imported code. Each is checked by the test suite (`tests/functional_tests/formal_designs`): SBY proves the assertions and reaches the covers. Pulse, counter and FIFO include a deliberate `EXAMPLE_BUG` variant that SBY refutes; the handshake is checked against mutants of its design. They are covered by the repository's MIT licence and need no additional packages.

All examples use one clock. At a sampled tick, assertions see values before that
tick's nonblocking updates; `|=>` compares with the following sample. Resets are
synchronous. `disable iff` can cancel an obligation that is already open and suppresses new ones during reset, so a
property about reset behaviour must stay active during reset. State initializers are part of each model.

Run each recipe from its example directory after installing sva2yosys, Yosys,
SBY and the configured solvers. Generated
`build/` files and SBY work directories are disposable and are not source files.
