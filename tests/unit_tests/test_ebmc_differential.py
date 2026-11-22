"""EBMC differential support contract (support.ebmc_differential), without EBMC or Spot."""

from __future__ import annotations

import dataclasses
import re
import sys
from pathlib import Path

import pytest

from support.ebmc_differential import (
    AGREES,
    DISAGREES,
    ERROR,
    NOT_COMPARED,
    AllowList,
    AllowListError,
    Engine,
    case_key,
    design_source,
    draft,
    ebmc_options,
    ebmc_version,
    expected_verdicts,
    judge,
    load_all_cases,
    observed_verdict,
    parse_allow_list,
    parse_ebmc_output,
    property_tokens,
    run_job,
    select,
    spot_command,
    traces_digest,
)
from support.toolchain import Tool, ToolError
from support.tracefile import TraceCase, load_cases

TRACE = """\
== finite_case
property: a |-> ##1 b
signals: a b v:3
a : 1 . 1 .
b : . 1 . .
v : 5 . 7 .
fail : . . . 1
clause: 16.12
class: normative

== cover_case
property: a ##1 b
kind: cover
a : 1 . 1 .
b : . 1 . .
pass : . 1 . .
clause: 16.14
class: normative

== lasso_case
property: s_eventually b
scope: attempt
loop: 1
a : 1 . .
b : . . 1
outcome : T T T
inf-outcome: T F T
clause: 16.12.13
class: normative
"""


def _cases(tmp_path: Path, text: str = TRACE) -> dict[str, TraceCase]:
    path = tmp_path / "sample.trace"
    path.write_text(text, encoding="utf-8")
    return {case.name: case for case in load_cases(path)}


def _property_case(
    tmp_path: Path,
    prop: str,
    *,
    kind: str = "assert",
    disable: str | None = None,
    procedure: str | None = None,
) -> TraceCase:
    base = _cases(tmp_path)["finite_case"]
    widths = {name: 1 for name in "abcdexyz"} | {"v": 4}
    return dataclasses.replace(
        base, property=prop, widths=widths, kind=kind, disable=disable, procedure=procedure
    )


# --- the allow-list ------------------------------------------------------------------------

ALLOW_TEXT = """\
# a comment
exclude disable-iff @disable
exclude pending intersect within   # trailing comment
exclude unbounded-under-not not+##[m:$] not+strong
skip sample:skipped_case a reason with words
include bdd sample:lasso_case explicit reason

== delays
engine: bmc
kinds: assert cover
vocabulary: ##N and
requires: ##N
positive: sample:cover_case
negative: sample:finite_case
note: fixed delays

== implication
engine: bmc
kinds: assert
vocabulary: ##N |->
positive: sample:finite_case
negative: sample:finite_case

== eventually
engine: buechi
kinds: assert
vocabulary: s_eventually
forbids: @recurrence
positive: sample:lasso_case
negative: sample:lasso_case
"""


def test_the_allow_list_is_parsed() -> None:
    allow = parse_allow_list(ALLOW_TEXT)
    assert [e.slug for e in allow.exclusions] == ["disable-iff", "pending", "unbounded-under-not"]
    assert allow.exclusions[0].groups == (frozenset({"@disable"}),)
    assert allow.exclusions[2].groups == (
        frozenset({"not", "##[m:$]"}),
        frozenset({"not", "strong"}),
    )
    assert allow.skips == {"sample:skipped_case": "a reason with words"}
    assert allow.includes == {(Engine.BDD, "sample:lasso_case"): "explicit reason"}
    delays = allow.shapes[0]
    assert (delays.name, delays.engine, delays.note) == ("delays", Engine.BMC, "fixed delays")
    assert delays.kinds == frozenset({"assert", "cover"})
    assert delays.requires == frozenset({"##N"})
    assert delays.forbids == frozenset()
    assert allow.shapes[2].forbids == frozenset({"@recurrence"})


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("nonsense", "unknown line"),
        ("exclude only-a-slug", "exclude <slug>"),
        ("skip a", "skip <case>"),
        ("include bmc a", "include <engine>"),
        ("include nothing a reason", "unknown engine"),
        ("== s\nengine: bmc\nkinds: assert\nvocabulary: a\npositive: x\n", "missing 'negative'"),
        (
            "== s\nengine: weird\nkinds: assert\nvocabulary: a\npositive: x\nnegative: y\n",
            "unknown engine",
        ),
        (
            "== s\nengine: bmc\nkinds: assert\nvocabulary:\npositive: x\nnegative: y\n"
            "== s\nengine: bmc\nkinds: assert\nvocabulary:\npositive: x\nnegative: y\n",
            "duplicate shape",
        ),
    ],
)
def test_a_malformed_allow_list_names_the_problem(text: str, fragment: str) -> None:
    with pytest.raises(AllowListError, match=re.escape(fragment)):
        parse_allow_list(text)


