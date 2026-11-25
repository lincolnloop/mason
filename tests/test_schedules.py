import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from artifacts import inbox, ledger, paths, project, results, run, rundue, runner, schedules, skills

from . import ROOT
from .fixtures import RUN, make

REPO = 'o/app'


class ParseTests(unittest.TestCase):
    def test_schedules(self) -> None:
        for text, expected in (
            ('off', 'off'),
            ('hourly', 'hourly :00'),
            ('hourly :15', 'hourly :15'),
            ('daily', 'daily 00:00'),
            ('Daily 6:30', 'daily 06:30'),
            ('weekly', 'weekly mon 00:00'),
            ('weekly fri 17:00', 'weekly fri 17:00'),
            ('monthly day 2 06:00', 'monthly day 2 06:00'),
            ('quarterly', 'quarterly day 1 00:00'),
            ('yearly day 15', 'yearly day 15 00:00'),
        ):
            with self.subTest(text=text):
                self.assertEqual(str(schedules.parse(text)), expected)

    def test_malformed(self) -> None:
        for text, message in (
            ('', 'empty'),
            ('fortnightly', 'starts with off'),
            ('monthly day 31', '1 to 28'),
            ('weekly someday', 'not understood'),
            ('daily 25:00', 'not a time'),
            ('hourly 10:00', 'not understood'),
            ('off now', 'starts with off'),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(schedules.ScheduleError, message):
                schedules.parse(text)
        self.assertEqual(schedules.duration('12h'), timedelta(hours=12))
        with self.assertRaises(schedules.ScheduleError):
            schedules.duration('2w')


class PeriodTests(unittest.TestCase):
    def test_keys_round_trip(self) -> None:
        moment = datetime(2026, 11, 25, 9, 40)
        for interval, key in (
            ('hourly', '2026-11-25T09'),
            ('daily', '2026-11-25'),
            ('weekly', '2026-W48'),
            ('monthly', '2026-11'),
            ('quarterly', '2026-Q4'),
            ('yearly', '2026'),
        ):
            with self.subTest(interval=interval):
                period = schedules.containing(moment, interval)
                self.assertEqual(period.key, key)
                self.assertEqual(schedules.from_key(key), period)
                self.assertLess(period.start, period.end)
                self.assertEqual(period.next().previous(), period)
        self.assertEqual(schedules.from_key('2026-12').next().key, '2027-01')
        for key in ('2026-13', '2026-W60', 'November'):
            with self.subTest(key=key), self.assertRaises(schedules.ScheduleError):
                schedules.from_key(key)

    def test_weeks_belong_to_the_month_of_their_thursday(self) -> None:
        # 2026-W44 runs Monday 26 October to Sunday 1 November: its Thursday is in October.
        self.assertEqual(
            schedules.within('2026-11', 'weekly'), ['2026-W45', '2026-W46', '2026-W47', '2026-W48']
        )
        self.assertIn('2026-W44', schedules.within('2026-10', 'weekly'))
        self.assertEqual(schedules.within('2026-Q4', 'monthly'), ['2026-10', '2026-11', '2026-12'])
        self.assertEqual(len(schedules.within('2026-11-25', 'hourly')), 24)


class SlotTests(unittest.TestCase):
    def test_a_report_is_due_after_its_period(self) -> None:
        monthly = schedules.parse('monthly day 2 06:00')
        found = schedules.slots(monthly, 'report', datetime(2026, 10, 1), datetime(2026, 12, 1))
        self.assertEqual(
            [(s.key, s.due) for s in found],
            [('2026-09', datetime(2026, 10, 2, 6)), ('2026-10', datetime(2026, 11, 2, 6))],
        )
        self.assertEqual(
            schedules.current_key('monthly', 'report', datetime(2026, 11, 1)), '2026-10'
        )

    def test_a_check_is_due_in_its_period(self) -> None:
        weekly = schedules.parse('weekly wed 07:00')
        upcoming = schedules.next_slot(weekly, 'check', datetime(2026, 11, 25, 8))
        self.assertEqual((upcoming.key, upcoming.due), ('2026-W49', datetime(2026, 12, 2, 7)))
        self.assertEqual(
            schedules.current_key('weekly', 'check', datetime(2026, 11, 25)), '2026-W48'
        )
        self.assertIsNone(schedules.next_slot(schedules.parse('off'), 'check', datetime.now()))
        self.assertEqual(
            schedules.slots(schedules.parse('off'), 'check', datetime(2026, 1, 1), datetime.now()),
            [],
        )

    def test_a_short_month(self) -> None:
        monthly = schedules.parse('monthly day 28')
        self.assertEqual(
            schedules.anchor(monthly, schedules.from_key('2027-02')), datetime(2027, 2, 28)
        )


class SkillScheduleTests(unittest.TestCase):
    def test_declared_by_the_skills(self) -> None:
        found = skills.workflows()
        self.assertEqual(
            (found['dependency-report'].schedule, found['dependency-report'].kind),
            ('monthly day 2 06:00', 'report'),
        )
        self.assertEqual(found['dependency-report'].catch_up, 'all')
        self.assertEqual(
            (found['dependency-assessment'].kind, found['dependency-assessment'].catch_up),
            ('check', 'latest'),
        )
        self.assertEqual(found['code-review'].schedule, 'off')


class Clean(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.spec = skills.workflows()['dependency-report']

    def tearDown(self) -> None:
        self._tmp.cleanup()
        for home in (paths.config_home(), paths.state_home(), paths.data_home()):
            shutil.rmtree(home, ignore_errors=True)

    def settings(self, text: str = '') -> Path:
        path = paths.settings(REPO)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'version = 1\n{text}')
        return path

    def record(
        self, period: str, outcome: str | None = None, stamp: str = '20261102-060000'
    ) -> Path:
        run_id = f'{stamp}-dependency-report'
        directory = paths.runs(REPO) / run_id
        directory.mkdir(parents=True)
        record = make(RUN, id=run_id, project=REPO, period=period)
        record['produced_by'] = {**record['produced_by'], 'skill': 'dependency-report'}
        (directory / 'run.json').write_text(json.dumps(record))
        if outcome:
            run.end(directory, outcome)
        return directory


class ScheduleSettingTests(Clean):
    def test_the_skill_default_or_the_project(self) -> None:
        self.assertEqual(project.planned(REPO, 'dependency-report', self.spec).source, 'skill')
        self.settings("[schedule]\ndependency-report = 'weekly mon'\n")
        planned = project.planned(REPO, 'dependency-report', self.spec)
        self.assertEqual(
            (str(planned.schedule), planned.source, planned.since),
            ('weekly mon 00:00', 'settings', None),
        )

    def test_set_keeps_the_rest_of_the_file(self) -> None:
        path = self.settings(
            "# mine\n[schedule]\nsentry-errors = 'daily'\n\n[limits]\nopen_agent_prs = 2\n"
        )
        project.set_schedule(REPO, 'dependency-report', 'weekly fri 09:00', date(2026, 11, 25))
        project.set_schedule(REPO, 'sentry-errors', 'off', date(2026, 11, 26))
        text = path.read_text()
        self.assertIn('# mine', text)
        self.assertIn(
            "dependency-report = { every = 'weekly fri 09:00', since = 2026-11-25 }", text
        )
        self.assertIn("sentry-errors = { every = 'off', since = 2026-11-26 }", text)
        self.assertIn('open_agent_prs = 2', text)
        planned = project.planned(REPO, 'dependency-report', self.spec)
        self.assertEqual(planned.since, date(2026, 11, 25))
        with self.assertRaises(schedules.ScheduleError):
            project.set_schedule(REPO, 'dependency-report', 'sometimes', date(2026, 11, 25))

    def test_set_creates_the_file(self) -> None:
        path = project.set_schedule(REPO, 'dependency-report', 'monthly', date(2026, 11, 25))
        self.assertTrue(path.read_text().startswith('version = 1\n\n[schedule]\n'))

    def test_checked(self) -> None:
        self.assertEqual(
            project.check(
                {'schedule': {'x': 'daily', 'y': {'every': 'weekly', 'since': date(2026, 1, 1)}}}
            ),
            [],
        )
        errors = project.check(
            {
                'schedule': {
                    'x': 'sometimes',
                    'y': {'since': date(2026, 1, 1)},
                    'z': {'every': 'daily', 'since': 'soon'},
                }
            }
        )
        self.assertEqual(len(errors), 3)


class LedgerTests(Clean):
    def test_each_state(self) -> None:
        self.settings(
            "[project]\nadded = 2025-01-01\n[schedule]\ndependency-report = 'monthly day 2 06:00'\n"
        )
        self.record('2026-07', 'done', '20260802-060000')
        self.record('2026-08', 'failed', '20260902-060000')
        ledger.skip(
            REPO, 'dependency-report', '2026-09', 'nothing to report: the project was paused'
        )
        # 2026-10's run saved a result but never recorded its end: the result counts.
        saved = self.record('2026-10', stamp='20261102-060000')
        meta = paths.results(REPO) / 'dependency-report' / '2026-10' / 'meta.json'
        meta.parent.mkdir(parents=True)
        meta.write_text(json.dumps({'run': saved.name}))
        now = datetime(2026, 12, 2, 12)
        _, found, following = ledger.history(REPO, 'dependency-report', self.spec, now, count=6)
        self.assertEqual(
            [(s.key, s.state) for s in found],
            [
                ('2026-06', 'overdue'),
                ('2026-07', 'done'),
                ('2026-08', 'failed'),
                ('2026-09', 'skipped'),
                ('2026-10', 'done'),
                ('2026-11', 'due'),
            ],
        )
        self.assertEqual((following.key, following.due), ('2026-12', datetime(2027, 1, 2, 6)))

    def test_a_changed_schedule_starts_from_its_date(self) -> None:
        self.settings(
            "[schedule]\ndependency-report = { every = 'weekly mon', since = 2026-11-20 }\n"
        )
        _, found, _ = ledger.history(
            REPO, 'dependency-report', self.spec, datetime(2026, 12, 2), count=10
        )
        self.assertEqual([s.key for s in found], ['2026-W47', '2026-W48'])

    def test_without_a_start_only_the_latest_period_counts(self) -> None:
        self.settings("[schedule]\ndependency-report = 'monthly day 2 06:00'\n")
        _, found, _ = ledger.history(
            REPO, 'dependency-report', self.spec, datetime(2026, 12, 2, 12)
        )
        self.assertEqual([(s.key, s.state) for s in found], [('2026-11', 'due')])

    def test_off(self) -> None:
        self.settings("[schedule]\ndependency-report = 'off'\n")
        self.assertEqual(
            ledger.history(REPO, 'dependency-report', self.spec, datetime.now())[1:], ([], None)
        )

    def test_an_unattended_failure_counts(self) -> None:
        logs = paths.logs(REPO)
        logs.mkdir(parents=True)
        runner.write_status(logs / '20261102-060000-dependency-report.log', 1, period='2026-10')
        self.assertEqual(
            ledger.facts(REPO, 'dependency-report').failed,
            {'2026-10': str(logs / '20261102-060000-dependency-report.log')},
        )

    def test_skip_refusals(self) -> None:
        with self.assertRaisesRegex(ledger.LedgerError, 'not a period key'):
            ledger.skip(REPO, 'dependency-report', 'October', 'x')
        with self.assertRaisesRegex(ledger.LedgerError, 'reason is empty'):
            ledger.skip(REPO, 'dependency-report', '2026-10', ' ')

    def test_run_end(self) -> None:
        directory = self.record('2026-10')
        self.assertEqual(run.end(directory, 'nothing_to_do')['outcome'], 'nothing_to_do')
        with self.assertRaisesRegex(ValueError, 'is one of'):
            run.end(directory, 'pr_opened')
        with self.assertRaisesRegex(ValueError, 'no readable run record'):
            run.end(self.tmp, 'done')


class RunDueTests(Clean):
    def setUp(self) -> None:
        super().setUp()
        self.settings(
            '[project]\nadded = 2026-11-20\n'
            "[workflows]\nenabled = ['dependency-report', 'dependency-assessment', 'code-review']\n"
            "[schedule]\ndependency-report = 'monthly day 2'\ndependency-assessment = 'daily'\n"
            "code-review = 'daily'\n"
        )
        self.now = datetime(2026, 11, 25, 12)

    def test_catch_up(self) -> None:
        todo, notes = rundue.plan([REPO], now=self.now)
        planned = {(t.workflow, t.period): t.superseded for t in todo}
        # A check runs only its latest open period, superseding the rest; a report runs each.
        self.assertEqual(
            planned[('dependency-assessment', '2026-11-25')],
            ['2026-11-20', '2026-11-21', '2026-11-22', '2026-11-23', '2026-11-24'],
        )
        self.assertNotIn(('dependency-report', '2026-10'), planned)  # due 2 Nov, before added
        self.assertIn('acts on one PR', ' '.join(notes))  # code-review needs --pr

    def test_superseded_periods_are_skipped_when_the_latest_succeeds(self) -> None:
        todo, _ = rundue.plan([REPO], now=self.now)
        lines = rundue.run_all(todo, lambda item: 0)
        self.assertIn('o/app dependency-assessment 2026-11-25: done', lines)
        reasons = {s['period']: s['reason'] for s in ledger.read_skips(REPO)}
        self.assertEqual(reasons['2026-11-22'], 'superseded by the run for 2026-11-25')
        self.assertTrue(rundue.run_all(todo[:1], lambda item: 3)[0].endswith(': failed (status 3)'))

    def test_retries_then_leaves_it_for_a_person(self) -> None:
        self.settings(
            "[project]\nadded = 2026-11-25\n[workflows]\nenabled = ['dependency-report']\n"
            "[schedule]\ndependency-report = 'daily'\n"
        )
        for i in range(rundue.RETRIES + 1):
            self.record('2026-11-24', 'failed', f'2026112{5}-0{i}0000')
        todo, notes = rundue.plan([REPO], now=self.now)
        self.assertEqual(todo, [])
        self.assertIn('failed 3 times; left for a person', notes[0])

    def test_a_run_in_progress_is_left_alone(self) -> None:
        (paths.project_state(REPO) / 'dependency-assessment.lock').mkdir(parents=True)
        todo, notes = rundue.plan([REPO], now=self.now)
        self.assertNotIn('dependency-assessment', [t.workflow for t in todo])
        self.assertIn('o/app dependency-assessment: a run is in progress', notes)
        self.assertIn('not run: o/app dependency-assessment', rundue.describe(todo, notes))

    def test_the_command(self) -> None:
        result = subprocess.run(
            [sys.executable, '-I', str(ROOT / 'bin' / 'artifact'), 'run-due', '--dry-run'],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('o/app dependency-assessment', result.stdout)


class ConsolidationTests(Clean):
    def setUp(self) -> None:
        super().setUp()
        self.team = self.tmp / 'team'
        weekly = self.team / 'weekly-report' / 'SKILL.md'
        weekly.parent.mkdir(parents=True)
        weekly.write_text(
            "---\nname: weekly-report\nmetadata:\n  mason-requires: '>=0.4'\n"
            "  mason-task: 'Write the weekly report'\n  mason-schedule: 'weekly mon'\n"
            "  mason-kind: 'report'\n---\n"
        )
        monthly = self.team / 'monthly-report' / 'SKILL.md'
        monthly.parent.mkdir(parents=True)
        monthly.write_text(
            "---\nname: monthly-report\nmetadata:\n  mason-requires: '>=0.4'\n"
            "  mason-task: 'Write the monthly report'\n  mason-schedule: 'monthly day 3'\n"
            "  mason-kind: 'report'\n  mason-consumes: 'weekly-report'\n---\n"
        )
        self.settings(
            f"[project]\nadded = 2026-10-01\n[skills]\nweekly-report = '{weekly.parent}'\n"
            f"monthly-report = '{monthly.parent}'\n"
        )
        self.spec = skills.resolve('monthly-report', project.skill_paths(REPO)).workflow

    def weekly_done(self, key: str) -> None:
        directory = paths.runs(REPO) / f'20261201-000000-weekly-report-{key.lower()}'
        directory.mkdir(parents=True)
        record = make(
            RUN, id='20261201-000000-weekly-report', project=REPO, period=key, outcome='done'
        )
        record['produced_by'] = {**record['produced_by'], 'skill': 'weekly-report'}
        (directory.parent / f'20261201-00000{key[-1]}-weekly-report').mkdir()
        (directory.parent / f'20261201-00000{key[-1]}-weekly-report' / 'run.json').write_text(
            json.dumps(record)
        )

    def test_blocked_until_its_weeks_are_settled(self) -> None:
        now = datetime(2026, 12, 3, 12)
        _, found, _ = ledger.history(REPO, 'monthly-report', self.spec, now, count=1)
        self.assertEqual(found[0].state, 'blocked')
        self.assertIn(
            'waiting for weekly-report 2026-W45, 2026-W46, 2026-W47, 2026-W48', found[0].detail
        )
        for key in ('2026-W45', '2026-W46', '2026-W47'):
            self.weekly_done(key)
        ledger.skip(REPO, 'weekly-report', '2026-W48', 'a holiday week')
        _, found, _ = ledger.history(REPO, 'monthly-report', self.spec, now, count=1)
        self.assertEqual(found[0].state, 'due')
        # Earlier months still wait for their weeks, and the inbox says so.
        items = inbox.schedule_items(REPO, dict(os.environ))
        self.assertTrue(
            [i for i in items if i.kind == 'blocked' and 'monthly-report 2026-10' in i.text]
        )

    def test_results_within_a_period(self) -> None:
        for key in ('2026-W44', '2026-W45', '2026-W49', 'notes'):
            meta = paths.results(REPO) / 'weekly-report' / key / 'meta.json'
            meta.parent.mkdir(parents=True)
            meta.write_text(json.dumps({'title': key, 'body': 'b.md', 'saved_at': 'x'}))
        found = results.listing(REPO, 'weekly-report', within='2026-11')
        self.assertEqual([r.key for r in found], ['2026-W45'])
        with self.assertRaises(schedules.ScheduleError):
            results.listing(REPO, within='November')


class HeartbeatTests(unittest.TestCase):
    def test_units_and_cron(self) -> None:
        units = rundue.systemd_units('/bin/mason', 10, '/home/me/.config/mason/env')
        self.assertIn(
            "ExecStart=/bin/mason run-due --trigger 'systemd timer mason-run-due.timer'",
            units['mason-run-due.service'],
        )
        self.assertIn('EnvironmentFile=-/home/me/.config/mason/env', units['mason-run-due.service'])
        self.assertIn('OnUnitActiveSec=10min', units['mason-run-due.timer'])
        self.assertEqual(
            rundue.cron_line('/bin/mason', 15, '/l'),
            '*/15 * * * * /bin/mason run-due --trigger cron >> /l 2>&1',
        )

    def test_the_command_writes_only_with_systemd(self) -> None:
        def mason(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, '-I', str(ROOT / 'bin' / 'artifact'), 'schedule', 'install', *args],
                capture_output=True, text=True, check=False,
            )  # fmt: skip

        self.assertIn('crontab', mason().stdout)
        self.assertIn('run-due --trigger cron', mason('--cron').stdout)
        result = mason('--systemd', '--every', '5')
        self.assertIn('systemctl --user', result.stdout)
        unit = paths.config_home().parent / 'systemd' / 'user' / 'mason-run-due.timer'
        self.assertIn('OnUnitActiveSec=5min', unit.read_text())
        shutil.rmtree(paths.config_home().parent / 'systemd')
        self.assertIn('between 1 and 60', mason('--every', '0').stderr)


class CliTests(Clean):
    def mason(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, '-I', str(ROOT / 'bin' / 'artifact'), *args],
            capture_output=True, text=True, check=False,
        )  # fmt: skip

    def test_status_due_overdue_history_skip_schedule(self) -> None:
        self.settings("[workflows]\nenabled = ['dependency-report', 'code-review']\n")
        result = self.mason('schedule', 'set', REPO, 'dependency-report', 'daily 00:00')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.mason('status')
        self.assertIn('dependency-report', result.stdout)
        self.assertIn('daily 00:00', result.stdout)
        self.assertIn('(set for this project)', result.stdout)
        self.assertIn('not scheduled', result.stdout)  # code-review is off
        today = datetime.now()
        yesterday = (today - timedelta(days=1)).strftime('%Y-%m-%d')
        result = self.mason('due')
        self.assertIn(yesterday, result.stdout)
        result = self.mason('overdue')
        self.assertEqual(result.returncode, 0)  # within its grace: due, not overdue
        result = self.mason('skip', REPO, 'dependency-report', yesterday, '--reason', 'a holiday')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mason('due').stdout, 'Nothing is due.\n')
        self.assertIn('skipped', self.mason('history', REPO, 'dependency-report').stdout)
        self.assertIn('upcoming', self.mason('due', '--within', '2d').stdout)
        self.assertIn('is not added', self.mason('status', '--repo', 'o/other').stderr)

    def test_overdue_exits_1(self) -> None:
        self.settings(
            "[project]\nadded = 2025-01-01\n[workflows]\nenabled = ['dependency-report']\n"
            "[schedule]\ndependency-report = 'daily'\n"
        )
        self.record((datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d'), 'failed')
        result = self.mason('overdue')
        self.assertEqual(result.returncode, 1)
        self.assertIn('failed', result.stdout)

    def test_run_start_and_run_end(self) -> None:
        checkout = self.tmp / 'app'
        subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
        git = ['git', '-C', str(checkout)]
        subprocess.run([*git, 'remote', 'add', 'origin', 'git@github.com:o/app.git'], check=True)
        subprocess.run(
            [*git, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main'],
            check=True,
        )
        result = self.mason('run-start', str(checkout), 'dependency-report', '--period', '2026-10')
        self.assertEqual(result.returncode, 0, result.stderr)
        directory = json.loads(result.stdout)['dir']
        self.assertEqual(
            json.loads((Path(directory) / 'run.json').read_text())['period'], '2026-10'
        )
        result = self.mason('run-end', directory, '--outcome', 'done')
        self.assertTrue(result.stdout.endswith('for 2026-10: done\n'))
        time.sleep(1.1)  # a run's id is its start, to the second
        result = self.mason('run-start', str(checkout), 'dependency-report')
        period = json.loads((Path(json.loads(result.stdout)['dir']) / 'run.json').read_text())[
            'period'
        ]
        self.assertEqual(period, schedules.current_key('monthly', 'report', datetime.now()))
        result = self.mason('run-start', str(checkout), 'dependency-report', '--period', 'soon')
        self.assertIn('not a period key', result.stderr)


class RetrospectivePeriodTests(unittest.TestCase):
    """The retrospectives' collectors cover the run's calendar period."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.run_file = Path(self._tmp.name) / 'run.json'

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def period(self, record: dict, since: str | None = None, period: str | None = None):
        from argparse import Namespace

        from artifacts.cli import _period

        self.run_file.write_text(json.dumps(record))
        return _period(Namespace(run=self.run_file, since=since, period=period), 30)

    def test_the_runs_own_period(self) -> None:
        since, until = self.period({'period': '2026-10'})
        self.assertEqual(since, datetime(2026, 10, 1).astimezone().astimezone(UTC))
        self.assertEqual(until, datetime(2026, 11, 1).astimezone().astimezone(UTC))

    def test_period_and_since_override_it(self) -> None:
        since, _ = self.period({'period': '2026-10'}, period='2026-09')
        self.assertEqual(since, datetime(2026, 9, 1).astimezone().astimezone(UTC))
        since, until = self.period({'period': '2026-10'}, since='2026-10-15')
        self.assertEqual(since, datetime(2026, 10, 15, tzinfo=UTC))
        self.assertGreater(until, datetime(2026, 11, 1, tzinfo=UTC))

    def test_no_period_is_the_last_period_days(self) -> None:
        since, until = self.period({})
        self.assertEqual((until - since).days, 30)

    def test_a_current_period_ends_now_and_a_future_one_is_refused(self) -> None:
        from artifacts.cli import Refused

        today = datetime.now()
        _, until = self.period({'period': f'{today:%Y-%m}'})
        self.assertLessEqual(until, datetime.now(UTC))
        with self.assertRaisesRegex(Refused, 'has not started'):
            self.period({'period': f'{today.year + 1}'})
        with self.assertRaisesRegex(Refused, 'not a period key'):
            self.period({'period': 'soon'})


if __name__ == '__main__':
    unittest.main()
