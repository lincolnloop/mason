"""One run of a workflow at a time per project, across sessions, schedulers and agents.

A lock is a directory under `runs/<project>/`, so taking it is a single atomic `mkdir`. It holds
the time it was taken. A lock older than `STALE_AFTER` is left over from a run that crashed, and
is replaced, with a message saying so.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import detect

STALE_AFTER = timedelta(hours=6)


def path(hub: Path, project: Path, workflow: str) -> Path:
    return hub / 'runs' / detect.project_key(project) / f'{workflow}.lock'


def acquire(lock: Path, now: datetime | None = None) -> tuple[bool, str]:
    """Take the lock. Returns (taken, message)."""
    now = now or datetime.now(UTC)
    lock.parent.mkdir(parents=True, exist_ok=True)
    message = f'took {lock}'
    try:
        lock.mkdir()
    except FileExistsError:
        since = _since(lock)
        if since is not None and now - since < STALE_AFTER:
            return False, f'{lock} is held, since {since:%Y-%m-%dT%H:%M:%SZ}: another run is active'
        release(lock)
        taken = 'an unknown time' if since is None else f'{since:%Y-%m-%dT%H:%M:%SZ}'
        message = f'replaced a stale lock taken at {taken}; took {lock}'
        try:
            lock.mkdir()
        except FileExistsError:
            return False, f'{lock} was taken by another run at the same moment'
    (lock / 'since').write_text(f'{now:%Y-%m-%dT%H:%M:%SZ}\n', encoding='utf-8')
    return True, message


def release(lock: Path) -> None:
    (lock / 'since').unlink(missing_ok=True)
    try:
        lock.rmdir()
    except FileNotFoundError:
        pass


def _since(lock: Path) -> datetime | None:
    try:
        text = (lock / 'since').read_text(encoding='utf-8').strip()
        return datetime.strptime(text, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)
    except (OSError, ValueError):
        pass
    # No readable time: the lock may have been taken a moment ago and not written yet, so use
    # the directory's own time rather than treating it as stale.
    try:
        return datetime.fromtimestamp(lock.stat().st_mtime, UTC)
    except OSError:
        return None
