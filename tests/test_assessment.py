import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from artifacts import assessment, embed, render, validate

from . import ROOT
from .fixtures import FINDING, RUN, make

RUN_RECORD = make(RUN, id='20261119-090000-dependency-assessment')
COMMIT = 'c' * 40


def alert(number: int, package: str, severity: str = 'high', ecosystem: str = 'pip') -> dict:
    return {
        'number': number,
        'state': 'open',
        'created_at': '2026-11-01T00:00:00Z',
        'dependency': {
            'package': {'ecosystem': ecosystem, 'name': package},
            'manifest_path': 'requirements/base.txt' if ecosystem == 'pip' else 'package-lock.json',
        },
        'security_advisory': {'severity': severity, 'ghsa_id': f'GHSA-{number}'},
        'security_vulnerability': {'first_patched_version': {'identifier': '9.9.9'}},
    }


def finding(fid: str, package: str, refs: list[int], classification: str, **dependency) -> dict:
    return make(
        FINDING,
        id=fid,
        fingerprint=f'deps/pip/{package}',
        source={'process': 'dependency-assessment', 'refs': [f'dependabot#{n}' for n in refs]},
        kind='dependency' if classification == 'easy' else 'investigate',
        title=f'{package} has a fixed vulnerability',
        dependency={
            'ecosystem': 'pip',
            'package': package,
            'installed': '1.0.0',
            'fixed': None if classification == 'no_fix' else '2.0.0',
            'manifests': ['requirements/base.txt'],
            'classification': classification,
            **dependency,
        },
    )


ALERTS = [
    alert(1, 'pillow', 'critical'),
    alert(2, 'pillow'),
    alert(3, 'urllib3'),
    alert(4, 'certifi'),
]
FINDINGS = [
    finding('dependabot-2026-11-pillow', 'pillow', [1, 2], 'major', tracked_by=None),
    finding('dependabot-2026-11-urllib3', 'urllib3', [3], 'easy'),
]


def build(alerts=ALERTS, findings=FINDINGS, already_fixed=(4,)) -> dict:
    return assessment.build(
        repo='example/project',
        run=RUN_RECORD,
        assessed_at='2026-11-19T09:00:00Z',
        commit=COMMIT,
        alerts=alerts,
        findings=findings,
        already_fixed=list(already_fixed),
    )


class BuildTests(unittest.TestCase):
    def test_every_alert_is_accounted_for_once(self) -> None:
        data = build()
        self.assertEqual(validate(data), [])
        self.assertEqual(
            [(r['number'], r['classification'], r['finding']) for r in data['alerts']],
            [
                (1, 'major', 'dependabot-2026-11-pillow'),
                (2, 'major', 'dependabot-2026-11-pillow'),
                (3, 'easy', 'dependabot-2026-11-urllib3'),
                (4, 'already_fixed', None),
            ],
        )
        self.assertEqual(data['alerts'][0]['severity'], 'critical')

    def test_gaps_and_overlaps_are_refused_together(self) -> None:
        twice = finding('dependabot-2026-11-pillow-2', 'pillow', [2], 'easy')
        stale = finding('dependabot-2026-11-old', 'old', [99], 'easy')
        with self.assertRaises(assessment.AssessmentError) as caught:
            build(findings=[*FINDINGS, twice, stale], already_fixed=(3, 50))
        message = str(caught.exception)
        self.assertIn('#2 is in more than one finding', message)
        self.assertIn('#3 is in finding dependabot-2026-11-urllib3 and also already fixed', message)
        self.assertIn('#4 is in no finding and not already fixed', message)
        self.assertIn('#99, in dependabot-2026-11-old, is not open', message)
        self.assertIn('#50, given as already fixed, is not open', message)

    def test_a_covering_finding_needs_a_dependency_block(self) -> None:
        bare = make(finding('dependabot-2026-11-x', 'x', [4], 'easy'), dependency=...)
        with self.assertRaisesRegex(assessment.AssessmentError, 'has no dependency block'):
            build(findings=[*FINDINGS, bare], already_fixed=())


