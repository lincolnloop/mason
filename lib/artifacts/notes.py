"""Analyses and assessments a developer keeps, out of the project's history: `mason note`
(docs/workflows.md §8.5).

A note is a result with no workflow behind it: `results/<owner>/<repo>/notes/<date>-<slug>/`,
holding `note.md` and the same `meta.json` as any result, plus a status, tags and links. So
`mason publish notes/<key>` and `mason results` work for notes as for anything else, and an
update keeps the earlier version.

A note has a lifecycle (docs/method.md: facts with a lifecycle get a status and a date): it is
`draft` or `proposed` while it is a proposal, then `adopted` (what the project keeps moves into
the project, and the note links to it), `dropped`, or `superseded` by another note.
"""

import re
import shutil
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import results

WORKFLOW = 'notes'
BODY = 'note.md'
STATUSES = ('draft', 'proposed', 'adopted', 'dropped', 'superseded')


class NoteError(Exception):
    pass


def slug(title: str) -> str:
    words = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    return words[:60].rstrip('-') or 'note'


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _stamp(now: datetime | None) -> str:
    return _now(now).strftime('%Y-%m-%dT%H:%M:%SZ')


def save(
    repo: str,
    source: Path,
    title: str | None = None,
    update: str | None = None,
    status: str = 'draft',
    tags: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> str:
    """Save a new note, or a new version of `update`. Returns its key."""
    try:
        text = source.read_text(encoding='utf-8')
    except FileNotFoundError as exc:
        raise NoteError(f'{source}: does not exist') from exc
    if not text.strip():
        raise NoteError(f'{source}: is empty')
    if status not in STATUSES:
        raise NoteError(f'status must be one of {", ".join(STATUSES)}, got {status!r}')
    if update:
        note = load(repo, update, env)
        meta = dict(note.meta)
        kept = note.path / results.PREVIOUS / meta['saved_at'].replace(':', '')
        kept.mkdir(parents=True, exist_ok=True)
        for item in note.path.iterdir():
            if item.name in (BODY, results.META):
                shutil.copy2(item, kept / item.name)
        if title:
            meta['title'] = title.strip()
        target, key = note.path, update
    else:
        if not title or not title.strip():
            raise NoteError('a new note needs --title')
        key = f'{_now(now):%Y-%m-%d}-{slug(title)}'
        try:
            target = results.directory(repo, WORKFLOW, key, env)
        except results.ResultError as exc:
            raise NoteError(str(exc)) from exc
        if target.exists():
            raise NoteError(f'{key} exists: give --update {key} for a new version of it')
        meta = {
            'workflow': WORKFLOW,
            'key': key,
            'title': title.strip(),
            'body': BODY,
            'created': _stamp(now),
            'status': status,
            'tags': [],
            'links': [],
        }
    meta['saved_at'] = _stamp(now)
    meta['tags'] = sorted({*meta.get('tags', []), *(t.strip() for t in tags if t.strip())})
    target.mkdir(parents=True, exist_ok=True)
    (target / BODY).write_text(text, encoding='utf-8')
    results.write_json(target / results.META, meta)
    return key


def load(repo: str, key: str, env: Mapping[str, str] | None = None) -> results.Result:
    try:
        return results.load(repo, WORKFLOW, key, env)
    except results.ResultError as exc:
        raise NoteError(f'no note {key} for {repo} (`mason note list --repo {repo}`)') from exc


def set_status(
    repo: str,
    key: str,
    status: str,
    link: str | None = None,
    superseded_by: str | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> str:
    """Record what happened to a note: adopted (with a link to where the project keeps it),
    dropped, superseded (by another note), or back to draft or proposed."""
    if status not in STATUSES:
        raise NoteError(f'status must be one of {", ".join(STATUSES)}, got {status!r}')
    if status == 'superseded':
        if not superseded_by:
            raise NoteError('superseded: give --by <the note that replaces it>')
        load(repo, superseded_by, env)
    note = load(repo, key, env)
    meta = dict(note.meta)
    meta['status'] = status
    meta['status_at'] = _stamp(now)
    if superseded_by:
        meta['superseded_by'] = superseded_by
    if link:
        meta['links'] = [*meta.get('links', []), link]
    results.write_json(note.path / results.META, meta)
    return f'{key}: {status}\n'


def listing(
    repo: str,
    status: str | None = None,
    tag: str | None = None,
    env: Mapping[str, str] | None = None,
) -> list[results.Result]:
    found = results.listing(repo, WORKFLOW, env)
    return [
        n
        for n in found
        if (status is None or n.meta.get('status') == status)
        and (tag is None or tag in n.meta.get('tags', []))
    ]


def render_list(repo: str, found: Sequence[results.Result]) -> str:
    if not found:
        return f'{repo}: no notes\n'
    lines = [repo]
    for note in found:
        meta: dict[str, Any] = note.meta
        tags = f'  [{", ".join(meta["tags"])}]' if meta.get('tags') else ''
        lines.append(f'  {note.key}  {meta["status"]:<10}  {meta["title"]}{tags}')
    return '\n'.join(lines) + '\n'


def show(note: results.Result) -> str:
    meta = note.meta
    header = [
        f'# {meta["title"]}',
        '',
        f'{note.key} · {meta["status"]} · saved {meta["saved_at"]}',
    ]
    if meta.get('tags'):
        header.append(f'Tags: {", ".join(meta["tags"])}')
    if meta.get('links'):
        header.append(f'Links: {", ".join(meta["links"])}')
    if meta.get('superseded_by'):
        header.append(f'Superseded by: {meta["superseded_by"]}')
    body = (note.path / BODY).read_text(encoding='utf-8')
    return '\n'.join(header) + '\n\n---\n\n' + body
