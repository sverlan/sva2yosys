"""Source positions carried by IR nodes for diagnostics and generated comments."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceRange:
    """A half-open source range: lines and columns are 1-based, the end is exclusive."""

    file: str
    line: int
    column: int
    end_line: int
    end_column: int

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.column}"
