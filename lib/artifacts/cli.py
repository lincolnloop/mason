"""Command line for the artifact contracts. Run through `bin/artifact`.

Every command that reads an artifact validates it first and refuses to go on if it is invalid.
"""

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import collect, deps, embed, lock, outcomes, project, render, run, validate

HUB = Path(__file__).resolve().parents[2]


class Refused(Exception):
    """An input problem to report on stderr, exit status 1."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='artifact', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)

    cmd = commands.add_parser('validate', help='validate an artifact file; exit 1 if invalid')
    cmd.add_argument('file', type=Path, help='the artifact JSON file')

    cmd = commands.add_parser('render-change', help='render a change as a pull request body')
    cmd.add_argument('file', type=Path)
    cmd.add_argument('--run', type=Path, required=True, help="the run's run.json")
    cmd.add_argument(
        '--findings', type=Path, nargs='+', required=True, help='files of the findings it addresses'
    )
    cmd.add_argument(
        '--not-in-this-pr', type=Path, nargs='*', default=[], help="the pass's other findings"
    )
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'alerts', help="save a project's open Dependabot alerts, exactly as GitHub returns them"
    )
    cmd.add_argument('project', type=Path, help='project root')
    cmd.add_argument('-o', '--output', type=Path, required=True)

    cmd = commands.add_parser(
        'render-stopped', help='the comment a run leaves on the open PR it stopped for'
    )
    cmd.add_argument('run', type=Path, help="the run's run.json")
    cmd.add_argument('--pr', type=int, required=True, help='the open PR')
    cmd.add_argument('--pr-body', type=Path, help="the open PR's body (default: fetched with gh)")
    cmd.add_argument(
        '--alerts', type=Path, required=True, help='gh api …/dependabot/alerts --paginate output'
    )
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'collect-outcomes', help="what happened to a workflow's PRs and the alerts, as outcomes/1"
    )
    cmd.add_argument('project', type=Path, help='project root')
    cmd.add_argument('--run', type=Path, required=True, help="the collecting run's run.json")
    cmd.add_argument(
        '--since', help='start of the period (ISO date or time); default period_days ago'
    )
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'render-retrospective', help='render outcomes and findings as a report issue body'
    )
    cmd.add_argument('outcomes', type=Path, help='collect-outcomes output')
    cmd.add_argument('--run', type=Path, required=True, help="the run's run.json")
    cmd.add_argument('--findings', type=Path, nargs='*', default=[], help='the findings files')
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'run-start', help="create a run's directory and record; prints dir, id and branch"
    )
    cmd.add_argument('project', type=Path, help='project root')
    cmd.add_argument('workflow', help='e.g. dependency-upgrades')

    cmd = commands.add_parser('extract', help='print the artifacts embedded in markdown as JSON')
    cmd.add_argument('input', type=Path, nargs='?', help='markdown (default: stdin)')
    cmd.add_argument('--schema', help='only artifacts of this schema, e.g. change/1')

    for name, help_text in (
        ('deps-diff', 'the packages that changed between two revisions, for change.dependencies'),
        ('alerts-closed', 'which open Dependabot alerts a revision closes, for change.alerts'),
    ):
        cmd = commands.add_parser(name, help=help_text)
        cmd.add_argument('--repo', type=Path, default=Path('.'))
        cmd.add_argument('--base', required=True, help='the revision before the change')
        if name == 'alerts-closed':
            cmd.add_argument(
                '--alerts',
                type=Path,
                required=True,
                help='gh api …/dependabot/alerts --paginate output',
            )
        cmd.add_argument('--head', default=deps.WORKTREE, help=f'a ref, or {deps.WORKTREE}')
        cmd.add_argument('--pip', action='append', default=[], help='glob of compiled requirements')
        cmd.add_argument('--npm', action='append', default=[], help='glob of npm lockfiles')
        cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'config',
        help="print a project's configuration as JSON: .agents/automation.toml, over values "
        'detected from the repository and defaults',
    )
    cmd.add_argument('project', type=Path, nargs='?', default=Path('.'), help='project root')
    cmd.add_argument('--section', help='check and print only this section (and the shared ones)')
    cmd.add_argument(
        '--toml',
        action='store_true',
        help='print everything as a starting automation.toml, with where each value came from',
    )

    commands.add_parser('version', help="this repository's commit, for produced_by.automation_sha")

    cmd = commands.add_parser('lock', help='take or release the one-run-at-a-time lock')
    cmd.add_argument('action', choices=('acquire', 'release'))
    cmd.add_argument('project', type=Path, help='project root')
    cmd.add_argument('workflow', help='e.g. dependency-upgrades')

    args = parser.parse_args(argv)
    try:
        output = COMMANDS[args.command](args)
    except Refused as exc:
        print(exc, file=sys.stderr)
        return 1
    if output is not None:
        if getattr(args, 'output', None):
            args.output.write_text(output, encoding='utf-8')
        else:
            sys.stdout.write(output)
    return 0


def _validate(args: argparse.Namespace) -> str:
    artifacts = _load(args.file)
    return f'{args.file}: valid {artifacts[0]["schema"]}\n'


def _render_change(args: argparse.Namespace) -> str:
    (change,) = _load(args.file, schema='change/1')
    (record,) = _load(args.run, schema='run/1')
    if change['run'] != record['id']:
        raise Refused(f'change.run is {change["run"]!r}, but {args.run} is run {record["id"]!r}')
    findings = [f for path in args.findings for f in _load(path, schema='finding/1')]
    others = [f for path in args.not_in_this_pr for f in _load(path, schema='finding/1')]
    addressed = {f['id'] for f in findings}
    if set(change['findings']) != addressed:
        missing = sorted(set(change['findings']) - addressed)
        extra = sorted(addressed - set(change['findings']))
        raise Refused(
            'the findings given do not match change.findings'
            + (f'; missing: {", ".join(missing)}' if missing else '')
            + (f'; not listed in the change: {", ".join(extra)}' if extra else '')
        )
    try:
        return render.change_body(change, findings, record, others)
    except ValueError as exc:
        raise Refused(str(exc)) from exc


def _render_stopped(args: argparse.Namespace) -> str:
    (record,) = _load(args.run, schema='run/1')
    try:
        alerts = deps.load_alerts(_read(args.alerts))
        if args.pr_body:
            body = _read(args.pr_body)
        else:
            body = collect.gh(
                'pr',
                'view',
                str(args.pr),
                '--repo',
                record['project'],
                '--json',
                'body',
                '--jq',
                '.body',
            )
        embedded = embed.extract(body)
        missing = run.not_covered(alerts, embedded)
        return render.stopped_comment(record, args.pr, missing)
    except (ValueError, KeyError, collect.CollectError) as exc:
        raise Refused(str(exc)) from exc


def _alerts(args: argparse.Namespace) -> None:
    try:
        repo = project.load(args.project)['repo']['name']
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    try:
        text = collect.gh(
            'api', f'repos/{repo}/dependabot/alerts?state=open&per_page=100', '--paginate'
        )
        count = len(deps.load_alerts(text))
    except (collect.CollectError, ValueError) as exc:
        raise Refused(str(exc)) from exc
    # Saved as GitHub returned it: every later step reads this file, so nothing is dropped.
    args.output.write_text(text, encoding='utf-8')
    print(f'{count} open alerts saved to {args.output}', file=sys.stderr)


def _collect_outcomes(args: argparse.Namespace) -> str:
    (record,) = _load(args.run, schema='run/1')
    try:
        config = project.load(args.project, 'dependency-retrospective')
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    settings = config['dependency-retrospective']
    policy = {
        'fix_within_days': {
            s: settings[f'fix_within_days_{s}'] for s in ('critical', 'high', 'medium', 'low')
        },
        'stale_after_months': settings['stale_after_months'],
        'stale_pr_after_days': config['limits']['stale_after_days'],
    }
    until = datetime.now(UTC)
    since = until - timedelta(days=settings['period_days'])
    if args.since:
        try:
            since = datetime.fromisoformat(args.since.replace('Z', '+00:00'))
        except ValueError as exc:
            raise Refused(f'--since: {exc}') from exc
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
    repo = config['repo']['name']
    try:
        raw_prs = collect.upgrade_prs(repo)
        alerts = collect.all_alerts(repo)
        reverted = collect.reverts(args.project, config['repo']['main_branch'], since.isoformat())
    except (collect.CollectError, ValueError) as exc:
        raise Refused(str(exc)) from exc
    packages = [
        collect.package_health(ecosystem, name, count, collect.fetch_json)
        for ecosystem, name, count in collect.packages_to_check(alerts, raw_prs)
    ]
    result = outcomes.build(
        repo=repo,
        run=record,
        since=since,
        until=until,
        policy=policy,
        raw_prs=raw_prs,
        alerts=alerts,
        reverts=reverted,
        packages=packages,
    )
    errors = validate(result)
    if errors:  # a bug here, not bad input: say so rather than write a broken artifact
        raise Refused('\n'.join(['the collected outcomes are invalid:', *errors]))
    return json.dumps(result, indent=2, ensure_ascii=False) + '\n'


def _render_retrospective(args: argparse.Namespace) -> str:
    (collected,) = _load(args.outcomes, schema='outcomes/1')
    (record,) = _load(args.run, schema='run/1')
    if collected['run'] != record['id']:
        raise Refused(f'the outcomes are from run {collected["run"]!r}, not {record["id"]!r}')
    findings = [f for path in args.findings for f in _load(path, schema='finding/1')]
    try:
        return render.retrospective_body(collected, findings, record)
    except ValueError as exc:
        raise Refused(str(exc)) from exc


def _run_start(args: argparse.Namespace) -> str:
    try:
        repo = project.load(args.project)['repo']['name']
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    directory, record = run.start(HUB, args.project, args.workflow, repo, _version(args).strip())
    branch = f'{args.workflow}-{record["id"].removesuffix("-" + args.workflow)}'
    return (
        json.dumps({'dir': str(directory), 'id': record['id'], 'branch': branch}, indent=2) + '\n'
    )


def _extract(args: argparse.Namespace) -> str:
    text = _read(args.input) if args.input else sys.stdin.read()
    try:
        artifacts = embed.extract(text, args.schema)
    except ValueError as exc:
        raise Refused(str(exc)) from exc
    return json.dumps(artifacts, indent=2, ensure_ascii=False) + '\n'


def _deps_diff(args: argparse.Namespace) -> str:
    _need_globs(args)
    before = _state(args, args.base)
    after = _state(args, args.head)
    return json.dumps(deps.diff(before, after), indent=2) + '\n'


def _alerts_closed(args: argparse.Namespace) -> str:
    _need_globs(args)
    try:
        alerts = deps.load_alerts(_read(args.alerts))
    except (ValueError, KeyError) as exc:
        raise Refused(f'{args.alerts}: {exc}') from exc
    try:
        result = deps.closed_alerts(alerts, _state(args, args.base), _state(args, args.head))
    except KeyError as exc:
        raise Refused(f'{args.alerts}: an alert is missing {exc}') from exc
    return json.dumps(result, indent=2) + '\n'


def _config(args: argparse.Namespace) -> str:
    try:
        resolved = project.resolve(args.project)
        if args.toml:
            return project.to_toml(resolved)
        project.require(resolved, args.section)
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    config = resolved.config
    shared = {name: config[name] for name in project.SHARED_SECTIONS}
    output: dict[str, object] = shared | (
        {args.section: config[args.section]} if args.section else config
    )
    # Where each value came from, so a skill can tell the person what it assumed.
    output['sources'] = {
        f'{section}.{key}': source
        for (section, key), source in resolved.sources.items()
        if section in output and source != 'file'
    }
    output['notes'] = resolved.notes
    return json.dumps(output, indent=2) + '\n'


def _version(args: argparse.Namespace) -> str:
    def git(*command: str) -> str:
        result = subprocess.run(
            ['git', '-C', str(HUB), *command], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()

    try:
        sha = git('rev-parse', 'HEAD')
        dirty = bool(git('status', '--porcelain', '--untracked-files=no'))
    except subprocess.CalledProcessError as exc:
        raise Refused(f'cannot read the automation commit: {exc.stderr.strip()}') from exc
    return f'{sha}{"-dirty" if dirty else ""}\n'


def _lock(args: argparse.Namespace) -> str:
    where = lock.path(HUB, args.project, args.workflow)
    if args.action == 'release':
        lock.release(where)
        return f'released {where}\n'
    taken, message = lock.acquire(where)
    if not taken:
        raise Refused(message)
    return message + '\n'


COMMANDS = {
    'config': _config,
    'version': _version,
    'lock': _lock,
    'validate': _validate,
    'render-change': _render_change,
    'render-stopped': _render_stopped,
    'alerts': _alerts,
    'run-start': _run_start,
    'collect-outcomes': _collect_outcomes,
    'render-retrospective': _render_retrospective,
    'extract': _extract,
    'deps-diff': _deps_diff,
    'alerts-closed': _alerts_closed,
}


def _need_globs(args: argparse.Namespace) -> None:
    if not args.pip and not args.npm:
        raise Refused('give at least one --pip or --npm glob, e.g. --pip "requirements/*.txt"')


def _state(args: argparse.Namespace, ref: str) -> list[deps.Installed]:
    try:
        return deps.read_state(args.repo, ref, args.pip, args.npm)
    except subprocess.CalledProcessError as exc:
        raise Refused(f'git failed at {ref}: {exc.stderr.strip()}') from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise Refused(str(exc)) from exc


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except FileNotFoundError as exc:
        raise Refused(f'{path}: does not exist') from exc


def _load(path: Path, schema: str | None = None) -> list[dict[str, Any]]:
    """Read a file holding one artifact or an array of them, and validate each."""
    try:
        data = json.loads(_read(path))
    except json.JSONDecodeError as exc:
        raise Refused(f'{path}: is not valid JSON: {exc}') from exc
    artifacts = data if isinstance(data, list) else [data]
    if not artifacts:
        raise Refused(f'{path}: holds no artifacts')
    problems = []
    for index, artifact in enumerate(artifacts):
        where = f'{path}[{index}]' if isinstance(data, list) else str(path)
        if schema and isinstance(artifact, dict) and artifact.get('schema') != schema:
            problems.append(f'{where}: expected a {schema}, got {artifact.get("schema")!r}')
            continue
        errors = validate(artifact)
        if errors:
            problems.append(f'{where}: {len(errors)} error(s)')
            problems += [f'- {error}' for error in errors]
    if problems:
        raise Refused('\n'.join(problems))
    return artifacts