# --- the tokens of a property --------------------------------------------------------------


@pytest.mark.parametrize(
    ("prop", "tokens"),
    [
        ("a", set()),
        ("a && !b || c", set()),
        ("a |-> ##1 b", {"|->", "##N"}),
        ("a |=> b ##[1:3] c", {"|=>", "##[m:n]"}),
        ("a ##[1:$] b", {"##[m:$]"}),
        ("a[*3] ##1 b[*1:2]", {"[*N]", "##N", "[*m:n]"}),
        ("a[*] ##0 b[+] ##0 c[*2:$]", {"[*m:$]", "##N"}),
        ("a[->2] ##1 b[->1:3] ##1 c[->1:$]", {"[->N]", "##N", "[->m:n]", "[->m:$]"}),
        ("a[=2] ##1 b[=1:3] ##1 c[=1:$]", {"[=N]", "##N", "[=m:n]", "[=m:$]"}),
        ("always [2:5] a", {"always", "[m:n]"}),
        ("s_eventually [2:$] a", {"s_eventually", "[m:$]"}),
        ("nexttime [3] a", {"nexttime", "[n]"}),
        ("not (a until b)", {"not", "until"}),
        ("strong(a ##1 b) and weak(c)", {"strong", "##N", "and", "weak"}),
        ("a inside {1, 2}", {"inside", "{", "}"}),
        ("v[0] && $past(a)", {"[", "]", "$past"}),
        ("v == 4'd5 && a", set()),
        ("v == 4'b1x0? && a", {"@xz-literal"}),
        ("if (a) b else c", {"if", "else"}),
        ("always s_eventually a", {"always", "s_eventually", "@recurrence"}),
        ("s_eventually always a", {"always", "s_eventually", "@eventually-always"}),
        ("always (a |-> s_eventually b)", {"always", "|->", "s_eventually", "@recurrence"}),
    ],
)
def test_property_tokens(tmp_path: Path, prop: str, tokens: set[str]) -> None:
    assert property_tokens(_property_case(tmp_path, prop)) == frozenset(tokens)


def test_signals_of_the_case_are_not_tokens_but_other_words_are(tmp_path: Path) -> None:
    assert property_tokens(_property_case(tmp_path, "go |-> stop")) == {"go", "|->", "stop"}


def test_pseudo_tokens_describe_the_directive(tmp_path: Path) -> None:
    assert "@disable" in property_tokens(_property_case(tmp_path, "a", disable="rst"))
    assert "@procedure" in property_tokens(_property_case(tmp_path, "a", procedure="initial"))
    assert "@assume" in property_tokens(_property_case(tmp_path, "a", kind="assume"))
    assert "@cover" in property_tokens(_property_case(tmp_path, "a", kind="cover"))
    assert property_tokens(_property_case(tmp_path, "a")) == frozenset()


# --- selecting the cases -------------------------------------------------------------------


def _selected(allow: AllowList, case: TraceCase) -> list[tuple[str | None, str, str]]:
    return [(s.engine.value if s.engine else None, s.shape, s.reason) for s in select(case, allow)]


