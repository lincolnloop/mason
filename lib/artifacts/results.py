"""What workflows produce, kept by mason as the master copy, and published from there
(docs/workflows.md §8).

A result is a directory under the project's results directory (`paths.results`), named by the
workflow and a key the workflow chooses (a month, a period):

    results/dependency-report/2026-10/
      meta.json        title, the file to publish, when it was saved, the run that saved it
      report.md        the files the run saved, its data among them
      report.json
      run.json         the run that produced it
      published.json   each copy made of it: target, where, when, the version it was made from
      previous/<run>/  an earlier version, kept when a later run saves the same key

The master is this directory until the developer removes it. Everything published is a copy,
and a result can be published to several places. Each publish is recorded, so `mason results`
shows where a result went and which copies are older than the master.
"""

import json
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import discussions, paths, schedules, validate

KEY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
META, PUBLISHED, RUN, PREVIOUS = 'meta.json', 'published.json', 'run.json', 'previous'
# Targets: `discussion[:<category>]` (the workflow's `category` setting when none is given),
# `issue` (in the project, with the `automation` label), and `file:<directory>`.
TARGETS = ('discussion', 'issue', 'file')
LABEL = 'automation'

Gh = Callable[..., str]


class ResultError(Exception):
    pass


def _now(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise ResultError(f'{path}: is not valid JSON: {exc}') from exc


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def directory(repo: str, workflow: str, key: str, env: Mapping[str, str] | None = None) -> Path:
    if not KEY.fullmatch(key) or not KEY.fullmatch(workflow):
        raise ResultError(f'{workflow}/{key}: a workflow and key are letters, digits, . _ and -')
    try:
        return paths.results(repo, env) / workflow / key
    except ValueError as exc:
        raise ResultError(str(exc)) from exc


def save(
    run_file: Path,
    key: str,
    files: Sequence[Path],
    body: Path,
    title: str,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
    comment: Path | None = None,
) -> Path:
    """Save a run's output as the result for `key`, keeping any earlier version. Every JSON file
    that is an artifact is validated first; nothing is saved if one is invalid."""
    record = _json(run_file)
    if not isinstance(record, dict) or record.get('schema') != 'run/1':
        raise ResultError(f'{run_file}: is not a run record (run.json)')
    if errors := validate(record):
        raise ResultError(f'{run_file}: {errors[0]}')
    if not title.strip():
        raise ResultError('--title is empty')
    if body not in files:
        files = [*files, body]
    if comment is not None and comment not in files:
        files = [*files, comment]
    for path in files:
        if not path.is_file():
            raise ResultError(f'{path}: does not exist')
        if path.name in (META, PUBLISHED, RUN):
            raise ResultError(f'{path}: {path.name} is a name the result keeps for itself')
        if path.suffix == '.json':
            data = _json(path)
            for artifact in data if isinstance(data, list) else [data]:
                if isinstance(artifact, dict) and 'schema' in artifact:
                    if errors := validate(artifact):
                        raise ResultError(f'{path}: {len(errors)} error(s): {errors[0]}')
    if len({p.name for p in files}) != len(files):
        raise ResultError('two files have the same name')
    workflow = record['produced_by']['skill']
    target = directory(record['project'], workflow, key, env)
    if target.exists():
        old = _json(target / META) or {}
        kept = target / PREVIOUS / str(old.get('run', 'unknown'))
        kept.mkdir(parents=True, exist_ok=True)
        for item in target.iterdir():
            if item.name not in (PREVIOUS, PUBLISHED):
                shutil.move(str(item), kept / item.name)
    target.mkdir(parents=True, exist_ok=True)
    for path in files:
        shutil.copy2(path, target / path.name)
    shutil.copy2(run_file, target / RUN)
    write_json(
        target / META,
        {
            'workflow': workflow,
            'key': key,
            'title': title.strip(),
            'body': body.name,
            **({'comment': comment.name} if comment is not None else {}),
            'saved_at': _now(now),
            'run': record['id'],
        },
    )
    return target


@dataclass
class Result:
    repo: str
    workflow: str
    key: str
    path: Path
    meta: dict[str, Any]
    published: list[dict[str, Any]]

    def stale(self) -> list[dict[str, Any]]:
        """Copies made from an earlier version than the master."""
        return [p for p in self.published if p.get('version') != self.meta.get('saved_at')]


def load(repo: str, workflow: str, key: str, env: Mapping[str, str] | None = None) -> Result:
    path = directory(repo, workflow, key, env)
    meta = _json(path / META)
    if not isinstance(meta, dict):
        raise ResultError(f'no result {workflow}/{key} for {repo} (`mason results --repo {repo}`)')
    published = _json(path / PUBLISHED) or []
    return Result(repo, workflow, key, path, meta, published)


def listing(
    repo: str,
    workflow: str | None = None,
    env: Mapping[str, str] | None = None,
    within: str | None = None,
) -> list[Result]:
    """A project's results; with `within`, only those keyed by a period inside it (the weeks
    of a month, by the rule in `schedules.within`), for a workflow that consolidates them."""
    if within is not None:
        schedules.from_key(within)
    root = paths.results(repo, env)
    found = []
    for meta in sorted(root.glob(f'{workflow or "*"}/*/{META}')):
        key = meta.parent.name
        if within is not None and not _inside(key, within):
            continue
        found.append(load(repo, meta.parent.parent.name, key, env))
    return found


def _inside(key: str, outer: str) -> bool:
    try:
        period = schedules.from_key(key)
    except schedules.ScheduleError:
        return False
    return key in schedules.within(outer, period.interval)


def render(found: Sequence[Result]) -> str:
    if not found:
        return 'No results saved yet.\n'
    lines = []
    for result in found:
        stale = {id(p) for p in result.stale()}
        lines.append(
            f'{result.workflow}/{result.key}  {result.meta["title"]}  '
            f'(saved {result.meta["saved_at"]})'
        )
        for copy in result.published:
            note = '  older than the master' if id(copy) in stale else ''
            lines.append(f'  {copy["target"]}: {copy["where"]} ({copy["at"]}){note}')
        if not result.published:
            lines.append('  not published')
    return '\n'.join(lines) + '\n'


def publish(
    result: Result,
    target: str,
    gh: Gh,
    category: str | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> str:
    """Make a copy of the result at `target`, and record it. Returns where it went."""
    kind, _, argument = target.partition(':')
    if kind not in TARGETS:
        raise ResultError(
            f'{target}: a target is discussion[:<category>], issue or file:<directory>'
        )
    body = (result.path / result.meta['body']).read_text(encoding='utf-8')
    if kind == 'discussion':
        category = argument or category
        if not category:
            raise ResultError('discussion: name the category (discussion:<category>)')
        try:
            where = discussions.post(gh, result.repo, category, result.meta['title'], body)
        except discussions.DiscussionError as exc:
            raise ResultError(str(exc)) from exc
    elif kind == 'issue':
        where = _issue(result, gh, body)
    else:
        if not argument:
            raise ResultError('file: name the directory (file:<directory>)')
        folder = Path(argument).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        copy = folder / f'{result.repo.replace("/", "-")}-{result.workflow}-{result.key}.md'
        # A file copy is replaced: the master has changed, and nobody replies to a file.
        copy.write_text(body, encoding='utf-8')
        where = str(copy)
    result.published = [
        p for p in result.published if not (p['target'] == target and kind in ('file', 'issue'))
    ]
    result.published.append(
        {'target': target, 'where': where, 'at': _now(now), 'version': result.meta['saved_at']}
    )
    write_json(result.path / PUBLISHED, result.published)
    return where


def _issue(result: Result, gh: Gh, body: str) -> str:
    """Open the result's issue, or, when one is already open under its title and the result was
    saved with a comment (what changed since the last version), replace its body and add the
    comment. The issue is found on GitHub, not in this machine's records, so runs on two machines
    keep one issue."""
    title = result.meta['title']
    found = json.loads(
        gh(
            'issue',
            'list',
            '--repo',
            result.repo,
            '--label',
            LABEL,
            '--state',
            'open',
            '--search',
            f'in:title "{title}"',
            '--json',
            'number,url,title',
        )  # fmt: skip
        or '[]'
    )
    existing = next((i for i in found if i.get('title') == title), None)
    folder = result.path
    if existing is None:
        url = gh(
            'issue', 'create', '--repo', result.repo, '--title', title, '--label', LABEL,
            '--body-file', str(folder / result.meta['body']),
        ).strip()  # fmt: skip
        if 'comment' in result.meta:
            gh('issue', 'comment', url, '--body-file', str(folder / result.meta['comment']))
        return url
    if 'comment' not in result.meta:
        raise ResultError(
            f'{existing["url"]} is already "{title}". A published issue is not replaced: post a '
            'correction as a comment on it.'
        )
    gh('issue', 'edit', existing['url'], '--body-file', str(folder / result.meta['body']))
    gh('issue', 'comment', existing['url'], '--body-file', str(folder / result.meta['comment']))
    return existing['url']


def remove(result: Result) -> str:
    """Delete the master. Published copies are left where they are."""
    shutil.rmtree(result.path)
    copies = ''.join(f'\n- {p["target"]}: {p["where"]}' for p in result.published)
    left = f' Its published copies are left in place:{copies}' if copies else ''
    return f'Removed {result.workflow}/{result.key}.{left}\n'
