"""Canonical SVA IR: backend-independent semantics of concurrent assertions.

Nodes are frozen dataclasses with tuple children; dispatch on them with ``match``.
The IR describes meaning only: no registers, slots, histories or generated names.
"""
