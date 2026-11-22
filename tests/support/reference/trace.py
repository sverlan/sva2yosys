"""Finite traces of sampled signal values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class TraceError(ValueError):
    """A value needed by the evaluation is not in the trace."""


@dataclass(frozen=True)
class Trace:
    """Sampled values at samples ``0 … len(trace) - 1``.

    Attributes:
        samples: One mapping per sample from signal path to value. A value is taken
            modulo ``2**width`` of the signal's type (negative values are two's
            complement).
    """

    samples: tuple[Mapping[str, int], ...]

    def __len__(self) -> int:
        return len(self.samples)

    def value(self, path: str, sample: int) -> int:
        """The raw value of ``path`` at ``sample``.

        Raises:
            TraceError: The sample or the signal is not in the trace.
        """
        if not 0 <= sample < len(self.samples):
            raise TraceError(f"sample {sample} is outside the trace (length {len(self.samples)})")
        row = self.samples[sample]
        if path not in row:
            raise TraceError(f"no value for {path} at sample {sample}")
        return row[path]
