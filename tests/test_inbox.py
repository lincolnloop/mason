import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from artifacts import inbox, paths, runner

from . import ROOT

REPO = 'o/app'
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def pr(number: int, **fields) -> dict:
    return {
        'number': number,
        'title': f'Change {number}',
        'url': f'https://github.com/o/app/pull/{number}',
        'isDraft': False,
        'reviewDecision': '',
        'mergeable': 'MERGEABLE',
        'statusCheckRollup': [{'name': 'tests', 'conclusion': 'SUCCESS'}],
        'createdAt': '2026-11-22T12:00:00Z',
        **fields,
    }


def issue(number: int, comments: int = 0) -> dict:
    return {
        'number': number,
        'title': f'Report {number}',
        'url': f'https://github.com/o/app/issues/{number}',
        'comments': [{}] * comments,
        'createdAt': '2026-11-24T12:00:00Z',
    }


class FakeGh:
    def __init__(self, prs: Sequence[dict] = (), issues: Sequence[dict] = (), fail: str = ''):
        self.prs, self.issues, self.fail = list(prs), list(issues), fail

    def __call__(self, args: Sequence[str]) -> tuple[int, str, str]:
        if self.fail:
            return 1, '', self.fail
        assert args[1:3] == ['list', '--repo'] and '--label' in args, args
        return 0, json.dumps(self.prs if args[0] == 'pr' else self.issues), ''


def settings(repo: str, text: str = '') -> None:
    path = paths.settings(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'version = 1\n{text}')


def start_run(repo: str, run_id: str) -> Path:
    directory = paths.runs(repo) / run_id
    directory.mkdir(parents=True)
    (directory / 'run.json').write_text('{}')
    return directory


class Clean(unittest.TestCase):
    def tearDown(self) -> None:
        for home in (paths.config_home(), paths.state_home(), paths.data_home()):
            shutil.rmtree(home, ignore_errors=True)


class DeferTests(Clean):
    def test_records_each_action(self) -> None:
        run = start_run(REPO, '20261125-090000-dependency-upgrades')
        inbox.defer(run, 'Create the automation label', 'it is how open PRs are counted', 'gh x')
        inbox.defer(run, ' Archive the noise ', ' a person decides ')
        self.assertEqual(
            json.loads((run / inbox.DEFERRED).read_text()),
            [
                {
                    'action': 'Create the automation label',
                    'reason': 'it is how open PRs are counted',
                    'command': 'gh x',
                },
                {'action': 'Archive the noise', 'reason': 'a person decides'},
            ],
        )

    def test_refusals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(inbox.InboxError, 'not a run directory'):
                inbox.defer(Path(tmp), 'a', 'b')
        run = start_run(REPO, '20261125-090000-code-review')
        with self.assertRaisesRegex(inbox.InboxError, '--reason is empty'):
            inbox.defer(run, 'a', ' ')
        (run / inbox.DEFERRED).write_text('{"action": "a"}')
        with self.assertRaisesRegex(inbox.InboxError, 'must be a list'):
            inbox.defer(run, 'a', 'b')
        (run / inbox.DEFERRED).write_text('[')
        with self.assertRaisesRegex(inbox.InboxError, 'not valid JSON'):
            inbox.defer(run, 'a', 'b')


