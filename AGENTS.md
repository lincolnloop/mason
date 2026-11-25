# AGENTS.md

Guidance for coding agents working **on** this repository. `CLAUDE.md` and `GEMINI.md` are
symlinks to this file.

Skills here run against other repositories. A developer installs mason as a tool (`uv tool
install`) and the skills with `npx skills add -g` (`docs/packaging.md`), so:

- **Nothing project-specific in a skill.** Repository names, paths, commands and labels come
  from `mason config`: the developer's optional settings file for the project, over
  values detected from the repository and defaults (`docs/design.md` §5). If a skill needs a
  value that isn't in the configuration, add it to `lib/artifacts/project.py`, with detection or
  a default where one is safe, rather than hard-coding it.
- **The code is one Python 3.12 package, `artifacts`, run as the `mason` command:** installed as
  a uv tool, or `uv run mason` from a checkout (`pyproject.toml`, `uv.lock`). It must not import
  from a consuming repo. Dependencies are declared and locked; ask before adding one, and prefer
  the standard library where it does the job.
- **Contracts are stable.** Changing a schema in `schemas/` means bumping its version and updating
  every skill that reads or writes it in the same change.
- **Skills are agent-neutral.** They use no tool or feature only one agent has. They call `mason`
  on the PATH, never a path into this repository, since they are installed on their own
  (`npx skills add`): read a schema with `mason schema <name>`, and another skill as
  `../<name>/SKILL.md`, which is installed beside it.
- **Nothing a developer keeps goes in a repository:** settings and run state live in their home
  directory (`lib/artifacts/paths.py`), never in a project or this checkout.
- **Skills state the mason versions they work with** (`metadata.mason-requires`). A skill that
  starts using a new command or field raises its lower bound in the same change.
- **Every script has tests** beside it, covering malformed input as well as the happy path.

Style: single quotes, 100-character lines, explicit type hints, ruff for lint and format
(configured in `pyproject.toml`).

## Commands

- Tests (stdlib `unittest`): `uv run --locked python -m unittest discover -s tests -t .`
- Lint and format: `ruff check . && ruff format --check .`
- Validate an artifact: `uv run mason validate <file.json>` (a review also takes
  `--diff <pr.diff>`)
- Add a project (clone, workflows, GitHub access, label, settings):
  `uv run mason project add <owner>/<repo> [--workflow <name>] [--yes] [--dry-run]`, then
  `mason project list`, `archive [--undo]` and `remove`
- A project's resolved configuration:
  `uv run mason config <project> [--toml]`
- Check the prerequisites (tools, `gh` login, a workflow's tokens):
  `uv run mason doctor [--workflow <name>] [--unattended]`
- Run a workflow unattended:
  `uv run mason run <workflow> --repo <owner>/<repo> --unattended [--pr N] [--dry-run]`
- What needs the developer: `uv run mason inbox [--repo <owner>/<repo>] [--count] [--json]`
- Everything else: `uv run mason --help`
- After changing dependencies in `pyproject.toml`: `uv lock`, and commit `uv.lock` with it.

The JSON examples in `schemas/*.md` are run as tests (`tests/test_schema_docs.py`), review
examples against the page's `diff` block. When a schema changes, update its page in the same
commit.

Commit messages explain why. The diff shows what.
