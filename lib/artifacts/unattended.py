"""Start a workflow with nobody watching: from cron, a systemd timer, CI, or another agent.

It runs Claude Code headless (`claude -p`) from the project, with this repository added, the
skill told it is unattended (`AUTOMATION_UNATTENDED=1`), and permissions that allow only what
the workflow needs. Anything else is refused rather than asked about (`--permission-mode
dontAsk`), because nobody is there to answer. The session's output goes to a log under `runs/`.

The tool lists are defence in depth. The real limit is the GitHub token the session runs with:
give it no more than reading alerts, pushing branches and opening pull requests.
"""

import os
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
    'Bash(python3 -I *)',
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
# shell variable (its value is unknown, so `python3 -I $HUB/…` is refused even though
# `python3 -I *` is allowed), and `>` redirection (a file write outside any allowed tool).
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


WORKFLOWS = {
    'dependency-upgrades': Workflow(
        prompt='Run the dependency upgrades for this project, unattended.',
        # pip_compile and the build commands, as the project configures them.
        tools=('Bash(make *)', 'Bash(npm *)', 'Bash(pip-compile *)'),
    ),
    'dependency-retrospective': Workflow(
        prompt='Run the dependency retrospective for this project, unattended.',
        # This repository's own checks, run before proposing a change to its skills.
        tools=('Bash(python3 -m unittest *)', 'Bash(ruff *)'),
    ),
}


def command(
    hub: Path,
    project: Path,
    workflow: str,
    extra_tools: tuple[str, ...] = (),
    claude: str = 'claude',
) -> list[str]:
    spec = WORKFLOWS[workflow]
    rules = RULES.format(hub=hub.resolve(), project=project.resolve())
    return [
        claude,
        '-p',
        spec.prompt + rules,
        '--add-dir',
        str(hub),
        '--permission-mode',
        'dontAsk',
        '--allowedTools',
        *BASE_TOOLS,
        *spec.tools,
        *extra_tools,
        '--disallowedTools',
        *DENIED_TOOLS,
    ]


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
    env = {**os.environ, 'AUTOMATION_UNATTENDED': '1', 'AUTOMATION_TRIGGER': trigger}
    with log.open('w', encoding='utf-8') as handle:
        result = subprocess.run(
            args, cwd=project, env=env, stdout=handle, stderr=subprocess.STDOUT, check=False
        )
    return result.returncode, log
