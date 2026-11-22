"""Unit contract of support.trace_suite (corpus contract points 2-5, 9-11, 16).

Expectations are derived by hand from the trace text below and from the tables' own rows,
never from the generator under test.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from support import trace_driver
from support.ebmc_differential import load_all_cases, traces_digest
from support.trace_suite import (
    CORPUS_DIRECTORY,
    EXPORTED_CATEGORIES,
    TERNARY_FROM,
    Attempts,
    CaseEntry,
    Category,
    Infinite,
    SampleIndex,
    Stimulus,
    build_corpus,
    case_id,
    category_of,
    check_corpus,
    entry_for,
    main,
    render_csv,
    render_formal,
    render_manifest,
    render_sim,
    render_wavedrom,
    write_corpus,
)
from support.tracefile import TraceCase, load_cases

DEMO = """\
== plain
property: a |-> ##1 b
signals: a b v:8
a : 1 . . 1
b : . 1 . .
v : 0 255 16 3
fail : . . 1 1
pass : . 1 . .
clause: 16.9.1 16.12.7
class: normative

== first
property: a and b
disable: !r
scope: first-failure
a : 1 1 .
b : . 1 .
r : . . 1
fail : . 1 .
support: oracle
clause: 16.9.5, Annex F
class: interpretation I3, I5

== att
property: a |-> ##[1:2] b
scope: attempt
a : 1 . .
b : . . 1
fail : . . 1
pass : . . .
clause: 16.12.2
class: implementation-defined

== cov
property: a ##1 b
kind: cover
a : 1 .
b : . 1
pass : . 1
clause: 16.14.3
class: normative

== asm
property: a
kind: assume
a : 1 1
clause: 16.14.2
class: normative

== ini
property: a
procedure: initial
a : 1 .
fail : . .
clause: 16.14.6
class: normative

== lp
property: s_eventually a
scope: attempt
loop: 1
a : . . 1
pass : . . 1
pending: 0
outcome: T T T
decided: 2 2 2
inf-outcome: T T T
inf-decided: 2 2 2
clause: 16.12.13
class: normative

== lp2
property: s_eventually a
scope: attempt
loop: 1
a : . .
pending: 2
final-fail: 2
outcome: p p
decided: . .
inf-outcome: F F
inf-decided: . .
clause: 16.12.13
class: normative

== atm
property: a |-> ##1 b
scope: attempt
a : 1 . 1
b : . 1 .
pass : . 1 .
pending: 1
final-fail: 0
outcome: T t p
decided: 1 1 .
clause: 16.12.2
class: normative

