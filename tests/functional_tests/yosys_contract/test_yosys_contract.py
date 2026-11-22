"""Opt-in functional contract: the free Yosys/SBY flow as the code generator assumes it.

See CONTRACT.md in this folder (numbers in test docstrings refer to its points).
Requires SVA2YOSYS_SBY and, for the structural checks, SVA2YOSYS_YOSYS.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from support.toolchain import SbyResult, SbyStatus, Tool, ToolError, run_sby, run_yosys

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONTRACT_SBY = {"rv": FIXTURES / "contract_rv.sby", "rs": FIXTURES / "contract_rs.sby"}
DISABLE_SBY = FIXTURES / "disable_between_edges.sby"
CHECK_STYLE_SBY = FIXTURES / "check_style.sby"

RV = "read_verilog -formal -sv"
RS = "read_slang -D FORMAL"
FRONTENDS = pytest.mark.parametrize("frontend", ["rv", "rs"])


def _run(tool: Tool, sby_file: Path, task: str, tmp_path: Path) -> SbyResult:
    return run_sby(tool, sby_file, task, tmp_path / task)


def _failed_steps(result: SbyResult) -> list[int | None]:
    return [hit.step for hit in result.failed_assertions]


# --- 2-5: the slot monitor through both frontends ------------------------------------


@FRONTENDS
def test_failure_reported_at_earliest_impossible_sample(
    sby_tool: Tool, tmp_path: Path, frontend: str
) -> None:
    """2."""
    result = _run(sby_tool, CONTRACT_SBY[frontend], "bmc_bad", tmp_path)
    assert result.status is SbyStatus.FAIL
    assert _failed_steps(result) == [3]
    assert result.failed_assertions[0].location.startswith("monitor_window.sv:19.")


@FRONTENDS
def test_correct_design_passes_bmc(sby_tool: Tool, tmp_path: Path, frontend: str) -> None:
    """3."""
    assert _run(sby_tool, CONTRACT_SBY[frontend], "bmc_ok", tmp_path).status is SbyStatus.PASS


@pytest.mark.extended
@FRONTENDS
def test_slot_monitor_proves_without_helper_invariants(
    sby_tool: Tool, tmp_path: Path, frontend: str
) -> None:
    """4."""
    assert _run(sby_tool, CONTRACT_SBY[frontend], "prove_ok", tmp_path).status is SbyStatus.PASS


@pytest.mark.extended
@FRONTENDS
def test_unreachable_cover_is_reported_unreached(
    sby_tool: Tool, tmp_path: Path, frontend: str
) -> None:
    """5."""
    result = _run(sby_tool, CONTRACT_SBY[frontend], "cover_d2", tmp_path)
    assert result.status is SbyStatus.FAIL
    assert len(result.unreached_covers) == 1
    assert result.reached_covers == ()


@pytest.mark.extended
@FRONTENDS
def test_cover_reached_at_last_allowed_sample(
    sby_tool: Tool, tmp_path: Path, frontend: str
) -> None:
    """5."""
    result = _run(sby_tool, CONTRACT_SBY[frontend], "cover_d3", tmp_path)
    assert result.status is SbyStatus.PASS
    assert [hit.step for hit in result.reached_covers] == [3]


# --- 6: asynchronous disable iff ------------------------------------------------------


@pytest.mark.extended
@pytest.mark.parametrize("frontend", ["rv", "mx"])
@pytest.mark.parametrize(
    ("case", "status", "steps"),
    [
        ("sync_pulse", SbyStatus.FAIL, [14]),
        ("async_pulse", SbyStatus.PASS, []),
        ("async_nopulse", SbyStatus.FAIL, [14]),
    ],
)
def test_disable_pulse_between_edges(
    sby_tool: Tool,
    tmp_path: Path,
    frontend: str,
    case: str,
    status: SbyStatus,
    steps: list[int],
) -> None:
    """6."""
    result = _run(sby_tool, DISABLE_SBY, f"{case}_{frontend}", tmp_path)
    assert result.status is status
    assert _failed_steps(result) == steps


# --- 7: check style versus clock mode -----------------------------------------------


@pytest.mark.extended
@pytest.mark.parametrize(
    ("task", "status", "steps"),
    [
        ("sc_comb", SbyStatus.FAIL, [3]),
        ("sc_clk", SbyStatus.FAIL, [4]),
        ("mc_comb", SbyStatus.FAIL, [4]),
        ("mc_clk", SbyStatus.PASS, []),
    ],
)
def test_check_style_depends_on_clock_mode(
    sby_tool: Tool, tmp_path: Path, task: str, status: SbyStatus, steps: list[int]
) -> None:
    """7."""
    result = _run(sby_tool, CHECK_STYLE_SBY, task, tmp_path)
    assert result.status is status
    assert _failed_steps(result) == steps


# --- 8: $global_clock -----------------------------------------------------------------


@pytest.mark.extended
def test_global_clock_register_with_read_verilog(yosys_tool: Tool) -> None:
    """8."""
    run_yosys(
        yosys_tool,
        [
            f"{RV} disable_tb.sv",
            "hierarchy -top tb_between_edges",
            "proc",
            "select -assert-count 1 t:$ff",
        ],
        FIXTURES,
    )


@pytest.mark.extended
def test_global_clock_rejected_by_read_slang(yosys_tool: Tool) -> None:
    """8."""
    with pytest.raises(ToolError, match="no global clocking is available"):
        run_yosys(yosys_tool, ["plugin -i slang", f"{RS} disable_tb.sv"], FIXTURES)


# --- 9: bind ----------------------------------------------------------------------------

_BIND_FILES = "bind_dut.sv bind_mon.sv"


@pytest.mark.extended
@pytest.mark.parametrize(("bind", "checks"), [("bind_module.sv", 2), ("bind_instance.sv", 1)])
def test_read_slang_honours_bind(yosys_tool: Tool, bind: str, checks: int) -> None:
    """9."""
    run_yosys(
        yosys_tool,
        [
            "plugin -i slang",
            f"{RS} --top top {_BIND_FILES} {bind}",
            "hierarchy -top top",
            "flatten",
            f"select -assert-count {checks} t:$check",
        ],
        FIXTURES,
    )


@pytest.mark.extended
@pytest.mark.parametrize("bind", ["bind_module.sv", "bind_instance.sv"])
def test_read_verilog_rejects_bind(yosys_tool: Tool, bind: str) -> None:
    """9: a syntax error (Yosys before 0.70 dropped the bound checker without a message)."""
    with pytest.raises(ToolError, match="syntax error"):
        run_yosys(yosys_tool, [f"{RV} {_BIND_FILES} {bind}"], FIXTURES)


@pytest.mark.extended
def test_failing_yosys_script_is_a_tool_error(yosys_tool: Tool) -> None:
    """run_yosys reports a non-zero exit."""
    with pytest.raises(ToolError, match="assert"):
        run_yosys(yosys_tool, [f"{RV} bind_mon.sv", "select -assert-count 99 t:*"], FIXTURES)


# --- 11-14: liveness ---------------------------------------------------------------------

LIVENESS_SV = "liveness_canary.sv"
LIVENESS_SBY = FIXTURES / "liveness_canary.sby"


def _live(tool: Tool, task: str, tmp_path: Path) -> SbyResult:
    return _run(tool, LIVENESS_SBY, task, tmp_path)


@pytest.mark.extended
@pytest.mark.parametrize(("top", "flavor"), [("live_true", "live"), ("fair_only", "fair")])
def test_procedural_s_eventually_gives_live_or_fair_check(
    yosys_tool: Tool, top: str, flavor: str
) -> None:
    """11."""
    run_yosys(
        yosys_tool,
        [
            f"{RV} {LIVENESS_SV}",
            f"hierarchy -top {top}",
            "proc",
            "select -assert-count 1 t:$check",
            f"select -assert-count 1 t:$check r:FLAVOR={flavor} %i",
        ],
        FIXTURES,
    )


@pytest.mark.extended
def test_module_level_clocked_s_eventually_is_a_syntax_error(yosys_tool: Tool) -> None:
    """11."""
    with pytest.raises(ToolError, match="syntax error"):
        run_yosys(yosys_tool, [f"{RV} liveness_module_clocked.sv"], FIXTURES)


def test_true_liveness_property_passes(sby_live_tool: Tool, tmp_path: Path) -> None:
    """12."""
    assert _live(sby_live_tool, "live_pass", tmp_path).status is SbyStatus.PASS


def test_false_liveness_property_fails(sby_live_tool: Tool, tmp_path: Path) -> None:
    """12."""
    assert _live(sby_live_tool, "live_fail", tmp_path).status is SbyStatus.FAIL


@pytest.mark.extended
@pytest.mark.parametrize(
    ("task", "status"),
    [
        ("live_fair_without", SbyStatus.FAIL),
        ("live_fair_with", SbyStatus.PASS),
        ("live_fair_toggle", SbyStatus.FAIL),
    ],
)
def test_assumed_s_eventually_is_a_fairness_constraint(
    sby_live_tool: Tool, tmp_path: Path, task: str, status: SbyStatus
) -> None:
    """12."""
    assert _live(sby_live_tool, task, tmp_path).status is status


@pytest.mark.extended
def test_assumption_without_assertion_is_an_sby_error(sby_live_tool: Tool, tmp_path: Path) -> None:
    """12."""
    assert _live(sby_live_tool, "live_fair_only", tmp_path).status is SbyStatus.ERROR


@pytest.mark.extended
def test_false_safety_assertion_does_not_fail_a_live_task(
    sby_live_tool: Tool, tmp_path: Path
) -> None:
    """13."""
    result = _live(sby_live_tool, "live_mixed", tmp_path)
    assert result.status is SbyStatus.PASS
    assert result.failed_assertions == ()


@pytest.mark.extended
@pytest.mark.parametrize(
    ("task", "status"),
    [
        ("live_fail", SbyStatus.FAIL),
        ("live_conc_control", SbyStatus.FAIL),
        ("live_vacuous", SbyStatus.PASS),
        ("live_conc_vacuous", SbyStatus.PASS),
    ],
)
def test_unsatisfiable_safety_assertion_makes_false_liveness_pass(
    sby_live_tool: Tool, tmp_path: Path, task: str, status: SbyStatus
) -> None:
    """13."""
    assert _live(sby_live_tool, task, tmp_path).status is status


@pytest.mark.extended
@pytest.mark.parametrize(
    ("task", "status"),
    [
        ("prove_unguarded", SbyStatus.PASS),
        ("prove_guarded_ok", SbyStatus.PASS),
        ("prove_guarded_bad", SbyStatus.FAIL),
        ("prove_unguarded_bad", SbyStatus.FAIL),
    ],
)
def test_prove_task_ignores_liveness_checks(
    sby_tool: Tool, tmp_path: Path, task: str, status: SbyStatus
) -> None:
    """14."""
    result = _live(sby_tool, task, tmp_path)
    assert result.status is status
    if status is SbyStatus.FAIL:
        assert [hit.location.split(":")[0] for hit in result.failed_assertions] == [LIVENESS_SV]
        marker = "SAFETY_UNGUARDED_BAD" if task == "prove_unguarded_bad" else "SAFE_OK ?"
        lines = (FIXTURES / LIVENESS_SV).read_text().splitlines()
        line = next(i for i, text in enumerate(lines, 1) if marker in text)
        assert result.failed_assertions[0].location.startswith(f"{LIVENESS_SV}:{line}.")


@pytest.mark.extended
def test_define_enables_the_liveness_check_only_in_the_live_task(
    sby_live_tool: Tool, tmp_path: Path
) -> None:
    """14."""
    assert _live(sby_live_tool, "live_guarded", tmp_path).status is SbyStatus.FAIL


# --- 17-19: $anyseq and the liveness witness ----------------------------------------------

WITNESS_SBY = FIXTURES / "liveness_witness.sby"


@pytest.mark.extended
def test_anyseq_is_free_in_a_prove_task(sby_tool: Tool, tmp_path: Path) -> None:
    """17."""
    result = _run(sby_tool, WITNESS_SBY, "prove_anyseq_safety", tmp_path)
    assert result.status is SbyStatus.FAIL


@pytest.mark.extended
def test_anyseq_is_free_in_a_live_task(sby_live_tool: Tool, tmp_path: Path) -> None:
    """17."""
    assert _run(sby_live_tool, WITNESS_SBY, "live_anyseq", tmp_path).status is SbyStatus.FAIL


@pytest.mark.extended
def test_anyseq_under_the_define_leaves_the_prove_task_unaffected(
    sby_tool: Tool, tmp_path: Path
) -> None:
    """18."""
    result = _run(sby_tool, WITNESS_SBY, "prove_anyseq_guarded", tmp_path)
    assert result.status is SbyStatus.PASS


@pytest.mark.extended
def test_anyseq_under_the_define_is_checked_in_the_live_task(
    sby_live_tool: Tool, tmp_path: Path
) -> None:
    """18."""
    result = _run(sby_live_tool, WITNESS_SBY, "live_anyseq_guarded", tmp_path)
    assert result.status is SbyStatus.FAIL


@pytest.mark.extended
@pytest.mark.parametrize(
    ("task", "status"),
    [
        ("live_wit_answered", SbyStatus.PASS),
        ("live_wit_unanswered", SbyStatus.FAIL),
        ("live_wit_same_sample", SbyStatus.PASS),
        ("live_wit_next_sample", SbyStatus.PASS),
        ("live_wit_repick_mutant", SbyStatus.FAIL),
    ],
)
def test_witness_liveness_pattern(
    sby_live_tool: Tool, tmp_path: Path, task: str, status: SbyStatus
) -> None:
    """19."""
    assert _run(sby_live_tool, WITNESS_SBY, task, tmp_path).status is status


# --- 16: nothing written next to the fixtures -------------------------------------------


@pytest.mark.extended
def test_runs_leave_no_files_next_to_fixtures(
    sby_tool: Tool, yosys_tool: Tool, tmp_path: Path
) -> None:
    """16."""
    before = sorted(p.name for p in FIXTURES.iterdir())
    _run(sby_tool, CONTRACT_SBY["rv"], "bmc_ok", tmp_path)
    run_yosys(yosys_tool, [f"{RV} bind_mon.sv"], FIXTURES)
    assert sorted(p.name for p in FIXTURES.iterdir()) == before


# --- 20: what read_slang does with the forms of generated monitors -----------------------


@pytest.mark.extended
def test_read_slang_rejects_the_procedural_liveness_form(yosys_tool: Tool) -> None:
    """20."""
    with pytest.raises(ToolError, match=r"SVA unsupported|unsupported SVA feature"):
        run_yosys(yosys_tool, ["plugin -i slang", f"{RS} --top live_true {LIVENESS_SV}"], FIXTURES)


@pytest.mark.extended
def test_read_slang_rejects_the_anyseq_call_but_accepts_the_attribute(yosys_tool: Tool) -> None:
    """20."""
    with pytest.raises(ToolError, match="unknown system name"):
        run_yosys(
            yosys_tool,
            ["plugin -i slang", f"{RS} --top anyseq_call slang_anyseq_call.sv"],
            FIXTURES,
        )
    run_yosys(
        yosys_tool, ["plugin -i slang", f"{RS} --top anyseq_attr slang_anyseq_attr.sv"], FIXTURES
    )


@pytest.mark.extended
def test_read_slang_needs_a_flag_for_a_register_read_before_its_declaration(
    yosys_tool: Tool,
) -> None:
    """20."""
    with pytest.raises(ToolError, match="used before its declaration"):
        run_yosys(
            yosys_tool, ["plugin -i slang", f"{RS} --top use_before slang_use_before.sv"], FIXTURES
        )
    run_yosys(
        yosys_tool,
        [
            "plugin -i slang",
            f"{RS} --allow-use-before-declare --top use_before slang_use_before.sv",
        ],
        FIXTURES,
    )


@pytest.mark.extended
def test_best_effort_hierarchy_keeps_the_modules_with_formal_properties(yosys_tool: Tool) -> None:
    """20."""
    files = f"--top top {_BIND_FILES} bind_module.sv"
    flat = run_yosys(yosys_tool, ["plugin -i slang", f"{RS} {files}", "ls"], FIXTURES)
    assert "1 modules:" in flat
    kept = run_yosys(
        yosys_tool,
        ["plugin -i slang", f"{RS} --best-effort-hierarchy {files}", "ls"],
        FIXTURES,
    )
    assert "5 modules:" in kept
    assert "mon$top.u2.u_mon" in kept and "dut$top.u3" in kept


# --- 21: the smtbmc engine and a wide constant read with a variable index ------------------

_WIDE_SBY = """[tasks]
bmc

