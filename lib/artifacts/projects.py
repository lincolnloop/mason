"""The projects a developer looks after: `mason project add`, `list`, `archive` and `remove`.

Adding a project does, once, what each workflow would otherwise find missing at its first run:
mason's own clone of the repository, the developer's settings file naming the workflows they
run on it, the `automation` label the workflows' PRs and issues carry, and a check that the
GitHub login reaches what those workflows need. Nothing is added to the project's repository
except the label (docs/packaging.md §1).
"""

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import paths, project, skills, workspace

LABEL = 'automation'
LABEL_DESCRIPTION = 'Opened by an agent workflow'

# Runs a command and returns (exit status, stdout, stderr).
Gh = Callable[[Sequence[str]], tuple[int, str, str]]


class ProjectError(Exception):
    pass


def gh(args: Sequence[str]) -> tuple[int, str, str]:
    try:
        result = subprocess.run(['gh', *args], capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise ProjectError('gh is not installed') from exc
    return result.returncode, result.stdout, result.stderr


@dataclass
class Access:
    """What the GitHub login reaches in a repository."""

    write: bool
    discussions: bool
    # None when alerts can be read; otherwise why not.
    alerts_problem: str | None
    label: bool

    def problems(self, workflows: Sequence[str]) -> list[str]:
        """What stops the given workflows, as sentences naming the workflows affected."""
        needs: dict[str, list[str]] = {}
        declared = skills.workflows()
        for name in workflows:
            for need in ('write', *(declared[name].github if name in declared else ())):
                needs.setdefault(need, []).append(name)
        problems = []
        if 'write' in needs and not self.write:
            problems.append(
                'the GitHub login can only read this repository; every workflow pushes '
                'branches or opens issues and PRs'
            )
        if 'alerts' in needs and self.alerts_problem:
            problems.append(
                f"Dependabot alerts can't be read ({self.alerts_problem}), which "
                f'{", ".join(needs["alerts"])} need'
            )
        if 'discussions' in needs and not self.discussions:
            problems.append(
                f'Discussions are turned off for the repository, and '
                f'{", ".join(needs["discussions"])} posts there'
            )
        return problems


def check_access(repo: str, run: Gh = gh) -> Access:
    status, out, err = run(['api', f'repos/{repo}'])
    if status != 0:
        raise ProjectError(f'gh api repos/{repo} failed: {err.strip() or out.strip()}')
    data = json.loads(out)
    permissions = data.get('permissions') or {}
    status, out, err = run(['api', f'repos/{repo}/dependabot/alerts?per_page=1'])
    alerts_problem = None if status == 0 else _first_line(err or out) or f'status {status}'
    status, out, _ = run(
        ['label', 'list', '--repo', repo, '--search', LABEL, '--json', 'name', '--jq', '.[].name']
    )
    return Access(
        write=bool(permissions.get('push')),
        discussions=bool(data.get('has_discussions')),
        alerts_problem=alerts_problem,
        label=status == 0 and LABEL in out.split(),
    )


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ''


def choose(text: str) -> list[str]:
    """Workflows from what a person typed: names or numbers from `menu()`, separated by commas or
    spaces; empty or `all` for every one."""
    names = sorted(skills.workflows())
    words = text.replace(',', ' ').split()
    if not words or words == ['all']:
        return names
    chosen = []
    for word in words:
        if word.isdigit() and 1 <= int(word) <= len(names):
            name = names[int(word) - 1]
        elif word in names:
            name = word
        else:
            raise ProjectError(f'{word!r} is not a workflow (known: {", ".join(names)})')
        if name not in chosen:
            chosen.append(name)
    return chosen


def menu() -> str:
    lines = [f'{i:>3}. {name}' for i, name in enumerate(sorted(skills.workflows()), start=1)]
    return '\n'.join(lines)


@dataclass
class Plan:
    """What adding a project will do, and what it found, before anything is written."""

    repo: str
    clone: Path
    settings: Path
    workflows: list[str]
    settings_text: str
    create_label: bool
    access_problems: list[str]
    # workflow -> what it still needs in the settings file before it can run.
    missing: dict[str, str] = field(default_factory=dict)
    # Tokens the workflows need that the environment doesn't have.
    tokens: list[str] = field(default_factory=list)

    def describe(self) -> str:
        lines = [
            f'Project {self.repo}, cloned in {self.clone}',
            f'Workflows: {", ".join(self.workflows) or "none"}',
            '',
            'Will:',
            f'- write {self.settings}',
        ]
        if self.create_label:
            lines.append(f'- create the `{LABEL}` label in {self.repo}')
        if self.access_problems:
            lines += ['', 'GitHub access:', *(f'- {p}' for p in self.access_problems)]
        if self.missing:
            lines += ['', f'Settings still to add to {self.settings}:']
            lines += [f'- {name}: {problem}' for name, problem in self.missing.items()]
        if self.tokens:
            lines += [
                '',
                f'Tokens not set: {", ".join(self.tokens)}. Keep them in ~/.config/mason/env '
                '(README, "Tokens"), never in the settings file.',
            ]
        return '\n'.join(lines) + '\n'


def plan(
    repo: str,
    workflows: Sequence[str],
    run_gh: Gh = gh,
    env: Mapping[str, str] | None = None,
    clone: Callable[[str, Path], str] = workspace.ensure_clone,
) -> Plan:
    """Clone (or fetch) the repository and work out what adding it means. Writes nothing else."""
    env = os.environ if env is None else env
    try:
        settings = paths.settings(repo, env)
        directory = paths.clone(repo, env)
    except ValueError as exc:
        raise ProjectError(str(exc)) from exc
    declared = skills.workflows(env)
    if unknown := [name for name in workflows if name not in declared]:
        raise ProjectError(
            f'not installed: {", ".join(unknown)} (installed: {", ".join(declared) or "none"})'
        )
    if settings.exists():
        raise ProjectError(
            f'{repo} is already added: its settings are in {settings}. Edit that file to change '
            'its workflows.'
        )
    try:
        clone(repo, directory)
    except workspace.WorkspaceError as exc:
        raise ProjectError(str(exc)) from exc
    try:
        resolved = project.resolve(directory, env)
    except project.ConfigError as exc:
        raise ProjectError(str(exc)) from exc
    # Only the sections these workflows read: the file is a starting point to edit, not a list
    # of every setting mason has.
    keep = (*project.SHARED_SECTIONS, *workflows)
    resolved.config = {
        'workflows': {'enabled': list(workflows)},
        **{name: values for name, values in resolved.config.items() if name in keep},
    }
    resolved.sources[('workflows', 'enabled')] = 'file'
    resolved.file_path = settings
    missing = {}
    for name in workflows:
        if name in project.SECTIONS:
            try:
                project.require(resolved, name)
            except project.ConfigError as exc:
                problems = [line.removeprefix('- ') for line in str(exc).splitlines()[1:]]
                missing[name] = '; '.join(problems) or str(exc)
    access = check_access(repo, run_gh)
    tokens = sorted({t for n in workflows for t in declared[n].env if not env.get(t)})
    return Plan(
        repo=repo,
        clone=directory,
        settings=settings,
        workflows=list(workflows),
        settings_text=project.to_toml(resolved),
        create_label=access.write and not access.label,
        access_problems=access.problems(workflows),
        missing=missing,
        tokens=tokens,
    )


def apply(p: Plan, run_gh: Gh = gh) -> str:
    """Write the settings file and create the label. Returns what was done."""
    done = []
    if p.create_label:
        status, out, err = run_gh(
            ['label', 'create', LABEL, '--repo', p.repo, '--description', LABEL_DESCRIPTION]
        )
        if status != 0:
            raise ProjectError(f'gh label create failed: {err.strip() or out.strip()}')
        done.append(f'created the `{LABEL}` label')
    p.settings.parent.mkdir(parents=True, exist_ok=True)
    p.settings.write_text(p.settings_text, encoding='utf-8')
    done.append(f'wrote {p.settings}')
    return f'Added {p.repo}: {"; ".join(done)}.\n'


# --- Listing, archiving and removing ------------------------------------------------------------

# A run's directory (`<stamp>-<workflow>`) or log (`<stamp>-<workflow>.log`).
RUN_NAME = re.compile(r'^(\d{8})-(\d{6})-([a-z0-9][a-z0-9-]*?)(?:\.log)?$')
ARCHIVE_BLOCK = (
    '\n[project]\n'
    '# Set by `mason project archive`; remove it to run the project again.\n'
    "archived = '{date}'\n"
)


@dataclass
class Entry:
    repo: str
    workflows: list[str]
    archived: str | None
    # workflow -> when it last ran on this machine, as 'YYYY-MM-DD HH:MM' (UTC).
    last_runs: dict[str, str]
    cloned: bool


def added(env: Mapping[str, str] | None = None) -> list[str]:
    """Every project with a settings file, as owner/name."""
    root = paths.config_home(env) / 'projects'
    return sorted(f'{f.parent.name}/{f.stem}' for f in root.glob('*/*.toml'))


def last_runs(repo: str, env: Mapping[str, str] | None = None) -> dict[str, str]:
    latest: dict[str, str] = {}
    for directory in (paths.runs(repo, env), paths.logs(repo, env)):
        if not directory.is_dir():
            continue
        for item in directory.iterdir():
            if match := RUN_NAME.match(item.name):
                day, time, workflow = match.groups()
                stamp = f'{day[:4]}-{day[4:6]}-{day[6:]} {time[:2]}:{time[2:4]}'
                latest[workflow] = max(latest.get(workflow, ''), stamp)
    return latest


def entries(env: Mapping[str, str] | None = None) -> list[Entry]:
    found = []
    for repo in added(env):
        try:
            workflows = project.enabled_workflows(repo, env)
            archived_on = project.archived(repo, env)
        except project.ConfigError as exc:
            raise ProjectError(str(exc)) from exc
        found.append(
            Entry(
                repo=repo,
                workflows=workflows,
                archived=archived_on,
                last_runs=last_runs(repo, env),
                cloned=(paths.clone(repo, env) / '.git').exists(),
            )
        )
    return found


def render_list(found: Sequence[Entry]) -> str:
    if not found:
        return 'No projects added yet: `mason project add <owner>/<repo>`.\n'
    lines = []
    for entry in found:
        status = f'archived {entry.archived}' if entry.archived else 'active'
        clone = '' if entry.cloned else ', not cloned'
        lines.append(f'{entry.repo} ({status}{clone})')
        for name in entry.workflows:
            lines.append(f'  {name:<28} last run {entry.last_runs.get(name, "never")}')
        if not entry.workflows:
            lines.append('  no workflows enabled')
    return '\n'.join(lines) + '\n'


def _settings_file(repo: str, env: Mapping[str, str] | None) -> Path:
    try:
        path = paths.settings(repo, env)
    except ValueError as exc:
        raise ProjectError(str(exc)) from exc
    if not path.exists():
        raise ProjectError(f'{repo} is not added: `mason project add {repo}`')
    return path


def archive(repo: str, today: str, env: Mapping[str, str] | None = None) -> str:
    """Stop the project's runs, keeping its settings, clone, runs and logs."""
    path = _settings_file(repo, env)
    try:
        if when := project.archived(repo, env):
            return f'{repo} was already archived on {when}.\n'
        if 'project' in project.settings(repo, env):
            raise ProjectError(f'{path} already has a [project] section: add `archived` to it')
    except project.ConfigError as exc:
        raise ProjectError(str(exc)) from exc
    text = path.read_text(encoding='utf-8')
    path.write_text(text.rstrip('\n') + '\n' + ARCHIVE_BLOCK.format(date=today), encoding='utf-8')
    return f'Archived {repo}: it runs nothing until `mason project archive {repo} --undo`.\n'


def unarchive(repo: str, env: Mapping[str, str] | None = None) -> str:
    path = _settings_file(repo, env)
    try:
        when = project.archived(repo, env)
    except project.ConfigError as exc:
        raise ProjectError(str(exc)) from exc
    if not when:
        return f'{repo} is not archived.\n'
    text = path.read_text(encoding='utf-8')
    block = ARCHIVE_BLOCK.format(date=when)
    if block not in text:
        raise ProjectError(f'{path}: the [project] section was edited; remove `archived` by hand')
    path.write_text(text.replace(block, ''), encoding='utf-8')
    return f'{repo} runs again.\n'


@dataclass
class Removal:
    repo: str
    # What will be deleted, existing paths only.
    targets: list[Path]

    def describe(self) -> str:
        lines = [f'Removing {self.repo} deletes:', *(f'- {t}' for t in self.targets)]
        lines += [
            '',
            'It leaves everything on GitHub as it is: PRs, issues, discussions and the '
            '`automation` label.',
        ]
        return '\n'.join(lines) + '\n'


def removal(repo: str, env: Mapping[str, str] | None = None) -> Removal:
    settings = _settings_file(repo, env)
    state = paths.project_state(repo, env)
    if state.is_dir() and (held := sorted(p.name for p in state.glob('*.lock'))):
        raise ProjectError(f'{repo} has a run in progress ({", ".join(held)}): try again after it')
    candidates = [settings, paths.clone(repo, env), state]
    return Removal(repo, [p for p in candidates if p.exists()])


def remove(r: Removal) -> str:
    for target in r.targets:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    return f'Removed {r.repo}.\n'
