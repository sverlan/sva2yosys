"""Unit contract of the test-support trace-file loader (its docstrings are the contract)."""

from __future__ import annotations

from pathlib import Path

import pytest

from support.tracefile import CaseClass, TraceCase, TraceFileError, load_cases, sv_source

_GOOD = """# a comment
== first
property: a |-> ##[1:3] b   # trailing comment
signals: a b v:4
sample : 0 1 2 3
a      : 1 . . .
b      : . . . 1
v      : 0 15 . 2
fail   : . . . .
pass   : . . . 1
pending: 0

== second
property: x
kind: cover
disable: !r
x : 1 .
r : . 1
"""


_META = "clause: 16.7\nclass: normative\n"


def _write(tmp_path: Path, text: str, *, meta: bool = True) -> Path:
    """Write ``text``; unless ``meta`` is false, give every case that lacks them a clause and
    a class (the metadata tests below pass ``meta=False``)."""
    if meta:
        text = "".join(
            line + _META if line.startswith("property:") and "clause:" not in text else line
            for line in text.splitlines(keepends=True)
        )
    path = tmp_path / "t.trace"
    path.write_text(text)
    return path


def test_cases_are_parsed(tmp_path: Path) -> None:
    first, second = load_cases(_write(tmp_path, _GOOD))
    assert (first.name, first.line, first.file) == ("first", 2, "t.trace")
    assert first.property == "a |-> ##[1:3] b"
    assert first.kind == "assert"
    assert first.disable is None
    assert first.widths == {"a": 1, "b": 1, "v": 4}
    assert first.samples == (
        {"a": 1, "b": 0, "v": 0},
        {"a": 0, "b": 0, "v": 15},
        {"a": 0, "b": 0, "v": 0},
        {"a": 0, "b": 1, "v": 2},
    )
    assert first.fails == frozenset()
    assert first.passes == frozenset({3})
    assert first.pending == 0
    assert (second.kind, second.disable, second.pending) == ("cover", "!r", None)
    assert second.widths == {"x": 1, "r": 1}


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("== c\nproperty: a\na : 1 .\nb : 1\n", "length"),
        ("== c\na : 1\n", "property"),
        ("== c\nproperty: a\ncolour: red\na : 1\n", "colour"),
        ("== c\nproperty: a\na : 2\n", "value"),
        ("== c\nproperty: a\nsample : 1 2\na : 1 1\n", "sample"),
        ("== c\nproperty: a\na : 1\n== c\nproperty: a\na : 1\n", "duplicate"),
        ("property: a\n", "outside"),
        ("== c\nproperty: a\nsignals: a:0\na : 1\n", "width"),
        ("== c\nproperty: t\nt : 1\n", "reserved"),
        ("== c\nproperty: a\nsignals: clk\na : 1\n", "reserved"),
    ],
)
def test_malformed_files(tmp_path: Path, text: str, fragment: str) -> None:
    with pytest.raises(TraceFileError, match=fragment) as info:
        load_cases(_write(tmp_path, text))
    assert "t.trace:" in str(info.value)


def test_sv_source(tmp_path: Path) -> None:
    _, second = load_cases(_write(tmp_path, _GOOD))
    first = load_cases(_write(tmp_path, _GOOD))[0]
    text = sv_source(first, "m")
    assert "module m(input logic clk, input logic a, input logic b, input logic [3:0] v);" in text
    assert "p: assert property (@(posedge clk) a |-> ##[1:3] b);" in text
    cover = sv_source(second)
    assert "p: cover property (@(posedge clk) disable iff (!r) x);" in cover


def test_final_fail_is_parsed(tmp_path: Path) -> None:
    (case,) = load_cases(_write(tmp_path, "== c\nproperty: a\nfinal-fail: 2\na : 1\n"))
    assert case.final_fail == 2
    (case,) = load_cases(_write(tmp_path, "== c\nproperty: a\na : 1\n"))
    assert case.final_fail is None


def test_first_failure_scope(tmp_path: Path) -> None:
    ok = "== c\nproperty: (a |-> b) and (a |-> c)\nscope: first-failure\na: 1 .\nfail: 1 .\n"
    (case,) = load_cases(_write(tmp_path, ok))
    assert case.first_failure_only and case.fails == {0}
    for text, fragment in (
        (ok.replace("fail:", "pass:"), "no 'pass' row"),
        (ok.replace("first-failure", "never"), "scope must be"),
        ("== c\nproperty: a\nkind: cover\nscope: first-failure\na: 1\n", "not a cover"),
    ):
        with pytest.raises(TraceFileError, match=fragment):
            load_cases(_write(tmp_path, text))


_ATTEMPT = (
    "== c\nproperty: a |-> b\nsupport: oracle\nscope: attempt\n"
    "a : 1 . 1\nb : . . 1\noutcome : F t T\ndecided : 0 1 .\npass : . . 1\n"
)


