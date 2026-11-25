"""The mason command line: what the workflow skills call. Installed as `mason`; from a checkout,
`uv run mason` (or `bin/artifact`, which runs the same thing).

Every command that reads an artifact validates it first and refuses to go on if it is invalid.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import (
    assessment,
    collect,
    deps,
    detect,
    diff,
    discussions,
    doctor,
    embed,
    inbox,
    lock,
    notes,
    outcomes,
    paths,
    project,
    projects,
    render,
    report,
    results,
    review_context,
    review_outcomes,
    run,
    runner,
    sentry,
    sentry_outcomes,
    skills,
    validate,
    version,
    workspace,
)

REPO_NAME = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
PROJECT_HELP = "the project's root directory, or owner/name for mason's clone of it"


def project_dir(value: str) -> Path:
    """A project named by its directory, or by its GitHub repository: mason's clone of it
    (`mason project add` makes it)."""
    path = Path(value)
    if path.exists() or not REPO_NAME.fullmatch(value):
        return path
    try:
        clone = paths.clone(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if not (clone / '.git').exists():
        raise argparse.ArgumentTypeError(
            f'{value} has no clone in {clone}: `mason project add {value}` makes one'
        )
    return clone


class Refused(Exception):
    """An input problem to report on stderr, exit status 1."""


class Answer(Refused):
    """An answer on stdout with exit status 1, for a script to act on: `inbox --count`."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='mason', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)

    cmd = commands.add_parser('validate', help='validate an artifact file; exit 1 if invalid')
    cmd.add_argument('file', type=Path, help='the artifact JSON file')
    cmd.add_argument('--diff', type=Path, help="the PR's unified diff (required for a review)")

    cmd = commands.add_parser('annotate-diff', help='prefix every diff line with its coordinates')
    cmd.add_argument('input', type=Path, nargs='?', help='unified diff (default: stdin)')
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'review-context', help='gather what code-review needs about a PR into a directory'
    )
    cmd.add_argument('project', type=project_dir, help=PROJECT_HELP)
    cmd.add_argument('--pr', type=int, required=True)
    cmd.add_argument('--dir', type=Path, required=True, help='the run directory to write into')

    cmd = commands.add_parser('render-review', help='render a review as a comment or API payload')
    cmd.add_argument('file', type=Path)
    cmd.add_argument('--diff', type=Path, required=True)
    cmd.add_argument('--run', type=Path, required=True, help="the run's run.json")
    cmd.add_argument('--inline', choices=('none', 'suggestions', 'all'), default='none')
    cmd.add_argument(
        '--payload', action='store_true', help='emit the pulls/{n}/reviews API JSON instead'
    )
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'collect-review-outcomes',
        help="what happened to code-review's findings over a period, as review-outcomes/1",
    )
    cmd.add_argument('project', type=project_dir, help=PROJECT_HELP)
    cmd.add_argument('--run', type=Path, required=True, help="the collecting run's run.json")
    cmd.add_argument(
        '--since', help='start of the period (ISO date or time); default period_days ago'
    )
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'render-review-retrospective', help='render review outcomes and findings as a report'
    )
    cmd.add_argument('outcomes', type=Path, help='collect-review-outcomes output')
    cmd.add_argument('--run', type=Path, required=True, help="the run's run.json")
    cmd.add_argument('--findings', type=Path, nargs='*', default=[], help='the findings files')
    cmd.add_argument('-o', '--output', type=Path)

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
    cmd.add_argument('project', type=project_dir, help=PROJECT_HELP)
    cmd.add_argument('-o', '--output', type=Path, required=True)

    cmd = commands.add_parser(
        'open-findings',
        help='fingerprints of the findings already in open automation PRs and issues',
    )
    cmd.add_argument('project', type=project_dir, help=PROJECT_HELP)
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'sentry-issues',
        help="a project's unresolved Sentry issues in one environment, without personal data",
    )
    cmd.add_argument('project', type=project_dir, help=PROJECT_HELP)
    cmd.add_argument('--environment', help='default: the first in [sentry-errors] environments')
    cmd.add_argument(
        '--details', type=int, default=25, help='fetch releases for the N most frequent'
    )
    cmd.add_argument('-o', '--output', type=Path, required=True)

    cmd = commands.add_parser(
        'sentry-event', help="a Sentry issue's latest event, cut down to what explains the error"
    )
    cmd.add_argument('project', type=project_dir, help=PROJECT_HELP)
    cmd.add_argument('issue', help='the issue id, or its short id (e.g. EXAMPLE-1A2)')
    cmd.add_argument('--environment')
    cmd.add_argument('-o', '--output', type=Path, required=True)

    cmd = commands.add_parser(
        'render-triage', help='the issue a sentry-errors run opens for findings with no fix'
    )
    cmd.add_argument('--environment', required=True)
    cmd.add_argument('--run', type=Path, required=True, help="the run's run.json")
    cmd.add_argument('--findings', type=Path, nargs='+', required=True)
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'collect-sentry-outcomes', help='whether the Sentry fixes worked, as sentry-outcomes/1'
    )
    cmd.add_argument('project', type=project_dir, help=PROJECT_HELP)
    cmd.add_argument('--run', type=Path, required=True, help="the collecting run's run.json")
    cmd.add_argument(
        '--since', help='start of the period (ISO date or time); default period_days ago'
    )
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'render-sentry-retrospective', help='render Sentry outcomes and findings as a report'
    )
    cmd.add_argument('outcomes', type=Path, help='collect-sentry-outcomes output')
    cmd.add_argument('--run', type=Path, required=True, help="the run's run.json")
    cmd.add_argument('--findings', type=Path, nargs='*', default=[], help='the findings files')
    cmd.add_argument('-o', '--output', type=Path)

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
        'collect-outcomes', help="what happened to a workflow's PRs and the alerts, as outcomes/2"
    )
    cmd.add_argument('project', type=project_dir, help=PROJECT_HELP)
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
        'source',
        help="clone mason's own repository, at its default branch, to propose a change to it",
    )
    cmd.add_argument('directory', type=Path, help='where to clone it; must not exist')

    cmd = commands.add_parser(
        'schema', help="print a schema's page: what each field of an artifact means"
    )
    cmd.add_argument('name', nargs='?', help='e.g. dependency-report or finding; omit to list them')

    cmd = commands.add_parser(
        'collect-report', help="a month's vulnerability figures, as dependency-report/1"
    )
    cmd.add_argument('project', type=project_dir, help=PROJECT_HELP)
    cmd.add_argument('--run', type=Path, required=True, help="the reporting run's run.json")
    cmd.add_argument(
        '--month', help='YYYY-MM; default the last complete month. The current month runs to now'
    )
    cmd.add_argument(
        '--alerts', type=Path, help='saved alerts (gh api …/dependabot/alerts); default fetched'
    )
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser('render-report', help='render a dependency report as markdown')
    cmd.add_argument('report', type=Path, help='collect-report output')
    cmd.add_argument('--run', type=Path, required=True, help="the run's run.json")
    cmd.add_argument('--notes', type=Path, help="markdown on what's still open and the plan")
    cmd.add_argument('--title', action='store_true', help='print only the title')
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'post-discussion',
        help='publish markdown as a new discussion; refuses a title the category already has',
    )
    cmd.add_argument('project', type=project_dir, help=PROJECT_HELP)
    cmd.add_argument('--category', required=True, help='the Discussions category, by name')
    cmd.add_argument('--title', required=True)
    cmd.add_argument('--body-file', type=Path, required=True)

    cmd = commands.add_parser(
        'run-start', help="create a run's directory and record; prints dir, id and branch"
    )
    cmd.add_argument('project', type=project_dir, help=PROJECT_HELP)
    cmd.add_argument('workflow', help='e.g. dependency-upgrades')

    cmd = commands.add_parser('extract', help='print the artifacts embedded in markdown as JSON')
    cmd.add_argument('input', type=Path, nargs='?', help='markdown (default: stdin)')
    cmd.add_argument('--schema', help='only artifacts of this schema, e.g. change/1')
    cmd.add_argument('-o', '--output', type=Path)

    cmd = commands.add_parser(
        'build-assessment', help='every open alert and its finding, as assessment/1'
    )
    cmd.add_argument('project', type=project_dir, help=PROJECT_HELP)
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
    cmd.add_argument('project', type=project_dir, nargs='?', default=Path('.'), help=PROJECT_HELP)
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
        help='also check the tokens this workflow needs',
    )
    cmd.add_argument(
        '--unattended', action='store_true', help='also check for a long-lived Claude token'
    )

    cmd = commands.add_parser(
        'run',
        help='start a workflow in an agent session: interactive, or headless with --unattended',
    )
    cmd.add_argument('workflow', help='an installed workflow (`mason doctor` lists them)')
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

    cmd = commands.add_parser(
        'project', help='add, list, archive or remove the projects you look after'
    )
    action = cmd.add_subparsers(dest='action', required=True)
    sub = action.add_parser(
        'add',
        help='clone it, choose its workflows, check GitHub access, create the label, and write '
        'your settings for it',
    )
    sub.add_argument('repo', help='owner/name of its GitHub repository')
    sub.add_argument(
        '--workflow',
        action='append',
        default=[],
        help='a workflow to enable; repeatable (default: ask, or every workflow with --yes)',
    )
    sub.add_argument('--yes', action='store_true', help='ask nothing')
    sub.add_argument('--dry-run', action='store_true', help='say what it would do; write nothing')
    action.add_parser('list', help='each project, its workflows, and when each last ran here')
    sub = action.add_parser(
        'archive', help='stop its runs, keeping its settings, clone, runs and logs'
    )
    sub.add_argument('repo', help='owner/name')
    sub.add_argument('--undo', action='store_true', help='run it again')
    sub = action.add_parser(
        'remove', help='delete its settings, clone, runs and logs; nothing on GitHub changes'
    )
    sub.add_argument('repo', help='owner/name')
    sub.add_argument('--yes', action='store_true', help='ask nothing')

    cmd = commands.add_parser(
        'inbox', help='what needs you, across your projects; exit 1 with --count if anything does'
    )
    cmd.add_argument('--repo', action='append', default=[], help='only this project; repeatable')
    cmd.add_argument('--json', action='store_true', help='the items as JSON')
    cmd.add_argument('--count', action='store_true', help='print how many; exit 1 if any')

    cmd = commands.add_parser(
        'defer', help='record an action an unattended run left for a person, for `mason inbox`'
    )
    cmd.add_argument('run', type=Path, help="the run's directory (run-start's `dir`)")
    cmd.add_argument('--action', required=True, help='what to do')
    cmd.add_argument('--reason', required=True, help='why it needs a person')
    cmd.add_argument(
        '--command', dest='suggested', help='the command that would do it, if there is one'
    )

    cmd = commands.add_parser(
        'save-result', help="keep a run's output as the result for a key: mason's master copy"
    )
    cmd.add_argument('files', type=Path, nargs='*', help='the files to keep, its data among them')
    cmd.add_argument('--run', type=Path, required=True, help="the run's run.json")
    cmd.add_argument('--key', required=True, help='what names the result, e.g. 2026-10')
    cmd.add_argument('--title', required=True, help='its title wherever it is published')
    cmd.add_argument('--body', type=Path, required=True, help='the markdown file to publish')
    cmd.add_argument(
        '--comment',
        type=Path,
        help='what changed since the last version: an issue already open under the title gets '
        'this body and this comment',
    )

    cmd = commands.add_parser(
        'publish', help='copy a result to where the settings say, or to --to; records each copy'
    )
    cmd.add_argument('result', help='<workflow>/<key>, as `mason results` lists them')
    cmd.add_argument('--repo', required=True, help='owner/name')
    cmd.add_argument(
        '--to',
        action='append',
        default=[],
        help="discussion[:<category>], issue or file:<directory>; repeatable (default: the workflow's "
        '`publish` setting)',
    )

    cmd = commands.add_parser('results', help="a project's results, and where each was published")
    cmd.add_argument(
        '--repo', action='append', default=[], help='owner/name (default: every project)'
    )
    cmd.add_argument('--workflow', help='only this workflow')
    cmd.add_argument('--remove', metavar='WORKFLOW/KEY', help='delete this result (asks first)')
    cmd.add_argument('--yes', action='store_true', help='with --remove: ask nothing')

    cmd = commands.add_parser(
        'note', help="keep an analysis or assessment in mason, out of the project's history"
    )
    action = cmd.add_subparsers(dest='action', required=True)
    repo_help = 'owner/name (default: the checkout in the current directory)'
    sub = action.add_parser('save', help='save a new note, or a new version of one with --update')
    sub.add_argument('file', type=Path, help='the note, as markdown')
    sub.add_argument('--repo', help=repo_help)
    sub.add_argument('--title', help='its title (required for a new note)')
    sub.add_argument('--update', metavar='KEY', help='save a new version of this note')
    sub.add_argument('--status', default='draft', choices=notes.STATUSES)
    sub.add_argument('--tag', action='append', default=[], help='repeatable')
    sub = action.add_parser('list', help="a project's notes (default: every project's)")
    sub.add_argument('--repo', help=repo_help)
    sub.add_argument('--status', choices=notes.STATUSES)
    sub.add_argument('--tag')
    sub = action.add_parser('show', help='print a note')
    sub.add_argument('key')
    sub.add_argument('--repo', help=repo_help)
    sub = action.add_parser('status', help='record what happened to a note')
    sub.add_argument('key')
    sub.add_argument('status', choices=notes.STATUSES)
    sub.add_argument('--repo', help=repo_help)
    sub.add_argument('--link', help='where the project now keeps it: a PR, an issue, a page')
    sub.add_argument('--by', dest='superseded_by', metavar='KEY', help='for superseded: the note')

    cmd = commands.add_parser('lock', help='take or release the one-run-at-a-time lock')
    cmd.add_argument('action', choices=('acquire', 'release'))
    cmd.add_argument('project', type=project_dir, help=PROJECT_HELP)
    cmd.add_argument('workflow', help='e.g. dependency-upgrades')

    args = parser.parse_args(argv)
    try:
        output = COMMANDS[args.command](args)
    except Refused as exc:
        print(exc, file=sys.stdout if isinstance(exc, Answer) else sys.stderr)
        return 1
    if output is not None:
        if getattr(args, 'output', None):
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding='utf-8')
        else:
            sys.stdout.write(output)
    return 0


