"""Test support: the SymbiYosys examples and tests as a known-answer corpus.

The corpus is ``docs/examples`` and ``tests`` of https://github.com/YosysHQ/sby (ISC), fetched
by ``tests/support/corpora.py`` (entry ``sby``) and located by ``SVA2YOSYS_SBY_EXAMPLES`` (the
checkout root). Each ``.sby`` file describes verification jobs (its tasks) whose expected
result is its ``expect`` option (default: pass). This module classifies the files, rewrites
the sources of those with concurrent SVA through sva2yosys, runs SBY on the original job with
the source reads replaced, and compares SBY's final status with the expected one; the area test
(``tests/functional_tests/external/sby_examples``) and the draft command use it::

    uv run --env-file .env python tests/support/sby_examples.py --draft <out.tsv> [--jobs N]

Contract
--------
* ``parse_config`` is pure. It reads the text printed by ``sby --dumpcfg <file> [<task>]`` (the
  pre-processed job: tags and ``--pycode`` blocks are already expanded): the sections
  ``[options]``, ``[engines]``, ``[script]``, ``[files]`` (one path, or ``<name> <path>``, per
  line) and ``[file <name>]`` (inline file contents) as :class:`Config`. Comment and blank lines
  of ``[options]``, ``[engines]`` and ``[files]`` are dropped, the blank lines of ``[script]`` too
  (its comments stay), and ``[file]`` sections are kept as they are.
* ``expected_statuses`` is pure: the final statuses an ``expect`` option allows (``expect
  pass,fail`` is both), ``{"pass"}`` when there is none.
* ``has_concurrent_sva`` is pure: whether a source text (comments blanked) has a concurrent
  assertion, assumption, cover or restrict (``assert|assume|cover|restrict property``, also
  ``assert (property`` and a macro followed by ``property``, as in ``\\`mode property (...)``).
* ``plan_script`` is pure: from a task's ``[script]`` lines, the :class:`ScriptPlan`: the source
  files read (in order), the macros defined (``read -define``, ``-D``), whether any read is
  ``-formal`` (the macro ``FORMAL`` is then defined), whether the job runs with the SVA-capable
  front end its sources assume (no ``read -noverific``: the macro ``VERIFIC`` is then defined,
  as that front end does), the top (``-top`` of ``prep`` or ``hierarchy``), and the script with
  the source reads replaced by a marker line. Or a skip reason: a proprietary-frontend command
  (``verific``, ``read -verific``), VHDL, a reader option that changes assertions
  (``-assume-asserts``, ``-assert-assumes``, ``-noassert``, ``-noassume``, ``-norestrict``), a
  parameter override after the read (``-chparam``, which the rewrite cannot mirror: the monitors
  are generated for the declared parameters), no Verilog source read, or no top.
* ``classify_file`` decides, without SBY, which files are not compared: ``sby --dumptasks``
  fails (a helper script), no source (``[files]`` and ``[file]`` sections) has concurrent SVA, or
  the file tests a feature of SBY itself (``SBY_OWN_FEATURES``: a matrix of back-end engines and
  solver options, symbolic links); the reason is recorded.
* ``run_task`` is not pure. It runs the check mode of sva2yosys (in process) on the task's
  sources: the files with concurrent SVA are the verification files, the other sources are
  design files (``-v``), the top is the task's, the macros those of the reads. A task without
  any concurrent directive under those macros is ``skipped``. A rejected directive, or an
  error, makes the task ``rejected <fragment>``, with one exception: in a mode other than
  ``cover`` a rejected *labeled cover statement* is blanked out (with the property declaration
  it alone uses and the ``let`` declarations that nothing else uses), since a cover does not
  affect a ``bmc``, ``prove`` or ``live`` verdict; the task is then ``isolated``. An assertion,
  assumption or restrict is never removed. Otherwise it generates with ``-o``,
  writes a job file with the original ``[options]``, ``[engines]`` and other ``[files]``, the
  script with the reads replaced by the generated read commands (the liveness variant for the
  ``live`` mode or the suprove engine) and runs it with SBY (the liveness SBY for those),
  returning SBY's final status.
* Nothing here imports pyslang; ``src/`` never imports this module.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from support.toolchain import Tool

SBY_DIRECTORIES = ("docs/examples", "tests")
"""The directories of the checkout that hold ``.sby`` files."""

SBY_OWN_FEATURES: dict[str, str] = {
    "tests/regression/aiger_options.sby": "SBY back-end option matrix (aiger engines)",
    "tests/regression/btor_options.sby": "SBY back-end option matrix (btor engines and options)",
    "tests/links/symlink.sby": "SBY feature: links and copies of the files section",
}
"""Files that exercise SBY itself (engines, options, file handling) though their sources have
concurrent SVA; each with the reason it is not compared."""

RUN_TIMEOUT = 300.0

_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_SVA = re.compile(
    r"\b(?:assert|assume|cover|restrict)\s+property\b"
    r"|\bassert\s*\(\s*property\b"
    r"|`\w+\s+property\s*\("
)
_HEADER = re.compile(r"^\[([^\]\s]+)(?:\s+(.+?))?\]\s*$")
_PROPRIETARY_COMMANDS = frozenset({"verific"})  # the upstream command name
_ASSERTION_FLAGS = frozenset(
    {"-assume-asserts", "-assert-assumes", "-noassert", "-noassume", "-norestrict"}
)
_TOP = re.compile(r"(?:^|\s)-top\s+(\S+)")
READ_MARKER = "# <sva2yosys-reads>"
"""The line standing in the script for the replaced source reads."""


@dataclass(frozen=True)
class Config:
    """A pre-processed ``.sby`` job (``sby --dumpcfg``)."""

    options: tuple[str, ...] = ()
    engines: tuple[str, ...] = ()
    script: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    """(name in the job, path) per ``[files]`` line."""
    inline: tuple[tuple[str, str], ...] = ()
    """(name, contents) per ``[file <name>]`` section."""

    @property
    def mode(self) -> str:
        """The ``mode`` option (``bmc`` when none)."""
        for line in self.options:
            words = line.split()
            if len(words) == 2 and words[0] == "mode":
                return words[1]
        return "bmc"


def _useful(line: str) -> str | None:
    stripped = line.strip()
    return stripped if stripped and not stripped.startswith("#") else None


def _joined(lines: Sequence[str]) -> str:
    """The lines of an inline file as its contents (one final newline)."""
    return "\n".join(lines).rstrip("\n") + "\n"


def parse_config(text: str) -> Config:
    """Read a pre-processed job (see the module contract)."""
    sections: dict[str, list[str]] = {}
    inline: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        header = _HEADER.match(line)
        if header is not None:
            name, argument = header.group(1), header.group(2)
            if name == "file" and argument:
                current = []
                inline.append((argument, current))
            else:
                current = sections.setdefault(name, [])
            continue
        if current is not None:
            current.append(line)
    cleaned = {
        name: tuple(u for line in lines if (u := _useful(line)) is not None)
        for name, lines in sections.items()
    }
    files: list[tuple[str, str]] = []
    for line in cleaned.get("files", ()):
        words = line.split()
        files.append((words[0], words[-1]) if len(words) > 1 else (Path(words[0]).name, words[0]))
    return Config(
        options=cleaned.get("options", ()),
        engines=cleaned.get("engines", ()),
        script=tuple(line.rstrip() for line in sections.get("script", ()) if line.strip()),
        files=tuple(files),
        inline=tuple((name, _joined(lines)) for name, lines in inline),
    )


def expected_statuses(options: Sequence[str]) -> frozenset[str]:
    """The final statuses the ``expect`` option allows (``{"pass"}`` by default)."""
    for line in options:
        words = line.split(None, 1)
        if len(words) == 2 and words[0] == "expect":
            return frozenset(w.strip().lower() for w in words[1].split(",") if w.strip())
    return frozenset({"pass"})


def has_concurrent_sva(text: str) -> bool:
    """Whether the source has a concurrent assertion, assumption, cover or restrict."""
    return _SVA.search(_COMMENT.sub(" ", text)) is not None


@dataclass(frozen=True)
class ScriptPlan:
    """What a job's script reads, and the script with the reads replaced."""

    sources: tuple[str, ...]
    defines: tuple[str, ...]
    formal: bool
    proprietary_frontend: bool
    """Whether the job runs with the proprietary SVA-capable front end that the upstream
    examples assume (no ``read -noverific``): the upstream macro ``VERIFIC`` is then defined,
    as that front end does. Only the script text is read; that front end is never run."""
    top: str
    script: tuple[str, ...]
    """The script lines; the first source read is a :data:`READ_MARKER` line, the others gone."""


