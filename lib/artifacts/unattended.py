"""Start a workflow with nobody watching: from cron, a systemd timer, CI, or another agent.

It runs Claude Code headless (`claude -p`) from the project, with this repository added, the
skill told it is unattended (`MASON_UNATTENDED=1`), and permissions that allow only what
the workflow needs. Before starting, it runs `artifact doctor` offline, so a missing tool, login,
token or uv environment stops the run with a clear message before any session starts. Anything else is refused rather than asked about (`--permission-mode
dontAsk`), because nobody is there to answer. The session's output goes to a log under `runs/`.

The tool lists are defence in depth. The real limit is the GitHub token the session runs with:
give it no more than reading alerts, pushing branches and opening pull requests.
"""

import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import detect

# Tools every workflow needs: reading and editing files, git and gh, this repo's scripts, and
# the shell basics its steps use.
BASE_TOOLS = (
    'Read',
    'Edit',
    'Write',
    'Glob',
    'Grep',
    'Bash(git *)',
    'Bash(gh *)',
    'Bash(cd *)',
    'Bash(ls *)',
    'Bash(mkdir *)',
    'Bash(cp *)',
    'Bash(date *)',
    'Bash(echo *)',
    'Bash(touch *)',
    # Read-only helpers a pipeline uses (`git diff | grep …`); each part of a pipeline is
    # checked on its own.
    'Bash(grep *)',
    'Bash(head *)',
    'Bash(tail *)',
    'Bash(wc *)',
    'Bash(sort *)',
)
# Told to every session, because headless permission checks refuse what they can't see into: a
# shell variable (its value is unknown, so `… python -I $HUB/bin/artifact` is refused even
# though the full path is allowed), and `>` redirection (a file write outside any allowed tool).
RULES = (
    ' The mason repository ($HUB in the skill) is {hub}, and the project ($PROJECT) is'
    ' {project}. Write every path out in full: commands that use or assign a shell variable, or'
    " redirect output to a file with `>`, are refused. Save output with a command's own option"
    ' instead (`artifact … -o <file>`, `gh … --body-file <file>`).'
)
# Never, whatever the workflow: merging and reviewing are other steps' jobs, and history on a
# shared branch is not rewritten.
DENIED_TOOLS = (
    'Bash(gh pr merge *)',
    'Bash(gh pr review *)',
    'Bash(git push --force *)',
    'Bash(git push -f *)',
    'Bash(git push --force-with-lease *)',
)


@dataclass(frozen=True)
class Workflow:
    prompt: str
    tools: tuple[str, ...]
    # Whether the workflow acts on one pull request, given with --pr.
    needs_pr: bool = False
    # Environment variables the workflow can't run without. `artifact doctor` checks they are
    # set, never what they hold.
    env: tuple[str, ...] = ()


# This repository's own checks, run in a worktree of it before a retrospective proposes a change
# to its skills.
HUB_CHECKS = ('Bash(uv run --locked python -m unittest *)', 'Bash(ruff *)')


WORKFLOWS = {
    'dependency-upgrades': Workflow(
        prompt='Run the dependency upgrades for this project, unattended.',
        # pip_compile and the build commands, as the project configures them.
        tools=('Bash(make *)', 'Bash(npm *)', 'Bash(pip-compile *)'),
    ),
    'dependency-retrospective': Workflow(
        prompt='Run the dependency retrospective for this project, unattended.',
        tools=HUB_CHECKS,
    ),
    # A review reads someone else's code and never runs it: no build, test or package tools.
    'code-review': Workflow(
        prompt='Run the code review for pull request #{pr} in this project, unattended.',
        tools=(),
        needs_pr=True,
    ),
    'sentry-errors': Workflow(
        prompt='Run the Sentry errors workflow for this project, unattended.',
        # The project's tests and builds, as for an upgrade. Sentry is read through bin/artifact.
        tools=('Bash(make *)', 'Bash(npm *)'),
        env=('SENTRY_AUTH_TOKEN',),
    ),
    'sentry-retrospective': Workflow(
        prompt='Run the Sentry retrospective for this project, unattended.',
        tools=HUB_CHECKS,
        env=('SENTRY_AUTH_TOKEN',),
    ),
    'code-review-retrospective': Workflow(
        prompt='Run the code review retrospective for this project, unattended.',
        tools=HUB_CHECKS,
    ),
}