class ChangeTests(unittest.TestCase):
    def test_new_closed_and_reclassified(self) -> None:
        before = build()
        after_findings = [
            finding('dependabot-2026-11-pillow', 'pillow', [1, 2], 'blocked'),
            FINDINGS[1],
            finding('dependabot-2026-11-qs', 'qs', [5], 'easy'),
        ]
        after = build(
            alerts=[*ALERTS[:3], alert(5, 'qs', 'medium')],
            findings=after_findings,
            already_fixed=(),
        )
        changes = assessment.changes(before, after)
        self.assertEqual([r['number'] for r in changes['new']], [5])
        self.assertEqual([r['number'] for r in changes['closed']], [4])
        self.assertEqual(
            [(r['number'], r['was']) for r in changes['reclassified']], [(1, 'major'), (2, 'major')]
        )

    def test_a_first_assessment_is_all_new(self) -> None:
        self.assertEqual(len(assessment.changes(None, build())['new']), 4)

    def test_uncovered_alerts(self) -> None:
        self.assertEqual(assessment.uncovered(build(), [*ALERTS, alert(7, 'qs')]), [7])
        self.assertEqual(assessment.uncovered(build(), ALERTS[:2]), [])


class ValidateTests(unittest.TestCase):
    def test_broken_rows_are_named(self) -> None:
        data = build()
        data['alerts'][0]['finding'] = 'not-listed'
        data['alerts'][3]['finding'] = 'dependabot-2026-11-pillow'
        data['alerts'][1]['number'] = 1
        data['commit'] = 'abc'
        errors = validate(data)
        for path in (
            '$.alerts[0].finding',
            '$.alerts[3].finding',
            '$.alerts[1].number',
            '$.commit',
        ):
            self.assertTrue(any(e.startswith(path) for e in errors), (path, errors))


class RenderTests(unittest.TestCase):
    def test_title_and_body(self) -> None:
        data = build()
        self.assertEqual(render.assessment_title(data), 'Dependency assessment: November 2026')
        body = render.assessment_body(data, FINDINGS, RUN_RECORD, 'https://x/issues/1')
        self.assertTrue(body.startswith('**4 open alerts** (1 critical, 3 high)'))
        self.assertIn('Continues https://x/issues/1.', body)
        self.assertLess(body.index('## Major upgrades'), body.index('## Easy upgrades'))
        self.assertIn('| `pillow` (pip) | 1.0.0 | 2.0.0 | 2 | Critical | not tracked |', body)
        self.assertIn('Installed versions already fix #4', body)
        self.assertEqual(embed.extract(body, assessment.SCHEMA), [data])
        self.assertEqual(len(embed.extract(body, 'finding/1')), 2)

    def test_comment(self) -> None:
        before = build()
        after = build(alerts=ALERTS[:3], already_fixed=())
        text = render.assessment_comment(assessment.changes(before, after), after, RUN_RECORD)
        self.assertTrue(text.startswith('**Reassessed 19 November 2026:** 1 closed.'))
        self.assertIn('- #4 `certifi` (high)', text)
        unchanged = render.assessment_comment(assessment.changes(after, after), after, RUN_RECORD)
        self.assertIn('No change since the previous assessment.', unchanged)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def mason(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, '-I', str(ROOT / 'bin' / 'artifact'), *args],
            capture_output=True, text=True, check=False,
        )  # fmt: skip

    def write(self, name: str, content: object) -> str:
        path = self.tmp / name
        path.write_text(json.dumps(content), encoding='utf-8')
        return str(path)

    def test_check_assessment(self) -> None:
        data = self.write('assessment.json', build())
        ok = self.mason('check-assessment', data, '--alerts', self.write('a.json', ALERTS))
        self.assertEqual(ok.returncode, 0, ok.stderr)
        late = self.mason(
            'check-assessment', data, '--alerts', self.write('b.json', [*ALERTS, alert(9, 'qs')])
        )
        self.assertEqual(late.returncode, 1)
        self.assertIn('1 alert opened after the assessment', late.stderr)
        self.assertIn('#9', late.stderr)

    def test_render_refuses_missing_findings(self) -> None:
        data = self.write('assessment.json', build())
        run = self.write('run.json', RUN_RECORD)
        only_one = self.write('f.json', FINDINGS[0])
        result = self.mason('render-assessment', data, '--run', run, '--findings', only_one)
        self.assertEqual(result.returncode, 1)
        self.assertIn('dependabot-2026-11-urllib3', result.stderr)

    def test_comment_against_a_previous_assessment(self) -> None:
        before = self.write('before.json', build())
        after = self.write('after.json', build(alerts=ALERTS[:3], already_fixed=()))
        run = self.write('run.json', RUN_RECORD)
        result = self.mason('render-assessment-comment', after, '--run', run, '--previous', before)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('1 closed', result.stdout)


if __name__ == '__main__':
    unittest.main()
