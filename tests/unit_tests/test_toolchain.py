"""Unit contract of the test-support toolchain helper (its docstrings are the contract)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

import pytest

from support.toolchain import (
    CheckHit,
    SbyStatus,
    Tool,
    ToolError,
    directory_from_env,
    parse_sby_log,
    path_for_tool,
    run_sby,
    run_slang_ast_json,
    run_tool,
    run_yosys,
    tool_directory_from_env,
    tool_from_env,
)

# --- directory_from_env ----------------------------------------------------------


@pytest.mark.parametrize(
    "prefix",
    [
        ("wsl.exe", "-e"),
        ("wsl", "--exec"),
        ("wsl", "--distribution", "Ubuntu", "-e"),
        ("wsl",),
    ],
)
def test_wsl_path_prefix_is_passed_inside_linux_without_interpolating_arguments(
    tmp_path: Path, prefix: tuple[str, ...]
) -> None:
    """Review counterexample: imported Windows PATH follows Linux's default PATH."""
    tool = Tool("V", (*prefix, "/opt/ebmc"))
    command = [*tool.command, "a b.sv", "$(literal)"]
    with patch(
        "support.toolchain.subprocess.run",
        return_value=subprocess.CompletedProcess(command, 0, "", ""),
    ) as invoked:
        run_tool(tool, command, tmp_path, 10, "probe", path_prefix="C:/spot's $directory")
    args = invoked.call_args.args[0]
    launcher = list(prefix) if len(prefix) > 1 else [*prefix, "--exec"]
    assert args[: len(launcher)] == launcher
    assert args[len(launcher) : len(launcher) + 2] == ["/bin/sh", "-c"]
    assert "/mnt/c/spot's $directory" in args
    assert args[-3:] == ["/opt/ebmc", "a b.sv", "$(literal)"]
    # Neither the directory nor the original arguments are interpolated into shell code.
    script = args[len(launcher) + 2]
    assert "$directory" not in script
    assert "$(literal)" not in script


def test_wsl_launch_options_need_an_explicit_command_boundary(tmp_path: Path) -> None:
    tool = Tool("V", ("wsl", "--distribution", "Ubuntu", "/opt/ebmc"))
    with pytest.raises(ToolError, match="require -e or --exec"):
        run_tool(tool, tool.command, tmp_path, 10, "probe", path_prefix="C:/spot")


def test_unset_or_blank_variable_gives_no_directory(tmp_path: Path) -> None:
    assert directory_from_env("V", {}, tmp_path) is None
    assert directory_from_env("V", {"V": "  "}, tmp_path) is None


