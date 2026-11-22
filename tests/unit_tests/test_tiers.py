"""Tier contract: opt-ins are cumulative and full --slow remains compatible."""

import pytest

from support.tiers import Tier, includes, selected_tier


@pytest.mark.parametrize(
    ("extended", "slow", "expected"),
    [
        (False, False, Tier.FAST),
        (True, False, Tier.EXTENDED),
        (False, True, Tier.SLOW),
        (True, True, Tier.SLOW),
    ],
)
def test_opt_ins_select_the_requested_scope(extended: bool, slow: bool, expected: Tier) -> None:
    assert selected_tier(extended=extended, slow=slow) is expected


@pytest.mark.parametrize(
    ("selected", "required", "expected"),
    [
        (Tier.FAST, Tier.FAST, True),
        (Tier.FAST, Tier.EXTENDED, False),
        (Tier.FAST, Tier.SLOW, False),
        (Tier.EXTENDED, Tier.FAST, True),
        (Tier.EXTENDED, Tier.EXTENDED, True),
        (Tier.EXTENDED, Tier.SLOW, False),
        (Tier.SLOW, Tier.FAST, True),
        (Tier.SLOW, Tier.EXTENDED, True),
        (Tier.SLOW, Tier.SLOW, True),
    ],
)
def test_higher_tiers_include_every_lower_tier(
    selected: Tier, required: Tier, expected: bool
) -> None:
    assert includes(selected, required) is expected