def test_attempt_scope_rows_are_parsed(tmp_path: Path) -> None:
    (case,) = load_cases(_write(tmp_path, _ATTEMPT))
    assert case.attempt_scope and case.oracle_only and not case.first_failure_only
    assert case.outcomes == ("F", "t", "T")
    assert case.decisions == (0, 1, None)
    assert case.passes == {2}
    assert case.widths == {"a": 1, "b": 1}  # the attempt rows are not signals
    (plain,) = load_cases(_write(tmp_path, "== c\nproperty: a\na : 1\n"))
    assert not plain.attempt_scope and not plain.oracle_only
    assert plain.outcomes is None and plain.decisions is None
    assert case.rejected_by is None and plain.rejected_by is None


def test_rejected_by_is_parsed(tmp_path: Path) -> None:
    text = _ATTEMPT.replace("support: oracle\n", "support: oracle\nrejected-by: first sample\n")
    (case,) = load_cases(_write(tmp_path, text))
    assert case.oracle_only and case.rejected_by == "first sample"


@pytest.mark.parametrize(
    ("old", "new", "fragment"),
    [
        ("support: oracle\n", "rejected-by: first sample\n", "needs 'support: oracle'"),
        ("support: oracle\n", "support: oracle\nrejected-by:\n", "needs a message fragment"),
    ],
)
def test_malformed_rejected_by(tmp_path: Path, old: str, new: str, fragment: str) -> None:
    with pytest.raises(TraceFileError, match=fragment):
        load_cases(_write(tmp_path, _ATTEMPT.replace(old, new)))


@pytest.mark.parametrize(
    ("old", "new", "fragment"),
    [
        ("scope: attempt", "scope: never", "scope must be"),
        ("support: oracle", "support: yes", "support must be"),
        ("scope: attempt", "", "needs 'scope: attempt'"),
        ("outcome : F t T", "outcome : F t Q", "bad outcome"),
        ("outcome : F t T", "outcome : F t TT", "bad outcome"),
        ("decided : 0 1 .", "decided : 0 1 x", "bad decision"),
        ("decided : 0 1 .", "decided : 0 1", "differ in length"),
    ],
)
def test_malformed_attempt_files(tmp_path: Path, old: str, new: str, fragment: str) -> None:
    with pytest.raises(TraceFileError, match=fragment):
        load_cases(_write(tmp_path, _ATTEMPT.replace(old, new)))


_LASSO = (
    "== c\nproperty: s_eventually b\nsupport: oracle\nscope: attempt\nloop: 1\n"
    "b : . . 1\ninf-outcome : T T D\ninf-decided : 2 2 .\n"
)


def test_lasso_rows_are_parsed(tmp_path: Path) -> None:
    (case,) = load_cases(_write(tmp_path, _LASSO))
    assert case.loop == 1
    assert case.inf_outcomes == ("T", "T", "D")
    assert case.inf_decisions == (2, 2, None)
    assert case.widths == {"b": 1}  # the inf rows are not signals
    assert case.outcomes is None and case.decisions is None  # finite rows stay independent
    (plain,) = load_cases(_write(tmp_path, _ATTEMPT))
    assert plain.loop is None and plain.inf_outcomes is None and plain.inf_decisions is None


def test_loop_at_the_last_sample_is_allowed(tmp_path: Path) -> None:
    (case,) = load_cases(_write(tmp_path, _LASSO.replace("loop: 1", "loop: 2")))
    assert case.loop == 2


@pytest.mark.parametrize(
    ("old", "new", "fragment"),
    [
        ("loop: 1", "loop: 3", "outside the 3 written samples"),
        ("loop: 1", "loop: -1", "bad loop"),
        ("loop: 1", "loop: x", "bad loop"),
        ("loop: 1", "", "needs a 'loop' header"),
        ("scope: attempt\n", "", "needs 'scope: attempt'"),
        ("inf-outcome : T T D", "inf-outcome : T T p", "bad outcome"),
        ("inf-outcome : T T D", "inf-outcome : T T t", "bad outcome"),
        ("inf-decided : 2 2 .", "inf-decided : 2 2 x", "bad decision"),
        ("inf-decided : 2 2 .", "inf-decided : 2 2", "differ in length"),
    ],
)
def test_malformed_lasso_files(tmp_path: Path, old: str, new: str, fragment: str) -> None:
    with pytest.raises(TraceFileError, match=fragment):
        load_cases(_write(tmp_path, _LASSO.replace(old, new)))


def test_lasso_needs_samples(tmp_path: Path) -> None:
    text = "== c\nproperty: b\nscope: attempt\nloop: 0\n"
    with pytest.raises(TraceFileError, match="outside the 0 written samples"):
        load_cases(_write(tmp_path, text))