== lc
property: a
kind: cover
scope: attempt
loop: 0
a : 1 .
pass : 1 .
outcome: T t
decided: 0 1
inf-outcome: T F
inf-decided: 0 .
clause: 16.14.3
class: normative
"""

BY_NAME_CATEGORY = {
    "plain": Category.BOUNDED,
    "first": Category.BOUNDED,
    "att": Category.BOUNDED,
    "cov": Category.COVER,
    "asm": Category.ASSUME,
    "ini": Category.INITIAL,
    "lp": Category.LIVE,
    "lp2": Category.LIVE,
    "atm": Category.BOUNDED,
    "lc": Category.COVER,
}


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> dict[str, TraceCase]:
    path = tmp_path_factory.mktemp("demo") / "demo.trace"
    path.write_text(DEMO, encoding="utf-8")
    return {case.name: case for case in load_cases(path)}


@pytest.fixture(scope="module")
def tables() -> tuple[TraceCase, ...]:
    return tuple(load_all_cases())


@pytest.fixture(scope="module")
def corpus(tables: tuple[TraceCase, ...]) -> dict[str, str]:
    return build_corpus(tables, traces_digest())


def _stem(case: TraceCase) -> str:
    return Path(case.file).stem


def _exported(cases: tuple[TraceCase, ...]) -> list[TraceCase]:
    """Contract point 4: every case of the tables is exported."""
    return list(cases)


def _category(case: TraceCase) -> str:
    """Independent rule of contract point 5 (initial, cover, assume, live, else bounded)."""
    if case.procedure == "initial":
        return "initial"
    if case.kind in ("cover", "assume"):
        return case.kind
    return "live" if case.loop is not None else "bounded"


def _code(text: str) -> str:
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("//"))


def _directive(case: TraceCase) -> str:
    """The directive of point 8, without the ``initial`` wrapper and the semicolon."""
    disable = f"disable iff ({case.disable}) " if case.disable is not None else ""
    return f"p: {case.kind} property (@(posedge clk) {disable}{case.property})"


def _body(text: str) -> list[str]:
    """Lines after the leading generated-file header comment."""
    lines = text.split("\n")
    index = 0
    while index < len(lines) and lines[index].startswith(("#", "//")):
        index += 1
    return lines[index:]


# --- identity and category -------------------------------------------------------------------


def test_case_id_is_trace_stem_slash_case_name(demo: dict[str, TraceCase]) -> None:
    assert case_id(demo["plain"]) == "demo/plain"
    assert case_id(demo["lp"]) == "demo/lp"


@pytest.mark.parametrize(("name", "category"), sorted(BY_NAME_CATEGORY.items()))
def test_category_of_each_kind_of_case(
    demo: dict[str, TraceCase], name: str, category: Category
) -> None:
    assert category_of(demo[name]) is category


def test_category_priority_is_initial_cover_assume_live_bounded(
    demo: dict[str, TraceCase],
) -> None:
    base = demo["lp"]  # has a loop
    assert category_of(replace(base, kind="cover", procedure="initial")) is Category.INITIAL
    assert category_of(replace(base, kind="assume", procedure="initial")) is Category.INITIAL
    assert category_of(replace(base, kind="cover")) is Category.COVER
    assert category_of(replace(base, kind="assume")) is Category.ASSUME
    assert category_of(base) is Category.LIVE
    assert category_of(replace(base, loop=None)) is Category.BOUNDED
    assert category_of(replace(demo["cov"], loop=1)) is Category.COVER


def test_every_category_is_exported_in_step_two() -> None:
    assert frozenset(Category) == EXPORTED_CATEGORIES
    assert {c.value for c in Category} == {"initial", "cover", "assume", "live", "bounded"}


# --- manifest entries ------------------------------------------------------------------------


def test_entry_of_a_default_scope_case(demo: dict[str, TraceCase]) -> None:
    assert entry_for(demo["plain"]) == CaseEntry(
        case_id="demo/plain",
        category=Category.BOUNDED,
        kind="assert",
        scope="default",
        samples=4,
        failures=(2, 3),
        successes=(1,),
        case_class="normative",
        interpretations=(),
        clauses=("16.9.1", "16.12.7"),
        support_oracle=False,
        exported=True,
    )


def test_entry_of_a_first_failure_oracle_interpretation_case(demo: dict[str, TraceCase]) -> None:
    entry = entry_for(demo["first"])
    assert entry.scope == "first-failure"
    assert entry.failures == (1,)
    assert entry.successes == ()
    assert entry.case_class == "interpretation"
    assert entry.interpretations == ("I3", "I5")
    assert entry.clauses == ("16.9.5", "Annex F")
    assert entry.support_oracle is True
    assert entry.exported is True  # oracle cases are still exported (point 4)


def test_entry_of_an_attempt_scope_case(demo: dict[str, TraceCase]) -> None:
    entry = entry_for(demo["att"])
    assert entry.scope == "attempt"
    assert entry.failures == (2,)
    assert entry.case_class == "implementation-defined"
    assert entry.exported is True


@pytest.mark.parametrize("name", ["cov", "asm", "ini", "lp", "lp2", "lc"])
def test_entries_of_every_category_are_exported(demo: dict[str, TraceCase], name: str) -> None:
    entry = entry_for(demo[name])
    assert entry.category is BY_NAME_CATEGORY[name]
    assert entry.exported is True
    assert entry.case_id == f"demo/{name}"
    assert entry.sim is (demo[name].loop is None)  # a lasso has no simulation form (point 30)
    assert entry.loop == demo[name].loop


def test_entry_of_a_lasso_case_has_loop_sim_false_and_infinite(
    demo: dict[str, TraceCase],
) -> None:
    entry = entry_for(demo["lp"])
    assert (entry.loop, entry.sim) == (1, False)
    assert entry.infinite == Infinite(outcomes="TTT", decided=(2, 2, 2))
    undecided = entry_for(demo["lp2"])
    assert undecided.infinite == Infinite(outcomes="FF", decided=(None, None))
    assert entry_for(demo["lc"]).infinite == Infinite(outcomes="TF", decided=(0, None))


def test_entry_without_a_loop_has_no_infinite_part(demo: dict[str, TraceCase]) -> None:
    for name in ("plain", "atm", "cov", "ini"):
        entry = entry_for(demo[name])
        assert (entry.loop, entry.sim, entry.infinite) == (None, True, None)


def test_entry_attempts_come_from_the_per_attempt_rows(demo: dict[str, TraceCase]) -> None:
    assert entry_for(demo["atm"]).attempts == Attempts(
        outcomes="Ttp", decided=(1, 1, None), pending=1, final_fail=0
    )
    assert entry_for(demo["lp"]).attempts == Attempts(
        outcomes="TTT", decided=(2, 2, 2), pending=0, final_fail=None
    )
    assert entry_for(demo["lp2"]).attempts == Attempts(
        outcomes="pp", decided=(None, None), pending=2, final_fail=2
    )


def test_entry_attempts_are_absent_without_per_attempt_or_end_rows(
    demo: dict[str, TraceCase],
) -> None:
    for name in ("plain", "first", "att", "cov", "asm", "ini"):
        assert entry_for(demo[name]).attempts is None, name


def test_entry_kind_and_sample_count(demo: dict[str, TraceCase]) -> None:
    assert entry_for(demo["cov"]).kind == "cover"
    assert entry_for(demo["asm"]).kind == "assume"
    assert entry_for(demo["cov"]).successes == (1,)
    assert entry_for(demo["lp"]).samples == 3


# --- CSV -------------------------------------------------------------------------------------


def test_csv_has_header_and_decimal_rows(demo: dict[str, TraceCase]) -> None:
    text = render_csv(demo["plain"])
    assert "\r" not in text
    assert text.endswith("\n")
    lines = [line for line in _body(text) if line]
    assert lines == [
        "sample,a,b,v,fail,pass",
        "0,1,0,0,0,0",
        "1,0,1,255,0,1",
        "2,0,0,16,1,0",
        "3,1,0,3,1,0",
    ]


def test_csv_of_a_first_failure_case_has_a_zero_pass_column(demo: dict[str, TraceCase]) -> None:
    lines = [line for line in _body(render_csv(demo["first"])) if line]
    assert lines == ["sample,a,b,r,fail,pass", "0,1,0,0,0,0", "1,1,1,0,1,0", "2,0,0,1,0,0"]


def test_csv_of_a_lasso_case_adds_repeat_and_outcome_columns(demo: dict[str, TraceCase]) -> None:
    lines = [line for line in _body(render_csv(demo["lp"])) if line]
    assert lines == [
        "sample,a,fail,pass,repeat,outcome",
        "0,0,0,0,0,T",
        "1,0,0,0,1,T",
        "2,1,0,1,1,T",
    ]
    lines = [line for line in _body(render_csv(demo["lc"])) if line]
    assert lines == ["sample,a,fail,pass,repeat,outcome", "0,1,0,1,1,T", "1,0,0,0,1,F"]


def test_csv_of_a_case_without_a_loop_has_no_repeat_or_outcome(
    demo: dict[str, TraceCase],
) -> None:
    for name in ("plain", "atm", "cov", "ini"):
        header = _body(render_csv(demo[name]))[0]
        assert header.endswith(",fail,pass"), name
        assert "repeat" not in header and "outcome" not in header


# --- WaveDrom --------------------------------------------------------------------------------


def _expand(wave: str, data: list[str]) -> list[str | None]:
    """The per-sample tokens of a WaveDrom wave ('.' repeats, '=' consumes a data label)."""
    tokens: list[str | None] = []
    labels = iter(data)
    for char in wave:
        if char == ".":
            tokens.append(tokens[-1])
        elif char == "=":
            tokens.append(next(labels))
        else:
            tokens.append(char)
    return tokens


def _signals(text: str) -> dict[str, dict[str, object]]:
    document = json.loads(text)
    assert isinstance(document, dict)
    entries = document["signal"]
    assert isinstance(entries, list)
    return {str(entry["name"]): entry for entry in entries}


def test_wavedrom_is_valid_json_with_clock_signals_and_expected_rows(
    demo: dict[str, TraceCase],
) -> None:
    signals = _signals(render_wavedrom(demo["plain"]))
    assert {"clk", "a", "b", "v", "fail", "pass"} <= set(signals)


def test_wavedrom_one_bit_waves_write_a_value_only_where_it_changes(
    demo: dict[str, TraceCase],
) -> None:
    signals = _signals(render_wavedrom(demo["plain"]))
    # an unchanged value is "." (keep the state): a repeated "0" would draw a spike
    assert signals["a"]["wave"] == "10.1"
    assert signals["b"]["wave"] == "010."
    assert signals["fail"]["wave"] == "0.1."
    assert signals["pass"]["wave"] == "010."
    for name in ("a", "b", "fail", "pass"):
        wave = signals[name]["wave"]
        assert isinstance(wave, str)
        assert set(wave) <= {"0", "1", "."}
        assert wave[0] in "01"
        assert all(  # a value is repeated explicitly never: it would draw a glitch
            not (wave[k] in "01" and _expand(wave, [])[k] == _expand(wave, [])[k - 1])
            for k in range(1, len(wave))
        )


def test_wavedrom_wide_signal_is_a_data_wave_labelled_in_hex(
    demo: dict[str, TraceCase],
) -> None:
    entry = _signals(render_wavedrom(demo["plain"]))["v"]
    wave = entry["wave"]
    data = entry["data"]
    assert isinstance(wave, str)
    assert isinstance(data, list)
    assert set(wave) <= {"=", "."}
    tokens = _expand(wave, [str(label) for label in data])
    assert [int(str(t), 16) for t in tokens] == [0, 255, 16, 3]


def _labels(entry: dict[str, object]) -> list[str]:
    data = entry["data"]
    assert isinstance(data, list)
    return [str(label) for label in data]


def test_wavedrom_of_a_lasso_case_has_repeat_and_outcome_lanes(
    demo: dict[str, TraceCase],
) -> None:
    signals = _signals(render_wavedrom(demo["lp"]))
    assert {"clk", "a", "fail", "pass", "repeat", "outcome"} <= set(signals)
    # a value only where it changes: 0 before the loop sample, 1 from it
    assert signals["repeat"]["wave"] == "01."
    assert "phase" not in signals["repeat"]
    outcome = signals["outcome"]
    assert "phase" not in outcome
    wave = outcome["wave"]
    assert isinstance(wave, str)
    assert _expand(wave, _labels(outcome)) == ["T", "T", "T"]
    assert wave.count("=") == 1  # one box per run of equal outcomes
    mixed = _signals(render_wavedrom(demo["lc"]))
    wave = mixed["outcome"]["wave"]
    assert isinstance(wave, str)
    assert _expand(wave, _labels(mixed["outcome"])) == ["T", "F"]
    assert mixed["repeat"]["wave"] == "1."  # loop = 0: repeating from the first sample


def test_wavedrom_of_a_case_without_a_loop_has_no_repeat_or_outcome(
    demo: dict[str, TraceCase],
) -> None:
    for name in ("plain", "atm", "cov", "ini"):
        assert not {"repeat", "outcome"} & set(_signals(render_wavedrom(demo[name]))), name


# --- manifest --------------------------------------------------------------------------------


def _entries(demo: dict[str, TraceCase]) -> tuple[CaseEntry, ...]:
    return tuple(entry_for(demo[name]) for name in ("plain", "first", "cov"))


def _manifest(text: str) -> dict[str, object]:
    document = json.loads(text)
    assert isinstance(document, dict)
    return document


def _case_list(document: dict[str, object]) -> list[dict[str, object]]:
    lists = [value for value in document.values() if isinstance(value, list)]
    assert len(lists) == 1, "the manifest has exactly one list: the cases"
    return [dict(item) for item in lists[0]]


def test_manifest_is_sorted_deterministic_lf_and_valid_json(demo: dict[str, TraceCase]) -> None:
    entries = _entries(demo)
    text = render_manifest(entries, "ab" * 32)
    assert text == render_manifest(entries, "ab" * 32)
    assert text == render_manifest(tuple(reversed(entries)), "ab" * 32)  # sorted by the renderer
    assert "\r" not in text
    assert text.endswith("\n")
    assert "\n" in text.strip()  # indented
    ids = [str(item["id"]) for item in _case_list(_manifest(text))]
    assert ids == sorted(ids) == ["demo/cov", "demo/first", "demo/plain"]


def test_manifest_top_level_has_digest_and_counts(demo: dict[str, TraceCase]) -> None:
    document = _manifest(render_manifest(_entries(demo), "cd" * 32))
    assert document["traces-sha256"] == "cd" * 32
    assert document["case-count"] == 3
    assert document["exported-count"] == 3  # plain, first and cov: every case is exported


def test_manifest_entry_fields(demo: dict[str, TraceCase]) -> None:
    items = {str(i["id"]): i for i in _case_list(_manifest(render_manifest(_entries(demo), "0")))}
    first = items["demo/first"]
    assert first.get("infinite") is None  # absent keys are null (point 31)
    assert first.get("attempts") is None
    assert {k: v for k, v in first.items() if k not in ("infinite", "attempts")} == {
        "id": "demo/first",
        "category": "bounded",
        "kind": "assert",
        "scope": "first-failure",
        "samples": 3,
        "failures": [1],
        "successes": [],
        "class": "interpretation",
        "interpretations": ["I3", "I5"],
        "clauses": ["16.9.5", "Annex F"],
        "support_oracle": True,
        "exported": True,
        "loop": None,
        "sim": True,
    }
    cover = items["demo/cov"]
    assert cover["category"] == "cover"
    assert cover["kind"] == "cover"
    assert cover["successes"] == [1]
    assert cover["failures"] == []
    assert cover["exported"] is True


def _lasso_items(demo: dict[str, TraceCase]) -> dict[str, dict[str, object]]:
    entries = tuple(entry_for(demo[name]) for name in ("lp", "lp2", "atm", "lc"))
    return {str(i["id"]): i for i in _case_list(_manifest(render_manifest(entries, "0")))}


def test_manifest_entry_of_a_lasso_case_has_loop_sim_infinite_and_attempts(
    demo: dict[str, TraceCase],
) -> None:
    items = _lasso_items(demo)
    lasso = items["demo/lp"]
    assert (lasso["loop"], lasso["sim"], lasso["category"]) == (1, False, "live")
    assert lasso["infinite"] == {"outcomes": "TTT", "decided": [2, 2, 2]}
    assert lasso["attempts"] == {
        "outcomes": "TTT",
        "decided": [2, 2, 2],
        "pending": 0,
        "final_fail": None,
    }
    open_ended = items["demo/lp2"]
    assert open_ended["infinite"] == {"outcomes": "FF", "decided": [None, None]}  # null
    assert open_ended["attempts"] == {
        "outcomes": "pp",
        "decided": [None, None],
        "pending": 2,
        "final_fail": 2,
    }
    assert items["demo/lc"]["infinite"] == {"outcomes": "TF", "decided": [0, None]}
    assert items["demo/lc"]["sim"] is False


def test_manifest_entry_of_an_attempt_case_without_a_loop(demo: dict[str, TraceCase]) -> None:
    item = _lasso_items(demo)["demo/atm"]
    assert (item["loop"], item["sim"]) == (None, True)
    assert item.get("infinite") is None
    assert item["attempts"] == {
        "outcomes": "Ttp",
        "decided": [1, 1, None],
        "pending": 1,
        "final_fail": 0,
    }


def test_manifest_attempt_keys_absent_from_the_table_are_null(
    demo: dict[str, TraceCase],
) -> None:
    case = replace(demo["atm"], pending=None, final_fail=None)
    items = _case_list(_manifest(render_manifest((entry_for(case),), "0")))
    assert items[0]["attempts"] == {
        "outcomes": "Ttp",
        "decided": [1, 1, None],
        "pending": None,
        "final_fail": None,
    }


# --- the whole corpus ------------------------------------------------------------------------


def test_two_builds_are_byte_identical(tables: tuple[TraceCase, ...]) -> None:
    first = build_corpus(tables, "00")
    second = build_corpus(tuple(tables), "00")
    assert first == second
    assert list(first) == list(second)


def test_corpus_keys_follow_the_layout(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    expected = {"manifest.json"}
    for case in _exported(tables):
        base = f"{_category(case)}/{_stem(case)}/{case.name}"
        expected |= {
            f"formal/{base}.sv",
            f"traces/csv/{base}.csv",
            f"traces/wavedrom/{base}.json",
        }
        if case.loop is None:  # a lasso has no simulation form (point 30)
            expected.add(f"sim/{base}.sv")
    assert set(corpus) == expected


def test_every_category_has_files_and_a_sim_file_exists_exactly_for_non_lasso_cases(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    roots = {"sim", "formal", "traces/csv", "traces/wavedrom"}
    categories: set[str] = set()
    for key in corpus:
        if key == "manifest.json":
            continue
        root = next(r for r in roots if key.startswith(r + "/"))
        categories.add(key[len(root) + 1 :].split("/")[0])
    assert categories == {"bounded", "cover", "assume", "initial", "live"}
    sims = {k for k in corpus if k.startswith("sim/")}
    assert len(sims) == sum(1 for c in tables if c.loop is None)
    assert not any(k.startswith("sim/live/") for k in corpus)  # every live case is a lasso


def test_readme_and_license_are_not_generated(corpus: dict[str, str]) -> None:
    assert "README.md" not in corpus
    assert "LICENSE" not in corpus


def test_every_case_appears_exactly_once_in_the_manifest(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    items = _case_list(_manifest(corpus["manifest.json"]))
    ids = [str(item["id"]) for item in items]
    assert len(ids) == len(set(ids))
    assert sorted(ids) == sorted(f"{_stem(c)}/{c.name}" for c in tables)
    assert ids == sorted(ids)


def test_manifest_counts_are_the_independent_counts_of_the_tables(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    document = _manifest(corpus["manifest.json"])
    items = _case_list(document)
    assert all(item["exported"] is True for item in items)
    assert document["case-count"] == len(tables) == len(items)
    assert document["exported-count"] == len(_exported(tables))
    assert len(items) > 600
    by_category: dict[str, int] = {}
    for case in tables:
        by_category[_category(case)] = by_category.get(_category(case), 0) + 1
    manifest_counts: dict[str, int] = {}
    for item in items:
        name = str(item["category"])
        manifest_counts[name] = manifest_counts.get(name, 0) + 1
    assert manifest_counts == by_category
    assert set(by_category) == {"bounded", "cover", "assume", "initial", "live"}
    assert sum(1 for item in items if item["sim"] is False) == sum(
        1 for c in tables if c.loop is not None
    )
    assert document["traces-sha256"] == traces_digest()


def test_manifest_expectations_come_from_the_table_rows(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    items = {str(i["id"]): i for i in _case_list(_manifest(corpus["manifest.json"]))}
    for case in tables:
        item = items[f"{_stem(case)}/{case.name}"]
        assert item["failures"] == sorted(case.fails)
        assert item["successes"] == sorted(case.passes)
        assert item["samples"] == len(case.samples)
        assert item["support_oracle"] is case.oracle_only
        scope = (
            "first-failure"
            if case.first_failure_only
            else "attempt"
            if case.attempt_scope
            else "default"
        )
        assert item["scope"] == scope
        assert item["kind"] == case.kind
        assert item["category"] == _category(case)
        assert item["loop"] == case.loop
        assert item["sim"] is (case.loop is None)
        infinite = item.get("infinite")
        if case.loop is None:
            assert infinite is None, case.name
        else:
            assert case.inf_outcomes is not None
            assert infinite == {
                "outcomes": "".join(case.inf_outcomes),
                "decided": list(case.inf_decisions or ()),
            }, case.name
        attempts = item.get("attempts")
        if case.outcomes is None and case.pending is None and case.final_fail is None:
            assert attempts is None, case.name
        else:
            outcomes = None if case.outcomes is None else "".join(case.outcomes)
            decided = None if case.decisions is None else list(case.decisions)
            if case.procedure is not None:  # one native attempt: the one at sample 0
                outcomes = None if outcomes is None else outcomes[:1]
                decided = None if decided is None else decided[:1]
            assert attempts == {
                "outcomes": outcomes,
                "decided": decided,
                "pending": case.pending,
                "final_fail": case.final_fail,
            }, case.name


def test_every_generated_sv_file_says_it_is_generated(corpus: dict[str, str]) -> None:
    """CSV and JSON have no comment syntax: the manifest names the generator instead."""
    unmarked: list[str] = []
    for key, text in corpus.items():
        if not key.endswith(".sv"):
            continue
        first = text.split("\n", 1)[0]
        if not (
            first.startswith(("//", "#"))
            and "generated" in first.lower()
            and "do not edit" in first.lower()
        ):
            unmarked.append(key)
    assert not unmarked, unmarked[:5]


def test_generated_files_are_utf8_with_lf_and_no_trailing_noise(corpus: dict[str, str]) -> None:
    for key, text in corpus.items():
        assert "\r" not in text, key
        assert text.endswith("\n"), key
        text.encode("utf-8")


def test_every_wavedrom_file_is_valid_json_with_the_expected_rows(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    for case in _exported(tables):
        base = f"{_category(case)}/{_stem(case)}/{case.name}"
        signals = _signals(corpus[f"traces/wavedrom/{base}.json"])
        assert {"clk", "fail", "pass", *case.widths} <= set(signals), case.name
        assert ({"repeat", "outcome"} <= set(signals)) is (case.loop is not None), case.name
        if case.loop is not None:
            repeat = signals["repeat"]["wave"]
            assert isinstance(repeat, str)
            assert [int(str(t)) for t in _expand(repeat, [])] == [
                int(k >= case.loop) for k in range(len(case.samples))
            ], case.name
            outcome = signals["outcome"]
            wave = outcome["wave"]
            assert isinstance(wave, str)
            assert case.inf_outcomes is not None
            assert _expand(wave, _labels(outcome)) == list(case.inf_outcomes), case.name
        for name, width in case.widths.items():
            if width == 1:
                wave = signals[name]["wave"]
                assert isinstance(wave, str)
                values = [sample[name] for sample in case.samples]
                if name in trace_driver.disable_only_signals(case):
                    values = [
                        values[0],
                        *values[:-1],
                    ]  # drawn right after the edge (one cell later)
                assert [int(str(t)) for t in _expand(wave, [])] == values, (case.name, name)
        fail = signals["fail"]["wave"]
        assert isinstance(fail, str)
        expanded = _expand(fail, [])
        assert [i for i, t in enumerate(expanded) if t == "1"] == sorted(case.fails), case.name


def test_every_csv_row_repeats_the_table(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    for case in _exported(tables):
        base = f"{_category(case)}/{_stem(case)}/{case.name}"
        lines = [x for x in _body(corpus[f"traces/csv/{base}.csv"]) if x]
        header = "sample," + ",".join(case.widths) + ",fail,pass"
        lasso = case.loop is not None
        assert lines[0] == header + (",repeat,outcome" if lasso else ""), case.name
        assert len(lines) == len(case.samples) + 1, case.name
        for k, row in enumerate(case.samples):
            values = [str(k), *(str(row[n]) for n in case.widths)]
            values += [str(int(k in case.fails)), str(int(k in case.passes))]
            if case.loop is not None:
                assert case.inf_outcomes is not None
                values += [str(int(k >= case.loop)), case.inf_outcomes[k]]
            assert lines[k + 1] == ",".join(values), (case.name, k)


def test_sim_and_formal_forms_have_the_documented_shape(demo: dict[str, TraceCase]) -> None:
    case = demo["plain"]
    sim, formal = render_sim(case), render_formal(case)
    for text in (sim, formal):
        assert re.search(r"\bmodule\s+sva_case\b", text)
        assert "a |-> ##1 b" in text
    assert re.search(r"\bmodule\s+tb\b", sim)
    assert re.search(r"\bdone\b", sim)
    assert "SVA_FAIL demo/plain sample=" in sim
    assert "SVA_PASS demo/plain sample=" in sim
    code = "\n".join(line for line in formal.split("\n") if not line.lstrip().startswith("//"))
    assert "$display" not in code
    assert not re.search(r"\b(done|tb)\b", code)
    assert not re.search(r"\bassume\b", code)


# --- directive per kind, initial wrapper, sim actions per kind (points 8, 27-29) ----------


def test_directive_of_each_kind_in_both_forms(demo: dict[str, TraceCase]) -> None:
    for name, kind in (("plain", "assert"), ("cov", "cover"), ("asm", "assume")):
        case = demo[name]
        assert case.kind == kind
        directive = _directive(case)
        assert directive.startswith(f"p: {kind} property (@(posedge clk) ")
        assert f"  {directive};\n" in render_formal(case)
        assert directive in render_sim(case)
    # the table's disable iff is part of the directive, verbatim
    assert "p: assert property (@(posedge clk) disable iff (!r) a and b);" in render_formal(
        demo["first"]
    )


def test_initial_wrapper_in_both_forms(demo: dict[str, TraceCase]) -> None:
    case = demo["ini"]
    assert "  initial p: assert property (@(posedge clk) a);" in render_formal(case)
    sim = render_sim(case)
    assert "initial p: assert property (@(posedge clk) a)" in sim
    assert 'SVA_PASS demo/ini sample=%0d", $sampled(tick));' in sim
    assert 'else $display("SVA_FAIL demo/ini sample=%0d", $sampled(tick));' in sim
    for name in ("plain", "cov", "asm"):  # no wrapper without `procedure: initial`
        assert "initial p:" not in render_formal(demo[name])
        assert "initial p:" not in render_sim(demo[name])
    wrapped_cover = replace(demo["cov"], procedure="initial")
    assert "  initial p: cover property (@(posedge clk) a ##1 b);" in render_formal(wrapped_cover)
    assert "initial p: cover property (" in render_sim(wrapped_cover)
    assert category_of(wrapped_cover) is Category.INITIAL


def test_cover_sim_form_prints_one_cover_line_and_no_pass_or_fail(
    demo: dict[str, TraceCase],
) -> None:
    sim = render_sim(demo["cov"])
    assert re.search(
        r"cover property \(@\(posedge clk\) a ##1 b\)\s*"
        r'\$display\("SVA_COVER demo/cov sample=%0d", \$sampled\(tick\)\);',
        sim,
    )
    code = _code(sim)
    assert len(re.findall(r"\$display", code)) == 1
    assert "SVA_FAIL" not in code and "SVA_PASS" not in code
    assert "else" not in code  # a cover has no failure action
    register = render_sim(demo["cov"], Stimulus.STRING, SampleIndex.REGISTER)
    assert 'SVA_COVER demo/cov sample=%0d", sample_idx);' in register
    assert "SVA_COVER" not in render_formal(demo["cov"])


def test_assume_sim_form_reports_violations_like_an_assertion(demo: dict[str, TraceCase]) -> None:
    sim = render_sim(demo["asm"])
    assert re.search(r"\bp: assume property \(@\(posedge clk\) a\)", sim)
    assert 'SVA_PASS demo/asm sample=%0d", $sampled(tick));' in sim
    assert 'else $display("SVA_FAIL demo/asm sample=%0d", $sampled(tick));' in sim
    code = _code(sim)
    assert not re.search(r"\bassert\b", code)
    formal = _code(render_formal(demo["asm"]))
    assert len(re.findall(r"\bassume\s+property\b", formal)) == 1
    assert not re.search(r"\b(assert|cover)\b", formal)
    assert "$display" not in formal


# --- lasso: the phase driver (point 30) ----------------------------------------------------------


def test_lasso_formal_form_uses_the_phase_driver_without_counter_or_done(
    demo: dict[str, TraceCase],
) -> None:
    for name in ("lp", "lp2", "lc"):
        case = demo[name]
        text = render_formal(case)
        for line in trace_driver.phase_lines(case):
            assert line in text.split("\n"), (name, line)
        for line in trace_driver.string_stimulus_lines(case, "phase"):
            assert line in text.split("\n"), (name, line)
        code = _code(text)
        assert not re.search(r"\btick\b", code), name
        assert not re.search(r"\bdone\b", code), name
        assert "$display" not in code and not re.search(r"\bmodule\s+tb\b", code), name
        assert re.search(r"\bmodule\s+sva_case\s*\(\s*input\s+logic\s+clk\s*\)\s*;", code)
        assert f"  {_directive(case)};" in text


def test_lasso_ternary_formal_form_selects_by_phase(demo: dict[str, TraceCase]) -> None:
    text = render_formal(demo["lp"], Stimulus.TERNARY)
    code = _code(text)
    assert "(phase == " in code
    assert not re.search(r"\btick\b", code)
    for line in trace_driver.phase_lines(demo["lp"]):
        assert line in text.split("\n")


def test_a_non_lasso_formal_form_keeps_the_counter_and_has_no_phase(
    demo: dict[str, TraceCase],
) -> None:
    for name in ("plain", "cov", "asm", "ini", "atm"):
        code = _code(render_formal(demo[name]))
        assert not re.search(r"\bphase\b", code), name
        assert "tick" in code


def test_the_corpus_has_no_sim_file_exactly_for_the_lasso_cases(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    for case in tables:
        base = f"{_category(case)}/{_stem(case)}/{case.name}"
        assert (f"sim/{base}.sv" in corpus) is (case.loop is None), case.name
        assert f"formal/{base}.sv" in corpus, case.name


def test_every_corpus_directive_has_its_kind_and_initial_wrapper(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    for case in tables:
        base = f"{_category(case)}/{_stem(case)}/{case.name}"
        wrapper = "initial " if case.procedure == "initial" else ""
        formal = corpus[f"formal/{base}.sv"]
        assert f"  {wrapper}{_directive(case)};\n" in formal, case.name
        assert len(re.findall(r"\b(?:assert|assume|cover)\s+property\b", _code(formal))) == 1
        if case.loop is None:
            sim = corpus[f"sim/{base}.sv"]
            assert f"{wrapper}{_directive(case)}" in sim, case.name
            cid = f"{_stem(case)}/{case.name}"
            if case.kind == "cover":
                assert f"SVA_COVER {cid} sample=" in sim, case.name
                assert "SVA_FAIL" not in _code(sim) and "SVA_PASS" not in _code(sim)
            else:
                assert f"SVA_FAIL {cid} sample=" in sim and f"SVA_PASS {cid} sample=" in sim
                assert "SVA_COVER" not in _code(sim)


# --- the on-demand ternary stimulus ------------------------------------------------------------


def test_the_ternary_corpus_has_the_same_files_as_the_string_corpus(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    ternary = build_corpus(tables, "00", Stimulus.TERNARY)
    assert set(ternary) == set(corpus)


def test_ternary_files_have_no_string_rows_and_say_which_stimulus_they_use(
    tables: tuple[TraceCase, ...],
) -> None:
    ternary = build_corpus(tables, "00", Stimulus.TERNARY)
    for key, text in ternary.items():
        if key.endswith(".sv"):
            code = "\n".join(
                line for line in text.split("\n") if not line.lstrip().startswith("//")
            )
            assert "ROW_" not in code, key
            assert "row_idx" not in code, key
            assert "?" in code or "wire" not in code, key
    assert json.loads(ternary["manifest.json"])["stimulus"] == "ternary"
    assert json.loads(build_corpus(tables, "00")["manifest.json"])["stimulus"] == "string"


def test_ternary_formal_form_has_only_the_property_and_the_driver(
    demo: dict[str, TraceCase],
) -> None:
    text = render_formal(demo["plain"], Stimulus.TERNARY)
    assert "wire [0:0] a = (tick == " in text
    assert "wire [7:0] v = (tick == " in text
    assert "a |-> ##1 b" in text
    assert "$display" not in text


def test_the_committed_corpus_cannot_be_written_in_the_ternary_form() -> None:
    with pytest.raises(ValueError, match="committed values only"):
        write_corpus(CORPUS_DIRECTORY, Stimulus.TERNARY)


def test_the_command_line_refuses_ternary_without_another_root_and_without_write(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit):
        main(["--write", "--stimulus", "ternary"])
    with pytest.raises(SystemExit):
        main(["--check", "--stimulus", "ternary", "--root", str(tmp_path)])


def test_the_command_line_writes_the_ternary_corpus_to_another_root(tmp_path: Path) -> None:
    assert main(["--write", "--stimulus", "ternary", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "manifest.json").is_file()
    assert any((tmp_path / "formal").rglob("*.sv"))


def _formal_key(case: TraceCase) -> str:
    return f"formal/{_category(case)}/{_stem(case)}/{case.name}.sv"


def test_the_default_corpus_writes_the_long_cases_in_the_ternary_form(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    ternary = build_corpus(tables, "00", Stimulus.TERNARY)
    long_cases = [case for case in tables if len(case.samples) >= TERNARY_FROM]
    assert TERNARY_FROM == 30
    assert [case.name for case in long_cases] == [
        "ieee_16_7_req_range_4_to_32_latest",
        "ieee_16_7_req_range_4_to_32_too_early_and_too_late",
    ]
    for case in tables:
        key = _formal_key(case)
        if len(case.samples) >= TERNARY_FROM:
            assert corpus[key] == ternary[key], key
        else:
            assert "ROW_" in corpus[key] or not case.samples, key


def test_the_threshold_applies_to_the_simulation_form_and_leaves_the_data_forms_alone(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    ternary = build_corpus(tables, "00", Stimulus.TERNARY)
    for case in tables:
        if len(case.samples) >= TERNARY_FROM and case.loop is None:
            key = _formal_key(case).replace("formal/", "sim/")
            assert corpus[key] == ternary[key], key
    for key in corpus:
        if key.startswith("traces/"):
            assert corpus[key] == ternary[key], key


def test_a_lower_threshold_writes_more_cases_in_the_ternary_form(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    ternary = build_corpus(tables, "00", Stimulus.TERNARY)
    mixed = build_corpus(tables, "00", ternary_from=14)
    assert set(mixed) == set(corpus)
    for case in tables:
        key = _formal_key(case)
        expected = ternary if len(case.samples) >= 14 else corpus
        assert mixed[key] == expected[key], key


def test_a_threshold_above_every_trace_or_never_gives_the_string_corpus(
    tables: tuple[TraceCase, ...], corpus: dict[str, str]
) -> None:
    pure = build_corpus(tables, "00", ternary_from=None)
    high = build_corpus(tables, "00", ternary_from=max(len(c.samples) for c in tables) + 1)
    assert {k: v for k, v in pure.items() if k != "manifest.json"} == {
        k: v for k, v in high.items() if k != "manifest.json"
    }
    for case in tables:
        assert "ROW_" in pure[_formal_key(case)] or not case.samples


def test_the_manifest_records_the_threshold(tables: tuple[TraceCase, ...]) -> None:
    default = json.loads(build_corpus(tables, "00")["manifest.json"])
    assert default["stimulus"] == "string" and default["ternary-from"] == 30
    never = json.loads(build_corpus(tables, "00", ternary_from=None)["manifest.json"])
    assert never["ternary-from"] is None
    low = json.loads(build_corpus(tables, "00", ternary_from=5)["manifest.json"])
    assert low["ternary-from"] == 5


def test_the_committed_corpus_is_written_with_the_committed_values_only() -> None:
    with pytest.raises(ValueError, match="committed values only"):
        write_corpus(CORPUS_DIRECTORY, ternary_from=14)
    with pytest.raises(ValueError, match="committed values only"):
        write_corpus(CORPUS_DIRECTORY, ternary_from=None)


def test_the_command_line_guards_the_threshold_like_the_other_forms(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--write", "--ternary-from", "14"])
    with pytest.raises(SystemExit):
        main(["--check", "--ternary-from", "14", "--root", str(tmp_path)])
    for bad in ("0", "-3", "x"):
        with pytest.raises(SystemExit):
            main(["--write", "--ternary-from", bad, "--root", str(tmp_path)])


def test_the_command_line_writes_a_corpus_with_another_threshold(tmp_path: Path) -> None:
    assert main(["--write", "--ternary-from", "never", "--root", str(tmp_path)]) == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ternary-from"] is None
    long_file = next((tmp_path / "formal").rglob("ieee_16_7_req_range_4_to_32_latest.sv"))
    assert "ROW_" in long_file.read_text(encoding="utf-8")


# --- the sample-index option -------------------------------------------------------------------


def test_the_default_sim_form_prints_the_sampled_counter(demo: dict[str, TraceCase]) -> None:
    text = render_sim(demo["plain"])
    assert 'sample=%0d", $sampled(tick));' in text
    assert "sample_idx" not in text
    assert "negedge" not in text


def test_the_register_variant_copies_the_counter_at_the_falling_edge(
    demo: dict[str, TraceCase],
) -> None:
    text = render_sim(demo["plain"], Stimulus.STRING, SampleIndex.REGISTER)
    assert "always @(negedge clk) sample_idx <= tick;" in text
    assert 'sample=%0d", sample_idx);' in text
    assert "$sampled" not in "\n".join(
        line for line in text.split("\n") if not line.lstrip().startswith("//")
    )


def test_the_formal_form_does_not_depend_on_the_sample_index_option(
    tables: tuple[TraceCase, ...],
) -> None:
    sampled = build_corpus(tables, "00")
    register = build_corpus(tables, "00", Stimulus.STRING, SampleIndex.REGISTER)
    for key, text in sampled.items():
        if key.startswith(("formal/", "traces/")):
            assert register[key] == text, key
    assert json.loads(register["manifest.json"])["sample-index"] == "register"
    assert json.loads(sampled["manifest.json"])["sample-index"] == "sampled"


def test_the_committed_corpus_cannot_be_written_with_the_register_variant() -> None:
    with pytest.raises(ValueError, match="committed values only"):
        write_corpus(CORPUS_DIRECTORY, Stimulus.STRING, SampleIndex.REGISTER)


def test_the_command_line_guards_the_register_variant_like_the_ternary_one(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit):
        main(["--write", "--sample-index", "register"])
    with pytest.raises(SystemExit):
        main(["--check", "--sample-index", "register", "--root", str(tmp_path)])
    assert main(["--write", "--sample-index", "register", "--root", str(tmp_path)]) == 0
    assert "sample_idx" in next((tmp_path / "sim").rglob("*.sv")).read_text(encoding="utf-8")


def test_wavedrom_cell_k_ends_at_the_sampling_edge_k(demo: dict[str, TraceCase]) -> None:
    """Textbook drawing: a low first cell, then a rising edge at the start of every later cell:
    edge k is the end of cell k. The last edge is at the right border and is not drawn."""
    document = json.loads(render_wavedrom(demo["plain"]))  # four samples
    lanes = {str(entry["name"]): entry for entry in document["signal"]}
    assert lanes["clk"]["wave"] == "lp.."  # edges 0, 1, 2 drawn; edge 3 closes the last cell
    for name, lane in lanes.items():
        assert "phase" not in lane, name  # no shift: the cells are the samples
        if name != "clk":
            assert len(_expand(str(lane["wave"]), [str(x) for x in lane.get("data", [])])) == 4
    assert document["head"] == {"text": "demo/plain", "tock": 0}  # labels in the cells = samples


def test_wavedrom_clock_of_a_one_sample_case_has_no_edge(demo: dict[str, TraceCase]) -> None:
    from dataclasses import replace

    one = replace(
        demo["plain"], samples=demo["plain"].samples[:1], fails=frozenset(), passes=frozenset()
    )
    clock = next(e for e in json.loads(render_wavedrom(one))["signal"] if e["name"] == "clk")
    assert clock["wave"] == "l"


def test_wavedrom_merges_a_repeated_wide_value_into_one_box(demo: dict[str, TraceCase]) -> None:
    from dataclasses import replace

    case = demo["plain"]
    repeated = replace(
        case,
        samples=tuple(
            {**row, "v": value} for row, value in zip(case.samples, (5, 5, 7, 7), strict=True)
        ),
    )
    lane = next(
        entry for entry in json.loads(render_wavedrom(repeated))["signal"] if entry["name"] == "v"
    )
    assert lane["wave"] == "=.=."
    assert lane["data"] == ["05", "07"]


# --- review findings: initial attempts, end-of-simulation rows, safe deletion ----------


INITIAL_AND_END = """\
== init_one
property: a |-> ##1 b
procedure: initial
scope: attempt
a : 1 1 1 1
b : . 1 . .
outcome : T t t t
decided : 1 1 2 3
pass : . 1 . .
pending: 0
final-fail: 0
clause: 16.14.6
class: normative

