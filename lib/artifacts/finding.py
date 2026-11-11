"""`finding/1`: one thing a workflow found, and what should happen to it. See schemas/finding.md."""

import re
from typing import Any

from ._check import ENVELOPE_FIELDS, SLUG_PATTERN, Checker, check_envelope

SCHEMA = 'finding/1'

# dependency: goes into the upgrade PR. investigate: needs a person or its own PR. external:
# waiting on someone else, such as an upstream fix. skill_change: a change to this repository's
# skills, proposed by a retrospective.
KINDS = ('dependency', 'investigate', 'external', 'skill_change')
SEVERITIES = ('critical', 'important', 'suggestion')
EVIDENCE_STATUSES = ('verified', 'inferred')
ECOSYSTEMS = ('pip', 'npm')
# Ordered as the dependency-upgrades skill classifies: the first that applies wins.
CLASSIFICATIONS = ('already_fixed', 'no_fix', 'eol', 'blocked', 'major', 'easy')

ID_PATTERN = re.compile(r'^[a-z0-9][a-z0-9._-]*$')
FINGERPRINT_PATTERN = re.compile(r'^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)+$')

FIELDS = ('id', 'fingerprint', 'source', 'kind', 'title', 'claim', 'severity', 'evidence')
OPTIONAL = ('dependency',)


def validate(artifact: Any) -> list[str]:
    checker = Checker()
    if not checker.object(artifact, '$', ENVELOPE_FIELDS + FIELDS, OPTIONAL):
        return checker.errors
    check_envelope(checker, artifact, SCHEMA)
    check_finding(checker, artifact, '$')
    return checker.errors


def check_finding(checker: Checker, finding: dict[str, Any], path: str) -> None:
    checker.string(
        finding.get('id'), f'{path}.id', pattern=ID_PATTERN, pattern_hint='a lowercase slug'
    )
    checker.string(
        finding.get('fingerprint'),
        f'{path}.fingerprint',
        pattern=FINGERPRINT_PATTERN,
        pattern_hint='area/slug, e.g. deps/pip/django',
    )
    source = finding.get('source')
    if checker.object(source, f'{path}.source', ('process', 'refs')):
        checker.string(source.get('process'), f'{path}.source.process', pattern=SLUG_PATTERN)
        checker.string_array(source.get('refs'), f'{path}.source.refs', min_items=1)
    kind = finding.get('kind')
    checker.choice(kind, f'{path}.kind', KINDS)
    checker.string(finding.get('title'), f'{path}.title', max_length=200)
    checker.string(finding.get('claim'), f'{path}.claim')
    checker.choice(finding.get('severity'), f'{path}.severity', SEVERITIES)
    checker.array(
        finding.get('evidence'),
        f'{path}.evidence',
        lambda v, p: _evidence(checker, v, p),
        min_items=1,
    )

    # Any kind may describe a dependency (an EOL line is `investigate`), but only an easy
    # upgrade goes into the upgrade PR.
    dependency = finding.get('dependency')
    if dependency is not None:
        _dependency(checker, dependency, f'{path}.dependency')
    if kind == 'dependency':
        if dependency is None:
            checker.error(f'{path}.dependency', 'is required when kind is dependency')
        elif isinstance(dependency, dict) and dependency.get('classification') not in (
            None,
            'easy',
        ):
            checker.error(
                f'{path}.kind',
                f'is dependency, but the classification is {dependency.get("classification")!r}: only an '
                'easy upgrade goes into the upgrade PR; use investigate (eol, major, blocked) or drop the finding',
            )


def _evidence(checker: Checker, value: Any, path: str) -> None:
    if checker.object(value, path, ('ref', 'note', 'status')):
        checker.string(value.get('ref'), f'{path}.ref')
        checker.string(value.get('note'), f'{path}.note')
        checker.choice(value.get('status'), f'{path}.status', EVIDENCE_STATUSES)


def _dependency(checker: Checker, value: Any, path: str) -> None:
    required = ('ecosystem', 'package', 'installed', 'fixed', 'manifests', 'classification')
    if not checker.object(value, path, required, ('tracked_by',)):
        return
    checker.choice(value.get('ecosystem'), f'{path}.ecosystem', ECOSYSTEMS)
    checker.string(value.get('package'), f'{path}.package')
    checker.string(value.get('installed'), f'{path}.installed')
    checker.string(value.get('fixed'), f'{path}.fixed', nullable=True)
    checker.string_array(value.get('manifests'), f'{path}.manifests', min_items=1)
    classification = value.get('classification')
    checker.choice(classification, f'{path}.classification', CLASSIFICATIONS)
    if classification == 'no_fix' and value.get('fixed') is not None:
        checker.error(f'{path}.fixed', 'must be null when classification is no_fix')
    if classification in ('easy', 'major', 'blocked') and value.get('fixed') is None:
        checker.error(
            f'{path}.fixed', f'must name the fixed version when classification is {classification}'
        )
    if 'tracked_by' in value:
        checker.string(value['tracked_by'], f'{path}.tracked_by', nullable=True)
    elif classification in ('eol', 'major'):
        checker.error(
            f'{path}.tracked_by',
            f'is required when classification is {classification}: search for existing tracked work '
            'and give its URL, or null if there is none',
        )
