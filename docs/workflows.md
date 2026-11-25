---
status: proposal
updated: 2026-11-23
---

# Workflows as installable skills

A proposal: let each project choose which workflows it runs, map a workflow to whichever skill
implements it, and add workflows (shared or project-specific) without changing the engine. It
builds on `docs/packaging.md`, which already separates the engine from the skills and plans a
`requires:` block in each skill's frontmatter (§4.2).

## 1. The problem

A workflow and its skill share a name, and the engine knows every workflow by that name:

| What | Where | Effect |
|---|---|---|
| The task prompt, allowed tools, `needs_pr`, required tokens | `runner.WORKFLOWS` | `mason run` and `mason doctor` accept only names in this list |
| Each workflow's settings | `project.SECTIONS` | an unknown section is an error, so a new workflow can't have settings without an engine change |
| Domain collectors and schemas | `deps.py`, `sentry.py`, `review.py`; `run/1` | some contracts carry one workflow's fields (`run/1`'s outcomes, `alerts_not_covered`) |

So:
- **Every installed skill is visible in every run.** `mason run` adds the whole skills directory,
  whatever the project uses.
- **Choosing a skill depends on install order and description matching.** Two skills with the same
  name replace each other, whichever was installed last wins (`packaging.md` §4.4), and the
  session picks a skill from its `description`.
- **A new workflow is an engine change,** even one that needs nothing but `gh` and mason's generic
  commands.

## 2. Principles

1. **mason runs any workflow that keeps the mason contract,** not any skill. The contract is what
   mason adds over a scheduler and `claude -p`: a run record, the one-run lock, at most one PR per
   run, derived and validated artifacts, the autonomy levels (`design.md` §6), and an outcome a
   retrospective can check.
2. **A workflow is declared where it is written.** Its skill states what it needs: prompt, tools,
   tokens, inputs, settings, engine version. The engine reads the declaration; it doesn't keep a
   copy.
3. **A skill requests; the developer grants.** Tool permissions are the defence in depth for
   unattended runs. A skill can't grant itself more than the developer approved.
4. **Selection is per project, installation is per developer.** Packaging principle 1 holds:
   nothing of mason goes into a project.

## 3. Declaring a workflow

*Built in 0.2 (plan step 1), except settings (step 3).*

Each workflow skill declares itself in its frontmatter's `metadata`, the open SKILL.md format's
map of strings, so other agents ignore it and the skill stays agent-neutral. Since the values
are strings, a list is comma-separated:

```yaml
---
name: sentry-errors
description: 'This skill should be used when asked to "fix the Sentry errors", ...'
version: 1.0.1
metadata:
  mason-requires: '>=0.1,<0.2'
  mason-task: 'Run the Sentry errors workflow for this project'
  mason-tools: 'Bash(make *), Bash(npm *)'
  mason-env: 'SENTRY_AUTH_TOKEN'
---
```

- `mason-task` makes a skill a workflow: the request `mason run` starts the session with. A
  skill without it is a knowledge skill (`packaging.md` §4.1): it can be loaded by a workflow
  but can't be run as one.
- `mason-inputs` names what `mason run` must be given (`pr` for now), checked before the session
  starts. A task names `{pr}` exactly when its inputs include `pr`.
- `mason-tools` and `mason-env` are what an unattended run may use beyond the base tools, and
  the tokens it can't run without. The base tools and `DENIED_TOOLS` stay in the engine and
  apply to every workflow.
- `mason-github` is what the GitHub login must reach beyond pushing (`alerts`, `discussions`),
  which `mason project add` checks.
- `mason-requires` is the range of mason versions the skill works with (`packaging.md` §4.2).

The workflows are whatever workflow skills are installed: mason keeps no list of its own. The
first copy of a skill in the order a session loads them wins: mason's own skills directory, then
the global ones, then `MASON_SKILLS_PATH` (a checkout's `skills/`, for the tests).

**Settings (step 3)** don't fit a map of strings. They go in a file beside the skill,
`skills/<name>/settings.toml`, which installs with it:

```toml
[sentry_org]
type = 'str'
required = true

[environments]
type = 'list'
default = ['production']
```

That replaces the workflow's entry in `project.SECTIONS`. The shared sections (`repo`, `limits`,
`verify`) and the detection in `detect.py` stay in the engine. A skill whose settings can be
detected names the detector (`detect = 'pip_requirements'`) rather than shipping code.

