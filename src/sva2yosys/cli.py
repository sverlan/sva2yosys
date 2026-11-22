"""Command-line interface (user guide: ``docs/usage.md``).

Usage::

    sva2yosys PROPS... [-y DIR]... [--libext EXT]... [-v FILE]... [-f LIST]... [-F LIST]...
              [-I DIR]... [-D NAME[=VALUE]]... [--top NAME]... [--x-checks skip|reject]
              [-o DIR [--allow-design-sva]]

``PROPS`` are the **verification files**: SVA and supporting code (property modules,
glue, ``bind`` statements). Only they are translated. The **design** is found by Slang:

- ``-y DIR``: a module, interface or package that is instantiated, bound, imported or
  named by ``--top`` but not declared is looked up as ``DIR/<name><ext>``
  (:func:`sva2yosys.slang.invoke.load_design`); ``--libext`` sets the extensions
  (default ``.v`` and ``.sv``);
- ``-v FILE``: a design file, always loaded;
- ``-f LIST`` / ``-F LIST``: a filelist (:mod:`sva2yosys.filelist`); its files count as
  design files, its ``-y``, ``-I``, ``-D`` … options are added;
- ``-I DIR``, ``-D NAME[=VALUE]``: preprocessor include directory and macro, for all
  files; ``--top NAME``: the top module, required when a design is given (else any
  uninstantiated design module would become a top). A name declared in two loaded
  files is an error.

**Check** (no ``-o``): every concurrent assertion of every elaborated instance (in
verification and design files) is converted, validated and compiled to its temporal
machine, and reported on one line:

- ``ok        <scope>.<name>  <kind>  slots=<n>  form=<form>  bits=<m>`` (the form
  chosen by ``--strategy`` and its registered temporal bits; a property that needs
  per-attempt results, e.g. a nested implication, has ``form=attempt``, ``slots=0`` and
  its netlist registers as ``bits``; the conjuncts of a property ``and`` are joined
  with ``+``); a directive with a liveness part (a strong unbounded ``assert`` or L1 ``assume``, or
  ``not`` of a weak unbounded sequence, which is strong after negation; checked
  by the ``live`` task, ``docs/usage.md``) ends with ``live=pending`` or ``live=witness``
  (several parts joined with ``+``)
- ``rejected  <file>:<line>:<col>  <scope>.<name>: <message>``

followed by a summary line. Assertions without a label are named ``(unnamed)``.

``$isunknown`` is evaluated as the constant 0 (the model is two-state; ``--x-checks``,
docs/semantics.md §8a). A directive that checks nothing afterwards is reported on a
``skipped   <file>:<line>:<col>  <scope>.<name>: X-check: <reason>`` line and counted apart
in the summary (``N assertion(s): A accepted, S skipped (X-check), R rejected``, the middle
part only when ``S > 0``); a directive that keeps other checks is ``ok`` and is followed by
``warning   <file>:<line>:<col>  <scope>.<name>: <message>`` saying that its X part was
dropped. ``--x-checks=reject`` rejects every use of ``$isunknown``; an operand using
``$past`` is always rejected.

An ``assert`` or ``assume`` whose weak unbounded consequent provably can never fail
(``a |-> ##[1:$] b``; docs/semantics.md §9.6) is ``ok`` as usual and followed by a
``warning`` line at the directive; ``a |-> b[*1:$]`` can fail and gets none.

**Generate** (``-o DIR``): the verification files are rewritten
(:func:`sva2yosys.rewrite.rewrite`). An SVA item in a design file is an error unless
``--allow-design-sva`` makes it a warning. On success ``DIR`` receives:

- the rewritten verification files and the design copies with lowered binds;
- the monitors file;
- ``sva2yosys_read.ys``: the Yosys commands that read the result, one
  ``read_verilog -formal -sv`` per file (with ``--yosys-frontend slang``: ``plugin -i
  slang`` and one ``read_slang --best-effort-hierarchy -D FORMAL`` command, with the
  binds kept, no design copy and no live script), all as absolute paths. The monitors file comes
  first; then every input, a file declaring a package before every file using it
  (``import p::…``, ``p::x``), otherwise design files before verification files, each
  in loading order (a cycle between files is refused). Each command repeats the ``-D``
  definitions and the ``-I`` directories; a written file gets its original directory
  as first ``-I``, so that its relative includes resolve as for Slang;
- ``sva2yosys_read_live_<part>.ys``, one for each liveness part of an ``assert`` (``<part>`` is
  the monitor, ``p3``, or ``p3_c1`` for a conjunct of a property ``and``): the same commands
  with ``-DSVA2YOSYS_LIVE_<part>``, which enables that monitor's liveness check only (and
  ``-DSVA2YOSYS_LIVE_FAIR`` when a fairness ``assume`` is present). Read each in a ``live``
  task of its own (SBY ``mode live`` decides a task by its first verdict); the ``prove`` task
  reads ``sva2yosys_read.ys``; a strong directive holds when ``prove`` and every one of its
  ``live`` tasks pass;
- ``sva2yosys_read_live.ys``, only when some directive has a liveness part: the same
  commands with ``-DSVA2YOSYS_LIVE``, which enables every liveness check at once (right for
  one liveness part only);
- ``sva2yosys.f``: the same files, one per line (written files relative to ``DIR``);
- with ``--equivalence``, per optimised monitor ``M``: ``equivalence/M/`` with
  ``monitors.sv``, ``miter.sv`` and ``equivalence.sby``, an SBY job proving that ``M``'s
  ``fail`` and ``success`` equal its direct form's within the form's exactness scope (for
  ``window``, up to and including the first failure); ``sby -f equivalence.sby`` in that
  directory.

``--strategy`` (``direct`` by default, i.e. no optimisation; ``auto``, ``window``,
``history``) selects each monitor's form (:mod:`sva2yosys.optimize.forms`). A monitor with an
unbounded antecedent (``a ##[1:$] b |-> c``, ``b[->2]``, unbounded ``always``) has the direct
form only, whatever the strategy.

Files are UTF-8 with ``\\n`` line ends. One ``wrote <path>`` line is printed per file,
then the warnings, then one ``note`` per skipped X-check (the last line ends with
``, N skipped X-check(s)`` when there are some). Nothing is written when the rewrite is
refused (every diagnostic goes to standard error), when a written file would replace an
input file, when two written files would have the same name (e.g. an input named
``sva2yosys.f``), when an include of a written file would find a different file in
``DIR``, or when a path, definition or include directory contains whitespace (Yosys
commands cannot quote it). A write error is reported with exit status 1; files already
written may remain.

Exit status: 0 on success (check: no assertion rejected; generate: the rewrite
succeeded), 1 when an assertion is rejected, the rewrite is refused, a filelist is
invalid or the sources do not compile (Slang's report is written to standard error),
2 for usage errors.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from importlib.metadata import version
from pathlib import Path

from sva2yosys.codegen.emit_configurations import FAIR_DEFINE, LIVE_DEFINE, live_define, live_part
from sva2yosys.codegen.resources import check_emission
from sva2yosys.filelist import FileListError, read_filelist
from sva2yosys.ir.directive import DirectiveKind
from sva2yosys.ir.source import SourceRange
from sva2yosys.optimize.forms import Form, Strategy, choose, state_bits
from sva2yosys.passes.frontend import YosysFrontend, frontend_problem
from sva2yosys.rewrite import (
    MONITORS_FILE,
    FileRole,
    RewriteError,
    Severity,
    live_parts_warning,
    rewrite,
    syntax_items,
)
from sva2yosys.slang.convert import Conversion, Skipped, XChecks, convert
from sva2yosys.slang.export import export_texts
from sva2yosys.slang.invoke import LibrarySearch, SlangError, SlangOptions, load_design
from sva2yosys.temporal.attempts import AttemptMachine, attempt_size
from sva2yosys.temporal.budgets import DEFAULT_LIMITS, BudgetError, ResourceLimits
from sva2yosys.temporal.compile import (
    COVER_FAIRNESS_WARNING,
    CompileError,
    compile_monitors,
    is_fairness_assumption,
    never_fails_warning,
)
from sva2yosys.temporal.configurations import ConfigMachine, config_size
from sva2yosys.temporal.machine import Machine, machine_size

FILE_LIST = "sva2yosys.f"
EQUIVALENCE_DIR = "equivalence"
READ_SCRIPT = "sva2yosys_read.ys"
READ_LIVE_SCRIPT = "sva2yosys_read_live.ys"
READ_LIVE_PREFIX = "sva2yosys_read_live_"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line (``argv`` defaults to ``sys.argv[1:]``) and return the exit
    status."""
    parser = _parser()
    arguments = parser.parse_args(argv)
    limits = ResourceLimits(
        arguments.max_expansion,
        arguments.max_work,
        arguments.max_state_bits,
        arguments.max_emission,
    )
    verification = [Path(name) for name in arguments.files]
    files = [Path(name) for name in arguments.library_file]
    directories = [Path(name) for name in arguments.library_dir]
    extensions = list(arguments.libext)
    include_dirs = [Path(directory) for directory in arguments.include]
    defines = [_define(text) for text in arguments.define]
    try:
        for name, relative in arguments.filelists:
            listed_files = read_filelist(Path(name), relative_to_list=relative)
            files.extend(listed_files.files)
            directories.extend(listed_files.library_dirs)
            extensions.extend(listed_files.library_extensions)
            include_dirs.extend(listed_files.include_dirs)
            defines.extend(listed_files.defines)
    except FileListError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if (files or directories) and not arguments.top:
        # Without it, any uninstantiated module of a design file would become a top.
        parser.error("--top is required when design files are given (-y, -v, -f, -F)")
    if (
        arguments.yosys_frontend == YosysFrontend.SLANG.value
        and arguments.output
        and not arguments.top
    ):
        # read_slang would also elaborate the bound property module as a top of its own,
        # on free inputs.
        parser.error("--top is required with --yosys-frontend slang and -o")
    options = SlangOptions(
        top=tuple(arguments.top), defines=tuple(defines), include_dirs=tuple(include_dirs)
    )
    search = LibrarySearch(tuple(directories), tuple(extensions), tuple(files))
    try:
        sources = load_design(verification, search, options)
        document = export_texts(sources, options)
    except SlangError as error:
        print(error.report, file=sys.stderr)
        return 1
    conversion = convert(document, XChecks(arguments.x_checks))
    strategy = Strategy(arguments.strategy)
    frontend = YosysFrontend(arguments.yosys_frontend)
    if arguments.equivalence and arguments.output is None:
        parser.error("--equivalence needs -o")
    if arguments.output is None:
        return _check(conversion, strategy, limits, frontend)
    verification_names = frozenset(str(path) for path in verification)
    try:
        result = rewrite(
            sources,
            verification_names,
            syntax_items(document),
            conversion,
            Severity.WARNING if arguments.allow_design_sva else Severity.ERROR,
            strategy,
            arguments.equivalence,
            limits=limits,
            frontend=frontend,
        )
    except RewriteError as error:
        for diagnostic in error.diagnostics:
            print(diagnostic, file=sys.stderr)
        return 1
    output = Path(arguments.output)
    outputs = {f.source: f.name for f in result.files}
    order = _read_order([name for name, _ in sources], verification_names, document)
    if isinstance(order, str):
        print(f"error: {order}", file=sys.stderr)
        return 1
    listed = [MONITORS_FILE]
    listed.extend(outputs.get(name, str(Path(name).resolve())) for name in order)
    reads: list[tuple[Path, Path | None]] = [((output / MONITORS_FILE).resolve(), None)]
    for name in order:
        if name in outputs:
            reads.append(((output / outputs[name]).resolve(), Path(name).resolve().parent))
        else:
            reads.append((Path(name).resolve(), None))
    define_args = [f"-D{name}={value}" for name, value in options.defines]
    resolved_dirs = [directory.resolve() for directory in options.include_dirs]

    def read_script(extra: list[str]) -> str:
        return "".join(
            " ".join(
                [
                    "read_verilog -formal -sv",
                    *define_args,
                    *extra,
                    *(f"-I{d}" for d in ([origin] if origin is not None else []) + resolved_dirs),
                    str(path),
                ]
            )
            + "\n"
            for path, origin in reads
        )

    def read_slang_script() -> str:
        origins = [str(origin) for _, origin in reads if origin is not None]
        directories = list(dict.fromkeys([*origins, *(str(d) for d in resolved_dirs)]))
        words = [
            "read_slang",
            "--best-effort-hierarchy",
            # a monitor declares its sampled-value history registers after the conditions
            # that read them (read_verilog accepts that, Slang does not by default)
            "--allow-use-before-declare",
            "-D",
            "FORMAL",
            *define_args,
            *(f"-I{d}" for d in directories),
            *(word for top in options.top for word in ("--top", top)),
            *(str(path) for path, _ in reads),
        ]
        return "plugin -i slang\n" + " ".join(words) + "\n"

    script = read_slang_script() if frontend is YosysFrontend.SLANG else read_script([])

    def part_defines(monitor: str) -> list[str]:
        """The defines of the ``live`` task of one liveness part: its own, and the fairness one
        (a fairness ``assume`` holds in every run) when the design has such an ``assume``."""
        defines = [f"-D{live_define(monitor)}"]
        return [*defines, f"-D{FAIR_DEFINE}"] if result.fair else defines

    written = [
        (MONITORS_FILE, result.monitors),
        *((f.name, f.text) for f in result.files),
        (READ_SCRIPT, script),
        *(((READ_LIVE_SCRIPT, read_script([f"-D{LIVE_DEFINE}"])),) if result.live else ()),
        *(
            (f"{READ_LIVE_PREFIX}{live_part(monitor)}.ys", read_script(part_defines(monitor)))
            for monitor in result.live_monitors
        ),
        (FILE_LIST, "".join(f"{entry}\n" for entry in listed)),
        *(
            (f"{EQUIVALENCE_DIR}/{monitor}/{file}", text)
            for monitor, files in sorted(result.equivalence.items())
            for file, text in files.items()
        ),
    ]
    inputs = {Path(name).resolve() for name, _ in sources}
    problems = _include_problems(document, outputs, output, {name for name, _ in written})
    problems.extend(
        f"'{text}' contains whitespace, which a Yosys command cannot quote"
        for text in [*define_args, *(str(d) for d in resolved_dirs), *(str(p) for p, _ in reads)]
        if any(c.isspace() for c in text)
    )
    problems += [
        f"writing {output / name} would overwrite an input file"
        for name, _ in written
        if (output / name).resolve() in inputs
    ]
    names = [name for name, _ in written]
    problems.extend(
        f"two output files would be named {name}"
        for name in sorted({n for n in names if names.count(n) > 1})
    )
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    try:
        output.mkdir(parents=True, exist_ok=True)
        for name, text in written:
            (output / name).parent.mkdir(parents=True, exist_ok=True)
            _write(output / name, text)
            print(f"wrote {output / name}")
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(warning)
    for note in result.skipped:
        print(note)
    copies = sum(1 for f in result.files if f.role is FileRole.DESIGN_COPY)
    skipped = f", {len(result.skipped)} skipped X-check(s)" if result.skipped else ""
    print(
        f"{len(result.files) - copies} verification file(s) rewritten, {copies} design "
        f"copy(ies), {len(result.warnings)} warning(s){skipped}"
    )
    return 0


