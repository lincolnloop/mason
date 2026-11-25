"""A project's configuration: the developer's settings file for it, over detected values and defaults.

The settings belong to the developer, not the project (docs/packaging.md §5): they live at
`~/.config/mason/projects/<owner>/<repo>.toml` (`paths.settings`), found by the project's GitHub
repository, and nothing is kept in the project. The file is optional: a skill runs without it.
Every value a skill reads comes from, in order:

1. the settings file, if it sets the key (an empty list counts: it turns detection off);
2. the repository, where it says (`detect.py`): the GitHub remote, origin/HEAD, compiled
   requirements files and their headers, tracked npm lockfiles, an ignored `.env`;
3. a default, where one is safe (`DEFAULTS`).

So a skill can run in a new repository straight away, and the file is added once a value needs
to differ. Within the file every key is checked and unknown keys are errors, so a typo can't
silently fall back to a detected value. A value that is required but can't be worked out is an
error naming the key to set. The file states the version of its format (`version = 1`), so a
format change can refuse an old file with instructions rather than misread it.
"""

import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import detect, paths, schedules, skills
from ._check import REPO_PATTERN, Checker

# Where the file used to be kept: in the project. A `mason.toml` at a project's root is still
# read, with a note saying where to move it; the older `.agents/automation.toml` is refused with
# the same instruction rather than silently ignored.
PROJECT_CONFIG_PATH = Path('mason.toml')
OLD_CONFIG_PATH = Path('.agents') / 'automation.toml'
# The format this version of mason reads. Bump it, and say how to update in UPGRADE_HINTS, when a
# change would make an existing file mean something different.
CONFIG_VERSION = 1
UPGRADE_HINTS: dict[int, str] = {}
EXAMPLE = 'mason.toml.example in the mason repository'

# The sections the engine itself reads: section -> {key: (kind, required)}; kind is 'str',
# 'bool', 'int', or 'list' (of strings). `required` applies to the resolved value, never to the
# file. Each workflow's own section is declared by its skill (`settings.toml`, skills.py).
SECTIONS: dict[str, dict[str, tuple[str, bool]]] = {
    # The workflows the developer runs on this project. Unset: every workflow.
    'workflows': {'enabled': ('list', False)},
    # Set by `mason project archive`: the date it was archived. An archived project runs nothing.
    # Set by `mason project add`: the date it was added. Its schedules count from then.
    'project': {'archived': ('str', False), 'added': ('date', False)},
    'repo': {'name': ('str', True), 'main_branch': ('str', True)},
    'limits': {'open_agent_prs': ('int', True), 'stale_after_days': ('int', True)},
    'verify': {
        'exclusive': ('bool', True),
        'reason': ('str', False),
        'worktree_copy': ('list', True),
        'ci': ('bool', True),
    },
}
SHARED_SECTIONS = ('repo', 'limits', 'verify')

DEFAULTS: dict[str, dict[str, Any]] = {
    'limits': {'open_agent_prs': 3, 'stale_after_days': 14},
    # Nothing says whether two worktrees can run the tests at once, so assume they can't: the
    # cost is one confirmation per run.
    'verify': {
        'exclusive': True,
        'reason': 'not configured, so assumed',
        'worktree_copy': [],
        # Whether CI runs the tests on every pull request, so a run that can't test locally can
        # leave them to CI.
        'ci': False,
    },
}

HINT = (
    'Values the file leaves out are detected from the repository or defaulted. Set the ones '
    "that can't be in the settings file, ~/.config/mason/projects/<owner>/<repo>.toml (every key "
    f'is in {EXAMPLE}). `mason config <project> --toml` prints a starting file.'
)


class ConfigError(Exception):
    pass


