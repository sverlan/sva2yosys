"""Contract of support.ebmc_corpus: the pure functions (no tool, no corpus)."""

from __future__ import annotations

from support.ebmc_corpus import (
    Desc,
    Directive,
    Expected,
    Kind,
    Outcome,
    RunMode,
    Site,
    Status,
    adapt_clock,
    assign_sites,
    choose_top,
    classify,
    drop_directives,
    has_immediate_assertions,
    match_verdicts,
    parse_check,
    parse_desc,
    parse_monitors,
    plan_runs,
    sby_text,
    sites_of,
    skip_reason,
    unbounded_cover,
)

DESC = r"""CORE buechi
cover1.sv
--buechi --bound 20 --module top
^\[top\.p0\] always \[0:9\] top\.x < 10: PROVED up to bound 20$
^\[top\.p1\] always \[0:\$\] top\.x < 10: REFUTED$
\[top\.blk\.assert\.2\] always (top.a |-> top.b): PROVED .*$
^\[top\.c0\] cover strong\(##\[0:9\] top\.x == 5\): REFUTED up to bound 4$
^\[top\.a0\] always top\.x != 5: ASSUMED$
^\[.*\] \(1 and \(##2 1\)\) \|-> top\.x == 2: PROVED$
^\[top\.q\] always top.x: FAILURE: property not supported by BDD engine$
^EXIT=10$
^SIGNAL=0$
--
^warning: ignoring
"""


def _desc(text: str = DESC) -> Desc:
    return parse_desc(text, "case")


def _verdict(
    kind: Kind = Kind.ASSERT,
    status: Status = Status.PROVED,
    bound: int | None = None,
    text: str = "x",
    label: str | None = "p0",
) -> Expected:
    scope = "main" if label is not None else None
    return Expected(scope, label, kind, status, bound, text, status.value)


def _case(*verdicts: Expected, options: str = "", tag: str = "CORE") -> Desc:
    text = f"{tag}\nx.sv\n{options}\n"
    return Desc(**{**_desc(text).__dict__, "verdicts": verdicts})


# --- parse_desc -------------------------------------------------------------------------------


def test_header_and_options_are_read() -> None:
    desc = _desc()
    assert (desc.name, desc.tag, desc.tag_words, desc.design) == (
        "case",
        "CORE",
        ("buechi",),
        "cover1.sv",
    )
    assert desc.options == ("--buechi", "--bound", "20", "--module", "top")
    assert (desc.bound, desc.top, desc.engine, desc.buechi, desc.exit_code) == (
        20,
        "top",
        "bmc",
        True,
        10,
    )


def test_defaults_without_options() -> None:
    desc = parse_desc("CORE\nx.sv\n\n^EXIT=0$\n")
    assert (desc.bound, desc.top, desc.engine, desc.buechi) == (None, "main", "bmc", False)
    assert desc.verdicts == ()


def test_engine_and_top_options() -> None:
    assert parse_desc("CORE\nx.sv\n--bdd --top t\n").engine == "bdd"
    assert parse_desc("CORE\nx.sv\n--bdd --top t\n").top == "t"
    assert parse_desc("CORE\nx.sv\n--k-induction --bound 2\n").engine == "k-induction"
    assert parse_desc("CORE\nx.sv\n--ic3\n").engine == "ic3"


def test_verdict_lines_are_unescaped_as_far_as_needed() -> None:
    p0, p1, blk, cover, assumed, wildcard, other = _desc().verdicts
    assert (p0.scope, p0.label, p0.qualified) == ("top", "p0", "top.p0")
    assert (p0.kind, p0.status, p0.bound) == (Kind.ASSERT, Status.PROVED_UP_TO_BOUND, 20)
    assert p0.text == "always [0:9] top.x < 10"
    assert (p1.status, p1.bound, p1.text) == (Status.REFUTED, None, "always [0:$] top.x < 10")
    assert (blk.label, blk.status, blk.bound) == ("blk.assert.2", Status.PROVED, None)
    assert (cover.kind, cover.status, cover.bound) == (Kind.COVER, Status.REFUTED, 4)
    assert (assumed.kind, assumed.status) == (Kind.ASSUME, Status.OTHER)
    assert (wildcard.scope, wildcard.label, wildcard.qualified) == (None, None, None)
    assert (other.status, other.known) == (Status.OTHER, False)
    assert not assumed.known and p0.known


