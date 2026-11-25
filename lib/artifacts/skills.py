"""The installed workflow skills, and whether each works with this mason (docs/packaging.md §4.2).

A workflow skill and the commands it calls change together, so each skill states the mason
versions it works with in its frontmatter:

    metadata:
      mason-requires: '>=0.2,<0.3'

(`metadata` is the open SKILL.md format's map of strings, so other agents ignore it.) mason
refuses to start a run whose skill states a range this mason is outside, with the command that
fixes it, rather than failing partway through the run.

A skill is a workflow mason can run when it also declares the request that starts it, and what
an unattended run needs (lists are comma-separated, since metadata values are strings):

    metadata:
      mason-task: 'Run the code review for pull request #{pr} in this project'
      mason-inputs: 'pr'                      # what `mason run` must be given
      mason-tools: 'Bash(make *), Bash(npm *)'  # beyond the base tools, for unattended runs
      mason-env: 'SENTRY_AUTH_TOKEN'          # tokens it can't run without
      mason-github: 'alerts, discussions'     # what the GitHub login must reach beyond pushing

So the workflows are whatever workflow skills are installed: mason keeps no list of its own.
`MASON_SKILLS_PATH` adds directories searched after the installed ones, such as a checkout's
`skills/`.
"""

import os
import re
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from . import paths, schedules, version

INPUTS = ('pr',)
GITHUB = ('alerts', 'discussions')
REQUIREMENT = re.compile(r'^(>=|<=|==|>|<)\s*(\d+(?:\.\d+)*)$')


class SkillError(Exception):
    pass


KINDS = {'str': str, 'bool': bool, 'int': int, 'list': list, 'date': date}
SETTINGS_FILE = 'settings.toml'


@dataclass(frozen=True)
class Setting:
    """One key of a workflow's settings, as its skill's `settings.toml` declares it."""

    # 'str', 'bool', 'int' (at least 1) or 'list' of strings.
    kind: str
    # Whether a run needs a value: from the settings file, detection or the default.
    required: bool = False
    default: Any = None
    # The detector that finds it in the repository (`project.DETECTORS`).
    detect: str | None = None
    choices: tuple[str, ...] = ()
    # Required when this other key has a value.
    required_with: str | None = None


@dataclass(frozen=True)
class Settings:
    keys: dict[str, Setting] = field(default_factory=dict)
    # At least one of these keys must have a value, and the hint says what that means.
    one_of: tuple[str, ...] = ()
    one_of_hint: str = ''