@dataclass
class Resolved:
    config: dict[str, dict[str, Any]]
    # (section, key) -> where the value came from: 'file', 'default', or 'detected: <how>'
    sources: dict[tuple[str, str], str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    file_exists: bool = False
    # The settings file this project's values are read from, or would be: None when the
    # project's repository can't be detected.
    file_path: Path | None = None
    # What each section may hold: the engine's sections and the workflows' (`schema`).
    schema: dict[str, skills.Settings] = field(default_factory=dict)


def settings_path(project: Path, env: Mapping[str, str] | None = None) -> Path | None:
    """The developer's settings file for the project, from its GitHub repository."""
    repo = detect.repo_name(project)
    return paths.settings(repo, env) if repo else None


def read_file(
    project: Path, env: Mapping[str, str] | None = None
) -> tuple[dict[str, Any], Path | None, list[str]]:
    """The settings for a project, checked for types and unknown keys only.

    Returns (contents, the file they came from or None, notes)."""
    personal = settings_path(project, env)
    in_project = project / PROJECT_CONFIG_PATH
    where = str(personal) if personal else '~/.config/mason/projects/<owner>/<repo>.toml'
    if personal is not None and personal.exists():
        if in_project.exists():
            raise ConfigError(
                f'{in_project} and {personal} both hold settings for this project. mason reads '
                f'only {personal} now: merge anything else into it, and remove {in_project}.'
            )
        return _read(personal), personal, []
    if in_project.exists():
        note = (
            f'{in_project} is read for now, but settings belong to you, not the project: move it '
            f'to {where}.'
        )
        return _read(in_project), in_project, [note]
    old = project / OLD_CONFIG_PATH
    if old.exists():
        raise ConfigError(
            f'{old}: mason reads its settings from {where}. Move the file there and add '
            f'`version = {CONFIG_VERSION}` as its first setting.'
        )
    return {}, None, []


def _read(path: Path) -> dict[str, Any]:
    try:
        with path.open('rb') as handle:
            config = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f'{path}: is not valid TOML: {exc}') from exc
    version = config.pop('version', None)
    if version != CONFIG_VERSION:
        if version is None:
            problem = 'has no `version`'
        elif isinstance(version, int) and version < CONFIG_VERSION:
            problem = f'is version {version}. {UPGRADE_HINTS.get(version, "")}'.rstrip()
        else:
            problem = f'has version {version!r}, which this mason does not read'
        raise ConfigError(
            f'{path}: {problem}; this mason reads version {CONFIG_VERSION}. Set '
            f'`version = {CONFIG_VERSION}` as the first setting (see {EXAMPLE}).'
        )
    mapping = config.get('skills') if isinstance(config.get('skills'), dict) else None
    errors = check(config, (), schema(mapping))
    if errors:
        raise ConfigError(
            '\n'.join([f'{path}: {len(errors)} error(s)', *(f'- {e}' for e in errors)])
        )
    return config


def detected(project: Path) -> dict[str, dict[str, tuple[Any, str]]]:
    """section -> key -> (value, how it was detected)."""
    files = detect.tracked_files(project)
    found: dict[str, dict[str, tuple[Any, str]]] = {'repo': {}, 'verify': {}}
    if (name := detect.repo_name(project)) is not None:
        found['repo']['name'] = (name, 'the origin remote')
    if (branch := detect.main_branch(project)) is not None:
        found['repo']['main_branch'] = (branch, 'origin/HEAD')
    if copy := detect.worktree_copy(project):
        found['verify']['worktree_copy'] = (copy, 'an untracked .env that git ignores')
    if ci := detect.ci_config(files):
        found['verify']['ci'] = (True, f'{ci} runs on pull requests')
    return found


# What a workflow's settings can name as `detect`: detector -> how it finds its value.
DETECTORS = {
    'pip_requirements': 'tracked .txt files with a pip-compile header',
    'pip_sources': 'the .in file beside each compiled file',
    'pip_compile': "the command in the compiled files' headers",
    'npm_projects': 'directories with a tracked package-lock.json',
    'npm_build': 'a package.json with a build script; skip directories without one',
    'conventions': 'a tracked conventions document',
}


def detected_values(project: Path) -> dict[str, tuple[Any, str]]:
    """detector -> (value, how it was detected), for the values the repository says."""
    files = detect.tracked_files(project)
    values = detect.pip(project, files) | detect.npm(project, files)
    if conventions := detect.conventions(files):
        values['conventions'] = conventions
    return {name: (value, DETECTORS[name]) for name, value in values.items()}