def _validate(args: argparse.Namespace) -> str:
    artifacts = _load(args.file, diff_file=args.diff)
    return f'{args.file}: valid {artifacts[0]["schema"]}\n'


def _annotate(args: argparse.Namespace) -> str:
    return diff.annotate(_read(args.input) if args.input else sys.stdin.read())


def _review_context(args: argparse.Namespace) -> str:
    try:
        repo = project.load(args.project)['repo']['name']
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    args.dir.mkdir(parents=True, exist_ok=True)
    try:
        context = review_context.fetch(repo, args.pr, args.dir)
    except (collect.CollectError, ValueError, KeyError) as exc:
        raise Refused(str(exc)) from exc
    summary = {
        key: context[key]
        for key in ('pr', 'state', 'draft', 'head_sha', 'already_reviewed', 'delta')
    }
    summary['previous_review'] = bool(context['previous_review'])
    summary['files'] = [str(args.dir / name) for name in sorted(p.name for p in args.dir.iterdir())]
    return json.dumps(summary, indent=2) + '\n'


def _render_review(args: argparse.Namespace) -> str:
    (review,) = _load(args.file, diff_file=args.diff, schema='review/1')
    (record,) = _load(args.run, schema='run/1')
    if review['run'] != record['id']:
        raise Refused(f'review.run is {review["run"]!r}, but {args.run} is run {record["id"]!r}')
    try:
        if args.payload:
            payload = render.review_payload(review, record, args.inline)
            return json.dumps(payload, indent=2) + '\n'
        return render.review_comment(review, record, args.inline)
    except ValueError as exc:
        raise Refused(str(exc)) from exc


