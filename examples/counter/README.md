# Enabled modulo-four counter

The correct design proves and reaches its cover; the deliberate bug is refuted. The
expected results are derived from the design's contract, not captured from tool output.

`rtl/counter.sv` starts at zero, increments its two-bit value when `enable` is
high, holds otherwise and clears on synchronous reset. Two-bit addition wraps
from three to zero. Properties check increment/hold with nonoverlapping
implication, `$past` and `$stable`, and check reset separately without disabling that check
during reset. A cover observes 0, 1, 2, 3, 0 on successive samples.

Inputs are unconstrained; no assumption forces enable or suppresses resets.
Declaration initialization defines the initial state. Increment/hold obligations
are disabled by the reset input, while `rst |=> value == 0` remains active.
The `$past` and `$stable` expressions occur in the following-sample consequents,
after the trigger sample has been observed.

From this directory:

```text
sva2yosys props/counter_props.sv -y rtl --top counter -o build
sby -f -d build/prove counter.sby prove
sby -f -d build/cover counter.sby cover
```

Expected: four accepted directives, `prove` PASS and wraparound cover reached.

```text
sva2yosys props/counter_props.sv -y rtl --top counter -D EXAMPLE_BUG -o build
sby -f -d build/bug counter.sby prove
```

The bug increments by two. One enabled tick without reset changes zero to two
instead of one, so the following sample violates the increment property (FAIL).
Regenerate without `EXAMPLE_BUG` before running the correct-design recipe again.
