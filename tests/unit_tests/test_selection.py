"""Selection contract: explicit choice of external tools and corpora (support.selection)."""

from pathlib import Path

import pytest

from support.selection import (
    CORPORA,
    TOOLS,
    Unmet,
    corpus_not_selected_reason,
    is_other_reason,
    parse_selection,
    summarize,
    tier_reason,
    tool_not_selected_reason,
    unavailable,
    unmet,
)
from support.verilator import ENV_BUILD_PATH, ENV_ROOT, ENV_SHELL, ENV_VERILATOR

KNOWN = ("a", "b", "c")


def test_known_resources_are_in_the_documented_order() -> None:
    assert [t.name for t in TOOLS] == [
        "yosys",
        "sby",
        "sby-live",
        "slang",
        "ebmc",
        "spot",
        "verilator",
    ]
    assert [c.name for c in CORPORA] == ["yosys-sva", "ebmc", "sby-examples"]
    assert [t.command for t in TOOLS] == [False, False, True, False, True, False, False]


@pytest.mark.parametrize("text", ["none", "", "  ", " none "])
def test_none_and_blank_select_nothing(text: str) -> None:
    assert parse_selection(text, KNOWN, "--x") == frozenset()


def test_all_selects_every_known_name() -> None:
    assert parse_selection("all", KNOWN, "--x") == frozenset(KNOWN)


def test_names_are_split_trimmed_and_deduplicated() -> None:
    assert parse_selection(" a , c,a ", KNOWN, "--x") == frozenset({"a", "c"})


@pytest.mark.parametrize("text", ["a,,b", ",a", "a,", "a, "])
def test_empty_item_is_an_error(text: str) -> None:
    with pytest.raises(ValueError, match=r"--x.*empty item"):
        parse_selection(text, KNOWN, "--x")


def test_unknown_name_error_names_option_name_and_known_names() -> None:
    with pytest.raises(ValueError) as caught:
        parse_selection("a,foo", KNOWN, "--external-tools")
    message = str(caught.value)
    assert "--external-tools" in message
    assert "'foo'" in message
    assert "a, b, c" in message


@pytest.mark.parametrize("text", ["all,a", "a,all", "none,a", "all,none"])
def test_all_and_none_cannot_be_mixed_with_other_items(text: str) -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        parse_selection(text, KNOWN, "--x")


def test_nothing_selected_is_always_available() -> None:
    assert unavailable([], [], {}, Path()) == []


def test_unset_tool_is_reported_with_its_variable() -> None:
    messages = unavailable(["sby"], [], {}, Path())
    assert len(messages) == 1
    assert "SVA2YOSYS_SBY" in messages[0]
    assert "sby" in messages[0]


def test_tool_naming_a_missing_file_is_reported(tmp_path: Path) -> None:
    environ = {"SVA2YOSYS_YOSYS": str(tmp_path / "missing")}
    assert len(unavailable(["yosys"], [], environ, tmp_path)) == 1


def test_tool_naming_an_existing_file_is_available(tmp_path: Path) -> None:
    exe = tmp_path / "yosys"
    exe.write_text("")
    assert unavailable(["yosys"], [], {"SVA2YOSYS_YOSYS": str(exe)}, tmp_path) == []


def test_command_tool_needs_only_a_non_blank_command() -> None:
    assert unavailable(["sby-live"], [], {"SVA2YOSYS_SBY_LIVE": "wsl -e sby"}, Path()) == []
    assert len(unavailable(["sby-live"], [], {"SVA2YOSYS_SBY_LIVE": "  "}, Path())) == 1


