# Yosys SVA corpus contract (opt-in)

These tests run sva2yosys on an external corpus: the SVA tests of the Yosys project (`tests/sva` of https://github.com/YosysHQ/yosys, ISC license). The corpus is **not** part of this repository. A local checkout lives outside the repository, at an absolute path of your choice. `tests/support/corpora.py` fetches it (pinned to a commit, sparse on `tests/sva`) together with the other external corpora:

```text
uv run python tests/support/corpora.py C:/corpora yosys
```

`--update` re-fetches an existing clone, `--latest` takes the upstream head instead of the pinned commit; the script prints the `.env` line to use. The tests are opt-in: `SVA2YOSYS_YOSYS_SVA_TESTS` names the corpus directory (`'C:/corpora/yosys/tests/sva'`, an absolute path). They run only when the run selects the corpus (`--external-corpora=yosys-sva` or `all`) and, for the verdict tests, the tool they use (`--external-tools=sby` or `all`); otherwise they are skipped with a reason naming the option. A selected corpus whose variable is unset or does not name a directory is not a skip: pytest stops with a usage error before any test runs.

What is checked into the repository is the expectation table `fixtures/expectations.txt`: one line per case, a case being a corpus file `X.sv` read as is (`pass` variant) or with `FAIL` defined (`fail` variant). Each line gives:

- `pass` or `fail`: sva2yosys accepts the file, and the verdict is the given one;
- `rejected <fragment>`: sva2yosys rejects the file, and its first error message contains the fragment;
- `skipped <reason>`: the case cannot run here (e.g. a VHDL design).

A note may follow `#`. The table records the corpus revision it was written for, and the content digest that pins it.

The corpus's own convention (`runtest.sh`) is used: SBY `mode bmc`, `depth 10`, `smtbmc yices`, top module `top`, and `chformal -early -assume` after `prep`. The `fail` variant is read with `FAIL` defined (the corpus includes the file after `` `define FAIL ``; here `-D FAIL`).

## Contract

1. **The corpus is the classified one, and every case is listed.** The table's `corpus-sha256:` line is the SHA-256 of the corpus files (names and contents, line ends normalised to LF, in name order); a checkout with other content fails until every case is re-classified and the line updated. Each `X.sv` of the corpus has both variants in the table, and the table lists no file that the corpus lacks. A new upstream test therefore fails this check until it is classified. The digest is checked once per session by a fixture on which every other test of this area depends: on another revision they all fail (they do not skip) with the one message to fetch the pinned commit or re-classify; the digest test itself checks it directly and is the representative. A test that needs no corpus checks that guard on a scratch directory and that no test reaches the corpus around it.
2. **Acceptance is as listed.** For each case not `skipped`, the CLI (`sva2yosys X.sv [-D FAIL] -o DIR`) accepts the file when the table says `pass` or `fail`, and rejects it with exit status 1 and an error containing the fragment when it says `rejected`. A newly supported construct therefore fails this check until the table is updated; the change is reviewed, never automatic.
3. **Accepted cases have the listed verdict** (marked `slow`; needs `SVA2YOSYS_SBY`). The output, read with the generated `sva2yosys_read.ys`, gives the listed BMC result. The listed verdict is the corpus's expectation, except where a note explains a difference that does not come from sva2yosys (e.g. the same result without sva2yosys on this Yosys build).
