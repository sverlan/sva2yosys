"""Check a release tag before the release workflow publishes anything.

Usage (the release workflow, from the repository root)::

    uv run python tests/support/release_tag.py v0.1.0

Contract of :func:`release_problems`. A release is the tag ``vX.Y.Z``.
The tag is acceptable when:

* it has exactly that shape: ``v``, three numbers without leading zeros and nothing else;
* the ``version`` of ``pyproject.toml`` is ``X.Y.Z``;
* ``CHANGELOG.md`` has a section headed exactly ``## [X.Y.Z]`` (the changelog carries no dates;
  what is not released yet is under ``## [Unreleased]``), and the link line ``[X.Y.Z]: <URL>``
  whose address ends with ``/releases/tag/vX.Y.Z``.

Each unmet condition is one message; no message means the tag may be published. :func:`main` reads
``pyproject.toml`` and ``CHANGELOG.md`` of the current directory, prints the messages and returns 1
when there is one. It writes nothing and runs no tool.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
VERSION = re.compile(r'^version\s*=\s*"([^"]*)"', re.MULTILINE)


def release_problems(tag: str, pyproject: str, changelog: str) -> list[str]:
    """The reasons why ``tag`` must not be published, given the text of ``pyproject.toml`` and of
    ``CHANGELOG.md``; empty when it may."""
    shape = TAG.fullmatch(tag)
    if shape is None:
        return [f"the tag {tag!r} is not of the form vX.Y.Z (three numbers, no leading zero)"]
    number = ".".join(shape.groups())
    problems: list[str] = []
    found = VERSION.search(pyproject)
    if found is None:
        problems.append('pyproject.toml has no version = "X.Y.Z"')
    elif found[1] != number:
        problems.append(
            f"the tag number {number} differs from the version {found[1]} of pyproject.toml"
        )
    section = re.compile(rf"^## \[{re.escape(number)}\]\s*$", re.MULTILINE)
    if section.search(changelog) is None:
        problems.append(
            f"CHANGELOG.md has no section '## [{number}]' for {number} "
            "(unreleased changes are under '## [Unreleased]')"
        )
    link = re.compile(
        rf"^\[{re.escape(number)}\]: \S+/releases/tag/{re.escape(tag)}\s*$", re.MULTILINE
    )
    if link.search(changelog) is None:
        problems.append(f"CHANGELOG.md has no link line '[{number}]: .../releases/tag/{tag}'")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    """Check the tag given as the one argument against the files of the current directory."""
    parser = argparse.ArgumentParser(description="Check a release tag before it is published.")
    parser.add_argument("tag", help="the tag, for example v0.1.0")
    arguments = parser.parse_args(argv)
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    problems = release_problems(arguments.tag, pyproject, changelog)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