class GitHubTests(unittest.TestCase):
    def test_pull_requests_by_what_they_need(self) -> None:
        items = inbox.pr_items(
            REPO,
            [
                pr(1, statusCheckRollup=[{'name': 'tests', 'conclusion': 'FAILURE'}]),
                pr(2, mergeable='CONFLICTING'),
                pr(3, reviewDecision='CHANGES_REQUESTED'),
                pr(4, isDraft=True),
                pr(5, statusCheckRollup=[{'context': 'ci', 'state': 'PENDING'}]),
            ],
            NOW,
        )
        self.assertEqual(
            [(i.kind, i.text.split(': ', 1)[1]) for i in items],
            [
                (inbox.BLOCKED, 'CI failing (tests)'),
                (inbox.BLOCKED, 'conflicts with its base'),
                (inbox.REVIEW, 'changes requested, open 3 days'),
                (inbox.REVIEW, 'draft, unverified, open 3 days'),
                (inbox.REVIEW, 'waiting for review, open 3 days'),
            ],
        )
        self.assertEqual(items[0].where, 'https://github.com/o/app/pull/1')

    def test_issues_without_a_response(self) -> None:
        items = inbox.issue_items(REPO, [issue(7), issue(8, comments=2)], NOW)
        self.assertEqual([i.text for i in items], ['issue #7 Report 7: no response in 1 day'])

    def test_github_unreachable(self) -> None:
        (item,) = inbox.github_items(REPO, FakeGh(fail='HTTP 401: Bad credentials'), NOW)
        self.assertEqual(item.kind, inbox.BROKEN)
        self.assertIn('Bad credentials', item.text)


class LocalTests(Clean):
    def test_failed_runs_tokens_and_deferred_actions(self) -> None:
        logs = paths.logs(REPO)
        logs.mkdir(parents=True)
        for stamp, status in (('20261124-090000', 1), ('20261123-090000', 0)):
            runner.write_status(logs / f'{stamp}-dependency-upgrades.log', status, NOW)
        runner.write_status(logs / '20261124-090000-code-review.log', 0, NOW)
        old = start_run(REPO, '20261123-090000-dependency-assessment')
        inbox.defer(old, 'superseded', 'a later run decides again')
        new = start_run(REPO, '20261124-090000-dependency-assessment')
        inbox.defer(new, 'Create the label', 'counts open PRs', 'gh label create automation')
        items = inbox.local_items(
            REPO, ['dependency-upgrades', 'dependency-assessment', 'code-review', 'sentry-errors'],
            dict(os.environ, SENTRY_AUTH_TOKEN=''),
        )  # fmt: skip
        texts = [i.text for i in items]
        self.assertIn('sentry-errors needs SENTRY_AUTH_TOKEN', texts)
        self.assertIn('dependency-upgrades failed at 2026-11-25 12:00 UTC (status 1)', texts)
        self.assertFalse(any('code-review' in t for t in texts))
        (deferred,) = [i for i in items if i.kind == inbox.DECIDE]
        self.assertIn('Create the label (dependency-assessment, run 20261124-', deferred.text)
        self.assertEqual(deferred.where, 'gh label create automation')

    def test_tokens_kept_in_the_env_file_count(self) -> None:
        path = paths.config_home() / 'env'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# tokens\nexport SENTRY_AUTH_TOKEN=s3cr3t\nEMPTY=\n')
        self.assertEqual(inbox.token_file(), {'SENTRY_AUTH_TOKEN'})
        env = dict(os.environ, SENTRY_AUTH_TOKEN='')
        self.assertEqual(inbox.local_items(REPO, ['sentry-errors'], env), [])

    def test_one_item_for_skills_with_the_same_problem(self) -> None:
        from artifacts import skills

        from .test_skills import install

        for name in ('code-review', 'dependency-report'):
            install(skills.directories()[0], name, requires='')
        (item,) = inbox.local_items(REPO, ['code-review', 'dependency-report'], dict(os.environ))
        self.assertEqual(
            item.text,
            '2 skills (code-review, dependency-report): states no mason-requires in its '
            'frontmatter',
        )

    def test_only_enabled_workflows(self) -> None:
        run = start_run(REPO, '20261124-090000-dependency-report')
        inbox.defer(run, 'x', 'y')
        self.assertEqual(inbox.local_items(REPO, ['code-review'], dict(os.environ)), [])


