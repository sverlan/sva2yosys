"""Pinned native simulator observations, with explicit model limits (CONTRACT.md)."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from support import verilator
from support.interpretations import Probe, digest, expected, probes, require_pin
from support.toolchain import Tool
from support.verilator import Config, Event, Status, configuration, run, version
from sva2yosys.slang.export import export_texts

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROBES = probes(FIXTURES / "probes.json")
VERSION, DIGEST, OBSERVATIONS = expected(FIXTURES / "expectations.json")


@pytest.fixture(scope="session")
def classified_inputs() -> None:
    require_pin(digest(FIXTURES), DIGEST, "interpretation inputs")


@pytest.fixture(scope="session")
def classified_verilator(
    classified_inputs: None, verilator_tool: Tool, tmp_path_factory: pytest.TempPathFactory
) -> Config:
    config = configuration(os.environ)
    found = version(config, tmp_path_factory.mktemp("interpretation-version"))
    require_pin(found, VERSION, "Verilator version")
    return config


def test_the_inputs_and_all_topics_have_classified_observations(classified_inputs: None) -> None:
    assert {row.key for row in OBSERVATIONS} == {probe.key for probe in PROBES}
    assert {probe.source for probe in PROBES} == {p.name for p in FIXTURES.glob("*.sv")}
    # Infrastructure failures are never pinned as native language observations.
    assert all(row.status in (Status.FINISHED, Status.UNSUPPORTED) for row in OBSERVATIONS)


@pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.key)
def test_native_sources_are_well_formed_systemverilog(probe: Probe) -> None:
    source = FIXTURES / probe.source
    export_texts([(source.name, source.read_text(encoding="utf-8"))])


@pytest.mark.parametrize(
    "probe",
    [
        pytest.param(p, id=p.key, marks=() if p.key == "control" else pytest.mark.extended)
        for p in PROBES
    ],
)
def test_native_observation(probe: Probe, classified_verilator: Config, tmp_path: Path) -> None:
    observed = run(classified_verilator, FIXTURES / probe.source, tmp_path)
    pinned = next(row for row in OBSERVATIONS if row.key == probe.key)
    assert observed.status == pinned.status, observed.detail

    # Cross-action scheduling order is not a sample-time observation; retain multiplicity.
    def key(event: Event) -> tuple[str, int]:
        return event.label, event.value

    assert sorted(observed.events, key=key) == sorted(pinned.events, key=key), observed.detail
    if pinned.detail:
        assert pinned.detail in observed.detail


def test_ambient_only_build_tools_are_excluded_from_the_subprocess_environment(
    classified_verilator: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    dependency = ambient / "sva2yosys_ambient_only_dependency.exe"
    dependency.touch()
    dependency.chmod(0o755)
    monkeypatch.setenv("PATH", str(ambient))
    command = [
        verilator.shell_path(classified_verilator.shell.command[0]),
        "-c",
        'if command -v "$1" >/dev/null; then printf "found\\n"; else printf "absent\\n"; fi',
        "dependency-lookup",
        dependency.name,
    ]
    # A positive lookup proves the sentinel is visible when explicitly configured.
    explicit = replace(classified_verilator, build_dirs=(*classified_verilator.build_dirs, ambient))
    positive = verilator._invoke(explicit, command, tmp_path / "positive", 30, "explicit lookup")
    isolated = verilator._invoke(
        classified_verilator, command, tmp_path / "isolated", 30, "ambient-only lookup"
    )
    assert positive.returncode == isolated.returncode == 0
    assert positive.stdout.strip() == "found", positive.stderr
    assert isolated.stdout.strip() == "absent", isolated.stderr
