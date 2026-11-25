---
status: in progress
updated: 2026-11-19
---

# Packaging and use

How mason is installed, configured and run against a project. It replaces the "star" model in
`docs/design.md` §7, where one checkout is reached with `--add-dir` and each project holds a
`mason.toml`. Part of it is built; the table in §9 says which.

## 1. Principles

1. **The tool belongs to the developer, not the project.** mason assists one developer. A file in
   the project that only that developer's tool reads is noise to everyone else, and two developers
   using different features would fight over it. So nothing of mason goes into a project
   repository: no config file, no workflow file, no run output.
2. **The only shared surface is what gets published:** pull requests, review comments, issues,
   discussions, and wherever else the developer publishes a result. They are what a team sees,
   and they say what was done and why. They are copies: the master of each result is the one
   mason keeps for the developer, until they remove it (`docs/workflows.md` §8). How the work was
   produced, by which tool on whose machine, stays with whoever produced it.
3. **The published formats are the public contract.** Every published item embeds its data in a
   versioned schema (`schemas/`). Anything that reads or writes those formats works with mason,
   including tools that aren't mason.
4. **A workflow skill and the commands it calls are one unit.** They change together (a new
   field, a new command), so they are versioned and released together.
5. **Extend before replacing.** A skill takes settings, reads the project's own documents, and
   offers defined extension points. Replacing a whole skill is for a different procedure, not a
   different preference.
6. **Coordinate through GitHub, not local state.** Whether an upgrade PR is open, or a month has
   been reported, is read from GitHub. So a run behaves the same on any machine, and two developers
   running the same workflow on one project don't collide.

## 2. Three layers

| Layer | What | Where |
|---|---|---|
| Engine | the `mason` command: config, collectors, renderers, validators, publishing, locks, the headless runner | installed per developer as a tool, from a release tag |
| Developer state | settings per project, run records, logs, clones and worktrees | the developer's home directory (§5, §6) |
| Published output | PRs, comments, issues, discussions, each embedding its data | the project's GitHub repository |

## 3. Installing

Two commands, both from the same release tag:

```sh
uv tool install git+https://github.com/example-org/mason@v0.5.0
mason skills install example-org/mason@v0.5.0            # for mason's sessions only
mason skills install example-org/mason@v0.5.0 --global   # or: for every agent session
```

- **The engine** is a uv tool: `mason` on the PATH, in its own environment, upgraded with
  `uv tool upgrade mason`. It is not a dependency of any project.
- **The skills** are installed with `npx skills` (vercel-labs/skills, §4.3). It is already how
  knowledge-skill repositories are installed, it knows where each agent looks for skills, and it
  keeps a lock of what came from where. `mason skills` only runs it in the right place, at a
  pinned version, with its telemetry off.
- **Where the skills go is the developer's choice.** By default they go into mason's data
  directory (`~/.local/share/mason/.claude/skills`, with its own lock), and only sessions started
  with `mason run` load them, through `--add-dir`. A developer who doesn't want mason in every
  agent session, or every project, gets that by default. With `--global` they go where the agent
  loads them for every session, and a workflow can be asked for anywhere. Codex doesn't load
  skills from an added directory, so for Codex `--global` is the only choice for now.
- **Skills call `mason <command>`,** never a path into a checkout. That removes the path handling
  in today's skills: finding the repository two directories above the skill, resolving symlinks,
  and the rule that every path must be written out in full.
- **`mason doctor`** checks the prerequisites as now. It also checks each installed workflow skill
  against the engine (§4.2) and reports which source each skill came from.

## 4. Skills

### 4.1 Two kinds

| Kind | Examples | Calls the engine | Ships |
|---|---|---|---|
| Workflow | dependency-upgrades, dependency-report, the retrospectives, sentry-errors | yes: its commands and schemas | with mason, same tag |
| Knowledge | a framework's idioms, a project's conventions, a system map | no | from its own repository, installed on its own |

A knowledge skill needs no engine and works in any agent session. A workflow skill does nothing
useful without the engine at a matching version.

