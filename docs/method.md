---
status: draft
updated: 2026-11-11
---

# Method

A workflow's value is in its checks: the ones that stop a wrong conclusion before it becomes a
PR. Most of them were learned from a mistake that looked reasonable at the time. This page lists
them with the reason for each, so a skill that implements them can be changed on purpose. A skill's `SKILL.md` is the operational form; this page is the
why.

## `dependency-upgrades`

- **Classify in this order:** already fixed → no upstream fix → **EOL line** → blocked by a
  parent's range → major bump of a direct dependency → easy. The wrong classification is the main
  risk, and the first match wins.
- **Check every alert for an EOL line.** Compare the installed release *line* against every
  `vulnerable_version_range` in the advisory. A line in no range was never patched: it is
  end-of-life, not "fixed in X". In Dependabot's UI the two look the same, for example a Django
  5.1 install with an advisory fixed only in 5.2.x. But they need different follow-up: an EOL
  line keeps generating alerts until the line changes.
- **Read the version that is actually installed** from the compiled requirements and lockfiles
  (every nested path for npm), not the alert's version field, which isn't always present.
- **Search for existing tracked work** before describing an EOL line or a major bump as new, and
  report how stale it is. Otherwise a stalled upgrade is re-described as new work every pass.
- **One commit per package**, so each one can be reverted alone.
- **Derive the tables from the resolved diff**, not the alert list. One bump can close another
  alert for free (upgrading `postcss` can move `nanoid`), and an alert can already be fixed
  before the change, in which case the change shouldn't take the credit.
- **npm:** for a direct dependency, edit `package.json` and run `npm install --package-lock-only`.
  For a transitive one, run `npm update <pkg> --package-lock-only`. If the version doesn't move,
  a parent's range is blocking it.
- **Check the tools before changing anything.** Recompile every requirements file with no
  source changed: a working toolchain reproduces them exactly. A broken virtualenv, a tool that
  won't import, or a different tool version all show up here, rather than as a half-done run.
- **Check the result of a compile, not its exit status.** A wrapper script can swallow a failed
  `pip-compile` and exit 0, leaving the source edited and the compiled file unchanged. The new
  version must be in the compiled file's diff.
- **Move a transitive pip package without pinning it** (`--upgrade-package`). A pin added to
  force one upgrade stays long after its reason has gone.
- **A run's worktree doesn't run a developer's checkout hooks.** They are set up for that
  developer's checkouts, and one that fails makes the run fail. The project's commit hooks
  still run.
- **Read the advisory's patched versions line by line.** The EOL question is whether any
  patched version is in the installed release line, not whether the vulnerable range covers it
  (it always does: that is why there is an alert).
- **Never claim a check ran when it didn't.**

## `dependency-retrospective`

- **Judge outcomes, not opinions.** A merged PR whose alerts close, a CI failure, an edit before
  merge, a revert: these are what the retrospective learns from. A reviewer's passing comment is
  context for one of them, not evidence on its own.
- **Find where the time went** before saying alerts are fixed too slowly. The fixes are
  different: run more often, review faster, plan the hard upgrade, or accept that the work
  happens elsewhere.
- **The evidence bar for changing the skill:** the same problem in at least two PRs, or one
  revert or CI failure clearly caused by the method. Anything less goes in the report, not the
  skill. A skill that changes on every complaint stops being predictable.
- **Recurrence is the signal.** A finding that was in the last retrospective and is still true
  matters more than a new one, so it is marked as recurring and raised in severity.
- **Abandonment is a judgement from several signals:** no release for a long time, a
  deprecation notice, an archived repository, alerts that never get a fix. Any one alone can be
  a finished, stable package. The report shows the signals and the options; a person decides.
- **Collect with a script; judge with the agent.** The agent never counts or dates anything, so
  the numbers in the report can be trusted and compared across retrospectives.

## `code-review`

- **Invariant-driven, not narrative-driven.** Trace the real cascades, call sites and argument
  guards, rather than judging whether the PR's rationale sounds right. This matters most when
  the same agent wrote the change.