def schema(
    mapping: Mapping[str, str] | None = None, env: Mapping[str, str] | None = None
) -> dict[str, skills.Settings]:
    """section -> what it may hold: the engine's own sections, then each workflow's, from the
    skills the project runs (installed, or mapped by its `[skills]`)."""
    shared = {
        section: skills.Settings(
            {
                key: skills.Setting(kind, required, DEFAULTS.get(section, {}).get(key))
                for key, (kind, required) in keys.items()
            }
        )
        for section, keys in SECTIONS.items()
    }
    return shared | skills.settings_of(mapping, env)


def resolve(project: Path, env: Mapping[str, str] | None = None) -> Resolved:
    """The configuration a skill uses: file, then detected, then default, key by key.

    Only types and unknown keys are checked here; `require` checks completeness."""
    file_config, source, notes = read_file(project, env)
    found = detected(project)
    by_detector = detected_values(project)
    sections = schema(file_config.get('skills'), env)
    resolved = Resolved(
        config={},
        file_exists=source is not None,
        file_path=source or settings_path(project, env),
        notes=list(notes),
        schema=sections,
    )
    for section, settings in sections.items():
        values: dict[str, Any] = {}
        for key, setting in settings.keys.items():
            if key in file_config.get(section, {}):
                values[key] = file_config[section][key]
                resolved.sources[(section, key)] = 'file'
            elif key in found.get(section, {}):
                values[key], how = found[section][key]
                resolved.sources[(section, key)] = f'detected: {how}'
            elif setting.detect in by_detector:
                values[key], how = by_detector[setting.detect]
                resolved.sources[(section, key)] = f'detected: {how}'
            elif setting.default is not None:
                values[key] = setting.default
                resolved.sources[(section, key)] = 'default'
        if values or section in file_config:
            resolved.config[section] = values
    for section in sorted(set(file_config) - set(sections) - {'skills', 'grants', 'schedule'}):
        resolved.notes.append(f'[{section}] is for no installed workflow, so it is not read')
    for path in detect.unsupported_lockfiles(detect.tracked_files(project)):
        resolved.notes.append(f'{path}: not read; dependency-upgrades supports pip-compile and npm')
    return resolved


def require(resolved: Resolved, section: str | None = None) -> None:
    """Raise ConfigError unless the shared sections, and `section` if given, are complete."""
    sections = resolved.schema or schema()
    if section is not None and section not in sections:
        raise ConfigError(f'no such section [{section}] (known: {", ".join(sections)})')
    needed = (*SHARED_SECTIONS, *((section,) if section else ()))
    errors = check(resolved.config, needed, sections)
    if errors:
        notes = [f'Note: {note}' for note in resolved.notes]
        raise ConfigError(
            '\n'.join([f'{len(errors)} error(s)', *(f'- {e}' for e in errors), *notes, HINT])
        )


def load(project: Path, section: str | None = None) -> dict[str, Any]:
    resolved = resolve(project)
    require(resolved, section)
    return resolved.config


