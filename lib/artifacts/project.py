"""A consuming project's configuration: `.agents/automation.toml`, over detected values and defaults.

The file is optional. Every value a skill reads comes from, in order:

1. the file, if it sets the key (an empty list counts: it turns detection off);
2. the repository, where it says (`detect.py`): the GitHub remote, origin/HEAD, compiled
   requirements files and their headers, tracked npm lockfiles, an ignored `.env`;
3. a default, where one is safe (`DEFAULTS`).

So a skill can run in a new repository straight away, and the file is added once a value needs
to differ. Within the file every key is checked and unknown keys are errors, so a typo can't
silently fall back to a detected value. A value that is required but can't be worked out is an
error naming the key to set.
"""

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import detect
from ._check import REPO_PATTERN, Checker

CONFIG_PATH = Path('.agents') / 'automation.toml'

# section -> {key: (kind, required)}; kind is 'str', 'bool', 'int', or 'list' (of strings).
# `required` applies to the resolved value, never to the file.
SECTIONS: dict[str, dict[str, tuple[str, bool]]] = {
    'repo': {'name': ('str', True), 'main_branch': ('str', True)},
    'limits': {'open_agent_prs': ('int', True), 'stale_after_days': ('int', True)},
    'verify': {
        'exclusive': ('bool', True),
        'reason': ('str', False),
        'worktree_copy': ('list', True),
        'ci': ('bool', True),
    },
    'dependency-upgrades': {
        'pip_requirements': ('list', True),
        'pip_sources': ('list', True),
        'pip_compile': ('str', False),
        'npm_projects': ('list', True),
        'npm_build': ('str', False),
        'verify': ('list', False),
    },
    'code-review': {
        # The project's own knowledge skills to load, e.g. its conventions or framework rules.
        'knowledge': ('list', True),
        # Which findings are posted as inline comments: none, suggestions, or all.
        'inline': ('str', True),
    },
    'code-review-retrospective': {'period_days': ('int', True)},
    'sentry-errors': {
        'sentry_org': ('str', True),
        'sentry_project': ('str', True),
        'sentry_url': ('str', True),
        'environments': ('list', True),
        'period_days': ('int', True),
        # Paths whose stack frames are this project's code; the rest are libraries.
        'trace_path_prefixes': ('list', True),
        # Other repositories a fix may belong in, such as infrastructure configuration.
        'related_repos': ('list', True),
        'verify': ('list', False),
        # How long after a fix is merged its errors must stay quiet to count as fixed.
        'quiet_after_days': ('int', True),
    },
    'sentry-retrospective': {
        'period_days': ('int', True),
        # Allowed between a merge and its deploy, before an event counts against the fix.
        'deploy_grace_hours': ('int', True),
    },
    'dependency-retrospective': {
        'period_days': ('int', True),
        'fix_within_days_critical': ('int', True),
        'fix_within_days_high': ('int', True),
        'fix_within_days_medium': ('int', True),
        'fix_within_days_low': ('int', True),
        'stale_after_months': ('int', True),
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
    'dependency-upgrades': {'pip_requirements': [], 'pip_sources': [], 'npm_projects': []},
    # One consolidated review comment; inline comments only when a project asks for them.
    'code-review': {'knowledge': [], 'inline': 'none'},
    'code-review-retrospective': {'period_days': 30},
    # The org and project can't be detected: they are set in the file.
    'sentry-errors': {
        'sentry_url': 'https://sentry.io',
        'environments': ['production'],
        'period_days': 14,
        'trace_path_prefixes': [],
        'related_repos': [],
        'quiet_after_days': 14,
    },
    'sentry-retrospective': {'period_days': 30, 'deploy_grace_hours': 24},
    # Starting points, to tune per project: how far back a retrospective looks by default, how
    # fast each severity should be fixed, and how long without a release makes a package stale.
    'dependency-retrospective': {
        'period_days': 30,
        'fix_within_days_critical': 3,
        'fix_within_days_high': 7,
        'fix_within_days_medium': 30,
        'fix_within_days_low': 90,
        'stale_after_months': 24,
    },
}

HINT = (
    'Values the file leaves out are detected from the repository or defaulted. Set the ones '
    f"that can't be in {CONFIG_PATH} (see docs/config.example.toml)."
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


def read_file(project: Path) -> tuple[dict[str, Any], bool]:
    """The file's contents, checked for types and unknown keys only. ({}, False) if absent."""
    path = project / CONFIG_PATH
    try:
        with path.open('rb') as handle:
            config = tomllib.load(handle)
    except FileNotFoundError:
        return {}, False
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f'{path}: is not valid TOML: {exc}') from exc
    errors = check(config)
    if errors:
        raise ConfigError(
            '\n'.join([f'{path}: {len(errors)} error(s)', *(f'- {e}' for e in errors)])
        )
    return config, True


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
    how = {
        'pip_requirements': 'tracked .txt files with a pip-compile header',
        'pip_sources': 'the .in file beside each compiled file',
        'pip_compile': "the command in the compiled files' headers",
        'npm_projects': 'directories with a tracked package-lock.json',
        'npm_build': 'a package.json with a build script; skip directories without one',
    }
    upgrades = detect.pip(project, files) | detect.npm(project, files)
    found['dependency-upgrades'] = {key: (value, how[key]) for key, value in upgrades.items()}
    return found


def resolve(project: Path) -> Resolved:
    """The configuration a skill uses: file, then detected, then default, key by key.

    Only types and unknown keys are checked here; `require` checks completeness."""
    file_config, exists = read_file(project)
    found = detected(project)
    resolved = Resolved(config={}, file_exists=exists)
    for section, keys in SECTIONS.items():
        values: dict[str, Any] = {}
        for key in keys:
            if key in file_config.get(section, {}):
                values[key] = file_config[section][key]
                resolved.sources[(section, key)] = 'file'
            elif key in found.get(section, {}):
                values[key], how = found[section][key]
                resolved.sources[(section, key)] = f'detected: {how}'
            elif key in DEFAULTS.get(section, {}):
                values[key] = DEFAULTS[section][key]
                resolved.sources[(section, key)] = 'default'
        if values or section in file_config:
            resolved.config[section] = values
    for path in detect.unsupported_lockfiles(detect.tracked_files(project)):
        resolved.notes.append(f'{path}: not read; dependency-upgrades supports pip-compile and npm')
    return resolved


def require(resolved: Resolved, section: str | None = None) -> None:
    """Raise ConfigError unless the shared sections, and `section` if given, are complete."""
    if section is not None and section not in SECTIONS:
        raise ConfigError(f'no such section [{section}] (known: {", ".join(SECTIONS)})')
    needed = (*SHARED_SECTIONS, *((section,) if section else ()))
    errors = check(resolved.config, needed)
    if errors:
        notes = [f'Note: {note}' for note in resolved.notes]
        raise ConfigError(
            '\n'.join([f'{len(errors)} error(s)', *(f'- {e}' for e in errors), *notes, HINT])
        )


def load(project: Path, section: str | None = None) -> dict[str, Any]:
    resolved = resolve(project)
    require(resolved, section)
    return resolved.config


def check(config: dict[str, Any], required_sections: tuple[str, ...] = ()) -> list[str]:
    """Check types and unknown keys everywhere; completeness only in `required_sections`."""
    checker = Checker()
    checker.object(
        config, '$', required_sections, [s for s in SECTIONS if s not in required_sections]
    )
    for name, section in config.items():
        keys = SECTIONS.get(name)
        if keys is None:
            continue
        path = f'$.{name}'
        required = [k for k, (_, needed) in keys.items() if needed and name in required_sections]
        if not checker.object(section, path, required, [k for k in keys if k not in required]):
            continue
        for key, value in section.items():
            if key not in keys:
                continue
            kind = keys[key][0]
            where = f'{path}.{key}'
            if kind == 'str':
                checker.string(value, where)
            elif kind == 'bool':
                checker.boolean(value, where)
            elif kind == 'int':
                checker.integer(value, where, minimum=1)
            else:
                checker.string_array(value, where)
    repo = config.get('repo')
    if isinstance(repo, dict) and isinstance(repo.get('name'), str):
        checker.string(repo['name'], '$.repo.name', pattern=REPO_PATTERN, pattern_hint='owner/repo')
    review = config.get('code-review')
    if isinstance(review, dict) and review.get('inline') not in (
        None,
        'none',
        'suggestions',
        'all',
    ):
        checker.error('$.code-review.inline', 'must be none, suggestions or all')
    upgrades = config.get('dependency-upgrades')
    if 'dependency-upgrades' in required_sections and isinstance(upgrades, dict):
        if not upgrades.get('pip_requirements') and not upgrades.get('npm_projects'):
            checker.error(
                '$.dependency-upgrades',
                'found no compiled requirements files or npm lockfiles; set pip_requirements or '
                'npm_projects',
            )
        if upgrades.get('pip_requirements') and not upgrades.get('pip_compile'):
            checker.error('$.dependency-upgrades.pip_compile', 'is required with pip_requirements')
    return checker.errors


def to_toml(resolved: Resolved) -> str:
    """The resolved configuration as a starting `.agents/automation.toml`.

    Values from the file are written as they are. Detected and default values are commented
    out with where they came from, so uncommenting a line is how a value gets pinned."""
    lines = [
        f'# {CONFIG_PATH}: values the file sets are live; commented-out values are what was',
        '# detected or defaulted, shown so one can be uncommented and changed.',
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
    if isinstance(value, list):
        return '[' + ', '.join(json.dumps(item) for item in value) + ']'
    return json.dumps(value)
