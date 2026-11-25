"""Fetch what `outcomes.build` needs: the I/O half of outcome collection.

GitHub is read through `gh`, so it uses whatever login `gh` has. The project's git history is
read with `git`. Package registries (PyPI, npm) are read anonymously with `urllib`; only package
names are sent. A registry or repository that can't be read gives that package an `error`, not a
failed collection.
"""

import json
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import deps, embed, outcomes

USER_AGENT = 'mason-retrospective'
PR_FIELDS = (
    'number,title,url,state,isDraft,createdAt,mergedAt,closedAt,headRefName,headRefOid,'
    'body,comments,reviews,statusCheckRollup'
)
ALERT_STATES = 'open,fixed,dismissed,auto_dismissed'
SOURCE_LABELS = ('source', 'repository', 'code', 'github', 'homepage')
# github.com/<these>/… is not a repository.
NOT_OWNERS = {'sponsors', 'orgs', 'users', 'marketplace', 'apps', 'topics', 'features'}

Fetch = Callable[[str], Any]


class CollectError(Exception):
    pass


def gh(*args: str) -> str:
    result = subprocess.run(['gh', *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise CollectError(f'gh {" ".join(args[:2])} failed: {result.stderr.strip()}')
    return result.stdout


def upgrade_prs(repo: str, workflow: str = 'dependency-upgrades') -> list[dict[str, Any]]:
    """Every PR the workflow opened, in any state. They carry the `automation` label."""
    text = gh(
        'pr',
        'list',
        '--repo',
        repo,
        '--label',
        'automation',
        '--state',
        'all',
        '--limit',
        '1000',
        '--json',
        PR_FIELDS,
    )
    prefix = f'{workflow}-'
    return [pr for pr in json.loads(text) if pr['headRefName'].startswith(prefix)]


def all_alerts(repo: str) -> list[dict[str, Any]]:
    text = gh(
        'api', f'repos/{repo}/dependabot/alerts?state={ALERT_STATES}&per_page=100', '--paginate'
    )
    alerts: list[dict[str, Any]] = []
    for page in deps.json_stream(text):
        alerts.extend(page if isinstance(page, list) else [page])
    return alerts


def reverts(project: Path, main_branch: str, since: str) -> list[dict[str, Any]]:
    """Commits on the main branch that revert an upgrade commit, since `since`."""
    result = subprocess.run(
        [
            'git',
            '-C',
            str(project),
            'log',
            f'origin/{main_branch}',
            f'--since={since}',
            '--format=%H%x09%cI%x09%s',
            '--grep=^Revert "Upgrade ',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CollectError(f'git log failed: {result.stderr.strip()}')
    found = []
    for line in result.stdout.splitlines():
        sha, date, subject = line.split('\t', 2)
        found.append(
            {
                'sha': sha,
                'date': date,
                'subject': subject,
                'package': outcomes.revert_package(subject),
            }
        )
    return found


def packages_to_check(
    alerts: list[dict[str, Any]], raw_prs: list[dict[str, Any]]
) -> list[tuple[str, str, int]]:
    """(ecosystem, package, open alerts) for every package with an open alert, or a finding in an
    upgrade PR (an EOL line or major bump left out is exactly where abandonment shows)."""
    counts: dict[tuple[str, str], int] = {}
    for alert in alerts:
        if alert.get('state') != 'open':
            continue
        package = alert['dependency']['package']
        key = (package['ecosystem'], deps.normalise(package['ecosystem'], package['name']))
        counts[key] = counts.get(key, 0) + 1
    for raw in raw_prs:
        try:
            found = embed.extract(raw.get('body') or '', 'finding/1')
        except ValueError:
            continue
        for finding in found:
            dependency = finding.get('dependency')
            if dependency:
                counts.setdefault((dependency['ecosystem'], dependency['package']), 0)
    return sorted((eco, name, n) for (eco, name), n in counts.items() if eco in ('pip', 'npm'))


def package_health(ecosystem: str, name: str, open_alerts: int, fetch: Fetch) -> dict[str, Any]:
    record: dict[str, Any] = {
        'ecosystem': ecosystem,
        'package': name,
        'latest': None,
        'latest_released_at': None,
        'deprecated': None,
        'repository': None,
        'archived': None,
        'open_alerts': open_alerts,
        'error': None,
    }
    try:
        if ecosystem == 'pip':
            record |= _pypi(fetch(f'https://pypi.org/pypi/{urllib.parse.quote(name)}/json'))
        else:
            record |= _npm(
                fetch(f'https://registry.npmjs.org/{urllib.parse.quote(name, safe="@")}')
            )
    except Exception as exc:  # any registry failure: record it, don't stop the collection
        record['error'] = f'registry: {exc}'
        return record
    owner_repo = github_repo(record['repository'])
    if owner_repo:
        try:
            info = json.loads(gh('api', f'repos/{owner_repo}', '--jq', '{archived: .archived}'))
            record['archived'] = bool(info.get('archived'))
        except (CollectError, ValueError) as exc:
            record['error'] = f'repository: {exc}'
    return record


def _pypi(data: dict[str, Any]) -> dict[str, Any]:
    info = data['info']
    latest = info['version']
    files = data.get('releases', {}).get(latest) or []
    # Prefer the links a project labels as its source over its homepage, funding or docs.
    labelled = sorted(
        (info.get('project_urls') or {}).items(),
        key=lambda item: next(
            (i for i, word in enumerate(SOURCE_LABELS) if word in item[0].lower()),
            len(SOURCE_LABELS),
        ),
    )
    urls = [*(url for _, url in labelled), info.get('home_page') or '']
    inactive = any('Development Status :: 7 - Inactive' in c for c in info.get('classifiers') or [])
    return {
        'latest': latest,
        'latest_released_at': files[0]['upload_time_iso_8601'] if files else None,
        'deprecated': 'classified as Inactive on PyPI' if inactive else None,
        'repository': next((u for u in urls if github_repo(u)), None),
    }


def _npm(data: dict[str, Any]) -> dict[str, Any]:
    latest = data['dist-tags']['latest']
    repository = data.get('repository')
    url = repository.get('url') if isinstance(repository, dict) else repository
    return {
        'latest': latest,
        'latest_released_at': data.get('time', {}).get(latest),
        'deprecated': data.get('versions', {}).get(latest, {}).get('deprecated') or None,
        'repository': url,
    }


def github_repo(url: str | None) -> str | None:
    """'owner/repo' from a GitHub URL in any of the forms registries use, else None."""
    if not url or 'github.com' not in url:
        return None
    path = url.split('github.com', 1)[1].lstrip(':/')
    parts = [p for p in path.split('/') if p]
    if len(parts) < 2 or parts[0].lower() in NOT_OWNERS:
        return None
    return f'{parts[0]}/{parts[1].removesuffix(".git")}'


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(
        url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)
