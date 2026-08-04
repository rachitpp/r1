"""Pure domain logic: rows in, decisions out. No I/O, no prompts, no HTTP.

The first resident is the SPEC §22 checklist, which was extracted from the
route for exactly this reason — ``api/`` is routes only (CLAUDE.md), and a
ranking rule that needs no database is testable without one.
"""
