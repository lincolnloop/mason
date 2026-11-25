"""The mason command line: what the workflow skills call. Installed as `mason`; from a checkout,
`uv run mason` (or `bin/artifact`, which runs the same thing).

Every command that reads an artifact validates it first and refuses to go on if it is invalid.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import (
    assessment,
    collect,
    deps,
    detect,
    doctor,
    embed,
    lock,
    paths,
    project,
    render,
    run,
    runner,
    validate,
    version,
    workspace,
)


class Refused(Exception):
    """An input problem to report on stderr, exit status 1."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='mason', description=__doc__)
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
        'schema', help="print a schema's page: what each field of an artifact means"
    )
    cmd.add_argument('name', nargs='?', help='e.g. assessment or finding; omit to list them')

    cmd = commands.add_parser(
        'run-start', help="create a run's directory and record; prints dir, id and branch"
    )
    cmd.add_argument('project', type=Path, help='project root')
    cmd.add_argument('workflow', help='e.g. dependency-upgrades')

    cmd = commands.add_parser('extract', help='print the artifacts embedded in markdown as JSON')
    cmd.add_argument('input', type=Path, nargs='?', help='markdown (default: stdin)')
    cmd.add_argument('--schema', help='only artifacts of this schema, e.g. change/1')
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'build-assessment', help='every open alert and its finding, as assessment/1'
    )
    cmd.add_argument('project', type=Path, help='project root')
    cmd.add_argument('--run', type=Path, required=True, help="the run's run.json")
    cmd.add_argument('--alerts', type=Path, required=True, help='mason alerts output')
    cmd.add_argument('--findings', type=Path, nargs='*', default=[], help='the findings files')
    cmd.add_argument(
        '--already-fixed', type=int, nargs='*', default=[], help='alerts installed versions fix'
    )
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'render-assessment', help="render an assessment as the month's assessment issue body"
    )
    cmd.add_argument('assessment', type=Path)
    cmd.add_argument('--run', type=Path, required=True, help="the run's run.json")
    cmd.add_argument('--findings', type=Path, nargs='*', default=[], help='the findings files')
    cmd.add_argument('--previous-issue', help="the previous month's issue URL, for a new month")
    cmd.add_argument('--title', action='store_true', help='print only the title')
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'render-assessment-comment', help='what changed since the previous assessment'
    )
    cmd.add_argument('assessment', type=Path)
    cmd.add_argument('--run', type=Path, required=True, help="the run's run.json")
    cmd.add_argument('--previous', type=Path, help='the previous assessment.json, if there is one')
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'check-assessment',
        help='refuse, listing them, if alerts open now are missing from an assessment',
    )
    cmd.add_argument('assessment', type=Path)
    cmd.add_argument('--alerts', type=Path, required=True, help='mason alerts output, made now')

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
        help="print a project's configuration as JSON: mason.toml, over values "
        'detected from the repository and defaults',
    )
    cmd.add_argument('project', type=Path, nargs='?', default=Path('.'), help='project root')
    cmd.add_argument('--section', help='check and print only this section (and the shared ones)')
    cmd.add_argument(
        '--toml',
        action='store_true',
        help='print everything as a starting mason.toml, with where each value came from',
    )

    commands.add_parser('version', help="this repository's commit, for produced_by.automation_sha")

    cmd = commands.add_parser(
        'doctor', help='check the tools, login and tokens a workflow needs; exit 1 if any fail'
    )
    cmd.add_argument(
        '--workflow',
        choices=sorted(runner.WORKFLOWS),
        help='also check the tokens this workflow needs',
    )
    cmd.add_argument(
        '--unattended', action='store_true', help='also check for a long-lived Claude token'
    )

    cmd = commands.add_parser(
        'run',
        help='start a workflow in an agent session: interactive, or headless with --unattended',
    )
    cmd.add_argument('workflow', choices=sorted(runner.WORKFLOWS))
    where = cmd.add_mutually_exclusive_group()
    where.add_argument(
        '--repo', help="owner/name: run in mason's own clone of it, made or fetched first"
    )
    where.add_argument(
        '--project', type=Path, help='a checkout to run in (default: the current directory)'
    )
    cmd.add_argument('--pr', type=int, help='the pull request, for a workflow that acts on one')
    cmd.add_argument('--request', default='', help='added to the request, e.g. "for October 2026"')
    cmd.add_argument('--unattended', action='store_true', help='headless: ask nothing, log it')
    cmd.add_argument(
        '--allow',
        action='append',
        default=[],
        help='unattended: another tool the project needs, e.g. "Bash(docker *)"; repeatable',
    )
    cmd.add_argument(
        '--trigger',
        default='mason run',
        help='unattended: what started it, shown on what it publishes, e.g. "systemd timer"',
    )
    cmd.add_argument('--claude', default='claude', help='the claude executable')
    cmd.add_argument('--dry-run', action='store_true', help='print the command; run nothing')

    cmd = commands.add_parser(
        'skills', help="install or list mason's skills, with `npx skills` and telemetry off"
    )
    action = cmd.add_subparsers(dest='action', required=True)
    sub = action.add_parser(
        'install',
        help="install skills for mason's sessions only, or for every session with --global",
    )
    sub.add_argument('source', help='e.g. <org>/mason@<tag>, or a path to a mason checkout')
    sub.add_argument(
        '--global',
        dest='everywhere',
        action='store_true',
        help='install for every agent session (~/.claude/skills), not only mason run',
    )
    sub.add_argument(
        '--agent', action='append', default=[], help='claude-code (default), codex, …; repeatable'
    )
    sub.add_argument('--skill', action='append', default=[], help='only this skill; repeatable')
    sub = action.add_parser('list', help='list the installed skills')
    sub.add_argument('--global', dest='everywhere', action='store_true')

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
            args.output.parent.mkdir(parents=True, exist_ok=True)
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding='utf-8')
    print(f'{count} open alerts saved to {args.output}', file=sys.stderr)


