"""Run the guide's compiler workflow against canonical example sources."""

from __future__ import annotations

import re
import shlex
import shutil
from pathlib import Path

import pytest

from sva2yosys.cli import main

ROOT = Path(__file__).resolve().parents[3]


def test_getting_started_commands_check_generate_bug_and_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Relative guide inputs work and the final generation restores normal RTL."""
    document = (ROOT / "docs" / "getting-started.md").read_text(encoding="utf-8")
    blocks = re.findall(r"<!-- getting-started-cli -->\s*```text\n(.*?)\n```", document, re.DOTALL)
    commands = [shlex.split(line) for block in blocks for line in block.splitlines()]
    assert len(commands) == 4
    work = tmp_path / "pulse"
    shutil.copytree(ROOT / "examples" / "pulse", work, ignore=shutil.ignore_patterns("build"))
    monkeypatch.chdir(work)

    for index, command in enumerate(commands):
        assert command[0] == "sva2yosys"
        assert main(command[1:]) == 0
        captured = capsys.readouterr()
        assert not captured.err, captured.err
        read_script = work / "build" / "sva2yosys_read.ys"
        if index == 0:
            assert "4 assertion(s): 4 accepted, 0 rejected" in captured.out
            assert not read_script.exists()
        else:
            text = read_script.read_text(encoding="utf-8")
            assert ("EXAMPLE_BUG" in text) is (index == 2)