def test_a_case_is_named_by_its_file_and_its_name(tmp_path: Path) -> None:
    assert case_key(_cases(tmp_path)["finite_case"]) == "sample:finite_case"


def test_a_shape_admits_a_case_by_kind_vocabulary_and_requires(tmp_path: Path) -> None:
    allow = parse_allow_list(ALLOW_TEXT)
    cases = _cases(tmp_path)
    # the delays shape has no "|->" in its vocabulary: the implication shape admits it
    assert _selected(allow, cases["finite_case"]) == [("bmc", "implication", "")]
    assert _selected(allow, cases["cover_case"]) == [("bmc", "delays", "")]
    cover_with_implication = dataclasses.replace(cases["cover_case"], property="a |-> ##1 b")
    assert _selected(allow, cover_with_implication) == [(None, "-", "unlisted-shape")]


def test_the_first_admitting_shape_of_an_engine_wins_and_others_engines_add(
    tmp_path: Path,
) -> None:
    allow = parse_allow_list(ALLOW_TEXT)
    case = dataclasses.replace(_cases(tmp_path)["finite_case"], property="a ##1 b")
    assert _selected(allow, case) == [("bmc", "delays", "")]
    lasso = _cases(tmp_path)["lasso_case"]
    assert _selected(allow, lasso) == [("bdd", "explicit", ""), ("buechi", "eventually", "")]


def test_infinite_engines_need_a_lasso_case(tmp_path: Path) -> None:
    allow = parse_allow_list(ALLOW_TEXT)
    finite = dataclasses.replace(_cases(tmp_path)["finite_case"], property="s_eventually b")
    assert _selected(allow, finite) == [(None, "-", "unlisted-shape")]


def test_a_forbidden_or_missing_token_keeps_a_case_out_of_a_shape(tmp_path: Path) -> None:
    allow = parse_allow_list(ALLOW_TEXT)
    lasso = _cases(tmp_path)["lasso_case"]
    recurrent = dataclasses.replace(
        lasso, property="always s_eventually b", widths={"b": 1, "a": 1}
    )
    assert [s for s in _selected(allow, recurrent) if s[0] == "buechi"] == []
    no_delay = dataclasses.replace(_cases(tmp_path)["cover_case"], property="a and b")
    assert _selected(allow, no_delay) == [(None, "-", "unlisted-shape")]


def test_an_exclusion_gives_the_reason_before_any_shape(tmp_path: Path) -> None:
    allow = parse_allow_list(ALLOW_TEXT)
    case = _cases(tmp_path)["finite_case"]
    disabled = dataclasses.replace(case, disable="rst")
    assert _selected(allow, disabled) == [(None, "-", "disable-iff")]
    unbounded = dataclasses.replace(case, property="not (a ##[1:$] b)")
    assert _selected(allow, unbounded) == [(None, "-", "unbounded-under-not")]
    nested = dataclasses.replace(case, property="a |-> not strong(b ##1 c)")
    assert _selected(allow, nested) == [(None, "-", "unbounded-under-not")]
    intersect = dataclasses.replace(case, property="(a ##1 b) intersect (c ##1 d)")
    assert _selected(allow, intersect) == [(None, "-", "pending")]


def test_a_skip_overrides_everything_and_an_include_overrides_the_shapes(tmp_path: Path) -> None:
    allow = parse_allow_list(ALLOW_TEXT)
    skipped = dataclasses.replace(_cases(tmp_path)["finite_case"], name="skipped_case")
    assert _selected(allow, skipped) == [(None, "-", "a reason with words")]
    lasso = _cases(tmp_path)["lasso_case"]
    assert ("bdd", "explicit", "") in _selected(allow, lasso)
    disabled = dataclasses.replace(lasso, disable="rst")
    assert _selected(allow, disabled) == [(None, "-", "disable-iff")]


