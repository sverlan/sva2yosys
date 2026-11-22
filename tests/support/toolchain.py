"""Test support: external tool configuration and invocation (Yosys, SymbiYosys).

sva2yosys itself only transforms SystemVerilog into SystemVerilog; these tools are
used by the test suites to validate its output.

Tools are never looked up on PATH: each one is named by an environment variable
(see ``.env.example``). Local paths are kept in a git-ignored ``.env`` file that uv
loads (``uv run --env-file .env ...``); this module only reads the environment.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePath, PureWindowsPath

ENV_YOSYS = "SVA2YOSYS_YOSYS"
"""Variable naming the ``yosys`` executable."""

ENV_SBY = "SVA2YOSYS_SBY"
"""Variable naming the ``sby`` executable."""

ENV_SBY_LIVE = "SVA2YOSYS_SBY_LIVE"
"""Variable holding a command (split on whitespace) running an ``sby`` with suprove."""

ENV_SLANG = "SVA2YOSYS_SLANG"
"""Variable naming the SystemVerilog ``slang`` executable (same version as pyslang)."""

ENV_EBMC = "SVA2YOSYS_EBMC"
"""Variable holding a command (split on whitespace) running EBMC, e.g. through WSL."""

ENV_LTL2TGBA = "SVA2YOSYS_LTL2TGBA"
"""Variable naming the directory that contains Spot's ``ltl2tgba`` executable (a Linux binary
when EBMC runs through WSL). It is put on the PATH of EBMC's process only, where EBMC's
``--buechi`` finds it."""

LTL2TGBA = "ltl2tgba"
"""The file name of Spot's translator inside the directory named by :data:`ENV_LTL2TGBA`."""

ENV_YOSYS_SVA_TESTS = "SVA2YOSYS_YOSYS_SVA_TESTS"
"""Variable naming a checkout of the Yosys SVA tests (``tests/sva`` of the Yosys sources),
an external corpus that is not part of this repository."""

ENV_EBMC_TESTS = "SVA2YOSYS_EBMC_TESTS"
"""Variable naming the EBMC regression tests (``regression/verilog/SVA`` of the hw-cbmc sources),
an external corpus that is not part of this repository."""

ENV_SBY_EXAMPLES = "SVA2YOSYS_SBY_EXAMPLES"
"""Variable naming the checkout root of the SymbiYosys sources (``docs/examples`` and ``tests``
of YosysHQ/sby), an external corpus that is not part of this repository."""


class ToolError(RuntimeError):
    """An external tool could not be started or produced unusable output."""


@dataclass(frozen=True)
class Tool:
    """A configured external tool.

    Attributes:
        variable: The environment variable it was configured from.
        command: The argument vector prefix: the executable, then any fixed
            leading arguments (for a command-style variable such as ``wsl -e …``).
    """

    variable: str
    command: tuple[str, ...]

    def subprocess_env(self, base: Mapping[str, str]) -> dict[str, str]:
        """Environment for running the tool.

        A copy of ``base`` in which the directory containing the executable and,
        when it exists, the sibling ``lib`` directory (``<bin>/../lib``) are put
        first on ``PATH``, in that order, joined with ``os.pathsep``. When the
        executable has no directory component (e.g. ``wsl``), ``PATH`` is left
        unchanged. ``base`` is not modified. The ``PATH`` key is matched
        case-insensitively and its original spelling is kept; it is created as
        ``PATH`` when absent.

        Args:
            base: The environment to start from.

        Returns:
            The environment for the subprocess.
        """
        env = dict(base)
        executable = Path(self.command[0])
        if executable.parent == Path():
            return env
        entries = [str(executable.parent)]
        lib = executable.parent.parent / "lib"
        if lib.is_dir():
            entries.append(str(lib))
        key = next((k for k in env if k.upper() == "PATH"), "PATH")
        if env.get(key):
            entries.append(env[key])
        env[key] = os.pathsep.join(entries)
        return env


