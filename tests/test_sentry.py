"""The Sentry workflow's scripts: reading Sentry, the outcomes, and the triage and retrospective."""

import json
import unittest
from datetime import UTC, datetime

from artifacts import change, embed, finding, project, render, sentry, sentry_outcomes

from .fixtures import CHANGE, FINDING, RUN, make

BASE = 'https://sentry.example/api/0'
ISSUE = {
    'id': '4501',
    'shortId': 'EXAMPLE-1A2',
    'title': 'DoesNotExist: Order matching query does not exist. (order 12345678 for ann@example.org)',
    'culprit': 'shop.views.cancel_order',
    'level': 'error',
    'issueCategory': 'error',
    'issueType': 'error',
    'metadata': {'type': 'DoesNotExist'},
    'count': '812',
    'userCount': 97,
    'firstSeen': '2026-10-02T08:00:00Z',
    'lastSeen': '2026-11-10T21:14:00Z',
    'permalink': 'https://sentry.example/issues/4501/',
}
EVENT = {
    'eventID': 'e1',
    'dateCreated': '2026-11-10T21:14:00Z',
    'release': {'version': '5f3a2c1'},
    'message': '',
    'user': {'email': 'ann@example.org', 'ip_address': '203.0.113.9'},
    'tags': [
        {'key': 'release', 'value': '5f3a2c1'},
        {'key': 'environment', 'value': 'production'},
        {'key': 'user.email', 'value': 'ann@example.org'},
        {'key': 'url', 'value': 'https://shop.example/orders/12345678/cancel'},
    ],
    'entries': [
        {'type': 'request', 'data': {'headers': [['Cookie', 'session=secret']]}},
        {
            'type': 'exception',
            'data': {
                'values': [
                    {
                        'type': 'DoesNotExist',
                        'value': 'Order matching query does not exist. ip 203.0.113.9',
                        'stacktrace': {
                            'frames': [
                                {
                                    'filename': 'django/db/models/query.py',
                                    'function': 'get',
                                    'lineNo': 650,
                                    'inApp': False,
                                },
                                {
                                    'filename': 'shop/views.py',
                                    'function': 'cancel_order',
                                    'lineNo': 90,
                                    'inApp': True,
                                    'context': [
                                        [89, '    ...'],
                                        [90, '    order = Order.objects.get(number=number)'],
                                    ],
                                },
                            ]
                        },
                    }
                ]
            },
        },
    ],
}
POLICY = {'quiet_after_days': 14, 'deploy_grace_hours': 24, 'stale_pr_after_days': 14}
RETRO_RUN = make(RUN, id='20261201-000000-sentry-retrospective')
SENTRY_FINDING = make(
    FINDING,
    id='sentry-2026-11-example-1a2',
    fingerprint='shop/cancel-unknown-order-500',
    source={'process': 'sentry-errors', 'refs': ['EXAMPLE-1A2']},
    kind='code_fix',
    title='Cancelling an unknown order is a 500',
    claim='cancel_order uses Order.objects.get, so an unknown number raises DoesNotExist.',
    dependency=...,
    impact={'period_days': 14, 'events': 812, 'users': 97},
)
SENTRY_CHANGE = make(
    CHANGE,
    findings=['sentry-2026-11-example-1a2'],
    branch='sentry-errors-20261111-093000',
    success_signal={
        'kind': 'sentry_quiet',
        'refs': ['EXAMPLE-1A2'],
        'check_after_days': 14,
        'description': 'EXAMPLE-1A2 gets no new events in production after the deploy',
    },
)


class FakeSentry:
    def __init__(self, pages: dict[str, tuple[object, str]]):
        self.pages, self.asked = pages, []

    def __call__(self, url: str) -> tuple[object, str]:
        self.asked.append(url)
        for prefix, response in self.pages.items():
            if url.startswith(prefix):
                return response
        raise sentry.SentryError(f'Sentry answered 404 for {url}')


