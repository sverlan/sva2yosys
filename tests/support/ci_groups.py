"""Test support: the groups the CI pipelines run in parallel (the full suite, one job per group).

Test-support module (never imported by ``src/``). Both CI pipelines (the GitHub workflow and the
GitLab pipeline) run the whole suite as one job for each group of :data:`GROUPS`, so that the
hours of one serial run become the time of the longest group.

Command line::

    uv run python tests/support/ci_groups.py --names     # the group names, as a JSON list
    uv run python tests/support/ci_groups.py <group>     # the pytest arguments of a group

Contract.

1. Partition. The groups together collect every test of ``tests`` exactly once. The last group,
   ``rest``, is everything the others do not take (``tests`` with an ``--ignore`` for each
   directory they take), so a new area or file falls into ``rest`` and is never lost.
2. Split. The long areas are cut so that each part fits the 20-minute limit of a local run:
   ``temporal_machine`` into four parts (``test_never_fails.py``, ``test_configuration_machine.py``,
   ``test_until_machine.py`` with ``test_unbounded_machine.py``, and the rest of the area) and
   ``generated_monitors`` into two (``test_generated_monitors.py`` alone, and the rest).
3. Arguments. :func:`pytest_args` returns the path and ``--ignore`` arguments of a group, relative
   to the repository root; an unknown group raises ``KeyError``. The command line prints them
   joined for a shell (``shlex.join``) or, with ``--names``, the group names as a JSON list; an
   unknown group is a usage error (exit code 2).
"""

from __future__ import annotations

import json
import shlex
import sys

_TEMPORAL = "tests/functional_tests/temporal_machine"
_GENERATED = "tests/functional_tests/generated_monitors"
_UNIT = "tests/unit_tests"
_EXTERNAL = "tests/functional_tests/external"

_NEVER_FAILS = f"{_TEMPORAL}/test_never_fails.py"
_CONFIGURATION = f"{_TEMPORAL}/test_configuration_machine.py"
_UNTIL = f"{_TEMPORAL}/test_until_machine.py"
_UNBOUNDED = f"{_TEMPORAL}/test_unbounded_machine.py"
_GENERATED_MAIN = f"{_GENERATED}/test_generated_monitors.py"

GROUPS: dict[str, tuple[str, ...]] = {
    "unit": (_UNIT,),
    "temporal-never-fails": (_NEVER_FAILS,),
    "temporal-configuration": (_CONFIGURATION,),
    "temporal-until-unbounded": (_UNTIL, _UNBOUNDED),
    "temporal-rest": (
        _TEMPORAL,
        f"--ignore={_NEVER_FAILS}",
        f"--ignore={_CONFIGURATION}",
        f"--ignore={_UNTIL}",
        f"--ignore={_UNBOUNDED}",
    ),
    "generated-monitors-main": (_GENERATED_MAIN,),
    "generated-monitors-rest": (_GENERATED, f"--ignore={_GENERATED_MAIN}"),
    "external": (_EXTERNAL,),
    "rest": (
        "tests",
        f"--ignore={_UNIT}",
        f"--ignore={_TEMPORAL}",
        f"--ignore={_GENERATED}",
        f"--ignore={_EXTERNAL}",
    ),
}
"""The pytest arguments of each group, in the order the groups are listed."""


def names() -> list[str]:
    """The group names, in the order of :data:`GROUPS`."""
    return list(GROUPS)


def pytest_args(group: str) -> tuple[str, ...]:
    """The path and ``--ignore`` arguments of ``group``; ``KeyError`` for an unknown name."""
    return GROUPS[group]


def main(argv: list[str]) -> int:
    """Print the group names (``--names``) or the arguments of the one group named."""
    if argv == ["--names"]:
        print(json.dumps(names()))
        return 0
    if len(argv) == 1 and argv[0] in GROUPS:
        print(shlex.join(pytest_args(argv[0])))
        return 0
    print(f"usage: ci_groups.py --names | {' | '.join(GROUPS)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
