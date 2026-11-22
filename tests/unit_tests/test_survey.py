"""Contract of support.survey: the pure parts (parsing, summaries, baseline comparison)."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from support import survey
from support.survey import (
    Entry,
    RunRecord,
    Status,
    Summary,
    Tolerance,
    compare_baselines,
    load_baseline,
    normalise_reason,
    parse_check_output,
    parse_module_sizes,
    save_baseline,
    summarise,
)

ROOT = "C:\\corp"

OUTPUT = (
    "ok        top.u.p_ok  assert  slots=3  form=direct  bits=7\n"
    "ok        top.u.p_two  cover  slots=1  form=attempt  bits=None\n"
    "rejected  C:\\corp\\lib\\a.sv:37:7  top.u.p_bad: unsupported construct 'Inside'\n"
    "skipped   /corp/lib/b.sv:5:3  top.q: X-check: $isunknown(x) checks nothing\n"
    "warning   /corp/lib/b.sv:6:3  top.w: a warning line\n"
    "4 assertion(s): 2 accepted, 1 skipped (X-check), 1 rejected\n"
)


def test_parse_check_output_reads_ok_rejected_and_skipped_lines() -> None:
    lines = parse_check_output(OUTPUT)
    assert [(x.status, x.name) for x in lines] == [
        (Status.OK, "top.u.p_ok"),
        (Status.OK, "top.u.p_two"),
        (Status.REJECTED, "top.u.p_bad"),
        (Status.SKIPPED, "top.q"),
    ]
    assert (lines[0].slots, lines[0].bits) == (3, 7)
    assert (lines[1].slots, lines[1].bits) == (1, None)
    assert lines[2].location == "C:\\corp\\lib\\a.sv:37:7"
    assert lines[2].message == "unsupported construct 'Inside'"
    assert lines[3].message.startswith("X-check:")


def test_normalise_reason_masks_numbers_paths_and_identifiers() -> None:
    a = normalise_reason("the disable iff condition reads x, which may change at 12")
    b = normalise_reason("the disable iff condition reads yy, which may change at 99")
    assert a == b
    assert normalise_reason("cannot open C:\\x\\y.sv at 3") == normalise_reason(
        "cannot open D:\\other\\z.sv at 4"
    )
    assert normalise_reason("unsupported type 'foo_t'") == normalise_reason(
        "unsupported type 'bar_t'"
    )
    assert normalise_reason("x" * 500) == "x" * 150


def test_parse_module_sizes_splits_on_endmodule() -> None:
    text = "module a;\nendmodule\nmodule bb;\n  wire x;\nendmodule\n"
    sizes = parse_module_sizes(text)
    assert len(sizes) == 2
    assert sizes[0] < sizes[1]
    assert parse_module_sizes("") == ()


def _entry(status: Status, category: str = "", corpus: str = "c") -> Entry:
    return Entry(corpus, "run", "f.sv", "top.p", status, category, None, None)


def test_summarise_counts_statuses_and_top_categories() -> None:
    entries = [
        _entry(Status.OK),
        _entry(Status.OK),
        _entry(Status.REJECTED, "B"),
        _entry(Status.REJECTED, "A"),
        _entry(Status.REJECTED, "A"),
        _entry(Status.SKIPPED, "S"),
        _entry(Status.TOOL_ERROR, "E"),
    ]
    runs = [RunRecord("c", "r1", 1.5, 100, 60), RunRecord("c", "r2", 0.5, 300, 250)]
    summary = summarise(entries, runs, top=2)
    assert summary.counts == {"ok": 2, "skipped": 1, "rejected": 3, "tool-error": 1}
    assert summary.top_categories == (("A", 2), ("B", 1))
    assert (summary.total_bits, summary.max_bits) == (0, 0)
    assert summary.total_bytes == 400
    assert summary.max_module_bytes == 250
    assert summary.seconds == pytest.approx(2.0)


def test_baseline_round_trip_is_stable_and_merges(tmp_path: Path) -> None:
    summary = summarise([_entry(Status.OK)], [RunRecord("c", "r", 1.0, 10, 10)])
    path = tmp_path / "baseline.json"
    save_baseline(path, {"c": summary})
    other = summarise([_entry(Status.OK, corpus="d")], [RunRecord("d", "r", 1.0, 10, 10)])
    save_baseline(path, {"d": other})
    loaded = load_baseline(path)
    assert set(loaded) == {"c", "d"}
    assert loaded["c"] == summary
    text = path.read_text(encoding="utf-8")
    assert json.loads(text)["corpora"]["c"]["counts"]["ok"] == 1
    save_baseline(path, {})
    assert path.read_text(encoding="utf-8") == text


def _drow(
    name: str,
    status: Status = Status.OK,
    category: str = "",
    bits: int | None = 3,
    corpus: str = "c",
) -> Entry:
    return Entry(corpus, "r", "f.sv", name, status, category, None, bits)


def test_directive_rows_are_sorted_and_tab_free() -> None:
    rows = survey.directive_rows(
        [
            _drow("b"),
            _drow("a\tx", Status.REJECTED, "bad\nthing", None),
            _drow("a", bits=None),
        ]
    )
    assert [r.name for r in rows] == ["a", "a x", "b"]
    assert rows[0].bits == "-" and rows[2].bits == "3"
    assert rows[1].category == "bad thing"


def test_directive_file_round_trip_format_and_merge(tmp_path: Path) -> None:
    path = tmp_path / "d.tsv"
    survey.save_directives(
        path, survey.directive_rows([_drow("q"), _drow("p", corpus="d")]), ["c", "d"]
    )
    survey.save_directives(
        path,
        survey.directive_rows([_drow("z", Status.REJECTED, "cat", None), _drow("a")]),
        ["c"],
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "# survey-directives v2",
        "c\tr\ta\t1\tok\t\t3",
        "c\tr\tz\t1\trejected\tcat\t-",
        "d\tr\tp\t1\tok\t\t3",
    ]
    assert survey.load_directives(path) == survey.directive_rows(
        [_drow("a"), _drow("z", Status.REJECTED, "cat", None), _drow("p", corpus="d")]
    )
    before = path.read_text(encoding="utf-8")
    survey.save_directives(path, [], [])
    assert path.read_text(encoding="utf-8") == before


def test_a_corpus_surveyed_with_no_directive_loses_its_stale_rows(tmp_path: Path) -> None:
    # origin: review. The surveyed corpora were inferred from the rows.
    path = tmp_path / "d.tsv"
    survey.save_directives(
        path, survey.directive_rows([_drow("a"), _drow("p", corpus="d")]), ["c", "d"]
    )
    survey.save_directives(path, [], ["c"])
    assert [r.corpus for r in survey.load_directives(path)] == ["d"]
    stale = survey.directive_rows([_drow("a")])
    assert survey.compare_directives(stale, [], ["c"]) == ("c r a: ok//3 -> <absent>",)
    assert survey.compare_directives(stale, [], []) == ()  # an unsurveyed corpus is not compared


def test_load_directives_rejects_a_bad_file(tmp_path: Path) -> None:
    path = tmp_path / "d.tsv"
    path.write_text("c\tr\tn\tok\t\t3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        survey.load_directives(path)
    path.write_text("# survey-directives v1\nc\tr\n", encoding="utf-8")  # the old format
    with pytest.raises(ValueError):
        survey.load_directives(path)
    path.write_text("# survey-directives v2\nc\tr\n", encoding="utf-8")
    with pytest.raises(ValueError):
        survey.load_directives(path)


def test_compare_directives_reports_added_removed_and_changed() -> None:
    old = survey.directive_rows(
        [_drow("same"), _drow("gone"), _drow("flip"), _drow("big"), _drow("o", corpus="other")]
    )
    new = survey.directive_rows(
        [
            _drow("same"),
            _drow("new", Status.REJECTED, "cat", None),
            _drow("flip", Status.SKIPPED, "x", None),
            _drow("big", bits=9),
        ]
    )
    assert survey.compare_directives(old, new, ["c"]) == (
        "c r big: ok//3 -> ok//9",
        "c r flip: ok//3 -> skipped/x/-",
        "c r gone: ok//3 -> <absent>",
        "c r new: <absent> -> rejected/cat/-",
    )
    assert survey.compare_directives(old, old, ["c", "other"]) == ()


def test_compare_directives_identifies_duplicate_names_by_occurrence() -> None:
    old = survey.directive_rows([_drow("d"), _drow("d", bits=5)])
    assert survey.compare_directives(old, old, ["c"]) == ()
    new = survey.directive_rows([_drow("d")])
    assert survey.compare_directives(old, new, ["c"]) == ("c r d #2: ok//5 -> <absent>",)


def test_two_directives_with_one_name_cannot_swap_their_values_unseen() -> None:
    # origin: review. Rows were keyed by (corpus, run, name) and sorted, so the values of
    # two `(unnamed)` directives (axisgfsm: bits 0 and 1) could be exchanged without a diff.
    old = survey.directive_rows([_drow("(unnamed)", bits=0), _drow("(unnamed)", bits=1)])
    swapped = survey.directive_rows([_drow("(unnamed)", bits=1), _drow("(unnamed)", bits=0)])
    assert [r.occurrence for r in old] == [1, 2]
    assert survey.compare_directives(old, swapped, ["c"]) == (
        "c r (unnamed): ok//0 -> ok//1",
        "c r (unnamed) #2: ok//1 -> ok//0",
    )


def _summary(ok: int, rejected: int, total: int, largest: int, seconds: float) -> Summary:
    entries = [_entry(Status.OK)] * ok + [_entry(Status.REJECTED, "A")] * rejected
    return summarise(entries, [RunRecord("c", "r", seconds, total, largest)])


def test_identical_summaries_compare_clean() -> None:
    base = _summary(5, 2, 1000, 100, 10.0)
    report = compare_baselines({"c": base}, {"c": base}, Tolerance())
    assert (report.failures, report.notices) == ((), ())


def test_any_count_change_fails() -> None:
    report = compare_baselines(
        {"c": _summary(5, 2, 1000, 100, 10.0)},
        {"c": _summary(6, 1, 1000, 100, 10.0)},
        Tolerance(),
    )
    assert any("ok" in f and "5" in f and "6" in f for f in report.failures)
    assert any("A" in f for f in report.failures)


def _bits_summary(*bits: int) -> Summary:
    entries = [Entry("c", "r", "f.sv", "p", Status.OK, "", 1, b) for b in bits]
    return summarise(entries, [])


def test_state_bits_are_summed_and_growth_is_checked() -> None:
    base = _bits_summary(10, 20, 30)
    assert (base.total_bits, base.max_bits) == (60, 30)
    assert not compare_baselines(
        {"c": base}, {"c": _bits_summary(10, 20, 30)}, Tolerance()
    ).failures
    grown = compare_baselines({"c": base}, {"c": _bits_summary(10, 20, 36, 1)}, Tolerance())
    assert any("state bits" in f for f in grown.failures)
    doubled = compare_baselines({"c": base}, {"c": _bits_summary(10, 20, 60)}, Tolerance())
    assert any("largest directive" in f for f in doubled.failures)


def test_total_size_growth_beyond_tolerance_fails_but_within_does_not() -> None:
    base = {"c": _summary(5, 2, 1000, 100, 10.0)}
    assert not compare_baselines(base, {"c": _summary(5, 2, 1100, 100, 10.0)}, Tolerance()).failures
    assert compare_baselines(base, {"c": _summary(5, 2, 1101, 100, 10.0)}, Tolerance()).failures
    assert not compare_baselines(base, {"c": _summary(5, 2, 500, 100, 10.0)}, Tolerance()).failures


def test_doubled_maximum_size_fails() -> None:
    base = {"c": _summary(5, 2, 1000, 100, 10.0)}
    assert compare_baselines(base, {"c": _summary(5, 2, 1000, 200, 10.0)}, Tolerance()).failures
    assert not compare_baselines(base, {"c": _summary(5, 2, 1000, 199, 10.0)}, Tolerance()).failures


def test_time_growth_is_a_notice_not_a_failure() -> None:
    base = {"c": _summary(5, 2, 1000, 100, 10.0)}
    report = compare_baselines(base, {"c": _summary(5, 2, 1000, 100, 15.1)}, Tolerance())
    assert report.failures == ()
    assert len(report.notices) == 1
    assert not compare_baselines(base, {"c": _summary(5, 2, 1000, 100, 14.9)}, Tolerance()).notices


def test_missing_corpus_in_baseline_fails() -> None:
    report = compare_baselines({}, {"c": _summary(1, 0, 10, 10, 1.0)}, Tolerance())
    assert report.failures


def test_rejection_location_with_spaces_is_parsed() -> None:
    line = "rejected  C:\\my corp\a b.sv:3:9  top.u.p: unsupported construct 'X'\n"
    (parsed,) = parse_check_output(line)
    assert parsed.location == "C:\\my corp\a b.sv:3:9"
    assert parsed.name == "top.u.p"
    assert parsed.message == "unsupported construct 'X'"


def _run_summary(completed: bool = True, size_failed: bool = False) -> Summary:
    record = RunRecord("c", "r", 1.0, 10, 10, completed, size_failed)
    return summarise([_entry(Status.OK)], [record])


def test_incomplete_jobs_and_size_failures_are_counted() -> None:
    summary = _run_summary(completed=False, size_failed=True)
    assert (summary.incomplete_jobs, summary.size_failed_jobs) == (1, ("r",))


def test_a_new_size_failure_or_incomplete_job_fails_the_comparison() -> None:
    good = {"c": _run_summary()}
    assert compare_baselines(good, {"c": _run_summary(size_failed=True)}, Tolerance()).failures
    assert compare_baselines(good, {"c": _run_summary(completed=False)}, Tolerance()).failures
    bad = {"c": _run_summary(size_failed=True)}
    assert not compare_baselines(bad, {"c": _run_summary(size_failed=True)}, Tolerance()).failures
    assert not compare_baselines(bad, {"c": _run_summary()}, Tolerance()).failures


def test_baseline_with_failure_counts_round_trips(tmp_path: Path) -> None:
    summary = _run_summary(completed=False, size_failed=True)
    path = tmp_path / "b.json"
    save_baseline(path, {"c": summary})
    assert load_baseline(path)["c"] == summary


def test_missing_baseline_corpus_fails_only_when_all_are_required() -> None:
    base = {"c": _run_summary(), "d": _run_summary()}
    current = {"c": _run_summary()}
    assert not compare_baselines(base, current, Tolerance()).failures
    report = compare_baselines(base, current, Tolerance(), require_all=True)
    assert any(f.startswith("d:") for f in report.failures)


def _fake_run(
    results: list[tuple[int | None, str, str, float]],
) -> object:
    calls = iter(results)
    return lambda args, timeout: next(calls)


def test_crash_after_partial_output_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok = "ok        top.p  assert  slots=1  form=direct  bits=2\n"
    monkeypatch.setattr(survey, "_run", _fake_run([(3, ok, "", 0.1), (3, "", "", 0.1)]))
    _, record = survey.run_job("c", survey.Job("j", ("f.sv",)), tmp_path, tmp_path)
    assert not record.completed
    summary_line = "1 assertion(s): 1 accepted, 0 rejected\n"
    monkeypatch.setattr(
        survey, "_run", _fake_run([(0, ok + summary_line, "", 0.1), (0, "", "", 0.1)])
    )
    _, record = survey.run_job("c", survey.Job("j", ("f.sv",)), tmp_path, tmp_path)
    assert record.completed
    monkeypatch.setattr(survey, "_run", _fake_run([(1, ok, "", 0.1), (1, "", "", 0.1)]))
    _, record = survey.run_job("c", survey.Job("j", ("f.sv",)), tmp_path, tmp_path)
    assert not record.completed  # exit 1 but no summary line: the output was cut off


def test_failed_size_run_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ok = "ok        top.p  assert  slots=1  form=direct  bits=2\n"
    ok += "1 assertion(s): 1 accepted, 0 rejected\n"
    monkeypatch.setattr(survey, "_run", _fake_run([(0, ok, "", 0.1), (None, "", "", 0.1)]))
    _, record = survey.run_job("c", survey.Job("j", ("f.sv",)), tmp_path, tmp_path)
    assert record.size_failed
    assert record.monitor_bytes is None


def test_children_run_with_utf8_output(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake)
    survey._run(["--help"], 5)
    assert seen["PYTHONUTF8"] == "1"
    assert seen["PYTHONIOENCODING"] == "utf-8"


def test_empty_selection_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as stopped:
        survey.main([str(tmp_path)])
    assert stopped.value.code == 2


def _job_summary(*failed_runs: str) -> Summary:
    records = [
        RunRecord(
            "c", name, 1.0, None if name in failed_runs else 10, 10, True, name in failed_runs
        )
        for name in ("a", "b", "c")
    ]
    return summarise([_entry(Status.OK)], records)


def test_a_size_failure_swapped_between_jobs_fails_the_comparison() -> None:
    before = {"c": _job_summary("a")}
    report = compare_baselines(before, {"c": _job_summary("b")}, Tolerance())
    assert any("b" in f for f in report.failures)
    assert not compare_baselines(before, {"c": _job_summary("a")}, Tolerance()).failures


def _completed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int, stdout: str) -> bool:
    monkeypatch.setattr(
        survey, "_run", _fake_run([(status, stdout, "error: boom\n", 0.1), (1, "", "", 0.1)])
    )
    _, record = survey.run_job("c", survey.Job("j", ("f.sv",)), tmp_path, tmp_path)
    return record.completed


def test_exit_status_must_match_the_reported_rejections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok = "ok        top.p  assert  slots=1  form=direct  bits=2\n"
    none = ok + "1 assertion(s): 1 accepted, 0 rejected\n"
    some = ok + "2 assertion(s): 1 accepted, 1 rejected\n"
    assert _completed(monkeypatch, tmp_path, 0, none)
    assert _completed(monkeypatch, tmp_path, 1, some)
    assert not _completed(monkeypatch, tmp_path, 1, none)
    assert not _completed(monkeypatch, tmp_path, 0, some)


def _assertionbench_tree(root: Path) -> None:
    """A tmp copy of the checkout shape: a plain-file design, an archive design, a design whose
    archive has an unsafe member, a design without bindings and one without a property module."""
    import io
    import tarfile

    def write(path: str, text: str) -> None:
        file = root / "verified_assertions" / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")

    for group, design in (("g1", "plain"), ("g1", "packed"), ("g2", "evil"), ("g2", "nomodule")):
        write(f"{group}/{design}/bindings.sva", f"bind {design} i_{design} u();\n")
        write(f"{group}/{design}/{design}.v", f"module {design}(input clk); endmodule\n")
        write(f"{group}/{design}/{design}_b.v", "module other; endmodule\n")
    write("g1/plain/property_backup.sva", "module i_plain; endmodule\n")
    write("g1/plain/property.sva", "module i_plain; endmodule\n")
    write("g1/plain/notes.sva", "// no module here\n")
    write("g1/nobind/nobind.v", "module nobind; endmodule\n")
    write("g1/nobind/property.sva", "module i_nobind; endmodule\n")

    def archive(design: str, members: list[tuple[str, str]]) -> None:
        path = root / "verified_assertions" / ("g1" if design == "packed" else "g2") / design
        with tarfile.open(path / "property.sva.tar.gz", "w:gz") as tar:
            for name, text in members:
                data = text.encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

    archive("packed", [("./x/property.sva", "module i_packed; endmodule\n")])
    archive(
        "evil",
        [
            ("../escape.sva", "module i_evil; endmodule\n"),
            ("/abs/property.sva", "module i_evil; endmodule\n"),
            ("C:/drive.sva", "module i_evil; endmodule\n"),
            ("nested/D:escape.sva", "module i_evil; endmodule\n"),
            ("colon:name.sva", "module i_evil; endmodule\n"),
        ],
    )
    archive("nomodule", [("property.sva", "module unrelated; endmodule\n")])


def test_assertionbench_recipe_plans_one_job_per_folder_with_bindings(tmp_path: Path) -> None:
    corpus = tmp_path / "assertion_data_for_LLM"
    corpus.mkdir()
    _assertionbench_tree(corpus)
    scratch = tmp_path / "scratch"
    recipe = next(r for r in survey.RECIPES if r.name == "ab")
    assert recipe.directory == "assertion_data_for_LLM"
    jobs = {job.label: job.args for job in recipe.plan(corpus, scratch)}
    assert sorted(jobs) == ["ab/g1/packed", "ab/g1/plain"]
    base = corpus / "verified_assertions" / "g1"
    plain = jobs["ab/g1/plain"]
    assert plain[:2] == (str(base / "plain" / "bindings.sva"), str(base / "plain" / "property.sva"))
    assert plain[2:] == (
        "-I",
        str(base / "plain"),
        "-v",
        str(base / "plain" / "plain.v"),
        "-v",
        str(base / "plain" / "plain_b.v"),
        "--top",
        "plain",
    )
    packed = jobs["ab/g1/packed"]
    extracted = scratch / "g1" / "packed" / "property.sva"
    assert packed[1] == str(extracted)
    assert extracted.read_text(encoding="utf-8") == "module i_packed; endmodule\n"
    assert not (tmp_path / "escape.sva").exists()
    assert not (scratch / "g2" / "evil").exists()


def test_assertionbench_headers_are_include_paths_not_design_files(tmp_path: Path) -> None:
    corpus = tmp_path / "assertion_data_for_LLM"
    folder = corpus / "verified_assertions" / "g" / "d"
    folder.mkdir(parents=True)
    (folder / "bindings.sva").write_text("bind d i_d u();\n")
    (folder / "property.sva").write_text("module i_d; endmodule\n")
    (folder / "d.v").write_text(
        '`include "timescale.v"\n`include "defs/defines.v"\nmodule d; endmodule\n'
    )
    (folder / "timescale.v").write_text("`timescale 1ns/1ps\n")
    (folder / "defines.v").write_text("`define W 8\n")
    (folder / "other.v").write_text('// `include "other_not_included.v"\nmodule other; endmodule\n')
    recipe = next(r for r in survey.RECIPES if r.name == "ab")
    [job] = recipe.plan(corpus, tmp_path / "scratch")
    assert job.args[2:4] == ("-I", str(folder))
    assert job.args[4:] == ("-v", str(folder / "d.v"), "-v", str(folder / "other.v"), "--top", "d")


def test_aggregate_only_corpora_leave_the_directive_baseline() -> None:
    def entry(corpus: str) -> Entry:
        return Entry(corpus, "run", "f.sv", "d", Status.OK, "", 1, 2)

    assert {"ab"} == survey.AGGREGATE_ONLY
    kept = survey.directive_entries([entry("ab"), entry("axi"), entry("ab"), entry("cc")])
    assert [e.corpus for e in kept] == ["axi", "cc"]


def test_every_recipe_limits_its_runs_and_assertionbench_allows_more() -> None:
    """A run past its recipe's limit is a timeout; AssertionBench jobs, with tens of
    thousands of mined assertions, get 300 seconds (120 by default) so that their result
    does not depend on machine load."""
    limits = {recipe.name: recipe.timeout for recipe in survey.RECIPES}
    assert limits.pop("ab") == 300
    assert set(limits.values()) == {120}


def test_assertionbench_leaves_out_the_design_that_cannot_finish() -> None:
    """The survey's AssertionBench jobs exclude MAC_rx_FF (144261 assertions in one job),
    with its reason recorded."""
    assert set(survey._AB_TOO_LARGE) == {"MAC_rx_FF"}
    assert "144261" in survey._AB_TOO_LARGE["MAC_rx_FF"]


def test_assertionbench_planner_skips_a_design_left_out(tmp_path: Path) -> None:
    """The planner makes no job for a design of ``_AB_TOO_LARGE`` (and the recipe lists it
    with its reason), while an ordinary design folder is a job."""
    for design in ("MAC_rx_FF", "small"):
        folder = tmp_path / "verified_assertions" / "group" / design
        folder.mkdir(parents=True)
        (folder / "bindings.sva").write_text(f"bind {design} i_{design} p(.*);\n")
        (folder / f"{design}.v").write_text(f"module {design}; endmodule\n")
        (folder / "property.sva").write_text(f"module i_{design}; endmodule\n")
    jobs = survey._plan_assertionbench(tmp_path, tmp_path / "scratch")
    assert [job.label for job in jobs] == ["ab/group/small"]
    recipe = next(r for r in survey.RECIPES if r.name == "ab")
    assert dict(recipe.left_out) == survey._AB_TOO_LARGE


def test_run_job_limits_both_runs_with_the_given_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_job`` passes its ``timeout`` to the check run and to the ``-o`` run."""
    limits: list[int] = []

    def fake_run(args: object, timeout: int) -> tuple[int | None, str, str, float]:
        limits.append(timeout)
        return 0, "", "", 0.1

    ok = survey.ParsedLine(survey.Status.OK, "p", "", "", 1, 1)
    monkeypatch.setattr(survey, "_run", fake_run)
    monkeypatch.setattr(survey, "parse_check_output", lambda stdout: [ok])
    job = survey._job("x/job", tmp_path / "a.sv")
    survey.run_job("x", job, tmp_path, tmp_path / "scratch", 300)
    assert limits == [300, 300]


