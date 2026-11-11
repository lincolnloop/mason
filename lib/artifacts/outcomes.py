"""`outcomes/1`: what happened to a workflow's PRs and to the project's alerts over a period.

The facts a retrospective judges, collected by a script so the model never counts or dates
anything itself. Everything here is read from GitHub, the project's git history and the
package registries:

- each upgrade PR: merged, merged after someone changed it, closed or still open, its CI
  results, the alerts it closed, who started its run, and what people said on it;
- each run that stopped because an upgrade PR was still open;
- upgrade commits that were later reverted;
- the alert history: how long fixes took by severity against the project's targets, and what
  is still open past them;
- the health of every package with an open alert or a finding: its latest release, whether it
  is deprecated, and whether its source repository is archived.

`build` is pure: it takes what was fetched and returns the artifact. `collect` (in collect.py)
does the fetching. See schemas/outcomes.md.
"""

from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from . import deps, embed
from ._check import ENVELOPE_FIELDS, Checker, check_envelope
from .run import ID_PATTERN as RUN_ID_PATTERN

SCHEMA = 'outcomes/1'

PR_STATES = ('open', 'merged', 'closed')
PACKAGE_FLAGS = ('stale', 'deprecated', 'archived')
# Check conclusions (CheckRun) and states (StatusContext), as gh reports them.
PASSED = {'SUCCESS', 'NEUTRAL', 'SKIPPED'}
FAILED = {'FAILURE', 'ERROR', 'TIMED_OUT', 'CANCELLED', 'ACTION_REQUIRED', 'STARTUP_FAILURE'}
COMMENT_LIMIT = 2000

FIELDS = ('run', 'period', 'policy', 'prs', 'stopped_runs', 'reverts', 'alerts', 'packages')
# The fields of one PR's record, shared with the other workflows' outcomes.
PR_FIELDS = (
    'number',
    'url',
    'title',
    'state',
    'draft',
    'created_at',
    'decided_at',
    'days_open',
    'stale',
    'run',
    'edited',
    'ci',
    'alerts_closed',
    'findings',
    'comments',
)


# --- Validation --------------------------------------------------------------------------------


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
    policy = artifact.get('policy')
    if checker.object(
        policy, '$.policy', ('fix_within_days', 'stale_after_months', 'stale_pr_after_days')
    ):
        within = policy.get('fix_within_days')
        if checker.object(within, '$.policy.fix_within_days', deps.SEVERITIES):
            for severity in deps.SEVERITIES:
                checker.integer(
                    within.get(severity), f'$.policy.fix_within_days.{severity}', minimum=1
                )
        checker.integer(policy.get('stale_after_months'), '$.policy.stale_after_months', minimum=1)
        checker.integer(
            policy.get('stale_pr_after_days'), '$.policy.stale_pr_after_days', minimum=1
        )
    checker.array(artifact.get('prs'), '$.prs', lambda v, p: _check_pr(checker, v, p))
    checker.array(
        artifact.get('stopped_runs'),
        '$.stopped_runs',
        lambda v, p: checker.object(
            v, p, ('id', 'trigger', 'started_by', 'started_at', 'pr', 'alerts_not_covered')
        ),
    )
    checker.array(
        artifact.get('reverts'),
        '$.reverts',
        lambda v, p: checker.object(v, p, ('sha', 'date', 'subject', 'package')),
    )
    alerts = artifact.get('alerts')
    checker.object(
        alerts,
        '$.alerts',
        (
            'open_now',
            'opened',
            'fixed',
            'dismissed',
            'fixed_by_upgrade_prs',
            'days_to_fix',
            'open_past_target',
        ),
    )
    checker.array(
        artifact.get('packages'), '$.packages', lambda v, p: _check_package(checker, v, p)
    )
    return checker.errors


def _check_pr(checker: Checker, value: Any, path: str) -> None:
    if checker.object(value, path, PR_FIELDS):
        checker.integer(value.get('number'), f'{path}.number', minimum=1)
        checker.choice(value.get('state'), f'{path}.state', PR_STATES)
        checker.timestamp(value.get('created_at'), f'{path}.created_at')
        checker.timestamp(value.get('decided_at'), f'{path}.decided_at', nullable=True)


def _check_package(checker: Checker, value: Any, path: str) -> None:
    fields = (
        'ecosystem',
        'package',
        'latest',
        'latest_released_at',
        'deprecated',
        'repository',
        'archived',
        'open_alerts',
        'flags',
        'error',
    )
    if checker.object(value, path, fields):
        checker.choice(value.get('ecosystem'), f'{path}.ecosystem', ('pip', 'npm'))
        checker.string(value.get('package'), f'{path}.package')
        checker.array(
            value.get('flags'), f'{path}.flags', lambda v, p: checker.choice(v, p, PACKAGE_FLAGS)
        )


