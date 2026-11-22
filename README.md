# sva2yosys

**sva2yosys** makes concurrent SystemVerilog Assertions (SVA) usable with the free Yosys/SymbiYosys formal flow.

It reads SystemVerilog RTL with SVA through [Slang](https://github.com/MikePopoloski/slang). It lowers the temporal semantics of each concurrent assertion into an explicit monitor built from ordinary RTL and immediate `assert`/`assume`/`cover`. The output is plain SystemVerilog that unmodified open-source [Yosys](https://github.com/YosysHQ/yosys) and [SBY (SymbiYosys)](https://github.com/YosysHQ/sby) can check.

```text
SystemVerilog RTL + SVA ──► Slang (parse + elaborate) ──► sva2yosys ──► plain SystemVerilog ──► Yosys ──► SBY
```

For example, the property below says that every rising edge of `req` is followed by `ack` within one to three cycles. sva2yosys replaces it with a monitor of ordinary registers and logic, ending in an immediate `assert`, which Yosys and SBY can check:

```systemverilog
a_resp: assert property ($rose(req) |-> ##[1:3] ack);
```

**Scope.** sva2yosys supports a checked subset of concurrent SVA as defined by [IEEE 1800-2017](https://standards.ieee.org/ieee/1800/6700/) (the clause numbers in the documentation refer to this edition), including bounded sequences and selected unbounded and liveness forms. The [support matrix](https://github.com/sverlan/sva2yosys/blob/main/docs/supported-sva.md) gives the exact status and restrictions of each construct. A construct that is not supported is rejected with a located diagnostic, never approximated.

**Exact monitors.** A monitor tracks precisely the obligations that are alive and reports a failure at the earliest sample at which it becomes certain. Compact representations (counters, windows, history) are used only where they are checked to be equivalent to the plain one.

## Why this project

I am convinced that verification is necessary in hardware design, and that SVA assertions are one of the best ways to state what a design must do. I use Yosys for my research designs, and I would like to use it for their verification as well, in complement to [EBMC](https://github.com/diffblue/hw-cbmc) and other tools, so that design, synthesis and formal checking stay in the same ecosystem. The free Yosys/SymbiYosys flow does not read most of the temporal part of concurrent SVA; sva2yosys fills the gap by lowering a larger, precisely documented part of concurrent SVA to plain SystemVerilog that this flow reads unchanged.

## Installation

sva2yosys is published on [PyPI](https://pypi.org/project/sva2yosys/) and needs Python 3.12 or later. The Slang parser (`pyslang`) is installed with it; there is no other Python dependency. The package provides one command, `sva2yosys`.

**Permanent installation.** To get the `sva2yosys` command in an isolated environment of its own, available from any directory (recommended):

```text
pipx install sva2yosys          # with pipx
uv tool install sva2yosys       # with uv
```

To install it into the current Python environment instead (a virtual environment, for example):

```text
pip install sva2yosys
```

**Temporary run, without installing.** The tool is downloaded into a cache and run once:

```text
uvx sva2yosys --help            # with uv
pipx run sva2yosys --help       # with pipx
```

The same works with any arguments, for example `uvx sva2yosys props.sv -y rtl --top top -o build`. To upgrade an installed copy: `pipx upgrade sva2yosys`, `uv tool upgrade sva2yosys` or `pip install --upgrade sva2yosys`.

Checking a proof additionally needs [Yosys](https://github.com/YosysHQ/yosys), [SymbiYosys](https://github.com/YosysHQ/sby) and a solver. The [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build/releases) is one convenient way to get them all at once. sva2yosys itself only rewrites SystemVerilog and never runs them.

## Quick start

The examples are in the repository, not in the package:

```text
git clone https://github.com/sverlan/sva2yosys
cd sva2yosys/examples/pulse
sva2yosys props/pulse_props.sv -y rtl --top pulse              # check which assertions would be lowered
sva2yosys props/pulse_props.sv -y rtl --top pulse -o build     # write the monitors into build/
sby -f -d build/prove pulse.sby prove                          # prove the assertions (needs Yosys, SBY and ABC)
sby -f -d build/cover pulse.sby cover                          # check that the covers are reachable (needs Yosys, SBY and Yices)
```

The first two commands only check and generate monitors; the `sby` commands run the proof and the cover tasks on them. The generated files are read by Yosys, or by the `[script]` section of an `.sby` file, with `script build/sva2yosys_read.ys`.

The design, [`rtl/pulse.sv`](https://github.com/sverlan/sva2yosys/blob/main/examples/pulse/rtl/pulse.sv), is a registered rising-edge detector: `pulse_out` is high for one cycle, one cycle after `level` rises. Its properties are written in SVA, in a separate module that is bound to the design, [`props/pulse_props.sv`](https://github.com/sverlan/sva2yosys/blob/main/examples/pulse/props/pulse_props.sv).

[Your first proof](https://github.com/sverlan/sva2yosys/blob/main/docs/getting-started.md) explains each step and then exposes a deliberate bug.

## Other free workflows

sva2yosys is one way to check SVA with free tools; it targets the Yosys/SBY flow and lowers each assertion to plain SystemVerilog. Another is [EBMC](https://github.com/diffblue/hw-cbmc) (BSD-3-Clause), a free model checker that reads SVA natively and checks it with bounded model checking, BDD or Büchi-based engines. The two cover different parts of the language, bring different engines, and give different answers on some constructs (EBMC's bounded checks and unbounded engines are not interchangeable with those of the Yosys/SBY flow). The [comparison matrix](https://github.com/sverlan/sva2yosys/blob/main/docs/tool-comparison.md#comparison-matrix) compares them construct by construct, and the sections after it record the versions and measurements behind it.

## Documentation

**How to use it**

- [Your first proof](https://github.com/sverlan/sva2yosys/blob/main/docs/getting-started.md): setup, generation, proof, cover and a counterexample, step by step.
- [CLI manual](https://github.com/sverlan/sva2yosys/blob/main/docs/usage.md): options, inputs, outputs and exit status.
- [`examples/`](https://github.com/sverlan/sva2yosys/blob/main/examples/README.md): small design/property workflows (pulse, counter, FIFO, handshake) with checked results.

**What is supported**

- [`docs/supported-sva.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/supported-sva.md): the status of each construct in one matrix.
- [`docs/sva-reference.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/sva-reference.md): the reference, construct by construct: restrictions, warnings and workarounds.
- [`docs/sva-examples.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/sva-examples.md): complete examples with their expected compiler outcomes, checked by the tests.

**Why you can trust it** (see also [How it is developed](#how-it-is-developed))

- [`docs/validation.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/validation.md): what an accepted assertion guarantees, the evidence behind it, and the readings of the standard adopted.
- [`docs/semantics.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/semantics.md): the normative rules and timing.
- [`docs/traceability.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/traceability.md): which test cases exercise each construct and IEEE clause (generated).
- Comparison with other flows: [`docs/tool-comparison.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/tool-comparison.md) opens with the comparison matrix and holds the versions, reproducible observations and source-only qualifications of the six compared flows, and [`docs/interpretation-probes.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/interpretation-probes.md) the targeted observations on how simulators read some constructs. Read the matrix at the top of the first page before the evidence.

**How it works**

- [`docs/monitors.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/monitors.md): what a generated monitor contains, as a skeleton for each kind of property (bounded, unbounded, strong and live, cover).
- [`docs/strategies.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/strategies.md): direct (online) and delayed (lookback) lowering, with worked examples.
- [`docs/architecture.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/architecture.md): internal structure, Slang and Yosys findings.

**Project**

- [`CONTRIBUTING.md`](https://github.com/sverlan/sva2yosys/blob/main/CONTRIBUTING.md): rules for changes, test tiers and layout, external tools.
- [`CHANGELOG.md`](https://github.com/sverlan/sva2yosys/blob/main/CHANGELOG.md): release notes.
- [`docs/test-matrix.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/test-matrix.md): the tests of each area by tier, tool and corpus (generated).

## How it is developed

The development is AI-assisted, and the work is shared between the human author and the AI. The human author plans and validates each step and rereads the code and the semantic decisions; the AI implements the code and writes the tests. Independent AI reviews of the plan and of the code are used as an additional check; they do not replace the human steps.

I use sva2yosys in my own research on hardware designs, so I will maintain it for a reasonable period: fixing semantic errors, extending the supported constructs, and following new versions of Slang and of the Yosys tools. Bug reports and requests for constructs are welcome as [GitHub issues](https://github.com/sverlan/sva2yosys/issues).

### What protects against errors

Most of the code is written by an AI, so correctness cannot rest on the author of the code. It rests on how the work is organized. Passing tests is not a proof, because tests and code can share a mistaken reading of the standard, but the following measures are built into the process and can be checked in the repository:

- **A property is not silently weakened.** An unsupported construct is rejected with a located diagnostic, and an unbounded `$` is never replaced by a finite limit. Rejecting a property is acceptable; proving a different property than the one written is not.
- **Contract before code.** Each component is developed in the order interface, contract, tests, implementation. The intended behavior and its limits are written down before the code exists, so the tests are not fitted to the code.
- **Expected results come from the standard.** The test cases give the inputs and the expected outcome at every sample (timing, reset, failure, non-vacuous cover). The expected outcomes are derived by hand from [IEEE 1800](https://standards.ieee.org/ieee/1800/6700/) and never copied from the output of sva2yosys or of another tool.
- **Two separate implementations of the semantics.** The same cases are checked against a reference evaluator written independently of the compiler, and against the generated monitors.
- **The generated code is checked by the real flow.** The tests run the output through Yosys and SBY and check what it does (proof and cover results), not the text it contains.
- **Other people's tests and adversarial checks.** The output is compared with public corpora (see the next sections), with EBMC in a differential test, and with generated combinations of properties and traces. A mutation campaign plants deliberate bugs in the compiler and requires the test suite to detect each of them.
- **Every counterexample becomes a test.** A bug found by a review, a probe or a mutant is added as a test with a hand-derived result, in the same change as its fix.
- **Narrow dependencies.** Only the `slang` package uses the Slang API; the rest of the compiler works on typed internal structures. The only runtime dependency is `pyslang`.
- **The limits are documented.** [`docs/validation.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/validation.md) states what an accepted assertion guarantees, the evidence for it, and the readings of the standard adopted where tools diverge.

## Testing against third-party corpora

Besides the hand-written trace tests, sva2yosys is run regularly against public SystemVerilog and SVA corpora. They are never copied into the repository: a script fetches each one at a pinned commit, and the test tables record the commit and a digest of the files, so a different revision is refused instead of compared silently.

- **Known answers** (the expected verdict of each case is checked): the [Yosys SVA tests](https://github.com/YosysHQ/yosys/tree/main/tests/sva), the [EBMC](https://github.com/diffblue/hw-cbmc) regression suite for SVA, and the [SymbiYosys](https://github.com/YosysHQ/sby) examples and tests.
- **Acceptance survey** (how much real code is accepted, rejected with which diagnostic, and the size of the monitors): [SVA-AXI4-FVIP](https://github.com/YosysHQ-GmbH/SVA-AXI4-FVIP), [sv-tests](https://github.com/chipsalliance/sv-tests), [common_cells](https://github.com/pulp-platform/common_cells), [wb2axip](https://github.com/ZipCPU/wb2axip), [OpenTitan](https://github.com/lowRISC/opentitan) and [AssertionBench](https://github.com/achieve-lab/assertion_data_for_LLM).

Sources, pinned commits, licenses, what each corpus is used for, and what was considered and not used are in [`docs/validation.md`](https://github.com/sverlan/sva2yosys/blob/main/docs/validation.md#corpora).

## The SVA IEEE corpus: independent checks for any SVA tool

[`sva-ieee-corpus/`](https://github.com/sverlan/sva2yosys/blob/main/sva-ieee-corpus/README.md) is a by-product of the validation work that is useful on its own: 661 small, self-contained assertion, assumption and cover cases, each with an expected result derived by hand from IEEE 1800, sample by sample. It is generated from the hand-written trace tables that the test suite uses, and it does not depend on sva2yosys. It can be used to check any other implementation of SVA, a simulator, a formal tool or another lowering, independently of this project, and to find where tools read the standard differently. It is released under the MIT license, with its own copy of the license file. To check the corpus with Yosys and SBY, see [the section on it in the corpus README](https://github.com/sverlan/sva2yosys/blob/main/sva-ieee-corpus/README.md#checking-the-corpus-with-yosys-and-sby).

## Building from source and contributing

From a source checkout, with [uv](https://docs.astral.sh/uv/):

```sh
uv sync                                  # environment
uv run sva2yosys --help
uv run pytest                            # local tests only (no external tool or corpus)
cp .env.example .env                     # then edit local tool and corpus paths
uv run --env-file .env pytest --external-tools=all --external-corpora=all          # validation
uv run --env-file .env pytest --external-tools=all --external-corpora=all --slow   # every tier
uv run ruff check . && uv run mypy       # lint and type check
```

The rules for contributions, the test tiers and the external tools are in [`CONTRIBUTING.md`](https://github.com/sverlan/sva2yosys/blob/main/CONTRIBUTING.md).

## License

MIT: see [`LICENSE`](https://github.com/sverlan/sva2yosys/blob/main/LICENSE). The SVA IEEE corpus under `sva-ieee-corpus/` is MIT as well, with its own copy of the license.

## Acknowledgements

sva2yosys is an independent implementation of an SVA lowering compiler for open-source formal verification.

The project was inspired in part by [sva-rtl-transpiler](https://github.com/astrollo/sva-rtl-transpiler) by astrollo, which demonstrated that SVA can practically be parsed with Slang and lowered into ordinary SystemVerilog checker logic. sva2yosys uses a separately designed semantic IR and an exact temporal compilation architecture, and does not reuse the implementation of sva-rtl-transpiler.

sva2yosys builds on:
- [Slang](https://github.com/MikePopoloski/slang) and its Python bindings, pyslang, by Mike Popoloski, for parsing and elaborating SystemVerilog;
- [Yosys](https://github.com/YosysHQ/yosys) and [SymbiYosys](https://github.com/YosysHQ/sby) by YosysHQ, the open-source synthesis and formal verification flow that sva2yosys targets.

Its tests are checked against [EBMC](https://github.com/diffblue/hw-cbmc) and [Spot](https://spot.lre.epita.fr/), run as external programs, and against the public corpora listed above, whose authors are thanked here.
