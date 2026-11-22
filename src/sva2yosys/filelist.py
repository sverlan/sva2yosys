"""Filelists (``-f`` / ``-F``), the usual way RTL projects list their sources.

A filelist is text with whitespace-separated entries. Comments are recognised only at
the start of an entry: an entry that is ``//`` or starts with ``#`` comments out the
rest of the line, and an entry starting with ``/*`` opens a block comment that ends after
the next ``*/``. Inside an entry these characters are ordinary (``rtl/foo#v2.sv`` is a
file name). An entry starting with ``//`` followed by more text is an error: it could be
a comment without a space or a UNC path. Entries:

- ``FILE``: a design file (always loaded, like ``-v``);
- ``-v FILE``: a design file;
- ``-y DIR``: a library directory;
- ``--libext EXT`` or ``+libext+EXT[+EXT…]``: library file extensions;
- ``-I DIR``, ``-IDIR`` or ``+incdir+DIR[+DIR…]``: include directories;
- ``-D NAME[=VALUE]``, ``-DNAME[=VALUE]`` or ``+define+NAME[=VALUE][+…]``: macros
  (value ``1`` when omitted);
- ``-f FILE`` / ``-F FILE``: a nested filelist.

Relative paths in a list read with ``-f`` are relative to the current directory; in a
list read with ``-F``, to the list's own directory (as in Slang and simulators). Any
other entry starting with ``-`` or ``+`` is an error: an option is never ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class FileListError(ValueError):
    """A filelist cannot be read, contains an unknown option, or includes itself."""


@dataclass(frozen=True)
class FileList:
    """What one filelist (with its nested lists) contributes, in order of appearance."""

    files: tuple[Path, ...] = ()
    library_dirs: tuple[Path, ...] = ()
    library_extensions: tuple[str, ...] = ()
    include_dirs: tuple[Path, ...] = ()
    defines: tuple[tuple[str, str], ...] = ()


@dataclass
class _Builder:
    files: list[Path] = field(default_factory=list)
    library_dirs: list[Path] = field(default_factory=list)
    library_extensions: list[str] = field(default_factory=list)
    include_dirs: list[Path] = field(default_factory=list)
    defines: list[tuple[str, str]] = field(default_factory=list)


def read_filelist(path: Path, *, relative_to_list: bool) -> FileList:
    """Read the filelist ``path`` (``relative_to_list``: ``-F``, else ``-f``).

    Raises:
        FileListError: See the class.
    """
    builder = _Builder()
    _read(path, relative_to_list, builder, ())
    return FileList(
        tuple(builder.files),
        tuple(builder.library_dirs),
        tuple(builder.library_extensions),
        tuple(builder.include_dirs),
        tuple(builder.defines),
    )


def _tokens(path: Path, text: str) -> list[str]:
    tokens: list[str] = []
    in_block = False
    for line in text.splitlines():
        for token in line.split():
            if in_block:
                in_block = not token.endswith("*/")
                continue
            if token.startswith("/*"):
                in_block = not (len(token) >= 4 and token.endswith("*/"))
                continue
            if token == "//" or token.startswith("#"):
                break
            if token.startswith("//"):
                raise FileListError(
                    f"{path}: '{token}' is ambiguous (a comment needs a space after //; "
                    "UNC paths are not supported)"
                )
            tokens.append(token)
    if in_block:
        raise FileListError(f"{path}: unterminated /* comment")
    return tokens


def _define(text: str) -> tuple[str, str]:
    name, _, value = text.partition("=")
    return name, value or "1"


def _read(path: Path, relative: bool, out: _Builder, stack: tuple[Path, ...]) -> None:
    resolved = path.resolve()
    if resolved in stack:
        raise FileListError(f"{path}: the filelist includes itself")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise FileListError(f"cannot read filelist {path}: {error}") from error
    base = path.parent if relative else Path()
    tokens = _tokens(path, text)

    def at(where: str) -> Path:
        candidate = Path(where)
        return candidate if candidate.is_absolute() else base / candidate

    index = 0

    def argument(option: str) -> str:
        nonlocal index
        index += 1
        if index >= len(tokens):
            raise FileListError(f"{path}: {option} needs an argument")
        return tokens[index]

    while index < len(tokens):
        token = tokens[index]
        if token in ("-f", "-F"):
            _read(at(argument(token)), token == "-F", out, (*stack, resolved))
        elif token == "-v":
            out.files.append(at(argument(token)))
        elif token == "-y":
            out.library_dirs.append(at(argument(token)))
        elif token == "--libext":
            out.library_extensions.append(argument(token))
        elif token == "-I":
            out.include_dirs.append(at(argument(token)))
        elif token == "-D":
            out.defines.append(_define(argument(token)))
        elif token.startswith("-I"):
            out.include_dirs.append(at(token[2:]))
        elif token.startswith("-D"):
            out.defines.append(_define(token[2:]))
        elif token.startswith("+incdir+"):
            out.include_dirs.extend(at(d) for d in token[len("+incdir+") :].split("+") if d)
        elif token.startswith("+define+"):
            out.defines.extend(_define(d) for d in token[len("+define+") :].split("+") if d)
        elif token.startswith("+libext+"):
            out.library_extensions.extend(e for e in token[len("+libext+") :].split("+") if e)
        elif token.startswith(("-", "+")):
            raise FileListError(f"{path}: unsupported filelist option {token}")
        else:
            out.files.append(at(token))
        index += 1
