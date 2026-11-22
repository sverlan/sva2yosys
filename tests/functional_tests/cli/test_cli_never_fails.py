"""Consequents that can never fail, on the command line (CONTRACT.md, point 16)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sva2yosys.cli import main
from sva2yosys.rewrite import MONITORS_FILE

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE = FIXTURES / "never_fails.sv"
WARNED = ["a_live", "m_live", "a_prop", "a_const", "a_next", "a_and"]
NOT_WARNED = ["a_rep", "a_until", "a_tail", "c_cover", "a_bounded"]
REASON = "its weak unbounded consequent always keeps a live match"


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    status = main(list(args))
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def _line_of(name: str) -> int:
    for number, text in enumerate(SOURCE.read_text().splitlines(), start=1):
        if text.strip().startswith(f"{name}:"):
            return number
    raise AssertionError(name)


def test_exactly_the_directives_that_cannot_fail_are_warned_about(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """16."""
    status, out, _ = _run(capsys, str(SOURCE))
    warnings = [line for line in out.splitlines() if line.startswith("warning")]
    assert status == 0
    assert sorted(
        name for name in WARNED + NOT_WARNED if any(f"never_fails.{name}:" in w for w in warnings)
    ) == sorted(WARNED)
    assert len(warnings) == len(WARNED)


def test_the_warning_follows_the_ok_line_and_is_located_at_the_directive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """16."""
    _, out, _ = _run(capsys, str(SOURCE))
    lines = out.splitlines()
    for name in WARNED:
        (index,) = [
            i for i, line in enumerate(lines) if line.startswith("ok") and f".{name} " in line
        ]
        warning = lines[index + 1]
        text = SOURCE.read_text().splitlines()[_line_of(name) - 1]
        column = text.index(f"{name}:") + 1
        assert warning.startswith(f"warning   {SOURCE}:{_line_of(name)}:{column}  ")
        assert f"never_fails.{name}:" in warning
        assert "configurations" in lines[index]


def test_assert_and_assume_wording(capsys: pytest.CaptureFixture[str]) -> None:
    """16."""
    _, out, _ = _run(capsys, str(SOURCE))
    lines = out.splitlines()
    (assert_line,) = [w for w in lines if w.startswith("warning") and "never_fails.a_live:" in w]
    (assume_line,) = [w for w in lines if w.startswith("warning") and "never_fails.m_live:" in w]
    assert assert_line.endswith(
        f"never_fails.a_live: this assert can never fail: {REASON} "
        "(a liveness check needs strong(...) or s_eventually, see docs/usage.md)"
    )
    assert "this assume can never fail, so it constrains nothing: " + REASON in assume_line
    # (a strong assume exists now, so the hint points at it)
    assert assume_line.endswith("(a fairness constraint needs a strong assume, see docs/usage.md)")
    (prop_line,) = [w for w in lines if w.startswith("warning") and "never_fails.a_prop:" in w]
    assert "its weak unbounded sequence always keeps a live match" in prop_line


def test_a_conjunction_names_the_conjunct_that_cannot_fail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """16."""
    _, out, _ = _run(capsys, str(SOURCE))
    (line,) = [w for w in out.splitlines() if w.startswith("warning") and "never_fails.a_and:" in w]
    assert "conjunct 2 of 2 of this assert can never fail: " + REASON in line


def test_matching_discharges_are_not_failures_so_no_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """16: b[*1:$], b until c, b ##1 (c ##[1:$] d), a cover and a bounded range."""
    _, out, _ = _run(capsys, str(SOURCE))
    for name in NOT_WARNED:
        assert f"ok        never_fails.{name} " in out
        assert not [
            line for line in out.splitlines() if line.startswith("warning") and f".{name}:" in line
        ]


def test_summary_and_exit_status_are_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    """16: warned directives are accepted."""
    status, out, _ = _run(capsys, str(SOURCE))
    assert status == 0
    assert out.splitlines()[-1] == "11 assertion(s): 11 accepted, 0 rejected"
    assert sum(line.startswith("ok") for line in out.splitlines()) == 11


def test_generation_prints_the_warnings_and_counts_them(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """16: with -o, `<file>:<line>:<col>: warning: ...` after the files, once per site."""
    status, out, _ = _run(capsys, str(SOURCE), "-o", str(tmp_path / "out"), "--top", "never_fails")
    lines = out.splitlines()
    warnings = [line for line in lines if ": warning: " in line]
    assert status == 0
    assert len(warnings) == len(WARNED)
    assert lines[-1].endswith(f"{len(WARNED)} warning(s)")
    assert lines.index(warnings[0]) > max(
        i for i, line in enumerate(lines) if line.startswith("wrote")
    )
    for name in WARNED:
        (line,) = [w for w in warnings if f"never_fails.{name}:" in w]
        assert line.startswith(f"{SOURCE}:{_line_of(name)}:")
    assert (tmp_path / "out" / MONITORS_FILE).exists()
