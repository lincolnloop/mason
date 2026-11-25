"""What needs the developer, across every project they look after: `mason inbox`.

Nothing in the inbox is kept by hand. Each item is derived, every time, from GitHub (the open
PRs and issues mason's workflows opened, which carry the `automation` label), from this
machine's run records (failed unattended runs, and the actions a run left for a person with
`mason defer`), and from what a run would check first (tokens, the installed skills). So dealing
with an item where it lives clears it from the inbox.
"""

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import paths, project, projects, skills

# Most urgent first: something is broken, a PR can't merge, a person's decision is waiting, a
# PR is waiting for review, an issue has had no response.
BROKEN, BLOCKED, DECIDE, REVIEW, RESPOND = 'broken', 'blocked', 'decide', 'review', 'respond'
ORDER = (BROKEN, BLOCKED, DECIDE, REVIEW, RESPOND)
DEFERRED = 'deferred.json'
FAILING = ('FAILURE', 'ERROR', 'TIMED_OUT', 'CANCELLED', 'ACTION_REQUIRED', 'STARTUP_FAILURE')


class InboxError(Exception):
    pass


@dataclass
class Item:
    project: str
    kind: str
    text: str
    # Where it is dealt with: a URL, a log, or a command.
    where: str

    def line(self) -> str:
        mark = '!' if self.kind in (BROKEN, BLOCKED) else '-'
        return f'  {mark} {self.text}\n      {self.where}'


# --- Deferred actions --------------------------------------------------------------------------


def defer(run_dir: Path, action: str, reason: str, command: str | None = None) -> str:
    """Record an action a run left for a person, in the run's directory."""
    if not (run_dir / 'run.json').is_file():
        raise InboxError(f'{run_dir} is not a run directory (no run.json): give the printed `dir`')
    for name, value in (('--action', action), ('--reason', reason)):
        if not value.strip():
            raise InboxError(f'{name} is empty')
    path = run_dir / DEFERRED
    items = _deferred(path)
    item = {'action': action.strip(), 'reason': reason.strip()}
    if command and command.strip():
        item['command'] = command.strip()
    items.append(item)
    path.write_text(json.dumps(items, indent=2) + '\n', encoding='utf-8')
    return f'Deferred for a person: {item["action"]}\n'


