"""`run/1`: who or what started a workflow run, where, and when, and how it ended.

Every run has one, so every run is accountable: the PR a run opens carries it, and a run that
stops because an earlier PR is still open says so in a comment on that PR. Either way the
record is on GitHub, where people already look, and not only on the machine that did the work.
See schemas/run.md.
"""

import json
import os
import re
import socket
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import deps, detect
from ._check import ENVELOPE_FIELDS, Checker, check_envelope

SCHEMA = 'run/1'

TRIGGERS = ('interactive', 'unattended')
# pr_opened: this run's PR carries the record. issue_opened: this run's report issue does.
# stopped_pr_open: an earlier upgrade PR is still open, so this run changed nothing, and the
# record is a comment on that PR.
OUTCOMES = ('pr_opened', 'issue_opened', 'stopped_pr_open')
ID_PATTERN = re.compile(r'^\d{8}-\d{6}-[a-z0-9][a-z0-9-]*$')

FIELDS = ('id', 'trigger', 'started_by', 'host', 'started_at')
# Absent while the run is in progress; the renderer that publishes the record sets it.
OPTIONAL = ('outcome', 'pr', 'alerts_not_covered')


def validate(artifact: Any) -> list[str]:
    checker = Checker()
    if not checker.object(artifact, '$', ENVELOPE_FIELDS + FIELDS, OPTIONAL):
        return checker.errors
    check_envelope(checker, artifact, SCHEMA)
    checker.string(
        artifact.get('id'), '$.id', pattern=ID_PATTERN, pattern_hint='<stamp>-<workflow>'
    )
    checker.choice(artifact.get('trigger'), '$.trigger', TRIGGERS)
    checker.string(artifact.get('started_by'), '$.started_by', max_length=200)
    checker.string(artifact.get('host'), '$.host', max_length=200)
    checker.timestamp(artifact.get('started_at'), '$.started_at')
    outcome = artifact.get('outcome')
    if 'outcome' in artifact:
        checker.choice(outcome, '$.outcome', OUTCOMES)
    if outcome == 'stopped_pr_open':
        for key in ('pr', 'alerts_not_covered'):
            if key not in artifact:
                checker.error(f'$.{key}', 'is required when outcome is stopped_pr_open')
        if 'pr' in artifact:
            checker.integer(artifact['pr'], '$.pr', minimum=1)
        if 'alerts_not_covered' in artifact:
            checker.array(
                artifact['alerts_not_covered'],
                '$.alerts_not_covered',
                lambda v, p: checker.integer(v, p, minimum=1),
            )
    else:
        for key in ('pr', 'alerts_not_covered'):
            if key in artifact:
                checker.error(f'$.{key}', 'is only for outcome stopped_pr_open')
    return checker.errors


def start(
    hub: Path,
    project: Path,
    workflow: str,
    repo: str,
    automation_sha: str,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
    login: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Create the run's directory and write its record. Returns (directory, record).

    `started_by` is `AUTOMATION_TRIGGER` when the launcher sets it (a timer, another agent),
    otherwise the GitHub login of an interactive user.
    """
    env = os.environ if env is None else env
    now = now or datetime.now(UTC)
    unattended = env.get('AUTOMATION_UNATTENDED') == '1'
    started_by = env.get('AUTOMATION_TRIGGER') or (
        'an unattended run' if unattended else (login or github_login() or 'unknown')
    )
    run_id = f'{now:%Y%m%d-%H%M%S}-{workflow}'
    record = {
        'schema': SCHEMA,
        'project': repo,
        'produced_by': {'skill': workflow, 'automation_sha': automation_sha},
        'id': run_id,
        'trigger': 'unattended' if unattended else 'interactive',
        'started_by': started_by[:200],
        'host': socket.gethostname()[:200] or 'unknown',
        'started_at': f'{now:%Y-%m-%dT%H:%M:%SZ}',
    }
    directory = hub / 'runs' / detect.project_key(project) / run_id
    (directory / 'findings').mkdir(parents=True)
    (directory / 'run.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return directory, record


def github_login() -> str | None:
    try:
        result = subprocess.run(
            ['gh', 'api', 'user', '--jq', '.login'], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def not_covered(
    alerts: list[dict[str, Any]], pr_artifacts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Open alerts that the artifacts embedded in an open PR don't account for.

    An alert is accounted for if the PR's change lists it (closed, already fixed or undecided)
    or one of its findings, in the PR or left out of it, cites it.
    """
    known: set[int] = set()
    for artifact in pr_artifacts:
        if artifact.get('schema') == 'change/1':
            for key in ('closed', 'already_fixed', 'undecided'):
                known.update(a['number'] for a in (artifact.get('alerts') or {}).get(key, []))
        elif artifact.get('schema') == 'finding/1':
            for ref in artifact['source']['refs']:
                number = ref.removeprefix('dependabot#')
                if number.isdigit():
                    known.add(int(number))
    missing = [deps.alert_summary(a) for a in alerts if a['number'] not in known]
    order = {severity: index for index, severity in enumerate(deps.SEVERITIES)}
    return sorted(missing, key=lambda a: (order.get(a['severity'], 9), a['number']))
