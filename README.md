# mason

Agent workflows shared across repositories. Each workflow is a skill that does one job and
writes structured artifacts, so steps compose. A project picks how much to automate by picking
which steps a person does.

| Workflow | Source | Output |
|---|---|---|
| `dependency-upgrades` | Dependabot alerts | one PR, one commit per package, with derived package and alert tables. It never merges: the PR is the hand-off |
| `code-review` | a pull request | one consolidated review, posted as a comment with its findings embedded; follow-ups report what happened to each earlier finding |
| `code-review-retrospective` | what happened to the review findings over a period | a report issue: which categories people act on; a PR to this repo when the review skill should change; proposed rules for the project |
| `dependency-retrospective` | what happened to the upgrade PRs and alerts over a period | a report issue: time to fix against targets, where the time went, stale or abandoned packages; and a PR to this repo when the upgrade skill should change |

## Set up

You need, on the machine that runs the workflow:

- Python 3.12 or later, git, and [Claude Code](https://docs.anthropic.com/en/docs/claude-code).
- `gh`, logged in as someone who can read the project's Dependabot alerts and push branches.
  Check with `gh api 'repos/<owner>/<repo>/dependabot/alerts?per_page=1'`.
- Whatever the project needs to recompile its requirements and build: pip-tools for
  pip-compile files, npm for lockfiles.

Then:

1. **Clone this repository** once, anywhere. Every project uses the same checkout; nothing is
   copied into projects.
   ```sh
   git clone <this repository> path/to/mason
   ```
2. **Check what it detects in the project.** No configuration file is needed. This prints what
   was found (the GitHub repo, main branch, requirements files, npm lockfiles, CI) and where
   each value came from:
   ```sh
   python3 -I path/to/mason/bin/artifact config path/to/<project> --section dependency-upgrades
   ```
   To change a value, `--toml` prints a starting `.agents/automation.toml` for the project:
   uncomment the lines to change, and commit the file there. `docs/config.example.toml` has every
   key.
3. **Create the label** the workflow's PRs carry. It is how open agent PRs are counted:
   ```sh
   gh label create automation --repo <owner>/<repo> --description 'Opened by an agent workflow'
   ```

## Run it

**Interactively,** from the project, with this repository added:

```sh
cd path/to/<project>
claude --add-dir path/to/mason
```

Then ask for "run the dependency upgrades", "review PR #42", or a retrospective ("run the
dependency retrospective", "run the code review retrospective"). The upgrade run tells you what
it detected, asks before anything
that needs confirmation (running tests that can't share a machine, opening issues), and ends
with the PR link and a short report. Each run's files (alerts, findings, the PR body, a
worktree) are kept under `runs/<project>/`.

**Unattended,** for a scheduler or another agent:

```sh
path/to/mason/bin/unattended dependency-upgrades path/to/<project>
```

A workflow that acts on one pull request takes it with `--pr`:

```sh
path/to/mason/bin/unattended code-review path/to/<project> --pr 42
```

This runs Claude Code headless from the project. The run:
- **Asks nothing.** Everything that needed a person is listed in its report.
- **Uses only the tools the workflow needs.** Anything else is refused, and merging is always
  refused.
- **Leaves the tests to CI** when they can't run locally, and opens a draft PR if nothing can
  verify the change.
- **Logs** the session to `runs/<project>/`. `--dry-run` prints the command without running
  it, and `--allow 'Bash(<command> *)'` adds a tool the project needs.

Only one run per project happens at a time. A run that finds nothing to upgrade opens no PR.
Give the `gh` login no more than it needs: reading alerts, pushing branches, opening PRs.

An interactive Claude Code sign-in expires, so a scheduled job needs a long-lived token: run
`claude setup-token` and give the job the token it prints, as `CLAUDE_CODE_OAUTH_TOKEN`.

**What a run leaves on GitHub.** Each run opens at most one PR, and never adds to an open one.
The PR ends with the run that opened it: its id, how it was started and by whom, the host and
the time. A run that finds an upgrade PR still open leaves that PR unchanged. It adds a comment
saying so, listing the open alerts the PR doesn't cover. Either way, you can see who or what
started each run without logging in to the machine that ran it.

Pass `--trigger` to say what started it. The PR, or the comment left on an open PR, shows it
along with the run's id and host:

```sh
bin/unattended dependency-upgrades path/to/<project> --trigger 'systemd timer on factory-1'
```

Any scheduler can call it. For example, a systemd timer, hourly:

```ini
# ~/.config/systemd/user/dependency-upgrades.service
[Unit]
Description=Dependency upgrades for <project>

[Service]
Type=oneshot
# CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) and GH_TOKEN, readable only by you.
EnvironmentFile=%h/.config/automation/env
ExecStart=path/to/mason/bin/unattended dependency-upgrades path/to/<project> --trigger 'systemd timer dependency-upgrades.timer'

# ~/.config/systemd/user/dependency-upgrades.timer
[Unit]
Description=Hourly dependency upgrades for <project>

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

Enable it with `systemctl --user enable --now dependency-upgrades.timer`. The retrospective is
set up the same way, run less often (`OnCalendar=weekly` or `monthly`), with
`bin/unattended dependency-retrospective`. Each one continues from the last. The run's log is
`journalctl --user -u dependency-upgrades`, plus the session log under `runs/`. Everything a
reviewer needs is on GitHub.

## Layout

```
skills/<name>/SKILL.md      one skill per workflow
schemas/                    the artifact contracts, with examples
lib/artifacts/              validators, renderers, embedding, config, locking, the dependency
                            readers, outcome collection, and the unattended runner
bin/artifact                the command line skills call (`--help` lists them all): config,
                            run-start, lock, validate, the collectors (alerts, deps-diff,
                            alerts-closed, review-context, collect-outcomes,
                            collect-review-outcomes), the renderers, annotate-diff, extract
bin/unattended              start a workflow headless, for schedulers and other agents
tests/                      stdlib unittest; the schema pages' examples are tests too
docs/design.md              principles, steps and hand-offs, autonomy, distribution
docs/method.md              the checks the workflow makes, and why
docs/config.example.toml    a project's .agents/automation.toml with every key set
runs/                       per-run worktrees, artifacts and logs (gitignored)
```

## What stays in each project

- Its `AGENTS.md`, which the skill reads for the project's own rules.
- Optionally, `.agents/automation.toml`, for any value that differs from what is detected.
