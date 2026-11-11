"""Run bin/artifact the way a skill does: as a subprocess under `python3 -I`."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from artifacts.embed import embed as embed_text

from . import ROOT
from .fixtures import CHANGE, FINDING, RUN, make

SCRIPT = ROOT / 'bin' / 'artifact'


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-I', str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, content: object) -> Path:
        path = self.tmp / name
        path.write_text(
            content if isinstance(content, str) else json.dumps(content), encoding='utf-8'
        )
        return path

    def test_valid_finding(self) -> None:
        result = run('validate', str(self.write('f.json', FINDING)), cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('valid finding/1', result.stdout)

    def test_invalid_finding_lists_errors(self) -> None:
        result = run(
            'validate', str(self.write('f.json', make(FINDING, kind='nope'))), cwd=self.tmp
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn('- $.kind: must be one of', result.stderr)

    def test_invalid_json(self) -> None:
        result = run('validate', str(self.write('f.json', '{"schema": ')), cwd=self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn('is not valid JSON', result.stderr)

    def test_missing_file(self) -> None:
        result = run('validate', str(self.tmp / 'absent.json'), cwd=self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn('does not exist', result.stderr)

    def test_unknown_schema(self) -> None:
        result = run('validate', str(self.write('x.json', {'schema': 'verdict/1'})), cwd=self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn('$.schema: must be one of', result.stderr)

    def test_a_planted_module_in_the_working_directory_is_not_imported(self) -> None:
        # -I keeps the working directory off sys.path, so a stray json.py there can't run.
        self.write('json.py', 'raise SystemExit("planted module imported")')
        result = run('validate', str(self.write('f.json', FINDING)), cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_render_change_and_extract_round_trip(self) -> None:
        body = self.tmp / 'body.md'
        result = run(
            'render-change',
            str(self.write('c.json', CHANGE)),
            '--findings',
            str(self.write('f.json', [FINDING])),
            '--run',
            str(self.write('run.json', RUN)),
            '-o',
            str(body),
            cwd=self.tmp,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run('extract', str(body), '--schema', 'change/1', cwd=self.tmp)
        self.assertEqual(json.loads(result.stdout), [CHANGE])

    def test_render_change_needs_the_findings_it_lists(self) -> None:
        other = make(FINDING, id='something-else')
        result = run(
            'render-change',
            str(self.write('c.json', CHANGE)),
            '--findings',
            str(self.write('f.json', other)),
            '--run',
            str(self.write('run.json', RUN)),
            cwd=self.tmp,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            'missing: dependabot-2026-11-pillow; not listed in the change: something-else',
            result.stderr,
        )

    def test_deps_commands_need_a_glob(self) -> None:
        result = run('deps-diff', '--base', 'HEAD', cwd=self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn('give at least one --pip or --npm glob', result.stderr)

    def test_render_change_needs_its_own_run(self) -> None:
        other_run = make(RUN, id='20261112-080000-dependency-upgrades')
        result = run(
            'render-change',
            str(self.write('c.json', CHANGE)),
            '--findings',
            str(self.write('f.json', [FINDING])),
            '--run',
            str(self.write('run.json', other_run)),
            cwd=self.tmp,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn('but', result.stderr)
        self.assertIn('20261112-080000-dependency-upgrades', result.stderr)

    def test_render_stopped(self) -> None:
        alerts = [
            {
                'number': n,
                'state': 'open',
                'dependency': {'package': {'ecosystem': 'pip', 'name': name}},
                'security_advisory': {'severity': severity},
            }
            for n, name, severity in ((7, 'pillow', 'high'), (12, 'Django', 'moderate'))
        ]
        pr_body = '\n'.join([embed_text(CHANGE), embed_text(FINDING)])
        result = run(
            'render-stopped',
            str(self.write('run.json', RUN)),
            '--pr',
            '40',
            '--pr-body',
            str(self.write('body.md', pr_body)),
            '--alerts',
            str(self.write('alerts.json', [alerts])),
            cwd=self.tmp,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('1 open alert is not covered by this PR', result.stdout)
        self.assertIn('| Medium | #12 | `django` |', result.stdout)
        # pillow's alert 7 is cited by the PR's finding, so it is covered.
        self.assertNotIn('#7', result.stdout.split('<!--')[0])


if __name__ == '__main__':
    unittest.main()
