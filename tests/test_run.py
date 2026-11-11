import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from artifacts import run

from .fixtures import RUN, SHA_A, make

NOW = datetime(2026, 11, 11, 9, 30, tzinfo=UTC)


def alert(number: int, name: str, severity: str) -> dict:
    return {
        'number': number,
        'dependency': {'package': {'ecosystem': 'npm', 'name': name}},
        'security_advisory': {'severity': severity},
    }


class RunValidationTests(unittest.TestCase):
    def test_in_progress_record(self) -> None:
        self.assertEqual(run.validate(RUN), [])

    def test_published_records(self) -> None:
        self.assertEqual(run.validate(make(RUN, outcome='pr_opened')), [])
        stopped = make(RUN, outcome='stopped_pr_open', pr=40, alerts_not_covered=[12])
        self.assertEqual(run.validate(stopped), [])

    def test_stopped_needs_the_pr_and_what_it_missed(self) -> None:
        errors = run.validate(make(RUN, outcome='stopped_pr_open'))
        self.assertIn('$.pr: is required when outcome is stopped_pr_open', errors)
        self.assertIn('$.alerts_not_covered: is required when outcome is stopped_pr_open', errors)

    def test_pr_only_when_stopped(self) -> None:
        errors = run.validate(make(RUN, outcome='pr_opened', pr=40))
        self.assertIn('$.pr: is only for outcome stopped_pr_open', errors)

    def test_fields(self) -> None:
        errors = run.validate(make(RUN, id='today', trigger='cron', started_at='9:30'))
        self.assertTrue(any(e.startswith('$.id: must match') for e in errors))
        self.assertTrue(any(e.startswith('$.trigger: must be one of') for e in errors))
        self.assertTrue(any(e.startswith('$.started_at: must be an ISO 8601') for e in errors))


class StartTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.hub = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def start(self, env: dict[str, str], login: str | None = 'octocat') -> tuple[Path, dict]:
        return run.start(
            self.hub,
            Path('/work/example-app'),
            'dependency-upgrades',
            'example-org/example-app',
            SHA_A,
            env=env,
            now=NOW,
            login=login,
        )

    def test_interactive_run_is_started_by_the_github_user(self) -> None:
        directory, record = self.start({})
        self.assertEqual(record['trigger'], 'interactive')
        self.assertEqual(record['started_by'], 'octocat')
        self.assertEqual(record['id'], '20261111-093000-dependency-upgrades')
        self.assertEqual(directory, self.hub / 'runs' / 'example-app' / record['id'])
        self.assertTrue((directory / 'findings').is_dir())
        saved = json.loads((directory / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(saved, record)
        self.assertEqual(run.validate(saved), [])

    def test_unattended_run_names_its_trigger(self) -> None:
        env = {'AUTOMATION_UNATTENDED': '1', 'AUTOMATION_TRIGGER': 'systemd timer'}
        _, record = self.start(env)
        self.assertEqual((record['trigger'], record['started_by']), ('unattended', 'systemd timer'))

    def test_unattended_without_a_named_trigger(self) -> None:
        _, record = self.start({'AUTOMATION_UNATTENDED': '1'})
        self.assertEqual(record['started_by'], 'an unattended run')


class NotCoveredTests(unittest.TestCase):
    def test_alerts_the_open_pr_does_not_account_for(self) -> None:
        alerts = [alert(1, 'qs', 'high'), alert(2, 'vite', 'critical'), alert(3, 'url', 'low')]
        artifacts = [
            {
                'schema': 'change/1',
                'alerts': {'closed': [{'number': 1}], 'already_fixed': [], 'undecided': []},
            },
            {'schema': 'finding/1', 'source': {'refs': ['dependabot#3']}},
        ]
        self.assertEqual(
            run.not_covered(alerts, artifacts),
            [{'number': 2, 'package': 'vite', 'severity': 'critical'}],
        )

    def test_most_severe_first(self) -> None:
        alerts = [alert(5, 'a', 'low'), alert(6, 'b', 'critical'), alert(4, 'c', 'moderate')]
        self.assertEqual([a['number'] for a in run.not_covered(alerts, [])], [6, 4, 5])


if __name__ == '__main__':
    unittest.main()
