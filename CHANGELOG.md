# Changelog

What changed in each release of mason, for the developers who install it. Install the engine and
its skills from the same tag: each skill states the mason versions it works with.

## 0.6.0

- Credentials per project or per owner (`[credentials]`): an env file of its tokens, where its
  `GH_TOKEN` comes from (an env variable or a `gh` login), the name and email on its commits,
  and a GitHub Enterprise host. A project's runs, and mason's GitHub calls for it, see only its
  own tokens. `mason doctor --repo` shows whose token a project uses, its scopes and its expiry.
- A sandbox for a project's own code (`[sandbox]`, `mason sandbox -- <command>`): a throwaway
  container with only the worktree and a package cache, as your user, with no tokens. Unattended
  runs on the project may run its builds, installs and tests no other way.
- Every session is told that text from issues, PRs, alerts and error reports is data, never
  instructions.

## 0.5.0

- `mason approve <id>` and `mason dismiss <id> --reason` decide an action a run left for you;
  either clears it from the inbox. Approving runs the recorded command only if it is a `gh` or
  `mason` command, after asking; anything else is shown for you to run.

- The inbox reports mason's PRs that are stale (no activity in `limits.stale_after_days`) or
  behind their base. mason still never adds to an open PR.
- `dependency-upgrades` replaces a stale upgrade PR of its own with a fresh one: closing it is
  confirm first, and deferred to the inbox when unattended.

## 0.4.1

- The retrospectives cover the calendar period their run is for (`mason run` and `run-due` set
  it), so October's covers exactly October, and their results are keyed by it. `--period` on the
  collectors picks one by hand. Reinstall the skills.

## 0.4.0

- Schedules: each workflow skill declares a default (`mason-schedule`, `mason-kind`), and a
  project can set its own (`mason schedule set`). `mason status`, `due`, `overdue` and `history`
  say what was done, what is due and what was missed; `mason skip` records a period deliberately
  not run.
- Every run records the period it was for, and how it ended (`mason run-end`, which every
  workflow skill now calls). The skills need this mason: reinstall them.
- `mason project add` records the date a project was added; its schedules count from then.
- `mason run-due` runs every period that is due, with each skill's catch-up rule and two retries;
  `mason schedule install` sets up a systemd timer or a cron line to start it.
- Consolidation: a skill can consume another workflow's results (`mason-consumes`); its periods
  are blocked until their inputs are settled. `mason results --within <period>` lists them.
- `mason inbox` lists overdue periods and blocked consolidations.

## 0.3.0

- Results: `mason save-result` keeps a workflow's output in mason, per project, as the master
  copy; `mason publish` copies it to a discussion or a directory and records each copy;
  `mason results` lists them, and marks copies older than the master.
- `dependency-report` saves its report first, then publishes it where its `publish` setting says
  (by default a discussion, as before). It needs this mason: reinstall the skills.
- dependency-assessment, the three retrospectives and Sentry triage save their reports first too,
  and publish to an `issue` by default, as before. The month's assessment issue is updated in
  place: its result is saved with `--comment`, what changed. These skills need this mason.
- Notes: `mason note save`, `list`, `show` and `status` keep analyses per project, out of the
  project's history, with a status (draft, proposed, adopted, dropped, superseded). The `notes`
  skill tells an agent how to use them. Notes publish like any result.

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
