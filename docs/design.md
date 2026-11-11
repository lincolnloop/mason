---
status: draft
updated: 2026-11-11
---

# Design

## 1. Why dependency upgrades first

A workflow is worth automating when its output is easy to judge. A dependency upgrade's output
is a pull request with a clear before and after:
- which packages moved, from what version to what;
- which security alerts close;
- which checks passed.

A person can decide it in minutes. Other workflows are added once they can produce output that
is as easy to decide.

## 2. Principles

1. **One skill does one thing, and skills compose.** As in Warp's factory
   (`warpdotdev-demos/cloud-factory-demo`), and in the Unix tradition, each step is a skill
   that does one job and writes structured artifacts for the next. Opening a PR, deciding
   whether to merge it, and reporting on what was merged are separate skills, so a project
   chooses how much to automate by choosing which steps a person does.
2. **The unit a person decides on is a pull request.** Merge, edit then merge, or close with a
   reason. A diff shows what adopting something means; a prose proposal only describes it.
3. **Structured artifacts are the interface,** between agents and between an agent and a person.
   Schemas live in `schemas/`, and every artifact is checked by a script before anything acts on
   it.
4. **Derive, don't ask the model to write it twice.** Package tables, alert counts and their
   totals are computed from the repository and the alert list, not written by the model. A
   value written in two places can disagree with itself.
5. **Fail closed, and tell the agent why.** A validator rejects a bad artifact with a message the
   agent can act on. It never silently rewrites the artifact.
6. **Say how we'll know it worked.** Every change declares a success signal that can be checked
   after the merge. For an upgrade, the signal is that the listed alerts close.
7. **Cap the input, not the decisions.** Proposals become PRs, so the brake is a limit on open
   agent PRs (§6).
8. **Surface information where people already look.** Workflows may run on a remote server
   nobody logs in to. So everything a person needs goes to GitHub, on the PR or a comment on it,
   and the machine keeps only working files and logs:
   - what changed;
   - who or what started a run;
   - why a run did nothing.

   Each run is accountable: one run, at most one PR, and a record of every run that opened or
   stopped on one (`schemas/run.md`).

## 3. Artifacts

Each artifact is embedded in the PR it belongs to, in a collapsed `<details>` block in the body,
so GitHub is the archive. `artifact extract` reads them back out.

### 3.1 `finding` (`schemas/finding.md`)

One per flagged package:

- **Identity:** `id` and a stable `fingerprint` (`deps/<ecosystem>/<package>`).
- **Source:** the alert numbers.
- **Content:** title, claim, evidence (each item marked `verified` or `inferred`), severity, and
  the package's classification.

`kind` decides what happens next:

- `dependency` (an easy upgrade) goes into the PR.
- `investigate` (an EOL line, a major bump, a blocked range) is listed in the PR under "Not in
  this PR", with a link to tracked work, or a proposed issue if there is none.
- `external` (no upstream fix yet) is listed the same way.

### 3.2 `change` (`schemas/change.md`)

One per PR:

- the findings it addresses, and a `summary`;
- `effect`: what changes, in a sentence or two;
- `blast_radius`;
- `verified` and `not_verified`: the checks run, with results, and what wasn't checked;
- **`success_signal`**: something checkable, plus `check_after_days`;
- `revert`;
- the derived `dependencies` and `alerts`, from `deps-diff` and `alerts-closed`.

`render-change` turns the change and its findings into the PR body.

### 3.3 `run` (`schemas/run.md`)

One per run, written when it starts: its id, `interactive` or `unattended`, who or what
started it, the host, and the time. It is published with its outcome: embedded in the PR the
run opened, or in a comment on the open PR it stopped for, listing the alerts that PR doesn't
cover.

## 4. Steps and hand-offs

