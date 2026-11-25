---
status: draft
updated: 2026-11-24
---

# Roadmap

mason is a developer's assistant for the maintenance and administration of software projects:
it runs agent workflows (dependency upgrades, code reviews, error triage, reports) across several
projects, keeps what they produce, and shows what was done, what is due and what needs a person.

This page orders the work into releases. The designs are in `docs/design.md`,
`docs/packaging.md` and `docs/workflows.md`; each item links to its section. A release is done
when everything under it is built, tested and documented in the README.

## Where it stands

Built:
- **Workflows:** `dependency-assessment`, `dependency-upgrades`, `dependency-report`,
  `dependency-retrospective`, `code-review`, `code-review-retrospective`, `sentry-errors`,
  `sentry-retrospective`.
- **Engine:** configuration with detection (`design.md` §5), validated and embedded artifacts,
  run records, the one-run lock, `mason doctor`.
- **Packaging** (`packaging.md` §9): installed as a uv tool; skills installed with `mason skills
  install`; settings and state in the developer's home directory; `mason run`, interactive or
  unattended, in mason's own clone of a project.

Not built: everything below that is not marked (built).

## 0.1: one developer, several projects

The first release a developer can adopt for all of their projects: add a project, choose its
workflows, and see in one place what needs them.

### Projects

`mason project` manages the projects a developer looks after, which today is a set of manual
README steps.

- `mason project add acme/app` (built): clone, detect the configuration (`mason config`), choose the
  workflows to enable, check the GitHub token can do what they need (read alerts, push, open PRs,
  post discussions), create the `automation` label, and write the settings file
  (`~/.config/mason/projects/acme/app.toml`). Interactive by default; `--workflows` and `--yes`
  for a script.
- `mason project list` (built): each project, its enabled workflows and when each last ran.
- `mason project archive acme/app` (built): stop its runs and keep its results and records.
  `mason project remove` (built) deletes its settings, clone and state; it asks first, and lists what is
  published that it leaves in place.
- `[workflows] enabled` in a project's settings (`workflows.md` §4; built), so a project runs
  only the workflows chosen for it. Only the list is in 0.1; mapping a workflow to another skill comes in
  0.2.

### The inbox

`mason inbox [--repo acme/app]` (built) lists what needs the developer, across every project,
most urgent first:

| Item | Source |
|---|---|
| A PR mason opened, waiting for review, failing CI, or in conflict | GitHub: open PRs with the `automation` label |
| A confirm-first action an unattended run couldn't take (file an issue, archive a Sentry group) | the run's record and report |
| A failed run, with its log | run records |
| A missing or expired token, or a tool `doctor` can't find | `mason doctor` for each project |
| A report issue or triage issue with no response | GitHub: issues mason opened |

- Each item links to where it is dealt with: the PR, the issue, the log, the command to fix it.
- Items are derived each time from GitHub and the run records, never kept by hand, so dealing
  with an item in GitHub clears it from the inbox.
- `--json` for other tools; `mason inbox --count` exits 1 when there is something to do, for a
  status bar or cron mail.
- Confirm-first actions are listed, not taken. Approving one from the inbox is in 0.5.

To list confirm-first actions, unattended runs record them in a structured form in the run's
directory (`deferred.json`, written by `mason defer`; built), rather than only in the report's
prose. Unattended runs also record how they ended beside their log, so failures can be listed.

### Finishing the packaging

- `metadata.mason-requires` in each workflow skill, checked by `mason doctor`, `mason run` and
  `run-start` (`packaging.md` §4.2; built).
- Published provenance names the tool and its version, not the host (`packaging.md` §8; built).
- The commands skills call take `owner/repo` as well as a path (`packaging.md` §9; built).

### Tidying for a release

- The version is `0.1.0`, matching the roadmap (built).
- `design.md` §7 replaced by a pointer to `packaging.md` (built).
- The README opens with `mason project add`, `mason run` and `mason inbox` (built).
- `CHANGELOG.md`, started with this release (built).

## 0.2: workflows as installable skills

A project chooses its workflows, and new ones are added without changing the engine.
`docs/workflows.md` §3 to §7 (plan steps 1 to 5).

- Each workflow declared in its skill's frontmatter (`mason-task` and the other `mason-*`
  metadata), replacing `runner.WORKFLOWS` (built).
- A workflow mapped to a skill from any source (`[skills]`); a per-run skills directory; the
  prompt names the skill's file (built).
