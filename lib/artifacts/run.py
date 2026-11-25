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

from . import deps, schedules
from ._check import ENVELOPE_FIELDS, Checker, check_envelope

SCHEMA = 'run/1'

TRIGGERS = ('interactive', 'unattended')
# pr_opened: this run's PR carries the record. issue_opened: this run's report issue does.
# review_posted: this run's review of a PR does.
# stopped_pr_open: an earlier upgrade PR is still open, so this run changed nothing, and the
# record is a comment on that PR.
# assessed: this run's assessment issue does. report_published: this run's discussion does.
OUTCOMES = (
    'pr_opened',
    'issue_opened',
    'review_posted',
    'assessed',
    'report_published',
    'stopped_pr_open',
    # How a run ended, recorded locally with `mason run-end`; `mason status` reads them.
    'done',
    'nothing_to_do',
    'failed',
)
# What a run can say about itself at its end.
ENDINGS = ('done', 'nothing_to_do', 'failed')
ID_PATTERN = re.compile(r'^\d{8}-\d{6}-[a-z0-9][a-z0-9-]*$')

FIELDS = ('id', 'trigger', 'started_by', 'started_at')
# Absent while the run is in progress; the renderer that publishes the record sets it.
# host is kept in the local record only: what is published names the tool, not the machine.
# period: the period the run was for (schedules.py), when it has a schedule.
OPTIONAL = ('host', 'outcome', 'pr', 'alerts_not_covered', 'period', 'ended_at')


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
    if 'host' in artifact:
        checker.string(artifact['host'], '$.host', max_length=200)
    if 'period' in artifact and checker.string(artifact['period'], '$.period', max_length=20):
        try:
            schedules.from_key(artifact['period'])
        except schedules.ScheduleError as exc:
            checker.error('$.period', str(exc))
    if 'ended_at' in artifact:
        checker.timestamp(artifact['ended_at'], '$.ended_at')
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


def published(record: Mapping[str, Any], outcome: str) -> dict[str, Any]:
    """The record as it is published, with its outcome: without the host, which stays local."""
    return {**{k: v for k, v in record.items() if k != 'host'}, 'outcome': outcome}


def start(
    runs_dir: Path,
    workflow: str,
    repo: str,
    automation_sha: str,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
    login: str | None = None,
    versions: Mapping[str, str] | None = None,
    period: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Create the run's directory under `runs_dir` (`paths.runs(repo)`, in the developer's state
    directory) and write its record. Returns (directory, record).

    `started_by` is `MASON_TRIGGER` when the launcher sets it (a timer, another agent),
    otherwise the GitHub login of an interactive user. `versions` adds `mason_version` and
    `skill_version` to `produced_by`.
    """
    env = os.environ if env is None else env
    now = now or datetime.now(UTC)
    unattended = env.get('MASON_UNATTENDED') == '1'
    started_by = env.get('MASON_TRIGGER') or (
        'an unattended run' if unattended else (login or github_login() or 'unknown')
    )
    run_id = f'{now:%Y%m%d-%H%M%S}-{workflow}'
    record = {
        'schema': SCHEMA,
        'project': repo,
        'produced_by': {'skill': workflow, 'automation_sha': automation_sha, **(versions or {})},
        'id': run_id,
        'trigger': 'unattended' if unattended else 'interactive',
        'started_by': started_by[:200],
        'host': socket.gethostname()[:200] or 'unknown',
        'started_at': f'{now:%Y-%m-%dT%H:%M:%SZ}',
        **({'period': period} if period else {}),
    }
    directory = runs_dir / run_id
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


def end(run_dir: Path, outcome: str, now: datetime | None = None) -> dict[str, Any]:
    """Record how a run ended in its local record, for `mason status`."""
    if outcome not in ENDINGS:
        raise ValueError(f'--outcome is one of {", ".join(ENDINGS)}, got {outcome!r}')
    path = run_dir / 'run.json'
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f'{path}: no readable run record: give the run directory') from exc
    record['outcome'] = outcome
    record['ended_at'] = f'{(now or datetime.now(UTC)):%Y-%m-%dT%H:%M:%SZ}'
    if errors := validate(record):
        raise ValueError(f'{path}: {errors[0]}')
    path.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return record
