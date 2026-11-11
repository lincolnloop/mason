import unittest
from datetime import UTC, datetime

from artifacts import collect, embed, outcomes, render

from .fixtures import CHANGE, FINDING, RUN, make

SINCE = datetime(2026, 10, 1, tzinfo=UTC)
UNTIL = datetime(2026, 11, 1, tzinfo=UTC)
POLICY = {
    'fix_within_days': {'critical': 3, 'high': 7, 'medium': 30, 'low': 90},
    'stale_after_months': 24,
    'stale_pr_after_days': 14,
}
RETRO_RUN = make(RUN, id='20261101-000000-dependency-retrospective')


def alert(number: int, severity: str, state: str, created: str, **dates: str) -> dict:
    return {
        'number': number,
        'state': state,
        'created_at': created,
        'dependency': {'package': {'ecosystem': 'pip', 'name': f'pkg{number}'}},
        'security_advisory': {'severity': severity},
        **dates,
    }


def raw_pr(number: int, state: str, **fields: object) -> dict:
    change = make(
        CHANGE,
        alerts={
            'open_before': {'critical': 0, 'high': 1, 'medium': 0, 'low': 0},
            'closed': [{'number': 7, 'package': 'pillow', 'severity': 'high'}],
            'already_fixed': [],
            'undecided': [],
        },
    )
    body = '\n'.join(
        [embed.embed(change), embed.embed(FINDING), embed.embed({**RUN, 'outcome': 'pr_opened'})]
    )
    pr = {
        'number': number,
        'title': 'Security upgrades',
        'url': f'https://github.com/example-org/example-app/pull/{number}',
        'state': state,
        'isDraft': False,
        'createdAt': '2026-10-10T09:00:00Z',
        'mergedAt': '2026-10-12T09:00:00Z' if state == 'MERGED' else None,
        'closedAt': '2026-10-12T09:00:00Z' if state in ('MERGED', 'CLOSED') else None,
        'headRefName': 'dependency-upgrades-20261010-090000',
        'headRefOid': 'd' * 40,
        'body': body,
        'comments': [],
        'statusCheckRollup': [
            {'name': 'tests', 'conclusion': 'SUCCESS'},
            {'context': 'ci/circleci: lint', 'state': 'SUCCESS'},
        ],
    }
    return {**pr, **fields}


def build(**changes: object) -> dict:
    inputs = {
        'repo': 'example-org/example-app',
        'run': RETRO_RUN,
        'since': SINCE,
        'until': UNTIL,
        'policy': POLICY,
        'raw_prs': [],
        'alerts': [],
        'reverts': [],
        'packages': [],
    }
    return outcomes.build(**{**inputs, **changes})