def test_lines_after_the_separator_and_other_lines_are_not_verdicts() -> None:
    desc = parse_desc("CORE\nx.sv\n\n^EXIT=0$\n^warning: ignoring\n--\n^\\[a\\.b\\] x: PROVED$\n")
    assert desc.verdicts == ()


def test_a_missing_backslash_before_the_closing_bracket_is_tolerated() -> None:
    desc = parse_desc("CORE\nx.sv\n\n^\\[m\\.c0] cover x: PROVED$\n")
    assert desc.verdicts[0].qualified == "m.c0"


# --- adapt_clock ------------------------------------------------------------------------------

UNCLOCKED = """module main(input clk);
  reg x;
  // p0: assert property (nothing);
  p0: assert property (x);
endmodule
"""


def test_default_clocking_is_added_before_endmodule_with_the_modules_clk() -> None:
    text, changed = adapt_clock(UNCLOCKED, "main")
    assert changed
    assert text == UNCLOCKED.replace(
        "endmodule", "  default clocking @(posedge clk); endclocking\nendmodule"
    )


def test_a_new_wire_clock_is_declared_when_the_module_has_no_clk() -> None:
    source = "module main;\n  reg x;\n  assert property (x);\nendmodule\n"
    text, changed = adapt_clock(source, "main")
    assert changed
    assert "wire __ebmc_clk;" in text
    assert "default clocking @(posedge __ebmc_clk); endclocking" in text
    assert text.index("endclocking") < text.index("endmodule")


def test_a_wire_or_reg_clk_declaration_counts() -> None:
    for declaration in ("wire clk;", "reg clk = 0;", "logic [0:0] clk;"):
        source = f"module main;\n  {declaration}\n  cover property (1);\nendmodule\n"
        text, changed = adapt_clock(source, "main")
        assert changed
        assert "@(posedge clk)" in text
        assert "__ebmc_clk" not in text


def test_clocked_assertions_are_left_alone() -> None:
    source = "module main(input clk);\n  a: assert property (@(posedge clk) x);\nendmodule\n"
    assert adapt_clock(source, "main") == (source, False)


def test_an_assertion_in_an_event_controlled_statement_is_clocked() -> None:
    source = "module main(input clk);\n  always @(posedge clk) assert property (x);\nendmodule\n"
    assert adapt_clock(source, "main") == (source, False)


def test_a_named_property_with_its_own_clock_is_clocked() -> None:
    source = (
        "module main(input clk);\n  property p; @(posedge clk) x; endproperty\n"
        "  a: assert property (p);\nendmodule\n"
    )
    assert adapt_clock(source, "main") == (source, False)
    unclocked = source.replace("@(posedge clk) ", "")
    assert adapt_clock(unclocked, "main")[1]


def test_an_existing_default_clocking_is_kept() -> None:
    source = (
        "module main(input clk);\n  default clocking @(negedge clk); endclocking\n"
        "  assert property (x);\nendmodule\n"
    )
    assert adapt_clock(source, "main") == (source, False)


def test_assertions_in_another_module_leave_the_text_unchanged() -> None:
    source = (
        "module other; assert property (y); endmodule\n"
        "module main(input clk); assert property (x); endmodule\n"
    )
    assert adapt_clock(source, "main") == (source, False)


def test_comments_are_ignored_and_a_missing_top_leaves_the_text_unchanged() -> None:
    commented = "module main; // assert property (x);\n  /* cover property (y); */\nendmodule\n"
    assert adapt_clock(commented, "main") == (commented, False)
    assert adapt_clock(UNCLOCKED, "absent") == (UNCLOCKED, False)


