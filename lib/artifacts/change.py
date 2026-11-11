"""`change/1`: what a workflow attaches to the pull request it opens. See schemas/change.md."""

import re
from typing import Any

from ._check import ENVELOPE_FIELDS, SHA_PATTERN, Checker, check_envelope
from .finding import ID_PATTERN
from .run import ID_PATTERN as RUN_ID_PATTERN

SCHEMA = 'change/1'

# ci: not run here; CI runs it on the pull request, and whoever merges checks the result.
CHECK_RESULTS = ('pass', 'ci', 'skipped')
# alerts_closed: the listed Dependabot alerts close. manual: a person checks, as described.
SIGNAL_KINDS = ('alerts_closed', 'manual')
BRANCH_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]*$')

FIELDS = (
    'run',
    'findings',
    'branch',
    'head_sha',
    'summary',
    'effect',
    'blast_radius',
    'verified',
    'not_verified',
    'success_signal',
    'revert',
)
# Derived by `bin/artifact deps-diff` and `alerts-closed`, never written by hand.
OPTIONAL = ('dependencies', 'alerts')
SEVERITIES = ('critical', 'high', 'medium', 'low')


def validate(artifact: Any) -> list[str]:
    checker = Checker()
    if not checker.object(artifact, '$', ENVELOPE_FIELDS + FIELDS, OPTIONAL):
        return checker.errors
    check_envelope(checker, artifact, SCHEMA)
    if 'dependencies' in artifact:
        checker.array(
            artifact['dependencies'],
            '$.dependencies',
            lambda v, p: _dependency(checker, v, p),
            min_items=1,
        )
    if 'alerts' in artifact:
        _alerts(checker, artifact['alerts'], '$.alerts')

    checker.string(artifact.get('run'), '$.run', pattern=RUN_ID_PATTERN, pattern_hint='a run id')
    findings = artifact.get('findings')
    checker.array(
        findings,
        '$.findings',
        lambda v, p: checker.string(v, p, pattern=ID_PATTERN, pattern_hint='a finding id'),
        min_items=1,
    )
    if isinstance(findings, list):
        checker.unique(((f'$.findings[{i}]', v) for i, v in enumerate(findings)), 'id')
    checker.string(
        artifact.get('branch'), '$.branch', pattern=BRANCH_PATTERN, pattern_hint='a branch name'
    )
    # The commit the PR was opened at. A merged head that differs means someone changed it first.
    checker.string(
        artifact.get('head_sha'), '$.head_sha', pattern=SHA_PATTERN, pattern_hint='a commit SHA'
    )
    checker.string(artifact.get('summary'), '$.summary', max_length=200)
    checker.string(artifact.get('effect'), '$.effect')
    checker.string_array(artifact.get('blast_radius'), '$.blast_radius', min_items=1)
    checker.array(artifact.get('verified'), '$.verified', lambda v, p: _check_run(checker, v, p))
    checker.string_array(artifact.get('not_verified'), '$.not_verified')
    verified = artifact.get('verified')
    not_verified = artifact.get('not_verified')
    if verified == [] and not_verified == []:
        checker.error(
            '$.verified', 'and $.not_verified are both empty: say what was checked, or what was not'
        )
    _signal(checker, artifact.get('success_signal'), '$.success_signal')
    checker.string(artifact.get('revert'), '$.revert')
    return checker.errors


def _check_run(checker: Checker, value: Any, path: str) -> None:
    if not checker.object(value, path, ('check', 'result'), ('note',)):
        return
    checker.string(value.get('check'), f'{path}.check')
    result = value.get('result')
    if result == 'fail':
        checker.error(
            f'{path}.result',
            'is fail: a change with a failing check is not ready for a pull request. Fix it, or '
            'move the check to not_verified and say why it cannot pass',
        )
    else:
        checker.choice(result, f'{path}.result', CHECK_RESULTS)
    if result == 'skipped' and not value.get('note'):
        checker.error(
            f'{path}.note', 'is required when result is skipped: say why the check did not run'
        )
    if 'note' in value:
        checker.string(value['note'], f'{path}.note')


def _signal(checker: Checker, value: Any, path: str) -> None:
    if not checker.object(value, path, ('kind', 'refs', 'check_after_days', 'description')):
        return
    kind = value.get('kind')
    checker.choice(kind, f'{path}.kind', SIGNAL_KINDS)
    checker.string_array(value.get('refs'), f'{path}.refs')
    if kind == 'alerts_closed' and value.get('refs') == []:
        checker.error(f'{path}.refs', f'must name what to check when kind is {kind}')
    checker.integer(
        value.get('check_after_days'), f'{path}.check_after_days', minimum=0, maximum=90
    )
    checker.string(value.get('description'), f'{path}.description')


def _dependency(checker: Checker, value: Any, path: str) -> None:
    fields = ('ecosystem', 'package', 'before', 'after', 'manifests', 'direct', 'via', 'copy')
    if not checker.object(value, path, fields):
        return
    checker.choice(value.get('ecosystem'), f'{path}.ecosystem', ('pip', 'npm'))
    checker.string(value.get('package'), f'{path}.package')
    checker.string(value.get('before'), f'{path}.before', nullable=True)
    checker.string(value.get('after'), f'{path}.after', nullable=True)
    if value.get('before') is None and value.get('after') is None:
        checker.error(path, 'has neither before nor after')
    elif value.get('before') == value.get('after'):
        checker.error(f'{path}.after', 'equals before; an unchanged package is not a change')
    checker.string_array(value.get('manifests'), f'{path}.manifests', min_items=1)
    checker.boolean(value.get('direct'), f'{path}.direct')
    checker.string_array(value.get('via'), f'{path}.via')
    checker.string(value.get('copy'), f'{path}.copy', nullable=True)


def _alerts(checker: Checker, value: Any, path: str) -> None:
    if not checker.object(value, path, ('open_before', 'closed', 'already_fixed', 'undecided')):
        return
    counts = value.get('open_before')
    if checker.object(counts, f'{path}.open_before', SEVERITIES):
        for severity in SEVERITIES:
            checker.integer(counts.get(severity), f'{path}.open_before.{severity}', minimum=0)
    for key in ('closed', 'already_fixed', 'undecided'):
        checker.array(value.get(key), f'{path}.{key}', lambda v, p: _alert(checker, v, p))
    if isinstance(counts, dict) and isinstance(value.get('closed'), list):
        closed: dict[str, int] = {}
        for alert in value['closed']:
            if isinstance(alert, dict) and alert.get('severity') in SEVERITIES:
                closed[alert['severity']] = closed.get(alert['severity'], 0) + 1
        for severity, number in closed.items():
            if isinstance(counts.get(severity), int) and number > counts[severity]:
                checker.error(
                    f'{path}.closed',
                    f'closes {number} {severity} alerts, but only {counts[severity]} were open',
                )


def _alert(checker: Checker, value: Any, path: str) -> None:
    if checker.object(value, path, ('number', 'package', 'severity')):
        checker.integer(value.get('number'), f'{path}.number', minimum=1)
        checker.string(value.get('package'), f'{path}.package')
        checker.choice(value.get('severity'), f'{path}.severity', SEVERITIES)
