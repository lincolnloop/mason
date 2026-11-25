import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from artifacts import discussions, embed, render, report, validate

from . import ROOT
from .fixtures import RUN, make

REPORT_RUN = make(RUN, id='20261201-090000-dependency-report')
TARGETS = {'critical': 3, 'high': 7, 'medium': 30, 'low': 90}
NOW = datetime(2026, 12, 1, 9, tzinfo=UTC)


def alert(
    number: int,
    severity: str,
    state: str,
    created: str,
    *,
    ghsa: str | None = None,
    package: str | None = None,
    ecosystem: str = 'pip',
    manifest: str = 'requirements/base.txt',
    **dates: str,
) -> dict:
    return {
        'number': number,
        'state': state,
        'created_at': created,
        'dependency': {
            'package': {'ecosystem': ecosystem, 'name': package or f'pkg{number}'},
            'manifest_path': manifest,
        },
        'security_advisory': {'severity': severity, 'ghsa_id': ghsa or f'GHSA-{number}'},
        **dates,
    }


# November 2026: two alerts for one advisory fixed (base.txt and dev.txt), one fixed late, one
# "fixed" on package.json while its lockfile copy stays open, one dismissed, one open from before.
ALERTS = [
    alert(
        1,
        'high',
        'fixed',
        '2026-11-02T00:00:00Z',
        ghsa='GHSA-a',
        package='urllib3',
        fixed_at='2026-11-05T00:00:00Z',
    ),
    alert(
        2,
        'high',
        'fixed',
        '2026-11-02T00:00:00Z',
        ghsa='GHSA-a',
        package='urllib3',
        manifest='requirements/dev.txt',
        fixed_at='2026-11-05T00:00:00Z',
    ),
    alert(
        3,
        'critical',
        'fixed',
        '2026-10-01T00:00:00Z',
        ghsa='GHSA-b',
        package='pillow',
        fixed_at='2026-11-10T00:00:00Z',
    ),
    alert(
        4,
        'medium',
        'fixed',
        '2026-10-20T00:00:00Z',
        ghsa='GHSA-c',
        package='vite',
        ecosystem='npm',
        manifest='client/app/package.json',
        fixed_at='2026-11-03T00:00:00Z',
    ),
    alert(
        5,
        'medium',
        'open',
        '2026-11-03T00:00:00Z',
        ghsa='GHSA-c',
        package='vite',
        ecosystem='npm',
        manifest='client/app/package-lock.json',
    ),
    alert(6, 'low', 'dismissed', '2026-09-01T00:00:00Z', dismissed_at='2026-11-20T00:00:00Z'),
    alert(7, 'high', 'open', '2026-06-01T00:00:00Z', ghsa='GHSA-d', package='virtualenv'),
]


def build(month: int = 11, alerts: list[dict] | None = None, now: datetime = NOW) -> dict:
    return report.build(
        repo='example/project',
        run=REPORT_RUN,
        year=2026,
        month=month,
        now=now,
        fix_within_days=TARGETS,
        alerts=ALERTS if alerts is None else alerts,
    )


class PeriodTests(unittest.TestCase):
    def test_a_complete_month(self) -> None:
        since, until, complete = report.month_bounds(2026, 11, NOW)
        self.assertEqual((since.day, until.month, complete), (1, 12, True))

    def test_december_ends_in_the_next_year(self) -> None:
        _, until, _ = report.month_bounds(2025, 12, NOW)
        self.assertEqual((until.year, until.month), (2026, 1))

    def test_the_current_month_runs_to_now(self) -> None:
        _, until, complete = report.month_bounds(2026, 12, NOW)
        self.assertEqual((until, complete), (NOW, False))

    def test_a_future_month_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            report.month_bounds(2027, 1, NOW)

    def test_months_are_parsed_strictly(self) -> None:
        self.assertEqual(report.parse_month('2026-03'), (2026, 3))
        for bad in ('2026-13', '2026-3', 'March', ''):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                report.parse_month(bad)

    def test_the_default_is_the_last_complete_month(self) -> None:
        self.assertEqual(report.previous_month(NOW), (2026, 11))
        self.assertEqual(report.previous_month(datetime(2027, 1, 5, tzinfo=UTC)), (2026, 12))


