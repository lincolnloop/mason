"""Run a project's own code in a container: `mason sandbox -- <command>`.

The agent session stays on the developer's machine; what runs in the sandbox is the project's
code and its dependencies' (builds, installs, `pip-compile`, `npm`, `make`, tests), which is
where a compromised package or a poisoned test would execute. The container gets:

- the directory the command runs in (a run's worktree, or mason's clone), read-write;
- a per-project cache for package managers, in the project's state directory;
- the developer's user id, so the files it writes stay theirs;
- nothing else: no home directory, no tokens, no SSH agent, no docker socket.

A project opts in, with an image that has its toolchain:

    [sandbox]
    image = 'python:3.12'       # required: mason can't guess a project's toolchain
    engine = 'docker'           # or 'podman'
    network = true              # dependency tools need it; false cuts the container off

Then an unattended run on it may run the project's commands only through `mason sandbox`: its
skill's own tools for them (`Bash(make *)`, `Bash(npm *)`, …) are dropped (runner.py).
"""

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import paths

ENGINES = ('docker', 'podman')
KEYS = ('image', 'engine', 'network')
# Where the per-project cache is mounted, and the variables that point package managers at it.
CACHE = '/mason-cache'
CACHE_VARIABLES = {
    'HOME': f'{CACHE}/home',
    'XDG_CACHE_HOME': f'{CACHE}/xdg',
    'PIP_CACHE_DIR': f'{CACHE}/pip',
    'UV_CACHE_DIR': f'{CACHE}/uv',
    'npm_config_cache': f'{CACHE}/npm',
}


class SandboxError(Exception):
    pass


@dataclass(frozen=True)
class Sandbox:
    image: str
    engine: str = 'docker'
    network: bool = True


def check(section: object) -> list[str]:
    """Problems with a [sandbox] table, for the settings checker."""
    if not isinstance(section, dict):
        return ['must be a table']
    problems = [f'{key}: is not a known field' for key in section if key not in KEYS]
    if not isinstance(section.get('image'), str) or not section.get('image', '').strip():
        problems.append("image: is required, e.g. 'python:3.12'")
    if 'engine' in section and section['engine'] not in ENGINES:
        problems.append(f'engine: one of {", ".join(ENGINES)}')
    if 'network' in section and not isinstance(section['network'], bool):
        problems.append('network: true or false')
    return problems


def configured(settings: Mapping[str, object]) -> Sandbox | None:
    """The project's sandbox from its settings, or None when it has none."""
    section = settings.get('sandbox')
    if not isinstance(section, dict):
        return None
    return Sandbox(
        image=str(section['image']),
        engine=str(section.get('engine', 'docker')),
        network=bool(section.get('network', True)),
    )


def command(
    sandbox: Sandbox,
    workdir: Path,
    cache: Path,
    argv: Sequence[str],
    uid: int | None = None,
    gid: int | None = None,
) -> list[str]:
    """The container command that runs `argv` in `workdir`."""
    if not argv:
        raise SandboxError('give the command to run, after --')
    workdir = workdir.resolve()
    uid = os.getuid() if uid is None else uid
    gid = os.getgid() if gid is None else gid
    run = [
        sandbox.engine, 'run', '--rm', '--interactive',
        '--user', f'{uid}:{gid}',
        '--volume', f'{workdir}:{workdir}',
        '--volume', f'{cache}:{CACHE}',
        '--workdir', str(workdir),
        '--network', 'bridge' if sandbox.network else 'none',
        '--security-opt', 'no-new-privileges',
        '--cap-drop', 'ALL',
    ]  # fmt: skip
    for name, value in CACHE_VARIABLES.items():
        run += ['--env', f'{name}={value}']
    return [*run, sandbox.image, *argv]


def inside(workdir: Path, repo: str, env: Mapping[str, str] | None = None) -> bool:
    """Whether `workdir` is a place a run may sandbox: mason's clone of the project, or a run's
    directory (its worktree). Never the developer's home or another project's files."""
    workdir = workdir.resolve()
    allowed = (paths.clone(repo, env).resolve(), paths.runs(repo, env).resolve())
    return any(workdir == root or root in workdir.parents for root in allowed)


def run(
    sandbox: Sandbox,
    repo: str,
    workdir: Path,
    argv: Sequence[str],
    env: Mapping[str, str] | None = None,
) -> int:
    if shutil.which(sandbox.engine) is None:
        raise SandboxError(f'{sandbox.engine} is not installed, and {repo} runs in a sandbox')
    cache = paths.project_state(repo, env) / 'sandbox-cache'
    for sub in ('home', 'xdg', 'pip', 'uv', 'npm'):
        (cache / sub).mkdir(parents=True, exist_ok=True)
    return subprocess.run(command(sandbox, workdir, cache, argv), check=False).returncode
