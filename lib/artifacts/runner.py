"""Start a workflow in an agent session: `mason run`.

Interactively, it starts Claude Code in the project with the request already typed, so the
person watches and answers. Unattended (from cron, a systemd timer, CI, or another agent), it runs
Claude Code headless (`claude -p`), tells the skill so (`MASON_UNATTENDED=1`), and allows only
what the workflow needs; anything else is refused rather than asked about (`--permission-mode
dontAsk`), because nobody is there to answer. It runs `mason doctor` first, so a missing tool,
login or token stops the run before any session starts, and logs the session to the project's
state directory.

Either way the session gets the project's state directory (`paths.project_state`), where its run
files go, and mason's skills directory when the skills are installed there rather than for every
session (`paths.data_home`, docs/packaging.md §3).

The tool lists are defence in depth. The real limit is the GitHub token the session runs with:
give it no more than reading alerts, pushing branches and opening pull requests.
"""

import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
# shell variable (its value is unknown, so `mason … $RUN/x` is refused even though the full path
# is allowed), and `>` redirection (a file write outside any allowed tool).
RULES = (
    ' The project ($PROJECT) is {project}, and its runs are kept under {state}. Write every path'
    ' out in full: commands that use or assign a shell variable, or redirect output to a file'
    " with `>`, are refused. Save output with a command's own option instead (`mason … -o"
    ' <file>`, `gh … --body-file <file>`).'
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
    # What to ask for, in the words the skill's description listens for. `{pr}` is the pull request.
    task: str
    tools: tuple[str, ...]
    # Whether the workflow acts on one pull request, given with --pr.
    needs_pr: bool = False
    # Environment variables the workflow can't run without. `artifact doctor` checks they are
    # set, never what they hold.
    env: tuple[str, ...] = ()
    # What the GitHub login must reach besides pushing branches and opening issues and PRs, which
    # every workflow does: 'alerts' (Dependabot alerts), 'discussions' (posting in Discussions).
    github: tuple[str, ...] = ()


# mason's own checks, run in a clone of it (`mason source`) before a retrospective proposes a
# change to its skills.
MASON_CHECKS = ('Bash(uv run --locked python -m unittest *)', 'Bash(ruff *)')


WORKFLOWS = {
    # Reads alerts, manifests, issues and PRs, and writes one issue: no build or test tools.
    'dependency-assessment': Workflow(
        task='Run the dependency assessment for this project',
        tools=(),
        github=('alerts',),
    ),
    'dependency-upgrades': Workflow(
        task='Run the dependency upgrades for this project',
        # pip_compile and the build commands, as the project configures them.
        tools=('Bash(make *)', 'Bash(npm *)', 'Bash(pip-compile *)'),
        github=('alerts',),
    ),
    'dependency-retrospective': Workflow(
        task='Run the dependency retrospective for this project',
        tools=MASON_CHECKS,
        github=('alerts',),
    ),
    # Reads alerts and issues and posts one discussion, all through mason and gh.
    'dependency-report': Workflow(
        task='Run the dependency report for this project',
        tools=(),
        github=('alerts', 'discussions'),
    ),
    # A review reads someone else's code and never runs it: no build, test or package tools.
    'code-review': Workflow(
        task='Run the code review for pull request #{pr} in this project',
        tools=(),
        needs_pr=True,
    ),
    'sentry-errors': Workflow(
        task='Run the Sentry errors workflow for this project',
        # The project's tests and builds, as for an upgrade. Sentry is read through mason.
        tools=('Bash(make *)', 'Bash(npm *)'),
        env=('SENTRY_AUTH_TOKEN',),
    ),
    'sentry-retrospective': Workflow(
        task='Run the Sentry retrospective for this project',
        tools=MASON_CHECKS,
        env=('SENTRY_AUTH_TOKEN',),
    ),
    'code-review-retrospective': Workflow(
        task='Run the code review retrospective for this project',
        tools=MASON_CHECKS,
    ),
}


def prompt(
    workflow: str, pr: int | None = None, request: str = '', unattended: bool = False
) -> str:
    """The request the session starts with: the workflow's task, then anything the person added
    (a month, a period, a scope), marked unattended when nobody is watching."""
    spec = WORKFLOWS[workflow]
    if spec.needs_pr and pr is None:
        raise ValueError(f'{workflow} acts on one pull request: give --pr')
    text = spec.task.format(pr=pr)
    if request.strip():
        text += f' ({request.strip()})'
    return text + (', unattended.' if unattended else '.')


def _directories(state: Path, skills: Path | None) -> list[str]:
    """`--add-dir` for the project's state directory, and mason's skills when they are kept there."""
    added = ['--add-dir', str(state)]
    if skills is not None:
        added += ['--add-dir', str(skills)]
    return added


def interactive_command(
    project: Path,
    state: Path,
    workflow: str,
    skills: Path | None = None,
    claude: str = 'claude',
    pr: int | None = None,
    request: str = '',
) -> list[str]:
    """Claude Code, started in the project with the request typed in. The person answers its
    questions and approves its actions, under their own permission settings."""
    text = prompt(workflow, pr, request) + f' The project ($PROJECT) is {project.resolve()}.'
    return [claude, text, *_directories(state, skills)]


def command(
    project: Path,
    state: Path,
    workflow: str,
    extra_tools: tuple[str, ...] = (),
    claude: str = 'claude',
    pr: int | None = None,
    skills: Path | None = None,
    request: str = '',
) -> list[str]:
    """Claude Code headless, for an unattended run."""
    spec = WORKFLOWS[workflow]
    rules = RULES.format(project=project.resolve(), state=state)
    # The session writes only to the project's state directory, where its run files go. mason is
    # allowed as the installed command, never `python <any file>`: a project's (or a pull
    # request's) Python must not be runnable through this rule.
    return [
        claude,
        '-p',
        prompt(workflow, pr, request, unattended=True) + rules,
        *_directories(state, skills),
        '--permission-mode',
        'dontAsk',
        '--allowedTools',
        *BASE_TOOLS,
        'Bash(mason *)',
        *spec.tools,
        *extra_tools,
        '--disallowedTools',
        *DENIED_TOOLS,
    ]


def doctor_command(workflow: str) -> list[str]:
    """`mason doctor` for an unattended run, run before any session starts."""
    return ['mason', 'doctor', '--workflow', workflow, '--unattended']


def environment(trigger: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """The session's environment: marked unattended, and without PYTHONPATH.

    From a checkout, `uv run mason` runs in mason's own environment, which uv announces with
    VIRTUAL_ENV and its `bin` first on PATH. Neither is passed on: a project's tests would run in
    mason's Python, and a retrospective's `uv run` in a clone of mason would warn about the
    mismatch."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if k not in ('PYTHONPATH', 'VIRTUAL_ENV')}
    if venv := source.get('VIRTUAL_ENV'):
        venv_bin = str(Path(venv) / 'bin')
        paths = [path for path in env.get('PATH', '').split(os.pathsep) if path != venv_bin]
        env['PATH'] = os.pathsep.join(paths)
    env.update(MASON_UNATTENDED='1', MASON_TRIGGER=trigger)
    return env


def log_path(logs_dir: Path, workflow: str, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now(UTC)).strftime('%Y%m%d-%H%M%S')
    return logs_dir / f'{stamp}-{workflow}.log'


def run(
    logs_dir: Path, project: Path, workflow: str, args: list[str], trigger: str
) -> tuple[int, Path]:
    """Run the session from the project, logging everything it prints. Returns (status, log).

    `trigger` names what started it (a timer, another agent). The run's record carries it, so
    the PR it opens says so.
    """
    log = log_path(logs_dir, workflow)
    log.parent.mkdir(parents=True, exist_ok=True)
    env = environment(trigger)
    with log.open('w', encoding='utf-8') as handle:
        result = subprocess.run(
            args, cwd=project, env=env, stdout=handle, stderr=subprocess.STDOUT, check=False
        )
    return result.returncode, log
