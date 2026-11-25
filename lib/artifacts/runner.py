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

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from . import skills

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


def declared(name: str) -> skills.Workflow:
    """The installed workflow called `name`, as its skill declares it."""
    found = skills.workflows()
    if name not in found:
        installed = ', '.join(found) or 'none'
        raise ValueError(
            f'{name} is not an installed workflow (installed: {installed}); '
            "`mason skills install <org>/mason@<tag>` installs mason's"
        )
    return found[name]


def prompt(
    workflow: str,
    pr: int | None = None,
    request: str = '',
    unattended: bool = False,
    spec: skills.Workflow | None = None,
    skill_file: Path | None = None,
) -> str:
    """The request the session starts with: the workflow's task, then anything the person added
    (a month, a period, a scope), marked unattended when nobody is watching. With `skill_file`,
    it names the skill to follow, rather than leaving the agent to choose one by description."""
    spec = spec or declared(workflow)
    if spec.needs_pr and pr is None:
        raise ValueError(f'{workflow} acts on one pull request: give --pr')
    text = spec.task.format(pr=pr)
    if skill_file is not None:
        text = f'Read {skill_file} and follow it. {text}'
    if request.strip():
        text += f' ({request.strip()})'
    return text + (', unattended.' if unattended else '.')


def _directories(state: Path, skills: Path | None) -> list[str]:
    """`--add-dir` for the project's state directory, and the run's skills directory."""
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
    spec: skills.Workflow | None = None,
    skill_file: Path | None = None,
) -> list[str]:
    """Claude Code, started in the project with the request typed in. The person answers its
    questions and approves its actions, under their own permission settings."""
    text = prompt(workflow, pr, request, spec=spec, skill_file=skill_file)
    text += f' The project ($PROJECT) is {project.resolve()}.'
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
    spec: skills.Workflow | None = None,
    skill_file: Path | None = None,
) -> list[str]:
    """Claude Code headless, for an unattended run."""
    spec = spec or declared(workflow)
    rules = RULES.format(project=project.resolve(), state=state)
    # The session writes only to the project's state directory, where its run files go. mason is
    # allowed as the installed command, never `python <any file>`: a project's (or a pull
    # request's) Python must not be runnable through this rule.
    return [
        claude,
        '-p',
        prompt(workflow, pr, request, True, spec, skill_file) + rules,
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
    write_status(log, result.returncode)
    return result.returncode, log


def write_status(log: Path, status: int, now: datetime | None = None) -> Path:
    """Record how an unattended run ended, beside its log, so `mason inbox` can find failures."""
    path = log.with_name(log.name.removesuffix('.log') + '.status.json')
    finished = (now or datetime.now(UTC)).strftime('%Y-%m-%d %H:%M UTC')
    record = {'status': status, 'log': str(log), 'finished_at': finished}
    path.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return path