def _deferred(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        items = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise InboxError(f'{path}: is not valid JSON: {exc}') from exc
    if not isinstance(items, list) or not all(
        isinstance(i, dict)
        and isinstance(i.get('action'), str)
        and isinstance(i.get('reason'), str)
        for i in items
    ):
        raise InboxError(f'{path}: must be a list of {{"action", "reason"}} objects')
    return items


# --- Run records -------------------------------------------------------------------------------


def latest_runs(repo: str, env: Mapping[str, str] | None = None) -> dict[str, Path]:
    """workflow -> its most recent run directory on this machine."""
    latest: dict[str, Path] = {}
    runs = paths.runs(repo, env)
    if runs.is_dir():
        for directory in sorted(runs.iterdir()):
            if match := projects.RUN_NAME.match(directory.name):
                latest[match.group(3)] = directory
    return latest


def latest_statuses(repo: str, env: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """workflow -> the status record of its most recent unattended run (`runner.write_status`)."""
    latest: dict[str, dict[str, Any]] = {}
    logs = paths.logs(repo, env)
    if logs.is_dir():
        for path in sorted(logs.glob('*.status.json')):
            if match := projects.RUN_NAME.match(path.name.removesuffix('.status.json')):
                try:
                    latest[match.group(3)] = json.loads(path.read_text(encoding='utf-8'))
                except json.JSONDecodeError:
                    latest[match.group(3)] = {'status': None, 'log': str(path)}
    return latest


# --- GitHub ------------------------------------------------------------------------------------

PR_FIELDS = 'number,title,url,isDraft,reviewDecision,mergeable,statusCheckRollup,createdAt'


def _days(stamp: str, now: datetime) -> int:
    return max(0, (now - datetime.fromisoformat(stamp.replace('Z', '+00:00'))).days)


def _ago(days: int) -> str:
    return 'today' if days == 0 else f'{days} day{"" if days == 1 else "s"}'


def pr_items(repo: str, prs: Sequence[dict[str, Any]], now: datetime) -> list[Item]:
    items = []
    for pr in prs:
        label = f'PR #{pr["number"]} {pr["title"]}'
        days = _ago(_days(pr['createdAt'], now))
        checks = pr.get('statusCheckRollup') or []
        failing = sorted(
            {
                c.get('name') or c.get('context') or '?'
                for c in checks
                if (c.get('conclusion') or c.get('state')) in FAILING
            }
        )
        if failing:
            items.append(
                Item(repo, BLOCKED, f'{label}: CI failing ({", ".join(failing)})', pr['url'])
            )
        elif pr.get('mergeable') == 'CONFLICTING':
            items.append(Item(repo, BLOCKED, f'{label}: conflicts with its base', pr['url']))
        elif pr.get('reviewDecision') == 'CHANGES_REQUESTED':
            items.append(Item(repo, REVIEW, f'{label}: changes requested, open {days}', pr['url']))
        elif pr.get('isDraft'):
            items.append(Item(repo, REVIEW, f'{label}: draft, unverified, open {days}', pr['url']))
        else:
            items.append(Item(repo, REVIEW, f'{label}: waiting for review, open {days}', pr['url']))
    return items


def issue_items(repo: str, issues: Sequence[dict[str, Any]], now: datetime) -> list[Item]:
    return [
        Item(
            repo,
            RESPOND,
            f'issue #{i["number"]} {i["title"]}: no response in {_ago(_days(i["createdAt"], now))}',
            i['url'],
        )
        for i in issues
        if not i.get('comments')
    ]


def github_items(repo: str, run_gh: projects.Gh, now: datetime) -> list[Item]:
    found = []
    for kind, fields in (('pr', PR_FIELDS), ('issue', 'number,title,url,comments,createdAt')):
        status, out, err = run_gh(
            [
                kind,
                'list',
                '--repo',
                repo,
                '--label',
                projects.LABEL,
                '--state',
                'open',
                '--limit',
                '100',
                '--json',
                fields,
            ]
        )
        if status != 0:
            problem = (err or out).strip().splitlines()[0] if (err or out).strip() else status
            return [
                Item(
                    repo, BROKEN, f"couldn't read {kind}s from GitHub: {problem}", 'gh auth status'
                )
            ]
        data = json.loads(out or '[]')
        found += pr_items(repo, data, now) if kind == 'pr' else issue_items(repo, data, now)
    return found


# --- Everything for one project ------------------------------------------------------------------


def token_file(env: Mapping[str, str] | None = None) -> set[str]:
    """The names set in ~/.config/mason/env, where a developer keeps the workflows' tokens
    (README, "Tokens"). Values are never read into the inbox."""
    path = paths.config_home(env) / 'env'
    if not path.is_file():
        return set()
    names = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        name, sep, value = line.removeprefix('export ').partition('=')
        if sep and value.strip() and not name.lstrip().startswith('#'):
            names.add(name.strip())
    return names


def local_items(repo: str, workflows: Sequence[str], env: Mapping[str, str]) -> list[Item]:
    found = []
    kept = token_file(env)
    problems: dict[str, list[str]] = {}
    declared = skills.workflows(env)
    try:
        mapping = project.skill_paths(repo, env)
    except project.ConfigError:
        mapping = {}  # collect() reports an invalid settings file
    for name in workflows:
        if name not in declared and name not in mapping:
            found.append(
                Item(repo, BROKEN, f'{name} is enabled but not installed', 'mason skills list')
            )
            continue
        try:
            skill = skills.check_resolved(name, mapping, env)
        except skills.SkillError as exc:
            problems.setdefault(str(exc).split(': ', 1)[-1], []).append(name)
            continue
        spec = skill.workflow if skill and skill.workflow else declared[name]
        missing = [t for t in spec.env if not env.get(t) and t not in kept]
        if missing:
            found.append(
                Item(repo, BROKEN, f'{name} needs {", ".join(missing)}', '~/.config/mason/env')
            )
    for problem, names in problems.items():
        if len(names) == 1:
            text = f'the {names[0]} skill: {problem}'
        else:
            text = f'{len(names)} skills ({", ".join(names)}): {problem}'
        found.append(
            Item(repo, BROKEN, text, 'mason doctor; mason skills install <org>/mason@<tag>')
        )
    for name, record in sorted(latest_statuses(repo, env).items()):
        if name in workflows and record.get('status') != 0:
            found.append(
                Item(
                    repo,
                    BROKEN,
                    f'{name} failed at {record.get("finished_at", "?")} '
                    f'(status {record.get("status")})',
                    str(record.get('log', '')),
                )
            )
    for name, directory in sorted(latest_runs(repo, env).items()):
        if name not in workflows:
            continue
        try:
            deferred = _deferred(directory / DEFERRED)
        except InboxError as exc:
            found.append(Item(repo, BROKEN, f'{name}: unreadable deferred actions', str(exc)))
            continue
        for d in deferred:
            found.append(
                Item(
                    repo,
                    DECIDE,
                    f'{d["action"]} ({name}, run {directory.name}): {d["reason"]}',
                    d.get('command') or str(directory),
                )
            )
    return found


def collect(
    repos: Sequence[str] | None = None,
    run_gh: projects.Gh = projects.gh,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> list[Item]:
    """Every item for the given projects (default: every active project), most urgent first."""
    env = os.environ if env is None else env
    now = now or datetime.now(UTC)
    found: list[Item] = []
    for repo in repos if repos is not None else projects.added(env):
        try:
            if project.archived(repo, env):
                continue
            workflows = project.enabled_workflows(repo, env)
        except project.ConfigError as exc:
            found.append(Item(repo, BROKEN, 'settings file is invalid', str(exc)))
            continue
        found += local_items(repo, workflows, env)
        found += github_items(repo, run_gh, now)
    return sorted(found, key=lambda i: (ORDER.index(i.kind), i.project))


def render(items: Sequence[Item]) -> str:
    if not items:
        return 'Nothing needs you.\n'
    lines = []
    for repo in dict.fromkeys(i.project for i in items):
        lines.append(repo)
        lines += [i.line() for i in items if i.project == repo]
    counts = {k: sum(1 for i in items if i.kind == k) for k in ORDER}
    lines += ['', ', '.join(f'{n} {k}' for k, n in counts.items() if n)]
    return '\n'.join(lines) + '\n'


def to_json(items: Sequence[Item]) -> str:
    return json.dumps([asdict(i) for i in items], indent=2) + '\n'
