# Example: a request/acknowledge handshake

A small design in which `start` raises `req` when the design is idle, `ack` follows two samples after `req` rises, and `req` drops the sample after `ack`. The properties check that protocol:

- `a_hold`: a request stays up until it is acknowledged;
- `a_resp`: every request is acknowledged within one to three samples;
- `a_ack_with_req`: an acknowledge only answers a pending request;
- `c_done` (a cover): a complete handshake happens.

All four are disabled while `rst` is high (`default disable iff (rst)`): a reset can abort an open request or acknowledgement obligation.

The files:

- `rtl/handshake.sv`: the design (never modified).
- `props/handshake_props.sv`: SVA properties in a property module, attached to every `handshake` instance with `bind`.
- `handshake.sby`: an SBY job that proves the assertions and reaches the cover.

From this directory:

```text
sva2yosys props/handshake_props.sv -y rtl --top handshake -o build
sby -f handshake.sby
```

The first command writes `build/`:

- the rewritten property file;
- a copy of `handshake.sv` with the bound instance inserted;
- the generated monitors;
- `sva2yosys_read.ys`, which reads all of it (with absolute paths).

`handshake.sby` copies that script into its work directory and runs it. Both tasks pass: `prove` proves `a_hold`, `a_resp` and `a_ack_with_req` (abc pdr), and `cover` reaches `c_done` at step 3.

The test suite runs exactly this example (`tests/functional_tests/formal_designs`), together with mutants of the design that must fail.