_READ_COMMANDS = frozenset({"read", "read_verilog"})
_SOURCE_SUFFIXES = (".v", ".sv", ".vh", ".svh")


def plan_script(script: Sequence[str]) -> ScriptPlan | str:
    """Plan the replacement of the source reads of ``script``, or the reason it cannot be."""
    sources: list[str] = []
    defines: list[str] = []
    formal = False
    proprietary_frontend = True
    top = ""
    lines: list[str] = []
    marked = False
    for raw in script:
        words = raw.split()
        if not words or words[0].startswith("#"):
            lines.append(raw)
            continue
        command, args = words[0], words[1:]
        if command in _PROPRIETARY_COMMANDS or (command == "read" and "-verific" in args):
            return "proprietary-frontend command"
        if "-chparam" in args:
            return "parameter override after the read (-chparam) is not mirrored in the rewrite"
        if command in ("prep", "hierarchy") and (match := _TOP.search(raw)):
            top = match.group(1)
        if command not in _READ_COMMANDS:
            lines.append(raw)
            continue
        if "-vhdl" in args or "-vhdl2008" in args:
            return "VHDL"
        flagged = sorted(_ASSERTION_FLAGS.intersection(args))
        if flagged:
            return f"reader option {flagged[0]} changes assertions"
        formal = formal or "-formal" in args
        proprietary_frontend = proprietary_frontend and "-noverific" not in args
        files: list[str] = []
        index = 0
        while index < len(args):
            word = args[index]
            if word in ("-define", "-D") and index + 1 < len(args):
                defines.append(args[index + 1])
                index += 2
                continue
            if word.startswith("-D") and len(word) > 2:
                defines.append(word[2:])
            elif not word.startswith("-"):
                files.append(word)
            index += 1
        if files:
            sources.extend(files)
            if not marked:
                lines.append(READ_MARKER)
                marked = True
        elif "-define" not in args and "-D" not in args and "-noverific" not in args:
            lines.append(raw)  # a read without a file or a define
    if not sources:
        return "no Verilog source read (it reads the result of an earlier stage)"
    if not top:
        return "no top (prep -top / hierarchy -top)"
    return ScriptPlan(
        tuple(sources), tuple(defines), formal, proprietary_frontend, top, tuple(lines)
    )


