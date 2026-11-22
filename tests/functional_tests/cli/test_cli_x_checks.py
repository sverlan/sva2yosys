"""X-checks on the command line (CONTRACT.md, point 15)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sva2yosys.cli import main
from sva2yosys.rewrite import MONITORS_FILE

FIXTURES = Path(__file__).resolve().parent / "fixtures"
XCHECKS = FIXTURES / "xchecks.sv"
SKIPPED = ["a_known", "a_valid_known", "m_known", "c_unknown", "a_trigger"]


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    status = main(list(args))
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def _line_of(name: str) -> int:
    for number, text in enumerate(XCHECKS.read_text().splitlines(), start=1):
        if text.strip().startswith(f"{name}:"):
            return number
    raise AssertionError(name)


def test_x_checks_are_skipped_and_counted_apart(capsys: pytest.CaptureFixture[str]) -> None:
    """15."""
    status, out, _ = _run(capsys, str(XCHECKS))
    lines = out.splitlines()
    assert status == 0
    skipped = [line for line in lines if line.startswith("skipped")]
    assert len(skipped) == 5
    for name in SKIPPED:
        (line,) = [s for s in skipped if f"xchecks.{name}:" in s]
        assert line.startswith(f"skipped   {XCHECKS}:{_line_of(name)}:")
        assert "X-check:" in line
    assert any("m_known" in s and "constrains nothing" in s for s in skipped)
    assert any("c_unknown" in s and "can never succeed" in s for s in skipped)
    assert any("a_known" in s and "can never fail" in s for s in skipped)
    ok = [line for line in lines if line.startswith("ok")]
    assert [("a_mixed" in line, "a_real" in line) for line in ok] == [(True, False), (False, True)]
    assert lines[-1] == "7 assertion(s): 2 accepted, 5 skipped (X-check), 0 rejected"


def test_the_warning_follows_the_directive_it_belongs_to(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """15."""
    _, out, _ = _run(capsys, str(XCHECKS))
    lines = out.splitlines()
    (index,) = [i for i, line in enumerate(lines) if line.startswith("ok") and "a_mixed" in line]
    warning = lines[index + 1]
    text = XCHECKS.read_text().splitlines()[_line_of("a_mixed") - 1]
    column = text.index("$isunknown") + 1
    assert warning.startswith(f"warning   {XCHECKS}:{_line_of('a_mixed')}:{column}  ")
    assert "xchecks.a_mixed:" in warning
    assert "$isunknown" in warning and "dropped" in warning
    assert sum(line.startswith("warning") for line in lines) == 1  # none for a_real


def test_x_checks_skip_is_the_default(capsys: pytest.CaptureFixture[str]) -> None:
    """15."""
    default = _run(capsys, str(XCHECKS))
    explicit = _run(capsys, str(XCHECKS), "--x-checks=skip")
    assert default == explicit


def test_reject_restores_the_rejection(capsys: pytest.CaptureFixture[str]) -> None:
    """15."""
    status, out, _ = _run(capsys, str(XCHECKS), "--x-checks=reject")
    lines = out.splitlines()
    assert status == 1
    assert not [line for line in lines if line.startswith(("skipped", "warning"))]
    rejected = [line for line in lines if line.startswith("rejected")]
    assert len(rejected) == 6
    (line,) = [r for r in rejected if "xchecks.a_known:" in r]
    text = XCHECKS.read_text().splitlines()[_line_of("a_known") - 1]
    assert line.startswith(
        f"rejected  {XCHECKS}:{_line_of('a_known')}:{text.index('$isunknown') + 1}"
    )
    assert "$isunknown" in line and "reject" in line
    (ok,) = [line for line in lines if line.startswith("ok")]
    assert "xchecks.a_real" in ok
    assert lines[-1] == "7 assertion(s): 1 accepted, 6 rejected"


@pytest.mark.parametrize("option", [[], ["--x-checks=skip"], ["--x-checks=reject"]])
def test_an_operand_using_past_is_rejected_under_both_policies(
    capsys: pytest.CaptureFixture[str], option: list[str]
) -> None:
    """15."""
    source = FIXTURES / "xchecks_past.sv"
    status, out, _ = _run(capsys, str(source), *option)
    lines = out.splitlines()
    assert status == 1
    text = source.read_text().splitlines()[2]
    (rejected,) = [line for line in lines if "xchecks_past.a_past:" in line]
    assert rejected.startswith(f"rejected  {source}:3:{text.index('$isunknown') + 1}")
    assert "$isunknown of an operand using $past" in rejected


def test_an_unknown_policy_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """15."""
    with pytest.raises(SystemExit) as exit_info:
        main([str(XCHECKS), "--x-checks=maybe"])
    assert exit_info.value.code == 2
    assert "--x-checks" in capsys.readouterr().err


def test_help_describes_the_option(capsys: pytest.CaptureFixture[str]) -> None:
    """15."""
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "--x-checks" in out and "skipped (X-check)" in out


def test_generation_removes_the_skipped_directives(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """15: no monitor for a skipped directive; notes, the warning and the count."""
    status, out, _ = _run(capsys, str(XCHECKS), "-o", str(tmp_path))
    assert status == 0
    lines = out.splitlines()
    notes = [line for line in lines if ": note: " in line]
    assert len(notes) == 5 and all("skipped (X-check)" in note for note in notes)
    warnings = [line for line in lines if ": warning: " in line]
    assert len(warnings) == 1 and "dropped" in warnings[0]
    assert lines[-1] == (
        "1 verification file(s) rewritten, 0 design copy(ies), 1 warning(s), 5 skipped X-check(s)"
    )
    written = (tmp_path / "xchecks.sv").read_text()
    assert written.count("sva2yosys: skipped (X-check)") == 5
    code = re.sub(r"/\*.*?\*/", "", written)  # the comments quote the assertions as written
    assert "assert property" not in code and "cover property" not in code
    monitors = (tmp_path / MONITORS_FILE).read_text()
    assert monitors.count("module __sva2yosys_p") == 2  # a_mixed and a_real


def test_generation_without_x_checks_prints_the_summary_of_before(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """15."""
    _, out, _ = _run(capsys, str(FIXTURES / "accepted.sv"), "-o", str(tmp_path))
    assert out.splitlines()[-1] == (
        "1 verification file(s) rewritten, 0 design copy(ies), 0 warning(s)"
    )


def test_generation_with_reject_is_refused(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """15."""
    status, _, err = _run(capsys, str(XCHECKS), "-o", str(tmp_path), "--x-checks=reject")
    assert status == 1
    assert "$isunknown" in err
    assert not (tmp_path / MONITORS_FILE).exists()