def _fake_parallel_run(
    active: list[int], peak: list[int], outs: list[str]
) -> Callable[[Sequence[str], int], tuple[int | None, str, str, float]]:
    """A ``_run`` whose duration depends on the job (so jobs finish out of order) and that
    records the concurrency reached and the ``-o`` directories used."""
    lock = threading.Lock()

    def fake_run(args: Sequence[str], timeout: int) -> tuple[int | None, str, str, float]:
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        name = args[0]
        time.sleep(0.02 * (5 - int(name[-1])))  # job 0 is the slowest
        with lock:
            active[0] -= 1
        if "-o" in args:
            outs.append(args[list(args).index("-o") + 1])
            return 0, "", "", 0.1
        line = f"ok        top.p{name[-1]}  assert  slots=1  form=direct  bits={name[-1]}\n"
        return 0, line + "1 assertion(s): 1 accepted, 0 rejected\n", "", 0.1

    return fake_run


def _parallel_jobs() -> list[survey.Job]:
    return [survey.Job(f"j{i}", (f"f{i}",)) for i in range(5)]


def test_parallel_jobs_give_the_results_of_a_sequential_run_in_plan_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = {}
    for workers in (1, 4):
        monkeypatch.setattr(survey, "_run", _fake_parallel_run([0], [0], []))
        results[workers] = survey.run_corpus_jobs(
            "c", _parallel_jobs(), tmp_path, tmp_path, 300, workers
        )
    assert results[1] == results[4]
    assert [record.run for _, record in results[4]] == ["j0", "j1", "j2", "j3", "j4"]
    assert [entries[0].name for entries, _ in results[4]] == [f"top.p{i}" for i in range(5)]