BLOCK = """module main(input clk);
  reg [7:0] c;
  always @(posedge clk) begin : blk
    p0: cover property (c == 1);
    if (c == 10) begin
      p1: cover property (c == 100);
    end
    c = c + 1;
  end
  p2: cover property (c == 2);
endmodule
"""


def test_assertions_in_an_event_controlled_block_are_clocked_and_others_are_not() -> None:
    clocked = BLOCK.replace("  p2: cover property (c == 2);\n", "")
    assert adapt_clock(clocked, "main") == (clocked, False)  # p0 and the nested p1 inherit
    text, changed = adapt_clock(BLOCK, "main")  # p2 is outside the block
    assert changed
    assert "default clocking @(posedge clk)" in text


def test_a_block_without_an_event_control_does_not_clock_its_assertions() -> None:
    plain = BLOCK.replace("always @(posedge clk)", "always_comb").replace("  p2:", "  //")
    assert adapt_clock(plain, "main")[1]
    after = BLOCK.replace("  p2: cover", "  q: cover").replace("    c = c + 1;", "")
    assert adapt_clock(after, "main")[1]


# --- skip_reason ------------------------------------------------------------------------------


def test_a_runnable_case_has_no_skip_reason() -> None:
    assert skip_reason(_case(_verdict())) is None


def test_skip_reasons() -> None:
    assert "KNOWNBUG" in str(skip_reason(_case(_verdict(), tag="KNOWNBUG")))
    assert "EXIT=2" in str(skip_reason(parse_desc("CORE\nx.sv\n\n^EXIT=2$\n")))
    assert "--aig" in str(skip_reason(_case(_verdict(), options="--aig")))
    assert "no property" in str(skip_reason(_case()))
    assert "no property" in str(skip_reason(_case(_verdict(status=Status.OTHER))))
    assert "no property" in str(skip_reason(_case(_verdict(kind=Kind.ASSUME))))


def test_several_clock_events_skip_the_case() -> None:
    two = "always @(posedge a) x<=1; always @(posedge b) y<=1;"
    one = "always @(posedge a) x<=1; always @ (posedge  a) y<=1;"
    case = _case(_verdict())
    assert "several clock events" in str(skip_reason(case, two))
    assert skip_reason(case, one) is None
    assert skip_reason(case, "// @(posedge a) @(posedge b)\nalways @(posedge a) x<=1;") is None


# --- plan_runs --------------------------------------------------------------------------------


def _plan(*verdicts: Expected, options: str = "") -> dict[str, tuple[int, ...]]:
    return {run.text: run.verdicts for run in plan_runs(_case(*verdicts, options=options))}


def test_a_bounded_answer_is_a_bmc_run_one_deeper() -> None:
    plan = _plan(_verdict(status=Status.PROVED_UP_TO_BOUND, bound=20))
    assert plan == {"bmc@21": (0,)}


def test_a_refutation_with_a_bound_option_is_bmc_and_without_one_a_prove_run() -> None:
    refuted = _verdict(status=Status.REFUTED)
    assert _plan(refuted, options="--bound 10") == {"bmc@11": (0,)}
    assert _plan(refuted) == {"prove": (0,)}


def test_an_unbounded_proof_or_engine_is_a_prove_run() -> None:
    assert _plan(_verdict()) == {"prove": (0,)}
    bounded = _verdict(status=Status.PROVED_UP_TO_BOUND, bound=5)
    assert _plan(bounded, options="--k-induction --bound 2") == {"prove": (0,)}
    assert _plan(_verdict(status=Status.REFUTED), options="--bdd") == {"prove": (0,)}


def test_covers_are_cover_runs() -> None:
    reached = _verdict(kind=Kind.COVER)
    unreached = _verdict(kind=Kind.COVER, status=Status.REFUTED, bound=4)
    assert _plan(reached, unreached, options="--bound 10") == {"cover@11": (0,), "cover@5": (1,)}
    assert _plan(reached) == {"cover@41": (0,)}
    assert _plan(reached, options="--bdd") == {"cover@41": (0,)}