def test_the_shipped_traces_load_and_the_digest_ignores_line_ends(tmp_path: Path) -> None:
    assert len(load_all_cases()) > 600
    (tmp_path / "a.trace").write_bytes(b"x\r\ny\r\n")
    crlf = traces_digest(tmp_path)
    (tmp_path / "a.trace").write_bytes(b"x\ny\n")
    assert traces_digest(tmp_path) == crlf
    (tmp_path / "a.trace").write_bytes(b"x\ny\nz\n")
    assert traces_digest(tmp_path) != crlf
    (tmp_path / "b.trace").write_bytes(b"x\ny\nz\n")
    (tmp_path / "ignored.txt").write_text("not a trace")
    before = traces_digest(tmp_path)
    (tmp_path / "ignored.txt").write_text("still not a trace")
    assert traces_digest(tmp_path) == before


# --- the design and the command ------------------------------------------------------------


def _lookup(design: str, signal: str, selector: str, index: int) -> int:
    """Evaluate the case-table lookup of ``signal`` at ``selector == index`` from the text."""
    match = re.search(rf"wire \[\d+:0\] {signal} = (.*);", design)
    assert match is not None, design
    text = match.group(1)
    pairs = re.findall(rf"\({selector} == \d+'d(\d+) \? \d+'d(\d+) : ", text)
    default = re.search(r": (\d+)'d(\d+)\)+$", text)
    assert default is not None, text
    for chosen, value in pairs:
        if int(chosen) == index:
            return int(value)
    return int(default.group(2))


def test_a_finite_design_replays_the_samples_and_holds_the_last_values(tmp_path: Path) -> None:
    case = _cases(tmp_path)["finite_case"]
    design = design_source(case, Engine.BMC)
    assert "module top(input logic clk);" in design
    assert re.search(r"logic \[\d+:0\] tick = 0;", design)
    assert "p: assert property (@(posedge clk) a |-> ##1 b);" in design
    assert "start_" not in design
    assert "phase" not in design
    for signal in "abv":
        values = [sample[signal] for sample in case.samples]
        found = [_lookup(design, signal, "tick", k) for k in range(len(values) + 3)]
        assert found == [*values, *[values[-1]] * 3]
    assert re.search(r"wire \[2:0\] v = ", design)
    assert re.search(r"wire \[0:0\] a = ", design)


def test_the_counter_saturates_after_the_written_samples(tmp_path: Path) -> None:
    case = _cases(tmp_path)["finite_case"]
    design = design_source(case, Engine.BMC)
    match = re.search(r"if \(tick != (\d+)'d(\d+)\) tick <= tick \+ 1'b1;", design)
    assert match is not None
    assert int(match.group(2)) == len(case.samples)
    assert int(match.group(2)) < 1 << int(match.group(1))


def test_a_cover_case_gets_a_cover_directive(tmp_path: Path) -> None:
    design = design_source(_cases(tmp_path)["cover_case"], Engine.BMC)
    assert "p: cover property (@(posedge clk) a ##1 b);" in design


def test_a_bdd_design_follows_the_infinite_trace(tmp_path: Path) -> None:
    case = _cases(tmp_path)["lasso_case"]
    design = design_source(case, Engine.BDD)
    assert "phase <= " in design
    assert "tick" not in design
    assert "p: assert property (@(posedge clk) s_eventually b);" in design
    wrap = re.search(r"if \(phase == \d+'d(\d+)\) phase <= \d+'d(\d+);", design)
    assert wrap is not None
    assert (int(wrap.group(1)), int(wrap.group(2))) == (len(case.samples) - 1, case.loop)
    assert [_lookup(design, "b", "phase", k) for k in range(len(case.samples))] == [
        s["b"] for s in case.samples
    ]


def test_a_buechi_design_has_one_assertion_per_first_visit(tmp_path: Path) -> None:
    case = _cases(tmp_path)["lasso_case"]
    design = design_source(case, Engine.BUECHI)
    assert "phase <= " in design
    assert "tick <= tick + 1'b1" in design
    for k in range(len(case.samples)):
        assert re.search(
            rf"start_{k}: assert property \(@\(posedge clk\) \(tick == \d+'d{k}\) "
            r"\|-> \(s_eventually b\)\);",
            design,
        )
    assert "p:" not in design


