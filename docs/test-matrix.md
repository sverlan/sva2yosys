# Test matrix

This page is generated; do not edit it. It counts the collected tests of each area per tier (`default`, `extended`, `slow`: the tier of its markers, `slow` wins) and the tests that need each external tool or corpus (a test needing several counts under each, so those columns do not add up to the total). The tools and corpora are selected with `--external-tools` and `--external-corpora`. Regenerate it with `uv run python tests/support/suite_matrix.py --write`; a test fails when it is stale.

| area | default | extended | slow | yosys | sby | sby-live | slang | ebmc | spot | verilator | yosys-sva | ebmc (corpus) | sby-examples |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `coverage_tests` | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `functional_tests/cli` | 107 | 1 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `functional_tests/documentation_examples` | 49 | 0 | 0 | 0 | 4 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `functional_tests/external/ebmc_corpus` | 7 | 0 | 355 | 1 | 356 | 355 | 0 | 0 | 0 | 0 | 0 | 358 | 0 |
| `functional_tests/external/ebmc_differential` | 55 | 0 | 236 | 0 | 0 | 0 | 0 | 263 | 38 | 0 | 0 | 0 | 0 |
| `functional_tests/external/interpretations` | 19 | 13 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 15 | 0 | 0 | 0 |
| `functional_tests/external/sby_examples` | 6 | 0 | 108 | 1 | 110 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 111 |
| `functional_tests/external/tool_comparison` | 26 | 0 | 0 | 17 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `functional_tests/external/yosys_corpus` | 29 | 0 | 25 | 0 | 25 | 0 | 0 | 0 | 0 | 0 | 53 | 0 | 0 |
| `functional_tests/formal_designs` | 7 | 52 | 0 | 0 | 56 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `functional_tests/generated_monitors` | 202 | 70 | 1123 | 71 | 1258 | 85 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `functional_tests/optimized_monitors` | 7 | 17 | 21 | 20 | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `functional_tests/reference_semantics` | 679 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `functional_tests/rewrite` | 198 | 23 | 68 | 4 | 140 | 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `functional_tests/slang_convert` | 489 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `functional_tests/slang_frontend` | 149 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| `functional_tests/sva_ieee_corpus` | 184 | 24 | 1 | 0 | 46 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 |
| `functional_tests/temporal_machine` | 2327 | 33 | 56 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `functional_tests/yosys_contract` | 6 | 62 | 0 | 15 | 35 | 19 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `unit_tests` | 3320 | 107 | 116 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **total** | 7874 | 403 | 2109 | 129 | 2062 | 481 | 4 | 265 | 38 | 18 | 53 | 358 | 111 |
