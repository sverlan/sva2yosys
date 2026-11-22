"""Contract of support.sby_examples: the pure parts (no sby, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from support.sby_examples import (
    READ_MARKER,
    Outcome,
    ScriptPlan,
    TaskResult,
    blank_labeled_cover,
    expected_statuses,
    file_digest,
    find_sby_files,
    has_concurrent_sva,
    parse_config,
    parse_rejections,
    plan_script,
)

DUMP = """[options]
mode prove
expect pass,fail

[engines]
smtbmc

[script]
# a comment
read -define A=1
read -formal top.sv
prep -top top

[files]
top.sv
alias.sv ../other/real.sv

[file inline.sv]
module m; endmodule
"""


def test_parse_config_reads_the_sections() -> None:
    config = parse_config(DUMP)
    assert config.options == ("mode prove", "expect pass,fail")
    assert config.engines == ("smtbmc",)
    assert config.script == (
        "# a comment",
        "read -define A=1",
        "read -formal top.sv",
        "prep -top top",
    )
    assert config.files == (("top.sv", "top.sv"), ("alias.sv", "../other/real.sv"))
    assert config.inline == (("inline.sv", "module m; endmodule\n"),)
    assert config.mode == "prove"


def test_mode_defaults_to_bmc() -> None:
    assert parse_config("[options]\ndepth 3\n").mode == "bmc"


def test_expected_statuses() -> None:
    assert expected_statuses(["mode bmc"]) == {"pass"}
    assert expected_statuses(["expect fail"]) == {"fail"}
    assert expected_statuses(["expect pass, fail"]) == {"pass", "fail"}


@pytest.mark.parametrize(
    "text",
    [
        "assert property (a |-> b);",
        "x: cover property (@(posedge clk) a);",
        "assume  property (a);",
        "restrict property (a);",
        "assert (property (a));",
        "`demo_mode property (a |=> b);",
    ],
)
def test_concurrent_sva_is_found(text: str) -> None:
    assert has_concurrent_sva(f"module m; {text} endmodule")


@pytest.mark.parametrize(
    "text",
    [
        "assert (a == b);",
        "// assert property (a);",
        "/* cover property (a); */",
        "wire property_x;",
        "assert final (a);",
    ],
)
def test_other_text_is_not_concurrent_sva(text: str) -> None:
    assert not has_concurrent_sva(f"module m; {text} endmodule")


def _plan(*lines: str) -> ScriptPlan:
    plan = plan_script(lines)
    assert isinstance(plan, ScriptPlan), plan
    return plan


def test_plan_replaces_the_reads_by_one_marker() -> None:
    plan = _plan(
        "read -define A=1",
        "read -formal a.sv",
        "read -sv b.v",
        "hierarchy -check -top t",
        "prep -top t",
    )
    assert plan.sources == ("a.sv", "b.v")
    assert plan.defines == ("A=1",)
    assert plan.formal and plan.proprietary_frontend
    assert plan.top == "t"
    assert plan.script == (READ_MARKER, "hierarchy -check -top t", "prep -top t")


def test_the_native_reader_option_and_read_verilog_defines() -> None:
    plan = _plan("read -noverific", "read_verilog -D X -DY=2 f.sv", "prep -top t")
    assert not plan.proprietary_frontend and not plan.formal
    assert plan.defines == ("X", "Y=2")
    assert plan.script == (READ_MARKER, "prep -top t")


@pytest.mark.parametrize(
    ("lines", "reason"),
    [
        (["verific -sv a.sv", "prep -top t"], "proprietary-frontend"),
        (["read -verific", "read -sv a.sv", "prep -top t"], "proprietary-frontend"),
        (["read -vhdl a.vhd", "prep -top t"], "VHDL"),
        (["read_verilog -formal -assume-asserts a.v", "prep -top t"], "-assume-asserts"),
        (["read_verilog -norestrict a.v", "prep -top t"], "-norestrict"),
        (["read -sv a.v", "hierarchy -top t -chparam W 3", "prep -top t"], "-chparam"),
        (["read_rtlil model.il", "prep"], "no Verilog source"),
        (["read -sv a.v", "prep"], "no top"),
    ],
)
def test_plan_gives_the_skip_reason(lines: list[str], reason: str) -> None:
    result = plan_script(lines)
    assert isinstance(result, str) and reason in result


def test_rejections_carry_file_location_label_and_message() -> None:
    out = (
        "ok        top.a  assert  slots=0\n"
        "rejected  C:\\x\\f.sv:155:24  top.w_cover: disable iff is only supported\n"
    )
    [rejection] = parse_rejections(out)
    assert (rejection.file, rejection.line, rejection.column) == ("C:\\x\\f.sv", 155, 24)
    assert rejection.label == "top.w_cover"
    assert rejection.message == "disable iff is only supported"


SOURCE = """module m(input clk, a);
    // keep: p0: cover property (@(posedge clk) a);
    p1: assert property (@(posedge clk) a);
    property pr; @(posedge clk) l; endproperty
    let l = a;
    w: cover property (pr);
    c: cover property (@(posedge clk) a ##1 a);
endmodule
"""


def test_a_labeled_cover_is_blanked_with_its_declaration_and_orphaned_let() -> None:
    result = blank_labeled_cover(SOURCE, "w")
    assert result is not None
    assert len(result.splitlines()) == len(SOURCE.splitlines())
    assert "endproperty" not in result and "let" not in result and "w:" not in result
    assert "p1: assert property" in result and "c: cover property" in result


def test_a_plain_cover_is_blanked_alone() -> None:
    result = blank_labeled_cover(SOURCE, "c")
    assert result is not None
    assert "c: cover" not in result and "w: cover property (pr);" in result
    assert "let l = a;" in result


@pytest.mark.parametrize("label", ["p1", "p0", "missing"])
def test_an_assertion_or_an_unknown_label_is_never_blanked(label: str) -> None:
    assert blank_labeled_cover(SOURCE, label) is None


def test_task_result_classes() -> None:
    def make(outcome: Outcome, *expected: str) -> TaskResult:
        return TaskResult("f.sby", "t", frozenset(expected or {"pass"}), outcome)

    assert make(Outcome("status", "pass")).cls == "agrees"
    assert make(Outcome("status", "pass", "frag")).cls == "agrees-isolated"
    assert make(Outcome("status", "fail")).cls == "differs"
    assert make(Outcome("status", "fail"), "fail").cls == "agrees"
    assert make(Outcome("rejected", "x")).cls == "rejected"
    assert make(Outcome("skipped", "x")).cls == "skipped"
    assert make(Outcome("error", "x")).cls == "error"


def test_find_sby_files_and_digest(tmp_path: Path) -> None:
    for name in ("docs/examples/a/x.sby", "tests/y/z.sby", "tests/y/z.sv", "other/q.sby"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"a\r\nb\r\n")
    assert find_sby_files(tmp_path) == ["docs/examples/a/x.sby", "tests/y/z.sby"]
    first = file_digest(tmp_path)
    (tmp_path / "tests/y/z.sv").write_bytes(b"a\nb\n")  # line ends are normalised
    assert file_digest(tmp_path) == first
    (tmp_path / "tests/y/z.sv").write_text("changed")
    assert file_digest(tmp_path) != first
    (tmp_path / "other/q.sby").write_text("not part of the corpus")
    (tmp_path / "tests/y/z.sv").write_bytes(b"a\nb\n")
    assert file_digest(tmp_path) == first


def test_a_procedural_cover_leaves_a_null_statement_not_a_dangling_procedure() -> None:
    source = (
        "module m(input clk, a);\n"
        "    initial c: cover property (@(posedge clk) a);\n"
        "    a1: assert property (@(posedge clk) a);\n"
        "endmodule\n"
    )
    result = blank_labeled_cover(source, "c")
    assert result is not None
    assert "initial" in result and "c:" not in result
    initial_line = result.splitlines()[1]
    assert initial_line.rstrip().endswith(";") and "cover" not in initial_line
    assert "a1: assert property" in result  # the assertion is not the body of `initial`


def test_a_property_declaration_that_is_used_elsewhere_is_not_removed() -> None:
    source = (
        "module m(input clk, a);\n"
        "    property pr; @(posedge clk) a; endproperty\n"
        "    w: cover property (pr);\n"
        "    a1: assert property (pr);\n"
        "endmodule\n"
    )
    assert blank_labeled_cover(source, "w") is None