def test_a_lasso_engine_refuses_a_finite_case(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="lasso"):
        design_source(_cases(tmp_path)["finite_case"], Engine.BDD)


def test_the_options_of_each_engine(tmp_path: Path) -> None:
    cases = _cases(tmp_path)
    finite, lasso = cases["finite_case"], cases["lasso_case"]
    assert ebmc_options(finite, Engine.BMC) == ["--top", "top", "--bound", "3"]
    assert ebmc_options(lasso, Engine.BDD) == ["--top", "top", "--bdd"]
    assert ebmc_options(lasso, Engine.BUECHI) == ["--top", "top", "--bdd", "--buechi"]


# --- answers and verdicts ------------------------------------------------------------------

OUTPUT = """\
EBMC version 6.1 (ebmc-6.1) 64-bit x86_64 linux
Converting
** Results:
[top.p] always (top.a |-> (##1 top.b)): REFUTED
[top.start_0] always (top.a |-> top.b): PROVED up to bound 3
[top.start_1] (top.tick == 1) |-> top.x: INCONCLUSIVE
[top.c] cover (top.a ##2 top.b): REFUTED up to bound 2
[top.d] always s_eventually top.a: UNSUPPORTED: assumption not supported by BDD engine

"""


def test_the_results_section_is_parsed() -> None:
    assert parse_ebmc_output(OUTPUT) == {
        "p": "REFUTED",
        "start_0": "PROVED up to bound 3",
        "start_1": "INCONCLUSIVE",
        "c": "REFUTED up to bound 2",
        "d": "UNSUPPORTED: assumption not supported by BDD engine",
    }
    assert parse_ebmc_output("no results here\n[top.p] x: REFUTED\n") == {}
    assert parse_ebmc_output("") == {}


@pytest.mark.parametrize(
    ("kind", "status", "verdict"),
    [
        ("assert", "REFUTED", "fail"),
        ("assert", "PROVED", "pass"),
        ("assert", "PROVED up to bound 4", "pass"),
        ("assert", "REFUTED up to bound 4", None),
        ("assert", "INCONCLUSIVE", None),
        ("assert", "UNSUPPORTED: x", None),
        ("assert", "FAILURE: property not supported by BDD engine", None),
        ("cover", "PROVED", "reached"),
        ("cover", "REFUTED up to bound 4", "unreached"),
        ("cover", "REFUTED", "unreached"),
        ("cover", "PROVED up to bound 4", None),
        ("cover", "INCONCLUSIVE", None),
    ],
)
def test_the_status_of_an_answer_is_a_verdict_or_undecided(
    kind: str, status: str, verdict: str | None
) -> None:
    assert observed_verdict(kind, status) == verdict


def test_the_expected_verdicts_are_trace_validity_per_label(tmp_path: Path) -> None:
    cases = _cases(tmp_path)
    finite, cover, lasso = cases["finite_case"], cases["cover_case"], cases["lasso_case"]
    assert expected_verdicts(finite, Engine.BMC) == {"p": "fail"}
    assert expected_verdicts(dataclasses.replace(finite, fails=frozenset()), Engine.BMC) == {
        "p": "pass"
    }
    assert expected_verdicts(cover, Engine.BMC) == {"p": "reached"}
    assert expected_verdicts(dataclasses.replace(cover, passes=frozenset()), Engine.BMC) == {
        "p": "unreached"
    }
    assert expected_verdicts(lasso, Engine.BDD) == {"p": "fail"}
    assert expected_verdicts(
        dataclasses.replace(lasso, inf_outcomes=("T", "T", "T")), Engine.BDD
    ) == {"p": "pass"}
    assert expected_verdicts(lasso, Engine.BUECHI) == {
        "start_0": "pass",
        "start_1": "fail",
        "start_2": "pass",
    }
    disabled = dataclasses.replace(lasso, inf_outcomes=("T", "D", "F"))
    assert expected_verdicts(disabled, Engine.BUECHI) == {"start_0": "pass", "start_2": "fail"}


