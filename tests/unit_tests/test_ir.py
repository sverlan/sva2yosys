"""Unit contract of the IR's own invariants."""

from __future__ import annotations

import pytest

from sva2yosys.ir.sequence import TRUE, Delay, Repeat


@pytest.mark.parametrize(("minimum", "maximum"), [(-1, 2), (3, 2), (-1, None)])
def test_invalid_delay_ranges_are_rejected(minimum: int, maximum: int | None) -> None:
    with pytest.raises(ValueError, match="delay range"):
        Delay(minimum, maximum)


@pytest.mark.parametrize(("minimum", "maximum"), [(-1, 0), (2, 1)])
def test_invalid_repetition_ranges_are_rejected(minimum: int, maximum: int | None) -> None:
    with pytest.raises(ValueError, match="repetition range"):
        Repeat(TRUE, minimum, maximum)


@pytest.mark.parametrize(("minimum", "maximum"), [(0, 0), (0, None), (2, 5), (3, 3)])
def test_valid_ranges_are_accepted(minimum: int, maximum: int | None) -> None:
    assert Delay(minimum, maximum).minimum == minimum
    assert Repeat(TRUE, minimum, maximum).maximum == maximum


def test_effective_strength() -> None:
    """The default strength: weak in assert/assume, strong in cover (§16.12.2)."""
    from sva2yosys.ir.property import Strength, effective_strength

    assert effective_strength(Strength.DEFAULT, cover=False) is Strength.WEAK
    assert effective_strength(Strength.DEFAULT, cover=True) is Strength.STRONG
    for explicit in (Strength.WEAK, Strength.STRONG):
        assert effective_strength(explicit, cover=False) is explicit
        assert effective_strength(explicit, cover=True) is explicit
