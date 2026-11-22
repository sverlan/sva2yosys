"""Properties of the GitHub workflows that matter for safety (`.github/workflows`).

The files are read as text: no YAML parser is a dependency. What is checked is what a mistake
could turn into an unwanted publication or into an unpinned third-party action.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
ACTIONS = WORKFLOWS.parent / "actions" / "tools" / "action.yml"
FILES = [*sorted(WORKFLOWS.glob("*.yml")), ACTIONS]


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _pin(text: str, name: str) -> str:
    found = re.search(rf'echo "{name}=(.*)"', text)
    assert found is not None, name
    return found[1]


def test_the_expected_workflows_exist() -> None:
    assert [path.name for path in FILES] == ["ci.yml", "full.yml", "release.yml", "action.yml"]


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_every_action_is_pinned_by_a_commit_and_names_its_version(path: Path) -> None:
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)(.*)$", path.read_text(encoding="utf-8"), re.M)
    assert uses
    for action, rest in uses:
        if action.startswith("./"):  # a local action of this repository
            assert (path.parents[2] / action).is_dir(), action
            continue
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action), action
        assert re.search(r"#\s*v\d+\.\d+\.\d+", rest), action


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_no_workflow_uses_a_stored_secret_or_write_access_by_default(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "secrets." not in text  # trusted publishing: no token is stored
    if path != ACTIONS:  # a local action takes the permissions of the job that uses it
        assert re.search(r"^permissions:\n  contents: read\n", text, re.M)
    assert "contents: write" not in text and "pull_request_target" not in text


def test_a_release_starts_only_from_a_public_tag() -> None:
    text = _text("release.yml")
    trigger = text[text.index("\non:") : text.index("\npermissions:")]
    assert 'tags: ["v[0-9]*.[0-9]*.[0-9]*"]' in trigger
    assert "branches" not in trigger and "workflow_dispatch" not in trigger
    assert "pull_request" not in trigger and "schedule" not in trigger


def test_the_upload_is_a_separate_job_behind_the_pypi_environment() -> None:
    text = _text("release.yml")
    assert text.count("id-token: write") == 1
    publish = text[text.index("\n  publish:") :]
    assert "id-token: write" in publish
    assert "needs: build" in publish
    assert re.search(r"environment:\s*\n\s+name: pypi\n", publish)
    assert "pypa/gh-action-pypi-publish@" in publish
    assert "pypa/gh-action-pypi-publish" not in text[: text.index("\n  publish:")]


def test_the_release_checks_the_tag_before_building() -> None:
    text = _text("release.yml")
    build = text[text.index("\n  build:") : text.index("\n  publish:")]
    assert 'git merge-base --is-ancestor "${GITHUB_SHA}" origin/main' in build
    assert 'tests/support/release_tag.py "${GITHUB_REF_NAME}"' in build
    assert build.index("release_tag.py") < build.index("uv build")
    assert "uv run pytest" in build and "uv run mypy" in build


def test_ci_runs_on_pushes_to_main_and_pull_requests_without_secrets() -> None:
    text = _text("ci.yml")
    assert "branches: [main]" in text and "pull_request:" in text
    assert "id-token" not in text


def test_the_full_suite_is_started_by_hand_only_without_secrets() -> None:
    text = _text("full.yml")
    triggers = re.search(r"^on:\n((?:[ \t]+.*\n|\n)+)", text, re.MULTILINE)
    assert triggers is not None
    assert triggers[1].split() == ["workflow_dispatch:"]
    assert "id-token" not in text and "secrets." not in text
    assert "--slow" in text and "--external-corpora=all" in text
    assert "tests/support/corpora.py" in ACTIONS.read_text(encoding="utf-8")


def test_the_oss_cad_suite_is_pinned_and_verified_by_its_checksum() -> None:
    text = _text("ci.yml")
    assert re.search(r'OSS_CAD_TAG: "\d{4}-\d{2}-\d{2}"', text)
    assert re.search(r"OSS_CAD_FILE: oss-cad-suite-linux-x64-\d{8}\.tgz", text)
    assert re.search(r"OSS_CAD_SHA256: [0-9a-f]{64}\b", text)
    assert "sha256sum -c -" in text
    assert "github.com/YosysHQ/oss-cad-suite-build/releases/download/" in text


def _value(text: str, name: str) -> str:
    found = re.search(rf"{name}: \"?([^\"\n]+)\"?", text)
    assert found is not None, name
    return found[1]


def test_the_full_suite_runs_on_the_runner_with_the_pins_of_the_ci_image() -> None:
    full, action = _text("full.yml"), ACTIONS.read_text(encoding="utf-8")
    assert "container:" not in full  # no third-party image: every download is verified here
    dockerfile = (WORKFLOWS.parents[1] / "docker" / "ci" / "Dockerfile").read_text(encoding="utf-8")
    for name in (
        "OSS_CAD_TAG",
        "OSS_CAD_FILE",
        "OSS_CAD_SHA256",
        "EBMC_URL",
        "EBMC_SHA256",
        "SLANG_URL",
        "SLANG_SHA256",
        "SPOT_URL",
        "SPOT_SHA256",
    ):
        argument = re.search(rf"^ARG {name}=(\S+)$", dockerfile, re.MULTILINE)
        assert argument is not None, name
        assert _pin(action, name) == argument[1], name
    assert _pin(action, "OSS_CAD_TAG") == _value(_text("ci.yml"), "OSS_CAD_TAG")
    assert action.count("sha256sum -c -") == 4  # suite, EBMC, slang and the Spot source


def test_the_full_suite_runs_one_parallel_job_for_each_group() -> None:
    full = _text("full.yml")
    assert "fromJSON(needs.prepare.outputs.groups)" in full
    assert "tests/support/ci_groups.py --names" in full
    assert "--external-tools=all --external-corpora=all --slow" in full


def test_the_ci_image_pins_the_same_suite_as_the_workflow() -> None:
    dockerfile = (WORKFLOWS.parents[1] / "docker" / "ci" / "Dockerfile").read_text(encoding="utf-8")
    workflow = _text("ci.yml")
    for name in ("OSS_CAD_TAG", "OSS_CAD_FILE", "OSS_CAD_SHA256"):
        argument = re.search(rf"^ARG {name}=(\S+)$", dockerfile, re.MULTILINE)
        assert argument is not None, name
        assert argument[1] == _value(workflow, name), name
    assert "sha256sum -c -" in dockerfile
    assert re.search(r"^ARG BASE=python:3\.14-slim-trixie$", dockerfile, re.MULTILINE)
    assert len(re.findall(r"^FROM base AS ", dockerfile, re.MULTILINE)) >= 3  # multi-stage
    assert re.search(r"^FROM base$", dockerfile, re.MULTILINE)  # one base image for every stage
    assert len(re.findall(r"^FROM ", dockerfile, re.MULTILINE)) == 5
    for name in ("EBMC", "SLANG", "SPOT"):  # every other download is checked too
        assert re.search(rf"^ARG {name}_SHA256=[0-9a-f]{{64}}$", dockerfile, re.MULTILINE), name
    assert dockerfile.count("sha256sum -c -") == 4
    assert "SVA2YOSYS_YOSYS=/opt/oss-cad-suite/bin/yosys" in dockerfile
    assert "SVA2YOSYS_SBY=/opt/oss-cad-suite/bin/sby" in dockerfile
    for variable in (
        "SVA2YOSYS_EBMC=",
        "SVA2YOSYS_LTL2TGBA=",
        "SVA2YOSYS_SLANG=",
        "SVA2YOSYS_SBY_LIVE=",
        "SVA2YOSYS_VERILATOR=",
        "SVA2YOSYS_VERILATOR_BUILD_PATH=/usr/bin:/usr/local/bin",
    ):
        assert variable in dockerfile, variable
