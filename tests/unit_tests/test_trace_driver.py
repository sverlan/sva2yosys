"""Unit contract of support.trace_driver (corpus contract points 12, 13, 14).

Expectations are derived by hand from the trace text below, never from the module under test.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from support.ebmc_differential import load_all_cases
from support.trace_driver import (
    counter_lines,
    counter_width,
    decode_row_string,
    directive_line,
    disable_only_signals,
    phase_lines,
    previous_lines,
    row_string,
    stimulus_lines,
    string_stimulus_lines,
)
from support.tracefile import TraceCase, load_cases

WIDE = """\
== wide
property: a |-> v == 4'd1
disable: !rst
signals: a rst v:8 n:4 m:5 k:9
a   : 1 . 1 . 1
rst : . . 1 . .
v   : 0x00 0x10 0xAB 0x0F 0xFF
n   : 0 15 10 1 9
m   : 0 31 16 1 0
k   : 0 256 511 1 0
clause: 16.9.1
class: normative

== looped
property: a
scope: attempt
loop: 1
a : 1 . 1
clause: 16.9.1
class: normative
"""


@pytest.fixture
def wide(tmp_path: Path) -> TraceCase:
    path = tmp_path / "wide.trace"
    path.write_text(WIDE, encoding="utf-8")
    return load_cases(path)[0]


@pytest.fixture
def looped(tmp_path: Path) -> TraceCase:
    path = tmp_path / "wide.trace"
    path.write_text(WIDE, encoding="utf-8")
    return load_cases(path)[1]


# --- row_string ------------------------------------------------------------------------------


def test_one_bit_rows_use_dot_for_zero_and_one_for_one(wide: TraceCase) -> None:
    assert row_string(wide, "a") == "1 . 1 . 1"
    assert row_string(wide, "rst") == ". . 1 . ."


def test_wide_rows_write_hex_digits_with_zero_as_0_and_never_a_dot(wide: TraceCase) -> None:
    assert row_string(wide, "v") == "00 10 AB 0F FF"  # width 8: 2 digits; 0x00 -> 00, 0x10 -> 10
    assert row_string(wide, "n") == "0 F A 1 9"  # width 4: 1 digit, uppercase
    assert row_string(wide, "m") == "00 1F 10 01 00"  # width 5: 2 digits
    assert row_string(wide, "k") == "000 100 1FF 001 000"  # width 9: 3 digits


@pytest.mark.parametrize("name", ["v", "n", "m", "k"])
def test_a_wide_row_never_contains_a_dot(wide: TraceCase, name: str) -> None:
    assert "." not in row_string(wide, name)


@pytest.mark.parametrize("name", ["a", "rst", "v", "n", "m", "k"])
def test_row_string_has_single_space_separators(wide: TraceCase, name: str) -> None:
    text = row_string(wide, name)
    assert "  " not in text
    assert text == text.strip()
    assert len(text.split(" ")) == len(wide.samples)


@pytest.mark.parametrize(("name", "digits"), [("v", 2), ("n", 1), ("m", 2), ("k", 3)])
def test_every_field_has_ceil_width_over_four_digits(
    wide: TraceCase, name: str, digits: int
) -> None:
    assert {len(field) for field in row_string(wide, name).split(" ")} == {digits}
    assert all(re.fullmatch(r"[0-9A-F]+", f) for f in row_string(wide, name).split(" "))


# --- decode_row_string -----------------------------------------------------------------------


def test_decode_reads_the_canonical_forms() -> None:
    assert decode_row_string("1 . 1 . 1", 1) == (1, 0, 1, 0, 1)
    assert decode_row_string("00 10 AB 0F FF", 8) == (0, 16, 171, 15, 255)
    assert decode_row_string("0 F A", 4) == (0, 15, 10)
    assert decode_row_string("00 1F 10", 5) == (0, 31, 16)


def test_decode_accepts_a_dot_as_a_zero_digit() -> None:
    assert decode_row_string(".. 1. .1", 8) == (0, 16, 1)
    assert decode_row_string(". . 1", 4) == (0, 0, 1)


def test_decode_of_a_single_sample() -> None:
    assert decode_row_string("A", 4) == (10,)
    assert decode_row_string("1", 1) == (1,)


@pytest.mark.parametrize("name", ["a", "rst", "v", "n", "m", "k"])
def test_decode_inverts_row_string_on_the_hand_written_case(wide: TraceCase, name: str) -> None:
    expected = tuple(sample[name] for sample in wide.samples)
    assert decode_row_string(row_string(wide, name), wide.widths[name]) == expected


def _all_signals() -> list[tuple[TraceCase, str]]:
    return [(case, name) for case in load_all_cases() for name in case.widths]


def test_round_trip_over_every_signal_of_every_trace_case() -> None:
    pairs = _all_signals()
    assert len(pairs) > 1000  # the tables are big: this is not a vacuous loop
    wrong: list[str] = []
    for case, name in pairs:
        text = row_string(case, name)
        expected = tuple(sample[name] for sample in case.samples)
        if decode_row_string(text, case.widths[name]) != expected:
            wrong.append(f"{case.file}:{case.name}:{name}")
    assert not wrong, wrong[:5]


def test_every_row_of_every_trace_case_is_canonical() -> None:
    wrong: list[str] = []
    for case, name in _all_signals():
        width = case.widths[name]
        fields = row_string(case, name).split(" ")
        if len(fields) != len(case.samples):
            wrong.append(f"{case.name}:{name} count")
        elif width == 1:
            if not all(f in (".", "1") for f in fields):
                wrong.append(f"{case.name}:{name} one-bit")
        elif not all(re.fullmatch(f"[0-9A-F]{{{(width + 3) // 4}}}", f) for f in fields):
            wrong.append(f"{case.name}:{name} wide")
    assert not wrong, wrong[:5]


# --- string_stimulus_lines -------------------------------------------------------------------


def test_string_stimulus_holds_every_row_string_and_selects_by_the_selector(
    wide: TraceCase,
) -> None:
    text = "\n".join(string_stimulus_lines(wide, "tick"))
    for name in wide.widths:
        assert f'"{row_string(wide, name)}"' in text
        assert re.search(rf"\bwire\b[^;]*\b{name}\b", text), name
    assert "tick" in text
    assert "$readmem" not in text
    assert not re.search(r"\bstring\b", text)  # plain bit selects, no string type


def test_string_stimulus_declares_each_wire_with_its_width(wide: TraceCase) -> None:
    text = "\n".join(string_stimulus_lines(wide, "tick"))
    for name, width in wide.widths.items():
        pattern = rf"\bwire\s+(?:logic\s+)?(?:\[{width - 1}:0\]\s*)?{name}\b"
        assert re.search(pattern, text), name


def test_string_stimulus_is_deterministic_and_selector_independent_in_shape(
    wide: TraceCase,
) -> None:
    first = string_stimulus_lines(wide, "tick")
    assert first == string_stimulus_lines(wide, "tick")
    other = string_stimulus_lines(wide, "phase")
    assert "phase" in "\n".join(other)
    assert "tick" not in "\n".join(other)


def test_string_stimulus_mentions_the_sample_count_for_the_hold_last_rule(
    wide: TraceCase,
) -> None:
    # the behaviour (last value held past the end) is checked by the round trip of the corpus
    # tests; here only that the written sample count is known to the decoder.
    text = "\n".join(string_stimulus_lines(wide, "tick"))
    assert re.search(r"\b5\b|'d5\b|\b4\b|'d4\b", text)


# --- counter, phase, directive ---------------------------------------------------------------


def test_counter_width_rule() -> None:
    assert counter_width(0) == 2
    assert counter_width(1) == 2
    assert counter_width(2) == 3  # (2 + 2).bit_length() == 3
    assert counter_width(5) == 3
    assert counter_width(6) == 4
    assert counter_width(14) == 5


def test_counter_lines_for_five_samples(wide: TraceCase) -> None:
    assert counter_lines(wide) == (
        "  logic [2:0] tick = 0;",
        "  always @(posedge clk) if (tick != 3'd5) tick <= tick + 1'b1;",
    )


def test_counter_starts_at_zero_and_saturates_at_the_sample_count(wide: TraceCase) -> None:
    declaration, update = counter_lines(wide)
    assert declaration.endswith("tick = 0;")
    assert "posedge clk" in update
    assert "tick != 3'd5" in update


def test_phase_lines_jump_to_the_loop_sample(looped: TraceCase) -> None:
    assert phase_lines(looped) == (
        "  logic [2:0] phase = 0;",
        "  always @(posedge clk) if (phase == 3'd2) phase <= 3'd1; else phase <= phase + 1'b1;",
    )


def test_phase_needs_a_lasso(wide: TraceCase) -> None:
    with pytest.raises(AssertionError):
        phase_lines(wide)


def test_directive_line_with_disable(wide: TraceCase) -> None:
    assert directive_line(wide) == (
        "p: assert property (@(posedge clk) disable iff (!rst) a |-> v == 4'd1);"
    )


def test_directive_line_without_disable_flag_leaves_the_disable_out(wide: TraceCase) -> None:
    assert directive_line(wide, include_disable=False) == (
        "p: assert property (@(posedge clk) a |-> v == 4'd1);"
    )


def test_directive_line_without_a_disable_is_the_same_either_way(looped: TraceCase) -> None:
    expected = "p: assert property (@(posedge clk) a);"
    assert directive_line(looped) == expected
    assert directive_line(looped, include_disable=False) == expected


def test_directive_line_label_and_kind(wide: TraceCase) -> None:
    cover = replace(wide, kind="cover", disable=None)
    assert directive_line(cover, "q") == "q: cover property (@(posedge clk) a |-> v == 4'd1);"


def _joined(case: TraceCase) -> str:
    return "\n".join(string_stimulus_lines(case, "tick"))


def test_the_digit_decoder_is_declared_only_when_a_wide_row_needs_it(
    wide: TraceCase, looped: TraceCase
) -> None:
    assert "hexd" in _joined(wide)
    one_bit = _joined(looped)
    assert "hexd" not in one_bit
    assert "function" not in one_bit


def test_every_generated_name_is_used_in_the_stimulus_lines(
    wide: TraceCase, looped: TraceCase
) -> None:
    for case in (wide, looped):
        text = _joined(case)
        assert len(re.findall(r"\brow_idx\b", text)) > 1  # declared and used
        for name in case.widths:
            assert len(re.findall(rf"\bROW_{name}\b", text)) > 1, name
        if "hexd" in text:
            assert len(re.findall(r"\bhexd\b", text)) > 3  # declared, assigned, used


def test_characters_are_compared_as_one_character_literals(wide: TraceCase) -> None:
    text = _joined(wide)
    for literal in ('"A"', '"0"', '"9"'):
        assert literal in text
    assert "8'h" not in text  # no character codes


def test_a_one_bit_row_is_compared_with_the_character_one(looped: TraceCase) -> None:
    assert '== "1"' in _joined(looped)
    assert "8'h" not in _joined(looped)


def test_directive_line_wraps_in_initial_only_on_request_for_a_procedure_case(
    wide: TraceCase,
) -> None:
    plain = "p: assert property (@(posedge clk) disable iff (!rst) a |-> v == 4'd1);"
    initial = replace(wide, procedure="initial")
    assert directive_line(initial) == plain  # the EBMC design never wraps
    assert directive_line(initial, wrap_initial=True) == f"initial {plain}"
    assert directive_line(wide, wrap_initial=True) == plain  # no procedure: nothing to wrap
    cover = replace(initial, kind="cover", disable=None)
    assert directive_line(cover, "q", wrap_initial=True) == (
        "initial q: cover property (@(posedge clk) a |-> v == 4'd1);"
    )
    assume = replace(initial, kind="assume", disable=None)
    assert directive_line(assume, wrap_initial=True).startswith("initial p: assume property (")


# --- the saved counter for signals read only by a disable iff condition ------------------------


def test_disable_only_signals_are_the_condition_signals_the_property_does_not_read(
    wide: TraceCase, looped: TraceCase
) -> None:
    assert disable_only_signals(wide) == ("rst",)  # property a |-> v == 4'd1, disable !rst
    assert disable_only_signals(looped) == ()  # no disable iff


def test_a_signal_read_by_both_the_condition_and_the_property_is_refused(
    wide: TraceCase,
) -> None:
    both = replace(wide, disable="!a")
    with pytest.raises(ValueError, match="read by both"):
        disable_only_signals(both)


def test_system_functions_and_keywords_in_the_condition_are_not_signals(
    wide: TraceCase,
) -> None:
    case = replace(wide, disable="$isunknown(m) || rst")  # m is a signal; $isunknown is not
    assert disable_only_signals(case) == ("rst", "m")
    assert disable_only_signals(replace(wide, disable="rst iff 1")) == ("rst",)


def test_the_saved_counter_register_holds_the_counter_before_the_last_edge(
    wide: TraceCase,
) -> None:
    lines = previous_lines(wide, "tick")
    assert "  logic [2:0] tick_prev = 0;" in lines
    assert "  always @(posedge clk) tick_prev <= tick;" in lines


def test_the_disable_only_signal_is_decoded_with_the_saved_counter(wide: TraceCase) -> None:
    text = _joined_delayed(wide)
    assert "wire [31:0] row_idx_prev = (tick_prev <" in text
    assert re.search(r"wire rst = \(ROW_rst\[8\*\(\d+-2\*row_idx_prev\) \+: 8\] == \"1\"\);", text)
    assert re.search(r"wire a = \(ROW_a\[8\*\(\d+-2\*row_idx\) \+: 8\] == \"1\"\);", text)
    assert "row_idx_prev" not in _joined(replace(wide, disable=None))


def _joined_delayed(case: TraceCase) -> str:
    return "\n".join(string_stimulus_lines(case, "tick", "tick_prev"))


def test_the_conditional_chain_uses_the_saved_counter_for_the_disable_only_signal(
    wide: TraceCase,
) -> None:
    lines = stimulus_lines(wide, "tick", "tick_prev")
    rst = next(line for line in lines if line.startswith("  wire [0:0] rst"))
    a = next(line for line in lines if line.startswith("  wire [0:0] a "))
    assert "tick_prev ==" in rst and "tick ==" not in rst.replace("tick_prev ==", "")
    assert "tick_prev" not in a
    assert stimulus_lines(wide, "tick") == stimulus_lines(wide, "tick", None)