def test_liveness_answers_also_get_a_live_run() -> None:
    plain = _verdict(status=Status.REFUTED, text="always x")
    eventually = _verdict(status=Status.REFUTED, text="s_eventually x")
    plan = _plan(plain, eventually, options="--bound 10")
    assert plan == {"bmc@11": (0, 1), "live": (1,)}
    assert _plan(plain, options="--buechi --bound 10") == {"bmc@11": (0,), "live": (0,)}


def test_verdicts_without_a_known_answer_have_no_run() -> None:
    other = _verdict(status=Status.OTHER)
    assume = _verdict(kind=Kind.ASSUME, status=Status.OTHER)
    assert _plan(other, assume) == {}


# --- classify ---------------------------------------------------------------------------------


def test_asserts_agree_on_pass_for_proved_and_on_fail_for_refuted() -> None:
    proved = _verdict(status=Status.PROVED_UP_TO_BOUND, bound=5)
    refuted = _verdict(status=Status.REFUTED)
    assert classify(proved, Outcome("pass")) == "agrees"
    assert classify(proved, Outcome("fail", failed_in=(RunMode.BMC,))) == "disagrees"
    assert classify(refuted, Outcome("fail", failed_in=(RunMode.BMC,))) == "agrees"
    assert classify(refuted, Outcome("pass")) == "disagrees"


def test_a_liveness_failure_against_a_bounded_proof_is_a_bound_gap() -> None:
    proved = _verdict(status=Status.PROVED_UP_TO_BOUND, bound=5)
    assert classify(proved, Outcome("fail", failed_in=(RunMode.LIVE,))) == "bound-gap"
    both = Outcome("fail", failed_in=(RunMode.BMC, RunMode.LIVE))
    assert classify(proved, both) == "disagrees"
    unbounded = _verdict(status=Status.PROVED)
    assert classify(unbounded, Outcome("fail", failed_in=(RunMode.LIVE,))) == "disagrees"


def test_covers_agree_when_reached_matches_proved() -> None:
    reached = _verdict(kind=Kind.COVER, status=Status.PROVED)
    unreached = _verdict(kind=Kind.COVER, status=Status.REFUTED, bound=3)
    assert classify(reached, Outcome("reached")) == "agrees"
    assert classify(reached, Outcome("unreached")) == "disagrees"
    assert classify(unreached, Outcome("unreached")) == "agrees"
    assert classify(unreached, Outcome("reached")) == "disagrees"


def test_an_unbounded_unreached_cover_is_a_bound_gap_and_a_reached_one_agrees() -> None:
    unreached = _verdict(kind=Kind.COVER, status=Status.REFUTED)
    reached = _verdict(kind=Kind.COVER, status=Status.PROVED)
    assert classify(unreached, Outcome("unreached"), unbounded=True) == "bound-gap"
    assert classify(unreached, Outcome("unreached")) == "agrees"
    assert classify(unreached, Outcome("reached"), unbounded=True) == "disagrees"
    assert classify(reached, Outcome("reached"), unbounded=True) == "agrees"


def test_a_cover_is_unbounded_without_a_bound_or_with_an_unbounded_engine() -> None:
    unreached = _verdict(kind=Kind.COVER, status=Status.REFUTED)
    bounded = _verdict(kind=Kind.COVER, status=Status.REFUTED, bound=4)
    assert unbounded_cover(_case(unreached), unreached)
    assert not unbounded_cover(_case(unreached, options="--bound 9"), unreached)
    assert not unbounded_cover(_case(bounded), bounded)
    assert unbounded_cover(_case(bounded, options="--bdd"), bounded)
    assert not unbounded_cover(_case(_verdict()), _verdict())


def test_immediate_assertions_of_the_design_are_recognised() -> None:
    assert has_immediate_assertions("always @(posedge clk) assert (x);")
    assert has_immediate_assertions("initial begin a: assume #0 (x); end")
    assert has_immediate_assertions("always_comb assert final (x);")
    assert not has_immediate_assertions("p0: assert property (x); cover property (y);")
    assert not has_immediate_assertions("// assert (x);\n/* assume (y) */")


