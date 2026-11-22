"""The generated traceability page is current and complete (CONTRACT.md, "Traceability page")."""

from __future__ import annotations

import re

from support.traceability import (
    MATRIX,
    PAGE,
    ROWS,
    STRUCTURAL,
    Construct,
    collect,
    mapped_rows,
    matrix_rows,
    render,
    verified_rows,
)


def test_committed_page_equals_the_generated_one() -> None:
    assert PAGE.read_text(encoding="utf-8") == render(), (
        "docs/traceability.md is stale: run `uv run python tests/support/traceability.py --write`"
    )


def test_every_construct_has_an_accepted_trace_case() -> None:
    accepted = {
        construct
        for item in collect()
        if not item.case.oracle_only
        for construct in item.constructs
    }
    assert set(Construct) - accepted == set()


def test_every_tested_matrix_row_is_mapped() -> None:
    """A status cell containing TESTED anywhere (mixed-status rows included) needs a mapping."""
    tested = verified_rows(MATRIX.read_text(encoding="utf-8"))
    assert tested - mapped_rows() == set(), "TESTED rows without a construct or a structural area"


def test_every_mapped_row_exists_in_the_matrix() -> None:
    rows = set(matrix_rows(MATRIX.read_text(encoding="utf-8")))
    assert mapped_rows() - rows == set(), "mapped rows missing from the matrix (renamed?)"


def test_structural_evidence_points_exist_in_the_contracts() -> None:
    """Every structural entry cites a numbered point that exists in the area's CONTRACT.md."""
    root = PAGE.parents[1] / "tests" / "functional_tests"
    for row, evidence in STRUCTURAL.items():
        assert evidence, row
        for item in evidence:
            contract = (root / item.area / "CONTRACT.md").read_text(encoding="utf-8")
            assert re.search(rf"^{re.escape(item.point)}\. ", contract, re.MULTILINE), (
                f"{row}: point {item.point} of {item.area}"
            )
    assert set(ROWS) == set(Construct)
