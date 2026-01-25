# CLAUDE.md — Working Rules for This Repo

This file defines **how Claude Code should operate** in this repository. It does **not** redefine system logic.

## 1) Source of truth (read first)

Before writing any code, read:

1. **Agent Instructions README** (`AGENT_INSTRUCTIONS_README`)
2. **Crypto Perps Design Spec (Agent-Ready Revision)**

If there is any conflict:

> **Design Spec > Agent Instructions README > This file**

Do not invent behavior not explicitly specified in those documents.

---

## 2) Scope lock

- Implement **Phase 1 (MVP) only** unless explicitly instructed otherwise.
- Do **not** proceed to Layer B dynamics, state-machine exits, or relative momentum unless told to do so.
- Do **not** refactor `pysystemtrade` core abstractions.

If something seems missing or ambiguous:
- Choose the simplest assumption
- Encode it in a test
- Stop and surface the ambiguity

---

## 3) How to work (required)

- Make **small, sequential commits** aligned to the Phase 1 checklist.
- After each logical step:
  - Run tests
  - Ensure the repo is in a runnable state
- Prefer clarity over cleverness.

Avoid:
- Large refactors
- Abstract base classes without need
- Generalization beyond the spec

---

## 4) Testing discipline

- Use `pytest`
- Tests are part of the deliverable, not optional
- If behavior matters, it must be asserted in a test

Required minimum tests:
- Forecast scaling and caps
- Gross leverage cap
- IDM cap
- Accounting identity: `total_pnl = price + funding − costs`

---

## 5) Coding style

- Follow existing `pysystemtrade` conventions where applicable
- Prefer explicit variable names over abbreviations
- Keep functions small and single-purpose
- Add docstrings only where behavior is non-obvious

---

## 6) When to stop

Stop work when **all Phase 1 tests pass** and the system:

- Runs end-to-end on the example dataset
- Produces an equity curve
- Writes a positions CSV

Do **not** continue to enhancements, optimizations, or Phase 2 features unless explicitly instructed.

---

**Reminder:** This repo is optimized for *correctness and auditability*, not speed or elegance.