def file_digest(root: Path) -> str:
    """SHA-256 of every file of the corpus directories (name and content, line ends normalised
    to LF), in name order."""
    digest = hashlib.sha256()
    paths: list[Path] = []
    for directory in SBY_DIRECTORIES:
        paths.extend(p for p in (root / directory).rglob("*") if p.is_file())
    for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        if path.is_symlink():
            # Windows checks a git symlink out as a file holding the target path: hash that
            # text on every platform instead of the content behind the link.
            content = path.readlink().as_posix().encode()
        else:
            content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + content + b"\0")
    return digest.hexdigest()


def find_sby_files(root: Path) -> list[str]:
    """The ``.sby`` files of the checkout, as POSIX paths relative to ``root``, sorted."""
    found: list[str] = []
    for directory in SBY_DIRECTORIES:
        found.extend(p.relative_to(root).as_posix() for p in (root / directory).rglob("*.sby"))
    return sorted(found)


# --- running --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """Our result for one task."""

    kind: str
    """``status`` (SBY ran), ``rejected``, ``skipped`` or ``error``."""
    detail: str
    """The SBY status (lower case), the rejection fragment, the skip reason or the error."""
    isolated: str = ""
    """For a ``status``: the fragment of the rejected cover statements that were blanked out
    (see the contract), else empty."""
    failures: tuple[tuple[str, int], ...] = ()
    """For a ``status``: the failed assertions SBY reported, as (name, step)."""


@dataclass(frozen=True)
class TaskResult:
    """One task of one ``.sby`` file with its expected and our result."""

    file: str
    task: str
    """The task name, ``-`` for a file without tasks (or skipped as a whole)."""
    expected: frozenset[str]
    outcome: Outcome

    @property
    def cls(self) -> str:
        """``agrees``, ``differs``, ``rejected``, ``skipped`` or ``error``."""
        match self.outcome.kind:
            case "status":
                if self.outcome.detail not in self.expected:
                    return "differs"
                return "agrees-isolated" if self.outcome.isolated else "agrees"
            case other:
                return other


