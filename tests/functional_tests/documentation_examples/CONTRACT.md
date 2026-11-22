# Executable SVA reference examples

1. **One source.** Every runnable reference example names a checked-in SV source
   and a hand-specified catalog expectation. Displayed code is taken from that
   source, optionally between one pair of reference snippet markers. Every catalog
   ID appears exactly once in a marked reference block; unknown/duplicate/missing
   blocks and changed snippets fail a default local freshness check.
2. **Check and generate.** Every example runs through the real CLI entry point in
   both modes. Accepted examples generate usable lowering rather than merely
   passing check mode. Rejected examples have the documented status, message and
   source location; X-check skips and warnings are checked separately. Generation
   and external-tool outputs are confined to pytest temporary directories.
3. **Honest expectations.** Counts, exit statuses and diagnostic fragments are
   written from the current documented contract. A flipped expected status, wrong
   message or missing summary fails verification; an extra warning is not ignored.
   Source paths and complete frontend diagnostics are not frozen to one host.
   Every gallery entry also has a local `sva-expectation` marker followed by the
   catalog's exit statuses, directive counts (or frontend-error status), diagnostic
   fragments and warning fragments. The contract test compares those lines within
   that entry's block so prose cannot silently drift from the hand-specified catalog.
4. **Coverage.** Every supported matrix construct family has a reference section
   and checked examples. Restrictions varying with directive, operands, context or
   strength are stated explicitly. Syntax fragments that are not complete runnable
   examples are labelled separately. Planned/rejected syntax is never presented as
   supported merely because Slang parses it.
5. **Behavioral controls.** Opt-in Yosys/SBY checks exercise a proof and an intentional
   failure, a reachable nonvacuous cover, and a strong assertion through separate
   safety and liveness tasks. Wrong/missing checks must fail those controls. These
   verify emitted behavior; they do not use generated RTL text as a semantic oracle.
6. **Boundaries and scheduling.** Local metadata/snippet/CLI representatives require
   no external resources. Tool checks use the shared selection fixtures; expensive
   families have explicit opt-in tiers. This area changes documentation and test
   infrastructure, not the compiler, the semantic trace corpus or its export format.
7. **First-run workflow.** The getting-started guide's marked compiler commands
   run in order from a temporary copy of the canonical pulse example: check,
   generate, generate the deliberate bug, and restore the correct build. Each
   invocation succeeds; check mode reports the independently counted three
   assertions and one cover.
   Generation creates a read script; restoring removes the bug definition from
   that script. Formal PASS, reachable cover and bug FAIL remain behavioral
   obligations of the canonical example checks in the formal_designs area.