### 4.2 Compatibility

Each workflow skill declares the mason versions it works with in its frontmatter, under
`metadata`, the open SKILL.md format's map of strings, so other agents ignore it:

```yaml
metadata:
  mason-requires: '>=0.2,<0.3'
```

`mason doctor` checks every installed workflow skill. `mason run`, and `mason run-start` (the
first command every workflow skill runs), refuse a skill whose range this mason is outside, with
the command to fix it, rather than failing partway through a run. The range covers the schemas
too: they ship with the engine. Once the commands and schemas
are stable enough to state compatibility like this, the workflow pack could move to its own
repository without anyone noticing.

### 4.3 What `npx skills` does (checked 2026-11-19, version 1.8.1)

- **Sources:** a GitHub repository (pinned to a tag or commit with `@`), a local path, a URL, or
  skills shipped in an npm package. It finds `SKILL.md` at a repository's root or under
  `skills/<name>/`.
- **Scope:** per project (`.agents/skills/`, with `skills-lock.json` in the project, meant to be
  committed) or global (`-g`: `~/.claude/skills/`, `~/.agents/skills/` and so on, with a lock under
  the user's state directory). **Global fits principle 1.** A project-scoped install puts a lock
  file and skill links in the project.
- **Links:** each agent's directory gets a symlink to one canonical copy, so an update reaches
  every agent.
- **Same name from two sources:** `skills add` installs one skill per name per agent directory,
  and the last `add` replaces the earlier one, with an "overwrites" warning. There is no declared
  precedence. The experimental `sync` command, for skills shipped in npm packages, has rules: a
  skill installed with `add` is never shadowed, a direct dependency beats a transitive one, and a
  tie installs neither.
- **Updates and removal:** `skills update` refetches from the recorded source; `remove --all`
  removes only skills the lock tracks, never hand-written ones.
- **Telemetry is on by default.** Repository and skill identifiers are sent for repositories
  GitHub confirms are public. `DISABLE_TELEMETRY=1` or `DO_NOT_TRACK=1` turns it off; set it in the
  developer's environment.

### 4.4 Replacing a skill

Because the last `add` wins, replacement is a matter of install order, recorded in the lock:

```sh
npx skills add example-org/mason@v0.5.0 -g --skill '*'                # the defaults
npx skills add example-org/team-skills -g --skill dependency-report # replaces one, with a warning
```

- A replacement keeps the skill's name and declares the same `requires`, so its published
  output reads the same to anything downstream.
- `mason doctor` lists each workflow skill's source from the lock, so it is always clear which
  version produced a run.
- Before replacing, try the extension points: settings (§5), the project's own documents, which
  skills already read, and a skill's declared hooks (for example, a note added to a report).

## 5. Settings

Per developer, outside every repository:

```
~/.config/mason/config.toml                  defaults: agent, limits, schedules
~/.config/mason/projects/<owner>/<repo>.toml one file per project, by GitHub repository
```

- Keyed by repository, not by path, so the same settings apply on a laptop, a server or a hosted
  runner.
- Everything that can be detected from the repository still is; the files hold only what
  differs. `mason config <owner>/<repo>` shows the resolved values and where each came from, as
  now.
- Facts the team needs, such as which manifests reach production, belong in the project's own
  documents, written for people (a deployment page, `AGENTS.md`), which skills read anyway. A
  developer can copy them into their settings; the project doesn't depend on a tool's config.
- Secrets come from the environment or a keyring, never from these files.

## 6. State

```
~/.local/state/mason/<owner>/<repo>/runs/<run-id>/   working files, logs, the full run record
~/.local/state/mason/<owner>/<repo>/<workflow>.lock  one run at a time, per machine
~/.cache/mason/repos/<owner>/<repo>/                 mason's own clone, and its worktrees
```

- mason works in its own clone, never the developer's checkout, so a run can't disturb work in
  progress, and a developer doesn't need the project checked out at all.
- Nothing here is needed by anyone else. Losing working files and logs loses no records.
- **Results are records, and kept elsewhere.** What a workflow produces is saved under
  `~/.local/share/mason/results/<owner>/<repo>/`, and that copy is the master until the developer
  removes it; anything published is a copy of it (`docs/workflows.md` §8).
- The lock stops overlapping runs on one machine. Across machines and developers, the GitHub
  checks in principle 6 do it ("an upgrade PR is already open", "October is already reported").

## 7. Running

```sh
mason run dependency-report --repo example-org/example-app              # interactive
mason run dependency-report --repo example-org/example-app --unattended # headless
```

- `mason run` prepares the clone and worktree, takes the lock, starts the run record and starts
  the agent, interactively or headless, through an adapter per agent. Inside an agent session that
  is already open, asking for the workflow by name does the same through the skill.
- **Scheduling is the developer's choice,** and lives on their side:
  - `mason schedule` writes a systemd timer, a launchd job or a cron line;
  - or a GitHub Actions workflow in the developer's own private repository, which installs mason
    at a tag and runs `mason run … --unattended` on a schedule. Actions' `concurrency:` groups
    replace the local lock there, and the state directory is thrown away after each job.
  - Nothing is added to the project's CI.
- **Code review**, the one workflow that reacts to an event, polls on a schedule for pull
  requests updated since its last review, which it finds from its own earlier comments.

## 8. Published output

- Every published item embeds its data in a versioned schema, as now.
- **Provenance is the tool, not the operator:** the skill, its source and version, and the
  engine version. The run record keeps who started a run, on which host, and where its files
  are, but only locally. A published `run` gives the run id, whether it was interactive or
  unattended, and when.
- A team reading the project sees the same thing whichever developer, machine or tool produced
  it.

## 9. What changes from today

| Before | Proposed | Status |
|---|---|---|
| One checkout, reached with `--add-dir` | `uv tool install` from a tag; skills via `npx skills add -g` | built |
| Skills find the engine by path (`$HUB`) | skills call `mason` on the PATH; `mason schema` prints a schema's page | built |
| A retrospective's skill change in a worktree of the developer's checkout | a fresh clone from `mason source` | built |
| `mason.toml` in the project | `~/.config/mason/projects/<owner>/<repo>.toml` (a project-root file is still read, with a note to move it) | built |
| `runs/` inside the mason checkout | `~/.local/state/mason/` | built |
| `bin/link-skills` for Codex | `npx skills add -a codex` | built (removed) |
| Worktrees of the developer's checkout | mason's own clone under `~/.cache/mason/repos/` (`mason run --repo`) | built |
| Projects named by path | projects named `owner/repo` (`mason run --repo`; every command that takes a project) | built |
| `bin/unattended` | `mason run --unattended` | built |
| Skills installed for every session | `mason skills install`: for `mason run` sessions only, or `--global` | built |
| Skills don't declare an engine version | `metadata.mason-requires` in each workflow skill, checked by `mason doctor`, `mason run` and `run-start` | built |
| The published run record names the host and the person | published provenance is the tool and its version: the skill's and mason's versions; the host stays local | built (the person or trigger that started it is still published) |

## 10. Open questions

- **Hosted runners:** how Claude Code authenticates in GitHub Actions, and whether a fine-grained
  token for a repository in another organisation needs that organisation's approval.
- **`npx skills` is a Node tool.** Node is already a prerequisite for projects with npm lockfiles,
  but not for every project. A `mason skills install` wrapper could hide it; it would still call
  `npx skills`.
- **Which `npx skills` commands to rely on.** `add`, `update`, `remove` and the global lock are
  stable. `sync` and `install` are experimental, and the documentation says their behaviour may
  change.
- **Adapters for other agents.** Headless runs are Claude Code only today. Codex uses a sandbox
  rather than a list of allowed commands, so its adapter maps the same intent differently.
- **Migration of an existing project file.** A project that already has `mason.toml` at its root:
  read it once with a warning saying where it should move, then refuse it in the next minor
  release.