[options]
mode bmc
depth 10

[engines]
{engine}

[script]
read_verilog -formal -sv wide_string.sv
prep -top wide_string
{extra}
[files]
wide_string.sv
"""


def _wide(tool: Tool, tmp_path: Path, engine: str, extra: str = "") -> SbyResult:
    (tmp_path / "wide_string.sv").write_text(
        (FIXTURES / "wide_string.sv").read_text(encoding="utf-8"), encoding="utf-8"
    )
    sby_file = tmp_path / "wide.sby"
    sby_file.write_text(_WIDE_SBY.format(engine=engine, extra=extra), encoding="utf-8")
    return run_sby(tool, sby_file, "bmc", tmp_path / "work")


# yosys-smtbmc raises Python's recursion limit (64 * 1024) and its stack only on POSIX (smtio.py:
# `if os.name == "posix"`), so the expansion overflows the default limit of 1000 on Windows only.
_WINDOWS = os.name == "nt"


def _expect_the_recursion_limit_or_the_failure(result: SbyResult) -> None:
    """On Windows the expansion stops with the recursion error; elsewhere it completes and the
    engine reports the failed assertion."""
    if _WINDOWS:
        assert result.status is SbyStatus.ERROR
        assert "RecursionError" in result.log
        assert not result.failed_assertions
    else:
        assert result.status is SbyStatus.FAIL
        assert "RecursionError" not in result.log
        assert result.failed_assertions


@pytest.mark.extended
@pytest.mark.parametrize("solver", ["yices", "boolector", "bitwuzla"])
def test_the_default_unrolling_stops_on_a_wide_string_with_a_recursion_error_on_windows(
    sby_tool: Tool, tmp_path: Path, solver: str
) -> None:
    """21."""
    _expect_the_recursion_limit_or_the_failure(_wide(sby_tool, tmp_path, f"smtbmc {solver}"))


@pytest.mark.extended
@pytest.mark.parametrize("solver", ["boolector", "bitwuzla"])
def test_nounroll_does_not_help_boolector_and_bitwuzla(
    sby_tool: Tool, tmp_path: Path, solver: str
) -> None:
    """21."""
    result = _wide(sby_tool, tmp_path, f"smtbmc --nounroll {solver}")
    _expect_the_recursion_limit_or_the_failure(result)


@pytest.mark.extended
@pytest.mark.parametrize(
    ("engine", "extra"),
    [
        ("smtbmc --nounroll yices", ""),
        ("smtbmc z3", ""),
        ("smtbmc yices", "techmap\nopt -fast\n"),
    ],
    ids=["nounroll-yices", "z3", "techmap"],
)
def test_the_workarounds_report_the_failure_of_the_wide_string(
    sby_tool: Tool, tmp_path: Path, engine: str, extra: str
) -> None:
    """21."""
    result = _wide(sby_tool, tmp_path, engine, extra)
    assert result.status is SbyStatus.FAIL, result.log[-2000:]
    assert _failed_steps(result) == [6]