def test_judge_classes(tmp_path: Path) -> None:
    cases = _cases(tmp_path)
    finite, lasso = cases["finite_case"], cases["lasso_case"]
    agree = judge(finite, Engine.BMC, {"p": "REFUTED"}, "")
    assert (agree.cls, agree.ours, agree.ebmc) == (AGREES, "p=fail", "p=REFUTED")
    differ = judge(finite, Engine.BMC, {"p": "PROVED up to bound 3"}, "")
    assert differ.cls == DISAGREES
    assert "p: ours fail, ebmc pass" in differ.detail
    undecided = judge(finite, Engine.BMC, {"p": "INCONCLUSIVE"}, "")
    assert (undecided.cls, undecided.detail) == (
        NOT_COMPARED,
        "engine-undecided: p: INCONCLUSIVE",
    )
    missing = judge(finite, Engine.BMC, {"q": "REFUTED"}, "")
    assert missing.cls == NOT_COMPARED
    assert "p: missing" in missing.detail
    failure = judge(finite, Engine.BMC, {}, "CONVERSION ERROR")
    assert (failure.cls, failure.detail) == (ERROR, "CONVERSION ERROR")
    statuses = {"start_0": "PROVED", "start_1": "REFUTED", "start_2": "PROVED"}
    assert judge(lasso, Engine.BUECHI, statuses, "").cls == AGREES
    statuses["start_2"] = "REFUTED"
    both = judge(lasso, Engine.BUECHI, statuses, "")
    assert both.cls == DISAGREES
    assert both.detail == "start_2: ours pass, ebmc fail"
    mixed = judge(lasso, Engine.BUECHI, {**statuses, "start_0": "INCONCLUSIVE"}, "")
    assert mixed.cls == DISAGREES  # a difference wins over an undecided label


# --- running EBMC --------------------------------------------------------------------------

FAKE_EBMC = """\
import os, sys
from pathlib import Path

args = sys.argv[1:]
Path("args.txt").write_text(" ".join(args))
Path("path.txt").write_text(os.environ["PATH"].split(os.pathsep)[0])
mode = os.environ.get("FAKE_MODE", "refuted")
if "--version" in args:
    print("EBMC version 6.1 (ebmc-6.1) 64-bit x86_64 linux")
    print("6.1 (ebmc-6.1)")
elif mode == "hang":
    import time
    time.sleep(60)
elif mode == "broken":
    print("CONVERSION ERROR")
    sys.exit(2)
else:
    print("** Results:")
    print("[top.p] always x: REFUTED")
"""


@pytest.fixture
def fake_ebmc(tmp_path: Path) -> Tool:
    script = tmp_path / "fake_ebmc.py"
    script.write_text(FAKE_EBMC, encoding="utf-8")
    return Tool("FAKE", (sys.executable, str(script)))


def test_ebmc_is_run_on_the_design_with_the_options(fake_ebmc: Tool, tmp_path: Path) -> None:
    case = _cases(tmp_path)["finite_case"]
    work = tmp_path / "work"
    judgement = run_job(fake_ebmc, None, case, Engine.BMC, work)
    assert judgement.cls == AGREES
    assert (work / "design.sv").read_text(encoding="utf-8") == design_source(case, Engine.BMC)
    args = (work / "args.txt").read_text(encoding="utf-8").split()
    assert args[0].endswith("design.sv")
    assert args[1:] == ["--top", "top", "--bound", "3"]


def test_the_directory_of_spot_is_first_on_the_path_for_the_buechi_engine_only(
    fake_ebmc: Tool, tmp_path: Path
) -> None:
    case = _cases(tmp_path)["lasso_case"]
    spot = tmp_path / "spot dir"
    spot.mkdir()
    run_job(fake_ebmc, spot, case, Engine.BUECHI, tmp_path / "buechi")
    assert (tmp_path / "buechi" / "path.txt").read_text(encoding="utf-8") == str(spot)
    run_job(fake_ebmc, spot, case, Engine.BDD, tmp_path / "bdd")
    assert (tmp_path / "bdd" / "path.txt").read_text(encoding="utf-8") != str(spot)