def _read_order(
    names: list[str], verification: frozenset[str], document: Mapping[str, object]
) -> list[str] | str:
    """Inputs in reading order, or the reason none exists.

    A file declaring a package precedes every file using it (``import p::…``, ``p::x``),
    transitively; otherwise design files precede verification files, each in loading
    order. A cycle between files is refused: ``read_verilog`` reads files one by one.
    """

    def entries(key: str) -> list[tuple[str, str]]:
        listed = document.get(key)
        return [
            (str(entry.get("name")), str(entry.get("sva2yosys_inputFile")))
            for entry in (listed if isinstance(listed, list) else [])
            if isinstance(entry, dict)
        ]

    declaring = dict(entries("sva2yosys_packages"))
    before: dict[str, set[str]] = {name: set() for name in names}
    for package, user in entries("sva2yosys_packageUses"):
        source = declaring.get(package)
        if source is not None and source != user and user in before and source in before:
            before[user].add(source)
    rank = {name: (name in verification, index) for index, name in enumerate(names)}
    order: list[str] = []
    pending = set(names)
    while pending:
        ready = [name for name in pending if not before[name] & pending]
        if not ready:
            cycle = ", ".join(sorted(pending))
            return f"package dependencies between these files form a cycle: {cycle}"
        chosen = min(ready, key=rank.__getitem__)
        order.append(chosen)
        pending.remove(chosen)
    return order