class BuildTests(unittest.TestCase):
    def test_the_month(self) -> None:
        month = build()['month']
        self.assertEqual(month['fixed'], {'critical': 1, 'high': 2, 'medium': 0, 'low': 0})
        self.assertEqual(month['advisories']['fixed']['high'], 1)
        self.assertEqual(month['moved']['medium'], 1)
        self.assertEqual(month['dismissed']['low'], 1)
        # The critical took 40 days against a 3-day target; the highs took 3 against 7.
        self.assertEqual(
            month['fixed_within_target'], {'critical': 0, 'high': 2, 'medium': 0, 'low': 0}
        )
        self.assertEqual(month['open_at_end'], {'critical': 0, 'high': 1, 'medium': 1, 'low': 0})
        self.assertEqual(month['open_at_start'], {'critical': 1, 'high': 1, 'medium': 1, 'low': 1})

    def test_the_year_so_far_and_each_month(self) -> None:
        data = build()
        self.assertEqual(data['year_to_date']['since'], '2026-01-01T00:00:00Z')
        self.assertEqual(sum(data['year_to_date']['fixed'].values()), 3)
        self.assertEqual([row['month'] for row in data['months']][-2:], ['2026-10', '2026-11'])
        self.assertEqual(data['months'][-1]['advisories_fixed'], 2)
        self.assertEqual(data['months'][9]['critical_open_at_end'], 1)  # October

    def test_open_by_package_most_severe_then_oldest(self) -> None:
        rows = build()['open']
        self.assertEqual([r['package'] for r in rows], ['virtualenv', 'vite'])
        self.assertEqual(rows[1]['manifests'], ['client/app/package-lock.json'])
        self.assertEqual(rows[0]['oldest_days'], 183.0)

    def test_the_current_month_is_marked_incomplete(self) -> None:
        data = build(12)
        self.assertFalse(data['period']['complete'])
        self.assertEqual(data['period']['until'], '2026-12-01T09:00:00Z')

    def test_no_alerts(self) -> None:
        data = build(alerts=[])
        self.assertEqual(validate(data), [])
        self.assertEqual(data['open'], [])

    def test_the_result_validates(self) -> None:
        self.assertEqual(validate(build()), [])


MANIFESTS = {
    'production': ['requirements/base.txt'],
    'production_build': ['client/*/package*.json'],
    'development': ['requirements/dev.txt'],
    'not_deployed': [],
}


class GroupTests(unittest.TestCase):
    def test_configured_groups_by_manifest(self) -> None:
        groups = report.build(
            repo='example/project',
            run=REPORT_RUN,
            year=2026,
            month=11,
            now=NOW,
            fix_within_days=TARGETS,
            alerts=ALERTS,
            manifests=MANIFESTS,
        )['groups']
        self.assertEqual(groups['source'], 'configured')
        month = groups['month']
        # urllib3's one advisory was fixed in both base.txt and dev.txt: counted in each group.
        self.assertEqual(month['production']['advisories_fixed'], 2)  # urllib3, pillow
        self.assertEqual(month['development']['advisories_fixed'], 1)
        self.assertEqual(month['production_build']['open_at_end'], 1)  # the vite lockfile
        self.assertEqual(month['production']['open_at_end'], 1)  # virtualenv, on base.txt here
        self.assertEqual(month['unclassified']['fixed'], 0)

    def test_an_unmatched_manifest_is_unclassified(self) -> None:
        stray = [alert(1, 'high', 'open', '2026-11-02T00:00:00Z', manifest='tools/package.json')]
        data = report.build(
            repo='example/project',
            run=REPORT_RUN,
            year=2026,
            month=11,
            now=NOW,
            fix_within_days=TARGETS,
            alerts=stray,
            manifests=MANIFESTS,
        )
        self.assertEqual(data['groups']['month']['unclassified']['open_at_end'], 1)
        self.assertEqual(data['open'][0]['groups'], ['unclassified'])

    def test_without_configuration_dependabots_scope_is_used(self) -> None:
        runtime = alert(1, 'high', 'open', '2026-11-02T00:00:00Z')
        runtime['dependency']['scope'] = 'runtime'
        dev = alert(2, 'low', 'open', '2026-11-02T00:00:00Z', manifest='requirements/dev.txt')
        dev['dependency']['scope'] = 'development'
        unknown = alert(3, 'low', 'open', '2026-11-02T00:00:00Z')
        groups = build(alerts=[runtime, dev, unknown])['groups']
        self.assertEqual(groups['source'], 'dependabot')
        self.assertEqual(
            {g: c['open_at_end'] for g, c in groups['month'].items() if c['open_at_end']},
            {'production': 1, 'development': 1, 'unclassified': 1},
        )

    def test_the_headline_says_how_much_is_in_production(self) -> None:
        data = report.build(
            repo='example/project',
            run=REPORT_RUN,
            year=2026,
            month=11,
            now=NOW,
            fix_within_days=TARGETS,
            alerts=ALERTS,
            manifests=MANIFESTS,
        )
        body = render.dependency_report_body(data, REPORT_RUN)
        self.assertIn(
            "Of those, 1 is in the site's own code and 1 in build tools run on the production "
            'servers',
            body.splitlines()[0],
        )
        self.assertIn("| Production: the site's own code | 2 | 1 | 1 | 0 | 2 |", body)
        self.assertIn('The project lists which of its files are deployed', body)