SCHEMA_PAGES = Path(__file__).resolve().parent / 'schemas'


def _schema(args: argparse.Namespace) -> str:
    pages = sorted(p.stem for p in SCHEMA_PAGES.glob('*.md') if p.stem != 'README')
    if not args.name:
        return '\n'.join(pages) + '\n'
    name = args.name.split('/')[0]
    if name not in pages:
        raise Refused(f'no schema {args.name!r} (known: {", ".join(pages)})')
    return (SCHEMA_PAGES / f'{name}.md').read_text(encoding='utf-8')


def _run_start(args: argparse.Namespace) -> str:
    try:
        repo = project.load(args.project)['repo']['name']
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    directory, record = run.start(paths.runs(repo), args.workflow, repo, _version(args).strip())
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


def _open_alerts(path: Path) -> list[dict[str, Any]]:
    try:
        return deps.load_alerts(_read(path))
    except ValueError as exc:
        raise Refused(f'{path}: {exc}') from exc


def _build_assessment(args: argparse.Namespace) -> str:
    (record,) = _load(args.run, schema='run/1')
    try:
        config = project.load(args.project)
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    main = config['repo']['main_branch']
    try:
        commit = subprocess.run(
            ['git', '-C', str(args.project), 'rev-parse', f'origin/{main}'],
            capture_output=True, text=True, check=True,
        ).stdout.strip()  # fmt: skip
    except subprocess.CalledProcessError as exc:
        raise Refused(f'cannot read origin/{main}: {exc.stderr.strip()}') from exc
    findings = [f for path in args.findings for f in _load(path, schema='finding/1')]
    try:
        result = assessment.build(
            repo=config['repo']['name'],
            run=record,
            assessed_at=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
            commit=commit,
            alerts=_open_alerts(args.alerts),
            findings=findings,
            already_fixed=args.already_fixed,
        )
    except assessment.AssessmentError as exc:
        raise Refused(f"the findings don't account for every open alert:\n{exc}") from exc
    errors = validate(result)
    if errors:
        raise Refused('\n'.join(['the assessment is invalid:', *errors]))
    return json.dumps(result, indent=2, ensure_ascii=False) + '\n'


def _render_assessment(args: argparse.Namespace) -> str:
    (data,) = _load(args.assessment, schema=assessment.SCHEMA)
    if args.title:
        return render.assessment_title(data) + '\n'
    (record,) = _load(args.run, schema='run/1')
    findings = [f for path in args.findings for f in _load(path, schema='finding/1')]
    missing = set(data['findings']) - {f['id'] for f in findings}
    if missing:
        raise Refused(f'the assessment names findings not given: {", ".join(sorted(missing))}')
    try:
        return render.assessment_body(data, findings, record, args.previous_issue)
    except ValueError as exc:
        raise Refused(str(exc)) from exc


def _render_assessment_comment(args: argparse.Namespace) -> str:
    (data,) = _load(args.assessment, schema=assessment.SCHEMA)
    (record,) = _load(args.run, schema='run/1')
    previous = _load(args.previous, schema=assessment.SCHEMA)[0] if args.previous else None
    return render.assessment_comment(assessment.changes(previous, data), data, record)


def _check_assessment(args: argparse.Namespace) -> str:
    (data,) = _load(args.assessment, schema=assessment.SCHEMA)
    missing = assessment.uncovered(data, _open_alerts(args.alerts))
    if missing:
        numbers = ', '.join(f'#{n}' for n in missing)
        raise Refused(
            f'{_count_alerts(len(missing))} opened after the assessment of {data["assessed_at"]}: '
            f'{numbers}. Run the dependency assessment first.'
        )
    return f'the assessment of {data["assessed_at"]} covers every open alert\n'


def _count_alerts(number: int) -> str:
    return f'{number} alert' + ('' if number == 1 else 's')


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
    # The developer's settings file: where it was read from, or where to create one.
    output['settings'] = {
        'path': str(resolved.file_path) if resolved.file_path else None,
        'exists': resolved.file_exists,
    }
    return json.dumps(output, indent=2) + '\n'