# --- Building ----------------------------------------------------------------------------------


def build(
    *,
    repo: str,
    run: dict[str, Any],
    since: datetime,
    until: datetime,
    policy: dict[str, Any],
    raw_prs: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    reverts: list[dict[str, Any]],
    packages: list[dict[str, Any]],
) -> dict[str, Any]:
    """The outcomes artifact, from what `collect` fetched. Pure: no network, no clock."""
    prs = [pr for pr in (pr_record(raw, until, policy) for raw in raw_prs) if in_period(pr, since)]
    merged_closes = {n for pr in prs if pr['state'] == 'merged' for n in pr['alerts_closed']}
    return {
        'schema': SCHEMA,
        'project': repo,
        'produced_by': {
            'skill': 'dependency-retrospective',
            'automation_sha': run['produced_by']['automation_sha'],
        },
        'run': run['id'],
        'period': {'since': _stamp(since), 'until': _stamp(until)},
        'policy': policy,
        'prs': sorted(prs, key=lambda pr: pr['number']),
        'stopped_runs': _stopped_runs(raw_prs, since, until),
        'reverts': reverts,
        'alerts': alert_history(alerts, since, until, policy['fix_within_days'], merged_closes),
        'packages': [_flag(p, until, policy['stale_after_months']) for p in packages],
    }


def pr_record(raw: dict[str, Any], until: datetime, policy: dict[str, Any]) -> dict[str, Any]:
    artifacts = _embedded(raw.get('body') or '')
    change = next((a for a in artifacts if a.get('schema') == 'change/1'), None)
    run = next((a for a in artifacts if a.get('schema') == 'run/1'), None)
    findings = [a for a in artifacts if a.get('schema') == 'finding/1']
    in_pr = set(change['findings']) if change else set()
    state = raw['state'].lower()
    created = _parse(raw['createdAt'])
    decided = raw.get('mergedAt') or raw.get('closedAt')
    end = _parse(decided) if decided and state != 'open' else until
    days_open = round((end - created) / timedelta(days=1), 1)
    opened_at = change.get('head_sha') if change else None
    return {
        'number': raw['number'],
        'url': raw['url'],
        'title': raw['title'],
        'state': state,
        'draft': bool(raw.get('isDraft')),
        'created_at': _stamp(created),
        'decided_at': _stamp(end) if state != 'open' else None,
        'days_open': days_open,
        'stale': state == 'open' and days_open > policy['stale_pr_after_days'],
        'run': (
            {k: run[k] for k in ('id', 'trigger', 'started_by', 'started_at')} if run else None
        ),
        # Someone pushed to the PR after it was opened (for a merged PR: before merging it).
        'edited': None if opened_at is None else raw.get('headRefOid') != opened_at,
        'ci': _ci(raw.get('statusCheckRollup') or []),
        'alerts_closed': (
            [a['number'] for a in change.get('alerts', {}).get('closed', [])] if change else []
        ),
        'findings': {
            'in_pr': sorted(in_pr),
            'left_out': sorted(f['id'] for f in findings if f['id'] not in in_pr),
        },
        'comments': _said(raw),
    }