class ClientTests(unittest.TestCase):
    def test_unresolved_follows_pages_and_scrubs(self) -> None:
        second = f'{BASE}/projects/example-org/example-app/issues/?cursor=2'
        fake = FakeSentry(
            {
                second: (
                    [{**ISSUE, 'id': '4502', 'shortId': 'EXAMPLE-1A3'}],
                    '<x>; rel="next"; results="false"',
                ),
                f'{BASE}/projects/example-org/example-app/issues/': (
                    [ISSUE],
                    f'<{second}>; rel="next"; results="true"',
                ),
            }
        )
        client = sentry.Client('https://sentry.example', 'example-org', 'example-app', fake)
        issues = client.unresolved('production', '14d')
        self.assertEqual([i['short_id'] for i in issues], ['EXAMPLE-1A2', 'EXAMPLE-1A3'])
        self.assertIn('environment=production', fake.asked[0])
        self.assertIn('query=is%3Aunresolved', fake.asked[0])
        self.assertEqual((issues[0]['events'], issues[0]['users']), (812, 97))
        self.assertEqual(
            issues[0]['title'],
            'DoesNotExist: Order matching query does not exist. (order <number> for <email>)',
        )

    def test_event_keeps_the_error_and_drops_personal_data(self) -> None:
        summary = sentry.event_summary(EVENT)
        text = json.dumps(summary)
        for secret in (
            'ann@example.org',
            '203.0.113.9',
            'session=secret',
            '12345678',
            'user.email',
        ):
            self.assertNotIn(secret, text)
        (exception,) = summary['exceptions']
        self.assertEqual(exception['value'], 'Order matching query does not exist. ip <ip>')
        self.assertEqual(
            exception['frames'],
            [
                {
                    'file': 'shop/views.py',
                    'function': 'cancel_order',
                    'line': 90,
                    'code': '    order = Order.objects.get(number=number)',
                }
            ],
        )
        self.assertEqual(summary['tags'], {'release': '5f3a2c1', 'environment': 'production'})

    def test_a_missing_token_is_a_clear_error(self) -> None:
        import os

        saved = os.environ.pop(sentry.TOKEN_VARIABLE, None)
        try:
            with self.assertRaisesRegex(sentry.SentryError, 'SENTRY_AUTH_TOKEN is not set'):
                sentry._http_fetch('https://sentry.example/api/0/x')
        finally:
            if saved is not None:
                os.environ[sentry.TOKEN_VARIABLE] = saved


class ArtifactTests(unittest.TestCase):
    def test_sentry_finding_and_change_validate(self) -> None:
        self.assertEqual(finding.validate(SENTRY_FINDING), [])
        self.assertEqual(change.validate(SENTRY_CHANGE), [])
        no_refs = make(
            SENTRY_CHANGE, success_signal={**SENTRY_CHANGE['success_signal'], 'refs': []}
        )
        self.assertIn(
            '$.success_signal.refs: must name what to check when kind is sentry_quiet',
            change.validate(no_refs),
        )

    def test_sentry_needs_its_org_and_project_in_the_file(self) -> None:
        errors = project.check(
            {'sentry-errors': {'environments': ['production']}}, ('sentry-errors',)
        )
        self.assertIn('$.sentry-errors.sentry_org: is required', errors)


class RenderTests(unittest.TestCase):
    def test_fix_pr_shows_the_errors_and_who_checks_the_signal(self) -> None:
        body = render.change_body(SENTRY_CHANGE, [SENTRY_FINDING], RUN)
        self.assertIn('| Cancelling an unknown order is a 500 | EXAMPLE-1A2 | 812 | 97 |', body)
        self.assertIn('Events and users over the last 14 days.', body)
        self.assertIn('The Sentry retrospective checks this 14 days after merge', body)

    def test_left_out_errors_are_counted_in_events(self) -> None:
        other = make(
            SENTRY_FINDING,
            id='sentry-2026-11-x',
            kind='data_fix',
            title='Bad row',
            impact={'period_days': 14, 'events': 40},
        )
        body = render.change_body(SENTRY_CHANGE, [SENTRY_FINDING], RUN, [other])
        self.assertIn('- **Bad row** (40 events, data fix)', body)

    def test_triage_issue(self) -> None:
        archive = make(
            SENTRY_FINDING,
            id='sentry-2026-11-bot',
            fingerprint='bots/head-requests',
            kind='archive',
            title='Bots send HEAD to /admin',
            impact={'period_days': 14, 'events': 5},
        )
        body = render.triage_body(
            'production', [archive, make(SENTRY_FINDING, kind='data_fix')], RUN
        )
        self.assertTrue(body.startswith('**Sentry triage (production): 2026-11-11.**'))
        self.assertLess(
            body.index('## Data to fix'), body.index('## To archive or resolve in Sentry')
        )
        self.assertIn('(EXAMPLE-1A2; 812 events, 97 users)', body)
        self.assertEqual(len(embed.extract(body, 'finding/1')), 2)
        self.assertEqual(embed.extract(body, 'run/1')[0]['outcome'], 'issue_opened')