class PrTests(unittest.TestCase):
    def test_merged_pr(self) -> None:
        (pr,) = build(raw_prs=[raw_pr(40, 'MERGED')])['prs']
        self.assertEqual((pr['state'], pr['edited'], pr['days_open']), ('merged', False, 2.0))
        self.assertEqual(pr['run']['started_by'], 'systemd timer')
        self.assertEqual(pr['alerts_closed'], [7])
        self.assertEqual(
            pr['ci'], {'passed': ['ci/circleci: lint', 'tests'], 'failed': [], 'pending': []}
        )
        self.assertEqual(pr['findings'], {'in_pr': ['dependabot-2026-11-pillow'], 'left_out': []})

    def test_edited_before_merge(self) -> None:
        (pr,) = build(raw_prs=[raw_pr(40, 'MERGED', headRefOid='e' * 40)])['prs']
        self.assertTrue(pr['edited'])

    def test_failed_ci(self) -> None:
        checks = [
            {'name': 'tests', 'conclusion': 'FAILURE'},
            {'name': 'build', 'status': 'IN_PROGRESS'},
        ]
        (pr,) = build(raw_prs=[raw_pr(40, 'OPEN', statusCheckRollup=checks)])['prs']
        self.assertEqual(pr['ci'], {'passed': [], 'failed': ['tests'], 'pending': ['build']})

    def test_stale_open_pr(self) -> None:
        (pr,) = build(raw_prs=[raw_pr(40, 'OPEN')])['prs']
        self.assertEqual((pr['state'], pr['decided_at'], pr['days_open']), ('open', None, 21.6))
        self.assertTrue(pr['stale'])

    def test_prs_decided_before_the_period_are_left_out(self) -> None:
        old = raw_pr(
            30,
            'MERGED',
            createdAt='2026-08-01T00:00:00Z',
            mergedAt='2026-08-02T00:00:00Z',
            closedAt='2026-08-02T00:00:00Z',
        )
        self.assertEqual(build(raw_prs=[old])['prs'], [])

    def test_a_pr_without_artifacts_still_counts(self) -> None:
        (pr,) = build(raw_prs=[raw_pr(40, 'CLOSED', body='Written by hand.')])['prs']
        self.assertEqual((pr['run'], pr['edited'], pr['alerts_closed']), (None, None, []))

    def test_stopped_runs_come_from_comments(self) -> None:
        stopped = {
            **RUN,
            'id': '20261015-100000-dependency-upgrades',
            'started_at': '2026-10-15T10:00:00Z',
            'outcome': 'stopped_pr_open',
            'pr': 40,
            'alerts_not_covered': [12, 13],
        }
        comments = [
            {
                'author': {'login': 'reviewer'},
                'createdAt': '2026-10-11T00:00:00Z',
                'body': 'Looks fine.',
            },
            {
                'author': {'login': 'bot'},
                'createdAt': '2026-10-15T10:01:00Z',
                'body': embed.embed(stopped),
            },
        ]
        result = build(raw_prs=[raw_pr(40, 'MERGED', comments=comments)])
        self.assertEqual(
            result['stopped_runs'],
            [
                {
                    'id': stopped['id'],
                    'trigger': 'unattended',
                    'started_by': 'systemd timer',
                    'started_at': '2026-10-15T10:00:00Z',
                    'pr': 40,
                    'alerts_not_covered': 2,
                }
            ],
        )
        # The run's own comment is data, not a person's comment.
        self.assertEqual([c['author'] for c in result['prs'][0]['comments']], ['reviewer'])

    def test_reviews_count_as_what_people_said(self) -> None:
        reviews = [
            {
                'author': {'login': 'lead'},
                'state': 'CHANGES_REQUESTED',
                'body': 'Pin qs.',
                'submittedAt': '2026-10-11T09:00:00Z',
            },
            {
                'author': {'login': 'other'},
                'state': 'COMMENTED',
                'body': '',
                'submittedAt': '2026-10-11T10:00:00Z',
            },
        ]
        (pr,) = build(raw_prs=[raw_pr(40, 'MERGED', reviews=reviews)])['prs']
        self.assertEqual(
            [(c['kind'], c['author'], c['body']) for c in pr['comments']],
            [('review: changes_requested', 'lead', 'Pin qs.')],
        )


