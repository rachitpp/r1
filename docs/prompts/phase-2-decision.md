Phase 2 is blocked on this host — confirmed. We're moving the backend to
an unrestricted personal machine tonight. Close out cleanly here:

1. Write the DECISIONS.md entry recording the WDAC/torch block: what
   failed (torch._C DLL, Application Control policy, at import before
   any download), why no workaround was attempted, and the resolution
   (backend development moves to an unrestricted machine; Neon means the
   database is unaffected). Note that tree-sitter passed the same gate
   in Phase 1 while torch did not.

2. Commit the Phase 2 dependency bump (sentence-transformers, pgvector,
   pyyaml + the torch stack in pyproject.toml and uv.lock) as its own
   small commit with a message noting it is pre-staged for the
   environment move and unused so far.

3. Stage and commit the untracked docs/prompts/ files.

4. Leave ROADMAP Phase 2 as "not started" — but add a one-line note
   under it recording the blocked attempt and the environment move, so
   the next session knows why deps exist for a phase with no code.

5. Create docs/HANDOFF.md with the content I'll paste separately.

6. Push everything to origin.

Then give me a ≤6-line summary of what you committed and confirm the
working tree is clean.