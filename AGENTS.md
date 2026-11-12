# AGENTS.md

Guidance for coding agents working **on** this repository. `CLAUDE.md` and `GEMINI.md` are
symlinks to this file.

Skills here run against other repositories (reached with `claude --add-dir`, or linked for
Codex with `bin/link-skills`: `docs/design.md` §7), so:

- **Nothing project-specific in a skill.** Repository names, paths, commands and labels come
  from `artifact config`: the consuming repo's optional configuration file, over
  values detected from the repository and defaults (`docs/design.md` §5). If a skill needs a
  value that isn't in the configuration, add it to `lib/artifacts/project.py`, with detection or
  a default where one is safe, rather than hard-coding it.
- **Scripts are stdlib-only Python 3.12** and self-contained. They must not import from a
  consuming repo and must run with `python3 -I`. Ask before adding any dependency.
- **Contracts are stable.** Changing a schema in `schemas/` means bumping its version and updating
  every skill that reads or writes it in the same change.
- **Skills are agent-neutral.** They use no tool or feature only one agent has, and find
  this repository from their own path with symlinks resolved, since they may be reached
  through `.claude/skills`, `.agents/skills` or a user-level link.
- **Every script has tests** beside it, covering malformed input as well as the happy path.

Style: single quotes, 100-character lines, explicit type hints, ruff for lint and format
(configured in `pyproject.toml`).

## Commands

- Tests (stdlib `unittest`, no install step): `python3 -m unittest discover -s tests -t .`
- Lint and format: `ruff check . && ruff format --check .`
- Validate an artifact: `python3 -I bin/artifact validate <file.json>` (a review also takes
  `--diff <pr.diff>`)
- A project's resolved configuration: `python3 -I bin/artifact config <project> [--toml]`
- Run a workflow unattended: `bin/unattended <workflow> <project> [--pr N] [--dry-run]`
- Link the skills for Codex: `bin/link-skills [--dir DIR] [--remove] [--dry-run]`
- Everything else: `python3 -I bin/artifact --help`

The JSON examples in `schemas/*.md` are run as tests (`tests/test_schema_docs.py`), review
examples against the page's `diff` block. When a schema changes, update its page in the same
commit.

Commit messages explain why. The diff shows what.