def test_parallel_jobs_overlap_and_a_single_worker_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    peaks = {}
    for workers in (1, 4):
        peak = [0]
        monkeypatch.setattr(survey, "_run", _fake_parallel_run([0], peak, []))
        survey.run_corpus_jobs("c", _parallel_jobs(), tmp_path, tmp_path, 300, workers)
        peaks[workers] = peak[0]
    assert peaks[1] == 1
    assert 2 <= peaks[4] <= 4


def test_concurrent_jobs_write_their_monitors_to_directories_of_their_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outs: list[str] = []
    monkeypatch.setattr(survey, "_run", _fake_parallel_run([0], [0], outs))
    survey.run_corpus_jobs("c", _parallel_jobs(), tmp_path, tmp_path, 300, 4)
    assert len(outs) == 5
    assert len(set(outs)) == 5
    assert all(Path(out).parent == tmp_path for out in outs)


@pytest.mark.parametrize("value", ["0", "-2", "x"])
def test_jobs_must_be_a_positive_integer(tmp_path: Path, value: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        survey.main([str(tmp_path), "--jobs", value, "axi"])
    assert stopped.value.code == 2


def test_progress_goes_to_standard_error_one_line_per_finished_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(survey, "_run", _fake_parallel_run([0], [0], []))
    survey.run_corpus_jobs("c", _parallel_jobs(), tmp_path, tmp_path, 300, 4)
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert sorted(line.split()[2] for line in lines) == ["j0", "j1", "j2", "j3", "j4"]
    assert [line.split()[1] for line in lines] == [f"{n}/5" for n in range(1, 6)]
    assert all(line.startswith("c ") for line in lines)