def parse_settings(text: str) -> Settings:
    """A skill's `settings.toml`, checked."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SkillError(f'is not valid TOML: {exc}') from exc
    one_of = data.pop('one_of', [])
    hint = data.pop('one_of_hint', '')
    if not isinstance(one_of, list) or not all(isinstance(k, str) for k in one_of):
        raise SkillError('one_of: must be a list of keys')
    keys = {}
    for name, spec in data.items():
        if not isinstance(spec, dict):
            raise SkillError(f'{name}: must be a table, with at least `type`')
        unknown = set(spec) - {'type', 'required', 'default', 'detect', 'choices', 'required_with'}
        if unknown:
            raise SkillError(f'{name}: unknown {", ".join(sorted(unknown))}')
        kind = spec.get('type')
        if kind not in KINDS:
            raise SkillError(f'{name}: type must be one of {", ".join(KINDS)}, got {kind!r}')
        default = spec.get('default')
        if default is not None and not _is(kind, default):
            raise SkillError(f'{name}: default {default!r} is not a {kind}')
        choices = spec.get('choices', [])
        if choices and (kind != 'str' or not all(isinstance(c, str) for c in choices)):
            raise SkillError(f'{name}: choices are for a str key, as a list of strings')
        keys[name] = Setting(
            kind=kind,
            required=bool(spec.get('required', False)),
            default=default,
            detect=spec.get('detect'),
            choices=tuple(choices),
            required_with=spec.get('required_with'),
        )
    for name, setting in keys.items():
        if setting.required_with is not None and setting.required_with not in keys:
            raise SkillError(f'{name}: required_with names no key {setting.required_with!r}')
    if missing := [k for k in one_of if k not in keys]:
        raise SkillError(f'one_of names no key {", ".join(missing)}')
    return Settings(keys, tuple(one_of), hint)


def _is(kind: str, value: Any) -> bool:
    if kind == 'list':
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if kind == 'int':
        return isinstance(value, int) and not isinstance(value, bool) and value >= 1
    return isinstance(value, KINDS[kind])


@dataclass(frozen=True)
class Workflow:
    """A workflow, as its skill declares it."""

    # What to ask for, in the words the skill's description listens for. `{pr}` is the pull
    # request.
    task: str
    # Tools an unattended run may use beyond the base set (`runner.BASE_TOOLS`).
    tools: tuple[str, ...] = ()
    # Whether the workflow acts on one pull request, given with --pr.
    needs_pr: bool = False
    # Environment variables it can't run without. `mason doctor` checks they are set, never
    # what they hold.
    env: tuple[str, ...] = ()
    # What the GitHub login must reach besides pushing branches and opening issues and PRs,
    # which every workflow does: 'alerts' (Dependabot alerts), 'discussions'.
    github: tuple[str, ...] = ()
    # How often it runs unless a project's settings say otherwise (schedules.py), whether a
    # run covers a period that has ended ('report') or runs once in each ('check'), which
    # missed periods to run ('all' or 'latest'), and how long after it is due a period's run
    # counts as overdue.
    schedule: str = 'off'
    kind: str = 'check'
    catch_up: str = 'latest'
    grace: str = '1d'


def directories(env: Mapping[str, str] | None = None) -> list[Path]:
    """Where skills are found: mason's own place first (`mason skills install`), then the global
    places (`--global`), then any in `MASON_SKILLS_PATH`."""
    source = os.environ if env is None else env
    home = Path(source.get('HOME') or Path.home())
    data = paths.data_home(env)
    extra = [Path(p) for p in source.get('MASON_SKILLS_PATH', '').split(os.pathsep) if p]
    return [
        data / '.claude' / 'skills',
        data / '.agents' / 'skills',
        home / '.claude' / 'skills',
        home / '.agents' / 'skills',
        *extra,
    ]


def frontmatter(text: str) -> dict[str, object]:
    """The YAML frontmatter's top-level keys, and the `metadata` map: enough for `name`,
    `version` and `metadata.mason-requires`. Values are plain or quoted scalars."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        raise SkillError('has no frontmatter')
    try:
        end = lines.index('---', 1)
    except ValueError as exc:
        raise SkillError('frontmatter is not closed with ---') from exc
    data: dict[str, object] = {}
    section: dict[str, str] | None = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, sep, value = line.strip().partition(':')
        if not sep:
            raise SkillError(f'cannot read frontmatter line {line!r}')
        if line.startswith((' ', '\t')):
            if section is None:
                raise SkillError(f'indented line outside a map: {line!r}')
            section[key] = _scalar(value)
        elif value.strip():
            data[key] = _scalar(value)
            section = None
        else:
            section = {}
            data[key] = section
    return data


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _version_tuple(text: str) -> tuple[int, ...]:
    numbers = re.match(r'^\d+(?:\.\d+)*', text.strip())
    if not numbers:
        raise SkillError(f'{text!r} is not a version')
    return tuple(int(part) for part in numbers.group().split('.'))


def satisfies(engine: str, requirement: str) -> bool:
    """Whether a version meets a range such as '>=0.2,<0.3'."""
    have = _version_tuple(engine)
    for clause in requirement.split(','):
        match = REQUIREMENT.match(clause.strip())
        if not match:
            raise SkillError(f'{clause.strip()!r} is not a version requirement (e.g. >=0.2,<0.3)')
        operator, wanted = match.groups()
        want = _version_tuple(wanted)
        width = max(len(have), len(want))
        a, b = have + (0,) * (width - len(have)), want + (0,) * (width - len(want))
        ok = {
            '>=': a >= b,
            '<=': a <= b,
            '==': a == b,
            '>': a > b,
            '<': a < b,
        }[operator]
        if not ok:
            return False
    return True


@dataclass
class Skill:
    name: str
    path: Path
    version: str | None
    requires: str | None
    # Set when the skill declares itself a workflow (`mason-task`).
    workflow: Workflow | None = None
    # Its settings, from `settings.toml` beside it; None when it has none.
    settings: Settings | None = None

    def problem(self, engine: str) -> str | None:
        """Why this skill can't run with this mason, or None."""
        if self.requires is None:
            return 'states no mason-requires in its frontmatter'
        if engine == 'unknown':
            return None
        try:
            if satisfies(engine, self.requires):
                return None
        except SkillError as exc:
            return str(exc)
        return (
            f'needs mason {self.requires}, and this is mason {engine}: install mason and its '
            'skills from the same release tag (`uv tool install …@<tag>`, '
            '`mason skills install …@<tag>`)'
        )


