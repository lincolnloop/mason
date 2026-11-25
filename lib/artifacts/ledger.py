"""What was done, what is due, and what was missed: `mason status`, `due`, `overdue`, `history`
(docs/workflows.md §9).

Nothing here is kept by hand. A workflow's schedule (its skill's default, or the project's
settings) gives its slots: a key and a time each is due. The ledger is what this machine knows
about each slot:

- the run records (`runs/<id>/run.json`), with the period each run was for and how it ended
  (`mason run-end`);
- the results saved (`results/.../meta.json` names the run that saved it);
- how unattended runs exited (`logs/*.status.json`);
- the periods a person chose to skip (`mason skip`, in the project's state directory).

A slot is **done** when a run for it ended done or with nothing to do, or saved a result;
**skipped** when a person said so; **failed** when its runs failed and none succeeded; **due**
from its due time until its grace has passed; **overdue** after that.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from . import paths, project, schedules, skills

DONE, SKIPPED, FAILED, DUE, OVERDUE, UPCOMING = (
    'done',
    'skipped',
    'failed',
    'due',
    'overdue',
    'upcoming',
)
SKIPS = 'skips.json'


class LedgerError(Exception):
    pass


def local(stamp: str) -> datetime:
    """A UTC timestamp from a record, as the machine's local time without a zone."""
    return datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone().replace(tzinfo=None)


@dataclass
class Facts:
    """What this machine knows about one workflow on one project, by period key."""

    succeeded: dict[str, str]  # key -> the run that did it
    failed: dict[str, str]  # key -> a run (or log) that failed
    skipped: dict[str, str]  # key -> why


