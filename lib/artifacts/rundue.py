"""Run whatever is due, across every project: `mason run-due`, the one command a scheduler
starts (docs/workflows.md §9.5).

A scheduler (cron, a systemd timer, a GitHub Actions schedule) only has to start this regularly;
mason decides what to run from each workflow's schedule and the ledger. So changing a schedule
never means editing a timer, and an overdue workflow means something is wrong, not that nobody
wrote a timer for it.

For each enabled, scheduled workflow of each active project, the open periods (due, overdue,
or failed fewer than `RETRIES` times) are run oldest first, unattended, one at a time:

- `catch_up = 'all'` (reports, where every period matters) runs each open period;
- `catch_up = 'latest'` (checks, where only the current state matters) runs the latest, and
  records the older ones as skipped, superseded by it.

A workflow whose lock is held (a run in progress) is left alone until the next start.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from . import ledger, lock, paths, project, skills

RETRIES = 2


@dataclass
class Planned:
    repo: str
    workflow: str
    period: str
    # Older periods a 'latest' workflow's run supersedes; recorded as skipped when it runs.
    superseded: list[str]


def plan(
    repos: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> tuple[list[Planned], list[str]]:
    """What to run now, and notes on what is left alone and why."""
    now = now or datetime.now()
    todo, notes = [], []
    for row in ledger.overview(repos, env, now):
        if not row.open:
            continue
        skill = skills.resolve(row.workflow, project.skill_paths(row.repo, env), env)
        spec = skill.workflow if skill else None
        if spec is None:
            continue
        if spec.needs_pr:
            notes.append(f'{row.repo} {row.workflow}: acts on one PR; not run on a schedule')
            continue
        if lock.path(paths.project_state(row.repo, env), row.workflow).exists():
            notes.append(f'{row.repo} {row.workflow}: a run is in progress')
            continue
        known = ledger.facts(row.repo, row.workflow, env)
        open_slots = []
        for s in row.open:
            if s.state == ledger.BLOCKED:
                notes.append(f'{row.repo} {row.workflow} {s.key}: {s.detail}')
                continue
            if s.state == ledger.FAILED and known.failures.get(s.key, 0) > RETRIES:
                notes.append(
                    f'{row.repo} {row.workflow} {s.key}: failed {known.failures[s.key]} times; '
                    'left for a person (mason inbox)'
                )
                continue
            open_slots.append(s)
        if not open_slots:
            continue
        if spec.catch_up == 'latest':
            latest = open_slots[-1]
            todo.append(
                Planned(row.repo, row.workflow, latest.key, [s.key for s in open_slots[:-1]])
            )
        else:
            todo += [Planned(row.repo, row.workflow, s.key, []) for s in open_slots]
    return todo, notes


Run = Callable[[Planned], int]


def run_all(todo: Sequence[Planned], start: Run, env: Mapping[str, str] | None = None) -> list[str]:
    """Run each planned period in turn with `start` (which runs `mason run … --unattended`).
    Returns a line for each."""
    lines = []
    for item in todo:
        status = start(item)
        if status == 0:
            for key in item.superseded:
                ledger.skip(
                    item.repo, item.workflow, key, f'superseded by the run for {item.period}', env
                )
        state = 'done' if status == 0 else f'failed (status {status})'
        lines.append(f'{item.repo} {item.workflow} {item.period}: {state}')
    return lines


def describe(todo: Sequence[Planned], notes: Sequence[str]) -> str:
    lines = [
        f'{t.repo} {t.workflow} {t.period}'
        + (f' (supersedes {", ".join(t.superseded)})' if t.superseded else '')
        for t in todo
    ]
    lines += [f'not run: {n}' for n in notes]
    return '\n'.join(lines) + '\n' if lines else 'Nothing is due.\n'


# --- The heartbeat -------------------------------------------------------------------------------

UNIT = 'mason-run-due'


def cron_line(mason: str, minutes: int, log: str) -> str:
    return f'*/{minutes} * * * * {mason} run-due --trigger cron >> {log} 2>&1'


def systemd_units(mason: str, minutes: int, env_file: str) -> dict[str, str]:
    """A user service that runs `mason run-due`, and a timer that starts it every few minutes.
    The service reads the developer's tokens from their env file (README, "Tokens")."""
    service = f"""[Unit]
Description=mason: run every workflow period that is due

[Service]
Type=oneshot
EnvironmentFile=-{env_file}
# uv's usual install location: a user unit's PATH doesn't include it.
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart={mason} run-due --trigger 'systemd timer {UNIT}.timer'
"""
    timer = f"""[Unit]
Description=mason: start {UNIT}.service every {minutes} minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec={minutes}min
Persistent=true

[Install]
WantedBy=timers.target
"""
    return {f'{UNIT}.service': service, f'{UNIT}.timer': timer}
