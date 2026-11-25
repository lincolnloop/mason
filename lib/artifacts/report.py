"""`dependency-report/1`: a month's vulnerability figures, for the people the project answers to.

The report says what was fixed, what is still open and how fast fixes came, for one calendar
month and for the year so far. Every figure comes from the Dependabot alert history through
`outcomes.classify`, the same rules the retrospective counts with, so the two never disagree.
Nothing here is judged; the skill adds the commentary.

`build` is pure: it takes the alerts and the period and returns the artifact. The data is
embedded in the published report, so a figure, once published, stays as it was published even if
an advisory is later revised. See schemas/dependency-report.md.
"""

import re
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from typing import Any

from . import deps, outcomes
from ._check import ENVELOPE_FIELDS, Checker, check_envelope
from .run import ID_PATTERN as RUN_ID_PATTERN

SCHEMA = 'dependency-report/1'
SKILL = 'dependency-report'
MONTH_PATTERN = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')

FIELDS = ('run', 'period', 'policy', 'month', 'year_to_date', 'months', 'groups', 'open')
# Where a vulnerable package is, from the manifest its alert is on. The project lists which of
# its manifests each group covers (`[dependency-report]`); when it lists none, Dependabot's own
# runtime/development label is used, which can be wrong (it guesses from file names and from
# how a package was declared).
GROUPS = ('production', 'production_build', 'development', 'not_deployed', 'unclassified')
CONFIGURED_GROUPS = GROUPS[:-1]
GROUP_FIELDS = (
    'fixed',
    'advisories_fixed',
    'open_at_end',
    'advisories_open_at_end',
    'critical_open_at_end',
)
BY_SEVERITY = (
    'opened',
    'fixed',
    'moved',
    'dismissed',
    'open_at_start',
    'open_at_end',
    'fixed_within_target',
)
SUMMARY_FIELDS = (*BY_SEVERITY, 'advisories', 'days_to_fix')
MONTH_FIELDS = ('month', 'fixed', 'advisories_fixed', 'open_at_end', 'critical_open_at_end')
OPEN_FIELDS = (
    'ecosystem',
    'package',
    'severity',
    'alerts',
    'advisories',
    'oldest_days',
    'manifests',
    'groups',
)


# --- Periods -----------------------------------------------------------------------------------


def parse_month(value: str) -> tuple[int, int]:
    """'2026-11' -> (2026, 11). Raises ValueError for anything else."""
    if not MONTH_PATTERN.fullmatch(value):
        raise ValueError(f'a month must be YYYY-MM, got {value!r}')
    year, month = value.split('-')
    return int(year), int(month)


def previous_month(now: datetime) -> tuple[int, int]:
    """The last complete calendar month before `now`."""
    return (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)


def month_bounds(year: int, month: int, now: datetime) -> tuple[datetime, datetime, bool]:
    """(since, until, complete) for a calendar month in UTC. A month still in progress ends now.

    Raises ValueError for a month that hasn't started.
    """
    since = datetime(year, month, 1, tzinfo=UTC)
    end = (
        datetime(year + 1, 1, 1, tzinfo=UTC)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=UTC)
    )
    if since > now:
        raise ValueError(f'{year}-{month:02d} has not started yet')
    return (since, end, True) if end <= now else (since, now, False)


# --- Building ----------------------------------------------------------------------------------


