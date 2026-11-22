"""Slang adapter: the only package that imports ``pyslang``.

It elaborates SystemVerilog with pyslang and exports the concurrent assertions as a
subset of Slang's native ``--ast-json`` format (see ``export.py``). Everything
outside this package consumes that JSON (later: typed IR), never pyslang objects.
"""