def read(path: Path) -> Skill:
    try:
        data = frontmatter(path.read_text(encoding='utf-8'))
    except (OSError, SkillError) as exc:
        raise SkillError(f'{path}: {exc}') from exc
    metadata = data.get('metadata')
    metadata = metadata if isinstance(metadata, dict) else {}
    skill_version = data.get('version')
    try:
        workflow = _workflow(metadata)
    except SkillError as exc:
        raise SkillError(f'{path}: {exc}') from exc
    settings_path = path.parent / SETTINGS_FILE
    settings = None
    if settings_path.is_file():
        try:
            settings = parse_settings(settings_path.read_text(encoding='utf-8'))
        except SkillError as exc:
            raise SkillError(f'{settings_path}: {exc}') from exc
    return Skill(
        name=str(data.get('name') or path.parent.name),
        path=path,
        version=skill_version if isinstance(skill_version, str) else None,
        requires=metadata.get('mason-requires'),
        workflow=workflow,
        settings=settings,
    )


def _list(text: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in (text or '').split(',') if item.strip())


def _workflow(metadata: Mapping[str, str]) -> Workflow | None:
    task = metadata.get('mason-task', '').strip()
    if not task:
        return None
    inputs = _list(metadata.get('mason-inputs'))
    github = _list(metadata.get('mason-github'))
    for name, values, known in (('mason-inputs', inputs, INPUTS), ('mason-github', github, GITHUB)):
        unknown = [v for v in values if v not in known]
        if unknown:
            raise SkillError(f'{name}: unknown {", ".join(unknown)} (known: {", ".join(known)})')
    if ('{pr}' in task) != ('pr' in inputs):
        raise SkillError("mason-task names {pr} exactly when mason-inputs has 'pr'")
    schedule = metadata.get('mason-schedule', 'off')
    kind = metadata.get('mason-kind', 'check')
    catch_up = metadata.get('mason-catch-up', 'all' if kind == 'report' else 'latest')
    grace = metadata.get('mason-grace', '1d')
    try:
        schedules.parse(schedule)
        schedules.duration(grace)
    except schedules.ScheduleError as exc:
        raise SkillError(str(exc)) from exc
    if kind not in schedules.KINDS:
        raise SkillError(f'mason-kind: report or check, got {kind!r}')
    if catch_up not in ('all', 'latest'):
        raise SkillError(f'mason-catch-up: all or latest, got {catch_up!r}')
    return Workflow(
        task=task,
        tools=_list(metadata.get('mason-tools')),
        needs_pr='pr' in inputs,
        env=_list(metadata.get('mason-env')),
        github=github,
        schedule=schedule,
        kind=kind,
        catch_up=catch_up,
        grace=grace,
    )


def installed(env: Mapping[str, str] | None = None) -> list[Skill]:
    """Every installed copy of a workflow skill, in the order `directories` searches."""
    found = []
    for directory in directories(env):
        for path in sorted(directory.glob('*/SKILL.md')):
            try:
                skill = read(path)
            except SkillError:
                # Other skills live beside mason's, in YAML this reader doesn't need to follow.
                # Only a skill that declares itself a workflow must be readable.
                if 'mason-task' in path.read_text(encoding='utf-8', errors='replace'):
                    raise
                continue
            if skill.workflow is not None:
                found.append(skill)
    return found


def workflows(env: Mapping[str, str] | None = None) -> dict[str, Workflow]:
    """name -> workflow, from the copy of each workflow skill a session loads first."""
    found: dict[str, Workflow] = {}
    for skill in installed(env):
        if skill.workflow is not None:
            found.setdefault(skill.name, skill.workflow)
    return dict(sorted(found.items()))


def find(workflow: str, env: Mapping[str, str] | None = None) -> Skill | None:
    """The copy of a workflow's skill a session loads first, or None if none is installed."""
    return next((s for s in installed(env) if s.name == workflow), None)


def check(workflow: str, env: Mapping[str, str] | None = None) -> Skill | None:
    """The workflow's installed skill, refusing one that doesn't work with this mason."""
    skill = find(workflow, env)
    if skill is not None and (problem := skill.problem(version.package_version())):
        raise SkillError(f'{skill.path}: {problem}')
    return skill


# --- A project's skills, and a run's ------------------------------------------------------------