@dataclass(frozen=True)
class SbyTools:
    """The tools of a run."""

    sby: Tool
    sby_live: Tool | None = None


def _run(tool: Tool, args: list[str], cwd: Path) -> tuple[int, str]:
    from support.toolchain import ToolError, _run_tool

    try:
        done = _run_tool(tool, args, cwd, 120.0, "sby")
    except ToolError as error:
        return 1, str(error)
    return done.returncode, done.stdout


def task_names(tool: Tool, root: Path, sby_file: str) -> list[str] | None:
    """The task names of the file (empty for a file without tasks), ``None`` when ``sby``
    cannot expand it."""
    path = root / sby_file
    code, out = _run(tool, [*tool.command, "--dumptasks", path.name], path.parent)
    if code != 0:
        return None
    return [line.strip() for line in out.splitlines() if line.strip()]


def task_config(tool: Tool, root: Path, sby_file: str, task: str) -> Config | None:
    """The pre-processed job of a task (``task`` empty for a file without tasks)."""
    path = root / sby_file
    args = [*tool.command, "--dumpcfg", path.name, *([task] if task else [])]
    code, out = _run(tool, args, path.parent)
    return parse_config(out) if code == 0 else None


def _cli(arguments: list[str]) -> tuple[int, str, str]:
    from sva2yosys.cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = main(arguments)
        except SystemExit as stop:
            status = stop.code if isinstance(stop.code, int) else 2
    return status, out.getvalue(), err.getvalue()


def _fragment(message: str) -> str:
    from support.survey import normalise_reason

    return normalise_reason(re.sub(r"^.*?: error: ", "", message).strip())


def _first_error(err: str) -> str:
    return next((line for line in err.splitlines() if "error" in line), err.strip())


def _sources(
    root: Path, sby_file: str, config: Config, plan: ScriptPlan, work: Path
) -> dict[str, Path]:
    """The source files of the job by the name the script reads them with."""
    base = (root / sby_file).parent
    mapping = {name: (base / path).resolve() for name, path in config.files}
    source_dir = work / "inline"
    for name, text in config.inline:
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / name).write_text(text, encoding="utf-8", newline="\n")
        mapping[name] = source_dir / name
    return {name: mapping.get(name, base / name) for name in plan.sources}


def classify_file(root: Path, sby_file: str, tool: Tool) -> str | None:
    """The reason a file is not compared at all, or ``None`` when its tasks are."""
    if sby_file in SBY_OWN_FEATURES:
        return SBY_OWN_FEATURES[sby_file]
    tasks = task_names(tool, root, sby_file)
    if tasks is None:
        return "not a job (sby cannot expand it: a helper script)"
    config = task_config(tool, root, sby_file, tasks[0] if tasks else "")
    if config is None:
        return "not a job (sby cannot expand it)"
    base = (root / sby_file).parent
    texts = [text for _, text in config.inline]
    for _, path in config.files:
        candidate = base / path
        if candidate.suffix in (".v", ".sv", ".vh", ".svh") and candidate.is_file():
            texts.append(candidate.read_text(encoding="utf-8", errors="replace"))
    if not any(has_concurrent_sva(text) for text in texts):
        return "no concurrent SVA"
    return None


