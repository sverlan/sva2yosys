"""Repository test tiers, independent of pytest collection.

FAST always runs; EXTENDED adds broader searches/tool checks; SLOW adds the full
stress suite. Selecting SLOW includes both lower tiers. This changes scheduling,
never the assertions or example counts of the larger searches.
"""

from enum import Enum, auto


class Tier(Enum):
    """Increasing test scope, with FAST selected when no opt-in is given."""

    FAST = auto()
    EXTENDED = auto()
    SLOW = auto()


def selected_tier(*, extended: bool, slow: bool) -> Tier:
    """Select FAST by default; --slow wins when both opt-ins are supplied."""
    if slow:
        return Tier.SLOW
    return Tier.EXTENDED if extended else Tier.FAST


def includes(selected: Tier, required: Tier) -> bool:
    """Whether the selected scope includes the test's required scope."""
    match selected:
        case Tier.FAST:
            return required is Tier.FAST
        case Tier.EXTENDED:
            return required is not Tier.SLOW
        case Tier.SLOW:
            return True
