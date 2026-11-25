# mason

Agent workflows shared across repositories. Each workflow is a skill that does one job and
writes structured artifacts, so steps compose. A project picks how much to automate by picking
which steps a person does.

| Workflow | Source | Output |
|---|---|---|
| `dependency-assessment` | Dependabot alerts, against what the main branch installs | the month's assessment issue: every open alert classified (easy, major, blocked, end-of-life, no fix), with a finding per package. Each run replaces the body and comments on what changed, so it can run as often as wanted |
| `dependency-upgrades` | the month's assessment issue | one PR applying the easy findings, one commit per package, with derived package and alert tables. It refuses an assessment that misses an open alert, and never merges: the PR is the hand-off |
| `code-review` | a pull request | one consolidated review, posted as a comment with its findings embedded; follow-ups report what happened to each earlier finding |
| `code-review-retrospective` | what happened to the review findings over a period | a report issue: which categories people act on; a PR to this repo when the review skill should change; proposed rules for the project |
| `sentry-errors` | one environment's unresolved Sentry issues | one PR per run fixing one error group, with a regression test; a triage issue for what needs a person |
| `sentry-retrospective` | whether the Sentry fixes made their errors stop | a report issue: fixes that didn't work and why, recurring classes of error, rules for the project; a PR to this repo when the skill should change |
| `dependency-retrospective` | what happened to the upgrade PRs and alerts over a period | a report issue: time to fix against targets, where the time went, stale or abandoned packages; and a PR to this repo when the upgrade skill should change |
| `dependency-report` | one calendar month of the Dependabot alert history | a post in the project's Discussions for the people it answers to: vulnerabilities fixed and still open, time to fix against targets, the year so far, and the plan for what remains |

## Set up

You need, on the machine that runs the workflow:

- [uv](https://docs.astral.sh/uv/), git, and an agent: [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
  or [Codex](https://developers.openai.com/codex). Unattended runs need Claude Code for now.
  uv provides Python (3.12 or later; it installs one if none is present) and this repository's
  locked dependencies, so nothing else on the Python side needs installing.
- Node 22.20 or later, if you install rulesets with `npx skills` or a project has npm lockfiles.
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

1. **Install mason, then its skills,** from the same release tag. mason is a tool you install
   for yourself, like `gh`: nothing is added to the projects you use it on.
   ```sh
   uv tool install git+https://github.com/<org>/mason@<tag>
   mason skills install <org>/mason@<tag>
   ```
   The first puts `mason` on your PATH; `uv tool upgrade mason` updates it. The second installs
   the workflow skills with `npx skills`, with its telemetry turned off. Install both from the
   same tag: the skills call `mason`'s commands.

   **Where the skills go is your choice:**
   - **For mason's sessions only** (the default): into `~/.local/share/mason`. Sessions you start
     with `mason run` load them; your other agent sessions never see them, so nothing about your
     everyday work changes.
   - **For every session** (`--global`): into `~/.claude/skills` (and `~/.agents/skills` with
     `--agent codex`). Then you can ask for a workflow in any session, without `mason run`.

   `mason skills list [--global]` shows what is installed. To work on mason itself, install from
   your checkout instead: `uv tool install --editable path/to/mason` and
   `mason skills install path/to/mason`.
2. **Check the prerequisites.** `doctor` reports any missing tool, login or token. Add
   `--workflow <name>` to check a workflow's tokens, and `--unattended` for a scheduled run's.
   ```sh
   mason doctor
   ```
3. **Check what it detects in the project.** No settings file is needed. This prints what
   was found (the GitHub repo, main branch, requirements files, npm lockfiles, CI) and where
   each value came from:
   ```sh
   mason config path/to/<project> --section dependency-upgrades
   ```
   To change a value, add a settings file for the project. It is yours, not the project's, so it
   lives in your home directory, named after the project's GitHub repository:
   `~/.config/mason/projects/<owner>/<repo>.toml` (`config` prints the path as `settings.path`).
   Either copy `mason.toml.example` (every key, for an example project) and keep only what
   differs, or start from `--toml`, which prints this project's detected values commented out,
   ready to uncomment.
4. **Create the label** the workflow's PRs carry. It is how open agent PRs are counted:
   ```sh
   gh label create automation --repo <owner>/<repo> --description 'Opened by an agent workflow'
   ```

## Run it

**Interactively,** `mason run` starts Claude Code with the request already typed, and adds the
project's run directory and (if they are kept there) mason's skills to the session:

```sh
mason run dependency-report --project path/to/<project> --request 'for October 2026'
mason run dependency-upgrades --repo <owner>/<repo>
```

`--project` runs in a checkout (the current directory by default). `--repo` runs in mason's own
clone of the repository, under `~/.cache/mason/repos/`, cloned the first time and fetched every
time after, so you don't need the project checked out and a run never touches a checkout you're
working in. With the skills installed `--global`, asking for a workflow in any session works too.

A project's own skill with the same name as one of mason's (`code-review` is a likely one)
replaces it or is replaced, whichever was installed last. To be sure which skill runs, name its
file: "Read ~/.local/share/mason/.claude/skills/dependency-upgrades/SKILL.md and follow it for this
project."

Then ask for "run the dependency upgrades", "review PR #42", "fix the Sentry errors", or a
retrospective ("run the dependency retrospective", "run the code review retrospective", "run
the Sentry retrospective"). The upgrade run tells you what
it detected, asks before anything
that needs confirmation (running tests that can't share a machine, opening issues), and ends
with the PR link and a short report. Each run's files (alerts, findings, the PR body, a
worktree) are kept under `~/.local/state/mason/<owner>/<repo>/runs/`.

**Unattended,** for a scheduler or another agent, add `--unattended`:

```sh
mason run dependency-upgrades --repo <owner>/<repo> --unattended
```

A workflow that acts on one pull request takes it with `--pr`:

```sh
mason run code-review --repo <owner>/<repo> --pr 42 --unattended
```

This runs Claude Code headless from the project (Codex isn't supported here yet). The run:
- **Checks first.** `mason doctor` runs before the session starts, and a missing tool,
  login or token stops the run with the reason.
- **Asks nothing.** Everything that needed a person is listed in its report.
- **Uses only the tools the workflow needs.** Anything else is refused, and merging is always
  refused.
- **Leaves the tests to CI** when they can't run locally, and opens a draft PR if nothing can
  verify the change.
- **Logs** the session to `~/.local/state/mason/<owner>/<repo>/logs/`. `--dry-run` prints the command without running
  it, and `--allow 'Bash(<command> *)'` adds a tool the project needs.

Only one run per project happens at a time. A run that finds nothing to upgrade opens no PR.
Give the `gh` login no more than it needs: reading alerts, pushing branches, opening PRs.

**Tokens** live in one file outside every repository, `~/.config/mason/env`, as `KEY=value`
lines with `chmod 600`. The systemd unit reads it with `EnvironmentFile`; for an interactive run,
load it only for that session: `(set -a; . ~/.config/mason/env; set +a; claude)`.
Never put a token in a settings file or in the project's `.env`
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
mason run dependency-upgrades --repo <owner>/<repo> --unattended --trigger 'systemd timer on factory-1'
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
# uv's usual install location; a user unit's PATH doesn't include it.
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=%h/.local/bin/mason run dependency-upgrades --repo <owner>/<repo> --unattended --trigger 'systemd timer dependency-upgrades.timer'

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
`mason run dependency-retrospective --repo <owner>/<repo> --unattended`. Each one continues from the last. The run's log is
`journalctl --user -u dependency-upgrades`, plus the session log under `~/.local/state/mason/`. Everything a
reviewer needs is on GitHub.

## Layout

```
skills/<name>/SKILL.md      one skill per workflow
lib/artifacts/schemas/      the artifact contracts, with examples (`schemas/` links to it;
                            `mason schema <name>` prints one)
lib/artifacts/              validators, renderers, embedding, config, locking, the dependency
                            readers, outcome collection, and the unattended runner
lib/artifacts/cli.py        the `mason` command the skills call (`mason --help` lists them all):
                            config, doctor, run-start, lock, validate, schema, source, the
                            collectors, the renderers, annotate-diff, extract. From a checkout,
                            `uv run mason` or `bin/artifact` runs it
pyproject.toml, uv.lock     the Python environment uv runs every script in, and its lock
tests/                      stdlib unittest; the schema pages' examples are tests too
docs/design.md              principles, steps and hand-offs, autonomy, distribution
docs/method.md              the checks the workflow makes, and why
mason.toml.example          a project's settings file with every key set, to copy and edit
```

## What stays in each project

Nothing of mason is kept in a project: not its settings, not its runs. mason assists one
developer, so those are the developer's (docs/packaging.md):

| What | Where |
|---|---|
| Settings for a project | `~/.config/mason/projects/<owner>/<repo>.toml` |
| Runs, locks and logs | `~/.local/state/mason/<owner>/<repo>/` |
| Tokens | `~/.config/mason/env` |

(Or under `$XDG_CONFIG_HOME` and `$XDG_STATE_HOME`, when those are set.) A settings file states
its format's `version`, so a later mason that changes the format refuses it with how to update,
rather than misreading it. A `mason.toml` left at a project's root is still read, with a note
saying where to move it.

What a project holds is its own:

- `AGENTS.md`, which the skills read for the project's commands and rules.
- Optionally, a conventions document (`docs/conventions.md` is detected), which code review
  reads as the project's own rules.
