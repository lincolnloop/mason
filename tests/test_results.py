import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from artifacts import paths, project, results

from . import ROOT
from .fixtures import FINDING, RUN, make
from .test_report import FakeGh

REPO = RUN['project']
T1 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


class Clean(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        for home in (paths.data_home(), paths.config_home()):
            shutil.rmtree(home, ignore_errors=True)

    def run_dir(self, run_id: str = RUN['id']) -> Path:
        directory = self.tmp / run_id
        directory.mkdir()
        (directory / 'run.json').write_text(json.dumps(make(RUN, id=run_id)))
        (directory / 'body.md').write_text(f'# Report\n\nfrom {run_id}\n')
        (directory / 'finding.json').write_text(json.dumps(FINDING))
        return directory

    def save(self, run_dir: Path, now: datetime = T1) -> Path:
        return results.save(
            run_dir / 'run.json',
            '2026-10',
            [run_dir / 'finding.json'],
            run_dir / 'body.md',
            'October report',
            now=now,
        )


class SaveTests(Clean):
    def test_saved_with_its_run_and_what_to_publish(self) -> None:
        saved = self.save(self.run_dir())
        self.assertEqual(saved, paths.results(REPO) / 'dependency-upgrades' / '2026-10')
        self.assertEqual(
            sorted(p.name for p in saved.iterdir()),
            ['body.md', 'finding.json', 'meta.json', 'run.json'],
        )
        meta = json.loads((saved / 'meta.json').read_text())
        self.assertEqual(
            meta,
            {
                'workflow': 'dependency-upgrades',
                'key': '2026-10',
                'title': 'October report',
                'body': 'body.md',
                'saved_at': '2026-11-01T09:00:00Z',
                'run': RUN['id'],
            },
        )

    def test_a_later_run_keeps_the_earlier_version(self) -> None:
        self.save(self.run_dir())
        saved = self.save(self.run_dir('20261112-090000-dependency-upgrades'), T2)
        self.assertIn('20261112', (saved / 'body.md').read_text())
        kept = saved / 'previous' / RUN['id']
        self.assertIn(RUN['id'], (kept / 'body.md').read_text())
        self.assertEqual(
            json.loads((kept / 'meta.json').read_text())['saved_at'], '2026-11-01T09:00:00Z'
        )

    def test_refusals(self) -> None:
        run_dir = self.run_dir()
        (run_dir / 'bad.json').write_text(json.dumps(make(FINDING, severity='urgent')))
        (run_dir / 'meta.json').write_text('{}')
        (run_dir / 'other.json').write_text(json.dumps({'schema': 'run/1'}))
        (run_dir / 'run' / 'x').mkdir(parents=True)
        for files, kwargs, message in (
            ([run_dir / 'bad.json'], {}, r'bad.json: \d error'),
            ([run_dir / 'missing.md'], {}, 'does not exist'),
            ([run_dir / 'meta.json'], {}, 'a name the result keeps'),
            (
                [run_dir / 'body.md', run_dir / 'run' / 'x' / '..' / '..' / 'body.md'],
                {},
                'same name',
            ),
            ([], {'title': ' '}, '--title is empty'),
            ([], {'key': '../x'}, 'letters, digits'),
            ([], {'run_file': run_dir / 'finding.json'}, 'not a run record'),
            ([], {'run_file': run_dir / 'other.json'}, 'project: is required'),
        ):
            arguments = {
                'run_file': run_dir / 'run.json',
                'key': '2026-10',
                'files': files,
                'body': run_dir / 'body.md',
                'title': 'T',
                **kwargs,
            }
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(results.ResultError, message),
            ):
                results.save(**arguments)
        self.assertFalse(paths.results(REPO).exists())


