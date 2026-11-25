import unittest

from artifacts import embed, render

from .fixtures import (
    CHANGE,
    DEPENDENCY,
    FINDING,
    REVIEW,
    REVIEW_FINDING,
    REVIEW_RUN,
    RUN,
    SHA_A,
    make,
)


def review_of(*findings: dict, **changes) -> dict:
    return make(REVIEW, findings=list(findings), **changes)


SUGGESTION = make(
    REVIEW_FINDING,
    id='F2',
    fingerprint='app/naming',
    severity='nit',
    title='Name',
    location={'path': 'app/views.py', 'line': 13, 'side': 'RIGHT', 'in_diff': True},
    suggestion='    extra = False',
)
OUTSIDE = make(
    REVIEW_FINDING,
    id='F3',
    fingerprint='app/model-slug',
    severity='suggestion',
    title='Slug not unique',
    location={'path': 'app/models.py', 'line': 171, 'side': 'RIGHT', 'in_diff': False},
)


class DispositionTests(unittest.TestCase):
    def test_blocking(self) -> None:
        self.assertEqual(render.disposition(REVIEW), 'Changes requested')
        self.assertEqual(render.counts(REVIEW), '1 important')

    def test_non_blocking(self) -> None:
        review = review_of(SUGGESTION, OUTSIDE)
        self.assertEqual(render.disposition(review), 'No blocking findings')
        self.assertEqual(render.counts(review), '1 suggestion, 1 nit')

    def test_resolved_findings_do_not_count(self) -> None:
        review = review_of(make(REVIEW_FINDING, status='addressed'), followup_of=SHA_A)
        self.assertEqual(render.disposition(review), 'No findings')
        self.assertEqual(render.counts(review), 'none open')


class CommentTests(unittest.TestCase):
    def test_comment_groups_by_severity_and_embeds_the_review(self) -> None:
        review = review_of(SUGGESTION, REVIEW_FINDING, OUTSIDE)
        comment = render.review_comment(review, REVIEW_RUN)
        self.assertIn('**Changes requested** (1 important, 1 suggestion, 1 nit)', comment)
        self.assertLess(comment.index('### Important'), comment.index('### Suggestions'))
        self.assertLess(comment.index('### Suggestions'), comment.index('### Nits'))
        self.assertIn('**F3. Slug not unique** `app/models.py:171`', comment)
        self.assertIn('Suggested replacement:\n\n```\n    extra = False\n```', comment)
        self.assertEqual(embed.extract(comment, 'review/1'), [{**review, 'inline': 'none'}])

    def test_follow_up_lists_earlier_findings(self) -> None:
        review = review_of(
            make(REVIEW_FINDING, status='declined'),
            make(SUGGESTION, status='still_open'),
            followup_of=SHA_A,
        )
        comment = render.review_comment(review, REVIEW_RUN)
        self.assertIn('### Since the last review\n\n- **F1. Lookup**: declined', comment)
        self.assertIn('(still open from the last review)', comment)

    def test_notes(self) -> None:
        finding = make(REVIEW_FINDING, evidence='inferred', rule='correctness/x', location=None)
        comment = render.review_comment(review_of(finding), REVIEW_RUN)
        self.assertIn('**F1. Lookup** (inferred, not verified; rule `correctness/x`)', comment)

    def test_too_long_is_refused(self) -> None:
        review = review_of(make(REVIEW_FINDING, body='x' * 70_000))
        with self.assertRaisesRegex(ValueError, 'GitHub allows'):
            render.review_comment(review, REVIEW_RUN)