def run_task(
    root: Path, sby_file: str, task: str, work: Path, tools: SbyTools, tool_for_config: Tool
) -> TaskResult:
    """Run one task (see the module contract)."""
    from support.ebmc_corpus import parse_check
    from support.toolchain import SbyStatus, ToolError, run_sby

    name = task or "-"
    config = task_config(tool_for_config, root, sby_file, task)
    if config is None:
        return TaskResult(sby_file, name, frozenset(), Outcome("error", "sby --dumpcfg failed"))
    expected = expected_statuses(config.options)

    def result(kind: str, detail: str) -> TaskResult:
        return TaskResult(sby_file, name, expected, Outcome(kind, detail))

    plan = plan_script(config.script)
    if isinstance(plan, str):
        return result("skipped", plan)
    work.mkdir(parents=True, exist_ok=True)
    sources = _sources(root, sby_file, config, plan, work)
    missing = [n for n, p in sources.items() if not p.is_file()]
    if missing:
        return result("skipped", f"source not in the checkout: {missing[0]}")
    verification = [p for p in sources.values() if has_concurrent_sva(_read(p))]
    if not verification:
        return result("skipped", "no concurrent SVA in the sources read")
    design = [p for p in sources.values() if p not in verification]
    arguments = [str(p) for p in verification]
    for path in design:
        arguments += ["-v", str(path)]
    defines = list(plan.defines)
    if plan.formal:
        defines.append("FORMAL")
    if plan.proprietary_frontend:
        defines.append("VERIFIC")
    for define in defines:
        arguments += ["-D", define]
    arguments += ["--top", plan.top]
    status, out, err = _cli(arguments)
    rejections = parse_rejections(out)
    directives = parse_check(out)
    if status not in (0, 1) or (status == 1 and not directives):
        return result("rejected", _fragment(_first_error(err)) or f"exit status {status}")
    isolated = ""
    if rejections:
        reduced = _isolate(rejections, verification, arguments, config.mode, work)
        if reduced is None:
            return result("rejected", _fragment(rejections[0].message))
        arguments, isolated = reduced, _fragment(rejections[0].message)
        status, out, err = _cli(arguments)
        directives = parse_check(out)
        if status != 0 or any(d.status == "rejected" for d in directives):
            return result("rejected", _fragment(rejections[0].message))
    if not directives:
        return result("skipped", "no concurrent SVA under the job's macros")
    if not any(d.status == "ok" for d in directives):
        return result("skipped", "no accepted directive")
    build = work / "build"
    status, out, err = _cli([*arguments, "-o", str(build)])
    if status != 0:
        return result("rejected", f"on generation: {_fragment(_first_error(err))}")
    live = config.mode == "live" or any("suprove" in line for line in config.engines)
    read_script = build / ("sva2yosys_read_live.ys" if live else "sva2yosys_read.ys")
    if not read_script.is_file():
        read_script = build / "sva2yosys_read.ys"
    run_tool = tools.sby_live if live else tools.sby
    if run_tool is None:
        return result("error", "no sby-live tool")
    reads = [
        _relative(line, build) for line in read_script.read_text(encoding="utf-8").splitlines()
    ]
    kept = [n for n, _ in config.files if n not in plan.sources]
    job = _job_text(config, plan, reads, build, root, sby_file, kept)
    job_file = work / "run.sby"
    job_file.write_text(job, encoding="utf-8", newline="\n")
    try:
        run = run_sby(run_tool, job_file, "run", work / "work", timeout=RUN_TIMEOUT)
    except ToolError as error:
        return result("error", str(error).splitlines()[0][:100])
    if run.status in (SbyStatus.ERROR,):
        tail = [line for line in run.log.splitlines() if "ERROR" in line][-1:]
        return result("error", tail[0].strip()[:100] if tail else "ERROR")
    return TaskResult(
        sby_file,
        name,
        expected,
        Outcome(
            "status",
            run.status.value.lower(),
            isolated,
            tuple((hit.name, hit.step or 0) for hit in run.failed_assertions),
        ),
    )


@dataclass(frozen=True)
class Rejection:
    """A rejected directive of the check mode's report."""

    file: str
    line: int
    column: int
    label: str
    """The directive's hierarchical label (``top.name``)."""
    message: str


_REJECTED = re.compile(r"^rejected\s+(\S.*?):(\d+):(\d+)\s+(\S+): (.*)$")


def parse_rejections(stdout: str) -> tuple[Rejection, ...]:
    """The rejected directives of the check mode's output, with their locations."""
    found: list[Rejection] = []
    for line in stdout.splitlines():
        match = _REJECTED.match(line)
        if match is not None:
            found.append(
                Rejection(
                    match.group(1),
                    int(match.group(2)),
                    int(match.group(3)),
                    match.group(4),
                    match.group(5),
                )
            )
    return tuple(found)


