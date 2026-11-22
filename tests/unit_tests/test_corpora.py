"""Contract of support.corpora: manifest shape and the planned git commands (no network, no git)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from support import corpora
from support.corpora import CORPORA, Corpus, dirty_check_command, env_line, plan_commands, select

DEST = Path("dest")


def _entry(sparse: tuple[str, ...]) -> Corpus:
    return Corpus("x", "https://example.org/x.git", "a" * 40, sparse, "ISC", "test")


def test_manifest_names_are_unique() -> None:
    names = [c.name for c in CORPORA]
    assert len(names) == len(set(names))


def test_manifest_urls_are_https() -> None:
    assert all(c.url.startswith("https://") for c in CORPORA)


def test_manifest_commits_are_full_hashes() -> None:
    assert all(re.fullmatch(r"[0-9a-f]{40}", c.commit) for c in CORPORA)


def test_manifest_has_yosys_sparse_on_tests_sva() -> None:
    yosys = select(["yosys"])[0]
    assert yosys.sparse_paths == ("tests/sva",)


def test_manifest_has_ebmc_sparse_on_the_sva_regression_tests() -> None:
    ebmc = select(["ebmc"])[0]
    assert ebmc.sparse_paths == ("regression/verilog/SVA",)
    assert ebmc.commit == "2a3263c66d042364769353ea0b068ea1f5eec54e"
    assert ebmc.license_note.startswith("BSD-3-Clause")


def test_manifest_has_sby_sparse_on_examples_and_tests() -> None:
    sby = select(["sby"])[0]
    assert sby.sparse_paths == ("docs/examples", "tests")
    assert sby.commit == "79fc9eddca5e33a28cc2f2cd19c9825efd9a7255"
    assert sby.license_note.startswith("ISC")


def test_assertionbench_entry_records_licence_situation_and_survey_only_purpose() -> None:
    entry = select(["assertion_data_for_LLM"])[0]
    assert "no repository licence" in entry.license_note
    assert "2406.18627" in entry.license_note
    assert "never used" in entry.license_note
    assert "acceptance survey only" in entry.purpose


def test_manifest_documents_every_entry() -> None:
    assert all(c.license_note and c.purpose for c in CORPORA)


def test_select_all_and_unknown() -> None:
    assert select([]) == CORPORA
    with pytest.raises(ValueError, match="nope"):
        select(["nope"])


def test_new_pinned_clone_is_partial_and_checks_out_commit() -> None:
    commands = plan_commands(_entry(()), DEST, latest=False, exists=False, update=False)
    target = str(DEST / "x")
    assert commands == (
        ("clone", "--filter=blob:none", "--no-checkout", "https://example.org/x.git", target),
        ("-C", target, "checkout", "--detach", "a" * 40),
    )


def test_new_sparse_clone_sets_cone_paths_before_checkout() -> None:
    commands = plan_commands(_entry(("p/q", "r")), DEST, latest=False, exists=False, update=False)
    target = str(DEST / "x")
    assert commands[1] == ("-C", target, "sparse-checkout", "set", "--cone", "p/q", "r")
    assert commands[2][2:] == ("checkout", "--detach", "a" * 40)
    assert len(commands) == 3


def test_latest_checks_out_remote_default_head() -> None:
    commands = plan_commands(_entry(()), DEST, latest=True, exists=False, update=False)
    assert commands[-1][-1] == "origin/HEAD"


def test_existing_clone_is_skipped() -> None:
    assert plan_commands(_entry(("p",)), DEST, latest=False, exists=True, update=False) == ()


def test_update_fetches_then_checks_out_again() -> None:
    commands = plan_commands(_entry(("p",)), DEST, latest=False, exists=True, update=True)
    assert commands[0][2] == "fetch"
    assert commands[-1][2:] == ("checkout", "--detach", "a" * 40)
    assert not any(c[0] == "clone" for c in commands)


def test_no_command_weakens_certificate_checks() -> None:
    for corpus in CORPORA:
        for latest in (False, True):
            for command in plan_commands(corpus, DEST, latest=latest, exists=False, update=False):
                text = " ".join(command).lower()
                assert "sslverify" not in text
                assert "-c" not in command
                assert "insecure" not in text


def test_env_line_points_to_yosys_tests_sva() -> None:
    line = env_line(DEST)
    assert line.startswith("SVA2YOSYS_YOSYS_SVA_TESTS='")
    assert line.endswith("/yosys/tests/sva'")
    assert "\\" not in line


def test_update_fetches_pinned_sha_explicitly() -> None:
    commands = plan_commands(_entry(()), DEST, latest=False, exists=True, update=True)
    assert ("-C", str(DEST / "x"), "fetch", "--filter=blob:none", "origin", "a" * 40) in commands
    assert commands.index(commands[1]) < len(commands) - 1  # fetched before the checkout


def test_latest_update_refreshes_remote_head_before_checkout() -> None:
    commands = plan_commands(_entry(()), DEST, latest=True, exists=True, update=True)
    head = ("-C", str(DEST / "x"), "remote", "set-head", "origin", "--auto")
    assert head in commands
    assert commands.index(head) < len(commands) - 1
    assert commands[-1][-1] == "origin/HEAD"


def test_dirty_check_is_porcelain_status_of_the_clone() -> None:
    command = dirty_check_command(_entry(()), DEST)
    # explicit, so a status.showUntrackedFiles=no configuration cannot hide untracked files
    assert command == ("-C", str(DEST / "x"), "status", "--porcelain", "--untracked-files=all")


def test_env_line_rejects_a_single_quote_in_the_path() -> None:
    with pytest.raises(ValueError, match="single quote"):
        env_line(Path("C:/Users/O'Neil/sva-corpora"))


def test_options_may_come_between_positionals(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, ...]] = []

    class Done:
        returncode = 0
        stdout = ""

    def fake_run(cmd: list[str], **_: object) -> Done:
        seen.append(tuple(cmd))
        return Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert corpora.main(["dest", "--latest", "--update", "yosys"]) == 0
    assert any("clone" in c for c in seen)


def test_update_refuses_dirty_clone(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, ...]] = []

    class Dirty:
        returncode = 0
        stdout = " M file"

    def fake_run(cmd: list[str], **_: object) -> Dirty:
        seen.append(tuple(cmd))
        return Dirty()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: None)
    monkeypatch.setattr(Path, "exists", lambda self: True)
    assert corpora.main(["dest", "yosys", "--update"]) == 1
    assert all("checkout" not in c and "fetch" not in c for c in seen)
