"""Compile SystemVerilog sources with pyslang and report diagnostics."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import pyslang
import pyslang.driver


class Severity(Enum):
    """Severity of a Slang diagnostic."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class SourceLocation:
    """A position in a source file (1-based line and column)."""

    file: str
    line: int
    column: int


@dataclass(frozen=True)
class SlangDiagnostic:
    """One diagnostic reported by Slang.

    Attributes:
        severity: Error or warning (notes are not reported separately).
        code: Slang's diagnostic code name, e.g. ``UndeclaredIdentifier``.
        message: The formatted message, without location.
        location: Where it was reported, or ``None`` when Slang gives no location.
    """

    severity: Severity
    code: str
    message: str
    location: SourceLocation | None


class SlangError(Exception):
    """The sources could not be compiled (Slang reported at least one error).

    Attributes:
        diagnostics: All errors and warnings, in Slang's order.
        report: Slang's formatted report (with source excerpts), for display.
    """

    def __init__(self, diagnostics: tuple[SlangDiagnostic, ...], report: str) -> None:
        super().__init__(report)
        self.diagnostics = diagnostics
        self.report = report


@dataclass(frozen=True)
class SlangOptions:
    """Options for compiling a design.

    Attributes:
        top: Top-level module names; empty means Slang's automatic choice.
        defines: Preprocessor macros as ``(name, value)`` pairs.
        include_dirs: Directories searched by ``` `include ```.
    """

    top: tuple[str, ...] = ()
    defines: tuple[tuple[str, str], ...] = ()
    include_dirs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class LibrarySearch:
    """Where :func:`load_design` finds the design (Slang's ``-y``, ``--libext``, ``-v``).

    Attributes:
        directories: Searched for a module, interface or package that the sources use
            but do not declare, as ``<directory>/<name><extension>``; the search repeats
            for what the found files use.
        extensions: File extensions tried in ``directories`` in addition to ``.v`` and
            ``.sv`` (Slang always tries those).
        files: Files always loaded as library units.
    """

    directories: tuple[Path, ...] = ()
    extensions: tuple[str, ...] = ()
    files: tuple[Path, ...] = ()


@dataclass(frozen=True)
class Compiled:
    """An elaborated design (pyslang objects; only for use inside this package).

    ``texts`` maps each input name to the text given to Slang.
    """

    compilation: pyslang.ast.Compilation
    source_manager: pyslang.SourceManager
    warnings: tuple[SlangDiagnostic, ...]
    texts: dict[str, str] = field(default_factory=dict)


def compile_design(sources: Sequence[Path], options: SlangOptions) -> Compiled:
    """Parse and elaborate ``sources`` as one design.

    Each source is its own compilation unit; all share one source manager and the
    preprocessor options (defines, include directories). File names in locations
    are the paths as given.

    Args:
        sources: SystemVerilog files.
        options: Compilation options.

    Returns:
        The elaborated design and its warnings.

    Raises:
        SlangError: Slang reported an error (parse or semantic), or a source file
            could not be read.
    """
    return compile_texts(read_sources(sources), options)


def read_sources(sources: Sequence[Path]) -> list[tuple[str, str]]:
    """Read source files as ``(name, text)`` pairs, ``name`` being the path as given.

    Raises:
        SlangError: A file could not be read (code ``CouldNotOpenFile``).
    """
    texts: list[tuple[str, str]] = []
    for source in sources:
        try:
            texts.append((str(source), source.read_text(encoding="utf-8", errors="replace")))
        except OSError as error:
            diagnostic = SlangDiagnostic(
                Severity.ERROR, "CouldNotOpenFile", f"cannot read {source}: {error}", None
            )
            raise SlangError((diagnostic,), f"error: cannot read {source}: {error}") from error
    return texts