def check(
    config: dict[str, Any],
    required_sections: tuple[str, ...] = (),
    sections: Mapping[str, skills.Settings] | None = None,
) -> list[str]:
    """Check types, choices and unknown keys in every known section; completeness only in
    `required_sections`. `sections` defaults to the engine's and the installed workflows'. A
    section for no known workflow isn't checked: `resolve` notes it."""
    sections = (
        schema(config.get('skills') if isinstance(config.get('skills'), dict) else None)
        if sections is None
        else sections
    )
    checker = Checker()
    if not isinstance(config, Mapping):
        checker.error('$', 'must be an object')
        return checker.errors
    for name in required_sections:
        if name not in config:
            checker.error(f'$.{name}', 'is required')
    # workflow name -> the directory of the skill this project runs for it.
    mapped = config.get('skills')
    if 'skills' in config and checker.object(mapped, '$.skills', (), tuple(mapped or ())):
        for name, value in mapped.items():
            checker.string(value, f'$.skills.{name}')
    # workflow name -> how often it runs: a schedule, or {every = <schedule>, since = <date>}.
    planned = config.get('schedule')
    if 'schedule' in config and checker.object(planned, '$.schedule', (), tuple(planned or ())):
        for name, value in planned.items():
            where = f'$.schedule.{name}'
            if isinstance(value, dict):
                if checker.object(value, where, ('every',), ('since',)):
                    _check_schedule(checker, value.get('every'), f'{where}.every')
                    if 'since' in value and not isinstance(value['since'], date):
                        checker.error(f'{where}.since', 'must be a date, e.g. 2026-11-25')
            else:
                _check_schedule(checker, value, where)
    # workflow name -> the tools its mapped skill may use unattended.
    granted = config.get('grants')
    if 'grants' in config and checker.object(granted, '$.grants', (), tuple(granted or ())):
        for name, value in granted.items():
            checker.string_array(value, f'$.grants.{name}')
    for name, section in config.items():
        settings = sections.get(name)
        if settings is None:
            continue
        keys = settings.keys
        path = f'$.{name}'
        complete = name in required_sections
        required = [k for k, setting in keys.items() if setting.required and complete]
        if not checker.object(section, path, required, [k for k in keys if k not in required]):
            continue
        for key, value in section.items():
            if key not in keys:
                continue
            setting, where = keys[key], f'{path}.{key}'
            if setting.kind == 'str':
                checker.string(value, where)
                if setting.choices and isinstance(value, str) and value not in setting.choices:
                    checker.error(where, f'must be one of {", ".join(setting.choices)}')
            elif setting.kind == 'bool':
                checker.boolean(value, where)
            elif setting.kind == 'date':
                if not isinstance(value, date) or isinstance(value, datetime):
                    checker.error(where, 'must be a date, e.g. 2026-11-25')
            elif setting.kind == 'int':
                checker.integer(value, where, minimum=1)
            else:
                checker.string_array(value, where)
        if not complete:
            continue
        if settings.one_of and not any(section.get(k) for k in settings.one_of):
            hint = f'{settings.one_of_hint}; ' if settings.one_of_hint else ''
            checker.error(path, f'{hint}set {" or ".join(settings.one_of)}')
        for key, setting in keys.items():
            other = setting.required_with
            if other and section.get(other) and not section.get(key):
                checker.error(f'{path}.{key}', f'is required with {other}')
    repo = config.get('repo')
    if isinstance(repo, dict) and isinstance(repo.get('name'), str):
        checker.string(repo['name'], '$.repo.name', pattern=REPO_PATTERN, pattern_hint='owner/repo')
    return checker.errors


def to_toml(resolved: Resolved) -> str:
    """The resolved configuration as a starting `mason.toml`.

    Values from the file are written as they are. Detected and default values are commented
    out with where they came from, so uncommenting a line is how a value gets pinned."""
    lines = [
        f'# {resolved.file_path or "Settings"}: values the file sets are live; commented-out values are what was',
        '# detected or defaulted, shown so one can be uncommented and changed.',
        f'version = {CONFIG_VERSION}',
    ]
    for section, values in resolved.config.items():
        lines += ['', f'[{section}]']
        for key, value in values.items():
            source = resolved.sources[(section, key)]
            if source == 'file':
                lines.append(f'{key} = {_toml_value(value)}')
            else:
                lines += [f'# {source}', f'# {key} = {_toml_value(value)}']
    for note in resolved.notes:
        lines += ['', f'# Note: {note}']
    return '\n'.join(lines) + '\n'


def _toml_value(value: Any) -> str:
    # TOML basic strings accept JSON string escapes, and the values are only str, int, bool
    # and lists of str.
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return '[' + ', '.join(json.dumps(item) for item in value) + ']'
    return json.dumps(value)


def enabled_workflows(repo: str, env: Mapping[str, str] | None = None) -> list[str]:
    """The workflows enabled for a project in the developer's settings: every installed or
    mapped (`[skills]`) workflow when the file doesn't exist or doesn't say. A name that isn't installed is kept: `mason run`
    and `mason inbox` say so, rather than the settings file becoming unreadable."""
    found = settings(repo, env)
    enabled = found.get('workflows', {}).get('enabled')
    if enabled is not None:
        return list(enabled)
    return sorted({*skills.workflows(env), *found.get('skills', {})})


