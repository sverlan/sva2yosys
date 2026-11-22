"""Typed manifest and pinning for targeted interpretation observations.

Contract: JSON manifests contain unique slug keys, a known topic (I1-I11 or D1),
an objective, a plain .sv fixture basename, and a nonempty explanation of the
comparison limit. Expectations are observed tool behavior, not IEEE authority.
Every topic is represented. Digest all .sv files and probes.json in sorted name
order, names plus NUL plus contents plus NUL, normalizing CRLF to LF. A changed
version/digest fails classification before tool comparisons. Never infer agreement
from unsupported syntax, absent observations or an excluded EBMC engine.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from support.verilator import Event, Status

TOPICS = (*(f"I{k}" for k in range(1, 12)), "D1")


@dataclass(frozen=True)
class Probe:
    """An objective and its SV fixture; limits make model/coverage restrictions explicit."""

    key: str
    topic: str
    objective: str
    source: str
    limit: str


@dataclass(frozen=True)
class Expected:
    """A pinned tool observation and a diagnostic fragment for unsupported/error rows."""

    key: str
    status: Status
    events: tuple[Event, ...]
    detail: str


def require_pin(found: str, pinned: str, resource: str) -> None:
    """Reject a changed version or input digest before comparing observations."""
    if found != pinned:
        raise ValueError(f"{resource} {found!r} is not the classified {pinned!r}: classify again")


def probes(path: Path) -> tuple[Probe, ...]:
    """Read and validate the manifest; ValueError on malformed/incomplete coverage."""
    document = _object(json.loads(path.read_text(encoding="utf-8")), "manifest")
    result: list[Probe] = []
    for raw in _list(document.get("probes"), "probes"):
        row = _object(raw, "probe")
        key = _string(row, "key")
        if re.fullmatch(r"[a-z][a-z0-9_-]*", key) is None:
            raise ValueError(f"invalid probe key {key!r}")
        if any(p.key == key for p in result):
            raise ValueError(f"duplicate probe {key!r}")
        topic = _string(row, "topic")
        if topic not in TOPICS:
            raise ValueError(f"unknown topic {topic!r}")
        source = _string(row, "source")
        if re.fullmatch(r"[A-Za-z0-9_-]+\.sv", source) is None:
            raise ValueError(f"source must be a plain SV fixture basename: {source!r}")
        result.append(Probe(key, topic, _string(row, "objective"), source, _string(row, "limit")))
    missing = set(TOPICS) - {p.topic for p in result}
    if missing:
        raise ValueError(f"missing interpretation topics: {sorted(missing)}")
    return tuple(result)


def digest(fixtures: Path) -> str:
    """Fingerprint all probe inputs, including metadata, without circular expectations."""
    fingerprint = hashlib.sha256()
    files = sorted([*fixtures.glob("*.sv"), fixtures / "probes.json"], key=lambda p: p.name)
    for path in files:
        fingerprint.update(path.name.encode())
        fingerprint.update(b"\0")
        fingerprint.update(path.read_bytes().replace(b"\r\n", b"\n"))
        fingerprint.update(b"\0")
    return fingerprint.hexdigest()


def expected(path: Path) -> tuple[str, str, tuple[Expected, ...]]:
    """Read (Verilator version, input digest, observations), rejecting duplicate keys."""
    document = _object(json.loads(path.read_text(encoding="utf-8")), "expectations")
    tool_version = _string(document, "verilator-version")
    fingerprint = _string(document, "inputs-sha256")
    if (
        not tool_version.startswith("Verilator ")
        or re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None
    ):
        raise ValueError("invalid pinned Verilator version or inputs-sha256")
    result: list[Expected] = []
    for raw in _list(document.get("observations"), "observations"):
        row = _object(raw, "observation")
        key = _string(row, "key")
        if any(p.key == key for p in result):
            raise ValueError(f"duplicate expectation {key!r}")
        try:
            status = Status(_string(row, "status"))
        except ValueError as error:
            raise ValueError(f"invalid status for {key}") from error
        detail = row.get("detail")
        if not isinstance(detail, str) or (status is not Status.FINISHED and not detail.strip()):
            raise ValueError(f"detail is required for {key}")
        events: list[Event] = []
        for item in _list(row.get("events"), "events"):
            event = _object(item, "event")
            label, value = _string(event, "label"), event.get("value")
            if not isinstance(value, int) or isinstance(value, bool) or label == "done":
                raise ValueError(f"invalid event for {key}")
            events.append(Event(label, value))
        if status is not Status.FINISHED and events:
            raise ValueError(f"incomplete event stream for {key}")
        result.append(Expected(key, status, tuple(events), detail))
    return tool_version, fingerprint, tuple(result)


def _object(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError(f"{where} must be an object")
    return cast("dict[str, object]", value)


def _list(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list")
    return cast("list[object]", value)


def _string(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be nonempty text")
    return value
