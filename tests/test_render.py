import unittest

from artifacts import embed, render

from .fixtures import CHANGE, DEPENDENCY, FINDING, RUN, make


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
            'automation aaaaaaaaaa with uncommitted changes',
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
