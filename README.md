# mason

Agent workflows shared across repositories. Each workflow is a skill that does one job and
writes structured artifacts, so steps compose. A project picks how much to automate by picking
which steps a person does.

| Workflow | Source | Output |
|---|---|---|
| `dependency-upgrades` | Dependabot alerts | one PR, one commit per package, with derived package and alert tables. It never merges: the PR is the hand-off |
| `code-review` | a pull request | one consolidated review, posted as a comment with its findings embedded; follow-ups report what happened to each earlier finding |
| `code-review-retrospective` | what happened to the review findings over a period | a report issue: which categories people act on; a PR to this repo when the review skill should change; proposed rules for the project |
| `sentry-errors` | one environment's unresolved Sentry issues | one PR per run fixing one error group, with a regression test; a triage issue for what needs a person |
| `sentry-retrospective` | whether the Sentry fixes made their errors stop | a report issue: fixes that didn't work and why, recurring classes of error, rules for the project; a PR to this repo when the skill should change |
| `dependency-retrospective` | what happened to the upgrade PRs and alerts over a period | a report issue: time to fix against targets, where the time went, stale or abandoned packages; and a PR to this repo when the upgrade skill should change |

## Set up

You need, on the machine that runs the workflow:

- Python 3.12 or later, git, and an agent: [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
  or [Codex](https://developers.openai.com/codex). Unattended runs need Claude Code for now.
- `gh`, logged in as someone who can read the project's Dependabot alerts and push branches.
  Check with `gh api 'repos/<owner>/<repo>/dependabot/alerts?per_page=1'`.
- Whatever the project needs to recompile its requirements and build: pip-tools for
  pip-compile files, npm for lockfiles.
- For the Sentry workflows: a Sentry token with only the `event:read` scope, as
  `SENTRY_AUTH_TOKEN`. Use a personal auth token, or an internal integration with read access
  to issues and events; organization auth tokens are for CI and aren't documented to reach these
  endpoints. You also need the project's `[sentry-errors]` section
  (`sentry_org` and `sentry_project`, which can't be detected).

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

With **Codex**, link the skills once, into `~/.agents/skills`, where Codex finds them from any
project, then launch from the project with this repository added, so the run can write its
files under `runs/`:

```sh
path/to/mason/bin/link-skills
cd path/to/<project>
codex --add-dir path/to/mason
```

`bin/link-skills` makes one symlink per skill, so a `git pull` here updates them; run it again
after a new skill is added, and `--remove` takes the links away. It leaves alone anything
already at a skill's name. A project's own skill with the same name as one here (`code-review`
is a likely one) is listed beside it, not replaced: name the one you mean (`$code-review` in
Codex shows both), or use the next way in.

**Any other agent,** or to be sure which skill runs: name the skill's file.
"Read path/to/mason/skills/dependency-upgrades/SKILL.md and follow it for this project." The
skills find this repository and the project by path, so nothing else needs installing.

Then ask for "run the dependency upgrades", "review PR #42", "fix the Sentry errors", or a
retrospective ("run the dependency retrospective", "run the code review retrospective", "run
the Sentry retrospective"). The upgrade run tells you what
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

This runs Claude Code headless from the project (Codex isn't supported here yet). The run:
- **Asks nothing.** Everything that needed a person is listed in its report.
- **Uses only the tools the workflow needs.** Anything else is refused, and merging is always
  refused.
- **Leaves the tests to CI** when they can't run locally, and opens a draft PR if nothing can
  verify the change.
- **Logs** the session to `runs/<project>/`. `--dry-run` prints the command without running
  it, and `--allow 'Bash(<command> *)'` adds a tool the project needs.

Only one run per project happens at a time. A run that finds nothing to upgrade opens no PR.
Give the `gh` login no more than it needs: reading alerts, pushing branches, opening PRs.

**Tokens** live in one file outside every repository, `~/.config/mason/env`, as `KEY=value`
lines with `chmod 600`. The systemd unit reads it with `EnvironmentFile`; for an interactive run,
load it only for that session: `(set -a; . ~/.config/mason/env; set +a; claude --add-dir …)`.
Never put a token in `.agents/automation.toml` (it is committed) or in the project's `.env`
(it is copied into every run's worktree). Anything in the session's environment can be read by
the session, so give each token only the scopes the workflows use.

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
# KEY=value lines, readable only by you (chmod 600): CLAUDE_CODE_OAUTH_TOKEN (from
# `claude setup-token`), GH_TOKEN, and SENTRY_AUTH_TOKEN for the Sentry workflows.
EnvironmentFile=%h/.config/mason/env
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
                            alerts-closed, review-context, sentry-issues, sentry-event,
                            open-findings, collect-*-outcomes), the renderers, annotate-diff,
                            extract
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
