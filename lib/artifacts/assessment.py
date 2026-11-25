"""`assessment/1`: every open Dependabot alert, and what was decided about it.

`dependency-assessment` writes one each time it runs, into the month's assessment issue, with its
findings beside it. It is the hand-off to `dependency-upgrades`, which applies the `easy` findings
and refuses to start from an assessment that doesn't cover every alert open now; and to anyone
asking what is still open and why.

Every open alert is accounted for exactly once: by one finding (through the finding's
`source.refs`), or as already fixed by what is installed. `build` refuses anything else, so an
alert can't fall through the gaps between runs. See schemas/assessment.md.
"""

import re
from typing import Any

from . import deps, finding
from ._check import ENVELOPE_FIELDS, SHA_PATTERN, Checker, check_envelope
from .run import ID_PATTERN as RUN_ID_PATTERN

SCHEMA = 'assessment/1'
SKILL = 'dependency-assessment'
FIELDS = ('run', 'assessed_at', 'commit', 'alerts', 'findings')
ALERT_FIELDS = (
    'number',
    'ecosystem',
    'package',
    'manifest',
    'severity',
    'classification',
    'finding',
)
CLASSIFICATIONS = finding.CLASSIFICATIONS
REF = re.compile(r'^dependabot#(\d+)$')


class AssessmentError(ValueError):
    pass


def build(
    *,
    repo: str,
    run: dict[str, Any],
    assessed_at: str,
    commit: str,
    alerts: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    already_fixed: list[int],
) -> dict[str, Any]:
    """The assessment of every open alert in `alerts`, from the run's findings.

    Raises AssessmentError naming every alert that is unaccounted for, accounted for twice, or
    claimed by a finding but not open."""
    open_numbers = {a['number'] for a in alerts}
    claimed: dict[int, list[str]] = {}
    for item in findings:
        for ref in item.get('source', {}).get('refs', []):
            if match := REF.match(ref):
                claimed.setdefault(int(match.group(1)), []).append(item['id'])
    fixed = set(already_fixed)
    problems = []
    for number in sorted(open_numbers):
        owners = claimed.get(number, [])
        if len(owners) > 1:
            problems.append(f'alert #{number} is in more than one finding: {", ".join(owners)}')
        elif owners and number in fixed:
            problems.append(f'alert #{number} is in finding {owners[0]} and also already fixed')
        elif not owners and number not in fixed:
            problems.append(f'alert #{number} is in no finding and not already fixed')
    for number in sorted(set(claimed) - open_numbers):
        problems.append(f'alert #{number}, in {claimed[number][0]}, is not open')
    for number in sorted(fixed - open_numbers):
        problems.append(f'alert #{number}, given as already fixed, is not open')
    for item in findings:
        if item['id'] in {o for owners in claimed.values() for o in owners} and not item.get(
            'dependency'
        ):
            problems.append(f'finding {item["id"]} covers alerts but has no dependency block')
    if problems:
        raise AssessmentError('\n'.join(problems))
    by_id = {item['id']: item for item in findings}
    rows = []
    for alert in sorted(alerts, key=lambda a: a['number']):
        summary = deps.alert_summary(alert)
        owner = claimed.get(alert['number'], [None])[0]
        rows.append(
            {
                'number': alert['number'],
                'ecosystem': alert['dependency']['package']['ecosystem'],
                'package': summary['package'],
                'manifest': alert['dependency'].get('manifest_path') or '',
                'severity': summary['severity'],
                'classification': (
                    by_id[owner]['dependency']['classification'] if owner else 'already_fixed'
                ),
                'finding': owner,
            }
        )
    return {
        'schema': SCHEMA,
        'project': repo,
        'produced_by': {'skill': SKILL, 'automation_sha': run['produced_by']['automation_sha']},
        'run': run['id'],
        'assessed_at': assessed_at,
        'commit': commit,
        'alerts': rows,
        'findings': sorted(by_id),
    }


def uncovered(assessment: dict[str, Any], alerts: list[dict[str, Any]]) -> list[int]:
    """Alerts open now that the assessment doesn't cover: they opened after it was made."""
    assessed = {row['number'] for row in assessment['alerts']}
    return sorted(a['number'] for a in alerts if a['number'] not in assessed)


def changes(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, list[Any]]:
    """What changed since the previous assessment: alerts new, closed, and reclassified."""
    before = {row['number']: row for row in (previous or {}).get('alerts', [])}
    after = {row['number']: row for row in current['alerts']}
    return {
        'new': [after[n] for n in sorted(set(after) - set(before))],
        'closed': [before[n] for n in sorted(set(before) - set(after))],
        'reclassified': [
            {**after[n], 'was': before[n]['classification']}
            for n in sorted(set(before) & set(after))
            if before[n]['classification'] != after[n]['classification']
        ],
    }


def validate(artifact: Any) -> list[str]:
    checker = Checker()
    if not checker.object(artifact, '$', ENVELOPE_FIELDS + FIELDS):
        return checker.errors
    check_envelope(checker, artifact, SCHEMA)
    checker.string(artifact.get('run'), '$.run', pattern=RUN_ID_PATTERN, pattern_hint='a run id')
    checker.timestamp(artifact.get('assessed_at'), '$.assessed_at')
    checker.string(artifact.get('commit'), '$.commit', pattern=SHA_PATTERN, pattern_hint='a commit')
    findings = artifact.get('findings')
    checker.string_array(findings, '$.findings')
    known = set(findings) if isinstance(findings, list) else set()

    def row(value: Any, path: str) -> None:
        if not checker.object(value, path, ALERT_FIELDS):
            return
        checker.integer(value.get('number'), f'{path}.number', minimum=1)
        for key in ('ecosystem', 'package'):
            checker.string(value.get(key), f'{path}.{key}')
        if not isinstance(value.get('manifest'), str):
            checker.error(f'{path}.manifest', 'must be a string')
        checker.choice(value.get('severity'), f'{path}.severity', deps.SEVERITIES)
        checker.choice(value.get('classification'), f'{path}.classification', CLASSIFICATIONS)
        owner = value.get('finding')
        if value.get('classification') == 'already_fixed':
            if owner is not None:
                checker.error(f'{path}.finding', 'must be null for an already fixed alert')
        elif checker.string(owner, f'{path}.finding') and owner not in known:
            checker.error(f'{path}.finding', f'{owner!r} is not in $.findings')

    alerts = artifact.get('alerts')
    checker.array(alerts, '$.alerts', row)
    if isinstance(alerts, list):
        checker.unique(
            (
                (f'$.alerts[{i}]', a.get('number'))
                for i, a in enumerate(alerts)
                if isinstance(a, dict)
            ),
            'number',
        )
    return checker.errors
