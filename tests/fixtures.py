"""Minimal valid artifacts. Tests copy one and break a single field."""

import copy
from typing import Any

SHA_A = 'a' * 40


def envelope(schema: str, skill: str) -> dict[str, Any]:
    return {
        'schema': schema,
        'project': 'example/project',
        'produced_by': {'skill': skill, 'automation_sha': SHA_A},
    }


DEPENDENCY: dict[str, Any] = {
    'ecosystem': 'pip',
    'package': 'pillow',
    'installed': '12.0.0',
    'fixed': '12.1.1',
    'manifests': ['requirements/base.txt'],
    'classification': 'easy',
}

FINDING: dict[str, Any] = {
    **envelope('finding/1', 'dependency-upgrades'),
    'id': 'dependabot-2026-11-pillow',
    'fingerprint': 'deps/pip/pillow',
    'source': {'process': 'dependency-upgrades', 'refs': ['dependabot#7']},
    'kind': 'dependency',
    'title': 'Pillow 12.0.0 has a fixed vulnerability',
    'claim': 'Pillow 12.1.1 fixes the advisory, and nothing pins it below that.',
    'severity': 'important',
    'evidence': [{'ref': 'requirements/base.txt', 'note': 'pillow==12.0.0', 'status': 'verified'}],
    'dependency': DEPENDENCY,
}

RUN: dict[str, Any] = {
    **envelope('run/1', 'dependency-upgrades'),
    'id': '20261111-093000-dependency-upgrades',
    'trigger': 'unattended',
    'started_by': 'systemd timer',
    'host': 'factory-1',
    'started_at': '2026-11-11T09:30:00Z',
}

CHANGE: dict[str, Any] = {
    **envelope('change/1', 'dependency-upgrades'),
    'run': '20261111-093000-dependency-upgrades',
    'findings': ['dependabot-2026-11-pillow'],
    'branch': 'dependency-upgrades-20261111-093000',
    'head_sha': 'd' * 40,
    'summary': 'Security upgrades: easy Dependabot fixes',
    'effect': 'Closes 1 high alert by upgrading Pillow.',
    'blast_radius': ['requirements/base.txt'],
    'verified': [{'check': 'make test', 'result': 'pass'}],
    'not_verified': [],
    'success_signal': {
        'kind': 'alerts_closed',
        'refs': ['dependabot#7'],
        'check_after_days': 1,
        'description': 'Dependabot closes alert 7',
    },
    'revert': 'Revert the PR.',
}


def make(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """A deep copy of `base` with top-level fields replaced; a value of ... deletes the field."""
    result = copy.deepcopy(base)
    for key, value in changes.items():
        if value is ...:
            result.pop(key, None)
        else:
            result[key] = value
    return result