class PayloadTests(unittest.TestCase):
    def test_no_inline_by_default(self) -> None:
        payload = render.review_payload(review_of(REVIEW_FINDING, SUGGESTION), REVIEW_RUN)
        self.assertEqual(payload['event'], 'COMMENT')
        self.assertEqual(payload['commit_id'], REVIEW['head_sha'])
        self.assertEqual(payload['comments'], [])

    def test_only_suggestions_inline(self) -> None:
        payload = render.review_payload(
            review_of(REVIEW_FINDING, SUGGESTION, OUTSIDE), REVIEW_RUN, 'suggestions'
        )
        self.assertEqual(len(payload['comments']), 1)
        comment = payload['comments'][0]
        self.assertEqual(
            (comment['path'], comment['line'], comment['side']), ('app/views.py', 13, 'RIGHT')
        )
        self.assertIn('```suggestion\n    extra = False\n```', comment['body'])
        self.assertIn('(suggestion posted inline)', payload['body'])

    def test_follow_up_posts_only_new_findings_inline(self) -> None:
        # A finding carried forward already has its inline comment from the earlier review.
        carried = make(REVIEW_FINDING, status='still_open')
        fresh = make(SUGGESTION, status='new')
        payload = render.review_payload(
            review_of(carried, fresh, followup_of=SHA_A), REVIEW_RUN, 'all'
        )
        self.assertEqual([c['line'] for c in payload['comments']], [13])
        self.assertIn('(still open from the last review)', payload['body'])

    def test_the_setting_travels_with_the_review(self) -> None:
        comment = render.review_comment(review_of(REVIEW_FINDING), REVIEW_RUN, 'all')
        (embedded,) = embed.extract(comment, 'review/1')
        self.assertEqual(embedded['inline'], 'all')

    def test_all_inline_skips_out_of_diff_and_closed(self) -> None:
        closed = make(SUGGESTION, id='F4', fingerprint='app/closed', status='withdrawn')
        review = review_of(REVIEW_FINDING, OUTSIDE, closed, followup_of=SHA_A)
        payload = render.review_payload(review, REVIEW_RUN, 'all')
        self.assertEqual([c['line'] for c in payload['comments']], [12])

    def test_range_is_carried(self) -> None:
        ranged = make(
            REVIEW_FINDING,
            location={
                'path': 'app/views.py',
                'start_line': 11,
                'start_side': 'RIGHT',
                'line': 13,
                'side': 'RIGHT',
                'in_diff': True,
            },
        )
        comment = render.review_payload(review_of(ranged), REVIEW_RUN, 'all')['comments'][0]
        self.assertEqual((comment['start_line'], comment['line']), (11, 13))


