"""Derive what a dependency upgrade actually changed, and which alerts it closes.

The tables in an upgrade PR are built from the compiled requirements files and npm lockfiles at
two revisions, not written from the alert list, so they include
every transitive package that moved, and the alerts are matched against the versions that are
really installed afterwards. One bump can move other packages (upgrading `postcss` can move
`nanoid` too), and the alert list doesn't say so.

Supported formats: pip-compile output (`name==version` lines with `# via` comments) and npm
lockfiles of version 2 or 3 (the `packages` map).
"""

import fnmatch
import json
import re
import subprocess
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SEVERITIES = ('critical', 'high', 'medium', 'low')
WORKTREE = 'WORKTREE'

PIP_LINE = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;\\]+)')
VERSION = re.compile(r'^v?(\d+(?:\.\d+)*)(.*)$')
CONSTRAINT = re.compile(r'^(>=|<=|>|<|=|==)?\s*(\S+)$')


@dataclass(frozen=True)
class Installed:
    """One installed copy of a package in one manifest."""

    ecosystem: str
    package: str
    version: str
    manifest: str
    copy: str | None  # npm: the parent chain of a nested copy ('url > qs'); else None
    direct: bool
    via: tuple[str, ...]


# --- Reading manifests -------------------------------------------------------------------------


