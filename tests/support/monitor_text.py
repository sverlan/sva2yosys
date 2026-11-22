"""Helpers on the text of generated monitors.

Contract:
- :func:`without_wire_comments` removes the ``  // <expression>`` comment that ends the ``assign``
  line of every condition wire ``e<N>`` (generated_monitors contract point 26) and nothing else.
  The historical digests of monitor text (recorded before those comments existed) are taken over
  this form, so they keep proving that the logic of every monitor is unchanged.
"""

from __future__ import annotations

import re

_WIRE_COMMENT = re.compile(r"^(    assign e\d+ = [^\n]*;)  // [^\n]*$", re.MULTILINE)


def without_wire_comments(text: str) -> str:
    """``text`` with the trailing comment of every condition-wire ``assign`` line removed."""
    return _WIRE_COMMENT.sub(r"\1", text)