# How one skill names another, installed beside it (AGENTS.md: "another skill as
# ../<name>/SKILL.md").
SIBLING = re.compile(r'\.\./([a-z0-9][a-z0-9-]*)/SKILL\.md')


def resolve(
    name: str, mapping: Mapping[str, str] | None = None, env: Mapping[str, str] | None = None
) -> Skill | None:
    """The skill a project runs for `name`: the one its settings map it to (`[skills]`), or else
    the installed one. None if there is neither."""
    if mapping and name in mapping:
        path = Path(mapping[name]).expanduser()
        path = path if path.name == 'SKILL.md' else path / 'SKILL.md'
        if not path.is_file():
            raise SkillError(f'[skills] {name}: no SKILL.md at {path}')
        skill = read(path)
        if skill.name != name:
            raise SkillError(f'[skills] {name}: {path} is the {skill.name} skill')
        if skill.workflow is None and name in workflows(env):
            raise SkillError(f'[skills] {name}: {path} declares no mason-task, so is no workflow')
        return skill
    return find(name, env) or _any(name, env)


def _any(name: str, env: Mapping[str, str] | None) -> Skill | None:
    """A skill that isn't a workflow (one a workflow names), from the same directories."""
    for directory in directories(env):
        path = directory / name / 'SKILL.md'
        if path.is_file():
            return read(path)
    return None


def check_resolved(
    name: str, mapping: Mapping[str, str] | None = None, env: Mapping[str, str] | None = None
) -> Skill | None:
    """As `check`, for the skill the project runs."""
    skill = resolve(name, mapping, env)
    if skill is not None and (problem := skill.problem(version.package_version())):
        raise SkillError(f'{skill.path}: {problem}')
    return skill


def stage(
    name: str,
    directory: Path,
    mapping: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """A skills directory for one run, holding the workflow's skill and every skill it names
    (`../<name>/SKILL.md`), each linked from where the project's settings or the install put it.
    So a session sees the skills this run uses, and no others of mason's. Returns the workflow's
    SKILL.md inside it."""
    skills_dir = directory / '.claude' / 'skills'
    skills_dir.mkdir(parents=True, exist_ok=True)
    pending, seen = [name], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        skill = resolve(current, mapping, env)
        if skill is None:
            raise SkillError(f'{current} is not installed, and {name} needs it')
        link = skills_dir / current
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(skill.path.parent.resolve(), target_is_directory=True)
        pending += SIBLING.findall(skill.path.read_text(encoding='utf-8'))
    return skills_dir / name / 'SKILL.md'


def settings_of(
    mapping: Mapping[str, str] | None = None, env: Mapping[str, str] | None = None
) -> dict[str, Settings]:
    """workflow -> the settings its skill declares, for the skills a project runs (installed,
    or mapped by its settings). A skill whose mapping is broken is left out: `mason run` and
    `mason inbox` report it."""
    found = {}
    # A mapping that isn't a path is the settings checker's to report, not this one's.
    mapping = {k: v for k, v in (mapping or {}).items() if isinstance(v, str)}
    for name in sorted({*workflows(env), *mapping}):
        try:
            skill = resolve(name, mapping, env)
        except SkillError:
            continue
        if skill is not None and skill.settings is not None:
            found[name] = skill.settings
    return found


def provenance(
    name: str,
    skill: Skill,
    mapping: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Where a run's skill came from, to publish with the run: `installed` (mason's own skills
    directory), `global`, `path` (MASON_SKILLS_PATH), or `mapped` by the project's settings,
    with the repository and commit of the mapped directory when it is in one. Never a local path:
    what is published names the tool, not the machine."""
    if mapping and name in mapping:
        return 'mapped' + _git_source(skill.path.parent)
    dirs = directories(env)
    parent = skill.path.parent.parent
    if parent in dirs[:2]:
        return 'installed'
    if parent in dirs[2:4]:
        return 'global'
    return 'path'


def _git_source(directory: Path) -> str:
    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ['git', '-C', str(directory), *args], capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    commit = git('rev-parse', '--short=12', 'HEAD')
    if not commit:
        return ''
    url = git('remote', 'get-url', 'origin') or ''
    # Credentials in a remote URL (https://token@host/...) never leave the machine.
    url = re.sub(r'^(\w+://)[^@/]+@', r'\1', url)
    dirty = '-dirty' if git('status', '--porcelain', '--', '.') else ''
    return f': {url or "a local repository"}@{commit}{dirty}'