def normalise(ecosystem: str, name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower() if ecosystem == 'pip' else name


def parse_pip(text: str, manifest: str) -> list[Installed]:
    found: list[Installed] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        if current is not None:
            # '-r x.in' is a pin in a source file: direct. '-r x.txt' (an include of another
            # compiled file) and '-c x.txt' (a constraint) say where else it is pinned, not
            # which package needs it, so neither is a parent.
            sources = [v for v in current['via'] if v.startswith(('-r ', '-c '))]
            via = tuple(v for v in current['via'] if v not in sources)
            direct = any(v.startswith('-r ') and v.endswith('.in') for v in sources)
            found.append(
                Installed('pip', current['name'], current['version'], manifest, None, direct, via)
            )

    in_via = False
    for line in text.splitlines():
        if line and not line[0].isspace() and not line.startswith('#'):
            # Any unindented line starts a new entry. One that isn't `name==version` (a URL
            # requirement, `-e`, an option) has no version to compare, but its `# via` lines
            # must not be attached to the entry before it.
            flush()
            match = PIP_LINE.match(line)
            current = (
                {'name': normalise('pip', match.group(1)), 'version': match.group(2), 'via': []}
                if match
                else None
            )
            in_via = False
            continue
        stripped = line.strip()
        if current is None or not stripped.startswith('#'):
            if not line.startswith(' '):
                in_via = False
            continue
        comment = stripped.lstrip('#').strip()
        if comment.startswith('via'):
            rest = comment[3:].strip()
            in_via = not rest
            if rest:
                current['via'].append(rest)
        elif in_via and comment:
            current['via'].append(comment)
    flush()
    return found


def parse_npm(text: str, manifest: str) -> list[Installed]:
    data = json.loads(text)
    if data.get('lockfileVersion', 1) < 2 or 'packages' not in data:
        raise ValueError(f'{manifest}: only npm lockfile versions 2 and 3 are supported')
    packages: dict[str, Any] = data['packages']
    root = packages.get('', {})
    declared = set(root.get('dependencies', {})) | set(root.get('devDependencies', {}))
    found = []
    for location, entry in packages.items():
        if not location or 'node_modules/' not in location or entry.get('link'):
            continue
        version = entry.get('version')
        if version is None:
            continue
        chain = location.split('node_modules/')[1:]
        chain = [part.rstrip('/') for part in chain]
        name = chain[-1]
        nested = len(chain) > 1
        found.append(
            Installed(
                'npm',
                name,
                version,
                manifest,
                ' > '.join(chain) if nested else None,
                not nested and name in declared,
                tuple(chain[:-1]),
            )
        )
    return found


def read_state(repo: Path, ref: str, pip: Iterable[str], npm: Iterable[str]) -> list[Installed]:
    """Every installed package in the matching manifests at `ref` (or the working tree)."""
    pip, npm = tuple(pip), tuple(npm)
    installed: list[Installed] = []
    for path in _files(repo, ref):
        if any(fnmatch.fnmatch(path, pattern) for pattern in pip):
            installed += parse_pip(_read(repo, ref, path), path)
        elif any(fnmatch.fnmatch(path, pattern) for pattern in npm):
            installed += parse_npm(_read(repo, ref, path), path)
    return installed


def _files(repo: Path, ref: str) -> list[str]:
    if ref == WORKTREE:
        args = ['git', '-C', str(repo), 'ls-files', '--cached', '--others', '--exclude-standard']
    else:
        args = ['git', '-C', str(repo), 'ls-tree', '-r', '--name-only', ref]
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.splitlines()


def _read(repo: Path, ref: str, path: str) -> str:
    if ref == WORKTREE:
        return (repo / path).read_text(encoding='utf-8')
    args = ['git', '-C', str(repo), 'show', f'{ref}:{path}']
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


# --- What changed ------------------------------------------------------------------------------


def diff(before: list[Installed], after: list[Installed]) -> list[dict[str, Any]]:
    """The packages whose version changed, grouped across the manifests they changed in.

    Each item is ready for `change.dependencies`.
    """

    def keyed(state: list[Installed]) -> dict[tuple[str, str, str, str | None], Installed]:
        return {(i.ecosystem, i.package, i.manifest, i.copy): i for i in state}

    old, new = keyed(before), keyed(after)
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for key in sorted(set(old) | set(new), key=lambda k: (k[0], k[1], k[3] or '', k[2])):
        was, now = old.get(key), new.get(key)
        if was is not None and now is not None and was.version == now.version:
            continue
        current = now or was
        assert current is not None
        # One row per version change: direct if pinned in any manifest, with every parent.
        group_key = (
            current.ecosystem,
            current.package,
            was.version if was else None,
            now.version if now else None,
            current.copy,
        )
        group = groups.setdefault(
            group_key,
            {
                'ecosystem': current.ecosystem,
                'package': current.package,
                'before': was.version if was else None,
                'after': now.version if now else None,
                'manifests': [],
                'direct': False,
                'via': [],
                'copy': current.copy,
            },
        )
        group['manifests'].append(current.manifest)
        group['direct'] = group['direct'] or current.direct
        group['via'] = sorted(set(group['via']) | set(current.via))
    for group in groups.values():
        if group['direct']:
            group['via'] = []
    return list(groups.values())


# A first release number this large is a year (2026.10.18) or a date (20261018): calendar
# versioning, where a new year says nothing about compatibility.
CALENDAR_VERSION_FROM = 1990


def is_major(before: str | None, after: str | None) -> bool:
    """A change of the first release number (0.x counts the second; calendar versions never do)."""
    if before is None or after is None:
        return False
    try:
        old, new = _release(before), _release(after)
    except ValueError:
        return False
    if old[0] >= CALENDAR_VERSION_FROM and new[0] >= CALENDAR_VERSION_FROM:
        return False
    if old[0] == 0 and new[0] == 0:
        return old[1:2] != new[1:2]
    return old[0] != new[0]


# --- Alerts ------------------------------------------------------------------------------------


def load_alerts(text: str) -> list[dict[str, Any]]:
    """Read the open alerts from `gh api …/dependabot/alerts --paginate` output.

    Without `--slurp` (which older `gh` doesn't have), `--paginate` writes each page's array
    straight after the last, which isn't one JSON document. So this reads a stream of JSON values:
    arrays of alerts (pages), a slurped array of pages, or one alert per line.
    """
    try:
        values = json_stream(text)
    except ValueError as exc:
        raise ValueError(f'alerts are {exc}') from exc
    alerts: list[Any] = []
    for value in values:
        if isinstance(value, dict):
            alerts.append(value)
        elif isinstance(value, list):
            for item in value:
                alerts.extend(item if isinstance(item, list) else [item])
        else:
            raise ValueError('alerts must be JSON arrays or objects')
    if not all(isinstance(alert, dict) for alert in alerts):
        raise ValueError('every alert must be a JSON object')
    return [alert for alert in alerts if alert.get('state', 'open') == 'open']


def json_stream(text: str) -> list[Any]:
    """Every JSON value in `text`, written one after another, as `gh api --paginate` does."""
    decoder = json.JSONDecoder()
    values: list[Any] = []
    position = 0
    while (position := _skip_space(text, position)) < len(text):
        try:
            value, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError as exc:
            raise ValueError(f'not valid JSON: {exc}') from exc
        values.append(value)
    return values


def _skip_space(text: str, position: int) -> int:
    while position < len(text) and text[position].isspace():
        position += 1
    return position


def closed_alerts(
    alerts: list[dict[str, Any]], before: list[Installed], after: list[Installed]
) -> dict[str, Any]:
    """Which open alerts the change closes, ready for `change.alerts`.

    An alert is vulnerable while any installed copy of its package in its manifest is inside the
    vulnerable range; one vulnerable nested npm copy keeps it open. It is `closed` by the change
    if it was vulnerable before and isn't after, and `already_fixed` if it wasn't vulnerable
    before (Dependabot hasn't caught up, or it was fixed by an earlier change).
    """
    open_before: Counter[str] = Counter()
    lists: dict[str, list[dict[str, Any]]] = {'closed': [], 'already_fixed': [], 'undecided': []}
    was, now = _versions(before), _versions(after)
    for alert in alerts:
        dependency = alert['dependency']
        ecosystem = dependency['package']['ecosystem']
        package = normalise(ecosystem, dependency['package']['name'])
        severity = _severity(alert)
        open_before[severity] += 1
        vulnerable = alert['security_vulnerability']['vulnerable_version_range']
        key = (ecosystem, package, dependency['manifest_path'])
        summary = {'number': alert['number'], 'package': package, 'severity': severity}
        try:
            vulnerable_before = any(in_range(v, vulnerable) for v in was.get(key, []))
            vulnerable_after = any(in_range(v, vulnerable) for v in now.get(key, []))
        except ValueError:
            lists['undecided'].append(summary)
            continue
        if not vulnerable_before:
            lists['already_fixed'].append(summary)
        elif not vulnerable_after:
            lists['closed'].append(summary)
    return {
        'open_before': {s: open_before.get(s, 0) for s in SEVERITIES},
        **{name: sorted(items, key=lambda a: a['number']) for name, items in lists.items()},
    }


def _versions(state: list[Installed]) -> dict[tuple[str, str, str], list[str]]:
    versions: dict[tuple[str, str, str], list[str]] = {}
    for item in state:
        versions.setdefault((item.ecosystem, item.package, item.manifest), []).append(item.version)
    return versions


def alert_summary(alert: dict[str, Any]) -> dict[str, Any]:
    """An alert as the tables and comments show it: number, package and severity."""
    package = alert['dependency']['package']
    return {
        'number': alert['number'],
        'package': normalise(package['ecosystem'], package['name']),
        'severity': _severity(alert),
    }


def _severity(alert: dict[str, Any]) -> str:
    severity = (alert.get('security_advisory') or {}).get('severity') or 'low'
    return 'medium' if severity == 'moderate' else severity


def in_range(version: str, vulnerable_range: str) -> bool:
    """Whether `version` satisfies a Dependabot range such as '>= 2.0.0, < 2.2.2'."""
    key = version_key(version)
    for constraint in vulnerable_range.split(','):
        match = CONSTRAINT.match(constraint.strip())
        if match is None:
            raise ValueError(f'unrecognised version constraint {constraint!r}')
        operator, bound = match.group(1) or '=', version_key(match.group(2))
        ok = {
            '>=': key >= bound,
            '<=': key <= bound,
            '>': key > bound,
            '<': key < bound,
            '=': key == bound,
            '==': key == bound,
        }[operator]
        if not ok:
            return False
    return True


def version_key(version: str) -> tuple[Any, ...]:
    """An ordering key for pip and npm versions.

    Release numbers compare numerically. A pre-release ('rc1', '-beta.2') sorts before its
    release and a post-release ('.post1') after it. Anything else raises ValueError rather
    than guess.
    """
    release = _release(version)
    suffix = VERSION.match(version.strip()).group(2)  # type: ignore[union-attr]
    padded = release + (0,) * (6 - len(release))
    suffix = suffix.split('+', 1)[0]  # local/build metadata doesn't order
    if not suffix:
        return (padded, 1, '')
    if re.fullmatch(r'[.-]?post\.?\d+', suffix):
        return (padded, 2, suffix)
    if re.fullmatch(r'[.-]?(a|b|rc|alpha|beta|pre|dev)[.-]?\d*([.-]\w+)*', suffix, re.IGNORECASE):
        return (padded, 0, suffix)
    raise ValueError(f'cannot order version {version!r}')


def _release(version: str) -> tuple[int, ...]:
    match = VERSION.match(version.strip())
    if match is None:
        raise ValueError(f'cannot order version {version!r}')
    return tuple(int(part) for part in match.group(1).split('.'))
