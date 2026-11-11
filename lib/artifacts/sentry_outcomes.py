"""`sentry-outcomes/1`: whether the fixes `sentry-errors` shipped made their errors stop.

The facts the Sentry retrospective judges, collected by a script:

- each fix PR: merged (and edited first), closed or still open, its CI results, what people said,
  and its **signal**: for every Sentry issue the fix was meant to quiet, the issue's last event,
  and whether that came after the fix was merged and deployed;
- each triage issue the workflow opened, and whether it is still open;
- how many issues are unresolved now, by environment.

A fix's signal is `met` when none of its issues has had an event since the merge (plus a grace
period for the deploy) and `quiet_after_days` have passed; `not_met` as soon as one has; and
`pending` until then. `build` is pure; `collect` fetches. See schemas/sentry-outcomes.md.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from . import collect as fetch
from . import embed, outcomes, sentry
from ._check import ENVELOPE_FIELDS, Checker, check_envelope
from .run import ID_PATTERN as RUN_ID_PATTERN

SCHEMA = 'sentry-outcomes/1'
SIGNALS = ('met', 'not_met', 'pending', 'not_applicable')
FIELDS = ('run', 'period', 'policy', 'prs', 'triage_issues', 'unresolved_now')


def validate(artifact: Any) -> list[str]:
    checker = Checker()
    if not checker.object(artifact, '$', ENVELOPE_FIELDS + FIELDS):
        return checker.errors
    check_envelope(checker, artifact, SCHEMA)
    checker.string(artifact.get('run'), '$.run', pattern=RUN_ID_PATTERN, pattern_hint='a run id')
    period = artifact.get('period')
    if checker.object(period, '$.period', ('since', 'until')):
        checker.timestamp(period.get('since'), '$.period.since')
        checker.timestamp(period.get('until'), '$.period.until')
    checker.object(
        artifact.get('policy'),
        '$.policy',
        ('quiet_after_days', 'deploy_grace_hours', 'stale_pr_after_days'),
    )

    def pr(value: Any, path: str) -> None:
        if checker.object(value, path, (*outcomes.PR_FIELDS, 'signal')):
            signal = value.get('signal')
            if checker.object(signal, f'{path}.signal', ('status', 'issues')):
                checker.choice(signal.get('status'), f'{path}.signal.status', SIGNALS)

    checker.array(artifact.get('prs'), '$.prs', pr)
    checker.array(
        artifact.get('triage_issues'),
        '$.triage_issues',
        lambda v, p: checker.object(v, p, ('number', 'url', 'state', 'created_at', 'findings')),
    )
    unresolved = artifact.get('unresolved_now')
    if not isinstance(unresolved, dict):
        checker.error('$.unresolved_now', 'must be an object of environment: count')
    else:
        for environment, count in unresolved.items():
            checker.integer(count, f'$.unresolved_now.{environment}', minimum=0)
    return checker.errors


def build(
    *,
    repo: str,
    run: dict[str, Any],
    since: datetime,
    until: datetime,
    policy: dict[str, Any],
    raw_prs: list[dict[str, Any]],
    last_events: dict[str, str | None],
    raw_triage: list[dict[str, Any]],
    unresolved_now: dict[str, int],
) -> dict[str, Any]:
    """`last_events` maps each Sentry short id to its last event's time (None if unknown). Pure."""
    prs = []
    for raw in raw_prs:
        record = outcomes.pr_record(raw, until, policy)
        if not outcomes.in_period(record, since):
            continue
        prs.append({**record, 'signal': signal(record, _refs(raw), last_events, until, policy)})
    return {
        'schema': SCHEMA,
        'project': repo,
        'produced_by': {
            'skill': 'sentry-retrospective',
            'automation_sha': run['produced_by']['automation_sha'],
        },
        'run': run['id'],
        'period': {'since': _stamp(since), 'until': _stamp(until)},
        'policy': policy,
        'prs': sorted(prs, key=lambda p: p['number']),
        'triage_issues': [_triage(issue) for issue in raw_triage],
        'unresolved_now': unresolved_now,
    }


def signal(
    pr: dict[str, Any],
    refs: list[str],
    last_events: dict[str, str | None],
    until: datetime,
    policy: dict[str, Any],
) -> dict[str, Any]:
    if pr['state'] != 'merged':
        return {'status': 'not_applicable', 'issues': [_issue(r, last_events, None) for r in refs]}
    fixed_at = _parse(pr['decided_at']) + timedelta(hours=policy['deploy_grace_hours'])
    issues = [_issue(r, last_events, fixed_at) for r in refs]
    if any(i['after_fix'] for i in issues):
        status = 'not_met'
    elif until - _parse(pr['decided_at']) >= timedelta(days=policy['quiet_after_days']):
        status = 'met'
    else:
        status = 'pending'
    return {'status': status, 'issues': issues}


def _issue(
    short_id: str, last_events: dict[str, str | None], fixed_at: datetime | None
) -> dict[str, Any]:
    last = last_events.get(short_id)
    return {
        'short_id': short_id,
        'last_event': last,
        'after_fix': bool(fixed_at and last and _parse(last) > fixed_at),
    }


def _refs(raw: dict[str, Any]) -> list[str]:
    """The Sentry issues the PR's change said it would quiet."""
    try:
        changes = embed.extract(raw.get('body') or '', 'change/1')
    except ValueError:
        return []
    for change in changes:
        if change['success_signal']['kind'] == 'sentry_quiet':
            return list(change['success_signal']['refs'])
    return []


def _triage(issue: dict[str, Any]) -> dict[str, Any]:
    try:
        found = embed.extract(issue.get('body') or '', 'finding/1')
    except ValueError:
        found = []
    return {
        'number': issue['number'],
        'url': issue['url'],
        'state': issue['state'].lower(),
        'created_at': issue['createdAt'],
        'findings': sorted(f['fingerprint'] for f in found),
    }


def collect(
    repo: str,
    run: dict[str, Any],
    since: datetime,
    until: datetime,
    policy: dict[str, Any],
    settings: dict[str, Any],
    client: sentry.Client,
) -> dict[str, Any]:
    raw_prs = fetch.upgrade_prs(repo, workflow='sentry-errors')
    last_events: dict[str, str | None] = {}
    for raw in raw_prs:
        for short_id in _refs(raw):
            if short_id in last_events:
                continue
            try:
                issue_id = client.by_short_id(short_id)
                seen = [
                    client.issue(issue_id, env)['last_seen'] for env in settings['environments']
                ]
                last_events[short_id] = max((s for s in seen if s), default=None)
            except (sentry.SentryError, KeyError):
                last_events[short_id] = None
    raw_triage = json.loads(
        fetch.gh(
            'issue',
            'list',
            '--repo',
            repo,
            '--label',
            'automation',
            '--state',
            'all',
            '--search',
            'in:title "Sentry triage"',
            '--limit',
            '200',
            '--json',
            'number,url,state,createdAt,body',
        )
    )
    raw_triage = [i for i in raw_triage if _parse(i['createdAt']) >= since]
    period = f'{settings["period_days"]}d'
    unresolved = {env: len(client.unresolved(env, period)) for env in settings['environments']}
    return build(
        repo=repo,
        run=run,
        since=since,
        until=until,
        policy=policy,
        raw_prs=raw_prs,
        last_events=last_events,
        raw_triage=raw_triage,
        unresolved_now=unresolved,
    )


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _stamp(value: datetime) -> str:
    return f'{value.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}'