def load_design(
    sources: Sequence[Path], search: LibrarySearch, options: SlangOptions
) -> list[tuple[str, str]]:
    """Find the design files that ``sources`` need, and read everything.

    Slang's source loader parses ``sources`` and ``search.files`` and looks up, in
    ``search.directories``, every module, interface or package that the loaded files
    instantiate or import but do not declare, repeatedly. It does not look up the
    targets of ``bind`` nor the ``top`` modules; those are looked up here the same way
    (``<directory>/<name><extension>``, the first directory that has a candidate; a
    ``top`` module whenever no design file declares it, even if a verification file
    does), and the loader runs again until nothing new is found. Only needed files are
    loaded;
    they are then compiled like any source (:func:`compile_texts`), so a module found
    this way can be a ``top``.

    Returns:
        ``(name, text)`` of ``sources`` (in the given order), then of the files found
        (``search.files`` and the directory hits, in loading order).

    Raises:
        SlangError: A file could not be read, the loader reported an error, two loaded
            files declare the same module, interface, program or package, or a name
            is ambiguous: one directory holds two candidates for it (e.g. ``leaf.v`` and
            ``leaf.sv``; Slang's choice between them is not specified).
    """
    extensions = tuple(dict.fromkeys((".v", ".sv", *search.extensions)))
    primary = [str(source) for source in sources]
    extra: list[Path] = []
    while True:
        source_manager = pyslang.SourceManager()
        source_manager.setDisableProximatePaths(True)
        loader = pyslang.driver.SourceLoader(source_manager)
        for source in sources:
            loader.addFiles(str(source))
        for library_file in (*search.files, *extra):
            loader.addLibraryFiles("", str(library_file))
        for directory in search.directories:
            loader.addSearchDirectories(str(directory))
        for extension in extensions:
            loader.addSearchExtension(extension)
        trees = loader.loadAndParseSources(_bag(options))
        if loader.errors:
            message = "; ".join(str(error) for error in loader.errors)
            diagnostic = SlangDiagnostic(Severity.ERROR, "SourceLoader", message, None)
            raise SlangError((diagnostic,), f"error: {message}")
        declared, wanted = _declared_and_bound(trees)
        # A --top module is design: it is looked up unless a design file declares it, even
        # when a verification file declares the same name (Slang then reports the
        # duplicate instead of silently checking the verification module).
        design_declared, _ = _declared_and_bound([t for t in trees if t.isLibraryUnit])
        missing = (wanted - declared) | (set(options.top) - design_declared)
        explicit = {library_file.resolve() for library_file in search.files}
        new = [
            found
            for name in sorted(missing)
            if (found := _search(name, search.directories, extensions)) is not None
            and found not in extra
            and found.resolve() not in explicit
        ]
        if not new:
            break
        extra.extend(new)
    found_names: list[str] = []
    for tree in trees:
        if tree.isLibraryUnit:
            name = source_manager.getFileName(tree.root.sourceRange.start)
            if name not in found_names and name not in primary:
                found_names.append(name)
    _no_duplicates(trees, source_manager)
    for name in found_names:
        path = Path(name).resolve()
        if path in explicit or path.parent not in {d.resolve() for d in search.directories}:
            continue
        _unambiguous(path.parent, path.stem, extensions)
    return read_sources([Path(name) for name in [*primary, *found_names]])


def _no_duplicates(
    trees: Sequence[pyslang.syntax.SyntaxTree], source_manager: pyslang.SourceManager
) -> None:
    """Refuse a module, interface, program or package declared in two loaded files:
    Slang then silently uses one of them (e.g. a verification module named like the
    design top would replace or be replaced by the design)."""
    where: dict[str, list[str]] = {}
    for tree in trees:
        name = source_manager.getFileName(tree.root.sourceRange.start)
        declared, _ = _declared_and_bound([tree])
        for declaration in declared:
            files = where.setdefault(declaration, [])
            if name not in files:
                files.append(name)
    clashes = {name: files for name, files in where.items() if len(files) > 1}
    if clashes:
        message = "; ".join(
            f"'{name}' is declared in several files: {', '.join(files)}"
            for name, files in sorted(clashes.items())
        )
        diagnostic = SlangDiagnostic(Severity.ERROR, "DuplicateDeclaration", message, None)
        raise SlangError((diagnostic,), f"error: {message}")


def _declared_and_bound(
    trees: Sequence[pyslang.syntax.SyntaxTree],
) -> tuple[set[str], set[str]]:
    """Names of the declared modules, interfaces, programs and packages; names of the
    modules that binds target (the first component of an instance path)."""
    declared: set[str] = set()
    bound: set[str] = set()

    def visit(node: pyslang.syntax.SyntaxNode) -> None:
        if isinstance(node, pyslang.syntax.ModuleDeclarationSyntax):
            declared.add(node.header.name.valueText)
        elif isinstance(node, pyslang.syntax.BindDirectiveSyntax):
            bound.add(str(node.target).strip().split(".")[0].split("[")[0].strip())

    for tree in trees:
        tree.root.visit(visit)
    return declared, bound