| Step | Skill | Reads | Writes | Status |
|---|---|---|---|---|
| Upgrade | `dependency-upgrades` | Dependabot alerts, the project | a PR with `change`, `finding`s and `run` embedded | built |
| Decide | a merge skill | the PR's embedded `change`, its CI status, the project's merge policy | merged, or a comment saying why not | not built |
| Learn | `dependency-retrospective` | `outcomes` collected from the upgrade PRs, the alert history and the package registries | a report issue; a PR to this repo proposing skill changes | built |
| Report | a report skill | the same `outcomes` | a summary for engineers or clients | not built |
| Review | `code-review` | one PR: its diff annotated with coordinates, the previous review, replies since | `review` and one posted review, a `COMMENT` | built |
| Fix | `sentry-errors` | one environment's unresolved Sentry issues, without personal data | a fix PR for one error group, with `change` and `finding`s embedded; or a triage issue | built |
| Learn | `sentry-retrospective` | `sentry-outcomes`: whether each fix's errors stopped after the merge | a report issue; a PR to this repo proposing skill changes; proposed project rules | built |
| Learn | `code-review-retrospective` | `review-outcomes`: what happened to each posted finding | a report issue; a PR to this repo proposing review-skill changes; proposed project rules | built |

The PR is the hand-off between steps. Everything the next step needs is in it, in a form a
script can read:
- the derived package table, with major version changes marked;
- the alerts it closes;
- each check's result: `pass` (ran here), `ci` (runs on the PR) or `skipped`;
- a draft PR when a check couldn't run anywhere.

So the decide step can be a person, or an agent applying a policy (patch-only, CI green, no
`skipped` check, nothing but manifests touched), without either needing to re-read the run.

### 4.1 `dependency-upgrades`

`skills/dependency-upgrades/SKILL.md` is the procedure. `docs/method.md` gives the reasons for
its checks. In outline:

1. Resolve the project's configuration, check the open-PR limit, and make a worktree.
2. Pull the open Dependabot alerts.
3. Classify each flagged package against what is actually installed: already fixed, no fix,
   EOL line, blocked, major bump, or easy. Search for existing tracked work.
4. Write a `finding` per package.
5. Apply the easy upgrades, one commit per package, then build and test.
6. Derive the package and alert tables, write the `change`, and open one PR. Never merge.

### 4.2 The improvement loop