class PublishTests(Clean):
    def test_file_copies_are_replaced_and_recorded(self) -> None:
        result = results.load(REPO, 'dependency-upgrades', self.save(self.run_dir()).name)
        folder = self.tmp / 'shared'
        where = results.publish(result, f'file:{folder}', FakeGh([], []), now=T1)
        self.assertEqual(Path(where).name, 'example-project-dependency-upgrades-2026-10.md')
        self.assertIn('# Report', Path(where).read_text())
        self.save(self.run_dir('20261112-090000-dependency-upgrades'), T2)
        result = results.load(REPO, 'dependency-upgrades', '2026-10')
        self.assertEqual(len(result.stale()), 1)
        self.assertIn('older than the master', results.render([result]))
        results.publish(result, f'file:{folder}', FakeGh([], []), now=T2)
        result = results.load(REPO, 'dependency-upgrades', '2026-10')
        self.assertEqual((len(result.published), result.stale()), (1, []))
        self.assertIn('20261112', Path(where).read_text())

    def test_discussion(self) -> None:
        result = results.load(REPO, 'dependency-upgrades', self.save(self.run_dir()).name)
        gh = FakeGh(['Reports'], [])
        self.assertEqual(
            results.publish(result, 'discussion', gh, category='Reports'), 'https://x/d/9'
        )
        self.assertEqual(gh.created['title'], 'October report')
        self.assertIn('discussion: https://x/d/9', results.render(results.listing(REPO)))
        with self.assertRaisesRegex(results.ResultError, 'already "October report"'):
            found = [
                {'title': 'October report', 'url': 'https://x/d/9', 'category': {'name': 'Reports'}}
            ]
            results.publish(result, 'discussion:Reports', FakeGh(['Reports'], found))

    def test_bad_targets(self) -> None:
        result = results.load(REPO, 'dependency-upgrades', self.save(self.run_dir()).name)
        for target, message in (
            ('slack:#x', 'a target is'),
            ('file:', 'name the directory'),
            ('discussion', 'name the category'),
        ):
            with self.subTest(target=target), self.assertRaisesRegex(results.ResultError, message):
                results.publish(result, target, FakeGh([], []))

    def test_listing_and_removing(self) -> None:
        self.assertEqual(results.render(results.listing(REPO)), 'No results saved yet.\n')
        result = results.load(REPO, 'dependency-upgrades', self.save(self.run_dir()).name)
        self.assertIn('not published', results.render(results.listing(REPO, 'dependency-upgrades')))
        self.assertEqual(results.listing(REPO, 'code-review'), [])
        results.publish(result, f'file:{self.tmp / "x"}', FakeGh([], []))
        message = results.remove(results.load(REPO, 'dependency-upgrades', '2026-10'))
        self.assertIn('published copies are left in place', message)
        with self.assertRaisesRegex(results.ResultError, 'no result'):
            results.load(REPO, 'dependency-upgrades', '2026-10')


class CliTests(Clean):
    def mason(self, *args: str, stdin: str = '') -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, '-I', str(ROOT / 'bin' / 'artifact'), *args],
            capture_output=True, text=True, input=stdin, check=False,
        )  # fmt: skip

    def test_save_publish_list_remove(self) -> None:
        run_dir = self.run_dir()
        result = self.mason(
            'save-result', str(run_dir / 'finding.json'), '--run', str(run_dir / 'run.json'),
            '--key', '2026-10', '--title', 'October', '--body', str(run_dir / 'body.md'),
        )  # fmt: skip
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = paths.settings(REPO)
        settings.parent.mkdir(parents=True)
        folder = self.tmp / 'reports'
        settings.write_text(f"version = 1\n[dependency-report]\npublish = ['file:{folder}']\n")
        self.assertEqual(project.value(REPO, 'dependency-report', 'publish'), [f'file:{folder}'])
        self.assertEqual(project.value(REPO, 'dependency-report', 'category'), 'Dependency reports')
        result = self.mason(
            'publish', 'dependency-upgrades/2026-10', '--repo', REPO, '--to', f'file:{folder}'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(f'file:{folder}: {folder}/'))
        result = self.mason('results', '--repo', REPO)
        self.assertIn('dependency-upgrades/2026-10  October', result.stdout)
        result = self.mason('publish', 'dependency-upgrades', '--repo', REPO)
        self.assertIn('give <workflow>/<key>', result.stderr)
        result = self.mason(
            'results', '--repo', REPO, '--remove', 'dependency-upgrades/2026-10', stdin='n\n'
        )
        self.assertEqual(result.stdout, 'Nothing deleted.\n')
        result = self.mason(
            'results', '--repo', REPO, '--remove', 'dependency-upgrades/2026-10', '--yes'
        )
        self.assertIn('Removed dependency-upgrades/2026-10', result.stdout)

    def test_an_empty_publish_setting_keeps_the_result_only(self) -> None:
        run_dir = self.run_dir()
        results.save(run_dir / 'run.json', '2026-10', [], run_dir / 'body.md', 'T')
        settings = paths.settings(REPO)
        settings.parent.mkdir(parents=True)
        settings.write_text('version = 1\n[dependency-upgrades]\n')
        # dependency-upgrades declares no `publish`: nothing is published by default.
        result = self.mason('publish', 'dependency-upgrades/2026-10', '--repo', REPO)
        self.assertIn('kept, not published', result.stdout)


if __name__ == '__main__':
    unittest.main()