def test_the_buechi_engine_needs_spot(fake_ebmc: Tool, tmp_path: Path) -> None:
    case = _cases(tmp_path)["lasso_case"]
    with pytest.raises(ValueError, match="ltl2tgba"):
        run_job(fake_ebmc, None, case, Engine.BUECHI, tmp_path / "w")


def test_a_failing_ebmc_is_an_error_with_its_last_line(
    fake_ebmc: Tool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "broken")
    judgement = run_job(
        fake_ebmc, None, _cases(tmp_path)["finite_case"], Engine.BMC, tmp_path / "w"
    )
    assert (judgement.cls, judgement.detail) == (ERROR, "CONVERSION ERROR")


def test_a_timeout_is_not_a_verdict(
    fake_ebmc: Tool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "hang")
    judgement = run_job(
        fake_ebmc, None, _cases(tmp_path)["finite_case"], Engine.BMC, tmp_path / "w", timeout=1.0
    )
    assert (judgement.cls, judgement.detail) == (NOT_COMPARED, "engine-undecided: timeout")


def test_a_missing_executable_is_an_error(tmp_path: Path) -> None:
    tool = Tool("MISSING", (str(tmp_path / "no-such-ebmc"),))
    judgement = run_job(tool, None, _cases(tmp_path)["finite_case"], Engine.BMC, tmp_path / "w")
    assert judgement.cls == ERROR
    assert "cannot start" in judgement.detail


def test_the_version_is_the_last_line_of_version_output(fake_ebmc: Tool, tmp_path: Path) -> None:
    assert ebmc_version(fake_ebmc, tmp_path) == "6.1 (ebmc-6.1)"
    silent = Tool("SILENT", (sys.executable, "-c", "pass"))
    with pytest.raises(ToolError, match="printed nothing"):
        ebmc_version(silent, tmp_path)


def test_spot_is_run_the_way_ebmc_is_run(tmp_path: Path) -> None:
    wsl = Tool("E", ("wsl.exe", "-e", "/mnt/c/opt/ebmc/ebmc"))
    assert spot_command(wsl, Path("C:/opt/spot/bin")) == [
        "wsl.exe",
        "-e",
        "/mnt/c/opt/spot/bin/ltl2tgba",
    ]
    bare = Tool("E", ("wsl", "/mnt/c/ebmc"))
    assert spot_command(bare, Path("C:/s")) == ["wsl", "/mnt/c/s/ltl2tgba"]
    native = Tool("E", ("/usr/bin/ebmc",))
    assert spot_command(native, Path("/opt/spot")) == [str(Path("/opt/spot") / "ltl2tgba")]


# --- the draft -----------------------------------------------------------------------------


def test_the_draft_writes_a_provisional_table_and_never_classifies(
    fake_ebmc: Tool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    allow = parse_allow_list(
        "exclude disable-iff @disable\n"
        "== any\nengine: bmc\nkinds: assert\nvocabulary: |-> ##N\nrequires: |->\n"
        "positive: delays:p03_delay1\nnegative: delays:p03_delay1\n"
    )
    out = tmp_path / "draft.tsv"
    only = ("delays:p03_delay1", "disable:p11_attempts_after_a_disable")
    draft(fake_ebmc, None, allow, out, tmp_path / "work", jobs=2, only=only)
    rows = [line.split("\t") for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows[0][:5] == ["case", "engine", "shape", "class", "detail"]
    by_case = {row[0]: row for row in rows[1:]}
    assert set(by_case) == set(only)
    compared = by_case["delays:p03_delay1"]
    assert compared[1:4] == ["bmc", "any", AGREES]
    assert compared[5:7] == ["p=fail", "p=REFUTED"]
    excluded = by_case["disable:p11_attempts_after_a_disable"]
    assert excluded[1:5] == ["-", "-", NOT_COMPARED, "disable-iff"]
    assert "bmc" in capsys.readouterr().out