def test_missing_directory_or_file_gives_no_directory(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("")
    assert directory_from_env("V", {"V": str(tmp_path / "missing")}, tmp_path) is None
    assert directory_from_env("V", {"V": "f"}, tmp_path) is None


def test_absolute_directory_is_returned(tmp_path: Path) -> None:
    other = tmp_path / "corpus"
    other.mkdir()
    assert directory_from_env("V", {"V": f" {other} "}, tmp_path / "elsewhere") == other


def test_relative_directory_is_taken_from_the_base(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    assert directory_from_env("V", {"V": "a/b"}, tmp_path) == tmp_path / "a" / "b"


def test_tool_directory_needs_the_executable_inside(tmp_path: Path) -> None:
    assert tool_directory_from_env("V", {"V": str(tmp_path)}, tmp_path, "ltl2tgba") is None
    assert tool_directory_from_env("V", {}, tmp_path, "ltl2tgba") is None
    (tmp_path / "ltl2tgba").write_text("x")
    assert tool_directory_from_env("V", {"V": str(tmp_path)}, tmp_path, "ltl2tgba") == tmp_path
    assert tool_directory_from_env("V", {"V": "."}, tmp_path, "ltl2tgba") == tmp_path / "."


# --- tool_from_env ---------------------------------------------------------------


def _executable(tmp_path: Path) -> Path:
    exe = tmp_path / "suite" / "bin" / "yosys.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    return exe


def test_unset_variable_gives_no_tool() -> None:
    assert tool_from_env("SVA2YOSYS_X", {}) is None


def test_blank_variable_gives_no_tool() -> None:
    assert tool_from_env("SVA2YOSYS_X", {"SVA2YOSYS_X": "   "}) is None


def test_missing_executable_gives_no_tool(tmp_path: Path) -> None:
    env = {"SVA2YOSYS_X": str(tmp_path / "nope.exe")}
    assert tool_from_env("SVA2YOSYS_X", env) is None


def test_directory_is_not_an_executable(tmp_path: Path) -> None:
    assert tool_from_env("SVA2YOSYS_X", {"SVA2YOSYS_X": str(tmp_path)}) is None


def test_existing_executable_gives_tool(tmp_path: Path) -> None:
    exe = _executable(tmp_path)
    tool = tool_from_env("SVA2YOSYS_X", {"SVA2YOSYS_X": f"  {exe}  "})
    assert tool == Tool(variable="SVA2YOSYS_X", command=(str(exe),))


def test_executable_path_may_contain_spaces(tmp_path: Path) -> None:
    exe = tmp_path / "Program Files" / "sby.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    tool = tool_from_env("SVA2YOSYS_X", {"SVA2YOSYS_X": str(exe)})
    assert tool is not None
    assert tool.command == (str(exe),)


def test_command_is_split_on_whitespace_and_not_checked() -> None:
    env = {"SVA2YOSYS_X": "wsl  -e /mnt/c/tools/bin/sby"}
    tool = tool_from_env("SVA2YOSYS_X", env, command=True)
    assert tool == Tool(variable="SVA2YOSYS_X", command=("wsl", "-e", "/mnt/c/tools/bin/sby"))


def test_blank_command_gives_no_tool() -> None:
    assert tool_from_env("SVA2YOSYS_X", {"SVA2YOSYS_X": ""}, command=True) is None


# --- Tool.subprocess_env -----------------------------------------------------------


def test_bin_and_lib_are_prepended_to_path(tmp_path: Path) -> None:
    exe = _executable(tmp_path)
    lib = exe.parent.parent / "lib"
    lib.mkdir()
    base = {"PATH": "orig"}
    env = Tool("V", (str(exe),)).subprocess_env(base)
    assert env["PATH"] == os.pathsep.join([str(exe.parent), str(lib), "orig"])
    assert base == {"PATH": "orig"}


def test_missing_lib_is_not_added(tmp_path: Path) -> None:
    exe = _executable(tmp_path)
    env = Tool("V", (str(exe),)).subprocess_env({"PATH": "orig"})
    assert env["PATH"] == os.pathsep.join([str(exe.parent), "orig"])


def test_path_key_spelling_is_kept(tmp_path: Path) -> None:
    exe = _executable(tmp_path)
    env = Tool("V", (str(exe),)).subprocess_env({"Path": "orig", "OTHER": "1"})
    assert set(env) == {"Path", "OTHER"}
    assert env["Path"].endswith("orig")
    assert env["OTHER"] == "1"


def test_path_is_created_when_absent(tmp_path: Path) -> None:
    exe = _executable(tmp_path)
    env = Tool("V", (str(exe),)).subprocess_env({})
    assert env["PATH"] == str(exe.parent)


def test_bare_command_leaves_path_unchanged() -> None:
    env = Tool("V", ("wsl", "-e", "sby")).subprocess_env({"PATH": "orig"})
    assert env == {"PATH": "orig"}


# --- parse_sby_log -----------------------------------------------------------------

_PREFIX = "SBY 13:41:34 [C:/tmp/work dir [x]]"

_FAIL_LOG = f"""\
{_PREFIX} engine_0: ##   0:00:00  Checking assertions in step 3..
{_PREFIX} summary: engine_0 (smtbmc yices) returned FAIL
{_PREFIX} summary: counterexample trace: C:\\tmp\\work\\engine_0\\trace.vcd
{_PREFIX} summary:   failed assertion top.u_mon.check_assert_19 at m.sv:19.25-19.39 step 3
{_PREFIX} DONE (FAIL, rc=2)
"""

_COVER_LOG = f"""\
{_PREFIX} summary: engine_0 (smtbmc yices) returned PASS
{_PREFIX} summary:   reached cover statement top.c1 at m.sv:21.25-21.45 step 3
{_PREFIX} summary:   reached cover statement top.c2 at m.sv:22.1-22.9 step 7
{_PREFIX} DONE (PASS, rc=0)
"""

_UNREACHED_LOG = f"""\
{_PREFIX} summary: engine_0 (smtbmc yices) returned FAIL
{_PREFIX} summary: engine_0 did not produce any traces
{_PREFIX} summary: unreached cover statements:
{_PREFIX} summary:   top.c1 at m.sv:21.25-21.45
{_PREFIX} summary:   top.c2 at m.sv:22.1-22.9
{_PREFIX} DONE (FAIL, rc=2)
"""


def test_failed_assertion_with_step() -> None:
    result = parse_sby_log(_FAIL_LOG)
    assert result.status is SbyStatus.FAIL
    assert result.failed_assertions == (
        CheckHit("top.u_mon.check_assert_19", "m.sv:19.25-19.39", 3),
    )
    assert result.reached_covers == ()
    assert result.unreached_covers == ()
    assert result.log == _FAIL_LOG


def test_reached_covers_in_log_order() -> None:
    result = parse_sby_log(_COVER_LOG)
    assert result.status is SbyStatus.PASS
    assert result.reached_covers == (
        CheckHit("top.c1", "m.sv:21.25-21.45", 3),
        CheckHit("top.c2", "m.sv:22.1-22.9", 7),
    )


def test_unreached_covers_have_no_step() -> None:
    result = parse_sby_log(_UNREACHED_LOG)
    assert result.status is SbyStatus.FAIL
    assert result.unreached_covers == (
        CheckHit("top.c1", "m.sv:21.25-21.45", None),
        CheckHit("top.c2", "m.sv:22.1-22.9", None),
    )
    assert result.failed_assertions == ()


@pytest.mark.parametrize("status", ["PASS", "FAIL", "UNKNOWN", "TIMEOUT", "ERROR"])
def test_every_done_status_is_recognised(status: str) -> None:
    assert parse_sby_log(f"{_PREFIX} DONE ({status}, rc=0)\n").status is SbyStatus(status)


def test_names_may_contain_spaces_from_escaped_identifiers() -> None:
    name = r"tb.\g.m "  # escaped identifier: SBY prints it with its terminating space
    log = (
        f"{_PREFIX} summary:   failed assertion {name}._witness_.check_13 at m.sv:16.37-16.52"
        " step 14\n"
        f"{_PREFIX} summary:   reached cover statement {name}.c at m.sv:3.1-3.9 step 2\n"
        f"{_PREFIX} summary: unreached cover statements:\n"
        f"{_PREFIX} summary:   {name}.u at m.sv:4.1-4.9\n"
        f"{_PREFIX} DONE (FAIL, rc=2)\n"
    )
    result = parse_sby_log(log)
    assert result.failed_assertions == (
        CheckHit(f"{name}._witness_.check_13", "m.sv:16.37-16.52", 14),
    )
    assert result.reached_covers == (CheckHit(f"{name}.c", "m.sv:3.1-3.9", 2),)
    assert result.unreached_covers == (CheckHit(f"{name}.u", "m.sv:4.1-4.9", None),)


def test_log_without_done_line_is_an_error() -> None:
    with pytest.raises(ToolError):
        parse_sby_log(f"{_PREFIX} summary: engine_0 (smtbmc yices) returned PASS\n")


# --- run_sby -----------------------------------------------------------------------


def _fake_tool(script: str) -> Tool:
    """A stand-in tool: the Python interpreter running ``script`` (extra args ignored)."""
    return Tool("V", (sys.executable, "-c", script))


def test_sby_output_without_done_line_is_a_tool_error(tmp_path: Path) -> None:
    sby_file = tmp_path / "x.sby"
    sby_file.write_text("")
    with pytest.raises(ToolError, match="without a DONE line"):
        run_sby(_fake_tool("print('no summary here')"), sby_file, "bmc", tmp_path / "w")


def test_sby_output_is_parsed(tmp_path: Path) -> None:
    sby_file = tmp_path / "x.sby"
    sby_file.write_text("")
    tool = _fake_tool("print('SBY 1:2:3 [job] DONE (PASS, rc=0)')")
    assert run_sby(tool, sby_file, "bmc", tmp_path / "w").status is SbyStatus.PASS


def test_tool_timeout_is_a_tool_error(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="timed out"):
        run_yosys(_fake_tool("import time; time.sleep(30)"), ["stat"], tmp_path, timeout=0.5)


def test_unstartable_sby_is_a_tool_error(tmp_path: Path) -> None:
    sby_file = tmp_path / "x.sby"
    sby_file.write_text("[options]\nmode bmc\n")
    tool = Tool("V", (str(tmp_path / "missing" / "sby.exe"),))
    with pytest.raises(ToolError):
        run_sby(tool, sby_file, "bmc", tmp_path / "work")


# --- run_yosys ---------------------------------------------------------------------


def test_yosys_command_with_semicolon_is_rejected(tmp_path: Path) -> None:
    tool = Tool("V", (str(tmp_path / "missing" / "yosys.exe"),))
    with pytest.raises(ValueError, match=";"):
        run_yosys(tool, ["read_verilog a.v; stat"], tmp_path)


def test_unstartable_yosys_is_a_tool_error(tmp_path: Path) -> None:
    tool = Tool("V", (str(tmp_path / "missing" / "yosys.exe"),))
    with pytest.raises(ToolError):
        run_yosys(tool, ["stat"], tmp_path)


# --- run_slang_ast_json --------------------------------------------------------------


def test_slang_json_is_parsed_from_stdout(tmp_path: Path) -> None:
    tool = _fake_tool("import json; print(json.dumps({'design': {'kind': 'Root'}}))")
    assert run_slang_ast_json(tool, ["a.sv"], tmp_path) == {"design": {"kind": "Root"}}


def test_slang_failure_is_a_tool_error(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="exited with 3"):
        run_slang_ast_json(_fake_tool("raise SystemExit(3)"), ["a.sv"], tmp_path, top=["t"])


def test_slang_invalid_json_is_a_tool_error(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="invalid JSON"):
        run_slang_ast_json(_fake_tool("print('not json')"), ["a.sv"], tmp_path)


# --- path_for_tool -----------------------------------------------------------------


def test_wsl_command_gets_a_mnt_path() -> None:
    tool = Tool("V", ("wsl", "-e", "/opt/sby"))
    assert path_for_tool(tool, PureWindowsPath("C:/Users/a b/w")) == "/mnt/c/Users/a b/w"


def test_wsl_exe_spelling_is_recognised() -> None:
    tool = Tool("V", (r"C:\Windows\System32\WSL.EXE", "-e", "sby"))
    assert path_for_tool(tool, PureWindowsPath("D:/w")) == "/mnt/d/w"


def test_wsl_path_without_drive_is_unchanged() -> None:
    tool = Tool("V", ("wsl", "-e", "sby"))
    assert path_for_tool(tool, "relative/w") == "relative/w"


def test_other_tools_get_the_path_unchanged() -> None:
    tool = Tool("V", ("C:/tools/sby.exe",))
    assert path_for_tool(tool, PureWindowsPath("C:/w")) == str(PureWindowsPath("C:/w"))


def test_a_hit_at_step_zero_has_no_step_in_the_summary() -> None:
    """SBY leaves step 0 out of its summary lines: a reached cover or a failed assertion
    without a step is at step 0."""
    log = (
        f"{_PREFIX} summary:   reached cover statement top.c at m.sv:21.25-21.45\n"
        f"{_PREFIX} summary:   failed assertion top.a at m.sv:19.25-19.39\n"
        f"{_PREFIX} summary:   reached cover statement top.d at m.sv:22.1-22.9 step 4\n"
        f"{_PREFIX} DONE (PASS, rc=0)\n"
    )
    result = parse_sby_log(log)
    assert result.reached_covers == (
        CheckHit("top.c", "m.sv:21.25-21.45", 0),
        CheckHit("top.d", "m.sv:22.1-22.9", 4),
    )
    assert result.failed_assertions == (CheckHit("top.a", "m.sv:19.25-19.39", 0),)
