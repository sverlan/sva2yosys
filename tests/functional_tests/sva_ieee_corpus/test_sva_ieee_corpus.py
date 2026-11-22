"""The sva-ieee-corpus contract (CONTRACT.md): freshness, stimulus, elaboration, round trip,
for every category of case (steps 1 and 2).

Expectations come from the trace tables (``load_all_cases``), never from the generator.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from support.ebmc_differential import load_all_cases, traces_digest
from support.toolchain import SbyStatus, Tool, run_sby
from support.trace_driver import decode_row_string, row_string
from support.trace_suite import (
    CORPUS_DIRECTORY,
    TERNARY_FROM,
    Stimulus,
    build_corpus,
    check_corpus,
    write_corpus,
)
from support.tracefile import TraceCase
from sva2yosys.cli import main
from sva2yosys.slang.invoke import SlangError, SlangOptions, compile_design, compile_texts

REGENERATE = "uv run python tests/support/trace_suite.py --write"
CASES = tuple(load_all_cases())
EXPORTED = list(CASES)  # point 4: every case of the tables is exported
SIM_CASES = [c for c in CASES if c.loop is None]  # a lasso has no simulation form (point 30)
LASSOS = [c for c in CASES if c.loop is not None]
BOUNDED = [c for c in CASES if c.kind == "assert" and c.loop is None and c.procedure is None]


def _category(case: TraceCase) -> str:
    """Independent rule of point 5: initial, cover, assume, live (a loop), else bounded."""
    if case.procedure == "initial":
        return "initial"
    if case.kind in ("cover", "assume"):
        return case.kind
    return "live" if case.loop is not None else "bounded"


def _stem(case: TraceCase) -> str:
    return Path(case.file).stem


_FORMS = {
    "sim.sv": ("sim", "sv"),
    "formal.sv": ("formal", "sv"),
    "csv": ("traces/csv", "csv"),
    "wave.json": ("traces/wavedrom", "json"),
}


def _key(case: TraceCase, form: str) -> str:
    """The corpus path of ``case`` in ``form`` (sim.sv, formal.sv, csv or wave.json)."""
    root, extension = _FORMS[form]
    return f"{root}/{_category(case)}/{_stem(case)}/{case.name}.{extension}"


# the cases written as string rows: every case of fewer than TERNARY_FROM samples (point 25)
STRING_CASES = [c for c in CASES if len(c.samples) < TERNARY_FROM]


def _forms_of(case: TraceCase) -> tuple[str, ...]:
    """The SV forms a case has: formal always, sim unless it is a lasso (point 30)."""
    return ("formal.sv",) if case.loop is not None else ("formal.sv", "sim.sv")


def _directive(case: TraceCase) -> str:
    """The directive of point 8 (no semicolon, no ``initial`` wrapper)."""
    disable = f"disable iff ({case.disable}) " if case.disable is not None else ""
    return f"p: {case.kind} property (@(posedge clk) {disable}{case.property})"


def _wrapper(case: TraceCase) -> str:
    return "initial " if case.procedure == "initial" else ""


def _code(text: str) -> str:
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("//"))


@pytest.fixture(scope="module")
def corpus() -> dict[str, str]:
    return build_corpus(CASES, traces_digest())


@pytest.fixture(scope="module")
def written(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus written once by ``write_corpus``; tests copy it before changing anything."""
    root = tmp_path_factory.mktemp("corpus")
    write_corpus(root)
    return root


@pytest.fixture
def copy(written: Path, tmp_path: Path) -> Path:
    target = tmp_path / "corpus"
    shutil.copytree(written, target)
    return target


# --- freshness (points 19, 20) -----------------------------------------------------------------


def test_the_committed_corpus_is_fresh() -> None:
    problems = check_corpus(CORPUS_DIRECTORY)
    assert not problems, f"run `{REGENERATE}`; differences: {problems[:5]}"


def test_a_freshly_written_corpus_checks_clean(written: Path) -> None:
    assert check_corpus(written) == []


def test_write_corpus_writes_every_built_file(written: Path, corpus: dict[str, str]) -> None:
    for key, text in corpus.items():
        assert (written / key).read_bytes() == text.encode("utf-8"), key