`mason doctor` lists the installed workflow skills and whether each works with this engine;
their source (from the `npx skills` lock) is still to come.

## 4. Choosing workflows per project

The developer's settings for a project name the workflows it runs, and may map one to a
particular skill:

```toml
# ~/.config/mason/projects/acme/app.toml
[workflows]
enabled = ['dependency-assessment', 'dependency-upgrades', 'code-review', 'billing-reconcile']

[workflows.code-review]
skill = 'acme/team-skills:code-review'   # instead of mason's; source as in the skills lock
knowledge = ['django-idioms']            # loaded with it (today's code-review.knowledge)
tools = []                               # tools granted beyond the base set (§5)

[sentry-errors]
sentry_org = 'acme'
sentry_project = 'app'
```

- **Unset `enabled` means every installed workflow,** so a project with no settings behaves as it
  does today.
- **The skill is chosen by settings, not install order.** Two skills with the same name can be
  installed from different sources; the mapping says which one runs. If unmapped and ambiguous,
  `mason run` refuses and lists both.
- **A run sees only what it uses.** `mason run` builds a per-run skills directory under the run's
  state directory, with symlinks to the mapped workflow skill, its knowledge skills and the skills
  it names as `../<name>/SKILL.md`. That directory, not the whole catalog, goes to `--add-dir`.
- **The prompt names the skill's file** ("Read `<run>/skills/code-review/SKILL.md` and follow it
  for this project…") instead of relying on its `description` matching. The description still
  matters in a session where a person asks for a workflow by name.
- **Settings are checked against the mapped skill's declaration.** Unknown keys stay errors. A
  section for a workflow that isn't installed is a warning from `mason doctor`, not an error, so
  uninstalling a skill doesn't break a project's settings.

With `--global` installs, a session started without `mason run` still sees every installed skill.
That is the developer's choice, as now.

## 5. Granting tools

A skill's `tools` is a request. For an unattended run the engine allows:

```
base tools + Bash(mason *) + (requested ∩ granted) − DENIED_TOOLS
```

- **mason's own skills are granted what they request**, because they ship with the engine at the
  same tag. Any other source needs the grant written in `[workflows.<name>] tools`.
- **A request that isn't granted** makes `mason doctor --workflow <name> --unattended` fail, and
  prints the line to add. A run never widens its permissions silently.
- **A grant is tied to the skill's version.** When `npx skills update` changes a skill whose
  request has changed, `mason doctor` reports the difference, and the run is refused until the
  grant is updated.
- **`DENIED_TOOLS` can't be overridden.** No setting allows merging, reviewing as approve or
  request-changes, or force-pushing.
- `--allow` on the command line still adds a tool for one run.

Interactive runs are unchanged: the person approves actions under their own permission settings.

## 6. Adding a workflow

### 6.1 With mason's generic commands only

Most new workflows need only `gh` and commands that aren't tied to one workflow: `config`, `lock`,
the run record, `validate`, `render-change`, `embed`/`extract`, `annotate-diff`. Such a workflow
is just a SKILL.md with `mason-task` in its metadata, installed from any source:

```sh
mason skills install acme/team-skills@v1 --skill billing-reconcile
```

Then enable it in the project's settings and `mason run billing-reconcile --repo acme/app`.

### 6.2 Project-specific workflows

A skill committed in the project's own `.claude/skills/` (or `.agents/skills/`) is already loaded,
because `mason run` starts the session in the project's clone. With `mason-task` in its metadata,
mason can run it as a workflow too. Committing it is the team's choice, not mason's, so principle
1 of `packaging.md` still holds.

It is code from the project's main branch, which more people can change than the developer's own
settings. So a project skill is never granted tools by default (§5), and `mason doctor` shows its
commit with the grant.

### 6.3 Workflows that need their own code

A workflow that needs a collector (as `sentry-errors` needs `sentry.py`) or its own schema can't
be only a SKILL.md, because skills call `mason` and nothing else. Two options:

- **Keep it in mason.** Good enough while the workflows are ours.
- **Plugin packages:** a Python package exposing `mason.commands` and `mason.schemas` entry
  points, installed into mason's tool environment
  (`uv tool install mason --with acme-mason-pack`). Its commands appear under `mason` and its
  schemas under `mason schema`.

This proposal defers plugins until a real workflow needs one.

## 7. Contract changes