def _include_problems(
    document: Mapping[str, object], outputs: dict[str, str], output: Path, written: set[str]
) -> list[str]:
    """Includes of written files that Yosys would resolve differently than Slang did.

    A written file lives in ``DIR``, which Yosys searches first for a relative include;
    a file there (written now, or already present) must be the one Slang included.
    """
    problems: list[str] = []
    includes = document.get("sva2yosys_includes")
    for include in includes if isinstance(includes, list) else []:
        if not isinstance(include, dict) or include.get("sva2yosys_inputFile") not in outputs:
            continue
        path, resolved = include.get("path"), include.get("sva2yosys_resolved")
        if not isinstance(path, str) or not isinstance(resolved, str) or Path(path).is_absolute():
            continue
        candidate = output / path
        if (path in written or candidate.exists()) and (
            candidate.resolve() != Path(resolved).resolve()
        ):
            problems.append(
                f'the include "{path}" of {include["sva2yosys_inputFile"]} would find '
                f"{candidate} instead of {resolved}"
            )
    return problems


def _write(path: Path, text: str) -> None:
    """Write ``text`` (UTF-8, ``\\n`` line ends) through a temporary file, so that ``path``
    is never left half-written. The temporary file is created exclusively (it can never
    be an existing file) and removed on failure."""
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=".sva2yosys-", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _check(
    conversion: Conversion,
    strategy: Strategy,
    limits: ResourceLimits = DEFAULT_LIMITS,
    frontend: YosysFrontend = YosysFrontend.VERILOG,
) -> int:
    accepted = 0
    rejected = len(conversion.rejections)
    lines: list[tuple[SourceRange | None, str]] = []
    covers: list[tuple[SourceRange, str]] = []
    fair = False
    live_parts: list[tuple[SourceRange, str]] = []
    for skip in conversion.skipped:
        lines.append((skip.source, _line("skipped", skip.source, _qualified(skip), skip.reason)))
    for rejection in conversion.rejections:
        lines.append(
            (
                rejection.source,
                _rejected(
                    rejection.source,
                    f"{rejection.scope}.{_name(rejection.name)}",
                    rejection.message,
                ),
            )
        )
    for directive in conversion.directives:
        qualified = f"{directive.scope}.{_name(directive.name)}"
        try:
            machines = compile_monitors(directive, limits=limits)
            problem = frontend_problem(machines, frontend)
            if problem is not None:
                raise CompileError(problem)
            for machine in machines:
                form = choose(machine, strategy) if isinstance(machine, Machine) else Form.DIRECT
                check_emission(machine, form, limits)
        except (CompileError, BudgetError) as error:
            lines.append((directive.source, _rejected(directive.source, qualified, str(error))))
            rejected += 1
            continue
        accepted += 1
        fair = fair or any(is_fairness_assumption(machine) for machine in machines)
        if directive.kind is DirectiveKind.ASSERT:
            live_parts.extend(
                (directive.source, qualified)
                for machine in machines
                if isinstance(machine, ConfigMachine) and machine.liveness
            )
        if directive.kind is DirectiveKind.COVER:
            covers.append((directive.source, qualified))
        lines.extend(
            (warning.source, _line("warning", warning.source, qualified, warning.message))
            for warning in conversion.warnings
            if warning.directive == directive.source
            and (warning.scope, warning.name) == (directive.scope, directive.name)
        )
        forms: list[str] = []
        bits: list[int | None] = []
        slots = 0
        for machine in machines:
            if isinstance(machine, AttemptMachine):
                forms.append("attempt")
                bits.append(attempt_size(machine).registers)
            elif isinstance(machine, ConfigMachine):
                forms.append("configurations")
                bits.append(config_size(machine).temporal_bits)
            else:
                form = choose(machine, strategy)
                forms.append(form.value)
                bits.append(state_bits(machine, form))
                slots += machine_size(machine).slots
        total_bits = None if None in bits else sum(b for b in bits if b is not None)
        live = [m.liveness.value for m in machines if isinstance(m, ConfigMachine) and m.liveness]
        live_text = f"  live={'+'.join(live)}" if live else ""
        lines.append(
            (
                directive.source,
                f"ok        {qualified}  {directive.kind.value}  "
                f"slots={slots}  form={'+'.join(forms)}  bits={total_bits}{live_text}",
            )
        )
        lines.extend(
            (directive.source, _line("warning", directive.source, qualified, message))
            for index, machine in enumerate(machines)
            if (message := never_fails_warning(machine, index, len(machines))) is not None
        )
    if len(live_parts) > 1:  # SBY decides a live task by its first verdict
        advice = "with -o, check this part alone with its sva2yosys_read_live_<part>.ys script"
        message = live_parts_warning(len(live_parts), advice)
        lines.extend(
            (source, _line("warning", source, qualified, message))
            for source, qualified in live_parts
        )
    if fair:  # covers do not see the fairness constraints
        lines.extend(
            (source, _line("warning", source, qualified, COVER_FAIRNESS_WARNING))
            for source, qualified in covers
        )
    for _, line in sorted(lines, key=lambda item: _order(item[0])):
        print(line)
    skipped = len(conversion.skipped)
    total = accepted + skipped + rejected
    skipped_text = f"{skipped} skipped (X-check), " if skipped else ""
    print(f"{total} assertion(s): {accepted} accepted, {skipped_text}{rejected} rejected")
    return 0 if rejected == 0 else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sva2yosys",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Check the concurrent SystemVerilog Assertions of verification files and lower\n"
            "them to plain SystemVerilog monitors for the open-source Yosys/SBY flow.\n"
            "PROPS are the verification files (SVA, supporting code, bind statements);\n"
            "only they are translated. The design is found with -y/-v/-f and never\n"
            "modified."
        ),
        epilog=(
            "examples:\n"
            "  sva2yosys props.sv -y rtl --top top                # check only\n"
            "  sva2yosys props.sv -f rtl.f --top top -o build     # write build/\n"
            "  then in Yosys, or in the [script] of an .sby file:\n"
            "    script build/sva2yosys_read.ys\n"
            "  strong properties (s_eventually, strong(...), not of a weak unbounded\n"
            "  sequence) also write build/sva2yosys_read_live.ys, for the SBY `live` task\n"
            "  (docs/usage.md; with several liveness parts use the per-part scripts\n"
            "  sva2yosys_read_live_<part>.ys, one `live` task each). Strong assume is\n"
            "  accepted with at most one configuration:\n"
            "  its fairness constraint applies in live only; live needs an assertion.\n"
            "  Covers beside fairness assumptions warn that reached results are provisional;\n"
            "  see docs/usage.md for confirmation. Safety prove results are conservative.\n"
            "\n"
            "-y is module lookup (an unknown module NAME is loaded from DIR/NAME.sv);\n"
            '-I is include lookup (`include "x.svh" is searched in DIR). Relative\n'
            "includes are found next to the including file without -I.\n"
            "See docs/usage.md."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version('sva2yosys')}",
        help="print the version of this package and exit",
    )
    parser.add_argument(
        "files", nargs="+", metavar="PROPS", help="verification files (translated with -o)"
    )
    design = parser.add_argument_group("design (never modified)")
    design.add_argument(
        "-y",
        dest="library_dir",
        action="append",
        default=[],
        metavar="DIR",
        help="library directory: an instantiated, bound, imported or --top module, "
        "interface or package that no loaded file declares is loaded from DIR/<name><ext>",
    )
    design.add_argument(
        "--libext",
        action="append",
        default=[],
        metavar="EXT",
        help="extension tried in -y directories, in order (default: .v and .sv)",
    )
    design.add_argument(
        "-v",
        dest="library_file",
        action="append",
        default=[],
        metavar="FILE",
        help="design file, always loaded",
    )
    design.add_argument(
        "-f",
        dest="filelists",
        action="append",
        default=[],
        type=lambda name: (name, False),
        metavar="LIST",
        help="filelist (files, -v, -y, --libext, -I, -D, +incdir+, +define+, nested -f/-F); "
        "relative paths are relative to the current directory",
    )
    design.add_argument(
        "-F",
        dest="filelists",
        action="append",
        type=lambda name: (name, True),
        metavar="LIST",
        help="like -f, with relative paths relative to the filelist's directory",
    )
    common = parser.add_argument_group("preprocessing and elaboration (all files)")
    common.add_argument(
        "-I",
        dest="include",
        action="append",
        default=[],
        metavar="DIR",
        help="include directory for `include (a relative include is first searched next "
        "to the including file)",
    )
    common.add_argument(
        "-D", dest="define", action="append", default=[], metavar="NAME[=VALUE]", help="macro"
    )
    common.add_argument(
        "--top",
        action="append",
        default=[],
        metavar="NAME",
        help="top module; required with -y/-v/-f/-F (without a design: Slang's choice)",
    )
    output = parser.add_argument_group("generation")
    output.add_argument(
        "-o",
        dest="output",
        metavar="DIR",
        help="rewrite the verification files into DIR, with the monitors and a read script",
    )
    output.add_argument(
        "--strategy",
        choices=[strategy.value for strategy in Strategy],
        default=Strategy.DIRECT.value,
        help="monitor form: direct (default; optimisation off, every event reported), auto "
        "(window where it gives less logic, else direct), window or history (forced where it "
        "applies; never for an unbounded antecedent or consequent); no form reports any "
        "event later than direct",
    )
    output.add_argument(
        "--x-checks",
        choices=[policy.value for policy in XChecks],
        default=XChecks.SKIP.value,
        help="$isunknown (the model is two-state, so it is the constant 0): skip (default) "
        "reports a directive that checks nothing afterwards as skipped (X-check), with no "
        "monitor, and warns when a directive keeps other checks; reject rejects every "
        "directive that uses it",
    )
    output.add_argument(
        "--yosys-frontend",
        choices=[frontend.value for frontend in YosysFrontend],
        default=YosysFrontend.VERILOG.value,
        help="the Yosys frontend that reads the result (default verilog: read_verilog -formal "
        "-sv, which does not support bind, so binds are lowered into copies of the design files). "
        "slang: read_slang, which applies bind, so binds are kept and no design file is "
        "copied; a strong unbounded property (liveness) is rejected; -o needs --top",
    )
    output.add_argument(
        "--equivalence",
        action="store_true",
        help="with -o: also write, per optimised monitor, an SBY job proving its fail and "
        "success equal to the direct monitor's within the form's scope (window: up to and "
        "including the first failure) (DIR/equivalence/<monitor>/equivalence.sby)",
    )
    output.add_argument(
        "--allow-design-sva",
        action="store_true",
        help="with -o: SVA left in a design file is a warning instead of an error",
    )
    resources = parser.add_argument_group("compiler resource limits (per monitor)")
    resources.add_argument(
        "--max-expansion",
        type=_nonnegative,
        default=DEFAULT_LIMITS.expansion,
        metavar="N",
        help="written letter estimate and created letters (default: %(default)s)",
    )
    resources.add_argument(
        "--max-work",
        type=_nonnegative,
        default=DEFAULT_LIMITS.work,
        metavar="N",
        help="compiler candidate/operand visits (default: %(default)s)",
    )
    resources.add_argument(
        "--max-state-bits",
        type=_nonnegative,
        default=DEFAULT_LIMITS.state_bits,
        metavar="N",
        help=(
            "bounded slot-machine temporal bits; other machine budgets are separate "
            "(default: %(default)s)"
        ),
    )
    resources.add_argument(
        "--max-emission",
        type=_nonnegative,
        default=DEFAULT_LIMITS.emission,
        metavar="N",
        help="selected-layout reference allowance (default: %(default)s)",
    )
    return parser


def _nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("a resource limit must be nonnegative")
    return number


def _define(text: str) -> tuple[str, str]:
    name, _, value = text.partition("=")
    return name, value or "1"


def _name(name: str) -> str:
    return name or "(unnamed)"


def _line(tag: str, source: SourceRange | None, qualified: str, message: str) -> str:
    where = str(source) if source is not None else "<unknown>"
    return f"{tag:<9} {where}  {qualified}: {message}"


def _rejected(source: SourceRange | None, qualified: str, message: str) -> str:
    return _line("rejected", source, qualified, message)


def _qualified(skip: Skipped) -> str:
    return f"{skip.scope}.{_name(skip.name)}"


def _order(source: SourceRange | None) -> tuple[str, int, int]:
    if source is None:
        return ("", 0, 0)
    return (source.file, source.line, source.column)