def tool_from_env(
    variable: str, environ: Mapping[str, str], *, command: bool = False
) -> Tool | None:
    """Look up a tool configured by ``variable``.

    Args:
        variable: The variable name, e.g. :data:`ENV_SBY`.
        environ: The environment to read.
        command: ``False``: the value is the path of an executable, which must
            exist as a file. ``True``: the value is a command split on whitespace,
            whose first word is not checked (it may be resolved by the OS, e.g.
            ``wsl``).

    Returns:
        The tool, or ``None`` when the variable is unset, blank, or (for an
        executable) does not name an existing file.
    """
    value = environ.get(variable, "").strip()
    if not value:
        return None
    if command:
        return Tool(variable=variable, command=tuple(value.split()))
    if not Path(value).is_file():
        return None
    return Tool(variable=variable, command=(value,))


def directory_from_env(variable: str, environ: Mapping[str, str], base: Path) -> Path | None:
    """Look up a data directory configured by ``variable`` (e.g. a test corpus).

    A relative value is taken relative to ``base`` (the repository root, so that
    ``.env`` can name a folder inside the checkout).

    Returns:
        The directory, or ``None`` when the variable is unset, blank, or does not
        name an existing directory.
    """
    value = environ.get(variable, "").strip()
    if not value:
        return None
    directory = base / value  # an absolute value replaces base
    return directory if directory.is_dir() else None


def tool_directory_from_env(
    variable: str, environ: Mapping[str, str], base: Path, executable: str
) -> Path | None:
    """Look up a tool configured as the directory that contains ``executable`` (Spot's
    ``ltl2tgba``, which another tool finds on its PATH).

    Returns:
        The directory (a relative value is taken from ``base``), or ``None`` when the
        variable is unset, blank, not a directory, or the directory holds no file named
        ``executable``.
    """
    directory = directory_from_env(variable, environ, base)
    if directory is None or not (directory / executable).is_file():
        return None
    return directory