def test_non_answers_pass_through() -> None:
    verdict = _verdict()
    assert classify(verdict, Outcome("rejected", "a fragment")) == "rejected: a fragment"
    assert classify(verdict, Outcome("error", "timeout")) == "error: timeout"
    assert classify(verdict, Outcome("skipped", "X-check")) == "skipped: X-check"
    assert classify(verdict, Outcome("no-directive")).startswith("skipped: no concurrent")
    other = _verdict(status=Status.OTHER)
    assert classify(other, Outcome("pass")).startswith("skipped: no known answer")


# --- check output, monitors, matching ---------------------------------------------------------

CHECK = """ok        main.p0  assert  slots=0  form=direct  bits=9
ok        main.p1  assert  slots=0  form=configurations  bits=2  live=pending
rejected  C:\\dir\\a.sv:21:31  main.p3: property operator 'implies' is not supported yet
skipped   a.sv:3:4  main.(unnamed): X-check: nothing left
ok        main.(unnamed)  cover  slots=0  form=direct  bits=1
warning   a.sv:9:1  main.p1: something
4 assertion(s): 3 accepted, 1 rejected
"""


def test_check_output_is_parsed_in_order() -> None:
    directives = parse_check(CHECK)
    assert [(d.label, d.status) for d in directives] == [
        ("main.p0", "ok"),
        ("main.p1", "ok"),
        ("main.p3", "rejected"),
        ("main.(unnamed)", "skipped"),
        ("main.(unnamed)", "ok"),
    ]
    assert [d.live for d in directives] == [False, True, False, False, False]
    assert (directives[2].line, directives[2].column) == (21, 31)
    assert directives[2].message == "property operator 'implies' is not supported yet"
    assert directives[4].kind == "cover"


MONITORS = """// generated
// sva2yosys monitor: assert p0
// source: a.sv:12:11
module __sva2yosys_p0 #(parameter bit CHECK = 1'b1) (
endmodule
// sva2yosys monitor: assume (unnamed)
module __sva2yosys_p1 #(parameter bit CHECK = 1'b1) (
// sva2yosys monitor: cover blk.c
module __sva2yosys_p2_c0 #(
// sva2yosys monitor: cover blk.c
module __sva2yosys_p2_c1 #(
"""


def test_monitor_sites_are_read_once_per_number() -> None:
    assert parse_monitors(MONITORS) == (
        Site("p0", 0, "assert"),
        Site("(unnamed)", 1, "assume"),
        Site("blk.c", 2, "cover"),
    )


def _directive(label: str, kind: str = "assert", status: str = "ok") -> Directive:
    return Directive(label, kind, status, "", False)


def test_sites_belong_to_directives_by_name_suffix_and_unnamed_rank() -> None:
    monitors = parse_monitors(MONITORS)
    directives = [
        _directive("main.p0"),
        _directive("main.(unnamed)", "assume"),
        _directive("main.blk.c", "cover"),
    ]
    assert assign_sites(directives, monitors) == (
        frozenset({0}),
        frozenset({1}),
        frozenset({2}),
    )
    assert assign_sites([_directive("main.q")], monitors) == (frozenset(),)


def test_sby_cell_names_give_site_numbers() -> None:
    names = [
        "main.__sva2yosys_i3._witness_.check_assert_x_sv_1_2",
        "top.u.__sva2yosys_i12_c1._witness_.y",
        "main.other",
    ]
    assert sites_of(names) == {3, 12}


def test_verdicts_match_directives_by_label_ordinal_and_wildcard_order() -> None:
    directives = [
        _directive("main.p0"),
        _directive("main.(unnamed)"),
        _directive("main.p5", "assume"),
        _directive("main.(unnamed)"),
        _directive("main.p6"),
    ]
    verdicts = [
        _verdict(label="p0"),
        _verdict(label="assert.2"),
        _verdict(label="nothing"),
        _verdict(label=None),
        _verdict(label=None),
    ]
    matched = match_verdicts(verdicts, directives)
    assert matched == {0: 0, 1: 3, 3: 1, 4: 4}  # wildcards take the unmatched, in order