def _search(name: str, directories: Sequence[Path], extensions: Sequence[str]) -> Path | None:
    for directory in directories:
        found = _unambiguous(directory, name, extensions)
        if found is not None:
            return found
    return None


def _unambiguous(directory: Path, name: str, extensions: Sequence[str]) -> Path | None:
    candidates = [
        directory / f"{name}{e}" for e in extensions if (directory / f"{name}{e}").is_file()
    ]
    if len(candidates) > 1:
        listed = ", ".join(str(c) for c in candidates)
        message = f"'{name}' is ambiguous in library directory {directory}: {listed}"
        diagnostic = SlangDiagnostic(Severity.ERROR, "AmbiguousLibraryFile", message, None)
        raise SlangError((diagnostic,), f"error: {message}")
    return candidates[0] if candidates else None


def compile_texts(sources: Sequence[tuple[str, str]], options: SlangOptions) -> Compiled:
    """Like :func:`compile_design` for in-memory sources.

    Args:
        sources: ``(name, text)`` pairs; ``name`` is used as the file name in
            locations and diagnostics.
        options: Compilation options.

    Returns:
        The elaborated design and its warnings.

    Raises:
        SlangError: Slang reported an error.
    """
    source_manager = pyslang.SourceManager()
    source_manager.setDisableProximatePaths(True)  # report file names as given
    bag = _bag(options)
    # Slang reports a buffer as dirname(path) joined with its name, and resolves relative
    # includes from path: pass the base name as name and the full path as path.
    trees = [
        pyslang.syntax.SyntaxTree.fromText(text, source_manager, Path(name).name, name, bag)
        for name, text in sources
    ]
    compiled = _elaborate(trees, source_manager, bag)
    return dataclasses.replace(compiled, texts=dict(sources))


def _bag(options: SlangOptions) -> pyslang.Bag:
    preprocessor = pyslang.parsing.PreprocessorOptions()
    preprocessor.predefines = [f"{name}={value}" for name, value in options.defines]
    preprocessor.additionalIncludePaths = [str(path) for path in options.include_dirs]
    compilation = pyslang.ast.CompilationOptions()
    if options.top:
        compilation.topModules = set(options.top)
    return pyslang.Bag([preprocessor, compilation])


def _elaborate(
    trees: Sequence[pyslang.syntax.SyntaxTree],
    source_manager: pyslang.SourceManager,
    bag: pyslang.Bag,
) -> Compiled:
    compilation = pyslang.ast.Compilation(bag)
    for tree in trees:
        compilation.addSyntaxTree(tree)
    engine = pyslang.DiagnosticEngine(source_manager)
    reported: list[pyslang.Diagnostic] = []
    converted: list[SlangDiagnostic] = []
    for diagnostic in compilation.getAllDiagnostics():
        severity = engine.getSeverity(diagnostic.code, diagnostic.location)
        if severity == pyslang.DiagnosticSeverity.Ignored:
            continue
        reported.append(diagnostic)
        converted.append(_convert(diagnostic, engine, source_manager))
    if any(d.severity is Severity.ERROR for d in converted):
        report = pyslang.DiagnosticEngine.reportAll(source_manager, reported)
        raise SlangError(tuple(converted), report)
    return Compiled(compilation, source_manager, tuple(converted))


def _convert(
    diagnostic: pyslang.Diagnostic,
    engine: pyslang.DiagnosticEngine,
    source_manager: pyslang.SourceManager,
) -> SlangDiagnostic:
    code = str(diagnostic.code)
    match = _CODE.fullmatch(code)
    severity = Severity.ERROR if diagnostic.isError() else Severity.WARNING
    location: SourceLocation | None = None
    if diagnostic.location.buffer.id != 0:
        loc = source_manager.getFullyExpandedLoc(diagnostic.location)
        location = SourceLocation(
            source_manager.getFileName(loc),
            source_manager.getLineNumber(loc),
            source_manager.getColumnNumber(loc),
        )
    return SlangDiagnostic(
        severity,
        match.group(1) if match else code,
        engine.formatMessage(diagnostic),
        location,
    )


_CODE = re.compile(r"DiagCode\((\w+)\)")