def facts(repo: str, workflow: str, env: Mapping[str, str] | None = None) -> Facts:
    succeeded: dict[str, str] = {}
    failed: dict[str, str] = {}
    saved_by = set()
    root = paths.results(repo, env) / workflow
    for meta in root.glob('*/meta.json') if root.is_dir() else ():
        try:
            saved_by.add(json.loads(meta.read_text(encoding='utf-8')).get('run'))
        except json.JSONDecodeError:
            continue
    runs = paths.runs(repo, env)
    for record_path in sorted(runs.glob(f'*-{workflow}/run.json')) if runs.is_dir() else ():
        try:
            record = json.loads(record_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            continue
        key = record.get('period')
        if not key or record.get('produced_by', {}).get('skill') != workflow:
            continue
        if record.get('outcome') in (DONE, 'nothing_to_do') or record.get('id') in saved_by:
            succeeded[key] = record['id']
        elif record.get('outcome') == FAILED:
            failed.setdefault(key, record['id'])
    logs = paths.logs(repo, env)
    for status in sorted(logs.glob(f'*-{workflow}.status.json')) if logs.is_dir() else ():
        try:
            record = json.loads(status.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            continue
        if record.get('period') and record.get('status') != 0:
            failed.setdefault(record['period'], str(record.get('log', status)))
    skipped = {
        s['period']: s['reason'] for s in read_skips(repo, env) if s.get('workflow') == workflow
    }
    return Facts(succeeded, failed, skipped)


def read_skips(repo: str, env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    path = paths.project_state(repo, env) / SKIPS
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise LedgerError(f'{path}: is not valid JSON: {exc}') from exc
    return data if isinstance(data, list) else []


def skip(
    repo: str,
    workflow: str,
    period: str,
    reason: str,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> str:
    """Record that a person chose not to run a workflow for a period."""
    try:
        schedules.from_key(period)
    except schedules.ScheduleError as exc:
        raise LedgerError(str(exc)) from exc
    if not reason.strip():
        raise LedgerError('--reason is empty: say why, for whoever reads the history')
    found = [s for s in read_skips(repo, env) if (s['workflow'], s['period']) != (workflow, period)]
    stamp = (now or datetime.now(UTC)).strftime('%Y-%m-%dT%H:%M:%SZ')
    found.append({'workflow': workflow, 'period': period, 'reason': reason.strip(), 'at': stamp})
    path = paths.project_state(repo, env) / SKIPS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(found, indent=2) + '\n', encoding='utf-8')
    return f'{workflow} {period}: skipped\n'


@dataclass
class Slot:
    key: str
    due: datetime
    state: str
    # The run that did it, the failed run or log, or why it was skipped.
    detail: str = ''


def history(
    repo: str,
    workflow: str,
    spec: skills.Workflow,
    now: datetime,
    count: int = 12,
    env: Mapping[str, str] | None = None,
) -> tuple[project.Planned, list[Slot], Slot | None]:
    """The last `count` slots due, newest last, and the next one."""
    planned = project.planned(repo, workflow, spec, env)
    schedule = planned.schedule
    if schedule.interval is None:
        return planned, [], None
    grace = schedules.duration(spec.grace)
    known = facts(repo, workflow, env)
    # Far enough back for `count` slots of this interval.
    span = {
        'hourly': timedelta(hours=1),
        'daily': timedelta(days=1),
        'weekly': timedelta(weeks=1),
        'monthly': timedelta(days=31),
        'quarterly': timedelta(days=92),
        'yearly': timedelta(days=366),
    }[schedule.interval]
    start = now - span * (count + 1)
    # Periods count from when the schedule was set, or the project added. Without either, only
    # the latest period is judged: a project set up before mason recorded the date would
    # otherwise show a year of periods nobody scheduled as overdue.
    began = planned.since or project.added(repo, env)
    if began is not None:
        start = max(start, datetime.combine(began, datetime.min.time()))
    else:
        latest = schedules.slots(schedule, spec.kind, start, now + timedelta(seconds=1))
        start = latest[-1].due if latest else start
    found = []
    for s in schedules.slots(schedule, spec.kind, start, now + timedelta(seconds=1)):
        if s.key in known.succeeded:
            found.append(Slot(s.key, s.due, DONE, known.succeeded[s.key]))
        elif s.key in known.skipped:
            found.append(Slot(s.key, s.due, SKIPPED, known.skipped[s.key]))
        elif s.key in known.failed:
            found.append(Slot(s.key, s.due, FAILED, known.failed[s.key]))
        elif now < s.due + grace:
            found.append(Slot(s.key, s.due, DUE))
        else:
            found.append(Slot(s.key, s.due, OVERDUE))
    upcoming = schedules.next_slot(schedule, spec.kind, now)
    following = Slot(upcoming.key, upcoming.due, UPCOMING) if upcoming else None
    return planned, found[-count:], following


@dataclass
class Row:
    repo: str
    workflow: str
    schedule: str
    source: str
    latest: Slot | None
    following: Slot | None
    # Every slot that needs something: due, overdue or failed.
    open: list[Slot]


def overview(
    repos: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> list[Row]:
    """Each enabled, scheduled workflow of each active project, with its state."""
    from . import projects

    now = now or datetime.now()
    rows = []
    for repo in repos if repos is not None else projects.added(env):
        if project.archived(repo, env):
            continue
        mapping = project.skill_paths(repo, env)
        for workflow in project.enabled_workflows(repo, env):
            try:
                skill = skills.resolve(workflow, mapping, env)
            except skills.SkillError:
                continue  # mason inbox reports it
            if skill is None or skill.workflow is None:
                continue
            planned, slots, following = history(repo, workflow, skill.workflow, now, env=env)
            rows.append(
                Row(
                    repo,
                    workflow,
                    str(planned.schedule),
                    planned.source,
                    slots[-1] if slots else None,
                    following,
                    [s for s in slots if s.state in (DUE, OVERDUE, FAILED)],
                )
            )
    return rows


def _when(moment: datetime) -> str:
    return f'{moment:%Y-%m-%d %H:%M}'


def render_status(rows: Sequence[Row]) -> str:
    if not rows:
        return 'No scheduled workflows.\n'
    lines = []
    for repo in dict.fromkeys(r.repo for r in rows):
        lines.append(repo)
        for row in (r for r in rows if r.repo == repo):
            latest = f'{row.latest.key} {row.latest.state}' if row.latest else '–'
            following = (
                f'next {row.following.key} at {_when(row.following.due)}'
                if row.following
                else 'not scheduled'
            )
            source = '' if row.source == 'skill' else '  (set for this project)'
            lines.append(
                f'  {row.workflow:<28} {row.schedule:<22} {latest:<22} {following}{source}'
            )
    return '\n'.join(lines) + '\n'


def render_open(rows: Sequence[Row], states: Sequence[str]) -> str:
    lines = []
    for row in rows:
        for s in row.open:
            if s.state in states:
                detail = f'  ({s.detail})' if s.detail else ''
                lines.append(
                    f'{row.repo}  {row.workflow}  {s.key}  {s.state}, due {_when(s.due)}{detail}'
                )
    return '\n'.join(lines) + '\n' if lines else ''


def render_history(workflow: str, slots: Sequence[Slot], following: Slot | None) -> str:
    lines = [workflow]
    for s in slots:
        detail = f'  {s.detail}' if s.detail else ''
        lines.append(f'  {s.key:<14} {s.state:<9} due {_when(s.due)}{detail}')
    if following:
        lines.append(f'  {following.key:<14} {following.state:<9} due {_when(following.due)}')
    return '\n'.join(lines) + '\n'