- Workflow settings declared by the skill (`settings.toml`), replacing its entry in
  `project.SECTIONS` (built).
- Tool requests and grants (`[grants]`); workflows from other sources (built). Workflows from the
  project's own `.claude/skills/` are still to come.
- Each run naming its skill's version and source (built). `run/2` moves to 0.4, where the
  ledger first needs its fields.

## 0.3: results and notes

Results kept by mason as the master copy, published wherever the developer chooses.
`docs/workflows.md` §8 (plan steps 6 and 7).

- The results directory, `mason save-result`, `mason publish`, `mason results`.
- `dependency-report`, then the retrospectives, the assessment and Sentry triage, moved to save
  first and publish where the settings say.
- `mason note` and the `notes` skill, for analyses and assessments kept out of the project's
  history.

## 0.4: schedules and status

mason owns the schedule, and says what was done, what is due and what was missed.
`docs/workflows.md` §9 (plan steps 8 and 9).

- Schedules per workflow and project; calendar periods; the ledger, with `run/2` (its `period`
  and the `done`, `nothing_to_do`, `failed` and `skipped` outcomes).
- `mason status`, `due`, `overdue`, `history`, `skip`, `schedule set`.
- `mason run-due` as a single heartbeat, and `mason schedule install`.
- Consolidation inputs, for reports built from other reports.
- The inbox gains overdue workflows and blocked consolidations.

## 0.5: following work through

PRs that are opened become changes that are merged.

- **PR follow-through:** a workflow that watches mason's own PRs, rebases those `main` has moved
  past, and reports CI failures and conflicts to the inbox, or fixes what it can.
- **The decide step** (`design.md` §4): merging under a project's written policy (patch-only, CI
  green, no skipped check, only manifests touched). Off by default.
- **Approving from the inbox:** a confirm-first action recorded by an unattended run is taken
  when the developer approves it, without re-running the workflow.

## 0.6: safety and identity

What makes it safe to run more workflows, from more sources, on more projects.

- **A sandbox per run** (a container) for workflows that run a project's code, with only the
  tokens that run needs.
- **Untrusted input:** issue and PR text, Sentry events and package metadata are treated as data
  throughout, with tests for prompt injection in the workflows that read them.
- **Credentials per project:** projects in different organisations with different tokens; a bot
  account so PRs don't appear as the developer; `doctor` checks token scopes and expiry.

## Later

Ordered roughly by when they are likely to matter. Each gets a design before it is built.

- **Cost tracking.** Each run records its token use and model in the ledger; budgets per
  project and workflow, which `run-due` respects; the model chosen per workflow. Not a priority
  while the business doesn't account for token costs; it becomes one if they rise.
- **Teams.** Who owns which workflow on a shared project; handing a project's results to
  another developer; a shared results store (`workflows.md` §12).
- **Testing skill changes.** Evaluations against recorded inputs, and trying a change on one
  project before all of them.
- **Event triggers:** a new critical alert, an opened PR, an error spike, alongside schedules.
- **Plugin packages** for workflows that need their own code (`workflows.md` §6.3).
- **Other agents:** unattended runs with Codex and others (`packaging.md` §10).
- **Other platforms:** GitLab; Jira or Linear for tracked work. The GitHub calls kept behind one
  module, so a second platform is an addition rather than a rewrite.
- **A view beyond the command line:** a terminal UI or generated HTML page for status and the
  inbox; a portfolio report across projects for a manager or client.
- **Housekeeping:** retention for logs and run directories; migrating stored results and the
  ledger when a schema changes.

## Workflow catalogue

Workflows to add as skills, independent of the releases above. Each one becomes worth building
when a project needs it; from 0.2 they need no engine change.

| Workflow | What it does |
|---|---|
| CI health | flaky tests, slow or failing jobs, and the trend |
| Runtime end-of-life | the Python, Node or framework version itself, not only packages |
| Code and secret scanning | GitHub's code-scanning and secret-scanning alerts, as Dependabot's are now |
| Audits without Dependabot | `pip-audit` or OSV, for projects that don't use Dependabot |
| Licence compliance | dependencies whose licences the project can't use |
| Repository hygiene | stale branches, issues and PRs |
| Release notes | a changelog from merged PRs |
| Expiry | certificates and domains |
| Documentation drift | docs that no longer match the code |
| Weekly and monthly consolidation | reports built from other reports (`workflows.md` §9.6) |
