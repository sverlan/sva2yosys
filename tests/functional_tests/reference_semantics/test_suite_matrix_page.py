"""The generated test matrix page is current (CONTRACT.md, "Test matrix page")."""

from __future__ import annotations

import pytest

from support.suite_matrix import PAGE, render


@pytest.mark.extended  # collects the whole suite in a subprocess (several seconds)
def test_committed_page_equals_the_generated_one() -> None:
    assert PAGE.read_text(encoding="utf-8") == render(), (
        "docs/test-matrix.md is stale: run `uv run python tests/support/suite_matrix.py --write`"
    )
