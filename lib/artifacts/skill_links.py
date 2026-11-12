"""Link this repository's skills into a directory an agent scans for skills.

Claude Code finds them through `--add-dir` (they are at `.claude/skills` here). Codex scans only
`.agents/skills` from the working directory up to the repository root, and the user's
`~/.agents/skills`, so when it is launched from a project it finds them only if they are linked
there. A link, not a copy: every project keeps using this one checkout.

Only symlinks into this repository are created or removed. Anything else already at a skill's
name is left alone and reported.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Result:
    name: str
    # linked | already | removed | absent | conflict
    status: str
    detail: str = ''


def skills(hub: Path) -> list[Path]:
    """This repository's skills: each directory under skills/ that holds a SKILL.md."""
    root = hub / 'skills'
    return sorted(p for p in root.iterdir() if (p / 'SKILL.md').is_file())


def _ours(path: Path, skill: Path) -> bool:
    return path.is_symlink() and path.resolve() == skill.resolve()


def link(hub: Path, target: Path, dry_run: bool = False) -> list[Result]:
    results = []
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
    for skill in skills(hub):
        path = target / skill.name
        if _ours(path, skill):
            results.append(Result(skill.name, 'already'))
        elif path.is_symlink() or path.exists():
            what = f'a link to {path.readlink()}' if path.is_symlink() else 'not a link'
            results.append(Result(skill.name, 'conflict', f'{path} is {what}'))
        else:
            if not dry_run:
                path.symlink_to(skill.resolve(), target_is_directory=True)
            results.append(Result(skill.name, 'linked', str(path)))
    return results


def unlink(hub: Path, target: Path, dry_run: bool = False) -> list[Result]:
    results = []
    for skill in skills(hub):
        path = target / skill.name
        if _ours(path, skill):
            if not dry_run:
                path.unlink()
            results.append(Result(skill.name, 'removed', str(path)))
        elif path.is_symlink() or path.exists():
            results.append(
                Result(skill.name, 'conflict', f'{path} is not a link to this repository')
            )
        else:
            results.append(Result(skill.name, 'absent'))
    return results