def raw_pr(number: int, state: str, merged: str | None) -> dict:
    body = '\n'.join(
        [
            embed.embed(SENTRY_CHANGE),
            embed.embed(SENTRY_FINDING),
            embed.embed({**RUN, 'outcome': 'pr_opened'}),
        ]
    )
    return {
        'number': number,
        'title': 'Fix cancel',
        'url': f'https://github.com/o/r/pull/{number}',
        'state': state,
        'isDraft': False,
        'createdAt': '2026-11-02T00:00:00Z',
        'mergedAt': merged,
        'closedAt': merged,
        'headRefName': 'sentry-errors-20261102-000000',
        'headRefOid': 'd' * 40,
        'body': body,
        'comments': [],
        'statusCheckRollup': [],
    }


class OutcomeTests(unittest.TestCase):
    def build(self, prs: list[dict], last: dict, until: datetime) -> dict:
        return sentry_outcomes.build(
            repo='example-org/example-app',
            run=RETRO_RUN,
            since=datetime(2026, 11, 1, tzinfo=UTC),
            until=until,
            policy=POLICY,
            raw_prs=prs,
            last_events=last,
            raw_triage=[
                {
                    'number': 9,
                    'url': 'https://github.com/o/r/issues/9',
                    'state': 'OPEN',
                    'createdAt': '2026-11-05T00:00:00Z',
                    'body': render.triage_body('production', [SENTRY_FINDING], RUN),
                }
            ],
            unresolved_now={'production': 41},
        )

    def test_quiet_after_the_window_is_met(self) -> None:
        result = self.build(
            [raw_pr(40, 'MERGED', '2026-11-03T00:00:00Z')],
            {'EXAMPLE-1A2': '2026-11-03T12:00:00Z'},
            datetime(2026, 11, 20, tzinfo=UTC),
        )
        self.assertEqual(sentry_outcomes.validate(result), [])
        signal = result['prs'][0]['signal']
        # The event 12 hours after the merge is within the 24-hour deploy grace.
        self.assertEqual(signal['status'], 'met')
        self.assertFalse(signal['issues'][0]['after_fix'])
        self.assertEqual(result['triage_issues'][0]['findings'], ['shop/cancel-unknown-order-500'])

    def test_an_event_after_the_deploy_is_not_met(self) -> None:
        result = self.build(
            [raw_pr(40, 'MERGED', '2026-11-03T00:00:00Z')],
            {'EXAMPLE-1A2': '2026-11-06T00:00:00Z'},
            datetime(2026, 11, 8, tzinfo=UTC),
        )
        self.assertEqual(result['prs'][0]['signal']['status'], 'not_met')

    def test_quiet_so_far_is_pending(self) -> None:
        result = self.build(
            [raw_pr(40, 'MERGED', '2026-11-03T00:00:00Z')],
            {'EXAMPLE-1A2': '2026-11-02T00:00:00Z'},
            datetime(2026, 11, 8, tzinfo=UTC),
        )
        self.assertEqual(result['prs'][0]['signal']['status'], 'pending')

    def test_unmerged_is_not_applicable(self) -> None:
        result = self.build([raw_pr(41, 'OPEN', None)], {}, datetime(2026, 11, 8, tzinfo=UTC))
        self.assertEqual(result['prs'][0]['signal']['status'], 'not_applicable')

    def test_report(self) -> None:
        result = self.build(
            [raw_pr(40, 'MERGED', '2026-11-03T00:00:00Z'), raw_pr(41, 'OPEN', None)],
            {'EXAMPLE-1A2': '2026-11-06T00:00:00Z'},
            datetime(2026, 11, 20, tzinfo=UTC),
        )
        body = render.sentry_retrospective_body(result, [], RETRO_RUN)
        self.assertIn('2 fix PRs: 0 working (quiet for 14 days after merge), 1 not working', body)
        self.assertIn('| #40 | merged | EXAMPLE-1A2 | not met | EXAMPLE-1A2 |', body)
        self.assertIn('| production | 41 |', body)
        self.assertIn('- #9 (open, 1 finding, opened 2026-11-05)', body)
        self.assertEqual({a['schema'] for a in embed.extract(body)}, {'sentry-outcomes/1', 'run/1'})


if __name__ == '__main__':
    unittest.main()
