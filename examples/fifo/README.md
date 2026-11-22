# Two-entry FIFO

The correct design proves and reaches both covers; the deliberate bug is refuted. The
expected results are derived from the design's contract, not captured from tool output.

This is a single-clock, two-entry, eight-bit queue. The RTL is a circular queue
with separate read/write pointers and two storage registers. `data_out` shows
the oldest word combinationally whenever the queue is nonempty; its value while
empty is unspecified. `occupancy`, `full` and `empty` describe the current state.

The interface makes acceptance explicit:

- Push is accepted when `push && !full` at the sampled tick.
- Pop is accepted when `pop && !empty` at the sampled tick.
- At full, a simultaneous push/pop accepts only the pop.
- At empty, a simultaneous push/pop accepts only the push.
- With one word, a simultaneous push/pop replaces the head and preserves occupancy.

Push and pop are accepted only outside reset: the RTL's reset branch processes neither request.

The property module uses a shift-based reference model with a head and tail;
it does not reuse the RTL's pointer representation. Its own occupancy decides
which requests should be accepted. Four assertions check occupancy, flags, bounds
and the oldest word. Two covers demonstrate a round trip and a full fill/drain.
Inputs, data and reset remain unconstrained; no assumption excludes boundary
requests. Both models start empty and clear synchronously on reset.

From this directory:

```text
sva2yosys props/fifo_props.sv -y rtl --top fifo -o build
sby -f -d build/prove fifo.sby prove
sby -f -d build/cover fifo.sby cover
```

Expected: six accepted directives, `prove` PASS and both covers reached.

```text
sva2yosys props/fifo_props.sv -y rtl --top fifo -D EXAMPLE_BUG -o build
sby -f -d build/bug fifo.sby prove
```

The bug flips bit zero of the oldest word. Pushing any word into the empty FIFO
then observing the following sample violates the data-order property (FAIL).
Empty output values are never used to claim a data failure. Regenerate without
the macro to restore the correct design.