def test_a_changed_file_is_reported(copy: Path) -> None:
    target = copy / _key(EXPORTED[0], "csv")
    target.write_text(target.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
    problems = check_corpus(copy)
    assert len(problems) == 1
    assert EXPORTED[0].name in problems[0]
    assert "changed" in problems[0].lower() or "stale" in problems[0].lower()


def test_a_missing_file_is_reported(copy: Path) -> None:
    (copy / _key(EXPORTED[0], "formal.sv")).unlink()
    problems = check_corpus(copy)
    assert len(problems) == 1
    assert "missing" in problems[0].lower()
    assert _key(EXPORTED[0], "formal.sv") in problems[0]


def test_an_extra_generated_file_is_reported(copy: Path) -> None:
    extra = copy / "sim" / "bounded" / "and" / "no_such_case.sv"
    extra.write_text("// leftover\n", encoding="utf-8")
    problems = check_corpus(copy)
    assert len(problems) == 1
    assert "extra" in problems[0].lower()
    assert "sim/bounded/and/no_such_case.sv" in problems[0]


def test_a_changed_or_missing_file_of_a_lasso_case_is_reported(copy: Path) -> None:
    lasso = LASSOS[0]
    target = copy / _key(lasso, "csv")
    target.write_text(target.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
    (copy / _key(lasso, "wave.json")).unlink()
    problems = check_corpus(copy)
    assert len(problems) == 2
    assert any("changed" in p.lower() and _key(lasso, "csv") in p for p in problems)
    assert any("missing" in p.lower() and _key(lasso, "wave.json") in p for p in problems)


def test_a_sim_file_of_a_lasso_case_is_an_extra_file(copy: Path) -> None:
    extra = copy / _key(LASSOS[0], "sim.sv")
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("// leftover\n", encoding="utf-8")
    problems = check_corpus(copy)
    assert len(problems) == 1 and "extra" in problems[0].lower()
    write_corpus(copy)  # the writer removes it
    assert not extra.exists()
    assert check_corpus(copy) == []


def test_a_stale_manifest_is_reported(copy: Path) -> None:
    manifest = copy / "manifest.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["traces-sha256"] = "0" * 64
    manifest.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    problems = check_corpus(copy)
    assert problems
    assert any("manifest.json" in problem for problem in problems)


def test_a_missing_manifest_is_reported(copy: Path) -> None:
    (copy / "manifest.json").unlink()
    problems = check_corpus(copy)
    assert any("manifest.json" in p and "missing" in p.lower() for p in problems)


def test_hand_written_files_are_not_checked(copy: Path) -> None:
    (copy / "README.md").write_text("hand written\n", encoding="utf-8")
    (copy / "LICENSE").write_text("MIT\n", encoding="utf-8")
    assert check_corpus(copy) == []


def test_write_corpus_removes_stale_generated_files_but_never_readme_or_license(
    copy: Path,
) -> None:
    (copy / "README.md").write_text("hand written\n", encoding="utf-8")
    (copy / "LICENSE").write_text("MIT\n", encoding="utf-8")
    stale = copy / "sim" / "bounded" / "and" / "gone.sv"
    stale.write_text("// leftover\n", encoding="utf-8")
    other = copy / "traces" / "wavedrom" / "bounded" / "and" / "gone.json"
    other.write_text("{}\n", encoding="utf-8")
    write_corpus(copy)
    assert not stale.exists()
    assert not other.exists()
    assert (copy / "README.md").read_text(encoding="utf-8") == "hand written\n"
    assert (copy / "LICENSE").read_text(encoding="utf-8") == "MIT\n"
    assert check_corpus(copy) == []


def test_write_corpus_repairs_changed_and_missing_files(copy: Path) -> None:
    (copy / _key(EXPORTED[1], "csv")).write_text("junk\n", encoding="utf-8")
    (copy / _key(SIM_CASES[2], "sim.sv")).unlink()
    write_corpus(copy)
    assert check_corpus(copy) == []


# --- stimulus (points 12, 23) ---------------------------------------------------------------------


def _literals(text: str) -> list[str]:
    return re.findall(r'"([^"\n]*)"', _code(text))


def test_the_cases_of_the_threshold_are_ternary_and_the_others_are_string_rows(
    corpus: dict[str, str],
) -> None:
    """25: 30 samples or more are written as conditionals; every other case has string rows."""
    assert [c.name for c in EXPORTED if len(c.samples) >= TERNARY_FROM] == [
        "ieee_16_7_req_range_4_to_32_latest",
        "ieee_16_7_req_range_4_to_32_too_early_and_too_late",
    ]
    manifest = json.loads(corpus["manifest.json"])
    assert manifest["stimulus"] == "string" and manifest["ternary-from"] == TERNARY_FROM
    for case in EXPORTED:
        for extension in _forms_of(case):
            code = _code(corpus[_key(case, extension)])
            if len(case.samples) >= TERNARY_FROM:
                assert "ROW_" not in code and "row_idx" not in code, case.name
            else:
                assert "ROW_" in code or not case.widths, case.name


def test_every_stimulus_string_of_the_corpus_decodes_to_the_table_and_is_canonical(
    corpus: dict[str, str],
) -> None:
    for case in STRING_CASES:
        for extension in _forms_of(case):
            literals = set(_literals(corpus[_key(case, extension)]))
            for name, width in case.widths.items():
                text = row_string(case, name)
                assert text in literals, (case.name, extension, name)
                expected = tuple(sample[name] for sample in case.samples)
                assert decode_row_string(text, width) == expected
                digits = (width + 3) // 4
                fields = text.split(" ")
                if width == 1:
                    assert all(f in (".", "1") for f in fields)
                else:
                    assert "." not in text
                    assert all(re.fullmatch(f"[0-9A-F]{{{digits}}}", f) for f in fields)


def test_the_formal_form_has_no_other_string_than_the_rows(corpus: dict[str, str]) -> None:
    for case in STRING_CASES:
        literals = set(_literals(corpus[_key(case, "formal.sv")]))
        rows = {row_string(case, name) for name in case.widths}
        assert rows <= literals, case.name
        # the decoder compares characters: "1", "0", "9", "A" are the only other literals
        assert {text for text in literals - rows if len(text) != 1} == set(), case.name


def test_a_comment_above_the_strings_repeats_the_rows_of_the_trace_file(
    corpus: dict[str, str],
) -> None:
    for case in EXPORTED:
        comments = [
            line.lstrip()
            for line in corpus[_key(case, "formal.sv")].split("\n")
            if line.lstrip().startswith("//")
        ]
        for name in case.widths:
            assert any(re.match(rf"//\s*{re.escape(name)}\s*:", c) for c in comments), (
                case.name,
                name,
            )


# --- shape of the two forms (points 7, 8, 9) -----------------------------------------------------


def _ports(text: str) -> str:
    match = re.search(r"\bmodule\s+sva_case\s*\((.*?)\)\s*;", text, re.DOTALL)
    assert match is not None
    return match.group(1)


def test_both_forms_declare_sva_case_with_a_clk_input(corpus: dict[str, str]) -> None:
    for case in EXPORTED:
        formal = _ports(corpus[_key(case, "formal.sv")])
        assert re.fullmatch(r"\s*input\s+(logic\s+|wire\s+)?clk\s*", formal), case.name
        if case.loop is not None:
            continue
        sim = _ports(corpus[_key(case, "sim.sv")])
        assert len(re.findall(r"\binput\b", sim)) == 1, case.name
        assert re.search(r"\binput\s+(logic\s+|wire\s+)?clk\b", sim), case.name
        assert re.search(r"\boutput\b[^,]*\bdone\b", sim), case.name


def test_property_text_and_directive_appear_verbatim(corpus: dict[str, str]) -> None:
    for case in EXPORTED:
        directive = f"{_wrapper(case)}{_directive(case)}"
        assert f"  {directive};\n" in corpus[_key(case, "formal.sv")], case.name
        if case.loop is None:  # the simulation form carries the action blocks on the same one
            assert directive in corpus[_key(case, "sim.sv")], case.name


def test_the_formal_form_is_the_driver_and_the_property_only(corpus: dict[str, str]) -> None:
    for case in EXPORTED:
        code = _code(corpus[_key(case, "formal.sv")])
        assert "$display" not in code, case.name
        assert len(re.findall(r"\b(?:assert|assume|cover)\s+property\b", code)) == 1, case.name
        others = {"assert", "assume", "cover"} - {case.kind}
        words = set(re.findall(r"\w+", " ".join([case.property, *case.widths])))
        for other in others:
            if other not in words:
                assert not re.search(rf"\b{other}\b", code), (case.name, other)
        forbidden = ["done", "tb"] + (["tick"] if case.loop is not None else ["phase"])
        for name in forbidden:
            if name not in words:
                assert not re.search(rf"\b{name}\b", code), (case.name, name)
        assert not re.search(r"\bmodule\s+tb\b", code), case.name
        assert len(re.findall(r"\bmodule\b", code)) == 1, case.name


def test_the_lasso_formal_form_drives_the_stimulus_by_phase(corpus: dict[str, str]) -> None:
    assert len(LASSOS) == 94 or len(LASSOS) == sum(1 for c in CASES if c.loop is not None)
    for case in LASSOS:
        lines = corpus[_key(case, "formal.sv")].split("\n")
        samples = len(case.samples)
        width = max(2, (samples + 2).bit_length())
        assert f"  logic [{width - 1}:0] phase = 0;" in lines, case.name
        assert (
            f"  always @(posedge clk) if (phase == {width}'d{samples - 1}) "
            f"phase <= {width}'d{case.loop}; else phase <= phase + 1'b1;"
        ) in lines, case.name
        code = _code("\n".join(lines))
        assert re.search(r"\(phase\s*<", code), case.name  # the row index follows phase
        words = set(re.findall(r"\w+", " ".join([case.property, *case.widths])))
        if "tick" not in words:
            assert "tick" not in code, case.name


def test_the_sim_form_reports_by_kind_and_has_a_test_bench(corpus: dict[str, str]) -> None:
    for case in SIM_CASES:
        text = corpus[_key(case, "sim.sv")]
        code = _code(text)
        cid = f"{_stem(case)}/{case.name}"
        if case.kind == "cover":
            assert f"SVA_COVER {cid} sample=" in text, case.name
            assert "SVA_FAIL" not in code and "SVA_PASS" not in code, case.name
        else:
            assert f"SVA_FAIL {cid} sample=" in text, case.name
            assert f"SVA_PASS {cid} sample=" in text, case.name
            assert "SVA_COVER" not in code, case.name
        assert "$display" in text
        assert "$sampled(tick)" in text
        assert re.search(r"\bmodule\s+tb\b", text), case.name
        assert re.search(r"\bdone\b", text), case.name
        assert re.search(r"\bmodule\s+sva_case\b", text)
        assert len(re.findall(r"\b(?:assert|assume|cover)\s+property\b", code)) == 1, case.name


def test_a_cover_prints_one_cover_line_per_match(corpus: dict[str, str]) -> None:
    for case in SIM_CASES:
        if case.kind != "cover":
            continue
        code = _code(corpus[_key(case, "sim.sv")])
        cid = f"{_stem(case)}/{case.name}"
        pattern = r"cover property \(@\(posedge clk\) .*\)\s*" + re.escape(
            f'$display("SVA_COVER {cid} sample=%0d", $sampled(tick));'
        )
        assert re.search(pattern, code), case.name
        assert len(re.findall(r"\$display", code)) == 1, case.name
        assert "else $display" not in code, case.name  # a cover has no failure action


def test_an_assume_or_initial_assertion_reports_like_an_assertion(corpus: dict[str, str]) -> None:
    for case in SIM_CASES:
        if case.kind == "cover":
            continue
        code = _code(corpus[_key(case, "sim.sv")])
        cid = f"{_stem(case)}/{case.name}"
        assert f'$display("SVA_PASS {cid} sample=%0d", $sampled(tick))' in code, case.name
        assert f'else $display("SVA_FAIL {cid} sample=%0d", $sampled(tick))' in code, case.name


def test_the_initial_wrapper_is_written_exactly_for_the_initial_procedure(
    corpus: dict[str, str],
) -> None:
    initial = [c for c in CASES if c.procedure == "initial"]
    assert initial and {c.kind for c in initial} == {"assert", "assume", "cover"}
    for case in CASES:
        for form in _forms_of(case):
            code = _code(corpus[_key(case, form)])
            assert (f"initial {case.kind[:0]}p:" in code) is (case.procedure == "initial"), (
                case.name,
                form,
            )


def test_every_example_is_self_contained(corpus: dict[str, str]) -> None:
    for key, text in corpus.items():
        if key.endswith(".sv"):
            assert "`include" not in text, key
            assert not re.search(r"\bimport\s+\w+::", text), key


# --- elaboration under Slang (point 21) ---


def _spread(items: list[TraceCase], count: int) -> list[TraceCase]:
    step = max(1, len(items) // count)
    return items[::step][:count]


def _elaboration_sample() -> list[TraceCase]:
    """A spread of about 25 bounded cases, plus one of each kind the contract names: every
    category, every kind, a lasso, an initial, and the scope and width variants."""
    picks = [
        next(c for c in BOUNDED if c.first_failure_only),
        next(c for c in BOUNDED if c.attempt_scope),
        next(c for c in BOUNDED if c.disable is not None),
        next(c for c in BOUNDED if max(c.widths.values(), default=1) > 1),
        next(c for c in CASES if c.procedure == "initial" and c.kind == "assert"),
        next(c for c in CASES if c.procedure == "initial" and c.kind == "cover"),
        next(c for c in CASES if c.procedure == "initial" and c.kind == "assume"),
        next(c for c in CASES if c.kind == "cover" and c.loop is None and c.procedure is None),
        next(c for c in CASES if c.kind == "assume" and c.loop is None and c.procedure is None),
        next(c for c in CASES if c.loop is not None and c.kind == "assert"),
        next(c for c in CASES if c.loop is not None and c.kind == "assume"),
        next(c for c in CASES if c.loop is not None and c.kind == "cover"),
        next(c for c in CASES if c.loop is not None and c.disable is not None),
    ]
    sample = _spread(BOUNDED, 21)
    return sample + [c for c in picks if c not in sample]


def _elaborate(path: Path, top: str) -> None:
    compile_design([path], SlangOptions(top=(top,)))


def _write(root: Path, key: str, text: str) -> Path:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("case", _elaboration_sample(), ids=lambda c: f"{_stem(c)}-{c.name}")
def test_a_sample_of_exported_files_elaborates_under_slang(
    case: TraceCase, corpus: dict[str, str], tmp_path: Path
) -> None:
    _elaborate(
        _write(tmp_path, _key(case, "formal.sv"), corpus[_key(case, "formal.sv")]), "sva_case"
    )
    if case.loop is None:
        _elaborate(_write(tmp_path, _key(case, "sim.sv"), corpus[_key(case, "sim.sv")]), "tb")
    else:
        assert _key(case, "sim.sv") not in corpus


def test_the_sample_covers_every_category_kind_a_lasso_and_an_initial() -> None:
    sample = _elaboration_sample()
    assert {_category(c) for c in sample} == {"bounded", "cover", "assume", "initial", "live"}
    assert {c.kind for c in sample} == {"assert", "assume", "cover"}
    assert any(c.loop is not None for c in sample)
    assert {c.kind for c in sample if c.procedure == "initial"} == {"assert", "assume", "cover"}
    assert any(c.loop is None for c in sample)


def test_the_sample_covers_wide_signals_disable_and_every_scope() -> None:
    sample = _elaboration_sample()
    assert any(max(c.widths.values(), default=1) > 1 for c in sample)
    assert any(c.disable is not None for c in sample)
    assert any(c.first_failure_only for c in sample)
    assert any(c.attempt_scope for c in sample)


@pytest.mark.extended
def test_every_exported_file_elaborates_under_slang(corpus: dict[str, str], tmp_path: Path) -> None:
    failures: list[str] = []
    for case in EXPORTED:
        for extension, top in (("formal.sv", "sva_case"), ("sim.sv", "tb")):
            if extension not in _forms_of(case):
                continue
            key = _key(case, extension)
            try:
                _elaborate(_write(tmp_path, key, corpus[key]), top)
            except SlangError as error:
                failures.append(f"{key}: {error.report[:200]}")
    assert not failures, failures[:3]


def test_a_malformed_property_is_rejected_by_the_control(
    corpus: dict[str, str], tmp_path: Path
) -> None:
    case = EXPORTED[0]
    key = _key(case, "formal.sv")
    text = corpus[key]
    assert case.property in text
    broken = text.replace(case.property, f"{case.property} |-> |-> ((", 1)
    with pytest.raises(SlangError):
        compile_texts([(key, broken)], SlangOptions(top=("sva_case",)))
    compile_texts([(key, text)], SlangOptions(top=("sva_case",)))  # the unbroken text is fine


# --- round trip through sva2yosys (point 22) ---


def _round_trip_sample() -> list[TraceCase]:
    """About 20 supported cases, spread over scope, wide signals and sampled values."""
    sampled = re.compile(r"\$(rose|fell|stable|changed|past|sampled)\b")
    # disable iff is out: sva2yosys rejects it on signals driven from the corpus counter
    # (internal-signal disable iff is deferred), though other tools accept the cases
    usable = [c for c in BOUNDED if not c.oracle_only and c.disable is None]
    groups = [
        [c for c in usable if c.first_failure_only],
        [c for c in usable if c.attempt_scope],
        [c for c in usable if max(c.widths.values(), default=1) > 1],
        [c for c in usable if sampled.search(c.property)],
        [c for c in usable if not (c.first_failure_only or c.attempt_scope)],
    ]
    chosen: list[TraceCase] = []
    for group in groups:
        # half with failures, half without, so both verdicts are exercised
        failing = [c for c in group if c.fails and c not in chosen]
        clean = [c for c in group if not c.fails and c not in chosen]
        chosen.extend(_spread(failing, 3) + _spread(clean, 1))
    return chosen[:20]


ROUND_TRIP = _round_trip_sample()
_SBY = """[tasks]
bmc

[options]
mode bmc
depth {depth}

[engines]
smtbmc yices

[script]
script sva2yosys_read.ys
prep -top sva_case

[files]
sva2yosys_read.ys
"""


def _usable(category: str) -> list[TraceCase]:
    """Formal files sva2yosys is expected to take: not oracle-only, no disable iff."""
    return [
        c for c in CASES if _category(c) == category and not c.oracle_only and c.disable is None
    ]


def _acceptance_sample() -> list[TraceCase]:
    """A default-tier sample of the formal files of every category (point 34)."""
    chosen: list[TraceCase] = []
    for category, count in (("bounded", 4), ("cover", 4), ("assume", 3), ("initial", 4)):
        chosen.extend(_spread(_usable(category), count))
    lasso = _usable("live")
    chosen.extend(_spread(lasso, 4))
    chosen.extend(c for c in _usable("cover") + _usable("assume") if c.loop is not None)
    return [c for k, c in enumerate(chosen) if all(c is not d for d in chosen[:k])]


ACCEPTANCE = _acceptance_sample()


def test_the_acceptance_sample_covers_every_category_and_kind() -> None:
    assert {_category(c) for c in ACCEPTANCE} == {"bounded", "cover", "assume", "initial", "live"}
    assert {c.kind for c in ACCEPTANCE if c.procedure == "initial"} >= {"assert", "cover"}
    assert any(c.loop is not None for c in ACCEPTANCE)
    assert all(not c.oracle_only and c.disable is None for c in ACCEPTANCE)


@pytest.mark.parametrize("case", ACCEPTANCE, ids=lambda c: f"{_category(c)}-{_stem(c)}-{c.name}")
def test_a_sample_of_formal_files_of_every_category_is_accepted_by_sva2yosys(
    case: TraceCase,
    corpus: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write(tmp_path, "case.sv", corpus[_key(case, "formal.sv")])
    assert main([str(path), "--top", "sva_case"]) == 0
    output = capsys.readouterr().out
    assert "rejected" not in output.replace("0 rejected", ""), output


def test_the_round_trip_sample_is_spread() -> None:
    assert 15 <= len(ROUND_TRIP) <= 20
    assert len({c.first_failure_only for c in ROUND_TRIP}) == 2
    assert any(c.attempt_scope for c in ROUND_TRIP)
    assert any(max(c.widths.values(), default=1) > 1 for c in ROUND_TRIP)
    assert any(c.fails for c in ROUND_TRIP) and any(not c.fails for c in ROUND_TRIP)


@pytest.mark.parametrize("case", ROUND_TRIP, ids=lambda c: f"{_stem(c)}-{c.name}")
def test_the_exported_formal_file_is_accepted_by_sva2yosys(
    case: TraceCase,
    corpus: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write(tmp_path, "case.sv", corpus[_key(case, "formal.sv")])
    assert main([str(path), "--top", "sva_case"]) == 0
    output = capsys.readouterr().out
    assert "rejected" not in output.replace("0 rejected", ""), output


@pytest.mark.parametrize("case", ROUND_TRIP, ids=lambda c: f"{_stem(c)}-{c.name}")
def test_the_lowered_formal_file_fails_first_where_the_manifest_says(
    case: TraceCase,
    corpus: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sby_tool: Tool,
) -> None:
    """Needs SBY (``--external-tools=sby``): no tool-free simulator runs the lowered monitors.

    BMC to depth N (steps 0 .. N-1) fails first at the earliest failure sample of the manifest,
    and passes when the table expects no failure.
    """
    path = _write(tmp_path, "case.sv", corpus[_key(case, "formal.sv")])
    build = tmp_path / "build"
    assert main([str(path), "--top", "sva_case", "-o", str(build)]) == 0
    capsys.readouterr()
    sby = build / "case.sby"
    sby.write_text(_SBY.format(depth=len(case.samples)), encoding="utf-8")
    result = run_sby(sby_tool, sby, "bmc", tmp_path / "work")
    if not case.fails:
        assert result.status is SbyStatus.PASS, result.log[-2000:]
        return
    assert result.status is SbyStatus.FAIL, result.log[-2000:]
    steps = [hit.step or 0 for hit in result.failed_assertions]
    assert min(steps) == min(case.fails), result.log[-2000:]


@pytest.mark.extended
@pytest.mark.parametrize("case", ROUND_TRIP, ids=lambda c: f"{_stem(c)}-{c.name}")
def test_z3_gives_the_same_verdict_on_the_round_trip_sample(
    case: TraceCase,
    corpus: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sby_tool: Tool,
) -> None:
    """39."""
    path = _write(tmp_path, "case.sv", corpus[_key(case, "formal.sv")])
    build = tmp_path / "build"
    assert main([str(path), "--top", "sva_case", "-o", str(build)]) == 0
    capsys.readouterr()
    sby = build / "case.sby"
    sby.write_text(
        _SBY.format(depth=len(case.samples)).replace("smtbmc yices", "smtbmc z3"), encoding="utf-8"
    )
    result = run_sby(sby_tool, sby, "bmc", tmp_path / "work")
    if not case.fails:
        assert result.status is SbyStatus.PASS, result.log[-2000:]
        return
    assert result.status is SbyStatus.FAIL, result.log[-2000:]
    steps = [hit.step or 0 for hit in result.failed_assertions]
    assert min(steps) == min(case.fails), result.log[-2000:]


_SBY_COVER = """[tasks]
cover

[options]
mode cover
depth {depth}

[engines]
smtbmc yices

[script]
script sva2yosys_read.ys
prep -top sva_case

[files]
sva2yosys_read.ys
"""


def _finite_covers() -> list[TraceCase]:
    """Finite (no loop) covers sva2yosys is expected to take, half reachable, half not."""
    usable = [c for c in _usable("cover") if c.loop is None]
    reachable = [c for c in usable if c.passes]
    unreachable = [c for c in usable if not c.passes]
    return _spread(reachable, 4) + _spread(unreachable, 3)


COVER_ROUND_TRIP = _finite_covers()


def test_the_cover_round_trip_sample_has_both_verdicts() -> None:
    assert any(c.passes for c in COVER_ROUND_TRIP)
    assert any(not c.passes for c in COVER_ROUND_TRIP)
    assert all(c.kind == "cover" and c.loop is None for c in COVER_ROUND_TRIP)


@pytest.mark.parametrize("case", COVER_ROUND_TRIP, ids=lambda c: f"{_stem(c)}-{c.name}")
def test_the_lowered_cover_is_reached_exactly_when_the_manifest_has_successes(
    case: TraceCase,
    corpus: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sby_tool: Tool,
) -> None:
    """Needs SBY (``--external-tools=sby``). SBY's cover mode over the written samples reaches
    the cover statement iff the table's ``pass`` row (the manifest ``successes``) is not empty
    (point 27: a cover has no failure; a cover is reachable exactly at its successes)."""
    path = _write(tmp_path, "case.sv", corpus[_key(case, "formal.sv")])
    build = tmp_path / "build"
    assert main([str(path), "--top", "sva_case", "-o", str(build)]) == 0
    capsys.readouterr()
    sby = build / "case.sby"
    sby.write_text(_SBY_COVER.format(depth=len(case.samples)), encoding="utf-8")
    result = run_sby(sby_tool, sby, "cover", tmp_path / "work")
    if case.passes:
        assert result.status is SbyStatus.PASS, result.log[-2000:]
        assert result.reached_covers and not result.unreached_covers, result.log[-2000:]
    else:
        assert result.status is SbyStatus.FAIL, result.log[-2000:]
        assert not result.reached_covers, result.log[-2000:]


# --- the on-demand ternary stimulus (point 25) ---


@pytest.fixture(scope="module")
def ternary() -> dict[str, str]:
    return build_corpus(CASES, traces_digest(), Stimulus.TERNARY)


@pytest.mark.parametrize("case", _elaboration_sample(), ids=lambda c: f"{_stem(c)}-{c.name}")
def test_a_sample_of_ternary_files_elaborates_under_slang(
    case: TraceCase, ternary: dict[str, str], tmp_path: Path
) -> None:
    formal = _key(case, "formal.sv")
    _elaborate(_write(tmp_path, formal, ternary[formal]), "sva_case")
    if case.loop is None:
        sim = _key(case, "sim.sv")
        _elaborate(_write(tmp_path, sim, ternary[sim]), "tb")


def test_the_ternary_files_state_the_same_directive_and_rows_as_the_string_files(
    corpus: dict[str, str], ternary: dict[str, str]
) -> None:
    for case in EXPORTED:
        for form in _forms_of(case):
            key = _key(case, form)
            if len(case.samples) >= TERNARY_FROM:
                assert ternary[key] == corpus[key], key  # already ternary (point 25)
                continue
            assert ternary[key] != corpus[key], key
            directive = f"{case.kind} property"
            assert _code(ternary[key]).count(directive) == _code(corpus[key]).count(directive), key
            comments = [x for x in ternary[key].split("\n") if x.startswith("//")]
            assert comments == [x for x in corpus[key].split("\n") if x.startswith("//")], key


# --- the timing documentation (point 36) ---


def _readme_timing_section() -> str:
    text = (CORPUS_DIRECTORY / "README.md").read_text(encoding="utf-8")
    assert "## Timing conventions" in text
    start = text.index("## Timing conventions")
    end = text.find("\n## ", start + 1)
    return text[start:] if end < 0 else text[start:end]


def test_the_readme_documents_the_sampling_rule_and_its_clauses() -> None:
    section = _readme_timing_section()
    for needle in ("Preponed", "16.5.1", "4.4.2.1", "just before"):
        assert needle in section, needle


def test_the_readme_documents_the_testbench_timeline_csv_and_wavedrom() -> None:
    section = _readme_timing_section()
    for needle in (
        "times 5, 15",
        "tick_prev",
        "$sampled(tick)",
        "CSV",
        "WaveDrom",
        "rising edge `k`",
    ):
        assert needle in section, needle
    assert "grid lines" in section
    assert "sample,a,b,fail,pass" in section  # the example in the three views


def test_the_documented_timeline_matches_the_generated_testbench(corpus: dict[str, str]) -> None:
    text = corpus[_key(EXPORTED[0], "sim.sv")]
    assert "always #5 clk = ~clk;" in text  # first rising edge at time 5
    assert "logic clk = 0;" in text
    assert "always @(posedge clk)" in text and "tick <= tick + 1'b1" in text
    assert "$sampled(tick)" in text


def test_the_readme_quotes_no_text_of_the_standard() -> None:
    section = _readme_timing_section()
    # the standard's sentence is paraphrased, not copied
    assert "is the value of this variable in the Preponed region" not in section


# --- the disable iff signals (point 37) ---


def _decoded_with(text: str, name: str) -> str:
    match = re.search(rf"wire (?:\[\d+:0\] )?{name} = .*?(row_idx_prev|row_idx)\b", text)
    assert match is not None, name
    return match.group(1)


def test_every_disable_case_decodes_exactly_its_condition_signals_with_the_saved_counter(
    corpus: dict[str, str],
) -> None:
    from support.trace_driver import disable_only_signals

    disabled = [c for c in CASES if c.disable is not None]
    assert len(disabled) == 68
    for case in disabled:
        only = set(disable_only_signals(case))
        if not only:  # a constant condition such as disable iff (0): no signal, no saved counter
            assert "tick_prev" not in corpus[_key(case, "formal.sv")], case.name
            continue
        forms = ["formal.sv"] + ([] if case.loop is not None else ["sim.sv"])
        saved = "phase_prev" if case.loop is not None else "tick_prev"
        for form in forms:
            text = corpus[_key(case, form)]
            assert f"always @(posedge clk) {saved} <= " in text, (case.name, form)
            for name in case.widths:
                expected = "row_idx_prev" if name in only else "row_idx"
                assert _decoded_with(text, name) == expected, (case.name, form, name)


def test_a_case_without_disable_has_no_saved_counter(corpus: dict[str, str]) -> None:
    for case in CASES:
        if case.disable is None:
            text = corpus[_key(case, "formal.sv")]
            assert (
                "tick_prev" not in text and "phase_prev" not in text and "row_idx_prev" not in text
            )


# --- the whole corpus through sva2yosys (point 38) ---


def _taken_by_sva2yosys(case: TraceCase) -> bool:
    """Point 38: the cases the tool takes: not `support: oracle`, and no `disable iff` that
    reads a signal (the constant `disable iff (1'b0)` is taken)."""
    constant = case.disable is None or re.fullmatch(r"1'b[01]", case.disable) is not None
    return not case.oracle_only and constant


@pytest.mark.extended
def test_every_exported_formal_file_is_taken_or_cleanly_rejected(
    corpus: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """38: no crash; accepted exactly when `_taken_by_sva2yosys`."""
    problems: list[str] = []
    for number, case in enumerate(CASES):
        path = _write(tmp_path, f"case{number}.sv", corpus[_key(case, "formal.sv")])
        label = f"{_stem(case)}/{case.name}"
        try:
            status = main([str(path), "--top", "sva_case", "-o", str(tmp_path / f"b{number}")])
        except Exception as error:  # a crash is what this test exists to find
            problems.append(f"{label}: {type(error).__name__}: {error}")
            continue
        capsys.readouterr()
        if (status == 0) != _taken_by_sva2yosys(case):
            problems.append(
                f"{label}: exit status {status}, taken expected {_taken_by_sva2yosys(case)}"
            )
    assert not problems, "\n".join(problems)


def _check_with_sby(
    job: tuple[int, TraceCase, str], root: Path, tool: Tool
) -> tuple[str, str | None]:
    """The verdict problem of one taken case under SBY, or None; its label."""
    number, case, text = job
    label = f"{_stem(case)}/{case.name}"
    path = _write(root, f"case{number}.sv", text)
    build = root / f"b{number}"
    if main([str(path), "--top", "sva_case", "-o", str(build)]) != 0:
        return label, "not accepted"
    mode = "cover" if case.kind == "cover" else "bmc"
    template = _SBY_COVER if mode == "cover" else _SBY
    sby = build / "case.sby"
    sby.write_text(template.format(depth=len(case.samples)), encoding="utf-8")
    result = run_sby(tool, sby, mode, root / f"w{number}")
    if mode == "cover":
        if (result.status is SbyStatus.PASS) != bool(case.passes):
            return label, f"cover {result.status}, table successes {sorted(case.passes)}"
        return label, None
    steps = [hit.step or 0 for hit in result.failed_assertions]
    if not case.fails:
        return label, None if result.status is SbyStatus.PASS else f"{result.status}, expected PASS"
    if result.status is not SbyStatus.FAIL or min(steps, default=-1) != min(case.fails):
        return (
            label,
            f"{result.status} first failure {min(steps, default=None)}, table {min(case.fails)}",
        )
    return label, None


@pytest.mark.slow
def test_every_taken_formal_file_has_the_verdict_of_the_table_under_sby(
    corpus: dict[str, str], tmp_path: Path, sby_tool: Tool
) -> None:
    """38: needs SBY (``--external-tools=sby``); four SBY runs at a time."""
    from concurrent.futures import ThreadPoolExecutor

    jobs = [
        (number, case, corpus[_key(case, "formal.sv")])
        for number, case in enumerate(CASES)
        if _taken_by_sva2yosys(case) and case.kind != "assume"
    ]
    assert len(jobs) > 400
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda job: _check_with_sby(job, tmp_path, sby_tool), jobs))
    problems = [f"{label}: {problem}" for label, problem in results if problem is not None]
    assert not problems, "\n".join(problems)