def _doctor(args: argparse.Namespace) -> str:
    tokens = runner.WORKFLOWS[args.workflow].env if args.workflow else ()
    checks = doctor.run_checks(tokens, dict(os.environ), args.unattended)
    report = doctor.render(checks)
    if doctor.failed(checks):
        raise Refused(report.rstrip('\n'))
    return report


def _version(args: argparse.Namespace) -> str:
    try:
        return version.automation_sha() + '\n'
    except version.VersionError as exc:
        raise Refused(str(exc)) from exc


def _run(args: argparse.Namespace) -> str:
    try:
        runner.prompt(args.workflow, args.pr, args.request)
    except ValueError as exc:
        raise Refused(str(exc)) from exc
    if args.repo:
        repo = args.repo
        try:
            directory = paths.clone(repo)
        except ValueError as exc:
            raise Refused(f'--repo: {exc}') from exc
        if not args.dry_run:
            try:
                print(workspace.ensure_clone(repo, directory), file=sys.stderr)
            except workspace.WorkspaceError as exc:
                raise Refused(str(exc)) from exc
    else:
        directory = (args.project or Path.cwd()).resolve()
        if not (directory / '.git').exists():
            raise Refused(
                f'{directory} is not the root of a git repository: give --project or --repo'
            )
        found = detect.repo_name(directory)
        if found is None:
            raise Refused(
                f'{directory} has no GitHub origin remote, so its runs have nowhere to go'
            )
        repo = found
    state, skills = paths.project_state(repo), paths.skills_home()
    if args.unattended:
        command = runner.command(
            directory, state, args.workflow, tuple(args.allow), args.claude, args.pr, skills,
            args.request,
        )  # fmt: skip
        env = runner.environment(args.trigger)
    else:
        command = runner.interactive_command(
            directory, state, args.workflow, skills, args.claude, args.pr, args.request
        )
        env = dict(os.environ)
    if args.dry_run:
        marks = (
            f'MASON_UNATTENDED=1 MASON_TRIGGER={shlex.quote(args.trigger)} '
            if args.unattended
            else ''
        )
        return f'cd {shlex.quote(str(directory))} && {marks}{shlex.join(command)}\n'
    state.mkdir(parents=True, exist_ok=True)
    if args.unattended:
        check = subprocess.run(
            runner.doctor_command(args.workflow),
            cwd=directory, env=env, capture_output=True, text=True, check=False,
        )  # fmt: skip
        if check.returncode != 0:
            raise Refused(
                f'{check.stdout}{check.stderr}{args.workflow} not started: mason doctor failed'
            )
        status, log = runner.run(paths.logs(repo), directory, args.workflow, command, args.trigger)
        if status != 0:
            raise Refused(f'{args.workflow} finished with status {status}; log: {log}')
        return f'{args.workflow} finished; log: {log}\n'
    status = subprocess.run(command, cwd=directory, env=env, check=False).returncode
    if status != 0:
        raise Refused(f'{args.workflow}: the session ended with status {status}')
    return ''


# The version of `npx skills` mason drives: pinned, so its behaviour can't change under a run.
SKILLS_CLI = 'skills@1.8.1'


def _skills(args: argparse.Namespace) -> str:
    """`npx skills`, run where mason's skills live: mason's data directory, so only `mason run`
    sessions load them, or the developer's global skills with --global."""
    if args.action == 'install':
        agents = args.agent or ['claude-code']
        command = ['npx', '--yes', SKILLS_CLI, 'add', args.source, '-y', '-a', *agents]
        command += ['--skill', *(args.skill or ['*'])]
    else:
        command = ['npx', '--yes', SKILLS_CLI, 'ls']
    if args.everywhere:
        command.append('-g')
    where = Path.home() if args.everywhere else paths.data_home()
    where.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')
    try:
        status = subprocess.run(command, cwd=where, env=env, check=False).returncode
    except FileNotFoundError as exc:
        raise Refused('npx is not installed: mason installs skills with `npx skills`') from exc
    if status != 0:
        raise Refused(f'npx skills failed with status {status}')
    if args.action == 'install' and not args.everywhere:
        return f'installed for mason run sessions only, in {where}\n'
    return ''


def _lock(args: argparse.Namespace) -> str:
    try:
        repo = project.load(args.project)['repo']['name']
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    where = lock.path(paths.project_state(repo), args.workflow)
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
    'doctor': _doctor,
    'lock': _lock,
    'run': _run,
    'skills': _skills,
    'validate': _validate,
    'render-change': _render_change,
    'render-stopped': _render_stopped,
    'alerts': _alerts,
    'run-start': _run_start,
    'schema': _schema,
    'extract': _extract,
    'build-assessment': _build_assessment,
    'render-assessment': _render_assessment,
    'render-assessment-comment': _render_assessment_comment,
    'check-assessment': _check_assessment,
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
