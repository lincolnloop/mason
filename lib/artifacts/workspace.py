"""mason's own clone of a project: `mason run --repo owner/name` works in it, never in a checkout
a developer is using, and needs no checkout at all (docs/packaging.md §6).

The clone is made once with `gh`, so it uses the same login as everything else, and fetched
before each run. Nothing in it is reset: a workflow branches from the remote's main branch in a
worktree of its own, so the clone's working tree is never what a run changes.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

Run = Callable[..., subprocess.CompletedProcess[str]]


class WorkspaceError(Exception):
    pass


def ensure_clone(repo: str, directory: Path, run: Run = subprocess.run) -> str:
    """Clone `repo` into `directory`, or fetch it if it is there already. Returns what was done."""
    if (directory / '.git').exists():
        _call(run, ['git', '-C', str(directory), 'fetch', '--prune', '--quiet', 'origin'])
        return f'fetched {repo} in {directory}'
    if directory.exists() and any(directory.iterdir()):
        raise WorkspaceError(f'{directory} exists and is not a clone of {repo}: move it away')
    directory.parent.mkdir(parents=True, exist_ok=True)
    _call(run, ['gh', 'repo', 'clone', repo, str(directory), '--', '--quiet'])
    return f'cloned {repo} into {directory}'


def _call(run: Run, command: list[str]) -> None:
    try:
        run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise WorkspaceError(f'{command[0]} is not installed') from exc
    except subprocess.CalledProcessError as exc:
        raise WorkspaceError(f'{" ".join(command[:3])} failed: {exc.stderr.strip()}') from exc
