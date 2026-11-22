"""Every trace case states its clause and its class (CONTRACT.md, "Case metadata")."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from support.tracefile import CaseClass, TraceCase, load_cases

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VALIDATION = Path(__file__).resolve().parents[3] / "docs" / "validation.md"
CASES = [case for path in sorted(FIXTURES.glob("*.trace")) for case in load_cases(path)]


def _interpretation_ids() -> frozenset[str]:
    rows = re.findall(r"^\| (I\d+) \|", VALIDATION.read_text(encoding="utf-8"), re.MULTILINE)
    return frozenset(rows)


def test_every_case_has_a_clause_and_a_class() -> None:
    # The parser rejects a case without them; this guards against an empty fixture set.
    assert len(CASES) >= 400
    assert all(case.clause for case in CASES)
    assert {case.case_class for case in CASES} >= {
        CaseClass.NORMATIVE,
        CaseClass.INTERPRETATION,
        CaseClass.IMPLEMENTATION_DEFINED,
    }


def test_interpretation_ids_exist_in_the_validation_document() -> None:
    known = _interpretation_ids()
    assert {"I1", "I8"} <= known
    for case in CASES:
        if case.case_class is CaseClass.INTERPRETATION:
            assert case.interpretations, f"{case.file}::{case.name}"
            assert set(case.interpretations) <= known, f"{case.file}::{case.name}"
        else:
            assert case.interpretations == (), f"{case.file}::{case.name}"


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case.file == "counterexamples.trace"],
    ids=lambda case: case.name,
)
def test_every_counterexample_has_an_origin(case: TraceCase) -> None:
    assert case.origin, f"{case.file}::{case.name}"
