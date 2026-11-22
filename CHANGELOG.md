# Changelog

All notable changes to sva2yosys are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the version numbers follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) with this reading while the major version is 0:

- a **minor** release (0.2.0, ...) may change the command line, the shape of the generated monitors or the
  accepted subset of SVA (for example by rejecting what was accepted by mistake);
- a **patch** release (0.1.1, ...) only fixes defects and does not widen the accepted subset;
- a property is never accepted with a different meaning in a later release: a fix to a wrong reading is a
  rejection or a corrected result, and the entry says so.

The version is written once, as `version` in `pyproject.toml`; `sva2yosys --version` prints it. Generated
files carry no version, so a release never changes an output by itself.

## [Unreleased]

## [0.1.0]

First release: a checked subset of concurrent SVA is read through Slang and lowered to plain
SystemVerilog monitors for the unmodified Yosys/SBY flow; everything else is rejected with a located
diagnostic. The features and their limits are described in the documentation, not repeated here:

- what the command does and its options: [`docs/usage.md`](docs/usage.md);
- which constructs are accepted, and which are rejected: [`docs/supported-sva.md`](docs/supported-sva.md)
  and [`docs/sva-reference.md`](docs/sva-reference.md);
- what an accepted assertion guarantees: [`docs/validation.md`](docs/validation.md);
- the SVA IEEE corpus, 661 cases with hand-derived results, MIT-licensed: [`sva-ieee-corpus/`](sva-ieee-corpus/README.md).

The read script it writes is for `read_verilog` (the default) or, with `--yosys-frontend slang`, for the `read_slang` plugin, which applies `bind` (binds are kept; liveness is not possible in that mode); see [`docs/usage.md`](docs/usage.md).

Requires Python 3.12 or newer and `pyslang` 12.0.0 (pinned); proving needs the
[OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build).

[Unreleased]: https://github.com/sverlan/sva2yosys/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sverlan/sva2yosys/releases/tag/v0.1.0