class AlertHistoryTests(unittest.TestCase):
    def history(self, alerts: list[dict], closed_by_prs: set[int] = frozenset()) -> dict:
        return outcomes.alert_history(
            alerts, SINCE, UNTIL, POLICY['fix_within_days'], set(closed_by_prs)
        )

    def test_counts_and_time_to_fix(self) -> None:
        result = self.history(
            [
                alert(1, 'high', 'fixed', '2026-10-01T00:00:00Z', fixed_at='2026-10-03T00:00:00Z'),
                alert(2, 'high', 'fixed', '2026-10-01T00:00:00Z', fixed_at='2026-10-11T00:00:00Z'),
                alert(
                    3,
                    'moderate',
                    'dismissed',
                    '2026-09-01T00:00:00Z',
                    dismissed_at='2026-10-20T00:00:00Z',
                ),
                alert(4, 'low', 'open', '2026-10-20T00:00:00Z'),
            ],
            closed_by_prs={2},
        )
        self.assertEqual(result['fixed']['high'], 2)
        self.assertEqual(result['days_to_fix']['high'], {'count': 2, 'median': 6.0, 'max': 10.0})
        self.assertEqual(result['days_to_fix']['low'], {'count': 0, 'median': None, 'max': None})
        self.assertEqual(result['dismissed']['medium'], 1)
        self.assertEqual(result['opened'], {'critical': 0, 'high': 2, 'medium': 0, 'low': 1})
        self.assertEqual(result['open_now']['low'], 1)
        self.assertEqual(result['fixed_by_upgrade_prs'], [2])

    def test_open_past_target_most_severe_and_oldest_first(self) -> None:
        result = self.history(
            [
                alert(1, 'medium', 'open', '2026-08-01T00:00:00Z'),
                alert(2, 'critical', 'open', '2026-10-25T00:00:00Z'),
                alert(3, 'critical', 'open', '2026-10-30T00:00:00Z'),  # 2 days: within 3
                alert(4, 'low', 'open', '2026-10-01T00:00:00Z'),  # 31 days: within 90
            ]
        )
        self.assertEqual(
            [(a['number'], a['age_days']) for a in result['open_past_target']],
            [(2, 7.0), (1, 92.0)],
        )

    def test_fixed_outside_the_period_is_not_counted(self) -> None:
        result = self.history(
            [alert(1, 'high', 'fixed', '2026-08-01T00:00:00Z', fixed_at='2026-09-01T00:00:00Z')]
        )
        self.assertEqual(result['fixed']['high'], 0)


class PackageTests(unittest.TestCase):
    def health(self, **fields: object) -> dict:
        base = {
            'ecosystem': 'npm',
            'package': 'request',
            'latest': '2.88.2',
            'latest_released_at': '2020-02-11T16:35:36Z',
            'deprecated': 'request has been deprecated',
            'repository': 'https://github.com/request/request',
            'archived': True,
            'open_alerts': 2,
            'error': None,
        }
        return {**base, **fields}

    def test_flags(self) -> None:
        (package,) = build(packages=[self.health()])['packages']
        self.assertEqual(package['flags'], ['stale', 'deprecated', 'archived'])

    def test_healthy(self) -> None:
        fresh = self.health(
            latest_released_at='2026-10-01T00:00:00Z', deprecated=None, archived=False
        )
        self.assertEqual(build(packages=[fresh])['packages'][0]['flags'], [])

    def test_github_repo(self) -> None:
        for url in (
            'git+https://github.com/request/request.git',
            'https://github.com/request/request/tree/main',
            'git@github.com:request/request.git',
        ):
            self.assertEqual(collect.github_repo(url), 'request/request', url)
        self.assertIsNone(collect.github_repo('https://gitlab.com/x/y'))
        self.assertIsNone(collect.github_repo('https://github.com/sponsors/someone'))

    def test_pypi_prefers_the_source_link(self) -> None:
        urls = {
            'Funding': 'https://github.com/sponsors/someone',
            'Homepage': 'https://example.org',
            'Source': 'https://github.com/pypa/virtualenv',
        }
        data = {'info': {'version': '1.0', 'project_urls': urls}, 'releases': {}}
        self.assertEqual(collect._pypi(data)['repository'], 'https://github.com/pypa/virtualenv')

    def test_registry_failure_is_recorded(self) -> None:
        def fail(url: str) -> dict:
            raise OSError('timed out')

        record = collect.package_health('pip', 'django', 1, fail)
        self.assertEqual(record['error'], 'registry: timed out')
        self.assertIsNone(record['latest'])

    def test_pypi_inactive_is_deprecated(self) -> None:
        data = {
            'info': {
                'version': '1.0',
                'project_urls': {'Source': 'https://github.com/a/b'},
                'classifiers': ['Development Status :: 7 - Inactive'],
            },
            'releases': {'1.0': [{'upload_time_iso_8601': '2019-01-01T00:00:00Z'}]},
        }
        result = collect._pypi(data)
        self.assertEqual(result['deprecated'], 'classified as Inactive on PyPI')
        self.assertEqual(result['repository'], 'https://github.com/a/b')

    def test_packages_to_check(self) -> None:
        alerts = [
            alert(1, 'high', 'open', '2026-10-01T00:00:00Z'),
            alert(1, 'high', 'open', '2026-10-02T00:00:00Z'),
            alert(2, 'high', 'fixed', '2026-10-01T00:00:00Z'),
        ]
        eol = make(
            FINDING,
            id='dependabot-2026-11-django',
            kind='investigate',
            dependency={
                **FINDING['dependency'],
                'package': 'django',
                'classification': 'eol',
                'fixed': None,
                'tracked_by': None,
            },
        )
        prs = [{'body': embed.embed(eol)}]
        self.assertEqual(
            collect.packages_to_check(alerts, prs), [('pip', 'django', 0), ('pip', 'pkg1', 2)]
        )

    def test_revert_package(self) -> None:
        self.assertEqual(outcomes.revert_package('Revert "Upgrade qs to 6.17.1"'), 'qs')
        self.assertIsNone(outcomes.revert_package('Revert "Add a feature"'))


