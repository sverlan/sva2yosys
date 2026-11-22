# Using sva2yosys

For installation and a complete proof/cover/counterexample walkthrough, start
with [Your first proof](getting-started.md).

For accepted SVA forms, operand restrictions, warnings and runnable examples, see
the [SVA reference](sva-reference.md). The [support matrix](supported-sva.md)
records the validation status of each construct.

sva2yosys reads your **verification files** (SystemVerilog Assertions and the code that supports them) together with your **design**. It rewrites the verification files so that the free Yosys/SBY flow checks the assertions with exact SVA semantics. The design is only read, never modified.

```text
sva2yosys PROPS... [-y DIR]... [--libext EXT]... [-v FILE]... [-f LIST]... [-F LIST]...
          [-I DIR]... [-D NAME[=VALUE]]... [--top NAME]...
          [--strategy direct|auto|window|history] [--x-checks skip|reject]
          [--max-expansion N] [--max-work N] [--max-state-bits N] [--max-emission N]
          [--yosys-frontend verilog|slang] [-o DIR [--equivalence] [--allow-design-sva]]
sva2yosys --version
```

`sva2yosys --help` prints the same summary, and `sva2yosys --version` the version of the installed package (`sva2yosys 0.1.0`; semantic versioning, see the [changelog](../CHANGELOG.md)).

## Typical use

The commands below run as they are from the `examples/fifo` directory of the repository.

```text
# check which assertions would be lowered
sva2yosys props/fifo_props.sv -y rtl --top fifo

# write the result into build/
sva2yosys props/fifo_props.sv -y rtl --top fifo -D FORMAL -o build
```

Then let Yosys read what was written, e.g. in the `[script]` section of an `.sby` file:

```ini
[options]
mode prove

[engines]
abc pdr

[script]
script /abs/path/to/build/sva2yosys_read.ys
prep -top fifo
```

Keep SBY's default `multiclock off`: the generated checks require it ([`docs/semantics.md`](semantics.md#4-timing-table-normative) §4). In that mode one solver step is one clock cycle and every clock of the design ticks at every step, so a design with several clocks is verified as if all its clocks were the same: clock-domain crossings (asynchronous FIFOs, synchronisers) are not checked.

## The two kinds of input

| | Verification files | Design files |
|---|---|---|
| Given as | positional arguments `PROPS` | found through `-y`, `-v`, `-f`/`-F` |
| Contain | `assert`/`assume`/`cover property`, `property`/`sequence`, `default clocking`, `default disable iff`, `bind`, property modules, glue logic | the RTL |
| sva2yosys | translates them | never modifies them |

The verification files usually reach the design through `bind`: for example, `bind fifo fifo_props u_props (.*);` attaches a property module to every `fifo` instance.

## Options

### Verification files: `PROPS`

Each positional argument is a verification file, and at least one is required. In generation, every concurrent assertion in it is replaced by an instance of a generated monitor. `property`/`sequence` declarations and clocking blocks are removed. `bind` statements are lowered (see *Outputs*). Everything else is kept character for character, and lines keep their numbers.

### Design files: `-y`, `--libext`, `-v`, `-f`, `-F`, `-I`, `-D`, `--top`

These options follow the conventions of other Verilog and SystemVerilog tools (`-y`, `+libext+`, `+incdir+`, `+define+`, `-f`, `-F`), so an existing project filelist usually works as it is. This section states what is specific to sva2yosys.

| Option | Meaning |
|---|---|
| `-y DIR` | library directory: a module, interface or package that is used but declared by no loaded file is loaded from `DIR/<name><ext>` |
| `--libext EXT` | extension tried in `-y` directories, besides `.v` and `.sv` (e.g. `--libext .vh`) |
| `-v FILE` | design file, **always loaded** (in some tools `-v` names a library file that is read only when needed; use `-y` for that) |
| `-f LIST`, `-F LIST` | filelist; relative paths are relative to the current directory with `-f`, to the filelist's own directory with `-F` |
| `-I DIR` | include directory for `` `include `` |
| `-D NAME[=VALUE]` | macro, for every file (value `1` when omitted) |
| `--top NAME` | top module |

**How `-y` searches.**

- The names looked up are the modules and interfaces that are instantiated, the targets of a `bind`, the packages that are imported, and the module named by `--top`.
- Files found this way are searched in turn for their own needs, until nothing is missing. Only files that are actually needed are loaded, so other files in `DIR` are ignored.
- Several `-y` are searched in the order given; the first directory with a candidate wins. If one directory holds two candidates for a name (`fifo.v` and `fifo.sv`), the run stops with an *ambiguous* error rather than picking one.
- The file must be named after what it declares (`fifo.sv` for `module fifo`). A file declaring several modules, or a package not named after its file, is not found by `-y`; give it with `-v`.

**Includes.** `-I` answers "where is `defs.svh`?" and is driven by `` `include ``; `-y` answers "which file declares module `fifo`?". A relative include is first searched next to the including file, so headers beside your properties need no `-I`; shared headers elsewhere do (`-I inc`).