def archived(repo: str, env: Mapping[str, str] | None = None) -> str | None:
    """The date the project was archived, or None."""
    return settings(repo, env).get('project', {}).get('archived')


def settings(repo: str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The developer's settings file for a project, checked; empty when there is none."""
    path = paths.settings(repo, env)
    return _read(path) if path.exists() else {}


def skill_paths(repo: str, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """workflow -> the skill directory the project's settings map it to (`[skills]`)."""
    return dict(settings(repo, env).get('skills', {}))


def grants(repo: str, env: Mapping[str, str] | None = None) -> dict[str, list[str]]:
    """workflow -> the tools the developer has granted its mapped skill (`[grants]`)."""
    return {name: list(tools) for name, tools in settings(repo, env).get('grants', {}).items()}


def value(repo: str, section: str, key: str, env: Mapping[str, str] | None = None) -> Any:
    """One setting from the developer's settings file, or its declared default: for commands
    that work from a project's name rather than its checkout, and need no detected value."""
    found = settings(repo, env)
    if key in found.get(section, {}):
        return found[section][key]
    declared = schema(found.get('skills'), env).get(section)
    setting = declared.keys.get(key) if declared else None
    return setting.default if setting else None


def _check_schedule(checker: Checker, value: Any, where: str) -> None:
    if checker.string(value, where):
        try:
            schedules.parse(value)
        except schedules.ScheduleError as exc:
            checker.error(where, str(exc))


@dataclass
class Planned:
    """How often a workflow runs on a project, and since when."""

    schedule: schedules.Schedule
    # 'settings' or 'skill' (its default).
    source: str
    # When the project's settings last changed it: periods before are judged by no schedule.
    since: date | None = None


def planned(
    repo: str, workflow: str, spec: skills.Workflow, env: Mapping[str, str] | None = None
) -> Planned:
    value = settings(repo, env).get('schedule', {}).get(workflow)
    if value is None:
        return Planned(schedules.parse(spec.schedule), 'skill')
    if isinstance(value, dict):
        return Planned(schedules.parse(value['every']), 'settings', value.get('since'))
    return Planned(schedules.parse(value), 'settings')


def set_line(path: Path, section: str, key: str, value: str | None) -> None:
    """Set one `key = value` line in a section of a settings file, or remove it (`value` None),
    leaving every other line, comments included, as it was. The section is added at the end if
    the file has none. The file is checked before and after."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'version = {CONFIG_VERSION}\n', encoding='utf-8')
    _read(path)  # refuse a file that is already invalid, rather than add to it
    lines = path.read_text(encoding='utf-8').splitlines()
    header = f'[{section}]'
    line = None if value is None else f'{key} = {value}'
    pattern = re.compile(rf'^\s*"?{re.escape(key)}"?\s*=')
    if header not in lines:
        if line is not None:
            lines += ['', header, line]
    else:
        start = lines.index(header)
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith('[')),
            len(lines),
        )
        found = next((i for i in range(start + 1, end) if pattern.match(lines[i])), None)
        if found is not None and line is None:
            del lines[found]
            if not any(lines[i].strip() for i in range(start + 1, end - 1)):
                # The section is empty now: remove its header, and the blank line before it.
                del lines[start : end - 1]
                if start and not lines[start - 1].strip():
                    del lines[start - 1]
        elif found is not None:
            lines[found] = line
        elif line is not None:
            lines.insert(start + 1, line)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    _read(path)


def set_schedule(
    repo: str, workflow: str, text: str, today: date, env: Mapping[str, str] | None = None
) -> Path:
    """Write a workflow's schedule into the project's settings, with the date it changed."""
    schedule = schedules.parse(text)
    path = paths.settings(repo, env)
    set_line(path, 'schedule', workflow, f"{{ every = '{schedule}', since = {today.isoformat()} }}")
    return path


def added(repo: str, env: Mapping[str, str] | None = None) -> date | None:
    """When the project was added (`mason project add`): its schedules count from then."""
    value = settings(repo, env).get('project', {}).get('added')
    return value if isinstance(value, date) else None
