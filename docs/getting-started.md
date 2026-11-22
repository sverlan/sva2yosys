# Your first proof

This walkthrough checks a registered rising-edge detector, proves its assertions,
reaches its cover, and demonstrates a counterexample for a deliberate bug. It uses
the checked-in [pulse example](../examples/pulse/README.md); no testbench or external
source corpus is needed.

## Set up the compiler

Install sva2yosys as described in the
[README](https://github.com/sverlan/sva2yosys#installation) (Python 3.12 or newer),
for example with `pipx install sva2yosys` or `uv tool install sva2yosys`, and check
that the command works:

```text
sva2yosys --help
```

The Slang frontend (pyslang) is installed with the package; a separate Slang
executable is not needed for compilation.

The example sources are not part of the package. Either clone the repository (MIT
licence, see [LICENSE](../LICENSE)) or download and unpack its ZIP archive from
GitHub, and work in its `examples/pulse` directory:

```text
git clone --depth 1 https://github.com/sverlan/sva2yosys
cd sva2yosys/examples/pulse
```

Or create an empty directory and save the three listings of this guide in it,
under exactly these names: `rtl/pulse.sv`, `props/pulse_props.sv` and `pulse.sby`.

To run the formal steps, also provide Yosys, SymbiYosys (`sby`), ABC and Yices
through an installation such as the
[OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build). Make its tools available in the shell
where you run SBY, following that installation's environment setup. The supplied
recipe uses ABC PDR for proof and `smtbmc yices` for cover. The compiler does not
install or invoke these tools. You can complete the check and generation steps
without them.

Generate and run SBY in the **same environment**: native Windows throughout, or
Linux/WSL throughout with its own sva2yosys installation, example copy and tool
installation.
Generated read scripts contain absolute paths. Passing a Windows-generated
script to Linux SBY requires more than translating the `.sby` filename; see the
[Windows/WSL guidance](usage.md#windowswsl-paths).

## Check the properties

Run all remaining commands in the directory that contains `rtl/`, `props/` and
`pulse.sby`; the generated files go to `build/` there.

The design, [`rtl/pulse.sv`](../examples/pulse/rtl/pulse.sv), registers a pulse
when `level` rises; a held-high input must not stretch that pulse. Its registers
start at zero, and reset is synchronous. Inputs and reset are otherwise
unconstrained.

```systemverilog
// Interface: registered rising-edge detector; output describes the previous tick.
module pulse(input logic clk, rst, level, output logic pulse_out);
    logic previous_level = 1'b0;
    logic pulse_q = 1'b0;

    always_ff @(posedge clk) begin
        previous_level <= level;  // follows the input, also during reset
        if (rst)
            pulse_q <= 1'b0;
`ifdef EXAMPLE_BUG
        else
            pulse_q <= level;
`else
        else
            pulse_q <= level && !previous_level;
`endif
    end

    assign pulse_out = pulse_q;
endmodule
```

The properties, in [`props/pulse_props.sv`](../examples/pulse/props/pulse_props.sv),
live in a separate module that `bind` attaches to the design. `$rose(level)` is true at a
sample where `level` has just risen (also at the first sample, if `level` is already high); a reset on the tick after a rise cancels the attempt that the rise started. The three assertions
require a pulse in the next sample after a rising input, no pulse otherwise, and a
pulse of one sample only. The cover asks for a rising input followed by a pulse and
then no pulse. `disable iff (rst)` suppresses these obligations during reset; it
does not force a reset to happen.

```systemverilog
module pulse_props(input logic clk, rst, level, pulse_out);
    default clocking @(posedge clk); endclocking
    default disable iff (rst);

    a_rise: assert property ($rose(level) |=> pulse_out);
    a_no_rise: assert property (!$rose(level) |=> !pulse_out);
    a_width: assert property (pulse_out |=> !pulse_out);

    c_pulse: cover property ($rose(level) ##1 pulse_out ##1 !pulse_out);
endmodule

bind pulse pulse_props u_props (
    .clk(clk), .rst(rst), .level(level), .pulse_out(pulse_out)
);
```

First check that the directives can be lowered:

<!-- getting-started-cli -->
```text
sva2yosys props/pulse_props.sv -y rtl --top pulse
```

Expect three assertions and one cover, summarized as:

```text
4 assertion(s): 4 accepted, 0 rejected
```

This checks whether the directives can be lowered. It does not prove them and
does not write a build. Read warnings, skipped directives and rejection messages
when checking other designs; an accepted count alone is not a verification result.

## Generate the monitors

<!-- getting-started-cli -->
```text
sva2yosys props/pulse_props.sv -y rtl --top pulse -o build
```

The output includes plain SystemVerilog monitors, rewritten verification code,
design copies where bind lowering needs inserted instances, and
`build/sva2yosys_read.ys`. Original input files remain unchanged. The read script
tells Yosys how to load these files; use it instead of assembling a separate file
list. More detail is in [CLI inputs and outputs](usage.md).

## Prove and reach a cover

The SBY recipe, [`pulse.sby`](../examples/pulse/pulse.sby), has two tasks. `prove`
runs the unbounded proof engine ABC PDR; `cover` searches for a witness with the
bounded solver engine `smtbmc yices` up to depth 12. Its `[script]` section loads
the generated read script, and the design top is `pulse`.

```ini
[tasks]
prove
cover

[options]
prove: mode prove
cover: mode cover
cover: depth 12

[engines]
prove: abc pdr
cover: smtbmc yices

[script]
script sva2yosys_read.ys
prep -top pulse

[files]
build/sva2yosys_read.ys
```

The engine and solver are your choice: change the `[engines]` lines to use another
one, provided it is installed. The options are described in the
[SBY reference](https://yosyshq.readthedocs.io/projects/sby/en/latest/reference.html#engines-section).

Run the tasks:

```text
sby -f -d build/prove pulse.sby prove
sby -f -d build/cover pulse.sby cover
```

Expect `PASS` for both tasks. `prove` checks the three assertions for all modeled
input sequences, rather than only for a chosen finite stimulus. `cover` finds a
witness within its configured depth of 12 for the single pulse sequence. A reached
cover demonstrates that sequence is possible; it does not prove an assertion.

SBY writes a `status` file and `logfile.txt` in each task directory. Inspect
`build/prove` and `build/cover`; the log names any generated witness traces.
The `-f` option replaces that task's previous output directory when rerunning it.

Keep SBY's default `multiclock off`. These monitors use one solver step per
sampled clock tick. Assertions sample values before that tick's nonblocking
updates, so the example uses `|=>` for the following sample. Several design clocks
would tick together in this mode; it does not verify clock-domain crossings.
This walkthrough needs no liveness engine; strong unbounded assertions have a
separate [liveness workflow](usage.md).

## See an expected failure

The example's `EXAMPLE_BUG` macro (the `ifdef` in `pulse.sv` above) changes the RTL
to register the input level instead of detecting its rising edge. Holding the input high can stretch the
output pulse. Regenerate with that macro:

<!-- getting-started-cli -->
```text
sva2yosys props/pulse_props.sv -y rtl --top pulse -D EXAMPLE_BUG -o build
```

Then run the same proof task with a separate output directory:

```text
sby -f -d build/bug pulse.sby prove
```

Expect `FAIL` and a nonzero command exit status. Here a failed proof is the intended
result: SBY has found a counterexample to an assertion. Inspect
`build/bug/logfile.txt` for the failing assertion and trace location. An `ERROR`
or missing solver is a setup problem, rather than this expected counterexample.

Restore the correct generated design before continuing:

<!-- getting-started-cli -->
```text
sva2yosys props/pulse_props.sv -y rtl --top pulse -o build
```

You can rerun the original prove and cover commands to obtain PASS again.
Previous task results remain records of the builds they checked; regeneration
alone does not refresh those results.

## Adapt the example

Keep assertions and supporting verification code in positional input files.
Supply RTL through `-y` directories (module-named files) or explicit `-v` files,
and select the design top with `--top`. Follow the example's module binding and
SBY read-script recipe. Start with a property whose failure you can demonstrate,
then add reset and environment assumptions deliberately.

For more practice, try the [counter](../examples/counter/README.md),
[handshake](../examples/handshake/README.md) and [FIFO](../examples/fifo/README.md)
examples; the [examples README](../examples/README.md) describes them all.
Use the [SVA reference](sva-reference.md) for accepted syntax and operand
restrictions, the [support matrix](supported-sva.md) for validation status, and
the [CLI manual](usage.md) for file lists, macros, includes and outputs.

If a run fails:

- **`sva2yosys` is unavailable:** check the installation and that the directory
  holding the installed command is on your `PATH` (`pipx ensurepath` does this for pipx).
- **A tool or solver is missing:** check the formal installation's shell setup
  and the engine named in `pulse.sby`. Python installation alone supplies neither.
- **A directive is rejected:** inspect its located diagnostic and the SVA reference.
  Unsupported syntax is not a proof failure.
- **Paths cannot be opened:** check the working directory and whether generation
  and verification used different hosts. Regenerate after moving a checkout.
- **Results are surprising:** inspect initial state, reset, assumptions, sampled
  timing and warnings before interpreting PASS. See [verification guarantees and
  limitations](validation.md).
