---
name: sentry-retrospective
description: 'This skill should be used when asked to "run the Sentry retrospective", "did the Sentry fixes work", "are the errors coming back" or "how are we doing on Sentry errors" in a project, interactively or unattended (from a scheduler or another agent). It is the outer loop over sentry-errors: a script checks, for every fix PR, whether its Sentry issues stopped after the merge, and collects the triage issues and what is still unresolved. This skill judges why fixes didn''t work, what keeps recurring, and what the project should change so the same class of error doesn''t ship again. It opens one report issue, and proposes skill changes as a PR when the evidence warrants it.'
version: 1.0.1
---

# Sentry retrospective

The outer loop over `sentry-errors`. That skill fixes one error group per run and predicts that
its errors will stop. This one checks the prediction, and asks three questions:

1. **The skill:** when a fix didn't stop its errors, why? A wrong root cause, a group that was
   really two, or a fix that silenced one symptom: each is a lesson for the diagnosis method.
2. **The project:** which classes of error keep shipping, and what would stop them at the
   source: a rule for writing code, a test pattern, a check in review?
3. **Process:** are triage issues acted on, and are fix PRs merged while they're still useful?

The facts come from a script (`artifact collect-sentry-outcomes`), which reads each fixed
issue's last event from Sentry. This skill judges them. The report is a GitHub issue with the
data embedded, so each retrospective continues from the last.

**Paths.** `$HUB` is the mason repository: two directories above this file, once symlinks are
resolved (a skill may be reached through a link to it). `$PROJECT` is the project root: the working
directory when launched from the project, or the directory named in the request. `artifact` below
means `uv run --project $HUB --locked python -I $HUB/bin/artifact`. `RUN` is the run directory,
printed in Step 1.3. These are names for paths, not shell variables: write each path out in full in
commands. Unattended, a command that uses or assigns a shell variable, or redirects output with `>`,
is refused; every command here that saves a file does it with its own option.

**Modes and privacy.** As in `sentry-errors`: interactive by default, unattended when the
request says so or `MASON_UNATTENDED=1` is set; Sentry is read only through `artifact`;
no personal data goes into GitHub.

## Autonomy

| Action | Level |
|---|---|
| Collect outcomes; read PRs, issues and Sentry | Unattended |
| Open the report issue | Unattended: the report is the point |
| Open a PR to this repository proposing a skill change | Unattended: a person merges it |
| Change the project's rules, or anything in Sentry | Confirm first; unattended, the report proposes it |
| Merge anything | Never |

## Step 1: Set up the run

1. `artifact config $PROJECT --section sentry-retrospective` and
   `artifact config $PROJECT --section sentry-errors`; stop if either is refused.
2. `artifact lock acquire $PROJECT sentry-retrospective`, and stop if it is refused. Release it
   in Step 7 on every path out.
3. `artifact run-start $PROJECT sentry-retrospective`. `RUN` is the printed `dir`.

## Step 2: Choose the period

Continue from the last retrospective:

```sh
gh issue list --repo <repo.name> --label automation --state all \
  --search 'in:title "Sentry retrospective"' --json number,title,body --limit 1
```

If there is one, pipe its body into `artifact extract --schema sentry-outcomes/1`: its
`period.until` is this run's `--since`, and its findings are the previous findings. Otherwise
leave `--since` out.

## Step 3: Collect the outcomes

```sh
artifact collect-sentry-outcomes $PROJECT --run $RUN/run.json [--since <until of the last one>] -o $RUN/outcomes.json
```

Read `$HUB/schemas/sentry-outcomes.md` for what each field means.

## Step 4: Judge the outcomes

### The skill: why didn't a fix work?

For each PR whose signal is `not_met`, find out why. Read the fix (`gh pr view <N>`), and the
issue's latest event since the fix (`artifact sentry-event $PROJECT <short id> -o …`):
- **wrong root cause:** the error comes from somewhere the fix didn't touch;
- **a group that was really two:** some events stopped, others kept coming;
- **a symptom silenced:** the error moved, or became a different one;
- **not deployed:** the last event's release predates the fix. That is not a failed fix; say
  so, and leave the skill alone.

Also read edited and closed fix PRs (`edited`, `comments`): what did people change, and why?

A finding here is a `skill_change` to `$HUB/skills/sentry-errors/SKILL.md` or the Sentry section
of `$HUB/docs/method.md`. **The evidence bar:** the same problem in at least two fixes, or one
fix whose failure the method clearly caused. It must hold for any project.

### The project: what keeps shipping?

Read this period's findings, from the fix PRs and the triage issues, across runs:
- **The same class of error, in different places:** a missing guard pattern, an unchecked
  lookup, an N+1 in the same kind of view. The fix belongs at the source: a rule for writing
  code in the project's own conventions or knowledge, or `AGENTS.md`; a test pattern; a check its review
  should make. Write it as an `investigate` finding, `fingerprint: rules/<slug>`, giving the
  rule as it should be written, and the fixes that show it.
- **Diagnostics gaps that keep costing time:** errors that were hard to diagnose because of how
  they are logged.

### Process: is the work moving?

- Triage issues still open from earlier periods, and what is in them.
- Fix PRs that are `stale`, or merged long after they were opened.
- `unresolved_now`, by environment, against the last retrospective's.

### Compare with the last retrospective

A fingerprint in the previous retrospective's findings that is still true is recurring: say so
("also in #<issue>"), and raise its severity by one step.

## Step 5: Write the findings and open the report

Write each as a `finding/1` in `$RUN/findings/`, with `source.process` set to
`sentry-retrospective`, refs and evidence as `#<PR>`, `#<issue>` or Sentry short ids. Validate
each. Then:

```sh
artifact render-sentry-retrospective $RUN/outcomes.json --run $RUN/run.json \
  --findings $RUN/findings/*.json -o $RUN/report.md
gh issue create --repo <repo.name> --label automation \
  --title 'Sentry retrospective: <period.since date> to <period.until date>' --body-file $RUN/report.md
```

## Step 6: Propose skill changes

Only for `skill_change` findings, and only if this repository has a GitHub remote. The procedure
is the dependency retrospective's Step 6 (`$HUB/skills/dependency-retrospective/SKILL.md`), with
this skill's name and `$HUB/skills/sentry-errors/SKILL.md` as the file changed.

## Step 7: Report, release the lock

Give the report issue's link; how many fixes worked, didn't, and are still being watched; each
finding by area; the skill-change PR link, or why there is none; and **Needs a person**: the
project rules proposed, and triage issues left open. Then, on every path: remove `$RUN/hub` if
it was created, and run `artifact lock release $PROJECT sentry-retrospective`.

## Do not

- Decide whether a fix worked yourself: the signal in `outcomes.json` does that.
- Count a fix that wasn't deployed yet as a failure.
- Copy personal data from events into the report.
- Change anything in Sentry, or reopen, comment on or merge the fix PRs.
