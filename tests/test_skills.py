import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from artifacts import doctor, paths, runner, skills, version

from . import HOME, ROOT

SKILL = """---
name: {name}
description: 'Used when asked to "do it", or ''it'' for short.'
version: 1.2.0
metadata:
  mason-requires: '{requires}'
  mason-task: 'Do {name} for this project'
---

# {name}
"""


def install(directory: Path, name: str, requires: str = '>=0.1') -> Path:
    path = directory / name / 'SKILL.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    text = SKILL.format(name=name, requires=requires)
    if not requires:
        text = text.replace("  mason-requires: ''\n", '')
    path.write_text(text)
    return path


class FrontmatterTests(unittest.TestCase):
    def test_reads_scalars_and_the_metadata_map(self) -> None:
        data = skills.frontmatter(SKILL.format(name='x', requires='>=0.2,<0.3'))
        self.assertEqual(data['name'], 'x')
        self.assertEqual(data['description'], 'Used when asked to "do it", or \'it\' for short.')
        self.assertEqual(data['version'], '1.2.0')
        self.assertEqual(
            data['metadata'],
            {'mason-requires': '>=0.2,<0.3', 'mason-task': 'Do x for this project'},
        )

    def test_malformed(self) -> None:
        for text, message in (
            ('# no frontmatter\n', 'has no frontmatter'),
            ('---\nname: x\n', 'not closed'),
            ('---\nname x\n---\n', 'cannot read'),
            ('---\n  indented: x\n---\n', 'outside a map'),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(skills.SkillError, message):
                skills.frontmatter(text)

    def test_every_workflow_skill_states_a_range_this_mason_meets(self) -> None:
        engine = version.package_version()
        for name in skills.workflows():
            with self.subTest(skill=name):
                skill = skills.read(ROOT / 'skills' / name / 'SKILL.md')
                self.assertEqual(skill.name, name)
                self.assertIsNotNone(skill.version)
                self.assertIsNone(skill.problem(engine))


class DeclarationTests(unittest.TestCase):
    def declare(self, **metadata: str) -> skills.Skill:
        lines = ''.join(f"  {k.replace('_', '-')}: '{v}'\n" for k, v in metadata.items())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x' / 'SKILL.md'
            path.parent.mkdir()
            path.write_text(f'---\nname: x\nmetadata:\n{lines}---\n')
            return skills.read(path)

    def test_a_workflow(self) -> None:
        skill = self.declare(
            mason_task='Review #{pr}',
            mason_inputs='pr',
            mason_tools='Bash(make *), Bash(npm *)',
            mason_env='A_TOKEN',
            mason_github='alerts, discussions',
        )
        expected = skills.Workflow(
            'Review #{pr}',
            ('Bash(make *)', 'Bash(npm *)'),
            True,
            ('A_TOKEN',),
            ('alerts', 'discussions'),
        )
        self.assertEqual(skill.workflow, expected)
        self.assertIsNone(self.declare(mason_requires='>=0.1').workflow)

    def test_malformed(self) -> None:
        for metadata, message in (
            ({'mason_task': 'Review #{pr}'}, 'exactly when'),
            ({'mason_task': 'Review', 'mason_inputs': 'pr'}, 'exactly when'),
            ({'mason_task': 'Do', 'mason_inputs': 'month'}, 'unknown month'),
            ({'mason_task': 'Do', 'mason_github': 'issues'}, 'unknown issues'),
        ):
            with (
                self.subTest(metadata=metadata),
                self.assertRaisesRegex(skills.SkillError, message),
            ):
                self.declare(**metadata)

    def test_the_checkout_comes_after_what_is_installed(self) -> None:
        env = dict(os.environ, MASON_SKILLS_PATH=f'/a{os.pathsep}/b')
        self.assertEqual(skills.directories(env)[-2:], [Path('/a'), Path('/b')])
        self.assertIn('code-review', skills.workflows())  # this checkout's, in the tests
        with self.assertRaisesRegex(ValueError, 'nope is not an installed workflow'):
            runner.declared('nope')


class RequirementTests(unittest.TestCase):
    def test_satisfies(self) -> None:
        self.assertTrue(skills.satisfies('0.2.0', '>=0.2,<0.3'))
        self.assertTrue(skills.satisfies('0.2.9', '>=0.2, <0.3'))
        self.assertFalse(skills.satisfies('0.3.0', '>=0.2,<0.3'))
        self.assertFalse(skills.satisfies('0.1.9', '>=0.2'))
        self.assertTrue(skills.satisfies('1.0', '==1.0.0'))
        self.assertTrue(skills.satisfies('0.2.0.dev1', '>0.1'))

    def test_malformed(self) -> None:
        for engine, requirement in (('0.2', '~=0.2'), ('0.2', '>=x'), ('dev', '>=0.1')):
            with self.subTest(requirement=requirement), self.assertRaises(skills.SkillError):
                skills.satisfies(engine, requirement)

    def test_problems(self) -> None:
        skill = skills.Skill('x', Path('/s/x/SKILL.md'), '1.0', '>=0.3')
        self.assertIn('needs mason >=0.3, and this is mason 0.2.0', skill.problem('0.2.0'))
        self.assertIn('same release tag', skill.problem('0.2.0'))
        self.assertIsNone(skill.problem('unknown'))  # a checkout that isn't installed
        unstated = skills.Skill('x', Path('/s/x/SKILL.md'), '1.0', None)
        self.assertIn('states no mason-requires', unstated.problem('0.2.0'))


class WithoutTheCheckout(unittest.TestCase):
    """Only what a test installs: not this checkout's skills (MASON_SKILLS_PATH)."""

    def setUp(self) -> None:
        self._path = os.environ.pop('MASON_SKILLS_PATH', None)

    def tearDown(self) -> None:
        if self._path is not None:
            os.environ['MASON_SKILLS_PATH'] = self._path


class InstalledTests(WithoutTheCheckout):
    def tearDown(self) -> None:
        super().tearDown()
        shutil.rmtree(paths.data_home(), ignore_errors=True)
        shutil.rmtree(HOME / '.claude', ignore_errors=True)

    def test_masons_own_copy_is_found_first(self) -> None:
        mine, everywhere = skills.directories()[0], skills.directories()[2]
        self.assertEqual(everywhere, HOME / '.claude' / 'skills')
        install(everywhere, 'code-review', '>=9')
        other = everywhere / 'not-a-workflow' / 'SKILL.md'
        other.parent.mkdir()
        other.write_text('---\nname: not-a-workflow\ndescription: >\n  folded: text\n---\n')
        (everywhere / 'notes' / 'SKILL.md').parent.mkdir()
        (everywhere / 'notes' / 'SKILL.md').write_text('---\nname: notes\n---\n')
        self.assertEqual([s.name for s in skills.installed()], ['code-review'])
        with self.assertRaisesRegex(skills.SkillError, 'needs mason >=9'):
            skills.check('code-review')
        install(mine, 'code-review')
        self.assertEqual(skills.find('code-review').path.parent.parent, mine)
        self.assertEqual(skills.check('code-review').version, '1.2.0')
        self.assertIsNone(skills.check('sentry-errors'))  # not installed: nothing to check

    def test_doctor_reports_each_skill(self) -> None:
        self.assertEqual(doctor.check_skills([], '0.2.0')[0].status, doctor.WARN)
        install(skills.directories()[0], 'code-review')
        install(skills.directories()[0], 'sentry-errors', '>=9')
        checks = doctor.check_skills(skills.installed(), '0.2.0', 'dependency-report')
        by_name = {c.name: c for c in checks}
        self.assertEqual(by_name['skill code-review'].status, doctor.OK)
        self.assertIn('1.2.0, in', by_name['skill code-review'].detail)
        self.assertEqual(by_name['skill sentry-errors'].status, doctor.FAIL)
        self.assertEqual(by_name['skill dependency-report'].detail, 'not installed')


class CliTests(WithoutTheCheckout):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        git = ['git', '-C', str(self.project)]
        subprocess.run([*git, 'remote', 'add', 'origin', 'git@github.com:o/app.git'], check=True)
        # run-start needs the main branch, from origin/HEAD.
        subprocess.run(
            [*git, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main'],
            check=True,
        )

    def tearDown(self) -> None:
        super().tearDown()
        self._tmp.cleanup()
        for home in (paths.data_home(), paths.state_home()):
            shutil.rmtree(home, ignore_errors=True)

    def mason(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, '-I', str(ROOT / 'bin' / 'artifact'), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_run_refuses_a_skill_for_another_mason(self) -> None:
        install(skills.directories()[0], 'dependency-report', '>=9')
        result = self.mason('run', 'dependency-report', '--project', str(self.project), '--dry-run')
        self.assertEqual(result.returncode, 1)
        self.assertIn('needs mason >=9', result.stderr)
        self.assertIn('dependency-report not started', result.stderr)

    def test_run_start_records_the_versions(self) -> None:
        install(skills.directories()[0], 'dependency-report')
        result = self.mason('run-start', str(self.project), 'dependency-report')
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((Path(json.loads(result.stdout)['dir']) / 'run.json').read_text())
        self.assertEqual(record['produced_by']['skill_version'], '1.2.0')
        self.assertEqual(record['produced_by']['mason_version'], version.package_version())


if __name__ == '__main__':
    unittest.main()