def artifact_command(hub: Path) -> list[str]:
    """How this repository's command line is run: in its own locked uv environment, isolated
    (`-I`) from the directory it runs in, which is usually a project's."""
    hub = hub.resolve()
    return [
        'uv',
        'run',
        '--project',
        str(hub),
        '--locked',
        'python',
        '-I',
        str(hub / 'bin' / 'artifact'),
    ]


def command(
    hub: Path,
    project: Path,
    workflow: str,
    extra_tools: tuple[str, ...] = (),
    claude: str = 'claude',
    pr: int | None = None,
) -> list[str]:
    spec = WORKFLOWS[workflow]
    if spec.needs_pr and pr is None:
        raise ValueError(f'{workflow} acts on one pull request: give --pr')
    rules = RULES.format(hub=hub.resolve(), project=project.resolve())
    # Only this repository's own command line, never `python -I <any file>`: a project's (or a
    # pull request's) Python must not be runnable through this rule.
    artifact = f'Bash({shlex.join(artifact_command(hub))} *)'
    return [
        claude,
        '-p',
        spec.prompt.format(pr=pr) + rules,
        '--add-dir',
        str(hub),
        '--permission-mode',
        'dontAsk',
        '--allowedTools',
        *BASE_TOOLS,
        artifact,
        *spec.tools,
        *extra_tools,
        '--disallowedTools',
        *DENIED_TOOLS,
    ]


def doctor_command(hub: Path, workflow: str) -> list[str]:
    """`artifact doctor` for an unattended run: offline, so it also proves that uv's cache
    holds this repository's locked environment."""
    command = artifact_command(hub)
    return [
        *command[:2],
        '--offline',
        *command[2:],
        'doctor',
        '--workflow',
        workflow,
        '--unattended',
    ]


def environment(trigger: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """The session's environment: marked unattended, with uv kept offline so a run never
    resolves or downloads a package, and without PYTHONPATH, which `-I` ignores anyway.

    bin/unattended runs in a uv script environment of its own, which uv announces with
    VIRTUAL_ENV and its `bin` first on PATH. Neither is passed on: the session's `uv run
    --project` would warn about the mismatch, and its `python` would be that environment's."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if k not in ('PYTHONPATH', 'VIRTUAL_ENV')}
    if venv := source.get('VIRTUAL_ENV'):
        venv_bin = str(Path(venv) / 'bin')
        paths = [path for path in env.get('PATH', '').split(os.pathsep) if path != venv_bin]
        env['PATH'] = os.pathsep.join(paths)
    env.update(MASON_UNATTENDED='1', MASON_TRIGGER=trigger, UV_OFFLINE='1')
    return env


def log_path(hub: Path, project: Path, workflow: str, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now(UTC)).strftime('%Y%m%d-%H%M%S')
    return hub / 'runs' / detect.project_key(project) / f'{stamp}-{workflow}.log'


def run(hub: Path, project: Path, workflow: str, args: list[str], trigger: str) -> tuple[int, Path]:
    """Run the session from the project, logging everything it prints. Returns (status, log).

    `trigger` names what started it (a timer, another agent). The run's record carries it, so
    the PR it opens says so.
    """
    log = log_path(hub, project, workflow)
    log.parent.mkdir(parents=True, exist_ok=True)
    env = environment(trigger)
    with log.open('w', encoding='utf-8') as handle:
        result = subprocess.run(
            args, cwd=project, env=env, stdout=handle, stderr=subprocess.STDOUT, check=False
        )
    return result.returncode, log