class CollectTests(Clean):
    def test_most_urgent_first_and_archived_skipped(self) -> None:
        settings(
            REPO, '[workflows]\nenabled = ["sentry-errors"]\n[schedule]\nsentry-errors = "off"\n'
        )
        settings('o/old', "[project]\narchived = '2026-11-01'\n")
        gh = FakeGh(prs=[pr(2), pr(3, mergeable='CONFLICTING')], issues=[issue(4)])
        items = inbox.collect(run_gh=gh, env=dict(os.environ, SENTRY_AUTH_TOKEN=''), now=NOW)
        self.assertEqual(
            [i.kind for i in items],
            [inbox.BROKEN, inbox.BLOCKED, inbox.REVIEW, inbox.RESPOND],
        )
        self.assertEqual({i.project for i in items}, {REPO})
        text = inbox.render(items)
        self.assertTrue(text.startswith('o/app\n  ! sentry-errors needs SENTRY_AUTH_TOKEN'))
        self.assertTrue(text.endswith('1 broken, 1 blocked, 1 review, 1 respond\n'))
        self.assertEqual(json.loads(inbox.to_json(items))[0]['kind'], inbox.BROKEN)

    def test_nothing(self) -> None:
        self.assertEqual(
            inbox.render(
                inbox.collect(run_gh=FakeGh(), env=dict(os.environ, SENTRY_AUTH_TOKEN=''), now=NOW)
            ),
            'Nothing needs you.\n',
        )

    def test_a_broken_settings_file(self) -> None:
        settings(REPO, '[workflows]\nenabled = "all"\n')
        (item,) = inbox.collect(
            run_gh=FakeGh(), env=dict(os.environ, SENTRY_AUTH_TOKEN=''), now=NOW
        )
        self.assertEqual(item.text, 'settings file is invalid')


GH_SCRIPT = """#!/bin/sh
case "$1" in
  pr) echo '[]' ;;
  issue) echo '[{"number": 4, "title": "Report", "url": "u", "comments": [], "createdAt": "2026-11-24T12:00:00Z"}]' ;;
esac
"""


class CliTests(Clean):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        bin_dir = Path(self._tmp.name)
        fake = bin_dir / 'gh'
        fake.write_text(GH_SCRIPT)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}')

    def tearDown(self) -> None:
        super().tearDown()
        self._tmp.cleanup()

    def mason(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, '-I', str(ROOT / 'bin' / 'artifact'), *args],
            capture_output=True, text=True, env=self.env, check=False,
        )  # fmt: skip

    def test_defer_then_inbox(self) -> None:
        settings(REPO, '[workflows]\nenabled = ["code-review"]\n')
        run = start_run(REPO, '20261125-090000-code-review')
        result = self.mason('defer', str(run), '--action', 'Post it', '--reason', 'policy')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.mason('inbox')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            '- Post it (code-review, run 20261125-090000-code-review): policy', result.stdout
        )
        self.assertIn('issue #4 Report: no response', result.stdout)
        result = self.mason('inbox', '--count')
        self.assertEqual((result.returncode, result.stdout), (1, '2\n'))
        result = self.mason('inbox', '--json', '--repo', REPO)
        self.assertEqual([i['kind'] for i in json.loads(result.stdout)], ['decide', 'respond'])
        result = self.mason('inbox', '--repo', 'o/other')
        self.assertEqual(result.returncode, 1)
        self.assertIn('o/other is not added', result.stderr)
        result = self.mason('defer', str(run.parent), '--action', 'a', '--reason', 'b')
        self.assertIn('not a run directory', result.stderr)

    def test_nothing_counts_zero(self) -> None:
        result = self.mason('inbox', '--count')
        self.assertEqual((result.returncode, result.stdout), (0, '0\n'))


