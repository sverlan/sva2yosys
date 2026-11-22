"""Test support: the tool-neutral driver that replays a trace case (interface only).

Shared by the EBMC differential design (``ebmc_differential.design_source``) and the corpus
generator (``trace_suite``), so that the way a trace becomes stimulus is implemented once.

Contract
--------
* Sample ``k`` of a case is the value seen by the property's sampling at the ``k``-th
  ``posedge clk`` (counting from 0). A counter starts at 0 and advances on ``posedge clk``;
  the stimulus of each signal is selected by the counter (or by the lasso ``phase``), so the
  first ``posedge clk`` samples the values of sample 0.
* After the last written sample the last values are held; no expectation is defined there,
  and a consumer must bound its check to the written samples.
* ``counter_width`` is ``max(2, (samples + 2).bit_length())``.
* With a lasso, ``phase`` runs 0 .. samples-1 and then jumps to ``case.loop``.
* The functions are pure and deterministic. ``counter_lines``, ``phase_lines``,
  ``stimulus_lines`` and ``directive_line`` emit the exact text that
  ``ebmc_differential.design_source`` emitted before the extraction (byte for byte);
  ``row_string``, ``decode_row_string`` and ``string_stimulus_lines`` serve the corpus.
"""

from __future__ import annotations

import re

from support.tracefile import TraceCase


def counter_width(samples: int) -> int:
    """Bit width of the sample counter for ``samples`` written samples."""
    return max(2, (samples + 2).bit_length())


def counter_lines(case: TraceCase) -> tuple[str, ...]:
    """Declaration and update of ``tick``: saturates at the sample count."""
    samples = len(case.samples)
    width = counter_width(samples)
    return (
        f"  logic [{width - 1}:0] tick = 0;",
        f"  always @(posedge clk) if (tick != {width}'d{samples}) tick <= tick + 1'b1;",
    )


def phase_lines(case: TraceCase) -> tuple[str, ...]:
    """Declaration and update of ``phase`` for a lasso case (``case.loop`` is not None)."""
    assert case.loop is not None, "a phase needs a lasso case"
    samples = len(case.samples)
    width = counter_width(samples)
    return (
        f"  logic [{width - 1}:0] phase = 0;",
        f"  always @(posedge clk) if (phase == {width}'d{samples - 1}) "
        f"phase <= {width}'d{case.loop}; else phase <= phase + 1'b1;",
    )


_KEYWORDS = frozenset(
    [
        "disable",
        "iff",
        "posedge",
        "negedge",
        "and",
        "or",
        "not",
        "if",
        "else",
        "throughout",
        "within",
        "intersect",
        "first_match",
        "strong",
        "weak",
        "until",
        "until_with",
        "s_until",
        "s_until_with",
        "always",
        "s_always",
        "eventually",
        "s_eventually",
        "nexttime",
        "s_nexttime",
        "implies",
        "case",
        "endcase",
        "default",
        "inside",
        "assert",
        "property",
        "sequence",
        "accept_on",
        "reject_on",
        "sync_accept_on",
        "sync_reject_on",
    ]
)


def _identifiers(text: str) -> set[str]:
    """The identifiers of an expression text (system functions and keywords left out)."""
    return {
        word
        for word in re.findall(r"[A-Za-z_]\w*", re.sub(r"\$\w+", " ", text))
        if word not in _KEYWORDS
    }


def disable_only_signals(case: TraceCase) -> tuple[str, ...]:
    """The signals read by the ``disable iff`` condition, in declaration order.

    A ``disable iff`` condition is evaluated with current values, after the non-blocking updates of
    the time slot (IEEE 1800-2017 16.12), whereas the property reads sampled values from before
    them (16.5.1). Both must give the table's row ``k`` at rising edge ``k``; with a driver that
    changes at the rising edge that is only possible for signals the property does not read, which
    are selected by the previous counter value (``previous_lines``). Raises ``ValueError`` when a
    signal is read by both the condition and the property: no such case exists in the tables, and a
    driver would need two versions of the signal."""
    if case.disable is None:
        return ()
    condition = _identifiers(case.disable) & set(case.widths)
    shared = condition & _identifiers(case.property)
    if shared:
        raise ValueError(
            f"{case.file}:{case.name}: {sorted(shared)} is read by both the property and the "
            "disable iff condition, which a rising-edge driver cannot present correctly"
        )
    return tuple(name for name in case.widths if name in condition)


def previous_lines(case: TraceCase, base: str) -> tuple[str, ...]:
    """A register ``<base>_prev`` holding ``base`` as it was before the last rising edge. A
    ``disable iff`` condition reads current values, after the non-blocking updates of the edge,
    when ``base`` has already advanced to the next row; the register still holds the row of that
    edge, which is the row a signal read only by the condition must show."""
    width = counter_width(len(case.samples))
    return (
        f"  // {base}_prev: {base} before the last rising edge. The disable iff condition reads",
        "  // current values, after the update, when the counter has already moved on; the signals",
        "  // only it reads are selected by this previous value and so show the row of the edge",
        f"  logic [{width - 1}:0] {base}_prev = 0;",
        f"  always @(posedge clk) {base}_prev <= {base};",
    )