def _said(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """What people said on the PR, as comments or reviews, oldest first. A run's own record,
    posted as a comment, is data (`stopped_runs`), not something a person said."""
    said = [
        {
            'kind': 'comment',
            'author': (comment.get('author') or {}).get('login') or 'unknown',
            'created_at': comment['createdAt'],
            'body': comment['body'][:COMMENT_LIMIT],
        }
        for comment in raw.get('comments') or []
        if not _embedded_runs(comment.get('body') or '')
    ]
    said += [
        {
            'kind': f'review: {(review.get("state") or "").lower() or "comment"}',
            'author': (review.get('author') or {}).get('login') or 'unknown',
            'created_at': review.get('submittedAt') or '',
            'body': (review.get('body') or '')[:COMMENT_LIMIT],
        }
        for review in raw.get('reviews') or []
        if review.get('body') or review.get('state') in ('APPROVED', 'CHANGES_REQUESTED')
    ]
    return sorted(said, key=lambda item: item['created_at'])


def in_period(pr: dict[str, Any], since: datetime) -> bool:
    if pr['state'] == 'open':
        return True
    return _parse(pr['created_at']) >= since or _parse(pr['decided_at']) >= since


def _ci(rollup: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {'passed': [], 'failed': [], 'pending': []}
    for check in rollup:
        name = check.get('name') or check.get('context') or 'unnamed'
        outcome = (check.get('conclusion') or check.get('state') or '').upper()
        key = 'passed' if outcome in PASSED else 'failed' if outcome in FAILED else 'pending'
        result[key].append(name)
    return {key: sorted(names) for key, names in result.items()}


def _stopped_runs(
    raw_prs: list[dict[str, Any]], since: datetime, until: datetime
) -> list[dict[str, Any]]:
    stopped = []
    for raw in raw_prs:
        for comment in raw.get('comments') or []:
            for run in _embedded_runs(comment.get('body') or ''):
                if run.get('outcome') != 'stopped_pr_open':
                    continue
                if not since <= _parse(run['started_at']) < until:
                    continue
                stopped.append(
                    {
                        'id': run['id'],
                        'trigger': run['trigger'],
                        'started_by': run['started_by'],
                        'started_at': run['started_at'],
                        'pr': run['pr'],
                        'alerts_not_covered': len(run['alerts_not_covered']),
                    }
                )
    return sorted(stopped, key=lambda r: r['started_at'])


def alert_history(
    alerts: list[dict[str, Any]],
    since: datetime,
    until: datetime,
    fix_within_days: dict[str, int],
    closed_by_upgrade_prs: set[int],
) -> dict[str, Any]:
    """Counts, times to fix and overdue alerts, by severity, from every alert's own dates."""

    def tally() -> dict[str, int]:
        return dict.fromkeys(deps.SEVERITIES, 0)

    open_now, opened, fixed, dismissed = tally(), tally(), tally(), tally()
    days: dict[str, list[float]] = {s: [] for s in deps.SEVERITIES}
    fixed_by_prs: list[int] = []
    overdue: list[dict[str, Any]] = []
    for alert in alerts:
        summary = deps.alert_summary(alert)
        severity = summary['severity'] if summary['severity'] in open_now else 'low'
        created = _parse(alert['created_at'])
        if since <= created < until:
            opened[severity] += 1
        state = alert.get('state')
        if state == 'open':
            open_now[severity] += 1
            age = round((until - created) / timedelta(days=1), 1)
            if age > fix_within_days[severity]:
                overdue.append({**summary, 'severity': severity, 'age_days': age})
        elif state == 'fixed' and alert.get('fixed_at'):
            fixed_at = _parse(alert['fixed_at'])
            if since <= fixed_at < until:
                fixed[severity] += 1
                days[severity].append((fixed_at - created) / timedelta(days=1))
                if alert['number'] in closed_by_upgrade_prs:
                    fixed_by_prs.append(alert['number'])
        elif state in ('dismissed', 'auto_dismissed'):
            when = alert.get('dismissed_at') or alert.get('auto_dismissed_at')
            if when and since <= _parse(when) < until:
                dismissed[severity] += 1
    order = {severity: index for index, severity in enumerate(deps.SEVERITIES)}
    return {
        'open_now': open_now,
        'opened': opened,
        'fixed': fixed,
        'dismissed': dismissed,
        'fixed_by_upgrade_prs': sorted(fixed_by_prs),
        'days_to_fix': {
            severity: (
                {
                    'count': len(values),
                    'median': round(median(values), 1),
                    'max': round(max(values), 1),
                }
                if values
                else {'count': 0, 'median': None, 'max': None}
            )
            for severity, values in days.items()
        },
        'open_past_target': sorted(
            overdue, key=lambda a: (order[a['severity']], -a['age_days'], a['number'])
        ),
    }


def _flag(package: dict[str, Any], until: datetime, stale_after_months: int) -> dict[str, Any]:
    flags = []
    released = package.get('latest_released_at')
    if released and until - _parse(released) > timedelta(days=round(stale_after_months * 30.44)):
        flags.append('stale')
    if package.get('deprecated'):
        flags.append('deprecated')
    if package.get('archived'):
        flags.append('archived')
    return {**package, 'flags': flags}


# --- Helpers -----------------------------------------------------------------------------------


def revert_package(subject: str) -> str | None:
    """The package a revert of an upgrade commit undid: 'Revert "Upgrade qs to 6.17.1"' -> 'qs'."""
    prefix = 'Revert "Upgrade '
    if not subject.startswith(prefix):
        return None
    rest = subject[len(prefix) :]
    return rest.split(' to ', 1)[0] if ' to ' in rest else None


def _embedded(markdown: str) -> list[dict[str, Any]]:
    # A damaged block in someone's PR shouldn't stop the whole collection; the PR then shows
    # no run and no change, which the retrospective reports as such.
    try:
        return embed.extract(markdown)
    except ValueError:
        return []


def _embedded_runs(markdown: str) -> list[dict[str, Any]]:
    return [a for a in _embedded(markdown) if a.get('schema') == 'run/1']


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _stamp(value: datetime) -> str:
    return f'{value.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}'