class ChangeBodyTests(unittest.TestCase):
    def test_body_sections_and_embedded_artifacts(self) -> None:
        body = render.change_body(CHANGE, [FINDING], RUN)
        self.assertTrue(body.startswith('Closes 1 high alert by upgrading Pillow.'))
        self.assertIn(
            '- **Pillow 12.0.0 has a fixed vulnerability** (important; dependabot#7)', body
        )
        self.assertIn('| `make test` | pass |  |', body)
        self.assertIn(
            'Dependabot closes alert 7. Check by hand 1 day after merge; nothing checks this '
            'automatically yet.',
            body,
        )
        self.assertIn('for n in 7; do', body)
        self.assertIn('gh api repos/example/project/dependabot/alerts/$n', body)
        self.assertIn(
            'Opened by run `20261111-093000-dependency-upgrades`, started by systemd timer '
            '(unattended) on factory-1 at 2026-11-11T09:30:00Z',
            body,
        )
        self.assertEqual(embed.extract(body), [CHANGE, FINDING, {**RUN, 'outcome': 'pr_opened'}])

    def test_check_left_to_ci(self) -> None:
        change = make(CHANGE, verified=[{'check': 'make test', 'result': 'ci'}])
        self.assertIn(
            '| `make test` | runs in CI on this PR |  |', render.change_body(change, [FINDING], RUN)
        )

    def test_dependency_tables_are_derived(self) -> None:
        change = make(
            CHANGE,
            dependencies=[
                {
                    'ecosystem': 'pip',
                    'package': 'pillow',
                    'before': '12.1.0',
                    'after': '13.0.0',
                    'manifests': ['requirements/base.txt'],
                    'direct': True,
                    'via': [],
                    'copy': None,
                },
                {
                    'ecosystem': 'pip',
                    'package': 'certifi',
                    'before': '2025.1.31',
                    'after': '2026.10.18',
                    'manifests': ['requirements/base.txt', 'requirements/dev.txt'],
                    'direct': False,
                    'via': ['requests'],
                    'copy': None,
                },
                {
                    'ecosystem': 'npm',
                    'package': 'qs',
                    'before': '6.16.0',
                    'after': '6.17.1',
                    'manifests': ['client/package-lock.json'],
                    'direct': False,
                    'via': ['url'],
                    'copy': 'url > qs',
                },
                {
                    'ecosystem': 'pip',
                    'package': 'python-discovery',
                    'before': None,
                    'after': '1.9.4',
                    'manifests': ['requirements/dev.txt'],
                    'direct': False,
                    'via': ['virtualenv'],
                    'copy': None,
                },
            ],
            alerts={
                'open_before': {'critical': 2, 'high': 3, 'medium': 1, 'low': 0},
                'closed': [
                    {'number': 7, 'package': 'pillow', 'severity': 'critical'},
                    {'number': 9, 'package': 'qs', 'severity': 'medium'},
                ],
                'already_fixed': [],
                'undecided': [{'number': 12, 'package': 'odd', 'severity': 'high'}],
            },
        )
        body = render.change_body(change, [FINDING], RUN)
        self.assertIn('| `pillow` | 12.1.0 | 13.0.0 **(major)** | `requirements/base.txt` |', body)
        self.assertIn('| `certifi` *(transitive, via requests)* | 2025.1.31 | 2026.10.18 |', body)
        self.assertIn('| `qs` *(nested copy: url > qs)* | 6.16.0 | 6.17.1 |', body)
        self.assertIn(
            '| `python-discovery` *(transitive, via virtualenv; new)* | – | 1.9.4 |', body
        )
        self.assertIn('2 of the 6 open alerts close when this merges.', body)
        self.assertIn('| Critical | 2 | 1 | 1 |', body)
        self.assertIn('| **Total** | **6** | **2** | **4** |', body)
        self.assertIn('check by hand: #12 (odd)', body)

    def test_findings_left_out(self) -> None:
        eol = make(
            FINDING,
            id='deps-django',
            fingerprint='deps/pip/django',
            kind='investigate',
            title='Django 5.1 is EOL',
            dependency={
                **DEPENDENCY,
                'package': 'django',
                'classification': 'eol',
                'fixed': None,
                'tracked_by': None,
            },
        )
        vite = make(
            eol,
            id='deps-vite',
            title='Vite 5 is EOL',
            source={'process': 'dependency-upgrades', 'refs': ['dependabot#1', 'dependabot#2']},
            dependency={
                **eol['dependency'],
                'package': 'vite',
                'tracked_by': 'https://github.com/o/r/tree/next-frontend',
            },
        )
        broken = make(
            FINDING,
            id='deps-browserslist',
            kind='investigate',
            title='browserslist breaks the build',
            claim='The upgraded tree fails to build.',
        )
        body = render.change_body(CHANGE, [FINDING], RUN, [vite, eol, broken])
        section = body.split('## Not in this PR\n\n')[1].split('## Revert')[0]
        self.assertEqual(
            section,
            '**Not tracked anywhere yet** (1 alert):\n\n'
            '- **Django 5.1 is EOL** (1 alert, eol)\n\n'
            '**Left out for the reason given** (1 alert):\n\n'
            '- **browserslist breaks the build** (1 alert, easy). The upgraded tree fails to build.\n\n'
            '**Tracked in branch [`next-frontend`](https://github.com/o/r/tree/next-frontend)** (2 alerts):\n\n'
            '- **Vite 5 is EOL** (2 alerts, eol)\n\n\n',
        )
        self.assertEqual(len(embed.extract(body, 'finding/1')), 4)

    def test_uncommitted_automation_is_said(self) -> None:
        run = make(RUN, produced_by={**RUN['produced_by'], 'automation_sha': 'a' * 40 + '-dirty'})
        self.assertIn(
            'mason aaaaaaaaaa with uncommitted changes',
            render.change_body(CHANGE, [FINDING], run),
        )


class StoppedCommentTests(unittest.TestCase):
    def test_lists_what_the_open_pr_does_not_cover(self) -> None:
        missing = [
            {'number': 12, 'package': 'django', 'severity': 'critical'},
            {'number': 9, 'package': 'qs', 'severity': 'medium'},
        ]
        comment = render.stopped_comment(RUN, 40, missing)
        self.assertTrue(
            comment.startswith(
                '**Run `20261111-093000-dependency-upgrades` stopped without changing this PR.**'
                '\n\nStarted by systemd timer (unattended) on factory-1 at 2026-11-11T09:30:00Z'
            )
        )
        self.assertIn('2 open alerts are not covered by this PR (1 critical, 1 medium)', comment)
        self.assertIn('<summary>The 2 alerts</summary>', comment)
        self.assertIn('| Critical | #12 | `django` |', comment)
        (record,) = embed.extract(comment, 'run/1')
        self.assertEqual(record['outcome'], 'stopped_pr_open')
        self.assertEqual((record['pr'], record['alerts_not_covered']), (40, [12, 9]))

    def test_nothing_missing(self) -> None:
        self.assertIn('Every open alert is covered', render.stopped_comment(RUN, 40, []))


if __name__ == '__main__':
    unittest.main()
