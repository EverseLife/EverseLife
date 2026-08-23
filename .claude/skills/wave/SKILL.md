---
name: wave
description: Take one item of the repair program from the vault code review (everselife-vault/90-production/09-code-review-2026-08-23.md), implement it to the quality bar, verify with tests/linters/race tests, run the reviewer agent, and tick the item off in the vault. Use when asked to "do wave N item M", "продолжай программу ревью", or "/wave".
---

# /wave — one item of the repair program

Argument: `<wave>.<item>` (e.g. `1.2`) or a short description; with no argument, pick the first unticked item of the lowest wave.

## Steps

1. **Read the program.** Open `../everselife-vault/90-production/09-code-review-2026-08-23.md`, find the item, read its finding text and the related rule in `CLAUDE.md` «Планка качества». Recall the relevant D-decisions (`../everselife-vault/90-production/02-decision-log.md`) if the item touches protocol (D-225/D-226) or money (D-153, И2).
2. **Check the tree.** `git status --short`: another session may be working in the same copy (see CLAUDE.md «Параллельные сессии»). Do not stash; use a separate test database name.
3. **Plan the cut** in 3–6 lines before editing: files, what moves where, which test proves it. If the item touches a file > 800 lines, the plan must say how the file gets smaller, not bigger.
4. **Implement.** Rules that apply to every item:
   - money/amounts/stock/stamina: `with_for_update` or SQL expression, plus a two-session race test (`tests/test_races.py` pattern: two `async_sessionmaker` sessions, `asyncio.gather`, assert the invariant);
   - reads do not write; answers confirm, events carry state;
   - no new lazy imports; layers `api → engine → models → constants`;
   - ASCII identifiers, English comments, SPDX header on new files.
5. **Verify.** A migration is proved on a **clean** database, never on the dev
   one: create a fresh database and run the whole chain (`alembic upgrade
   head`), because the dev database is already migrated step by step and does
   not exercise the from-scratch path. Then, from `backend/`: `ruff check src tests`, `ruff format --check` on touched files, pytest on an own database (`EVERSELIFE_TEST_DATABASE_URL=...everselife_test_<name>` with `-n`), the new race test; from `frontend/`: `npx tsc --noEmit -p tsconfig.app.json`, `npm run lint`, vitest if present. Run `python tools/spdx.py`.
6. **Review.** Launch the `reviewer` agent on the diff; fix what it flags or state why not.
7. **Tick off.** In the vault document: mark the item `✅ YYYY-MM-DD` with one line of what was done; if the score of the area changed materially, adjust the table. Add a `Следствия` line to the related D-decision when the change alters a contract.
8. **Report** in the chat: what changed (file:line), what proved it (test names, numbers before/after), what is next in the program. Do not commit unless asked.
