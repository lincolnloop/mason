"""Which mason produced an artifact: the commit it was built from.

Every artifact records it (`produced_by.automation_sha`), so it is clear which version of a skill
and its commands did the work. Run from a checkout, it is the checkout's commit, with `-dirty`
appended for uncommitted changes. Installed (`uv tool install git+…@<tag>`), there is no checkout:
the installer records the commit it built from in the package's `direct_url.json` (PEP 610).
"""

import json
import subprocess
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

# lib/artifacts/version.py -> the checkout's root, when run from one.
CHECKOUT = Path(__file__).resolve().parents[2]

Git = Callable[..., str]


class VersionError(Exception):
    pass


def automation_sha(
    checkout: Path = CHECKOUT, git: Git | None = None, direct_url: str | None = None
) -> str:
    """The commit this mason was built from: 40 hex digits, then `-dirty` for a modified checkout."""
    if (checkout / '.git').exists():
        run = git or _git
        try:
            sha = run(checkout, 'rev-parse', 'HEAD')
            dirty = bool(run(checkout, 'status', '--porcelain', '--untracked-files=no'))
        except subprocess.CalledProcessError as exc:
            raise VersionError(f'cannot read the mason commit: {exc.stderr.strip()}') from exc
        return f'{sha}{"-dirty" if dirty else ""}'
    text = direct_url if direct_url is not None else _direct_url()
    commit = _commit(text)
    if commit is None:
        raise VersionError(
            'mason was not installed from git, so the commit it was built from is unknown. '
            'Install it with `uv tool install git+https://github.com/<org>/mason@<tag>`.'
        )
    return commit


def source_url(
    checkout: Path = CHECKOUT, git: Git | None = None, direct_url: str | None = None
) -> str:
    """The repository this mason came from, to clone when a retrospective proposes a change to it.

    From a checkout, its `origin` remote; installed, the git URL it was installed from."""
    if (checkout / '.git').exists():
        run = git or _git
        try:
            return run(checkout, 'remote', 'get-url', 'origin')
        except subprocess.CalledProcessError as exc:
            raise VersionError(
                f'this mason checkout has no origin remote, so a change to it has nowhere to go: '
                f'{exc.stderr.strip()}'
            ) from exc
    text = direct_url if direct_url is not None else _direct_url()
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        data = {}
    url = data.get('url') if isinstance(data, dict) and data.get('vcs_info') else None
    if not isinstance(url, str) or not url:
        raise VersionError(
            'mason was not installed from git, so the repository it came from is unknown. '
            'Install it with `uv tool install git+https://github.com/<org>/mason@<tag>`.'
        )
    return url.removeprefix('git+')


def clone(url: str, directory: Path, run: Callable[..., object] = subprocess.run) -> None:
    """Clone mason's repository, at its default branch, into `directory` (which must not exist)."""
    if directory.exists():
        raise VersionError(f'{directory} already exists')
    try:
        run(
            ['git', 'clone', '--quiet', url, str(directory)],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise VersionError(f'git clone {url} failed: {exc.stderr.strip()}') from exc


def _commit(text: str | None) -> str | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    commit = ((data.get('vcs_info') or {}) if isinstance(data, dict) else {}).get('commit_id')
    if isinstance(commit, str) and len(commit) == 40:
        return commit
    return None


def _direct_url() -> str | None:
    try:
        return metadata.distribution('mason').read_text('direct_url.json')
    except metadata.PackageNotFoundError:
        return None


def _git(checkout: Path, *command: str) -> str:
    result = subprocess.run(
        ['git', '-C', str(checkout), *command], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()
