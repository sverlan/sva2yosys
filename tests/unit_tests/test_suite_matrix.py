"""Unit tests of the pure part of support.suite_matrix: area names, aggregation, rendering."""

from __future__ import annotations

from pathlib import Path

from support.suite_matrix import (
    TestRecord,
    aggregate,
    area_of,
    collection_command,
    collection_environment,
    render,
)
from support.verilator import ENV_BUILD_PATH, ENV_ROOT, ENV_SHELL, ENV_VERILATOR


def test_collection_selects_nothing_external_explicitly() -> None:
    command = collection_command()
    assert "--collect-only" in command
    assert "--external-tools=none" in command and "--external-corpora=none" in command


def test_collection_environment_drops_inherited_pytest_options_and_resources(
    tmp_path: Path,
) -> None:
    # origin: review. PYTEST_ADDOPTS reached the child: a filter gave a partial page.
    base = {
        "PYTEST_ADDOPTS": "-k nothing --external-tools=all",
        "PYTEST_PLUGINS": "x",
        "SVA2YOSYS_YOSYS": "y",
        "SVA2YOSYS_YOSYS_SVA_TESTS": "c",
        ENV_VERILATOR: "verilator",
        ENV_SHELL: "shell",
        ENV_ROOT: "root",
        ENV_BUILD_PATH: "build-path",
        "KEEP": "1",
    }
    environment = collection_environment(base, tmp_path / "out.json")
    assert environment["KEEP"] == "1"
    assert not {"PYTEST_ADDOPTS", "PYTEST_PLUGINS", "SVA2YOSYS_YOSYS"} & set(environment)
    assert "SVA2YOSYS_YOSYS_SVA_TESTS" not in environment
    assert not {ENV_VERILATOR, ENV_SHELL, ENV_ROOT, ENV_BUILD_PATH} & set(environment)
    assert environment["SVA2YOSYS_TEST_MATRIX_OUT"].endswith("out.json")
    assert base["PYTEST_ADDOPTS"]  # the input is not modified


def test_area_of_names_the_folder_of_the_test_file() -> None:
    assert area_of("unit_tests/test_a.py") == "unit_tests"
    assert area_of("coverage_tests/test_a.py") == "coverage_tests"
    assert area_of("functional_tests/cli/test_cli.py") == "functional_tests/cli"
    assert area_of("functional_tests/cli/sub/test_x.py") == "functional_tests/cli"
    assert (
        area_of("functional_tests/external/yosys_corpus/test_c.py")
        == "functional_tests/external/yosys_corpus"
    )


RECORDS = [
    TestRecord("unit_tests", "default"),
    TestRecord("unit_tests", "default"),
    TestRecord("unit_tests", "slow"),
    TestRecord("functional_tests/a", "extended", frozenset({"yosys", "sby"})),
    TestRecord("functional_tests/a", "slow", frozenset({"sby"}), frozenset({"yosys-sva"})),
]


def test_aggregate_counts_per_area_sorted_with_totals_last() -> None:
    rows = aggregate(RECORDS)
    assert [r.area for r in rows] == ["functional_tests/a", "unit_tests", "total"]
    first, second, total = rows
    assert first.tiers == {"default": 0, "extended": 1, "slow": 1}
    assert first.tools == {
        "yosys": 1,
        "sby": 2,
        "sby-live": 0,
        "slang": 0,
        "ebmc": 0,
        "spot": 0,
        "verilator": 0,
    }
    assert first.corpora == {"yosys-sva": 1, "ebmc": 0, "sby-examples": 0}
    assert second.tiers == {"default": 2, "extended": 0, "slow": 1}
    assert total.tiers == {"default": 2, "extended": 1, "slow": 2}
    assert total.tools["sby"] == 2
    assert total.corpora["yosys-sva"] == 1


def test_aggregate_of_nothing_has_only_a_zero_totals_row() -> None:
    (total,) = aggregate([])
    assert total.area == "total"
    assert sum(total.tiers.values()) == 0


def test_render_is_a_deterministic_table_with_a_totals_row() -> None:
    page = render(RECORDS)
    assert page == render(list(reversed(RECORDS)))
    lines = page.splitlines()
    header = next(line for line in lines if line.startswith("| area"))
    assert header == (
        "| area | default | extended | slow | yosys | sby | sby-live | slang "
        "| ebmc | spot | verilator | yosys-sva | ebmc (corpus) | sby-examples |"
    )
    assert "| `unit_tests` | 2 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |" in lines
    assert lines[-1] == "| **total** | 2 | 1 | 2 | 1 | 2 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |"
    assert "suite_matrix.py --write" in page
