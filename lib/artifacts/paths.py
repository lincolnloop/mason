"""Where mason keeps a developer's settings and run state: in their home directory, never in a
project or in mason's own checkout (docs/packaging.md §5, §6).

The XDG base directories, with their usual defaults:

    $XDG_CONFIG_HOME/mason/projects/<owner>/<repo>.toml   settings for one project
    $XDG_STATE_HOME/mason/<owner>/<repo>/                 runs, locks and logs for one project
    $XDG_CACHE_HOME/mason/repos/<owner>/<repo>/           mason's own clone of a project
    $XDG_DATA_HOME/mason/results/<owner>/<repo>/          what workflows produced: the master copy
    $XDG_DATA_HOME/mason/.claude/skills/                  mason's skills, when they are installed
                                                          for mason's sessions only

A project is named by its GitHub repository, not its path, so the same settings and state apply
whichever checkout, machine or runner a workflow runs from.
"""

import os
import re
from collections.abc import Mapping
from pathlib import Path

REPO_PATTERN = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


def _base(env: Mapping[str, str] | None, variable: str, default: str) -> Path:
    env = os.environ if env is None else env
    value = env.get(variable, '')
    # The XDG spec: a relative path is invalid and must be ignored.
    if value and Path(value).is_absolute():
        return Path(value) / 'mason'
    return Path(env.get('HOME') or Path.home()) / default / 'mason'


def config_home(env: Mapping[str, str] | None = None) -> Path:
    return _base(env, 'XDG_CONFIG_HOME', '.config')


def state_home(env: Mapping[str, str] | None = None) -> Path:
    return _base(env, 'XDG_STATE_HOME', '.local/state')


def cache_home(env: Mapping[str, str] | None = None) -> Path:
    return _base(env, 'XDG_CACHE_HOME', '.cache')


def data_home(env: Mapping[str, str] | None = None) -> Path:
    """Where mason's skills are installed when they are for mason's sessions only: `mason run`
    adds it to the session, and no other agent session sees them."""
    return _base(env, 'XDG_DATA_HOME', '.local/share')


def skills_home(env: Mapping[str, str] | None = None) -> Path | None:
    """`data_home` if skills are installed there, else None (they are installed globally, or not
    at all)."""
    home = data_home(env)
    return (
        home
        if (home / '.claude' / 'skills').is_dir() or (home / '.agents' / 'skills').is_dir()
        else None
    )


def _parts(repo: str) -> tuple[str, str]:
    if not REPO_PATTERN.fullmatch(repo) or '..' in repo.split('/'):
        raise ValueError(f'a repository is owner/name, got {repo!r}')
    owner, name = repo.split('/')
    return owner, name


def settings(repo: str, env: Mapping[str, str] | None = None) -> Path:
    """The developer's settings file for one project."""
    owner, name = _parts(repo)
    return config_home(env) / 'projects' / owner / f'{name}.toml'


def project_state(repo: str, env: Mapping[str, str] | None = None) -> Path:
    """Runs, locks and logs for one project."""
    owner, name = _parts(repo)
    return state_home(env) / owner / name


def clone(repo: str, env: Mapping[str, str] | None = None) -> Path:
    """mason's own clone of a project, for runs named by repository rather than a checkout."""
    owner, name = _parts(repo)
    return cache_home(env) / 'repos' / owner / name


def results(repo: str, env: Mapping[str, str] | None = None) -> Path:
    """What workflows produced for one project, kept as the master copy (results.py). Under the
    data directory, not the state directory: these are kept, not thrown away."""
    owner, name = _parts(repo)
    return data_home(env) / 'results' / owner / name


def runs(repo: str, env: Mapping[str, str] | None = None) -> Path:
    return project_state(repo, env) / 'runs'


def logs(repo: str, env: Mapping[str, str] | None = None) -> Path:
    return project_state(repo, env) / 'logs'