def test_corpus_must_be_a_directory(tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.write_text("")
    environ = {"SVA2YOSYS_YOSYS_SVA_TESTS": str(file)}
    assert len(unavailable([], ["yosys-sva"], environ, tmp_path)) == 1
    assert len(unavailable([], ["yosys-sva"], {}, tmp_path)) == 1
    environ = {"SVA2YOSYS_YOSYS_SVA_TESTS": str(tmp_path)}
    assert unavailable([], ["yosys-sva"], environ, tmp_path) == []


def test_the_sby_examples_corpus_is_configured_by_its_variable(tmp_path: Path) -> None:
    assert unavailable([], ["sby-examples"], {}, tmp_path) != []
    environ = {"SVA2YOSYS_SBY_EXAMPLES": str(tmp_path)}
    assert unavailable([], ["sby-examples"], environ, tmp_path) == []
    assert parse_selection("sby-examples", (c.name for c in CORPORA), "--x") == {"sby-examples"}


def test_relative_corpus_path_is_taken_from_root(tmp_path: Path) -> None:
    (tmp_path / "corpus").mkdir()
    assert unavailable([], ["yosys-sva"], {"SVA2YOSYS_YOSYS_SVA_TESTS": "corpus"}, tmp_path) == []


def test_every_unavailable_resource_gets_its_own_message_in_declaration_order() -> None:
    messages = unavailable(["slang", "yosys"], ["yosys-sva"], {}, Path())
    assert len(messages) == 3
    assert "SVA2YOSYS_YOSYS" in messages[0]
    assert "SVA2YOSYS_SLANG" in messages[1]
    assert "SVA2YOSYS_YOSYS_SVA_TESTS" in messages[2]


def test_skip_reasons_have_stable_prefixes() -> None:
    assert tool_not_selected_reason("sby").startswith("external tool not selected: sby")
    assert "--external-tools=sby" in tool_not_selected_reason("sby")
    assert corpus_not_selected_reason("yosys-sva").startswith(
        "external corpus not selected: yosys-sva"
    )
    assert tier_reason("slow", "--slow").startswith("tier slow: run with --slow")


def test_summary_of_no_unmet_needs_is_empty() -> None:
    assert summarize([]) == []
    assert summarize([Unmet(), Unmet()]) == []


def test_unmet_lists_the_unselected_resources_of_the_fixture_closure() -> None:
    fixtures = ["tmp_path", "yosys_tool", "sby_tool", "yosys_sva_tests"]
    assert unmet(None, fixtures, ["yosys"], []) == Unmet(
        None, frozenset({"sby"}), frozenset({"yosys-sva"})
    )
    assert unmet("slow", fixtures, ["yosys", "sby"], ["yosys-sva"]) == Unmet("slow")
    assert unmet(None, ["tmp_path"], [], []) == Unmet()


def test_summary_counts_each_axis_independently_in_fixed_order() -> None:
    needs = [
        Unmet(tier="slow", tools=frozenset({"slang"})),
        Unmet(tools=frozenset({"yosys", "slang"}), corpora=frozenset({"yosys-sva"})),
        Unmet(tier="extended"),
        Unmet(tier="slow"),
    ]
    lines = summarize(needs, other=2)
    assert len(lines) == 6
    assert lines[0].startswith("extended tier: 1")
    assert lines[1].startswith("slow tier: 2")
    assert lines[2].startswith("external tool yosys: 1")
    assert lines[3].startswith("external tool slang: 2")
    assert lines[4].startswith("external corpus yosys-sva: 1")
    assert lines[5] == "other: 2 tests"


def test_a_tier_skip_does_not_hide_a_missing_tool_and_two_tools_count_twice() -> None:
    # origin: review. The summary counted pytest's one skip reason per test.
    both = unmet("slow", ["yosys_tool", "sby_tool"], [], [])
    lines = summarize([both])
    assert lines == [
        "slow tier: 1 tests (run with --slow)",
        "external tool yosys: 1 tests (--external-tools=yosys or all)",
        "external tool sby: 1 tests (--external-tools=sby or all)",
    ]


def test_is_other_reason_separates_the_three_axes() -> None:
    assert not is_other_reason(tool_not_selected_reason("sby"))
    assert not is_other_reason(tier_reason("slow", "--slow"))
    assert not is_other_reason(corpus_not_selected_reason("yosys-sva"))
    assert is_other_reason("unconditional skip")


def test_every_resource_names_its_fixture() -> None:
    assert [t.fixture for t in TOOLS] == [
        "yosys_tool",
        "sby_tool",
        "sby_live_tool",
        "slang_tool",
        "ebmc_tool",
        "spot_dir",
        "verilator_tool",
    ]
    assert [c.fixture for c in CORPORA] == [
        "yosys_sva_tests",
        "ebmc_tests",
        "sby_examples",
    ]


def test_ebmc_is_a_command_and_spot_a_directory_with_its_executable(tmp_path: Path) -> None:
    assert unavailable(["ebmc"], [], {"SVA2YOSYS_EBMC": "wsl.exe -e /x/ebmc"}, Path()) == []
    assert len(unavailable(["ebmc"], [], {}, Path())) == 1
    environ = {"SVA2YOSYS_LTL2TGBA": str(tmp_path)}
    message = unavailable(["spot"], [], environ, Path())
    assert len(message) == 1
    assert "SVA2YOSYS_LTL2TGBA" in message[0]
    assert "ltl2tgba" in message[0]
    (tmp_path / "ltl2tgba").write_text("x")
    assert unavailable(["spot"], [], environ, Path()) == []
    assert len(unavailable(["spot"], [], {"SVA2YOSYS_LTL2TGBA": str(tmp_path / "no")}, Path())) == 1


def test_unselected_verilator_ignores_all_configuration(tmp_path: Path) -> None:
    assert unavailable([], [], {}, tmp_path) == []


def test_selected_verilator_rejects_missing_companion_configuration(tmp_path: Path) -> None:
    executable = tmp_path / "verilator"
    executable.write_text("")
    message = unavailable(["verilator"], [], {ENV_VERILATOR: str(executable)}, tmp_path)
    assert len(message) == 1
    assert "verilator" in message[0]
    assert ENV_SHELL in message[0]
    assert ENV_ROOT in message[0]
    assert ENV_BUILD_PATH in message[0]


def test_selected_verilator_accepts_valid_explicit_configuration(tmp_path: Path) -> None:
    executable = tmp_path / "verilator"
    shell = tmp_path / "bash"
    executable.write_text("")
    shell.write_text("")
    runtime = tmp_path / "runtime"
    include = runtime / "include"
    include.mkdir(parents=True)
    (include / "verilated.h").write_text("")
    build = tmp_path / "build"
    build.mkdir()
    environ = {
        ENV_VERILATOR: str(executable),
        ENV_SHELL: str(shell),
        ENV_ROOT: str(runtime),
        ENV_BUILD_PATH: str(build),
    }
    assert unavailable(["verilator"], [], environ, tmp_path) == []
