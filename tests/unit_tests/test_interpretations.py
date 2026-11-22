"""Manifest and input-pinning contracts (support.interpretations)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from support.interpretations import TOPICS, Expected, Probe, digest, expected, probes
from support.verilator import Event, Status

type ManifestRow = dict[str, str]
type ManifestChange = Callable[[list[ManifestRow]], None]
type ExpectationRow = dict[str, object]
type ExpectationChange = Callable[[list[ExpectationRow]], None]


def _manifest_rows() -> list[ManifestRow]:
    return [
        {
            "key": f"probe-{topic.lower()}",
            "topic": topic,
            "objective": f"Distinguish the observation for {topic}.",
            "source": f"{topic.lower()}.sv",
            "limit": "Finite two-state simulation only.",
        }
        for topic in TOPICS
    ]


def _write_manifest(path: Path, rows: list[ManifestRow]) -> None:
    path.write_text(json.dumps({"probes": rows}), encoding="utf-8")


def _expectation_document() -> dict[str, object]:
    return {
        "verilator-version": "Verilator 5.047 devel rev test",
        "inputs-sha256": "a" * 64,
        "observations": [
            {
                "key": "probe-i1",
                "status": "finished",
                "events": [{"label": "sample", "value": 3}],
                "detail": "",
            }
        ],
    }


def _expectation_rows(document: dict[str, object]) -> list[ExpectationRow]:
    rows = document["observations"]
    assert isinstance(rows, list)
    assert all(isinstance(row, dict) for row in rows)
    return rows


def test_manifest_covers_each_interpretation_and_returns_typed_probes(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "probes.json"
    rows = _manifest_rows()
    _write_manifest(manifest, rows)

    found = probes(manifest)

    assert all(isinstance(probe, Probe) for probe in found)
    assert {probe.topic for probe in found} == set(TOPICS)
    assert {probe.key for probe in found} == {row["key"] for row in rows}
    assert all(probe.source.endswith(".sv") for probe in found)


def test_manifest_rejects_missing_interpretation_topics(tmp_path: Path) -> None:
    manifest = tmp_path / "probes.json"
    _write_manifest(manifest, _manifest_rows()[:-1])

    with pytest.raises(ValueError):
        probes(manifest)


def test_manifest_rejects_invalid_probe_keys(tmp_path: Path) -> None:
    rows = _manifest_rows()
    rows[0]["key"] = "Probe-I1"
    manifest = tmp_path / "probes.json"
    _write_manifest(manifest, rows)

    with pytest.raises(ValueError, match="invalid probe key"):
        probes(manifest)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "manifest must be an object"),
        ({"probes": [None]}, "probe must be an object"),
        ({"probes": None}, "probes must be a list"),
    ],
)
def test_manifest_rejects_invalid_json_shapes(
    tmp_path: Path, document: object, message: str
) -> None:
    manifest = tmp_path / "probes.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        probes(manifest)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda rows: rows.__setitem__(1, dict(rows[0])), "duplicate"),
        (lambda rows: rows[0].__setitem__("topic", "I12"), "topic"),
        (lambda rows: rows[0].__setitem__("objective", "  "), "objective"),
        (lambda rows: rows[0].__setitem__("limit", ""), "limit"),
    ],
)
def test_manifest_rejects_duplicate_keys_unknown_topics_and_empty_explanations(
    tmp_path: Path, change: ManifestChange, message: str
) -> None:
    rows = _manifest_rows()
    change(rows)
    manifest = tmp_path / "probes.json"
    _write_manifest(manifest, rows)

    with pytest.raises(ValueError, match=message):
        probes(manifest)


@pytest.mark.parametrize(
    "source",
    ["../escape.sv", "subdir/probe.sv", r"subdir\probe.sv", "probe.txt"],
)
def test_manifest_rejects_nonlocal_or_wrong_type_fixture_names(tmp_path: Path, source: str) -> None:
    rows = _manifest_rows()
    rows[0]["source"] = source
    manifest = tmp_path / "probes.json"
    _write_manifest(manifest, rows)

    with pytest.raises(ValueError, match="source"):
        probes(manifest)


def test_digest_normalizes_line_endings_and_covers_all_sources_and_manifest(
    tmp_path: Path,
) -> None:
    manifest = {"probes": [{"key": "alpha"}]}
    (tmp_path / "probes.json").write_bytes(
        json.dumps(manifest, separators=(",", ":")).replace("\n", "").encode()
    )
    (tmp_path / "a.sv").write_bytes(b"module a;\r\nendmodule\r\n")
    (tmp_path / "b.sv").write_bytes(b"module b;\nendmodule\n")

    expected_hash = hashlib.sha256()
    for name, content in (
        ("a.sv", b"module a;\nendmodule\n"),
        ("b.sv", b"module b;\nendmodule\n"),
        ("probes.json", (tmp_path / "probes.json").read_bytes()),
    ):
        expected_hash.update(name.encode())
        expected_hash.update(b"\0")
        expected_hash.update(content)
        expected_hash.update(b"\0")
    pinned = digest(tmp_path)
    assert pinned == expected_hash.hexdigest()

    (tmp_path / "a.sv").write_bytes(b"module a;\nendmodule\n")
    assert digest(tmp_path) == pinned
    (tmp_path / "b.sv").write_text("module changed;\n", encoding="utf-8")
    assert digest(tmp_path) != pinned
    (tmp_path / "b.sv").write_text("module b;\nendmodule\n", encoding="utf-8")
    manifest["probes"][0]["key"] = "changed"
    (tmp_path / "probes.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert digest(tmp_path) != pinned


def test_expectations_return_pinned_version_digest_and_typed_events(tmp_path: Path) -> None:
    path = tmp_path / "expectations.json"
    document = _expectation_document()
    path.write_text(json.dumps(document), encoding="utf-8")

    version_text, source_digest, observations = expected(path)

    assert version_text == document["verilator-version"]
    assert source_digest == document["inputs-sha256"]
    assert observations == (Expected("probe-i1", Status.FINISHED, (Event("sample", 3),), ""),)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verilator-version", "5.047"),
        ("inputs-sha256", "A" * 64),
    ],
)
def test_expectations_reject_malformed_pins(tmp_path: Path, field: str, value: str) -> None:
    document = _expectation_document()
    document[field] = value
    path = tmp_path / "expectations.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid pinned Verilator version or inputs-sha256"):
        expected(path)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "expectations must be an object"),
        (
            {
                "verilator-version": "Verilator 5.047",
                "inputs-sha256": "a" * 64,
                "observations": [None],
            },
            "observation must be an object",
        ),
        (
            {
                "verilator-version": "Verilator 5.047",
                "inputs-sha256": "a" * 64,
                "observations": None,
            },
            "observations must be a list",
        ),
    ],
)
def test_expectations_reject_invalid_json_shapes(
    tmp_path: Path, document: object, message: str
) -> None:
    path = tmp_path / "expectations.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        expected(path)


@pytest.mark.parametrize(
    "event",
    [
        {"label": "sample", "value": True},
        {"label": "done", "value": 1},
    ],
)
def test_expectations_reject_invalid_events(tmp_path: Path, event: dict[str, object]) -> None:
    document = _expectation_document()
    _expectation_rows(document)[0]["events"] = [event]
    path = tmp_path / "expectations.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid event"):
        expected(path)


def test_nonfinished_expectations_cannot_contain_partial_events(tmp_path: Path) -> None:
    document = _expectation_document()
    row = _expectation_rows(document)[0]
    row["status"] = "unsupported"
    row["detail"] = "explicitly unsupported by the tool"
    path = tmp_path / "expectations.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="incomplete event stream"):
        expected(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda rows: rows.append(dict(rows[0])), "duplicate"),
        (lambda rows: rows[0].__setitem__("status", "mystery"), "status"),
    ],
)
def test_expectations_reject_duplicate_keys_and_unknown_statuses(
    tmp_path: Path, mutate: ExpectationChange, message: str
) -> None:
    document = _expectation_document()
    mutate(_expectation_rows(document))
    path = tmp_path / "expectations.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        expected(path)


@pytest.mark.parametrize("status", ["unsupported", "build-error", "runtime-error"])
def test_nonfinished_expectations_require_a_diagnostic_fragment(
    tmp_path: Path, status: str
) -> None:
    document = _expectation_document()
    row = _expectation_rows(document)[0]
    row["status"] = status
    row["detail"] = ""
    path = tmp_path / "expectations.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="detail"):
        expected(path)


@pytest.mark.parametrize("resource", ["Verilator version", "input digest"])
def test_changed_pins_require_reclassification(resource: str) -> None:
    from support.interpretations import require_pin

    require_pin("classified", "classified", resource)
    with pytest.raises(ValueError, match="classify again"):
        require_pin("changed", "classified", resource)