def build(
    *,
    repo: str,
    run: dict[str, Any],
    year: int,
    month: int,
    now: datetime,
    fix_within_days: dict[str, int],
    alerts: list[dict[str, Any]],
    manifests: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """The report's data for one month, from every alert the repository has. Pure: no network.

    `manifests` maps each of CONFIGURED_GROUPS to the manifest globs it covers.
    """
    manifests = {g: list((manifests or {}).get(g) or []) for g in CONFIGURED_GROUPS}
    since, until, complete = month_bounds(year, month, now)
    year_start = datetime(year, 1, 1, tzinfo=UTC)
    return {
        'schema': SCHEMA,
        'project': repo,
        'produced_by': {'skill': SKILL, 'automation_sha': run['produced_by']['automation_sha']},
        'run': run['id'],
        'period': {
            'month': f'{year}-{month:02d}',
            'since': _stamp(since),
            'until': _stamp(until),
            'complete': complete,
        },
        'policy': {'fix_within_days': dict(fix_within_days)},
        'month': summarise(alerts, since, until, fix_within_days),
        'year_to_date': {
            'since': _stamp(year_start),
            **summarise(alerts, year_start, until, fix_within_days),
        },
        'months': [_month_row(alerts, year, m, now) for m in range(1, month + 1)],
        'groups': {
            'source': 'configured' if any(manifests.values()) else 'dependabot',
            'manifests': manifests,
            'month': by_group(alerts, since, until, manifests),
            'year_to_date': by_group(alerts, year_start, until, manifests),
        },
        'open': open_by_package(alerts, since, until, manifests),
    }


def group_of(alert: dict[str, Any], manifests: dict[str, list[str]]) -> str:
    """The group an alert's manifest is in: the first configured group with a matching glob."""
    dependency = alert.get('dependency') or {}
    path = dependency.get('manifest_path') or ''
    if any(manifests.values()):
        for group in CONFIGURED_GROUPS:
            if any(fnmatchcase(path, glob) for glob in manifests.get(group, [])):
                return group
        return 'unclassified'
    return {'runtime': 'production', 'development': 'development'}.get(
        dependency.get('scope') or '', 'unclassified'
    )


def by_group(
    alerts: list[dict[str, Any]],
    since: datetime,
    until: datetime,
    manifests: dict[str, list[str]],
) -> dict[str, dict[str, int]]:
    """Fixed and still-open alerts and advisories in each group. An advisory found in two
    groups counts in both."""
    counts = {g: dict.fromkeys(GROUP_FIELDS, 0) for g in GROUPS}
    fixed: dict[str, set[str]] = {g: set() for g in GROUPS}
    still_open: dict[str, set[str]] = {g: set() for g in GROUPS}
    for item in outcomes.classify(alerts, since, until):
        group = group_of(item.alert, manifests)
        if item.status == 'fixed':
            counts[group]['fixed'] += 1
            fixed[group].add(item.advisory)
        elif item.status == 'open':
            counts[group]['open_at_end'] += 1
            still_open[group].add(item.advisory)
            if item.severity == 'critical':
                counts[group]['critical_open_at_end'] += 1
    for group in GROUPS:
        counts[group]['advisories_fixed'] = len(fixed[group])
        counts[group]['advisories_open_at_end'] = len(still_open[group])
    return counts


def summarise(
    alerts: list[dict[str, Any]],
    since: datetime,
    until: datetime,
    fix_within_days: dict[str, int],
) -> dict[str, Any]:
    """The figures for one period, by severity."""
    history = outcomes.alert_history(alerts, since, until, fix_within_days, set())
    within = dict.fromkeys(deps.SEVERITIES, 0)
    for item in outcomes.classify(alerts, since, until):
        if item.status == 'fixed' and (item.days_to_fix or 0) <= fix_within_days[item.severity]:
            within[item.severity] += 1
    at_start = dict.fromkeys(deps.SEVERITIES, 0)
    for item in outcomes.classify(alerts, since, since):
        if item.status == 'open':
            at_start[item.severity] += 1
    return {
        'opened': history['opened'],
        'fixed': history['fixed'],
        'moved': history['moved'],
        'dismissed': history['dismissed'],
        'open_at_start': at_start,
        'open_at_end': history['open_at_end'],
        'fixed_within_target': within,
        'advisories': history['advisories'],
        'days_to_fix': history['days_to_fix'],
    }


def _month_row(
    alerts: list[dict[str, Any]], year: int, month: int, now: datetime
) -> dict[str, Any]:
    since, until, _ = month_bounds(year, month, now)
    history = outcomes.alert_history(alerts, since, until, dict.fromkeys(deps.SEVERITIES, 1), set())
    return {
        'month': f'{year}-{month:02d}',
        'fixed': sum(history['fixed'].values()),
        'advisories_fixed': sum(history['advisories']['fixed'].values()),
        'open_at_end': sum(history['open_at_end'].values()),
        'critical_open_at_end': history['open_at_end']['critical'],
    }


def open_by_package(
    alerts: list[dict[str, Any]],
    since: datetime,
    until: datetime,
    manifests: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """What is still open at the period's end, one row per package: most severe, then oldest."""
    rank = {severity: index for index, severity in enumerate(deps.SEVERITIES)}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for item in outcomes.classify(alerts, since, until):
        if item.status != 'open':
            continue
        package = item.alert['dependency']['package']
        ecosystem = package['ecosystem']
        name = deps.normalise(ecosystem, package['name'])
        group = groups.setdefault(
            (ecosystem, name),
            {
                'severity': item.severity,
                'alerts': 0,
                'advisories': set(),
                'oldest': item.created,
                'manifests': set(),
                'groups': set(),
            },
        )
        group['groups'].add(group_of(item.alert, manifests or {}))
        group['alerts'] += 1
        group['advisories'].add(item.advisory)
        group['oldest'] = min(group['oldest'], item.created)
        if rank[item.severity] < rank[group['severity']]:
            group['severity'] = item.severity
        manifest = (item.alert.get('dependency') or {}).get('manifest_path')
        if manifest:
            group['manifests'].add(manifest)
    rows = [
        {
            'ecosystem': ecosystem,
            'package': name,
            'severity': group['severity'],
            'alerts': group['alerts'],
            'advisories': len(group['advisories']),
            'oldest_days': round((until - group['oldest']).total_seconds() / 86400, 1),
            'manifests': sorted(group['manifests']),
            'groups': [g for g in GROUPS if g in group['groups']],
        }
        for (ecosystem, name), group in groups.items()
    ]
    return sorted(rows, key=lambda r: (rank[r['severity']], -r['oldest_days'], r['package']))


# --- Validation --------------------------------------------------------------------------------


def validate(artifact: Any) -> list[str]:
    checker = Checker()
    if not checker.object(artifact, '$', ENVELOPE_FIELDS + FIELDS):
        return checker.errors
    check_envelope(checker, artifact, SCHEMA)
    checker.string(artifact.get('run'), '$.run', pattern=RUN_ID_PATTERN, pattern_hint='a run id')
    period = artifact.get('period')
    if checker.object(period, '$.period', ('month', 'since', 'until', 'complete')):
        checker.string(
            period.get('month'), '$.period.month', pattern=MONTH_PATTERN, pattern_hint='YYYY-MM'
        )
        checker.timestamp(period.get('since'), '$.period.since')
        checker.timestamp(period.get('until'), '$.period.until')
        checker.boolean(period.get('complete'), '$.period.complete')
    policy = artifact.get('policy')
    if checker.object(policy, '$.policy', ('fix_within_days',)):
        _check_severities(
            checker, policy.get('fix_within_days'), '$.policy.fix_within_days', minimum=1
        )
    _check_summary(checker, artifact.get('month'), '$.month', ())
    ytd = artifact.get('year_to_date')
    _check_summary(checker, ytd, '$.year_to_date', ('since',))
    if isinstance(ytd, dict):
        checker.timestamp(ytd.get('since'), '$.year_to_date.since')
    checker.array(artifact.get('months'), '$.months', lambda v, p: _check_month(checker, v, p))
    _check_groups(checker, artifact.get('groups'))
    checker.array(artifact.get('open'), '$.open', lambda v, p: _check_open(checker, v, p))
    return checker.errors


def _check_groups(checker: Checker, value: Any) -> None:
    path = '$.groups'
    if not checker.object(value, path, ('source', 'manifests', 'month', 'year_to_date')):
        return
    checker.choice(value.get('source'), f'{path}.source', ('configured', 'dependabot'))
    manifests = value.get('manifests')
    if checker.object(manifests, f'{path}.manifests', CONFIGURED_GROUPS):
        for group in CONFIGURED_GROUPS:
            checker.string_array(manifests.get(group), f'{path}.manifests.{group}')
    for period in ('month', 'year_to_date'):
        counts = value.get(period)
        if checker.object(counts, f'{path}.{period}', GROUPS):
            for group in GROUPS:
                where = f'{path}.{period}.{group}'
                if checker.object(counts.get(group), where, GROUP_FIELDS):
                    for field in GROUP_FIELDS:
                        checker.integer(counts[group].get(field), f'{where}.{field}', minimum=0)


def _check_severities(checker: Checker, value: Any, path: str, minimum: int = 0) -> None:
    if checker.object(value, path, deps.SEVERITIES):
        for severity in deps.SEVERITIES:
            checker.integer(value.get(severity), f'{path}.{severity}', minimum=minimum)


def _check_summary(checker: Checker, value: Any, path: str, extra: tuple[str, ...]) -> None:
    if not checker.object(value, path, (*extra, *SUMMARY_FIELDS)):
        return
    for field in BY_SEVERITY:
        _check_severities(checker, value.get(field), f'{path}.{field}')
    advisories = value.get('advisories')
    if checker.object(advisories, f'{path}.advisories', ('fixed', 'open_at_end')):
        for field in ('fixed', 'open_at_end'):
            _check_severities(checker, advisories.get(field), f'{path}.advisories.{field}')
    days = value.get('days_to_fix')
    if checker.object(days, f'{path}.days_to_fix', deps.SEVERITIES):
        for severity in deps.SEVERITIES:
            timing = days.get(severity)
            where = f'{path}.days_to_fix.{severity}'
            if checker.object(timing, where, ('count', 'median', 'max')):
                checker.integer(timing.get('count'), f'{where}.count', minimum=0)
                for key in ('median', 'max'):
                    number = timing.get(key)
                    if number is not None and (
                        not isinstance(number, int | float) or isinstance(number, bool)
                    ):
                        checker.error(f'{where}.{key}', 'must be a number or null')


def _check_month(checker: Checker, value: Any, path: str) -> None:
    if checker.object(value, path, MONTH_FIELDS):
        checker.string(
            value.get('month'), f'{path}.month', pattern=MONTH_PATTERN, pattern_hint='YYYY-MM'
        )
        for field in MONTH_FIELDS[1:]:
            checker.integer(value.get(field), f'{path}.{field}', minimum=0)


def _check_open(checker: Checker, value: Any, path: str) -> None:
    if checker.object(value, path, OPEN_FIELDS):
        # Dependabot's own ecosystem names (pip, npm, actions, …): reported, never acted on.
        checker.string(value.get('ecosystem'), f'{path}.ecosystem')
        checker.string(value.get('package'), f'{path}.package')
        checker.choice(value.get('severity'), f'{path}.severity', deps.SEVERITIES)
        checker.integer(value.get('alerts'), f'{path}.alerts', minimum=1)
        checker.integer(value.get('advisories'), f'{path}.advisories', minimum=1)
        oldest = value.get('oldest_days')
        if not isinstance(oldest, int | float) or isinstance(oldest, bool) or oldest < 0:
            checker.error(f'{path}.oldest_days', 'must be a number of days, 0 or more')
        checker.string_array(value.get('manifests'), f'{path}.manifests')
        checker.array(
            value.get('groups'),
            f'{path}.groups',
            lambda v, p: checker.choice(v, p, GROUPS),
            min_items=1,
        )


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
