"""Contract point 23: the EBMC design text is unchanged by the trace_driver extraction."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from support.ebmc_differential import Engine, design_source, load_all_cases
from support.tracefile import TraceCase

DIGESTS = Path(__file__).parent / "fixtures" / "ebmc_design_digests.txt"


def _expected() -> dict[tuple[str, str], str]:
    table: dict[tuple[str, str], str] = {}
    for line in DIGESTS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, engine, digest = line.split(" ")
            table[(key, engine)] = digest
    return table


def _key(case: TraceCase) -> str:
    return f"{Path(case.file).stem}:{case.name}"


def _engines(case: TraceCase) -> list[Engine]:
    return [Engine.BMC] + ([Engine.BDD, Engine.BUECHI] if case.loop is not None else [])


def test_ebmc_designs_are_byte_identical_to_the_pre_extraction_text() -> None:
    expected = _expected()
    seen: set[tuple[str, str]] = set()
    changed: list[str] = []
    for case in load_all_cases():
        for engine in _engines(case):
            key = (_key(case), engine.name)
            seen.add(key)
            digest = hashlib.sha256(design_source(case, engine).encode()).hexdigest()
            if expected.get(key) != digest:
                changed.append(f"{key[0]} {key[1]}")
    assert not changed, f"design text changed for {len(changed)} designs, e.g. {changed[:5]}"
    assert seen == set(expected), "the digest fixture and the trace tables list different designs"


@pytest.mark.parametrize(
    "name", ["counter_lines", "phase_lines", "stimulus_lines", "directive_line"]
)
def test_the_ebmc_design_is_built_from_the_shared_driver(name: str) -> None:
    import support.trace_driver as driver

    assert callable(getattr(driver, name))
    source = Path(design_source.__code__.co_filename).read_text(encoding="utf-8")
    assert name in source, f"ebmc_differential does not use {name}"