def _case(tmp_path: Path, extra: str) -> TraceCase:
    text = "== c\nproperty: a\n" + extra + "a : 1\n"
    return load_cases(_write(tmp_path, text, meta=False))[0]


def test_clause_and_class_are_parsed(tmp_path: Path) -> None:
    case = _case(tmp_path, "clause: 16.9.2 16.12.7 Annex F\nclass: normative\n")
    assert case.clause == ("16.9.2", "16.12.7", "Annex F")
    assert case.case_class is CaseClass.NORMATIVE
    assert case.interpretations == ()
    assert case.origin is None


def test_clause_items_may_be_comma_separated(tmp_path: Path) -> None:
    case = _case(tmp_path, "clause: 16.7, 20.9\nclass: implementation-defined\n")
    assert case.clause == ("16.7", "20.9")
    assert case.case_class is CaseClass.IMPLEMENTATION_DEFINED


def test_interpretation_ids_are_parsed(tmp_path: Path) -> None:
    case = _case(tmp_path, "clause: 16.7\nclass: interpretation I1, I8\n")
    assert case.case_class is CaseClass.INTERPRETATION
    assert case.interpretations == ("I1", "I8")


def test_origin_is_parsed(tmp_path: Path) -> None:
    case = _case(tmp_path, "clause: 16.7\nclass: normative\norigin: a mutant\n")
    assert case.origin == "a mutant"


def test_must_reject_needs_the_oracle(tmp_path: Path) -> None:
    case = _case(tmp_path, "clause: 16.7\nclass: must-reject\nsupport: oracle\n")
    assert case.case_class is CaseClass.MUST_REJECT


@pytest.mark.parametrize(
    ("extra", "fragment", "line"),
    [
        # line: the case header (1) for a missing key, else the offending header line
        ("class: normative\n", "no clause", 1),
        ("clause: 16.7\n", "no class", 1),
        ("clause: 16.7\nclass: normative\nclause: 16.8\n", "duplicate", 5),
        ("clause: 16.7\nclass: normative\nclass: normative\n", "duplicate", 5),
        ("clause: 16.7\nclass: normative\norigin: x\norigin: y\n", "duplicate", 6),
        ("clause:\nclass: normative\n", "clause", 3),
        ("clause: sixteen\nclass: normative\n", "bad clause", 3),
        ("clause: 16.\nclass: normative\n", "bad clause", 3),
        ("clause: Annex\nclass: normative\n", "bad clause", 3),
        ("clause: Annex FF\nclass: normative\n", "bad clause", 3),
        ("clause: 16.7\nclass: guess\n", "bad class", 4),
        ("clause: 16.7\nclass: interpretation\n", "bad class", 4),
        ("clause: 16.7\nclass: interpretation I\n", "bad class", 4),
        ("clause: 16.7\nclass: interpretation 1\n", "bad class", 4),
        ("clause: 16.7\nclass: normative I1\n", "bad class", 4),
        ("clause: 16.7\nclass: must-reject\n", "needs 'support: oracle'", 4),
        ("clause: 16.7\nclass: normative\norigin:\n", "origin", 5),
    ],
)
def test_malformed_metadata(tmp_path: Path, extra: str, fragment: str, line: int) -> None:
    text = "== c\nproperty: a\n" + extra + "a : 1\n"
    with pytest.raises(TraceFileError, match=fragment) as info:
        load_cases(_write(tmp_path, text, meta=False))
    assert f"t.trace:{line}:" in str(info.value)


def test_initial_procedure_is_parsed_and_emitted(tmp_path: Path) -> None:
    (plain, disabled) = load_cases(
        _write(
            tmp_path,
            "== a\nproperty: x\nprocedure: initial\nx : 1\n"
            "== b\nproperty: x\ndisable: r\nprocedure: initial\nx : 1\n",
        )
    )
    assert plain.procedure == "initial"
    assert disabled.procedure == "initial"
    assert "  initial p: assert property (@(posedge clk) x);\n" in sv_source(plain)
    assert "  initial p: assert property (@(posedge clk) disable iff (r) x);\n" in sv_source(
        disabled
    )


def test_no_procedure_by_default(tmp_path: Path) -> None:
    (case,) = load_cases(_write(tmp_path, "== a\nproperty: x\nx : 1\n"))
    assert case.procedure is None
    assert "initial" not in sv_source(case)


@pytest.mark.parametrize(
    ("extra", "fragment"),
    [
        ("procedure: always\n", "must be 'initial'"),
        ("procedure:\n", "must be 'initial'"),
    ],
)
def test_malformed_procedure_headers(tmp_path: Path, extra: str, fragment: str) -> None:
    with pytest.raises(TraceFileError, match=fragment):
        load_cases(_write(tmp_path, "== c\nproperty: a\n" + extra + "a : 1\n"))