def blank_labeled_cover(source: str, label: str) -> str | None:
    """``source`` with the statement ``<label>: cover property (...);`` blanked out (line ends
    kept), or ``None`` when ``label`` does not name exactly one cover statement (an assertion,
    assumption or restrict is never removed: that would change the verdict)."""
    clean = re.sub(
        r"//[^\n]*|/\*.*?\*/",
        lambda m: re.sub(r"[^\n]", " ", m.group(0)),
        source,
        flags=re.DOTALL,
    )
    pattern = re.compile(
        r"(?<![\w$])"
        + re.escape(label)
        + r"\s*:\s*(assert|assume|cover|restrict)\s+property\s*(?=\()"
    )
    matches = list(pattern.finditer(clean))
    if len(matches) != 1 or matches[0].group(1) != "cover":
        return None
    depth = 0
    for index in range(matches[0].end(), len(clean)):
        if clean[index] == "(":
            depth += 1
        elif clean[index] == ")":
            depth -= 1
            if depth == 0:
                end = clean.find(";", index)
                if end < 0 or clean[index + 1 : end].strip():
                    return None
                spans = [(matches[0].start(), end + 1)]
                argument = clean[matches[0].end() : index + 1].strip("() \n\r\t")
                if re.fullmatch(r"\w+", argument):
                    declaration = _declaration_spans(clean, argument)
                    spans += declaration
                    if declaration and _references(clean, spans, argument):
                        return None  # the declaration is used elsewhere: it must stay
                blanked = _blank_spans(source, clean, spans)
                # the removed statement becomes a null statement: a procedure it was the body of
                # (`initial c: cover property (...);`) keeps an empty body instead of taking
                # the next statement
                return blanked[:end] + ";" + blanked[end + 1 :]
    return None


def _references(clean: str, spans: list[tuple[int, int]], name: str) -> bool:
    """Whether ``name`` still occurs in ``clean`` outside the spans."""
    rest = list(clean)
    for begin, end in spans:
        rest[begin:end] = [" "] * (end - begin)
    return re.search(r"\b" + re.escape(name) + r"\b", "".join(rest)) is not None


def _declaration_spans(clean: str, name: str) -> list[tuple[int, int]]:
    """The span of ``property <name> ... endproperty`` (the declaration the blanked statement
    used, which a plain Yosys cannot read), if any."""
    found = re.search(
        r"\bproperty\s+" + re.escape(name) + r"\b.*?\bendproperty\b", clean, re.DOTALL
    )
    return [(found.start(), found.end())] if found else []


def _blank_spans(source: str, clean: str, spans: list[tuple[int, int]]) -> str:
    """``source`` with the spans, and the ``let`` declarations that only the spans used,
    replaced by spaces (line ends kept)."""
    text = list(source)
    gone = list(spans)
    remaining = list(clean)
    for begin, end in spans:
        remaining[begin:end] = [" "] * (end - begin)
    rest = "".join(remaining)
    for let in re.finditer(r"\blet\s+(\w+)\b[^;]*;", rest):
        name = r"\b" + re.escape(let.group(1)) + r"\b"
        if len(re.findall(name, rest)) == 1 and len(re.findall(name, clean)) > 1:
            gone.append((let.start(), let.end()))
    for begin, end in gone:
        text[begin:end] = [c if c == "\n" else " " for c in source[begin:end]]
    return "".join(text)


def _isolate(
    rejections: Sequence[Rejection],
    verification: Sequence[Path],
    arguments: list[str],
    mode: str,
    work: Path,
) -> list[str] | None:
    """The arguments with the rejected labeled ``cover`` statements blanked out of copies of
    their files, or ``None`` when that is not allowed: in ``cover`` mode a cover is the verdict,
    and an assertion, assumption or restrict is never removed."""
    if mode == "cover":
        return None
    known = {str(Path(p)): p for p in verification}
    texts: dict[str, str] = {}
    for rejection in rejections:
        file = str(Path(rejection.file))
        if file not in known:
            return None
        reduced = blank_labeled_cover(
            texts.get(file) or _read(known[file]), rejection.label.rsplit(".", 1)[-1]
        )
        if reduced is None:
            return None
        texts[file] = reduced
    replaced = list(arguments)
    for file, text in texts.items():
        target = work / "isolated" / known[file].name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        replaced[replaced.index(str(known[file]))] = str(target)
    return replaced


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _relative(line: str, build: Path) -> str:
    """A generated read command naming the generated files by their base names (SBY copies the
    files of the job into its ``src`` directory, where the script runs)."""
    return line.replace(str(build) + "\\", "").replace(str(build) + "/", "")