class ValidateTests(unittest.TestCase):
    def test_broken_fields_are_named(self) -> None:
        data = build()
        data['period']['month'] = '2026-11-01'
        data['month']['fixed']['high'] = -1
        data['open'][0]['oldest_days'] = 'old'
        del data['months'][0]['fixed']
        errors = validate(data)
        for path in (
            '$.period.month',
            '$.month.fixed.high',
            '$.open[0].oldest_days',
            '$.months[0].fixed',
        ):
            self.assertTrue(any(e.startswith(path) for e in errors), (path, errors))

    def test_not_an_object(self) -> None:
        self.assertTrue(report.validate([]))


class RenderTests(unittest.TestCase):
    def test_title(self) -> None:
        self.assertEqual(
            render.dependency_report_title(build()), 'Dependency report: November 2026'
        )

    def test_body_headline_notes_and_data(self) -> None:
        body = render.dependency_report_body(build(), REPORT_RUN, 'Vite 5 goes with the new build.')
        first = body.splitlines()[0]
        self.assertIn('2 vulnerabilities fixed (3 alerts), 1 of them critical', first)
        self.assertIn('0 critical vulnerabilities open', first)
        self.assertIn('2 of 3 fixes met their target time', first)
        self.assertIn(
            'Not counted as fixes: 1 alert closed on one file while still open on another; 1 dismissed.',
            body,
        )
        self.assertIn('Vite 5 goes with the new build.', body)
        self.assertIn('| `virtualenv` (pip) | High | 1 | 1 | 183 days | Not classified |', body)
        (data,) = embed.extract(body, report.SCHEMA)
        self.assertEqual(data, build())

    def test_an_incomplete_month_says_so(self) -> None:
        body = render.dependency_report_body(build(12), REPORT_RUN)
        self.assertTrue(body.startswith('**December 2026 (to 1 December 2026):'))

    def test_notes_cannot_carry_an_artifact(self) -> None:
        with self.assertRaises(ValueError):
            render.dependency_report_body(build(), REPORT_RUN, embed.embed(REPORT_RUN))
        with self.assertRaises(ValueError):
            render.dependency_report_body(build(), REPORT_RUN, 'x' * (render.NOTES_LIMIT + 1))


class FakeGh:
    """Answers `gh api graphql` by query, and records what was sent."""

    def __init__(self, categories: list[str], found: list[dict], enabled: bool = True) -> None:
        self.categories, self.found, self.enabled = categories, found, enabled
        self.created: dict[str, str] | None = None

    def __call__(self, *args: str) -> str:
        fields = dict(arg.split('=', 1) for arg in args if '=' in arg)
        query = fields['query']
        if 'createDiscussion' in query:
            self.created = fields
            return json.dumps(
                {'data': {'createDiscussion': {'discussion': {'url': 'https://x/d/9'}}}}
            )
        if 'search(' in query:
            return json.dumps({'data': {'search': {'nodes': self.found}}})
        nodes = [{'id': f'C{i}', 'name': name} for i, name in enumerate(self.categories)]
        return json.dumps(
            {
                'data': {
                    'repository': {
                        'id': 'R1',
                        'hasDiscussionsEnabled': self.enabled,
                        'discussionCategories': {'nodes': nodes},
                    }
                }
            }
        )