- **Don't report** pre-existing issues, issues a linter catches, or speculation without evidence
  in the diff. Each costs the reader's attention and teaches them to skim.
- **From Warp's `cloud-factory-demo`:**
  - The **annotated diff** (`[OLD:n]`, `[NEW:m]`, `[OLD:n,NEW:m]`) is the only source of line
    numbers, because GitHub rejects a review whose comment sits on a line the diff doesn't show.
  - The agent runs a validator on its own output, and fixes what it reports.
  - **Follow-up context:** the previous review, replies since, and the delta since its head, so
    a follow-up reports what happened to each finding instead of starting again.
  - Reviews are bound to a head SHA, and there is one per head.
  - Suggestion blocks are checked for duplicated context lines.
- **The trust boundary.** A review reads code someone else wrote. It never runs it, never
  checks it out into a working directory, and treats the PR's text as data. Unattended, its
  permissions include no build, test or package tools.
- **No verdict.** The review is a comment. Approving or blocking is a person's decision, or
  another step's under a policy.

## `code-review-retrospective`

- **Acted on is the strongest signal:** the flagged lines changed in a later commit, or a
  follow-up found the finding addressed. A reply saying "good catch" is weaker; silence is no
  signal at all.
- **A category rarely acted on is either wrong or not worth reading.** The corrected findings in
  it say which. Both are fixed in the skill, but differently: a wrong check is removed, a
  low-value one is demoted to a nit or dropped.
- **Shared skill, project rules.** A correction that holds anywhere changes the shared review
  skill. A correction that is this project's choice becomes a rule in the project's own
  knowledge, so the shared skill doesn't absorb one project's conventions.

## `sentry-errors`

- **Filter by environment first.** A Sentry issue groups events from every environment, and an
  error staging sees is a different matter from one production sees.
- **Check the release for every issue.** Is its commit on the main branch (errors from branch
  deploys aren't main-branch bugs)? Has the code changed since? Is a fix already merged and
  waiting for a deploy? Watch for deploy skew.
- **Grouping signals, strongest first:** the same mechanism in different places; the same
  message once the variable parts are removed; one failure logged twice; the same external
  host; the same release first seen (framework upgrades re-fingerprint existing errors); the
  same culprit. Performance issues group by the repeated query, not by URL.
- **Period counts are not lifetime counts,** and a performance issue's count is a sample once
  the span quota runs out.
- **Answer "how did this get through?"** for each group, naming the step that missed it. That
  is how a fix to the process gets proposed, not just a fix to the code.
- **A test that reproduces the error, then the fix.** A fix without one can't be told apart
  from a fix that silences the symptom.
- **Diagnostics gaps are worth fixing at the source:** f-string log messages that fragment
  grouping, an `ERROR` on every retry, retrying a 401 or 403, `RetryError` without
  `reraise=True`, a key fact truncated away, a missing `exc_info`.
- **Archive modes:** `untilEscalating` for noise; `forever` only for a confirmed false positive.
  Resolving is cheap, because Sentry reopens a regression. All of these are a person's.
- **Personal data stays in Sentry.** The scripts strip it before the agent sees an event, and
  nothing from an event goes into GitHub beyond what explains the bug.
- **One run, one PR, but not one open PR.** Fixes for different groups don't overlap, so they
  may be open together; a group already in an open PR or triage issue is never reported twice.

## `sentry-retrospective`

- **The signal is the point.** A fix predicts its errors will stop. When they don't, the
  diagnosis was wrong somewhere, and that is the most useful thing to learn from.
- **Not deployed is not failed.** An event from a release older than the fix says nothing about
  the fix.
- **Fix the class, not the instance.** The same kind of error in different places is a missing
  rule for writing code, a test pattern, or a review check, and the project's own knowledge is
  where it goes.

## Across all workflows

- **Autonomy levels:** unattended, confirm first, or person only (`design.md` §6). A PR's review
  is its confirmation.
- **Mark every claim** *verified* (checked against code or data) or *inferred*.
- **Keep timeless rules apart from facts with a lifecycle.** Timeless rules go in skills. Facts
  with a lifecycle go in project memory, with a status and a date; graduate them or delete them.
