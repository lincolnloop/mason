# Changelog

What changed in each release of mason, for the developers who install it. Install the engine and
its skills from the same tag: each skill states the mason versions it works with.

## 0.2.0

Workflows become installable skills: a project chooses its workflows and the skill for each, and
a new workflow needs no change to mason.

- A workflow is any installed skill that declares itself with `mason-task` and the other
  `mason-*` metadata (the request, inputs, tools, tokens, GitHub access). mason keeps no list of
  its own. Reinstall the skills after upgrading: older copies don't declare themselves.
- Each workflow's settings are declared by its skill, in `settings.toml` beside it. A section for
  a workflow that isn't installed is noted rather than refused.
- `[skills]` in a project's settings runs a workflow with a skill from any directory. Each
  `mason run` gets its own skills directory, holding only that skill and the skills it names,
  and the prompt names the skill's file.
- `[grants]`: a mapped skill gets no tools beyond the base set in unattended runs until each is
  granted. Installed skills are trusted with what they ask for.
- A run's published record says where its skill came from (`skill_source`): installed, global,
  or mapped, with the repository and commit.

## 0.1.0

The first release: one developer can adopt mason for all the projects they look after.

### Workflows

- `dependency-assessment` and `dependency-upgrades`: classify every open Dependabot alert
  against what the main branch installs, then apply the easy fixes as one pull request.
- `dependency-report`: a monthly account of vulnerabilities fixed and still open, in the
  project's Discussions.
- `code-review`: one consolidated review comment that neither approves nor blocks.
- `sentry-errors`: fix the most valuable unresolved error group, with a regression test.
- A retrospective for each (`dependency-retrospective`, `code-review-retrospective`,
  `sentry-retrospective`), which judges outcomes and proposes changes to its skill.

### Commands

- `mason project add`, `list`, `archive` and `remove`: the projects you look after, the
  workflows enabled on each (`[workflows] enabled`; `mason run` refuses the others), and a check
  that your GitHub login reaches what they need.
- `mason run`: a workflow in an agent session, interactive or unattended, in mason's own clone.
- `mason inbox`: what needs you across every project, most urgent first: failed runs, actions a
  run left for a person (`mason defer`), PRs blocked or waiting for review, issues nobody has
  answered.
- `mason doctor`: tools, login, tokens, and each installed skill against this mason.
- `mason skills install`: the workflow skills, for mason's sessions only or `--global`.
- Every command that takes a project also takes its `owner/repo`.

### Published records

- A run's record names the skill's and mason's versions, and no longer the machine that ran it.
