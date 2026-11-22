# SVA flow comparison evidence

This area checks the bounded public claims in `docs/tool-comparison.md` and the
comparison matrix at the top of the same page (`supported-sva.md` keeps only a pointer to it). It changes no compiler semantics.

1. **Six native probes.** Checked-in SystemVerilog sources isolate a procedural
   Boolean tautology, a free-input Boolean assertion, an explicit property clock,
   implication, a fixed delay and procedural Boolean `s_eventually`. The inputs
   must elaborate with Slang, and both sva2yosys CLI modes must give the catalog's
   independently specified counts/status. Displayed snippets are synchronized
   with these same sources through the existing documentation helper; each native
   observation sentence matches its case's admission/diagnostic and control verdict.
2. **Pinned observations.** Native comparisons require the configured `yosys_tool`,
   its exact recorded version (one for each platform: the same release is built with
   different compilers on Windows and Linux) and the SHA-256 of the sources/catalog in sorted
   name order with CRLF normalized. Changed inputs/builds fail for reclassification.
   Native acceptance is followed by hierarchy/proc and an expected assertion/live
   cell count; rejection requires a syntax diagnostic for that particular source.
   Launch errors, arbitrary nonzero exits and timeouts cannot count as rejections.
   The existing yosys-slang integration reads the same six sources using the
   pinned plugin binary relative to the configured Yosys installation. Every
   source is either rejected with its located unsupported-SVA diagnostic (not a
   tool launch error) or, for the three Boolean sources (Yosys 0.70), accepted with
   SBY controls that prove the tautology and refute the free-input assertion. An
   ignore-assertions control successfully elaborates the Boolean source and has
   zero checks: dropping directives is not support for them.
3. **Behavioral controls.** SBY must prove the Boolean tautology and refute the
   free-input assertion using the native frontend. A parse success is not proof.
   All tool outputs go under tmp_path. Tests use existing opt-in resource fixtures.
4. **Documentation.** The compact comparison has the agreed six columns and no
   `later` column. Every comparison row has a linked evidence entry for its
   Yosys+Verific cell. These entries say what the pinned public documentation or
   importer code supports, or explicitly say not established; all are not run.
   Sources are pinned public URLs, not an automated execution or semantic oracle.
   The detailed page records versions, objectives, commands and limits, and links
   canonical existing EBMC/Verilator evidence rather than copying their fixtures.
   In the Yosys+Verific column the marks are parenthesized (checkmark), (cross) or
   (half-circle): inferred, not executed. ❔ means not established.
   Compact cells use only supported (checkmark), rejected (cross), partial
   (half-circle with a short qualification), or unknown (❔) signs. Evidence
   categories and checking objectives belong on the detailed page.
   Earlier support tables use confirmed/qualified frontend-export evidence,
   unknown (❔), or N/A for a semantic rule with no frontend question. Bare
   dashes are not frontend results. EBMC compact cells describe capability and
   concrete engine/semantic limits; lasso/subset/representative coverage labels
   belong on the evidence page, not in those capability cells.
5. **Scope.** Unmeasured syntax is not classified as rejected. EBMC finite validity,
   lasso checks, simulation actions and formal reachability remain separate claims.
   Historical yosys-slang observations do not become current measurements. The
   restrict implementation is lowered as an assumption (formal only). A fast representative
   covers each contract; these six tiny probes need no extended/slow tier.