def test_a_wildcard_does_not_take_a_directive_of_another_kind() -> None:
    directives = [_directive("main.c", "cover"), _directive("main.a", "assert")]
    assert match_verdicts([_verdict(label=None)], directives) == {0: 1}


# --- dropping rejected directives -------------------------------------------------------------

SOURCE = """module main(input clk);
  reg x;
  initial p0: assert property (x);
  p1: assert property (x implies x);
  p2: cover property (x);
endmodule
"""


def test_the_statement_around_a_position_is_blanked() -> None:
    text = drop_directives(SOURCE, [(4, 25)])
    assert text is not None
    assert "p1" not in text and "implies" not in text
    assert "p0: assert property (x);" in text and "p2: cover property (x);" in text
    assert text.count("\n") == SOURCE.count("\n")


def test_two_statements_are_dropped_together() -> None:
    text = drop_directives(SOURCE, [(3, 15), (4, 25)])
    assert text is not None
    assert "p0" not in text and "p1" not in text and "p2" in text


def test_dropping_refuses_blocks_and_unknown_positions() -> None:
    block = "module m;\n  always @(posedge c) begin assert property (x); end\nendmodule\n"
    assert drop_directives(block, [(2, 45)]) is None
    assert drop_directives(SOURCE, [(99, 1)]) is None
    assert drop_directives(SOURCE, [(2, 3)]) is None  # `reg x;` is not a directive


# --- the SBY file -----------------------------------------------------------------------------


def test_the_sby_files_of_the_flows() -> None:
    files = ["build/sva2yosys_monitors.sv", "build/d.sv"]
    bmc = sby_text(RunMode.BMC, 21, "main", files)
    assert "mode bmc\ndepth 21\n" in bmc and "smtbmc yices" in bmc
    assert "read_verilog -formal -sv sva2yosys_monitors.sv\nread_verilog -formal -sv d.sv\n" in bmc
    assert "prep -top main" in bmc and bmc.endswith("build/sva2yosys_monitors.sv\nbuild/d.sv\n")
    prove = sby_text(RunMode.PROVE, None, "main", files)
    assert "mode prove" in prove and "abc pdr" in prove and "depth" not in prove
    assert "mode cover\ndepth 5\n" in sby_text(RunMode.COVER, 5, "main", files)
    live = sby_text(RunMode.LIVE, None, "main", files)
    assert "mode live" in live and "aiger suprove" in live
    assert "read_verilog -formal -sv -D SVA2YOSYS_LIVE d.sv" in live


# --- choose_top -------------------------------------------------------------------------------


def test_the_top_is_the_given_one_or_the_scope_of_the_answers_or_a_root_module() -> None:
    source = "module leaf(input a); endmodule\nmodule root(input a); leaf u (.a(a)); endmodule\n"
    given = parse_desc("CORE\nx.sv\n--module leaf\n")
    assert choose_top(given, source) == "leaf"
    scoped = parse_desc("CORE\nx.sv\n\n^\\[root\\.p\\] x: PROVED$\n")
    assert choose_top(scoped, source) == "root"
    assert choose_top(parse_desc("CORE\nx.sv\n\n"), source) == "root"
    assert choose_top(parse_desc("CORE\nx.sv\n\n"), "module main; endmodule") == "main"
    assert choose_top(parse_desc("CORE\nx.sv\n--top gone\n"), source) == "root"


def test_dropping_never_removes_an_assumption() -> None:
    source = "module m(input c);\n  restrict property (@(posedge c) x);\nendmodule\n"
    assert drop_directives(source, [(2, 25)]) is None
    assumed = source.replace("restrict", "assume")
    assert drop_directives(assumed, [(2, 25)]) is None


def test_block_names_of_ebmc_are_left_out_when_matching() -> None:
    directives = [_directive("main.p0", "cover")]
    verdicts = [_verdict(label="blk.p0"), _verdict(label="0.p0")]
    assert match_verdicts(verdicts[:1], directives) == {0: 0}
    assert match_verdicts(verdicts[1:], directives) == {0: 0}