class ApproveTests(Clean):
    def setUp(self) -> None:
        self._bin = tempfile.TemporaryDirectory()
        bin_dir = Path(self._bin.name)
        self.record = bin_dir / 'calls'
        fake = bin_dir / 'gh'
        fake.write_text(
            f'#!/bin/sh\necho "$*" >> {self.record}\n[ "$1" = fail ] && exit 4\necho made it\n'
        )
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}')
        settings(
            REPO,
            '[workflows]\nenabled = ["dependency-assessment"]\n[schedule]\ndependency-assessment = "off"\n',
        )
        self.run = start_run(REPO, '20261125-090000-dependency-assessment')
        inbox.defer(self.run, 'Create the label', 'counts open PRs', 'gh label create automation')
        inbox.defer(self.run, 'Delete the branch', 'stale', 'git push origin --delete x')
        inbox.defer(self.run, 'Fail', 'a test', 'gh fail')

    def tearDown(self) -> None:
        super().tearDown()
        self._bin.cleanup()

    def mason(self, *args: str, stdin: str = '') -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, '-I', str(ROOT / 'bin' / 'artifact'), *args],
            capture_output=True, text=True, input=stdin, env=self.env, check=False,
        )  # fmt: skip

    def ids(self) -> list[str]:
        return [i.id for i in inbox.local_items(REPO, ['dependency-assessment'], dict(os.environ))]

    def test_ids_in_the_inbox(self) -> None:
        run_id = self.run.name
        self.assertEqual(self.ids(), [f'{run_id}#1', f'{run_id}#2', f'{run_id}#3'])
        items = inbox.local_items(REPO, ['dependency-assessment'], dict(os.environ))
        self.assertIn(f'[{run_id}#1]', items[0].line())

    def test_approve_runs_gh_and_clears_it(self) -> None:
        item = f'{self.run.name}#1'
        self.assertEqual(
            self.mason('approve', item, '--repo', REPO, stdin='n\n').stdout, 'Left in the inbox.\n'
        )
        result = self.mason('approve', item, '--repo', REPO, stdin='y\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('approved and done', result.stdout)
        self.assertEqual(self.record.read_text(), 'label create automation\n')
        self.assertNotIn(item, self.ids())
        self.assertIn(
            'already approved', self.mason('approve', item, '--repo', REPO, '--yes').stderr
        )

    def test_anything_but_gh_or_mason_is_for_a_person(self) -> None:
        item = f'{self.run.name}#2'
        result = self.mason('approve', item, '--repo', REPO, '--yes')
        self.assertIn('do this yourself', result.stderr)
        self.assertIn('git push origin --delete x', result.stderr)
        self.assertIn('marked done', result.stdout)
        self.assertFalse(self.record.exists())
        self.assertEqual(inbox._deferred(self.run / inbox.DEFERRED)[1]['decided'], 'done by hand')

    def test_a_failed_command_stays_in_the_inbox(self) -> None:
        item = f'{self.run.name}#3'
        result = self.mason('approve', item, '--repo', REPO, '--yes')
        self.assertEqual(result.returncode, 1)
        self.assertIn('status 4', result.stderr)
        self.assertIn(item, self.ids())

    def test_dismiss_and_bad_ids(self) -> None:
        item = f'{self.run.name}#2'
        self.assertIn(
            'dismissed',
            self.mason('dismiss', item, '--repo', REPO, '--reason', 'kept on purpose').stdout,
        )
        self.assertEqual(inbox._deferred(self.run / inbox.DEFERRED)[1]['note'], 'kept on purpose')
        self.assertIn(
            'an id is', self.mason('dismiss', 'nope', '--repo', REPO, '--reason', 'x').stderr
        )
        self.assertIn(
            'no such',
            self.mason('dismiss', f'{self.run.name}#9', '--repo', REPO, '--reason', 'x').stderr,
        )

    def test_runnable(self) -> None:
        self.assertEqual(
            inbox.runnable("gh issue close 4 --comment 'done, thanks'"),
            ['gh', 'issue', 'close', '4', '--comment', 'done, thanks'],
        )
        for command in ('rm -rf /', 'gh "unclosed', '', 'bash -c "gh x"'):
            with self.subTest(command=command):
                self.assertIsNone(inbox.runnable(command))


class StatusTests(unittest.TestCase):
    def test_written_beside_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / '20261125-090000-code-review.log'
            path = runner.write_status(log, 3, NOW)
            self.assertEqual(path.name, '20261125-090000-code-review.status.json')
            self.assertEqual(
                json.loads(path.read_text()),
                {'status': 3, 'log': str(log), 'finished_at': '2026-11-25 12:00 UTC'},
            )


if __name__ == '__main__':
    unittest.main()