def stimulus_lines(case: TraceCase, selector: str, previous: str | None = None) -> tuple[str, ...]:
    """One ``wire`` per signal: the written value per ``selector`` value, last value held. With
    ``previous`` the signals of ``disable_only_signals`` are selected by it instead."""
    samples = len(case.samples)
    width = counter_width(samples)
    late = set(disable_only_signals(case)) if previous is not None else set()
    lines: list[str] = []
    for name, bits in case.widths.items():
        chosen = previous if name in late and previous is not None else selector
        value = f"{bits}'d{case.samples[-1][name]}"
        for k in reversed(range(samples)):
            value = f"({chosen} == {width}'d{k} ? {bits}'d{case.samples[k][name]} : {value})"
        lines.append(f"  wire [{bits - 1}:0] {name} = {value};")
    return tuple(lines)


def row_string(case: TraceCase, name: str) -> str:
    """The stimulus string of signal ``name`` (corpus contract point 12): samples separated by
    one space; 1-bit rows ``.`` and ``1``; wide rows ``ceil(width/4)`` uppercase hex digits."""
    width = case.widths[name]
    if width == 1:
        return " ".join("1" if sample[name] else "." for sample in case.samples)
    digits = _digits(width)
    return " ".join(f"{sample[name]:0{digits}X}" for sample in case.samples)


def decode_row_string(text: str, width: int) -> tuple[int, ...]:
    """The values written by ``text`` (the decoder's reading; ``.`` is a zero digit)."""
    values: list[int] = []
    for field in text.split(" "):
        value = 0
        for char in field:
            value = value * 16 + (0 if char == "." else int(char, 16))
        values.append(value)
    return tuple(values)


def _digits(width: int) -> int:
    return (width + 3) // 4


def string_stimulus_lines(
    case: TraceCase, selector: str, previous: str | None = None
) -> tuple[str, ...]:
    """Constant wires holding the row strings and one ``wire`` per signal decoding them by bit
    selects on ``selector`` (hold-last past the end). With ``previous`` the signals of
    ``disable_only_signals`` are decoded with it instead (their own index ``row_idx_prev``). Used by
    the corpus; the EBMC design keeps ``stimulus_lines``. Characters are compared as one-character
    literals."""
    samples = len(case.samples)
    width = counter_width(samples)
    late = set(disable_only_signals(case)) if previous is not None else set()
    lines: list[str] = []
    if any(bits > 1 for bits in case.widths.values()):  # the digit decoder is for wide rows only
        lines += [
            "  function automatic [3:0] hexd(input [7:0] c);",
            '    hexd = (c >= "A") ? 4\'(c - "A" + 8\'d10) :',
            '           (c >= "0" && c <= "9") ? 4\'(c - "0") : 4\'h0;',
            "  endfunction",
        ]
    lines += [
        "  // position of the current sample in the rows below (the last one is held past the end)",
        f"  wire [31:0] row_idx = ({selector} < {width}'d{samples}) ?",
        f"                          32'({selector}) : 32'd{samples - 1};",
    ]
    if late and previous is not None:
        lines += [
            "  // the same for the signals read only by the disable iff condition",
            f"  wire [31:0] row_idx_prev = ({previous} < {width}'d{samples}) ?",
            f"                            32'({previous}) : 32'd{samples - 1};",
        ]
    for name, bits in case.widths.items():
        digits = 1 if bits == 1 else _digits(bits)
        stride = digits + 1
        length = samples * digits + (samples - 1)
        row = f"ROW_{name}"
        lines.append(f'  wire [{8 * length - 1}:0] {row} = "{row_string(case, name)}";')

        index = "row_idx_prev" if name in late else "row_idx"

        def char(
            offset: int,
            row: str = row,
            length: int = length,
            stride: int = stride,
            index: str = index,
        ) -> str:
            return f"{row}[8*({length - 1 - offset}-{stride}*{index}) +: 8]"

        if bits == 1:
            lines.append(f'  wire {name} = ({char(0)} == "1");')
        else:
            digit_values = ", ".join(f"hexd({char(j)})" for j in range(digits))
            lines.append(f"  wire [{bits - 1}:0] {name} = {bits}'({{{digit_values}}});")
    return tuple(lines)


def directive_line(
    case: TraceCase,
    label: str = "p",
    include_disable: bool = True,
    wrap_initial: bool = False,
) -> str:
    """The case's directive on ``@(posedge clk)`` (no indentation), with its ``disable iff`` when
    present and ``include_disable`` (the EBMC design leaves it out: the differential tier
    excludes ``disable iff``), and, with ``wrap_initial`` and ``case.procedure``, written as
    ``initial <label>: ...`` (the EBMC design never wraps)."""
    disable = (
        f"disable iff ({case.disable}) " if include_disable and case.disable is not None else ""
    )
    text = f"{label}: {case.kind} property (@(posedge clk) {disable}{case.property});"
    return f"initial {text}" if wrap_initial and case.procedure is not None else text