class ArtifactTests(unittest.TestCase):
    def test_built_outcomes_validate(self) -> None:
        result = build(
            raw_prs=[raw_pr(40, 'MERGED'), raw_pr(41, 'OPEN')],
            alerts=[alert(4, 'low', 'open', '2026-05-01T00:00:00Z')],
            reverts=[
                {
                    'sha': 'f' * 40,
                    'date': '2026-10-20T00:00:00Z',
                    'subject': 'Revert "Upgrade qs to 6.17.1"',
                    'package': 'qs',
                }
            ],
        )
        self.assertEqual(outcomes.validate(result), [])

    def test_report(self) -> None:
        result = build(
            raw_prs=[raw_pr(40, 'MERGED', headRefOid='e' * 40), raw_pr(41, 'OPEN')],
            alerts=[
                alert(4, 'low', 'open', '2026-05-01T00:00:00Z'),
                alert(1, 'high', 'fixed', '2026-10-01T00:00:00Z', fixed_at='2026-10-03T00:00:00Z'),
            ],
            packages=[
                {
                    'ecosystem': 'npm',
                    'package': 'request',
                    'latest': '2.88.2',
                    'latest_released_at': '2020-02-11T16:35:36Z',
                    'deprecated': 'use fetch',
                    'repository': None,
                    'archived': None,
                    'open_alerts': 2,
                    'error': None,
                }
            ],
        )
        skill_change = make(
            FINDING,
            id='retro-2026-10-edits',
            fingerprint='skill/edited-before-merge',
            kind='skill_change',
            title='Upgrade PRs are edited before merge',
            claim='Both merged PRs were edited.',
            dependency=...,
            source={'process': 'dependency-retrospective', 'refs': ['#40']},
            evidence=[{'ref': '#40', 'note': 'edited', 'status': 'verified'}],
        )
        body = render.retrospective_body(result, [skill_change], RETRO_RUN)
        self.assertTrue(body.startswith('**Dependency retrospective: 2026-10-01 to 2026-11-01.**'))
        self.assertIn(
            '2 upgrade PRs: 1 merged (1 after edits), 0 closed without merging, 1 open (1 stale).',
            body,
        )
        self.assertIn('| High | 7 days | 1 | 2 | 2 | 0 | 0 |', body)
        self.assertIn('| Low | 90 days | 0 | – | – | 1 | 1 |', body)
        self.assertIn(
            '| #40 | systemd timer (unattended) | merged after edits | 2 | passed | 1 |', body
        )
        self.assertIn('| `request` (npm) | stale, deprecated |', body)
        self.assertIn('### Proposed changes to the workflow', body)
        embedded = {a['schema'] for a in embed.extract(body)}
        self.assertEqual(embedded, {'outcomes/1', 'finding/1', 'run/1'})
        (record,) = embed.extract(body, 'run/1')
        self.assertEqual(record['outcome'], 'issue_opened')

    def test_report_with_no_prs(self) -> None:
        body = render.retrospective_body(build(), [], RETRO_RUN)
        self.assertIn('No upgrade PRs in this period. 0 alerts fixed', body)
        self.assertIn('No findings this period.', body)


if __name__ == '__main__':
    unittest.main()