def _job_text(
    config: Config,
    plan: ScriptPlan,
    reads: list[str],
    build: Path,
    root: Path,
    sby_file: str,
    kept: list[str],
) -> str:
    """The job file: the original sections, the reads replaced, the generated files added."""
    script: list[str] = []
    for line in plan.script:
        script.extend(reads if line == READ_MARKER else [line])
    base = (root / sby_file).parent
    lines = ["[tasks]", "run", "", "[options]", *config.options, "", "[engines]", *config.engines]
    lines += ["", "[script]", *script, "", "[files]"]
    lines += [f"build/{p.name}" for p in sorted(build.iterdir()) if p.suffix in (".sv", ".v")]
    lines += [str((base / path).resolve()) for name, path in config.files if name in kept]
    for name, text in config.inline:
        if name in kept:
            lines += ["", f"[file {name}]", text.rstrip("\n")]
    return "\n".join(lines) + "\n"


# --- the draft ------------------------------------------------------------------------------

TSV_HEADER = ("file", "task", "expected", "ours", "class", "detail")


def classify_all(root: Path, tool: Tool) -> list[tuple[str, list[str], str | None]]:
    """For each ``.sby`` file: its tasks and the reason it is not compared (or ``None``)."""
    rows: list[tuple[str, list[str], str | None]] = []
    for sby_file in find_sby_files(root):
        reason = classify_file(root, sby_file, tool)
        tasks = task_names(tool, root, sby_file) if reason is None else []
        rows.append((sby_file, tasks or [""], reason))
    return rows


def _row(result: TaskResult) -> tuple[str, ...]:
    return (
        result.file,
        result.task,
        ",".join(sorted(result.expected)),
        result.outcome.kind,
        result.cls,
        result.outcome.detail,
    )


def _init_worker(tests_dir: str) -> None:
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)


def _draft_worker(args: tuple[str, str, str, str, str | None, str]) -> tuple[str, ...]:
    from support.toolchain import Tool

    root, sby_file, task, sby_command, live_command, work = args
    sby = Tool("SVA2YOSYS_SBY", (sby_command,))
    live = Tool("SVA2YOSYS_SBY_LIVE", tuple(live_command.split())) if live_command else None
    return _row(run_task(Path(root), sby_file, task, Path(work), SbyTools(sby, live), sby))


def draft(root: Path, sby: Tool, live: Tool | None, out: Path, work: Path, jobs: int) -> None:
    """Write the draft TSV: one row per (file, task)."""
    rows: list[tuple[str, ...]] = []
    todo: list[tuple[str, str, str, str, str | None, str]] = []
    for index, (sby_file, tasks, reason) in enumerate(classify_all(root, sby)):
        if reason is not None:
            rows.append((sby_file, "-", "", "skipped", "skipped", reason))
            continue
        for task in tasks:
            todo.append(
                (
                    str(root),
                    sby_file,
                    task,
                    sby.command[0],
                    " ".join(live.command) if live else None,
                    str(work / f"{index}_{task or 'job'}"),
                )
            )
    started = time.time()
    tests_dir = str(Path(__file__).resolve().parents[1])
    with ProcessPoolExecutor(
        max_workers=jobs, initializer=_init_worker, initargs=(tests_dir,)
    ) as pool:
        rows.extend(pool.map(_draft_worker, todo))
    rows.sort()
    out.write_text(
        "\n".join("\t".join(r) for r in (TSV_HEADER, *rows)) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"{len(rows)} rows in {time.time() - started:.0f} s -> {out}")


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point (the draft)."""
    import os

    parser = argparse.ArgumentParser(description="Draft classification of the SBY examples.")
    parser.add_argument("--draft", type=Path, required=True, help="output TSV")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--work", type=Path, default=Path(tempfile.gettempdir()) / "sby-draft")
    args = parser.parse_args(argv)
    tests_dir = str(Path(__file__).resolve().parents[1])
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from support.toolchain import (
        ENV_SBY,
        ENV_SBY_EXAMPLES,
        ENV_SBY_LIVE,
        directory_from_env,
        tool_from_env,
    )

    root = directory_from_env(ENV_SBY_EXAMPLES, os.environ, Path.cwd())
    sby = tool_from_env(ENV_SBY, os.environ)
    if root is None or sby is None:
        print(f"{ENV_SBY_EXAMPLES} and {ENV_SBY} must be set", file=sys.stderr)
        return 2
    live = tool_from_env(ENV_SBY_LIVE, os.environ, command=True)
    if args.work.exists():
        shutil.rmtree(args.work)
    draft(root, sby, live, args.draft, args.work, args.jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
