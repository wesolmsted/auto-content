# CLAUDE.md

Auto Content Engine: a general-purpose pipeline that cuts raw footage into beat-synced short videos, posts approved ones to Instagram Trial Reels and TikTok, and learns which styles win.

## Read first

- **[docs/DESIGN.md](docs/DESIGN.md)** is the engineering reference for all coding work: architecture, module contracts, data model, conventions, testing strategy, milestones and the decision log. Follow it.
- [README.md](README.md) is the product spec (goals, touchpoints, risks).
- If code and DESIGN.md disagree, update one of them in the same change. New design decisions go in DESIGN.md §13 (decision log).
- Work proceeds in the milestone slices in DESIGN.md §12. Don't start a slice's code before its tests exist (below).

## Test-first workflow

Every behavior change starts with a test that fails. This keeps tests honest: a test that never failed has never proven anything.

1. **Write the test first.** Describe the behavior with concrete inputs and the exact expected outputs (values, state transitions, raised errors). Use the real interfaces from DESIGN.md §6, with fakes from `tests/fakes/` for external systems.
2. **Run it and watch it fail for the right reason.** The failure must be an assertion about behavior (wrong value, missing state change, exception not raised). An `ImportError`, `NameError` or missing fixture doesn't count. Add a minimal stub (signature only, `raise NotImplementedError` or a wrong return value) so the test runs and fails on its assertion. Paste the failing output in your summary.
3. **Implement the code**, then run the test again. Iterate on the *code* until the test passes. Then run the full suite (`make check`).
4. **Don't weaken a test to make it pass.** If the test itself turns out to be wrong, stop and say so explicitly: what was wrong and why the new expectation is correct. Then change it, and confirm it still fails against the old/stub code.
5. **Bug fixes follow the same order:** first a test that reproduces the bug and fails, then the fix.

What counts as a non-trivial test:
- It asserts specific outcomes, not just "no exception", "is not None", or a type check.
- It would fail if the implementation were deleted, stubbed or returned a constant. If you can't name the plausible bug it catches, it isn't worth keeping.
- It doesn't restate the implementation (no mocking the function under test, no asserting a mock was called in place of checking the result).
- It covers edge cases relevant to the unit: empty inputs, boundaries (e.g. a cut exactly on the drop, snippet length not a beat multiple), and failure paths.
- For timeline/assembly logic, prefer property tests (Hypothesis) on the invariants in DESIGN.md §11.

## Commands

The project is not scaffolded yet (slice 1 in DESIGN.md §12). Once it is:

- `uv sync`: install
- `make check`: ruff lint + format check, mypy --strict, pytest (must pass before committing)
- `uv run pytest tests/unit -x`: fast loop
- `uv run ace --help`: CLI

## Project memory and skills

The [`.claude/`](.claude/) directory holds knowledge for this project and is committed with it:

- `.claude/memory/`: durable facts learned while working that aren't in the code, DESIGN.md or git history. Examples: API quirks found during integration, account setup details (no secrets), the owner's preferences. One fact per file, with an index line in `.claude/memory/MEMORY.md`. Read the index at the start of a session. Update or delete stale entries rather than adding duplicates.
- `.claude/skills/<name>/SKILL.md`: repeatable project workflows (e.g. adding a recipe variable, running a render dev loop). Add one when a multi-step procedure gets done more than once.

Never put secrets, tokens or personal account IDs in either.

## Parallel agent workflow

Coding is done by a main agent that hands scoped tasks to sub-agents, which run in parallel.

**Main agent**
- Splits work into tasks whose files don't overlap, so branches merge without conflicts.
- Merges finished branches into `main`, runs `make check`, and pushes.
- Owns the shared files: `docs/DESIGN.md`, `README.md`, `CLAUDE.md` and `.claude/memory/MEMORY.md`.
- When a task finishes, adds its memory file to the index and updates DESIGN.md if the agent reports a deviation.

**Sub-agents**
- Work on their own branch, `task/<slice-number>-<short-name>`, in their own git worktree.
- Commit there with the test-first workflow, and push the branch to `origin`. Never commit to or merge into `main`.
- Edit only the files the task assigns them. If the task seems to need a change to a shared file, say so in the final report instead of making it. Shared files include:
  - `pyproject.toml`, `uv.lock`, `engine/config.py`, `config/default.toml`;
  - `engine/db/` and `alembic/`;
  - `engine/cli.py`;
  - shared domain models;
  - the docs and memory index listed above.
- Before finishing, write one memory file, `.claude/memory/task-<slice-number>-<short-name>.md`, in the format in `.claude/memory/MEMORY.md`. It should say:
  - what was built, and the public functions and classes others will call;
  - deviations from DESIGN.md, and why;
  - gotchas, and how to run or test the module.

  Don't edit `MEMORY.md`.
- End with a short report:
  - branch name and commits;
  - `make check` result;
  - files touched;
  - any shared-file changes needed;
  - open questions.

## Working rules

- Readable code: clear names, straightforward structure, match surrounding code. No comments for simple changes; comment only non-obvious *why*.
- If it's unclear what's wanted, ask before proceeding.
- **Commit and push after each well-scoped change** without waiting to be asked: one concern per commit, `make check` passing (once the project is scaffolded), pushed to `origin`. Don't bundle unrelated changes, and don't commit half-finished work.
- **Temporary files go in `tmp/` at the repo (or worktree) root**, which is gitignored. Never use `/tmp` or other paths outside the repo; edits inside the repo don't need the owner's approval. Tests keep using pytest's `tmp_path`.
- No secrets in code or git. Everything environment-specific goes through `engine/config.py` (DESIGN.md §8).
- **The system never posts.** No code may call a platform's publish, upload or draft endpoint; platform APIs are read-only (metrics). The owner posts every video from the app after downloading it from the hand-off page (DESIGN.md §2.6, D8).
