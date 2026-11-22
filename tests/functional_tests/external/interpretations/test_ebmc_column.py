"""The finite-validity observations for I1, checked by calibrated EBMC BMC."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest

from support.ebmc_differential import (
    AGREES,
    Engine,
    Selection,
    case_key,
    ebmc_version,
    expected_verdicts,
    load_all_cases,
    observed_verdict,
    parse_allow_list,
    run_job,
    select,
    traces_digest,
)
from support.toolchain import Tool

AREA = Path(__file__).resolve().parent
DIFFERENTIAL = AREA.parent / "ebmc_differential"
EXPECTATIONS = DIFFERENTIAL / "fixtures" / "expectations.txt"
ALLOW_LIST = DIFFERENTIAL / "fixtures" / "allow_list.txt"
TRACE_DIGEST_HEADER = "traces-sha256"
EBMC_VERSION_HEADER = "ebmc-version"

_headers: dict[str, str] = {}
_classes: dict[tuple[str, str], tuple[str, str]] = {}
for _line in EXPECTATIONS.read_text(encoding="utf-8").splitlines():
    if not _line.strip() or _line.startswith("#"):
        continue
    _key, separator, _value = _line.partition(": ")
    if separator and _key in {TRACE_DIGEST_HEADER, EBMC_VERSION_HEADER}:
        _headers[_key] = _value.strip()
        continue
    _entry = _line.partition(" # ")[0].split(maxsplit=2)
    if len(_entry) == 3:
        _case_engine, _engine, _judgement = _entry
        _kind, _, _detail = _judgement.partition(" ")
        _classes[(_case_engine, _engine)] = (_kind, _detail)

ALLOW = parse_allow_list(ALLOW_LIST.read_text(encoding="utf-8"))
CASES = {case_key(case): case for case in load_all_cases()}


class Pair(NamedTuple):
    label: str
    positive: str
    negative: str
    shape: str


PAIRS = (
    Pair(
        "constants-i1",
        "constants:constant_false_antecedent_letter_never_triggers",
        "constants:constant_false_one_sample_ahead_fails_one_sample_later",
        "bmc-implication",
    ),
    Pair(
        "implication-controls",
        "delays:p05_latest_valid_match",
        "delays:p05_fail_at_last_chance",
        "bmc-implication",
    ),
)


@pytest.fixture(scope="session")
def classified_traces() -> None:
    """Guard every EBMC observation against the classified trace contents."""
    assert traces_digest() == _headers[TRACE_DIGEST_HEADER], (
        "trace files changed since EBMC classification; reclassify before comparing"
    )


@pytest.fixture(scope="session")
def classified_ebmc(
    classified_traces: None,
    ebmc_tool: Tool,
    tmp_path_factory: pytest.TempPathFactory,
) -> Tool:
    """Require the same EBMC build as the differential expectation table."""
    found = ebmc_version(ebmc_tool, tmp_path_factory.mktemp("interpretation-ebmc-version"))
    assert found == _headers[EBMC_VERSION_HEADER], (
        f"EBMC {found} is not the classified {_headers[EBMC_VERSION_HEADER]}: classify again"
    )
    return ebmc_tool


def _selection(key: str, shape_name: str) -> Selection:
    assert key in CASES, f"missing calibrated trace case {key}"
    selections = [
        selection
        for selection in select(CASES[key], ALLOW)
        if selection.engine is Engine.BMC and selection.shape == shape_name
    ]
    assert len(selections) == 1, f"{key} is not selected by {shape_name} for BMC"
    return selections[0]


def _assert_agrees(
    selection: Selection,
    expected: str,
    ebmc: Tool,
    work: Path,
) -> None:
    table_class, detail = _classes[(selection.key, Engine.BMC.value)]
    assert (table_class, detail) == (AGREES, ""), selection.key
    verdicts = expected_verdicts(selection.case, Engine.BMC)
    assert set(verdicts.values()) == {expected}, (selection.key, verdicts)
    result = run_job(ebmc, None, selection.case, Engine.BMC, work)
    assert result.cls == AGREES, (
        f"{selection.key}: expected validity {expected}; got ours={result.ours}, "
        f"EBMC={result.ebmc}, class={result.cls} {result.detail}"
    )
    assert result.ours == f"p={expected}"
    label, _, status = result.ebmc.partition("=")
    assert label == "p"
    assert observed_verdict(selection.case.kind, status) == expected


@pytest.mark.parametrize("pair", PAIRS, ids=lambda pair: pair.label)
def test_i1_finite_validity_matches_ebmc_bmc(
    pair: Pair,
    classified_ebmc: Tool,
    tmp_path: Path,
) -> None:
    """I1: allowed finite BMC cases agree on validity, including opposite controls."""
    positive = _selection(pair.positive, pair.shape)
    negative = _selection(pair.negative, pair.shape)
    positive_verdict = next(iter(expected_verdicts(positive.case, Engine.BMC).values()))
    negative_verdict = next(iter(expected_verdicts(negative.case, Engine.BMC).values()))
    assert positive_verdict in {"pass", "reached"}, pair.positive
    assert negative_verdict in {"fail", "unreached"}, pair.negative
    assert positive_verdict != negative_verdict

    _assert_agrees(positive, positive_verdict, classified_ebmc, tmp_path / "positive")
    _assert_agrees(negative, negative_verdict, classified_ebmc, tmp_path / "negative")
