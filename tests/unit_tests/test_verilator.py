"""Configured Verilator runner contracts (support.verilator)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import support.verilator as verilator
from support.toolchain import Tool, ToolError
from support.verilator import (
    ENV_BUILD_PATH,
    ENV_ROOT,
    ENV_SHELL,
    ENV_VERILATOR,
    Config,
    Event,
    configuration,
    parse_events,
    run,
    shell_path,
    version,
)


def _configuration_files(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    executable = tmp_path / "bin" / "verilator.exe"
    executable.parent.mkdir()
    executable.touch()
    shell = tmp_path / "msys" / "bash.exe"
    shell.parent.mkdir()
    shell.touch()
    root = tmp_path / "share" / "verilator"
    (root / "include").mkdir(parents=True)
    (root / "include" / "verilated.h").touch()
    build_bin = tmp_path / "build" / "bin"
    build_bin.mkdir(parents=True)
    build_lib = tmp_path / "build" / "lib"
    build_lib.mkdir(parents=True)
    return executable, shell, root, build_bin, build_lib


def _environment(tmp_path: Path) -> dict[str, str]:
    executable, shell, root, build_bin, build_lib = _configuration_files(tmp_path)
    return {
        ENV_VERILATOR: str(executable),
        ENV_SHELL: str(shell),
        ENV_ROOT: str(root),
        ENV_BUILD_PATH: os.pathsep.join((str(build_bin), str(build_lib))),
        "PATH": "ambient-path-must-not-supply-tools",
    }


def test_configuration_uses_the_explicit_executables_root_and_build_path(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)

    found = configuration(env)

    assert found == Config(
        Tool(ENV_VERILATOR, (env[ENV_VERILATOR],)),
        Tool(ENV_SHELL, (env[ENV_SHELL],)),
        Path(env[ENV_ROOT]),
        tuple(Path(part) for part in env[ENV_BUILD_PATH].split(os.pathsep)),
    )


def test_relative_configuration_remains_valid_after_changing_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _environment(tmp_path)
    monkeypatch.chdir(tmp_path)
    relative = {
        name: os.pathsep.join(str(Path(p).relative_to(tmp_path)) for p in value.split(os.pathsep))
        for name, value in env.items()
        if name != "PATH"
    }
    config = configuration(relative)
    other = tmp_path / "probe"
    other.mkdir()
    monkeypatch.chdir(other)
    assert Path(config.tool.command[0]).is_absolute()
    assert Path(config.shell.command[0]).is_absolute()
    assert Path(config.tool.command[0]).is_file()
    assert Path(config.shell.command[0]).is_file()
    assert config.root.is_absolute() and config.root.is_dir()
    assert all(p.is_absolute() and p.is_dir() for p in config.build_dirs)


@pytest.mark.parametrize("variable", [ENV_VERILATOR, ENV_SHELL, ENV_ROOT, ENV_BUILD_PATH])
def test_configuration_names_a_missing_required_setting(tmp_path: Path, variable: str) -> None:
    env = _environment(tmp_path)
    del env[variable]

    with pytest.raises(ValueError, match=variable):
        configuration(env)


@pytest.mark.parametrize("variable", [ENV_VERILATOR, ENV_SHELL])
def test_configuration_rejects_a_missing_configured_executable(
    tmp_path: Path, variable: str
) -> None:
    env = _environment(tmp_path)
    env[variable] = str(tmp_path / "missing.exe")

    with pytest.raises(ValueError, match=variable):
        configuration(env)


def test_configuration_rejects_a_missing_runtime_root(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    env[ENV_ROOT] = str(tmp_path / "missing-root")

    with pytest.raises(ValueError, match=ENV_ROOT):
        configuration(env)


def test_configuration_rejects_a_missing_build_directory(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    env[ENV_BUILD_PATH] = str(tmp_path / "missing-build-bin")

    with pytest.raises(ValueError, match=ENV_BUILD_PATH):
        configuration(env)


@pytest.mark.parametrize(
    ("source", "translated"),
    [
        (r"C:\opt\sh\usr\bin\bash.exe", "/c/opt/sh/usr/bin/bash.exe"),
        ("D:/tools/verilator", "/d/tools/verilator"),
        ("/usr/bin/bash", "/usr/bin/bash"),
        ("/mnt/c/opt/verilator", "/mnt/c/opt/verilator"),
    ],
)
def test_shell_path_translates_drive_paths_and_preserves_posix_paths(
    source: str, translated: str
) -> None:
    assert shell_path(source) == translated


@pytest.mark.parametrize(
    "source",
    [r"C:tools\verilator", r"\\server\share\verilator", "//server/share/verilator"],
)
def test_shell_path_rejects_paths_without_an_unambiguous_msys_mapping(source: str) -> None:
    with pytest.raises(ValueError):
        shell_path(source)


def test_shell_path_rejects_incomplete_unc_host() -> None:
    with pytest.raises(ValueError, match="no unambiguous MSYS mapping"):
        shell_path("//server")


def test_parse_events_preserves_event_order_and_ignores_other_output() -> None:
    output = "build log\nSVA2YOSYS_PROBE sample 2\nordinary output\n"
    output += "SVA2YOSYS_PROBE sample 4\nSVA2YOSYS_PROBE done 1\n"

    assert parse_events(output) == (Event("sample", 2), Event("sample", 4))


@pytest.mark.parametrize(
    "output",
    [
        "ordinary output only\n",
        "SVA2YOSYS_PROBE sample 1\n",
        "SVA2YOSYS_PROBE done 0\n",
        "SVA2YOSYS_PROBE done 1\nSVA2YOSYS_PROBE done 1\n",
        "SVA2YOSYS_PROBE done 1\nSVA2YOSYS_PROBE sample 2\n",
        "SVA2YOSYS_PROBE sample nope\nSVA2YOSYS_PROBE done 1\n",
        "SVA2YOSYS_PROBE sample 1 extra\nSVA2YOSYS_PROBE done 1\n",
        "SVA2YOSYS_PROBE\nSVA2YOSYS_PROBE done 1\n",
    ],
)
def test_parse_events_rejects_incomplete_or_malformed_protocol(output: str) -> None:
    with pytest.raises(ValueError):
        parse_events(output)


def _completed(
    *, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _config(tmp_path: Path) -> Config:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return configuration(_environment(tmp_path))


@pytest.mark.parametrize(
    ("build_log", "expected_status"),
    [
        ("%Error: syntax error\n", verilator.Status.BUILD_ERROR),
        ("%Error-UNSUPPORTED: unsupported assertion\n", verilator.Status.UNSUPPORTED),
    ],
)
def test_run_distinguishes_build_errors_from_explicit_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_log: str,
    expected_status: verilator.Status,
) -> None:
    source = tmp_path / "probe.sv"
    source.touch()
    calls = 0

    def invoke(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return _completed(returncode=1, stdout=build_log)

    monkeypatch.setattr(verilator, "_invoke", invoke)

    result = run(_config(tmp_path / "config"), source, tmp_path / "work")

    assert result.status is expected_status
    assert result.events == ()
    assert result.detail == build_log
    assert calls == 1
    assert (tmp_path / "work" / "build.log").read_text(encoding="utf-8") == build_log


def test_run_reports_runtime_failure_without_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "probe.sv"
    source.touch()
    outputs = iter(
        [
            _completed(stdout="build succeeded\n"),
            _completed(returncode=2, stdout="simulation crashed\n"),
        ]
    )
    monkeypatch.setattr(verilator, "_invoke", lambda *args, **kwargs: next(outputs))

    result = run(_config(tmp_path / "config"), source, tmp_path / "work")

    assert result.status is verilator.Status.RUNTIME_ERROR
    assert result.events == ()
    assert "simulation crashed" in result.detail


def test_run_rejects_successful_runtime_without_terminal_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "probe.sv"
    source.touch()
    outputs = iter(
        [
            _completed(),
            _completed(stdout="SVA2YOSYS_PROBE sample 3\n"),
        ]
    )
    monkeypatch.setattr(verilator, "_invoke", lambda *args, **kwargs: next(outputs))

    result = run(_config(tmp_path / "config"), source, tmp_path / "work")

    assert result.status is verilator.Status.RUNTIME_ERROR
    assert result.events == ()
    assert "did not report its final done marker" in result.detail


def test_run_returns_events_from_a_complete_successful_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "probe.sv"
    source.touch()
    outputs = iter(
        [
            _completed(stdout="build succeeded\n"),
            _completed(
                stdout=(
                    "SVA2YOSYS_PROBE first 0\nSVA2YOSYS_PROBE second 2\nSVA2YOSYS_PROBE done 1\n"
                )
            ),
        ]
    )
    monkeypatch.setattr(verilator, "_invoke", lambda *args, **kwargs: next(outputs))

    result = run(_config(tmp_path / "config"), source, tmp_path / "work")

    assert result.status is verilator.Status.FINISHED
    assert result.events == (Event("first", 0), Event("second", 2))


@pytest.mark.parametrize(
    ("completed", "message"),
    [
        (_completed(stdout="not a version line\n"), "unusable Verilator version output"),
        (_completed(returncode=1, stdout="Verilator 5.020 2024-01-01\n"), "unusable Verilator"),
    ],
)
def test_version_rejects_malformed_or_failed_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed: subprocess.CompletedProcess[str],
    message: str,
) -> None:
    monkeypatch.setattr(verilator, "_invoke", lambda *args, **kwargs: completed)

    with pytest.raises(ToolError, match=message):
        version(_config(tmp_path / "config"), tmp_path / "work")


@pytest.mark.parametrize("message", ["could not start shell", "timed out after 120 seconds"])
def test_run_propagates_start_and_timeout_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    source = tmp_path / "probe.sv"
    source.touch()

    def fail(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise ToolError(message)

    monkeypatch.setattr(verilator, "run_tool", fail)

    with pytest.raises(ToolError, match=message):
        run(_config(tmp_path / "config"), source, tmp_path / "work")


def test_invoke_passes_metacharacters_as_literal_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path / "config")
    literal = "source ; $(touch SHOULD_NOT_EXIST) 'quoted'.sv"
    captured: list[str] = []

    def capture(
        tool: Tool, command: list[str], workdir: Path, timeout: float, what: str
    ) -> subprocess.CompletedProcess[str]:
        captured.extend(command)
        return _completed()

    monkeypatch.setattr(verilator, "run_tool", capture)

    verilator._invoke(config, [literal], tmp_path / "work", 1, "probe")

    assert captured[-1] == literal
    assert literal not in captured[captured.index("-c") + 1]
