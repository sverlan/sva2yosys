# Contributing to sva2yosys

sva2yosys lowers concurrent SystemVerilog Assertions (read through [Slang](https://github.com/MikePopoloski/slang)) into plain SystemVerilog monitors that the unmodified Yosys/SymbiYosys flow can check. The primary requirement is **trustworthy semantics**: rejecting an unsupported property is acceptable, silently proving the wrong property is not.

## Architecture rules

- **Slang is isolated.** Only `src/sva2yosys/slang/` imports `pyslang`; everything else consumes typed internal structures (`tests/unit_tests/test_package_boundaries.py` enforces it).
- **Parsing, semantics and code generation stay in separate modules**, connected by the typed IR (see [`docs/architecture.md`](docs/architecture.md)).
- **Never guess a bound.** `$` is never replaced by a finite limit. An unsupported construct fails with a located diagnostic, never a silently different property.
- **Dependencies stay minimal.** Do not add a package or tool without discussing it first: what it is for, the alternatives (including the standard library), and its cost.

## Free tools only

Correctness rests on the in-repo reference evaluator, the hand-written trace tables and the free Yosys/SBY flow. Do not use commercial SVA or formal tools for design decisions or verification, and do not take expected results from another tool's output.

## Setup and checks

Use [uv](https://docs.astral.sh/uv/) only (`uv sync`, `uv run ...`); do not use `pip install` or `python -m venv`.

```sh
uv sync                    # environment
uv run pytest              # local tests only (no external tool or corpus)
uv run ruff check .        # lint
uv run ruff format src tests
uv run mypy                # strict type check
```

Pytest, ruff check and mypy must all be clean before a change is proposed. Run `ruff format` on `src/` and `tests/` only; fixture files are never reformatted.

Tests that need Yosys, SBY or other external tools and corpora, and the longer test tiers (`--extended`, `--slow`), are opt-in: see [`docs/validation.md`](docs/validation.md) and [`.env.example`](.env.example). CI runs the default tests with Yosys and SBY. Run the opt-in tests that cover what you changed, and say in the pull request if you could not. The full validation (every tool and corpus, with `--slow`) is the maintainer's check before a release ([`docs/validation.md`](docs/validation.md#7-reproducing-the-evidence)).

## Writing a change

- **Interface, contract, tests, implementation**, in this order: typed signatures and docstrings first, then a `CONTRACT.md` (functional areas under `tests/functional_tests/`) or the docstring contract (units), then the tests, then the code.
- A feature is supported only when it has both semantic tests and generated-code tests; being parsed by Slang is not enough.
- A test that checks something not in its `CONTRACT.md` is either a contract change (update the file) or a unit test.
- A test checks what the output does (elaboration, SBY results), not the text it emits.
- Every counterexample found (a review finding, a probe, a caught mutant) becomes a test case in the same change as its fix, with its expected result derived by hand.

## Code style

"Rust-shaped Python": frozen `dataclass`es for the IR, `Enum` for tags, `tuple` for child collections, type annotations everywhere, explicit `match` on IR nodes, pure transformations and narrow module interfaces.

## Documentation

A change updates the documents it affects in the same commit:

| Change | Update |
|---|---|
| a construct is parsed, implemented, tested or rejected | [`docs/supported-sva.md`](docs/supported-sva.md), [`docs/sva-reference.md`](docs/sva-reference.md) and, for a checked example, [`docs/sva-examples.md`](docs/sva-examples.md) |
| command-line options, inputs or outputs | [`docs/usage.md`](docs/usage.md) and the help in `src/sva2yosys/cli.py` |
| module boundaries, IR nodes, pipeline stages | [`docs/architecture.md`](docs/architecture.md) |
| timing, sampling, disable, cover, vacuity, weak or strong rules | [`docs/semantics.md`](docs/semantics.md) |
| lowering representation or strategy selection | [`docs/strategies.md`](docs/strategies.md) |
| evidence, oracles, readings of IEEE 1800 | [`docs/validation.md`](docs/validation.md) |
| a trace case, the construct mapping or the corpus generator (`tests/support/trace_suite.py`) | regenerate [`docs/traceability.md`](docs/traceability.md) and `sva-ieee-corpus/` (`uv run python tests/support/traceability.py --write`, `uv run python tests/support/trace_suite.py --write`) |
| a reference example | refresh with `uv run python tests/support/documentation_examples.py --write` |

## Commits and licence

- One coherent change per commit; do not mix layout moves, contract changes and behaviour fixes.
- By contributing you agree that your contribution is licensed under the [MIT licence](LICENSE).
