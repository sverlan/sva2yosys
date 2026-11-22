# Registered rising-edge pulse

New users can follow [Your first proof](../../docs/getting-started.md) for setup
and a step-by-step walkthrough of this example.

The correct design proves and reaches its cover; the deliberate bug is refuted. The
expected results are derived from the design's contract, not captured from tool output.

`rtl/pulse.sv` remembers the previous sampled `level`, also during reset. A rising
level produces `pulse_out` at the following sample; a held-high level produces no
further pulses. Reset clears the output register. The property module uses `$rose`
to check both pulse and no-pulse cases, plus one-sample pulse width. Its cover
observes a rising input, a pulse, then no pulse.

The initial state is zero; inputs and reset are otherwise unconstrained. The
three assertions use reset as their disable input. No response/fairness assumption
is needed. All temporal comparisons use the same positive-edge clock. A level that is already high at the first sample counts as a rise (`$rose` compares with the unknown value before the first sample), and a reset on the tick after a rise cancels the attempt that the rise started.

From this directory, in an environment containing sva2yosys and SBY:

```text
sva2yosys props/pulse_props.sv -y rtl --top pulse -o build
sby -f -d build/prove pulse.sby prove
sby -f -d build/cover pulse.sby cover
```

Expected: four accepted directives, `prove` PASS and the single cover reached.
To demonstrate failure, regenerate the same build with the buggy design:

```text
sva2yosys props/pulse_props.sv -y rtl --top pulse -D EXAMPLE_BUG -o build
sby -f -d build/bug pulse.sby prove
```

The bug registers the input level instead of its rising edge. Holding `level`
high for two ticks stretches the output pulse; expect a counterexample (FAIL).
Run the original generation command again to restore the correct design.
