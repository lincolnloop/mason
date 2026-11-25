import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from artifacts import notes, paths, results, skills

from . import ROOT
from .test_report import FakeGh

REPO = 'o/app'
T1 = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)


class Clean(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        for home in (paths.data_home(), paths.config_home()):
            shutil.rmtree(home, ignore_errors=True)

    def write(self, text: str = '# Analysis\n\nWorth keeping.\n') -> Path:
        path = self.tmp / 'note.md'
        path.write_text(text)
        return path


class NoteTests(Clean):
    def test_save_list_show(self) -> None:
        key = notes.save(REPO, self.write(), 'Workflow orchestration!', tags=['design'], now=T1)
        self.assertEqual(key, '2026-11-25-workflow-orchestration')
        (note,) = notes.listing(REPO)
        self.assertEqual(note.meta['status'], 'draft')
        self.assertEqual(note.meta['tags'], ['design'])
        self.assertIn(
            f'  {key}  draft       Workflow orchestration!  [design]',
            notes.render_list(REPO, [note]),
        )
        shown = notes.show(note)
        self.assertTrue(shown.startswith('# Workflow orchestration!\n\n2026-11-25-workflow'))
        self.assertIn('Worth keeping.', shown)
        self.assertEqual(notes.listing(REPO, status='adopted'), [])
        self.assertEqual(notes.listing(REPO, tag='other'), [])

    def test_an_update_keeps_the_earlier_version(self) -> None:
        key = notes.save(REPO, self.write('first'), 'Plan', now=T1)
        notes.save(REPO, self.write('second'), update=key, tags=['x'], now=T2)
        note = notes.load(REPO, key)
        self.assertEqual((note.path / 'note.md').read_text(), 'second')
        self.assertEqual(note.meta['created'], '2026-11-25T09:00:00Z')
        self.assertEqual(note.meta['saved_at'], '2026-11-26T09:00:00Z')
        self.assertEqual(
            (note.path / 'previous' / '2026-11-25T090000Z' / 'note.md').read_text(), 'first'
        )

    def test_status(self) -> None:
        old = notes.save(REPO, self.write(), 'Old plan', now=T1)
        new = notes.save(REPO, self.write(), 'New plan', now=T1)
        notes.set_status(REPO, old, 'adopted', link='https://x/pull/1', now=T2)
        self.assertEqual(notes.load(REPO, old).meta['links'], ['https://x/pull/1'])
        notes.set_status(REPO, old, 'superseded', superseded_by=new)
        self.assertIn('Superseded by: ' + new, notes.show(notes.load(REPO, old)))
        for args, message in (
            ((old, 'superseded'), 'give --by'),
            ((old, 'superseded', None, 'missing'), 'no note missing'),
            ((old, 'done'), 'status must be one of'),
            (('missing', 'dropped'), 'no note missing'),
        ):
            with self.subTest(args=args), self.assertRaisesRegex(notes.NoteError, message):
                notes.set_status(REPO, *args)

    def test_refusals(self) -> None:
        notes.save(REPO, self.write(), 'Plan', now=T1)
        for kwargs, message in (
            ({'title': 'Plan', 'now': T1}, 'exists: give --update'),
            ({'title': ' '}, 'needs --title'),
            ({'title': 'X', 'status': 'done'}, 'status must be one of'),
            ({'update': 'missing'}, 'no note missing'),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(notes.NoteError, message):
                notes.save(REPO, self.write(), **kwargs)
        with self.assertRaisesRegex(notes.NoteError, 'is empty'):
            notes.save(REPO, self.write(' \n'), 'Empty')
        with self.assertRaisesRegex(notes.NoteError, 'does not exist'):
            notes.save(REPO, self.tmp / 'missing.md', 'Missing')
        self.assertEqual(notes.slug('!!!'), 'note')

    def test_published_like_any_result(self) -> None:
        key = notes.save(REPO, self.write(), 'Plan', now=T1)
        where = results.publish(
            notes.load(REPO, key), f'file:{self.tmp / "shared"}', FakeGh([], [])
        )
        self.assertTrue(Path(where).name.endswith(f'notes-{key}.md'))
        self.assertIn('notes/' + key, results.render(results.listing(REPO)))


class SkillTests(unittest.TestCase):
    def test_the_skill_is_not_a_workflow(self) -> None:
        skill = skills.read(ROOT / 'skills' / 'notes' / 'SKILL.md')
        self.assertIsNone(skill.workflow)
        self.assertNotIn('notes', skills.workflows())


class CliTests(Clean):
    def mason(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, '-I', str(ROOT / 'bin' / 'artifact'), *args],
            capture_output=True, text=True, cwd=cwd, check=False,
        )  # fmt: skip

    def test_in_a_checkout_and_by_name(self) -> None:
        checkout = self.tmp / 'app'
        subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
        git = ['git', '-C', str(checkout)]
        subprocess.run([*git, 'remote', 'add', 'origin', 'git@github.com:o/app.git'], check=True)
        result = self.mason(
            'note', 'save', str(self.write()), '--title', 'Plan', '--tag', 'x', cwd=checkout
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        key = result.stdout.split()[2]
        self.assertIn(key, self.mason('note', 'list', cwd=checkout).stdout)
        self.assertIn('Worth keeping.', self.mason('note', 'show', key, '--repo', REPO).stdout)
        result = self.mason(
            'note', 'status', key, 'adopted', '--repo', REPO, '--link', 'https://x/1'
        )
        self.assertEqual(result.stdout, f'{key}: adopted\n')
        meta = json.loads((paths.results(REPO) / 'notes' / key / 'meta.json').read_text())
        self.assertEqual(meta['links'], ['https://x/1'])
        result = self.mason('note', 'show', key, cwd=self.tmp)
        self.assertIn('give --repo', result.stderr)


if __name__ == '__main__':
    unittest.main()