- **`run/2`.** `run/1`'s outcomes (`stopped_pr_open`, `alerts_not_covered`) belong to
  dependency upgrades. `run/2` keeps `pr_opened`, `issue_opened`, `review_posted`, adds
  `discussion_posted` and a generic `stopped` with a `reason` and a link, and moves
  `alerts_not_covered` into the stopping workflow's own comment. Every skill that writes `run/1`
  changes in the same release, as AGENTS.md requires.
- **Provenance.** `produced_by` records the skill's source and version from the skills lock, not
  only `automation_sha`. This is the change `packaging.md` §8 already plans; it matters more once
  skills come from more than one place.
- **`finding/1` and `change/1`** are close to generic. Their `kind` and classification values
  should be checked for dependency-only terms before third-party workflows rely on them.

## 8. Results and publishing

Today a workflow writes its output straight to GitHub. `dependency-report` renders the report to
a file in its run directory, then posts it with `mason post-discussion`, and the post is the only
copy anyone keeps. The run directory is scratch (`packaging.md` §6: "losing it loses logs, not
records"), and Discussions is the only destination.

This section separates producing a result from publishing it. **The result mason keeps is the
master copy, until the developer removes it.** Everything published (a discussion, an issue, a
file in a docs repository) is a copy of it, and a result may have several. So a copy can be
deleted, edited by someone else or go out of date, and the master still says what the workflow
produced.

### 8.1 The results directory

Each project gets a results directory under `XDG_DATA_HOME`, the XDG directory for data a
person keeps, not under `XDG_STATE_HOME` beside the runs:

```
~/.local/share/mason/<owner>/<repo>/results/
  dependency-report/2026-10/
    report.json        the data, in the workflow's schema (dependency-report/1)
    report.md          the rendered result
    run.json           the run that produced it
    published.json     where it has been published: [{target, url, at, run}, ...]
```

- `paths.results(repo)` names it, keyed by repository like the rest (`paths.py`).
- **A result has a stable key:** for a scheduled workflow, its period (§9.2), so the October
  report is always `dependency-report/2026-10`, whichever run made it. A workflow that isn't
  scheduled chooses its own (a PR and head, a note's date and title).
- **Saving is part of the contract.** A workflow that produces a result saves it with
  `mason save-result <run> <workflow> <key> <files…>`, which validates the data against its
  schema before copying. Publishing is optional and repeatable.
- **A result is not rewritten silently.** Saving to an existing key keeps the earlier version
  (`2026-10/previous/<run-id>/`), so a re-run can't lose what was already published, and each
  copy can be traced to the version it was made from.
- **Nothing removes a result but the developer:** `mason results remove <workflow>/<key>`, which
  asks first and lists the published copies it leaves in place. No run, cleanup or upgrade
  deletes one, and an unattended run never does.
- **Back it up like any other data a person keeps.** It is the only complete copy; §11 asks how
  it reaches more than one machine.
- **Run directories support the result.** Working files, worktrees and logs stay under
  `XDG_STATE_HOME`, but a run that saved a result is kept as long as the result is: its logs are
  how the result was made. Runs that saved nothing can be pruned.

### 8.2 Publishing

Publishing is a step of its own, run by the workflow or by the developer later:

```sh
mason publish dependency-report/2026-10 --repo acme/app --to 'discussion:Security reports'
mason publish dependency-report/2026-10 --repo acme/app --to 'file:~/Reports/acme-app/'
```

- **Targets** start with `discussion:<category>` (today's `post-discussion`), `issue`, and
  `file:<directory>` (a docs repository, a shared drive). Others, such as Slack or email, are
  added as publishers in the engine, or later in plugins (§6.3).
- **Each publish is recorded** in `published.json`: the target, the copy's URL or path, when,
  and the version of the result it was made from. So it is clear where a result has gone, and
  which copies are out of date after a re-run.
- **A copy is updated by publishing again.** How depends on the target: a `file:` copy is
  replaced; a discussion or issue, which people may have replied to, gets a comment with the new
  version rather than an edit (below). `mason results` marks copies made from an earlier
  version.
- **Each target still checks for duplicates itself.** Discussions already refuses a second post
  with the same title. `published.json` answers for this machine, but another machine's run may
  have published already, so the target is asked too (`packaging.md` principle 6). That check
  is about coordination; the master copy is still the local result.
- **Publishing never rewrites a post people can reply to.** A correction to a discussion or issue
  is a comment, as now (`dependency-report`'s autonomy table).

### 8.3 Declaring and configuring it

A workflow declares what it produces, and the project's settings say where it goes:

```yaml
metadata:
  mason:
    produces:
      schema: dependency-report/1
      key: period              # §9.2; a workflow that isn't scheduled names its own
      publish: ['discussion']  # the default targets, overridden by settings
```

```toml
# ~/.config/mason/projects/acme/app.toml
[dependency-report]
publish = ['discussion:Security reports', 'file:~/Reports/acme-app/']
```

- **An empty list means save only.** The result is kept and nothing is posted.
- **Unattended,** a run publishes to every listed target. **Interactive,** it confirms first, as
  publishing does now (`design.md` §6). A target's tools (e.g. `gh` for Discussions) are granted
  like any other (§5).
- `mason results --repo acme/app` lists the project's results and where each was published.

### 8.4 What changes

- `dependency-report` saves `report.json` and `report.md` with `save-result`, then calls
  `mason publish` for each configured target instead of `post-discussion`. Its description says
  "saves, and publishes where the project's settings say" rather than "publishes to Discussions".
- The same pattern then applies to the other GitHub-only outputs: the retrospectives' report
  issues, the dependency assessment issue and Sentry triage issues. PRs and reviews stay as they
  are: their destination is the code itself.
- `packaging.md` principle 2 ("the only shared surface is what gets published") still holds: a
  local result is the developer's, and only what they publish is shared. But published items are
  no longer the records: §6 changes to say the results directory is the master copy, and that
  losing it loses records.

### 8.5 Analyses and assessments

Not every result comes from a workflow. A developer asks an agent to analyse something (a design
review, an investigation, an option paper, an assessment of a library) and keeps the answer.
Today these go in the project's `docs/notes/`, which puts work that may never be acted on into
the project's history, and into every other developer's checkout.

They are kept as results too, without a workflow:

```
~/.local/share/mason/<owner>/<repo>/results/
  notes/2026-11-23-workflow-orchestration/
    note.md            the analysis, as written
    meta.json          title, created, updated, status, tags, links
    published.json     as for any result
```

- **Saved from any session:** `mason note save --repo acme/app --title '<title>' <file.md>`
  (`--update <key>` for a new version of an existing note). The key is the date and a slug of the
  title. A skill, `notes`, tells an agent when and how to save one, so "save this analysis" works
  in any session, with or without `mason run`.
- **Free-form, with a little structure.** The body is Markdown and isn't checked against a
  schema. `meta.json` is (`note/1`), so notes can be listed and filtered.
- **A status, because a note has a lifecycle** (`method.md`: facts with a lifecycle get a status
  and a date): `draft`, `proposed`, `adopted`, `dropped` or `superseded` (by another note's key).
  `mason note status <key> adopted --link <PR or issue>` records what happened to it.
- **Adopting graduates it.** When the work goes ahead, what the project needs to keep is moved
  into the project as a decision record, a design page or a PR description, written for the
  project, and the note links to it. Until then nothing of it is in the project's history.
- **Findable.** `mason note list --repo acme/app [--status proposed] [--tag <tag>]` and
  `mason note show <key>`. The `notes` skill lists a project's notes at the start of an analysis,
  so an agent builds on earlier ones instead of repeating them.
- **Published like any result** (§8.2) when it should be shared: as an issue or discussion for
  the team, or a file in a shared folder. The master stays here.
- **Version history.** Updating a note keeps the earlier version, as for any result (§8.1).
- **Not for secrets or production personal data.** A note is plain text in the developer's home
  directory, and may be published.

## 9. Schedules and status

Workflows run on a regular basis, and the useful question is less "can cron start it?" than
"what was done, what is due, and what was missed?". Today mason can't answer that: schedules are
systemd timers or cron lines written by hand (README), so mason doesn't know how often anything
is meant to run. Each workflow also has its own idea of time: `dependency-report` takes a calendar
`--month`, the retrospectives a rolling `period_days`, the assessment "the month's issue".

This section gives mason the schedule, a common unit of time, and a ledger to judge against.

### 9.1 Schedules

A skill declares a default schedule, and the developer's settings for a project override it:

```yaml
metadata:
  mason:
    schedule:
      default: monthly day 2
      kind: report            # report or check (§9.2)
      catch_up: all           # all or latest (§9.5)
      grace: 2d               # how long after a period is due before it is overdue
```

```toml
# ~/.config/mason/projects/acme/app.toml
[schedule]
dependency-assessment    = 'daily 06:00'
dependency-report        = 'weekly mon'
sentry-errors            = 'weekly mon 07:00'
dependency-retrospective = 'quarterly'
code-review              = 'off'
```

- **Intervals:** `hourly`, `daily`, `weekly`, `monthly`, `quarterly`, `yearly`, or `off`. An
  optional anchor says when in the interval: a weekday, a day of the month, a time. Times are in
  the developer's time zone, set once in `~/.config/mason/config.toml`.
- **Event-driven workflows** (code review) have a polling interval, not a schedule of periods.
  They are never overdue; `mason status` shows when they last polled.
- **Changing a schedule** with `mason schedule set acme/app dependency-report weekly` records the
  date of the change (`dependency-report.since`). Periods before it are judged by the old schedule,
  and periods from it by the new one, so switching from monthly to weekly doesn't mark the
  earlier weeks overdue.

### 9.2 Periods

Every interval maps to a calendar period with a stable key:

| Interval | Key | Example |
|---|---|---|
| hourly | date and hour | `2026-11-24T09` |
| daily | date | `2026-11-24` |
| weekly | ISO week | `2026-W48` |
| monthly | month | `2026-11` |
| quarterly | quarter | `2026-Q4` |
| yearly | year | `2026` |

- **A report covers the period that has just ended.** It runs once the period closes (plus its
  anchor), and its collectors take `--period 2026-W47`, replacing `--month` and `period_days`.
  The period is the result's key (§8.1): `dependency-report/2026-W47/`.
- **A check runs once in each period** (the assessment, the upgrades, sentry-errors). It looks at
  the state now, and its run is filed under the period it ran in.
- **Containment is defined once, in the engine.** A day is in one week, month, quarter and year.
  An ISO week can straddle two months, so a week belongs to the month that contains its Thursday,
  the ISO rule. `mason period within 2026-11 --interval weekly` lists `2026-W45` to `2026-W48`.

### 9.3 The ledger

Status is derived, never kept by hand. The ledger is the run records (`run/2`, §7) and the saved
results (§8.1). `run/2` gains:

- `period`: the period the run was for;
- an outcome of `done`, `nothing_to_do`, `failed` or `skipped`, alongside the publishing
  outcomes (`pr_opened`, `issue_opened`, and so on).

`mason skip <repo> <workflow> <period> --reason '<why>'` records a deliberate skip, so a missed
period someone chose not to run isn't shown as overdue.

### 9.4 Status

Each workflow, in each period, is in one state:

| State | Meaning |
|---|---|
| done | a run for the period succeeded (`nothing_to_do` counts) |
| skipped | a person skipped it, with a reason |
| due | the period is open and not yet done |
| upcoming | the next period, and when it opens |
| overdue | the period's due time plus its grace has passed, with no successful run |
| failed | a run was attempted and didn't finish; the latest log is linked |
| blocked | a consolidation whose inputs aren't all done (§9.6) |

Commands:

```sh
mason status [--repo acme/app | --all]   # every workflow: schedule, last done, next due, state
mason due [--all] [--within 7d]          # due now, and upcoming within a window
mason overdue [--all]                    # overdue and failed; exits 1 if there are any
mason history acme/app dependency-report # each period, its state, its run and its result
```

- `--all` covers every project with a settings file (`~/.config/mason/projects/`).
- `mason overdue` exits 1 when anything is overdue, so cron's mail, a monitoring check or a
  status bar can raise it without parsing output. `--json` gives the same for other tools.

### 9.5 Running on a schedule

A scheduler only needs to start mason regularly. mason decides what is due:

```
*/15 * * * * mason run-due --all
```

- `mason run-due` runs every workflow that is due or overdue, unattended, one at a time per
  project, under the existing lock (`packaging.md` §6). A workflow that is already running is
  left alone.
- **The scheduler is a heartbeat, not the schedule.** Changing a schedule never means editing a
  timer, and an overdue workflow means something is wrong (an expired token, the machine was
  off, a failing run) rather than a timer nobody wrote.
- **Missed periods** follow the skill's `catch_up`: `all` runs each missed period in order (a
  report, where every period matters), `latest` runs only the current one and records the others
  as skipped (a check, where only the current state matters).
- **Failures** are retried at the next heartbeat, up to a limit (`retries`, default 2 per period),
  then left as `failed` for a person.
- `mason schedule install` writes the heartbeat as a cron line, systemd timer or launchd job.
  The GitHub Actions variant in `packaging.md` §7 runs `mason run-due` on its own schedule.

Running by hand works as now: `mason run <workflow> --repo <repo> [--period <key>]` records its
period in the ledger, so a manual run counts.

### 9.6 Consolidating results

Combining results across periods (weekly reports into a monthly one) is a workflow of its own:
a skill with its own template and judgement, scheduled like any other. The engine supplies what
it needs:

```yaml
metadata:
  mason:
    schedule: {default: monthly day 3, kind: report}
    consumes: {workflow: weekly-report, interval: weekly}
```

- `mason results list --repo acme/app --workflow weekly-report --within 2026-11` gives the
  results for the weeks in November, by the containment rule (§9.2).
- **A consolidation is blocked until its inputs are done.** Its state is `blocked`, naming the
  missing periods, until each is done or skipped, so it never consolidates a partial month
  without saying so. `--partial` runs it anyway, and the result says which periods are missing.
- Its result is filed under its own period (`monthly-report/2026-11/`) and links to its inputs.

## 10. Retrospectives

A retrospective proposes changes to the skill it studies as a PR to that skill's source. Today
that is always mason (`mason source`). With other sources it reads the source from the skills
lock and clones that repository instead, and when it can't push there it puts the proposed change
in its report issue instead.

## 11. Plan

Each step can ship by itself. `docs/roadmap.md` places them in releases: steps 1 to 5 in 0.2,
6 and 7 in 0.3, 8 and 9 in 0.4, and step 2's `[workflows] enabled` list early, in 0.1.

| Step | Change | Behaviour seen by a developer |
|---|---|---|
| 1 | `mason-*` metadata in mason's skills; the engine reads it; `runner.WORKFLOWS` removed (built) | none |
| 2 | `[workflows] enabled` and `skill`; the per-run skills directory; the prompt names the skill's file | a project runs only what it enables |
| 3 | settings declared by skills; workflow entries removed from `project.SECTIONS` | none |
| 4 | tool requests and grants; workflows from other sources and from the project | new workflows without engine changes |
| 5 | `run/2` and per-skill provenance | published records name the skill's source |
| 6 | results directory, `save-result`, `mason publish`; `dependency-report` moved to it | reports kept per project, published where settings say |
| 7 | `mason note` and the `notes` skill | analyses kept per project, out of the project's history |
| 8 | schedules, periods and the ledger; `mason status`, `due`, `overdue`, `history`, `skip`, `schedule set` | what is done, due and overdue, across projects |
| 9 | `mason run-due` and `mason schedule install`; consolidation inputs | one heartbeat runs everything on its schedule |
| 10 | plugin packages | only if a workflow needs its own code |

## 12. Open questions

- **Detectors.** Should a skill be able to name only built-in detectors, or should plugins (§6.3)
  add them?
- **Ordering between workflows.** `dependency-upgrades` reads `dependency-assessment`'s issue.
  Should a skill declare the workflows it depends on, so enabling one without the other is a
  `mason doctor` error?
- **Codex.** Codex doesn't load skills from an added directory, so the per-run skills directory
  (§4) doesn't scope its sessions. Naming the skill's file in the prompt still works.
- **Notes shared within a team.** A note is one developer's. When several people work on one
  analysis, is publishing it (an issue or discussion) enough, or does the team want a shared
  store?
- **Status across machines.** The ledger is local, so `mason status` on a laptop doesn't see a
  server's runs. Sharing results (below) would share the ledger too; until then, should status
  also check GitHub for a period's published output, as `packaging.md` principle 6 does?
- **Where schedules are written.** `mason schedule set` has to write TOML, and the standard
  library only reads it. Keeping comments in the settings file would need a dependency such as
  `tomlkit`; a separate `schedules.toml` that only mason writes would not.
- **Results across machines.** The master copy lives on the machine that ran the workflow, so a
  result saved on a server isn't on the developer's laptop. Should the results directory be a git
  repository per developer, pushed to a private remote, so it has a history, a backup and one
  master across machines?
- **Grant storage.** Are grants best in the project settings file, or in a separate lock file that
  `mason doctor --approve` writes, so a grant is never hand-edited?