class DiscussionTests(unittest.TestCase):
    def test_posts_into_the_named_category(self) -> None:
        gh = FakeGh(['Notes', 'Dependency reports'], [])
        url = discussions.post(gh, 'o/r', 'dependency reports', 'Dependency report: X', 'body')
        self.assertEqual(url, 'https://x/d/9')
        self.assertEqual(gh.created['category'], 'C1')
        self.assertEqual(gh.created['repository'], 'R1')

    def test_a_title_already_in_the_category_is_refused(self) -> None:
        found = [{'title': 'T', 'url': 'https://x/d/1', 'category': {'name': 'Dependency reports'}}]
        gh = FakeGh(['Dependency reports'], found)
        with self.assertRaisesRegex(discussions.DiscussionError, 'https://x/d/1'):
            discussions.post(gh, 'o/r', 'Dependency reports', 'T', 'body')
        self.assertIsNone(gh.created)

    def test_the_same_title_in_another_category_is_not_a_clash(self) -> None:
        found = [{'title': 'T', 'url': 'https://x/d/1', 'category': {'name': 'Notes'}}, None]
        gh = FakeGh(['Dependency reports'], found)
        discussions.post(gh, 'o/r', 'Dependency reports', 'T', 'body')
        self.assertIsNotNone(gh.created)

    def test_a_missing_category_lists_the_ones_there_are(self) -> None:
        with self.assertRaisesRegex(discussions.DiscussionError, 'it has: Notes'):
            discussions.post(FakeGh(['Notes'], []), 'o/r', 'Dependency reports', 'T', 'b')

    def test_discussions_turned_off(self) -> None:
        with self.assertRaisesRegex(discussions.DiscussionError, 'turned off'):
            discussions.post(FakeGh([], [], enabled=False), 'o/r', 'X', 'T', 'b')

    def test_graphql_errors_and_bad_output(self) -> None:
        with self.assertRaisesRegex(discussions.DiscussionError, 'refused'):
            discussions.graphql(lambda *a: json.dumps({'errors': [{'message': 'nope'}]}), 'q')
        with self.assertRaisesRegex(discussions.DiscussionError, 'JSON'):
            discussions.graphql(lambda *a: 'not json', 'q')
        with self.assertRaisesRegex(discussions.DiscussionError, 'owner/name'):
            discussions.category(lambda *a: '{}', 'nobody', 'X')


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        (self.tmp / 'mason.toml').write_text(
            "version = 1\n\n[repo]\nname = 'example/project'\nmain_branch = 'main'\n",
            encoding='utf-8',
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def artifact(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, '-I', str(ROOT / 'bin' / 'artifact'), *args],
            capture_output=True,
            text=True,
            cwd=self.tmp,
            check=False,
        )

    def write(self, name: str, content: object) -> str:
        path = self.tmp / name
        path.write_text(json.dumps(content), encoding='utf-8')
        return str(path)

    def test_collect_and_render_from_saved_alerts(self) -> None:
        run = self.write('run.json', REPORT_RUN)
        alerts = self.write('alerts.json', ALERTS)
        out = self.tmp / 'report.json'
        result = self.artifact(
            'collect-report',
            str(self.tmp),
            '--run',
            run,
            '--month',
            '2026-11',
            '--alerts',
            alerts,
            '-o',
            str(out),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(out.read_text())['month']['fixed']['critical'], 1)
        title = self.artifact('render-report', str(out), '--run', run, '--title')
        self.assertEqual(title.stdout, 'Dependency report: November 2026\n')
        body = self.artifact('render-report', str(out), '--run', run)
        self.assertEqual(body.returncode, 0, body.stderr)
        self.assertIn("## What's still open", body.stdout)

    def test_bad_month_and_bad_alerts_are_refused(self) -> None:
        run = self.write('run.json', REPORT_RUN)
        alerts = self.write('alerts.json', ALERTS)
        result = self.artifact(
            'collect-report', str(self.tmp), '--run', run, '--month', '2026-1', '--alerts', alerts
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn('--month', result.stderr)
        broken = self.write('broken.json', [{'number': 1}])
        result = self.artifact(
            'collect-report', str(self.tmp), '--run', run, '--month', '2026-11', '--alerts', broken
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn('shape Dependabot returns', result.stderr)

    def test_a_report_from_another_run_is_refused(self) -> None:
        other = self.write('other.json', make(RUN, id='20261201-100000-dependency-report'))
        data = self.write('report.json', build())
        result = self.artifact('render-report', data, '--run', other)
        self.assertEqual(result.returncode, 1)
        self.assertIn('not', result.stderr)


if __name__ == '__main__':
    unittest.main()