**Filelists.** The entries that are understood:

| Entry | Meaning |
|---|---|
| `FILE` | a design file (as `-v`) |
| `-v FILE` | a design file |
| `-y DIR` | a library directory |
| `--libext EXT`, `+libext+EXT[+EXT…]` | library extensions |
| `-I DIR`, `-IDIR`, `+incdir+DIR[+DIR…]` | include directories |
| `-D NAME[=VALUE]`, `-DNAME[=VALUE]`, `+define+NAME[=VALUE][+…]` | macros |
| `-f LIST`, `-F LIST` | nested filelists |

**Any other option in a filelist is an error, never silently ignored**: a filelist written for another tool may contain options (a language mode, a vendor switch) that would change what is checked if they were skipped. Comments: an entry `//` or an entry starting with `#` comments out the rest of the line, and an entry starting with `/*` opens a block comment up to the next `*/`. Inside an entry these characters are ordinary (`rtl/foo#v2.sv` is a file name). An entry like `//server/x.sv` is an error: write `// comment` with a space (UNC paths are not supported).

**`--top NAME`** is required as soon as a design is given (`-y`, `-v`, `-f`, `-F`). Otherwise any module of a design file that nobody instantiates would become a top, and what gets checked would depend on unrelated files.

- The whole design is elaborated from that module. The module is looked up with `-y` whenever no design file declares it, even if a verification file declares a module of the same name.
- A module, interface, program or package declared in two loaded files is an error. Slang would silently use one of them, so a verification module named like the design top could replace the design.
- With verification files only (no design), `--top` may be omitted; Slang then picks the uninstantiated modules.

### Monitor forms: `--strategy`, `--equivalence`

**`--strategy direct|auto|window|history`.** Selects how each monitor stores its temporal state. The forms and their sizes are explained in [`strategies.md`](strategies.md).

- `direct` (the default): no optimisation. The monitor reports every failure and every success at the earliest sample.
- `auto`: a compact form (`window`, a counter) where it applies and is expected to give less logic; `direct` elsewhere.
- `window` / `history`: force that form where it applies. `history` keeps the history of the trigger and the signals and recomputes every age from it: fewer registers but much more logic, and not faster to prove in our measurements. `auto` never picks it.