def run_slang_ast_json(
    tool: Tool,
    sources: Sequence[str],
    cwd: Path,
    *,
    top: Sequence[str] = (),
    timeout: float = 600.0,
) -> object:
    """Elaborate ``sources`` with the slang executable and return its native AST JSON.

    Runs ``slang -q --ast-json - --ast-json-source-info [--top t ...] <sources>`` in
    ``cwd`` and parses standard output; no file is written.

    Args:
        tool: The configured ``slang`` tool.
        sources: Source files, relative to ``cwd`` or absolute.
        cwd: Working directory (file names in the JSON are as given).
        top: Top-level module names.
        timeout: Seconds before the run is aborted.

    Returns:
        The parsed JSON document.

    Raises:
        ToolError: slang could not be started, timed out, exited with a non-zero
            status, or printed invalid JSON.
    """
    args = [*tool.command, "-q", "--ast-json", "-", "--ast-json-source-info"]
    for name in top:
        args += ["--top", name]
    args += list(sources)
    completed = _run_tool(tool, args, cwd, timeout, "slang")
    if completed.returncode != 0:
        raise ToolError(
            f"{tool.variable}: slang exited with {completed.returncode}\n{completed.stderr}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ToolError(f"{tool.variable}: slang printed invalid JSON: {error}") from error


def run_yosys(tool: Tool, commands: Sequence[str], cwd: Path, *, timeout: float = 600.0) -> str:
    """Run a Yosys script given as a list of commands.

    Runs ``yosys -p "<c1>; <c2>; ..."`` with ``cwd`` as working directory, so
    relative file names in the commands resolve there. Yosys writes no files unless a
    command asks for it. Use ``select -assert-*`` commands to turn structural
    checks into a non-zero exit.

    Args:
        tool: The configured ``yosys`` tool.
        commands: Yosys commands; none may contain ``;``.
        cwd: Working directory.
        timeout: Seconds before the run is aborted.

    Returns:
        The console log.

    Raises:
        ValueError: A command contains ``;``.
        ToolError: Yosys could not be started, timed out, or exited with a
            non-zero status; the message ends with the log.
    """
    for command in commands:
        if ";" in command:
            raise ValueError(f"yosys command must not contain ';': {command!r}")
    args = [*tool.command, "-p", "; ".join(commands)]
    completed = _run_tool(tool, args, cwd, timeout, "yosys")
    log = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise ToolError(f"{tool.variable}: yosys exited with {completed.returncode}\n{log}")
    return log


def _run_tool(
    tool: Tool, args: list[str], cwd: Path, timeout: float, what: str
) -> subprocess.CompletedProcess[str]:
    """Run a tool subprocess, mapping start failures and timeouts to ToolError."""
    return run_tool(tool, args, cwd, timeout, what)


def run_tool(
    tool: Tool,
    args: Sequence[str],
    cwd: Path,
    timeout: float,
    what: str,
    *,
    path_prefix: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``args`` (the tool's command, then its arguments) in ``cwd``.

    The environment is :meth:`Tool.subprocess_env` of the process environment; when
    ``path_prefix`` is given it is put first on ``PATH`` after that, for this subprocess
    only. For WSL, the directory is translated to a WSL path and passed as a positional
    argument to ``/bin/sh`` inside WSL. That shell prepends it to the Linux ``PATH`` and
    executes the original command, preserving every argument literally. WSL launch options
    must precede an explicit ``-e`` or ``--exec`` (a bare ``wsl <command>`` also works).
    Bare WSL launches are normalised to ``--exec`` to avoid a default-shell interpretation
    layer. This takes precedence over Linux installations without Windows PATH import.

    Raises:
        ToolError: The tool could not be started or timed out (``what`` names the run).
    """
    env = tool.subprocess_env(os.environ)
    command = list(args)
    if path_prefix is not None:
        if path_prefix and PureWindowsPath(tool.command[0]).stem.lower() == "wsl":
            boundary = next(
                (index + 1 for index, arg in enumerate(tool.command) if arg in ("-e", "--exec")),
                1,
            )
            if boundary == 1 and len(tool.command) > 1 and tool.command[1].startswith("-"):
                raise ToolError("WSL launch options require -e or --exec before the tool command")
            launcher = command[:boundary]
            if boundary == 1:
                launcher.append("--exec")
            command = [
                *launcher,
                "/bin/sh",
                "-c",
                'PATH="$1${PATH:+:$PATH}"; export PATH; shift; exec "$@"',
                "sva2yosys-tool",
                path_for_tool(tool, path_prefix),
                *command[boundary:],
            ]
        else:
            key = next((k for k in env if k.upper() == "PATH"), "PATH")
            env[key] = os.pathsep.join(filter(None, [path_prefix, env.get(key, "")]))
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except OSError as error:
        raise ToolError(f"{tool.variable}: cannot start {tool.command[0]!r}: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise ToolError(f"{tool.variable}: {what} timed out after {timeout} s") from error


class SbyStatus(Enum):
    """Overall result of an SBY task (its ``DONE (<status>, rc=…)`` line)."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


@dataclass(frozen=True)
class CheckHit:
    """One assertion or cover statement reported in an SBY summary.

    Attributes:
        name: Hierarchical cell name as printed by SBY.
        location: Source location as printed by SBY (``file:line.col-line.col``).
        step: The step reported for it; ``None`` for an unreached cover. SBY leaves step 0
            out of its summary, so a failed assertion or reached cover with no step is at
            step 0.
    """

    name: str
    location: str
    step: int | None


@dataclass(frozen=True)
class SbyResult:
    """Parsed outcome of one SBY task.

    Attributes:
        status: The task status.
        failed_assertions: Assertions reported as failed, in log order.
        reached_covers: Cover statements reported as reached, in log order.
        unreached_covers: Cover statements reported as unreached, in log order.
        log: The full log text.
    """

    status: SbyStatus
    failed_assertions: tuple[CheckHit, ...]
    reached_covers: tuple[CheckHit, ...]
    unreached_covers: tuple[CheckHit, ...]
    log: str


_SUMMARY = re.compile(r"\] summary: (.*)$")
_DONE = re.compile(r"\] DONE \((PASS|FAIL|UNKNOWN|TIMEOUT|ERROR), rc=-?\d+\)")
# (SBY leaves step 0 out of its summary: "reached cover statement X at LOC" is step 0)
_FAILED = re.compile(r"\s+failed assertion (.+) at (\S+)(?: step (\d+))?")
_REACHED = re.compile(r"\s+reached cover statement (.+) at (\S+)(?: step (\d+))?")
_UNREACHED = re.compile(r"\s+(.+) at (\S+)")


def parse_sby_log(text: str) -> SbyResult:
    """Parse the console log of one SBY task.

    Reads the ``summary:`` lines (``failed assertion <name> at <loc> step <n>``,
    ``reached cover statement <name> at <loc> step <n>``, and the entries following
    ``unreached cover statements:``) and the final ``DONE (<status>, rc=<n>)``
    line. Lines are recognised after the ``SBY <time> [<job>]`` prefix, whatever
    the job name. Names may contain spaces (Verilog escaped identifiers such as
    ``top.\\g.m .cell``); the location is the last `` at <loc>`` of the line.

    Args:
        text: The log.

    Returns:
        The parsed result; ``log`` is ``text``.

    Raises:
        ToolError: No ``DONE`` line is present.
    """
    status: SbyStatus | None = None
    failed: list[CheckHit] = []
    reached: list[CheckHit] = []
    unreached: list[CheckHit] = []
    in_unreached = False
    for line in text.splitlines():
        done = _DONE.search(line)
        if done is not None:
            status = SbyStatus(done.group(1))
            in_unreached = False
            continue
        summary = _SUMMARY.search(line)
        if summary is None:
            continue
        body = summary.group(1)
        if body.strip() == "unreached cover statements:":
            in_unreached = True
            continue
        if match := _FAILED.fullmatch(body):
            failed.append(CheckHit(match.group(1), match.group(2), int(match.group(3) or 0)))
        elif match := _REACHED.fullmatch(body):
            reached.append(CheckHit(match.group(1), match.group(2), int(match.group(3) or 0)))
        elif in_unreached and (match := _UNREACHED.fullmatch(body)):
            unreached.append(CheckHit(match.group(1), match.group(2), None))
        else:
            in_unreached = False
    if status is None:
        raise ToolError("SBY log has no DONE line")
    return SbyResult(status, tuple(failed), tuple(reached), tuple(unreached), text)


def path_for_tool(tool: Tool, path: PurePath | str) -> str:
    """Spell ``path`` the way ``tool`` understands it.

    A command running through WSL (first word ``wsl`` or ``wsl.exe``) sees Windows
    drives under ``/mnt``: ``C:\a\b`` becomes ``/mnt/c/a/b``. WSL itself translates
    the working directory, but not path arguments. A path without a drive letter, and
    every other tool, gets ``str(path)`` unchanged.

    Args:
        tool: The tool that will receive the path as an argument.
        path: The path.

    Returns:
        The argument text.
    """
    if PureWindowsPath(tool.command[0]).stem.lower() != "wsl":
        return str(path)
    windows = PureWindowsPath(path)
    if not windows.drive.endswith(":"):
        return str(path)
    parts = windows.parts[1:]
    return "/".join(["/mnt", windows.drive[:-1].lower(), *parts])


def run_sby(
    tool: Tool, sby_file: Path, task: str, workdir: Path, *, timeout: float = 600.0
) -> SbyResult:
    """Run one task of an ``.sby`` file.

    SBY resolves the ``[files]`` section relative to its current directory, so it
    runs with the directory of ``sby_file`` as working directory; all of its output
    goes to ``workdir`` (``sby -f -d <workdir>``; spelled for the tool by
    :func:`path_for_tool`, so a WSL command gets a ``/mnt/...`` path). Nothing is written next to
    ``sby_file``.

    Args:
        tool: The configured ``sby`` tool.
        sby_file: The ``.sby`` file.
        task: The task name to run.
        workdir: Output directory; must not exist yet or will be overwritten.
        timeout: Seconds before the run is aborted.

    Returns:
        The parsed result of the task.

    Raises:
        ToolError: SBY could not be started, timed out, or printed no ``DONE``
            line.
    """
    args = [*tool.command, "-f", "-d", path_for_tool(tool, workdir), sby_file.name, task]
    completed = _run_tool(tool, args, sby_file.parent, timeout, f"sby {task}")
    try:
        return parse_sby_log(completed.stdout)
    except ToolError as error:
        raise ToolError(
            f"{tool.variable}: sby {task} exited with {completed.returncode} without a "
            f"DONE line\n{completed.stdout}{completed.stderr}"
        ) from error