== end_only
property: strong(a ##1 b)
a : . 1
b : . .
pending: 1
final-fail: 1
clause: 16.7
class: normative
"""


def test_an_initial_directive_has_one_native_attempt(tmp_path: Path) -> None:
    path = tmp_path / "ie.trace"
    path.write_text(INITIAL_AND_END, encoding="utf-8")
    case = {c.name: c for c in load_cases(path)}["init_one"]
    assert entry_for(case).attempts == Attempts(outcomes="T", decided=(1,), pending=0, final_fail=0)


def test_end_of_simulation_results_are_kept_without_an_outcome_row(tmp_path: Path) -> None:
    path = tmp_path / "ie.trace"
    path.write_text(INITIAL_AND_END, encoding="utf-8")
    case = {c.name: c for c in load_cases(path)}["end_only"]
    assert entry_for(case).attempts == Attempts(
        outcomes=None, decided=None, pending=1, final_fail=1
    )
    text = render_manifest((entry_for(case),), "00")
    item = json.loads(text)["cases"][0]
    assert item["attempts"] == {"outcomes": None, "decided": None, "pending": 1, "final_fail": 1}


def test_write_corpus_never_deletes_files_that_are_not_generated(tmp_path: Path) -> None:
    root = tmp_path / "dest"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (root / "notes.txt").write_text("keep\n", encoding="utf-8")
    (root / "sim" / "other").mkdir(parents=True)
    (root / "sim" / "other" / "mine.sv").write_text("// mine\n", encoding="utf-8")
    (root / "sim" / "bounded" / "and").mkdir(parents=True)
    (root / "sim" / "bounded" / "and" / "mine.txt").write_text("keep\n", encoding="utf-8")
    (root / "empty_dir").mkdir()
    write_corpus(root)
    assert (root / ".git" / "config").read_text(encoding="utf-8") == "[core]\n"
    assert (root / "notes.txt").is_file()
    assert (root / "sim" / "other" / "mine.sv").is_file()
    assert (root / "sim" / "bounded" / "and" / "mine.txt").is_file()
    assert (root / "empty_dir").is_dir()
    assert check_corpus(root) == []  # files that are not generated are not the corpus's business


# --- disable iff: the saved counter and the drawing ---------------------------------------------


def test_a_disable_case_declares_the_saved_counter_in_both_forms(
    demo: dict[str, TraceCase],
) -> None:
    for text in (render_formal(demo["first"]), render_sim(demo["first"])):
        assert "logic [2:0] tick_prev = 0;" in text
        assert "always @(posedge clk) tick_prev <= tick;" in text
        assert re.search(r"wire r = \(ROW_r\[8\*\(\d+-2\*row_idx_prev\)", text)
        assert re.search(r"wire a = \(ROW_a\[8\*\(\d+-2\*row_idx\)", text)
    assert "tick_prev" not in render_formal(demo["plain"])


def test_a_lasso_with_disable_uses_a_saved_phase(tmp_path: Path) -> None:
    path = tmp_path / "ld.trace"
    path.write_text(
        "== ld\nproperty: a |-> s_eventually b\ndisable: !r\nscope: attempt\nloop: 1\n"
        "a : 1 .\nb : . .\nr : 1 1\noutcome : F F\ndecided : . .\n"
        "inf-outcome : F F\ninf-decided : . .\nclause: 16.12\nclass: normative\n",
        encoding="utf-8",
    )
    case = load_cases(path)[0]
    text = render_formal(case)
    assert "always @(posedge clk) phase_prev <= phase;" in text
    assert "row_idx_prev = (phase_prev <" in text


def test_the_wave_of_a_disable_only_signal_is_drawn_one_cell_later(
    demo: dict[str, TraceCase],
) -> None:
    document = json.loads(render_wavedrom(demo["first"]))  # r = . . 1: row 2 after edge 2
    lanes = {str(lane["name"]): lane for lane in document["signal"]}
    assert lanes["r"]["wave"] == "0.."  # rows 0, 0, 0 in cells 0, 1, 2 (row 2 comes after the end)
    assert lanes["a"]["wave"] == "1.0"  # the other signals are drawn as the table rows
    assert "r: read only by the disable iff condition" in document["foot"]["text"]
    assert "foot" not in json.loads(render_wavedrom(demo["plain"]))
