"""Reference evaluator: a direct implementation of the SVA match semantics.

It evaluates IR properties on finite traces of sampled values, following IEEE 1800
Annex F (tight satisfaction) and the rules of ``docs/semantics.md``. It shares only the
IR with the temporal compiler and is the oracle for the semantic tests.
"""
