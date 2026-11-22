"""Configured Verilator simulations for the interpretation evidence, never product code.

Contract:
* Configuration names every executable/root/build directory explicitly, resolving
  relative paths before entering a probe directory. A selected
  configuration with missing paths is an error, never an ambient-PATH discovery.
* On Windows the configured MSYS build shell receives drive paths translated to
  /c/... form; all dynamic values are positional arguments, never shell source.
  The build PATH contains only the configured tool/bin, sibling lib and build
  directories. Ambient-only build tools cannot supply missing dependencies.
  Environment and temporary-directory overrides affect that process only.
* Version is the single Verilator --version line; malformed/failed output is an error.
* Build each checked-in top module in its own work directory with assertions and
  timing enabled. A timeout/start failure raises ToolError; a compiler error is
  unsupported only when its diagnostic explicitly says UNSUPPORTED. Other build
  and runtime failures remain distinct results.
* Simulations report ``SVA2YOSYS_PROBE <label> <integer>`` events in output order and
  exactly one terminal ``SVA2YOSYS_PROBE done 1``. Missing/malformed/duplicate terminal
  markers cannot be mistaken for an empty successful observation. Ordinary output
  is ignored by the event parser. Result detail preserves diagnostics, not verdicts.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PureWindowsPath

from support.toolchain import Tool, ToolError, run_tool, tool_from_env

ENV_VERILATOR = "SVA2YOSYS_VERILATOR"
ENV_SHELL = "SVA2YOSYS_VERILATOR_SHELL"
ENV_ROOT = "SVA2YOSYS_VERILATOR_ROOT"
ENV_BUILD_PATH = "SVA2YOSYS_VERILATOR_BUILD_PATH"


@dataclass(frozen=True)
class Config:
    """The Verilator executable, build shell, runtime root and explicit build PATH."""

    tool: Tool
    shell: Tool
    root: Path
    build_dirs: tuple[Path, ...]


@dataclass(frozen=True)
class Event:
    """An explicitly labelled observation (sample index, count or Boolean value)."""

    label: str
    value: int


class Status(Enum):
    FINISHED = "finished"
    UNSUPPORTED = "unsupported"
    BUILD_ERROR = "build-error"
    RUNTIME_ERROR = "runtime-error"


@dataclass(frozen=True)
class Result:
    """A simulation outcome; only FINISHED has a complete event stream."""

    status: Status
    events: tuple[Event, ...]
    detail: str


def configuration(environ: Mapping[str, str]) -> Config:
    """Read explicit configuration; ValueError names any unset/invalid required value."""
    executable = tool_from_env(ENV_VERILATOR, environ)
    shell = tool_from_env(ENV_SHELL, environ)
    errors: list[str] = []
    for name, tool in ((ENV_VERILATOR, executable), (ENV_SHELL, shell)):
        if tool is None:
            errors.append(f"{name} must name an existing executable file")
    root_value = environ.get(ENV_ROOT, "").strip()
    root = Path(root_value)
    if not root_value or not (root / "include" / "verilated.h").is_file():
        errors.append(f"{ENV_ROOT} must contain include/verilated.h")
    raw_dirs = environ.get(ENV_BUILD_PATH, "").strip()
    build_dirs = tuple(Path(part) for part in raw_dirs.split(os.pathsep) if part)
    if not raw_dirs or not build_dirs or any(not d.is_dir() for d in build_dirs):
        errors.append(f"{ENV_BUILD_PATH} must name existing build directories")
    if errors:
        raise ValueError("; ".join(errors))
    assert executable is not None and shell is not None
    return Config(
        Tool(executable.variable, (str(Path(executable.command[0]).resolve()),)),
        Tool(shell.variable, (str(Path(shell.command[0]).resolve()),)),
        root.resolve(),
        tuple(p.resolve() for p in build_dirs),
    )


def shell_path(path: str) -> str:
    """Translate an absolute Windows drive path to MSYS form; leave POSIX paths literal.

    Reject drive-relative paths and UNC paths rather than guessing their mapping.
    """
    if path.startswith("//"):
        raise ValueError(f"no unambiguous MSYS mapping for {path!r}")
    windows = PureWindowsPath(path)
    if windows.drive:
        if len(windows.drive) != 2 or windows.drive[1] != ":" or not windows.is_absolute():
            raise ValueError(f"no unambiguous MSYS drive mapping for {path!r}")
        return f"/{windows.drive[0].lower()}/" + "/".join(windows.parts[1:])
    return path


def parse_events(output: str) -> tuple[Event, ...]:
    """Parse the protocol and require one final done=1; ValueError on malformed output."""
    events: list[Event] = []
    done = False
    for line in output.splitlines():
        if not line.startswith("SVA2YOSYS_PROBE"):
            continue
        match = re.fullmatch(r"SVA2YOSYS_PROBE ([a-z][a-z0-9_]*) (-?\d+)", line)
        if match is None or done:
            raise ValueError(f"malformed or post-terminal probe event: {line!r}")
        label, value = match.group(1), int(match.group(2))
        if label == "done":
            if value != 1:
                raise ValueError("probe done marker must be 1")
            done = True
        else:
            events.append(Event(label, value))
    if not done:
        raise ValueError("probe did not report its final done marker")
    return tuple(events)


_SCRIPT = (
    'set -eu; export VERILATOR_ROOT="$1" TMPDIR="$2" TMP="$3" TEMP="$3"; '
    'PATH="$4"; export PATH; shift 4; exec "$@"'
)


def _invoke(
    config: Config, args: list[str], workdir: Path, timeout: float, what: str
) -> subprocess.CompletedProcess[str]:
    """Constant shell source with every configured path/argument passed positionally. The
    build directories come before the tool's own ``bin`` and ``lib``: a program the build
    produced must find the compiler's runtime DLLs before the older ones in the tool's ``lib``
    (it crashes with the wrong libstdc++)."""
    workdir.mkdir(parents=True, exist_ok=True)
    bins = [Path(config.tool.command[0]).parent]
    sibling_lib = bins[0].parent / "lib"
    if sibling_lib.is_dir():
        bins.append(sibling_lib)
    bins = [*config.build_dirs, *bins]
    command = [
        *config.shell.command,
        "-c",
        _SCRIPT,
        "sva2yosys-verilator",
        shell_path(str(config.root.resolve())),
        shell_path(str(workdir.resolve())),
        str(workdir.resolve()),
        ":".join(shell_path(str(p.resolve())) for p in bins),
        *args,
    ]
    return run_tool(config.shell, command, workdir, timeout, what)


def version(config: Config, workdir: Path) -> str:
    """Get the Verilator version through the same configured environment as probe builds."""
    completed = _invoke(
        config,
        [shell_path(config.tool.command[0]), "--version"],
        workdir,
        60.0,
        "verilator --version",
    )
    lines = [
        line.strip() for line in completed.stdout.splitlines() if line.startswith("Verilator ")
    ]
    if completed.returncode != 0 or len(lines) != 1:
        raise ToolError(f"unusable Verilator version output: {completed.stdout}{completed.stderr}")
    return lines[0]


def run(config: Config, source: Path, workdir: Path, *, timeout: float = 120.0) -> Result:
    """Build and run one top module; retain build/run logs inside workdir."""
    workdir.mkdir(parents=True, exist_ok=True)
    objects = workdir.resolve() / "obj"
    objects.mkdir(exist_ok=True)
    build = _invoke(
        config,
        [
            shell_path(config.tool.command[0]),
            "--binary",
            "--timing",
            "--assert",
            "--coverage-user",
            "--Wno-fatal",
            # MSYS2 g++ 16.2 (mingw-w64, also UCRT64): with -Os (Verilator's default) and the
            # default -std=gnu++20 it calls the constructor variant C4 of std::string(string&&),
            # which libstdc++'s import library does not export ("undefined reference to
            # basic_string(basic_string&&)"). Any inlining optimisation (-O1) avoids the call;
            # the language standard stays the default one.
            "-CFLAGS",
            "-O1",
            "--top-module",
            "top",
            "--prefix",
            "Vprobe",
            "--Mdir",
            shell_path(str(objects)),
            shell_path(str(source.resolve())),
        ],
        workdir,
        timeout,
        "Verilator build",
    )
    log = build.stdout + build.stderr
    (workdir / "build.log").write_text(log, encoding="utf-8")
    if build.returncode != 0:
        status = Status.UNSUPPORTED if "%Error-UNSUPPORTED:" in log else Status.BUILD_ERROR
        return Result(status, (), log)
    executable = objects / ("Vprobe.exe" if os.name == "nt" else "Vprobe")
    simulation = _invoke(
        config, [shell_path(str(executable))], workdir, timeout, "Verilator simulation"
    )
    output = simulation.stdout + simulation.stderr
    (workdir / "run.log").write_text(output, encoding="utf-8")
    if simulation.returncode != 0:
        return Result(Status.RUNTIME_ERROR, (), f"exit code {simulation.returncode}\n{output}")
    try:
        events = parse_events(output)
    except ValueError as error:
        return Result(Status.RUNTIME_ERROR, (), f"{error}\n{output}")
    return Result(Status.FINISHED, events, output)