def _collect_review_outcomes(args: argparse.Namespace) -> str:
    (record,) = _load(args.run, schema='run/1')
    try:
        config = project.load(args.project, 'code-review-retrospective')
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    since, until = _period(args.since, config['code-review-retrospective']['period_days'])
    try:
        result = review_outcomes.collect(config['repo']['name'], record, since, until)
    except (collect.CollectError, ValueError, KeyError) as exc:
        raise Refused(str(exc)) from exc
    errors = validate(result)
    if errors:
        raise Refused('\n'.join(['the collected outcomes are invalid:', *errors]))
    return json.dumps(result, indent=2, ensure_ascii=False) + '\n'


def _render_review_retrospective(args: argparse.Namespace) -> str:
    (collected,) = _load(args.outcomes, schema='review-outcomes/1')
    (record,) = _load(args.run, schema='run/1')
    if collected['run'] != record['id']:
        raise Refused(f'the outcomes are from run {collected["run"]!r}, not {record["id"]!r}')
    findings = [f for path in args.findings for f in _load(path, schema='finding/1')]
    try:
        return render.review_retrospective_body(collected, findings, record)
    except ValueError as exc:
        raise Refused(str(exc)) from exc


def _sentry_settings(project_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        config = project.load(project_dir, 'sentry-errors')
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    return config, config['sentry-errors']


def _open_findings(args: argparse.Namespace) -> str:
    try:
        repo = project.load(args.project)['repo']['name']
        return json.dumps(collect.open_findings(repo), indent=2) + '\n'
    except (project.ConfigError, collect.CollectError, ValueError) as exc:
        raise Refused(str(exc)) from exc


def _sentry_issues(args: argparse.Namespace) -> None:
    _, settings = _sentry_settings(args.project)
    environment = args.environment or settings['environments'][0]
    client = sentry.client_from_config(settings)
    try:
        issues = client.unresolved(environment, f'{settings["period_days"]}d')
        for index, issue in enumerate(issues[: args.details]):
            issues[index] = sentry.with_detail(issue, client.issue(issue['id'], environment))
    except (sentry.SentryError, KeyError, ValueError) as exc:
        raise Refused(str(exc)) from exc
    result = {
        'environment': environment,
        'period_days': settings['period_days'],
        'fetched_at': f'{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}',
        'issues': issues,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
    )
    print(
        f'{len(issues)} unresolved issues in {environment} saved to {args.output}', file=sys.stderr
    )


def _sentry_event(args: argparse.Namespace) -> None:
    _, settings = _sentry_settings(args.project)
    client = sentry.client_from_config(settings)
    try:
        issue_id = args.issue if args.issue.isdigit() else client.by_short_id(args.issue)
        event = client.latest_event(issue_id, args.environment)
    except (sentry.SentryError, KeyError, ValueError) as exc:
        raise Refused(str(exc)) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(event, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'latest event of {args.issue} saved to {args.output}', file=sys.stderr)


def _render_triage(args: argparse.Namespace) -> str:
    (record,) = _load(args.run, schema='run/1')
    findings = [f for path in args.findings for f in _load(path, schema='finding/1')]
    try:
        return render.triage_body(args.environment, findings, record)
    except ValueError as exc:
        raise Refused(str(exc)) from exc


def _collect_sentry_outcomes(args: argparse.Namespace) -> str:
    (record,) = _load(args.run, schema='run/1')
    try:
        resolved = project.resolve(args.project)
        project.require(resolved, 'sentry-errors')
        project.require(resolved, 'sentry-retrospective')
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    config = resolved.config
    settings, retro = config['sentry-errors'], config['sentry-retrospective']
    since, until = _period(args.since, retro['period_days'])
    policy = {
        'quiet_after_days': settings['quiet_after_days'],
        'deploy_grace_hours': retro['deploy_grace_hours'],
        'stale_pr_after_days': config['limits']['stale_after_days'],
    }
    try:
        result = sentry_outcomes.collect(
            config['repo']['name'],
            record,
            since,
            until,
            policy,
            settings,
            sentry.client_from_config(settings),
        )
    except (collect.CollectError, sentry.SentryError, ValueError, KeyError) as exc:
        raise Refused(str(exc)) from exc
    errors = validate(result)
    if errors:
        raise Refused('\n'.join(['the collected outcomes are invalid:', *errors]))
    return json.dumps(result, indent=2, ensure_ascii=False) + '\n'


def _render_sentry_retrospective(args: argparse.Namespace) -> str:
    (collected,) = _load(args.outcomes, schema='sentry-outcomes/1')
    (record,) = _load(args.run, schema='run/1')
    if collected['run'] != record['id']:
        raise Refused(f'the outcomes are from run {collected["run"]!r}, not {record["id"]!r}')
    findings = [f for path in args.findings for f in _load(path, schema='finding/1')]
    try:
        return render.sentry_retrospective_body(collected, findings, record)
    except ValueError as exc:
        raise Refused(str(exc)) from exc


def _period(since_arg: str | None, period_days: int) -> tuple[datetime, datetime]:
    until = datetime.now(UTC)
    since = until - timedelta(days=period_days)
    if since_arg:
        try:
            since = datetime.fromisoformat(since_arg.replace('Z', '+00:00'))
        except ValueError as exc:
            raise Refused(f'--since: {exc}') from exc
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
    return since, until


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
    since, until = _period(args.since, settings['period_days'])
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
    (collected,) = _load(args.outcomes, schema='outcomes/2')
    (record,) = _load(args.run, schema='run/1')
    if collected['run'] != record['id']:
        raise Refused(f'the outcomes are from run {collected["run"]!r}, not {record["id"]!r}')
    findings = [f for path in args.findings for f in _load(path, schema='finding/1')]
    try:
        return render.retrospective_body(collected, findings, record)
    except ValueError as exc:
        raise Refused(str(exc)) from exc


def _source(args: argparse.Namespace) -> str:
    try:
        url = version.source_url()
        version.clone(url, args.directory)
    except version.VersionError as exc:
        raise Refused(str(exc)) from exc
    return json.dumps({'dir': str(args.directory), 'url': url}, indent=2) + '\n'


SCHEMA_PAGES = Path(__file__).resolve().parent / 'schemas'


def _schema(args: argparse.Namespace) -> str:
    pages = sorted(p.stem for p in SCHEMA_PAGES.glob('*.md') if p.stem != 'README')
    if not args.name:
        return '\n'.join(pages) + '\n'
    name = args.name.split('/')[0]
    if name not in pages:
        raise Refused(f'no schema {args.name!r} (known: {", ".join(pages)})')
    return (SCHEMA_PAGES / f'{name}.md').read_text(encoding='utf-8')


def _collect_report(args: argparse.Namespace) -> str:
    (record,) = _load(args.run, schema='run/1')
    try:
        config = project.load(args.project, 'dependency-report')
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    targets = config['dependency-retrospective']
    now = datetime.now(UTC)
    try:
        year, month = report.parse_month(args.month) if args.month else report.previous_month(now)
        report.month_bounds(year, month, now)
    except ValueError as exc:
        raise Refused(f'--month: {exc}') from exc
    repo = config['repo']['name']
    if args.alerts:
        alerts: list[dict[str, Any]] = []
        for page in deps.json_stream(_read(args.alerts)):
            alerts.extend(page if isinstance(page, list) else [page])
    else:
        try:
            alerts = collect.all_alerts(repo)
        except collect.CollectError as exc:
            raise Refused(str(exc)) from exc
    try:
        result = report.build(
            repo=repo,
            run=record,
            year=year,
            month=month,
            now=now,
            fix_within_days={s: targets[f'fix_within_days_{s}'] for s in deps.SEVERITIES},
            alerts=alerts,
            manifests={g: config['dependency-report'][g] for g in report.CONFIGURED_GROUPS},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Refused(f'the alerts are not in the shape Dependabot returns: {exc!r}') from exc
    errors = validate(result)
    if errors:  # a bug here, not bad input: say so rather than write a broken artifact
        raise Refused('\n'.join(['the report data is invalid:', *errors]))
    return json.dumps(result, indent=2, ensure_ascii=False) + '\n'


def _render_report(args: argparse.Namespace) -> str:
    (data,) = _load(args.report, schema=report.SCHEMA)
    if args.title:
        return render.dependency_report_title(data) + '\n'
    (record,) = _load(args.run, schema='run/1')
    if data['run'] != record['id']:
        raise Refused(f'the report is from run {data["run"]!r}, not {record["id"]!r}')
    notes = _read(args.notes) if args.notes else ''
    try:
        return render.dependency_report_body(data, record, notes)
    except ValueError as exc:
        raise Refused(str(exc)) from exc


def _post_discussion(args: argparse.Namespace) -> str:
    try:
        repo = project.load(args.project)['repo']['name']
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    body = _read(args.body_file)
    if not body.strip():
        raise Refused(f'{args.body_file}: is empty')
    try:
        return discussions.post(collect.gh, repo, args.category, args.title, body) + '\n'
    except (discussions.DiscussionError, collect.CollectError) as exc:
        raise Refused(str(exc)) from exc


def _run_start(args: argparse.Namespace) -> str:
    try:
        repo = project.load(args.project)['repo']['name']
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    try:
        mapping = project.skill_paths(repo)
        skill = skills.check_resolved(args.workflow, mapping)
    except (skills.SkillError, project.ConfigError) as exc:
        raise Refused(str(exc)) from exc
    versions = {'mason_version': version.package_version()}
    if skill is not None:
        if skill.version:
            versions['skill_version'] = skill.version
        versions['skill_source'] = skills.provenance(args.workflow, skill, mapping)
    directory, record = run.start(
        paths.runs(repo), args.workflow, repo, _version(args).strip(), versions=versions
    )
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
    try:
        tokens = runner.declared(args.workflow).env if args.workflow else ()
    except ValueError as exc:
        raise Refused(str(exc)) from exc
    checks = doctor.run_checks(tokens, dict(os.environ), args.unattended)
    try:
        found = skills.installed()
    except skills.SkillError as exc:
        raise Refused(str(exc)) from exc
    checks += doctor.check_skills(found, version.package_version(), args.workflow)
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
    try:
        enabled = project.enabled_workflows(repo)
        archived_on = project.archived(repo)
        mapping = project.skill_paths(repo)
        granted = project.grants(repo)
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    if archived_on:
        raise Refused(
            f'{repo} was archived on {archived_on}: `mason project archive {repo} --undo` to run '
            'it again'
        )
    try:
        skill = skills.check_resolved(args.workflow, mapping)
    except skills.SkillError as exc:
        raise Refused(f'{exc}\n{args.workflow} not started') from exc
    if skill is None or skill.workflow is None:
        try:
            runner.declared(args.workflow)  # says what is installed
        except ValueError as exc:
            raise Refused(str(exc)) from exc
    spec = skill.workflow
    try:
        runner.prompt(args.workflow, args.pr, args.request, spec=spec)
    except ValueError as exc:
        raise Refused(str(exc)) from exc
    if args.unattended and (missing := runner.ungranted(args.workflow, spec, mapping, granted)):
        listed = ', '.join(f"'{tool}'" for tool in missing)
        raise Refused(
            f'{args.workflow} runs the skill in {skill.path.parent}, which asks for tools you '
            f"haven't granted for unattended runs: {listed}. If you trust it with them, add to "
            f'{paths.settings(repo)}:\n\n[grants]\n{args.workflow} = [{listed}]\n\n'
            f'{args.workflow} not started'
        )
    if args.workflow not in enabled:
        raise Refused(
            f'{args.workflow} is not enabled for {repo}: add it to `enabled` under [workflows] '
            f'in {paths.settings(repo)}'
        )
    state = paths.project_state(repo)
    # The session's own skills directory: this workflow's skill and the skills it names, linked
    # from where the settings or the install put them, so it sees no others of mason's.
    session = state / 'sessions' / runner.log_path(Path(), args.workflow).stem
    skill_file = session / '.claude' / 'skills' / args.workflow / 'SKILL.md'
    if not args.dry_run:
        try:
            skills.stage(args.workflow, session, mapping)
        except skills.SkillError as exc:
            raise Refused(f'{exc}\n{args.workflow} not started') from exc
    if args.unattended:
        command = runner.command(
            directory, state, args.workflow, tuple(args.allow), args.claude, args.pr, session,
            args.request, spec, skill_file,
        )  # fmt: skip
        env = runner.environment(args.trigger)
    else:
        command = runner.interactive_command(
            directory, state, args.workflow, session, args.claude, args.pr, args.request, spec,
            skill_file,
        )  # fmt: skip
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


def _project(args: argparse.Namespace) -> str:
    try:
        if args.action == 'list':
            return projects.render_list(projects.entries())
        if args.action == 'archive':
            if args.undo:
                return projects.unarchive(args.repo)
            return projects.archive(args.repo, datetime.now(UTC).strftime('%Y-%m-%d'))
        if args.action == 'remove':
            removal = projects.removal(args.repo)
            print(removal.describe(), file=sys.stderr)
            if not args.yes and _ask('Delete them? [y/N] ').strip().lower() not in ('y', 'yes'):
                return 'Nothing deleted.\n'
            return projects.remove(removal)
    except projects.ProjectError as exc:
        raise Refused(str(exc)) from exc
    return _project_add(args)


def _project_add(args: argparse.Namespace) -> str:
    workflows = list(dict.fromkeys(args.workflow))
    try:
        if not workflows and not args.yes:
            print(f'Workflows for {args.repo}:\n{projects.menu()}', file=sys.stderr)
            workflows = projects.choose(_ask('Enable which (numbers or names; Enter for all)? '))
        plan = projects.plan(args.repo, workflows or sorted(skills.workflows()))
        print(plan.describe(), file=sys.stderr)
        if args.dry_run:
            return 'Dry run: nothing written.\n'
        if not args.yes and _ask('Go ahead? [y/N] ').strip().lower() not in ('y', 'yes'):
            return 'Nothing written.\n'
        return projects.apply(plan)
    except projects.ProjectError as exc:
        raise Refused(str(exc)) from exc


def _ask(question: str) -> str:
    print(question, end='', file=sys.stderr, flush=True)
    try:
        return input()
    except EOFError as exc:
        raise Refused(
            '\nno answer: give --yes (and --workflow) to go ahead without asking'
        ) from exc


def _inbox(args: argparse.Namespace) -> str:
    added = projects.added()
    for repo in args.repo:
        if repo not in added:
            raise Refused(f'--repo: {repo} is not added: `mason project add {repo}`')
    try:
        items = inbox.collect(args.repo or None)
    except (inbox.InboxError, projects.ProjectError) as exc:
        raise Refused(str(exc)) from exc
    if args.count:
        if items:
            raise Answer(f'{len(items)}')
        return '0\n'
    return inbox.to_json(items) if args.json else inbox.render(items)


def _defer(args: argparse.Namespace) -> str:
    try:
        return inbox.defer(args.run, args.action, args.reason, args.suggested)
    except inbox.InboxError as exc:
        raise Refused(str(exc)) from exc


def _save_result(args: argparse.Namespace) -> str:
    try:
        saved = results.save(
            args.run, args.key, args.files, args.body, args.title, comment=args.comment
        )
    except results.ResultError as exc:
        raise Refused(str(exc)) from exc
    return f'{saved}\n'


def _result(value: str, repo: str) -> results.Result:
    workflow, _, key = value.partition('/')
    if not key:
        raise Refused(f'{value}: give <workflow>/<key>, e.g. dependency-report/2026-10')
    try:
        return results.load(repo, workflow, key)
    except results.ResultError as exc:
        raise Refused(str(exc)) from exc


def _publish(args: argparse.Namespace) -> str:
    result = _result(args.result, args.repo)
    try:
        targets = args.to or project.value(args.repo, result.workflow, 'publish') or []
        category = project.value(args.repo, result.workflow, 'category')
    except project.ConfigError as exc:
        raise Refused(str(exc)) from exc
    if not targets:
        return f'{args.result}: kept, not published (its `publish` setting is empty)\n'
    lines = []
    for target in targets:
        try:
            lines.append(f'{target}: {results.publish(result, target, collect.gh, category)}')
        except (results.ResultError, collect.CollectError) as exc:
            raise Refused('\n'.join([*lines, f'{target}: {exc}'])) from exc
    return '\n'.join(lines) + '\n'


def _results(args: argparse.Namespace) -> str:
    repos = args.repo or projects.added()
    if args.remove:
        if len(repos) != 1:
            raise Refused('--remove: give the project with --repo')
        result = _result(args.remove, repos[0])
        print(f"Deleting {result.path}, mason's copy of {args.remove}.", file=sys.stderr)
        if not args.yes and _ask('Delete it? [y/N] ').strip().lower() not in ('y', 'yes'):
            return 'Nothing deleted.\n'
        return results.remove(result)
    try:
        return ''.join(
            f'{repo}\n' + results.render(results.listing(repo, args.workflow)) for repo in repos
        )
    except results.ResultError as exc:
        raise Refused(str(exc)) from exc


def _note_repo(given: str | None) -> str | None:
    if given:
        return given
    return detect.repo_name(Path.cwd()) if (Path.cwd() / '.git').exists() else None


def _note(args: argparse.Namespace) -> str:
    repo = _note_repo(args.repo)
    try:
        if args.action == 'list':
            repos = [repo] if repo else projects.added()
            found = [notes.render_list(r, notes.listing(r, args.status, args.tag)) for r in repos]
            return ''.join(found) or 'No projects added yet.\n'
        if repo is None:
            raise Refused('give --repo, or run it in a checkout of the project')
        if args.action == 'save':
            key = notes.save(repo, args.file, args.title, args.update, args.status, args.tag)
            return f'Saved note {key} for {repo}\n'
        if args.action == 'show':
            return notes.show(notes.load(repo, args.key))
        return notes.set_status(repo, args.key, args.status, args.link, args.superseded_by)
    except (notes.NoteError, results.ResultError) as exc:
        raise Refused(str(exc)) from exc


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
    'project': _project,
    'inbox': _inbox,
    'defer': _defer,
    'save-result': _save_result,
    'publish': _publish,
    'results': _results,
    'note': _note,
    'run': _run,
    'skills': _skills,
    'validate': _validate,
    'render-change': _render_change,
    'render-stopped': _render_stopped,
    'annotate-diff': _annotate,
    'open-findings': _open_findings,
    'sentry-issues': _sentry_issues,
    'sentry-event': _sentry_event,
    'render-triage': _render_triage,
    'collect-sentry-outcomes': _collect_sentry_outcomes,
    'render-sentry-retrospective': _render_sentry_retrospective,
    'review-context': _review_context,
    'render-review': _render_review,
    'collect-review-outcomes': _collect_review_outcomes,
    'render-review-retrospective': _render_review_retrospective,
    'alerts': _alerts,
    'run-start': _run_start,
    'collect-outcomes': _collect_outcomes,
    'render-retrospective': _render_retrospective,
    'schema': _schema,
    'source': _source,
    'collect-report': _collect_report,
    'render-report': _render_report,
    'post-discussion': _post_discussion,
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


def _load(
    path: Path, diff_file: Path | None = None, schema: str | None = None
) -> list[dict[str, Any]]:
    """Read a file holding one artifact or an array of them, and validate each."""
    try:
        data = json.loads(_read(path))
    except json.JSONDecodeError as exc:
        raise Refused(f'{path}: is not valid JSON: {exc}') from exc
    artifacts = data if isinstance(data, list) else [data]
    diff_text = _read(diff_file) if diff_file else None
    if not artifacts:
        raise Refused(f'{path}: holds no artifacts')
    problems = []
    for index, artifact in enumerate(artifacts):
        where = f'{path}[{index}]' if isinstance(data, list) else str(path)
        if schema and isinstance(artifact, dict) and artifact.get('schema') != schema:
            problems.append(f'{where}: expected a {schema}, got {artifact.get("schema")!r}')
            continue
        errors = validate(artifact, diff_text)
        if errors:
            problems.append(f'{where}: {len(errors)} error(s)')
            problems += [f'- {error}' for error in errors]
    if problems:
        raise Refused('\n'.join(problems))
    return artifacts