`dependency-upgrades` is the inner loop: it runs often and opens PRs. `dependency-retrospective`
is the outer loop, as in Warp's factory, where a daily job learns from people's reactions to
automated reviews and proposes changes to the review skill. It runs less often (weekly or
monthly), and it learns from outcomes rather than opinions:
- **Process:** time to fix by severity against the project's targets, and where the time went
  (no run, a PR waiting for review, a package that wasn't easy, or fixed some other way).
- **The skill:** CI failures, edits before merge, closed PRs and reverts on its PRs. A pattern
  across PRs becomes a proposed change to the skill, as a PR to this repository that a person
  merges. That is how the method in `method.md` keeps improving.
- **The project:** stale, deprecated or archived packages, and packages that keep producing
  alerts.

The facts are collected by a script (`artifact collect-outcomes`, `schemas/outcomes.md`), and
the agent only judges them. The report is a GitHub issue with the outcomes and findings
embedded, so each retrospective continues from the last, and a recurring finding is visible as
recurring.

### 4.3 Running it

- **Interactively:** from the project, `claude --add-dir path/to/mason`, then "run the
  dependency upgrades". The skill asks before anything marked *confirm first*.
- **Unattended:** `bin/unattended dependency-upgrades <project>`, for a scheduler or another
  agent. It runs Claude Code headless with `AUTOMATION_UNATTENDED=1`, and with permissions that
  allow only what the workflow needs. Anything else is refused, not asked about. The skill asks
  nothing. It lists what needed a person in its report, leaves the tests to CI when it can't
  run them locally (`verify.ci`), and opens a draft when it can't verify at all.
- **One run at a time per project.** `artifact lock` holds a lock under `runs/` for the length
  of a run, so overlapping triggers can't race.
- **One run, one PR.** A run never adds to an open upgrade PR, because a reviewer part-way
  through it wouldn't notice a new commit. A run that finds one open stops, and comments on it
  with its run record and the open alerts that PR doesn't cover. So a manual run started while
  an unattended run's PR is waiting leaves its own trace, and each PR's history is its own run's.
- **Every run is identified:** an id, `interactive` or `unattended`, who or what started it
  (`bin/unattended --trigger`, or the GitHub login), the host, and the time. The PR ends with
  it, and embeds it as `run/1`.

How often to trigger is a project's choice. Vulnerabilities are acted on fastest when a run
starts soon after an alert opens. The cost of a run with nothing to do is small: it opens no PR.

## 5. Configuration

Skills get every project value from `artifact config <project>`, never from their own text. It
resolves each key in turn from:

1. **The project's `.agents/automation.toml`, if it sets the key.** The file is optional. An
   empty list counts as set, so `npm_projects = []` turns npm off.
2. **The repository, where it says:**
   - `repo.name` from the GitHub origin remote;
   - `main_branch` from origin/HEAD;
   - pip from tracked `.txt` files with a pip-compile or uv header (sources beside them, and the
     compile command from the header);
   - npm from tracked `package-lock.json` files, with `npm run build` where a `build` script
     exists;
   - `worktree_copy` from an untracked, ignored `.env`;
   - `verify.ci` from `.circleci/config.yml` or `.github/workflows/`.
3. **A default, where one is safe:**
   - three open agent PRs;
   - 14 days before stale;
   - `verify.exclusive = true`: nothing says whether two worktrees can run the tests at once,
     so the skill asks, and asking costs one confirmation.

So the skill runs in a new repository without any setup. The file is added, one key at a time,
once a value needs to differ.

`artifact config --toml` prints the resolved values as a starting file. Detected and default
values are commented out and labelled with where they came from, and uncommenting a line pins
that value. The JSON output has a `sources` map, so the skill can tell the person what it
assumed.

The test command has no safe guess. If it isn't configured, the agent takes it from the
project's `AGENTS.md` and records in the change that it chose it rather than read it from config.

Within the file every key is checked and unknown keys are errors, so a typo is refused rather
than silently replaced by a detected value. `docs/config.example.toml` has every key, filled in
for an example project.

## 6. Autonomy

| Action | Level |
|---|---|
| Read anything; write artifacts | Unattended |
| Commit upgrades and open the PR, within the open-PR limit | Unattended: the PR is the hand-off |
| File an issue; create a label; force a dependency past a parent's range | Confirm first; unattended runs list them for a person instead |
| Open a retrospective's report issue, or its PR proposing a skill change | Unattended: a person reads the report and merges the PR, or doesn't |
| Post a review of a PR, as a `COMMENT` | Unattended when asked to review it; interactive, confirm first |
| Approve or request changes on a PR | Never: a person, or a merge step under a policy |
| Archive, resolve or assign Sentry issues; fix data | Confirm first; unattended runs list them in a triage issue |
| Merge | Not the upgrade skill's job: a person, or a separate merge step under the project's policy |
| Dismiss an alert; change this repo's skills | Person only |

## 7. Distribution: a star, with this repo at the centre

There is one checkout of this repo. Projects around it are reached with `--add-dir`, so no project
holds a copy of the workflow skills. Facts from the Claude Code docs (checked 2026-11-10):

- **`@` references don't work for this.** A `@` reference to a directory adds only a file
  listing and doesn't grant access. The working mechanism is `--add-dir` (or `/add-dir`). Files
  there follow the normal permission rules, and a Bash `cd` into the directory persists.
- **What loads from an added directory:**
  - `.claude/skills/`, `.claude/commands/` and `.claude/agents/` always;
  - `CLAUDE.md` only when `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1` is set;
  - never `AGENTS.md` (make `CLAUDE.md` a symlink to it), hooks, `.mcp.json` or most settings.
    Hooks and MCP servers come only from the primary working directory.
- **Other tools:** Codex has `--add-dir` (write access) and `--cd`. Gemini has
  `--include-directories`. Whether they load skills from added directories isn't verified.

Launch from the project, so its own CLAUDE.md, skills, hooks, MCP servers and permissions all
apply, with this repo's skills added:

```sh
cd path/to/<project>
claude --add-dir path/to/mason
```

Consequences:

- This repo exposes its skills at `.claude/skills/`, a symlink to `skills/`, because that is the
  path Claude Code discovers.
- **No vendoring.** Skills call the shared code in `lib/` by path, relative to the skill
  directory.
- **Every skill begins by resolving its target project.** It runs `artifact config`, reads the
  project's `AGENTS.md`/`CLAUDE.md` explicitly rather than trusting that it was loaded, and runs
  commands from the project root.
- **Concurrent runs.** A workflow that branches and commits must not do it in a checkout a
  developer is using, so it uses a git worktree of the project. Projects whose tests use docker
  compose with shared named volumes must not run tests in two worktrees at once.
  `verify.exclusive` makes the skill ask before it runs them.
- **A version is a commit of this repo,** not a copy in each project. Each artifact records this
  repo's commit SHA, so it's clear which version of the skill produced it.