Some properties have no choice, and `--strategy` does not apply to them. A monitor whose consequent is a weak unbounded sequence (`a |-> ##[1:$] b`, a weak `until`) cannot keep one slot per age, because there is no maximum age: it keeps one register per reachable set of positions that an obligation can be in (a configuration), and has the form `configurations` ([`strategies.md`](strategies.md#unbounded-consequents-the-configuration-set-machine)). A monitor whose antecedent has an unbounded part (`##[m:$]`, `[*m:$]`, goto or nonconsecutive repetition, an unbounded `always`) has the form `direct` when its consequent is bounded (with an unbounded consequent it has the form `configurations`). A property that needs per-attempt results has the form `attempt` (see *What a check prints*). A consequent that cannot die (`a |-> ##[1:$] b`) can never fail: the check accepts it and warns (see *Consequents that can never fail*, below).

A `cover` of an unbounded sequence also has the form `configurations`. Supported: a strong unbounded sequence (the cover default, or `strong(...)`: `cover property (a ##[1:$] b)`) and an implication with a bounded antecedent of one match length into one (`cover property (a |-> ##[1:$] b)`); each attempt is covered once, at the sample where its first match ends. The other covers with an unbounded part are rejected with a located message; the support matrix lists them ([`supported-sva.md`](supported-sva.md)).

No form reports any event later than `direct`. `window` is exact up to and including the first failure, which is all an `assert` or `assume` consumes; it is never used for covers. If you need every failure reported (e.g. you count failures), keep `direct`. The check output shows the chosen form and its state bits per assertion, and each monitor's header comment names it.

**`--equivalence`** (with `-o`): writes `DIR/equivalence/<monitor>/` for every optimised monitor. Running `sby -f equivalence.sby` there proves, with `abc pdr`, that the optimised monitor's `fail` and `success` outputs equal the direct monitor's, within the form's exactness scope: at every sample for `history`; for `window`, up to and including the first failure (what an `assert` or `assume` consumes; nothing is compared afterwards). So you can check the optimisation of your own properties.

### X-checks: `--x-checks`

**`--x-checks skip|reject`.** `$isunknown(e)` is 1 when a bit of `e` is X or Z (IEEE 1800 §20.9). The monitors are two-state, so no value is X or Z and the call is evaluated as the constant 0 ([`semantics.md`](semantics.md) §8a).

- `skip` (the default): a directive that checks nothing afterwards (`assert property (!$isunknown(s))`, `v |-> !$isunknown(s)`, an `assume` of it, a `cover` of `$isunknown(s)`, a trigger `$isunknown(s) |-> p`, a `disable iff` that is always true) is reported as `skipped (X-check)`. It gets no monitor, is counted apart from accepted and rejected and is not an error. With `-o` its statement is replaced by a comment saying why. A directive that keeps other checks (`a |-> b && !$isunknown(c)`) is accepted with a warning that its X part was dropped. When the tool cannot prove that nothing is left, the directive is kept (with the warning), never skipped.
- `reject`: every directive that uses `$isunknown` is rejected with a located message.
- `$isunknown` of an operand using `$past` is rejected under both settings: `$past` is X at the first samples, which the two-state model cannot represent.

### Generation: `-o DIR`, `--allow-design-sva`

**`-o DIR`** writes the result into `DIR` (created if needed). Without `-o`, sva2yosys only checks.

**`--allow-design-sva`.** When writing with `-o`, SVA found in a design file is an error by default (check mode does not report it): assertions, declarations, binds and clocking blocks, including in a file included by a design file. sva2yosys does not translate design files, so such SVA would reach Yosys unchanged: `read_verilog` applies its own SVA semantics (or none) and does not support `bind` (a syntax error since Yosys 0.70, silently ignored before). The fix is usually to move it to a verification file, or pass the file as `PROPS`. With `--allow-design-sva` it becomes a warning, for when you accept Yosys's own handling.

### Binds and the Yosys frontend: `--yosys-frontend`

**`--yosys-frontend verilog|slang`.** Selects the Yosys frontend that the generated read script uses. Check mode applies the same rules, so with `slang` it rejects an assertion that this frontend cannot read (a liveness part). The differences between the two are in [`architecture.md`](architecture.md#yosys-frontend-findings).

- **`verilog`** (the default) writes `read_verilog -formal -sv` commands. That frontend does not support `bind`: it is a syntax error since Yosys 0.70, and earlier versions parsed it but never applied it, so a bound property module would silently disappear and a proof would pass with nothing checked. So each `bind` of a verification file is **lowered**: a copy of the bound design file is written with the instance inserted (your original file is never modified).
- **`slang`** writes `plugin -i slang` and one `read_slang` command, using the Slang-based frontend of the OSS CAD Suite (the plugin yosys-slang, tested with Yosys 0.63 and 0.70), which applies `bind`. **Binds are kept**: your `bind` statements stay as written in the written verification files, and no design file is copied. `--top` is required with `-o` in this mode. In recent Yosys versions `read_slang` is built in (checked with 0.70); the `plugin -i slang` line is still accepted, but a later Yosys may drop it, in which case the line has to be removed from the script.

**Limitations of `slang`, in the current state of the tool.**

- **Liveness cannot be done.** An assertion with a liveness part (a strong unbounded property such as `s_eventually`, and a strong `assume` read as a fairness constraint; the `live=` field of the check output) is rejected, because `read_slang` does not support the forms the generated liveness monitors use. No `sva2yosys_read_live.ys` is written. Check those assertions with the default frontend.
- **Instance binds** (`bind top.u1 …`) are rejected, as in the default mode: bind the module type.
- **Read the written files only with the script that was written.** The files of a `slang` run must not be read with `read_verilog`: since Yosys 0.70 it stops with a syntax error at the `bind`; earlier versions silently dropped the bound property module, and SBY then reported a pass for a design that checks nothing.
- A `bind` whose target module is not under `--top` is silently unused, like any module nobody instantiates.

### Compiler resource limits: `--max-expansion`, `--max-work`, `--max-state-bits`, `--max-emission`

Resource limits reject a property with a source location; they never shorten a range
or approximate its semantics. Check mode and output generation use the same policy.
On rejection no new rewrite output is written. Limits begin at typed IR, after Slang
elaboration; they are not wall-clock, RAM or solver-capacity guarantees.

| Option | Default | What is limited |
|---|---:|---|
| `--max-expansion` | 16384 | letters of a property (estimated from its text, and actually created), expression widths and sampled-history bits |
| `--max-work` | 100000000 | construction and emission steps of the compiler |
| `--max-state-bits` | 8192 | temporal registers of the bounded canonical monitor (not the sampled-value history); the per-attempt and configuration machines have limits of their own |
| `--max-emission` | 200000 | estimated size of the generated code for the chosen form |

Values are nonnegative integers; each limit is inclusive. They apply per monitor and per compilation/emission phase, with a written-directive check before conjunctions split. A run with many monitors is not subject to one aggregate memory allowance. For example, `--max-state-bits 16384` raises the canonical temporal-state limit. The limits on the letters, instances, registers and gates of the per-attempt machine, and on the number of configurations, are fixed and stay in force.

Passing the limits does not mean that a solver can prove the property in reasonable time: the compiler admits many properties that are too large for a solver. The defaults are set so that large delay, repetition and window properties (ranges of several thousand samples) are still admitted.

## What a check prints

```text
ok        fifo_top.u_fifo.u_props.a_no_overflow  assert  slots=1  form=direct  bits=3
rejected  props/fifo_props.sv:31:3  fifo_top.u_fifo.u_props.a_live: a strong unbounded property (liveness) in 'assume' is supported only when its obligations share one configuration (a fairness constraint); this one needs a witness, which picks one obligation, so as an assumption it would constrain only the obligation the solver chooses (docs/semantics.md section 9.6)
3 assertion(s): 2 accepted, 1 rejected
```

Each `ok` line gives the form of the monitor (`form=`: `direct`, `window`, `history`, `configurations` or `attempt`), the number of per-age obligation slots it uses (`slots=`; 0 for `configurations` and `attempt`) and its state bits (`bits=`), and for a strong property the representation of its liveness part (`live=`, see *Strong properties*). A property that needs per-attempt results (a nested implication, `not` or `or` of implications, `if`/`else` with implication branches, a cover of `and` or of an implication whose antecedent has several lengths, `eventually [m:n]`, `always [m:n]` of a non-sequence) has `form=attempt`: its monitor follows every attempt separately ([`strategies.md`](strategies.md)), and `--strategy` does not apply to it. The parts of a property `and` in an `assert`/`assume` are joined with `+` (e.g. `form=direct+attempt`).

There is one line per assertion and elaborated instance, then a summary. With X-checks (`--x-checks`, above) there are two more kinds of line, and the summary counts the skipped ones apart (that part appears only when there are some):

```text
skipped   props/fifo_props.sv:12:5  fifo_top.u_fifo.u_props.a_known: X-check: $isunknown is evaluated as 0 (two-state model), so this assert can never fail
ok        fifo_top.u_fifo.u_props.a_data  assert  slots=3  form=direct  bits=5
warning   props/fifo_props.sv:20:45  fifo_top.u_fifo.u_props.a_data: $isunknown is evaluated as 0 (two-state model): the X part of this assert is dropped, the rest is still checked
rejected  props/fifo_props.sv:25:49  fifo_top.u_fifo.u_props.a_past: $isunknown of an operand using $past is not supported: ...
4 assertion(s): 1 accepted, 1 skipped (X-check), 1 rejected
```

A `warning` line follows the `ok` line of its directive and is located at the first `$isunknown`.

**Consequents that can never fail.** An `assert` or `assume` whose weak unbounded consequent always keeps a live match (`a |-> ##[1:$] b`, `a |-> ##[1:$] b ##1 c`, `a |-> ##[1:$] 1'b0`, `always ##[1:$] b`, `##[1:$] b`) is accepted, its monitor is unchanged and the summary and exit status do not change, but it is followed by a `warning` line located at the directive (the weak property holds on every finite trace; checking that `b` eventually happens needs `strong(...)` or `s_eventually`, see *Strong properties* below):

```text
ok        fifo_top.u_fifo.u_props.a_resp  assert  slots=0  form=configurations  bits=2
warning   props/fifo_props.sv:40:3  fifo_top.u_fifo.u_props.a_resp: this assert can never fail: its weak unbounded consequent always keeps a live match (a liveness check needs strong(...) or s_eventually, see docs/usage.md)
```

- For an `assume` the message says it constrains nothing. In a property `and`, the conjunct that cannot fail is named (`conjunct 2 of 2 of this assert can never fail: ...`).
- A directive that can fail gets no warning, even when it is unbounded: `a |-> b[*1:$]` fails at once when `b` is low, and `b until c` can fail.
- The analysis is conservative: it may miss a warning but never warns for a directive that can fail (`a |-> b[->1]` never fails but is not warned about).

**Other things in the output.**

- With `-o`, a warning is written as `<file>:<line>:<col>: warning: ...` and counted in the final `N warning(s)`; the skipped directives are listed as `note` lines after the warnings, and the last line ends with `, N skipped X-check(s)`.
- Which unbounded shapes are accepted, and which are still rejected with `unbounded ranges ($) are not supported yet` (or the message of the construct), is listed in [`supported-sva.md`](supported-sva.md).

### Exit status

| Status | Meaning |
|---|---|
| 0 | check: no assertion rejected (skipped X-checks are not rejections); generation: everything written |
| 1 | a rejected assertion, a refused rewrite, an invalid filelist or compile error |
| 2 | usage error (e.g. no `PROPS`, or a design without `--top`) |

## Outputs (`-o DIR`)

| File | Content |
|---|---|
| `<name>.sv` | each verification file, rewritten, under its own name; each monitor instance is followed by a block comment quoting the assertion as written (whitespace folded to single spaces), e.g. `/* a_resp: assert property (@(posedge clk) req |=> ack); */` |
| `<name>.sv` | each design file that a `bind` targets: a **copy** with the bound instance inserted before `endmodule` of the target module (the original RTL file is never modified) |
| `sva2yosys_monitors.sv` | the generated monitor modules, one per assertion; the header of each gives its source location, the assertion as written (`// as written:`, the text of the first instance when several share the monitor), and the effective clock and `disable iff` condition after defaults (`// clock:`, `// disable iff:`) |
| `sva2yosys_read.ys` | Yosys commands reading everything (with `--yosys-frontend slang`: `plugin -i slang` and one `read_slang` command); the monitors first, then each file after the files declaring the packages it uses (`import p::…`, `p::x`); otherwise design files before verification files |
| `sva2yosys_read_live_<part>.ys` | (never with `--yosys-frontend slang`) one for each liveness part of an `assert`: the read commands with `-DSVA2YOSYS_LIVE_<part>` (and `-DSVA2YOSYS_LIVE_FAIR` when a fairness `assume` is present); read each in a `live` task of its own (*One liveness part per `live` task*) |
| `sva2yosys_read_live.ys` | (never with `--yosys-frontend slang`) only when some directive has a liveness part (a strong unbounded `assert` or accepted strong `assume`, including `not` of a weak one): the same commands with `-DSVA2YOSYS_LIVE`, which enables the liveness checks of the monitors; read it in the `live` task (*Strong properties*) |
| `sva2yosys.f` | the same files as a list (written files relative to `DIR`, others absolute) |
| `equivalence/<monitor>/` | with `--equivalence`: the equivalence job of each optimised monitor |

Each line of `sva2yosys_read.ys` is a `read_verilog -formal -sv` with your `-D` macros and `-I` directories. A file written to `DIR` also gets its original directory as first `-I`, so its relative includes still resolve. Read this script rather than listing files yourself.

To read a generated monitor next to your own assertion, see [Reading a monitor](monitors.md#reading-a-monitor).

## Strong properties: the `prove` and the `live` task

A strong unbounded `assert` (`s_eventually b`, `a |-> s_eventually b`, `a s_until b`, `a |-> strong(##[1:$] b)`, `strong(b[->3])`) or a `not` of a weak unbounded sequence (strong after negation: `not (a ##[1:$] b)`, `not (a until b)`; `not strong(...)` is weak and has no liveness part; [`supported-sva.md`](supported-sva.md)) has two parts. The **safety part** (an obligation whose frontier dies is a failure) is checked like any other assertion. The **liveness part** ("no obligation stays pending for ever", IEEE 1800 16.12.2) is generated inside `` `ifdef SVA2YOSYS_LIVE_<part> `` (a macro of its own monitor, which `SVA2YOSYS_LIVE` defines for all of them) and checked only when that macro is defined. The check line shows how the liveness part is represented, `live=pending` or `live=witness` (see [`semantics.md`](semantics.md) §9.6):

```text
ok        top.u_props.a_resp  assert  slots=0  form=configurations  bits=1  live=pending
```

The liveness part needs SBY's `live` mode (the suprove engine), in which the safety assertions become assumptions. So the directive is checked by **two SBY tasks**, and it holds only when **both** pass. A single `live` task would hide a safety failure (the failing traces are simply removed), so never run the liveness check alone. The `live` task needs an SBY with the suprove engine; run it where one is available (for example through WSL). Rewritten files and monitors are the same in both tasks: only the macro differs.

### One liveness part per `live` task

SBY's `live` mode with the suprove engine decides a task by the **first** verdict it reads, whatever the number of liveness properties in the design ([SBY issue 378](https://github.com/YosysHQ/sby/issues/378)). A task that holds two liveness parts, one failing and one holding, can end in `PASS` when the holding one comes first, and which comes first is not under your control (it changed between Yosys versions). So **each liveness part is checked in a `live` task of its own.**

With `-o`, sva2yosys writes one script for each liveness part of an `assert`, `sva2yosys_read_live_<part>.ys` (`<part>` is the monitor, `p3`, or `p3_c1` for the second conjunct of a property `and`; the module `__sva2yosys_<part>` in `sva2yosys_monitors.sv`, whose header comment names the assertion): the read commands with `-DSVA2YOSYS_LIVE_<part>`, which switches on that monitor's liveness check only, and with `-DSVA2YOSYS_LIVE_FAIR` when the design has a strong `assume` (a fairness constraint holds in every run, so every task has it). A design with several parts gets a warning at each of them. An `.sby` file with the `prove` task and one `live` task for each of two parts:

```ini
[tasks]
prove
live_p0
live_p1

[options]
prove: mode prove
live_p0 live_p1: mode live

[engines]
prove: abc pdr
live_p0 live_p1: aiger suprove

[script]
prove: script /abs/path/to/build/sva2yosys_read.ys
live_p0: script /abs/path/to/build/sva2yosys_read_live_p0.ys
live_p1: script /abs/path/to/build/sva2yosys_read_live_p1.ys
prep -top fifo_top
```

The directive holds when the `prove` task and the `live` task of **every** part of it pass (a property `and` has one part for each conjunct). For a design with a single liveness part, one task `live` reading the combined script `sva2yosys_read_live.ys` (`-DSVA2YOSYS_LIVE`, every part at once) is enough.

**Limit: instances.** A monitor is shared by every elaborated instance of its assertion (instances of a module, iterations of a generate loop), and the macro switches on the monitor, not an instance. An assertion with a liveness part that is elaborated more than once is several parts in one task, which no define separates; the warning says so. Check such instances one at a time, for example one instance in each design you give to SBY.

### Fairness assumptions and covers

A strong unbounded `assume` is accepted as a fairness constraint when its obligations share one configuration (`live=pending`): `assume property (req |-> s_eventually ack)`, `s_eventually ack`, `a s_until b` and `not (a until b)`. Under `SVA2YOSYS_LIVE` it adds an assumption that admits exactly the traces on which every obligation terminates or is disabled. A strong `assume` that needs several configurations (for example `req |-> strong(##[1:$] ack)`) is rejected: a single witness would weaken the assumption ([`semantics.md`](semantics.md) §9.6).

Read `sva2yosys_read_live.ys` (or the script of one liveness part, which also defines `SVA2YOSYS_LIVE_FAIR`) in the `live` task to include fairness. A `live` task needs at least one assertion; a task containing only assumptions is an SBY error. The `prove` task omits fairness and is conservative: a failure can be a prefix with no fair extension. For example, with `assume !ack` and `assume req |-> s_eventually ack`, `assert !req` holds on every admitted infinite trace, but can fail in `prove`. A safety pass remains valid.

The `cover` task omits fairness too. With those same assumptions, `cover property (req)` can be reached although no admitted infinite trace contains `req`. Every accepted cover in such a run therefore receives:

```text
this cover's result ignores the fairness assumptions (strong assume) of this run: a reached cover is provisional, see docs/usage.md
```

**Confirming a cover.** Check one cover at a time in a separate `live` task (`mode live`, engine `aiger suprove`, `multiclock off`, the live read script) in which a sticky bit records the cover monitor's success and the confirmation assertion is the only assertion being checked. Connect the cover monitor's `success` output to `cover_success`, keep all assumption monitors with `CHECK` enabled, set `CHECK=0` on (or omit) the other assertion monitors, whose safety assertions would become assumptions in live mode and could exclude the cover's trace, and add to the top module:

```systemverilog
`ifdef SVA2YOSYS_LIVE
logic seen = 1'b0;
always @(posedge clk) seen <= seen || cover_success;
always @(posedge clk) assert property (s_eventually !seen);
`endif
```

This task **fails exactly when the cover is reachable on a fair infinite trace**; the failure is the confirming witness. A pass means no fair trace reaches it. The monitor parameter `CHECK` is the current mechanism for switching a monitor's check off, and it may change in a future version ([`monitors.md`](monitors.md)).

<a id="windowswsl-paths"></a>

## Windows and WSL paths

Generated read scripts use paths from the environment that ran sva2yosys. If you
generate on Windows and run SBY inside WSL, translate the paths inside all read
scripts (including `-I` directories) to paths that Linux can read, or generate
inside WSL as well. Translating only the `.sby` file's path is insufficient.

## Troubleshooting

### When nothing is written

The run stops with exit status 1, writes nothing, and explains why on standard error, when any of these holds:

- an assertion of a verification file is not supported (the message says why). This includes a parameter that the assertion depends on and that cannot be compared (a `$`-valued, string, real, type or struct parameter, an escaped name, an interface port), instances that would need different monitors which their integral parameters and genvars do not tell apart (see *Source rewriting* in [`architecture.md`](architecture.md)), a name that means something else at the assertion's position, and an assertion that comes from a macro expansion;
- SVA appears in a design file (without `--allow-design-sva`), or in a file included by a verification file;
- a `bind` targets instances (`bind m: top.u1 …`, `bind top.u1 …`) rather than a module;
- an output would overwrite an input, two outputs would have the same name, or an include would find a different file in `DIR`;
- a path, macro or include directory contains whitespace (Yosys commands cannot quote it);
- package dependencies between files form a cycle (Yosys reads files one by one);
- a filelist is invalid, a library name is ambiguous, a module or package is declared in two files, or the sources do not compile.

### If SBY stops with a `RecursionError`

On **Windows**, the `smtbmc` engine of SBY (the Python script `yosys-smtbmc`) can stop with `RecursionError: maximum recursion depth exceeded`, without any message about the property, on a design that holds a wide constant read with a variable index (a lookup table, or a trace written as one long string). `read_verilog` turns such a read into a netlist whose SMT form nests about a thousand terms, which is more than Python's default recursion limit when `yosys-smtbmc` expands the definitions itself (its `--unroll` mode, which SBY turns on for every solver except `z3`). `yosys-smtbmc` raises the limit only on POSIX systems, so on Linux and macOS the same design runs through. It is a limit of that engine, not of the monitors: it happens with a trivial property too, and the `abc pdr` and `aiger suprove` engines do not use that script. Workarounds:

- `smtbmc --nounroll yices` in the `[engines]` section: the simplest, the same solver without the expansion;
- `smtbmc z3`, which SBY runs without the expansion;
- `techmap` and `opt -fast` after `prep` in the `[script]` section, which replaces the word-level cells by gates before the SMT model is written. Use it only when needed: it makes long traces faster, but on small designs the run is somewhat slower.

`boolector` and `bitwuzla` do not help: `yosys-smtbmc` always expands for them, `--nounroll` or not. A lookup table written as a chain of conditional expressions does not trigger the error.
