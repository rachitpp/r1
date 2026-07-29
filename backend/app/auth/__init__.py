"""Identity and tenancy (SPEC §13).

`tokens` mints and verifies the opaque signed session token; `github` runs the
OAuth dance. Neither touches the database — `db.queries` owns that — and
neither is imported by the agent or retrieval layers, which have no reason to
know that users exist (§13.5).
"""
