import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from artifacts import paths, runner, skills, workspace

from . import HOME, ROOT

STATE = Path('/state/o/app')
SKILLS = Path('/data/mason')


def mason(*args: str, cwd: Path | None = None, env: dict | None = None):
    return subprocess.run(
        [sys.executable, '-I', str(ROOT / 'bin' / 'artifact'), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env=env,
    )


class PromptTests(unittest.TestCase):
    def test_the_request_and_the_mode(self) -> None:
        self.assertEqual(
            runner.prompt('dependency-report'), 'Run the dependency report for this project.'
        )
        self.assertEqual(
            runner.prompt('dependency-report', request='for October 2026', unattended=True),
            'Run the dependency report for this project (for October 2026), unattended.',
        )

    def test_code_review_needs_a_pr(self) -> None:
        with self.assertRaisesRegex(ValueError, 'give --pr'):
            runner.prompt('code-review')
        self.assertIn('pull request #7', runner.prompt('code-review', pr=7))


class HeadlessCommandTests(unittest.TestCase):
    def test_command(self) -> None:
        command = runner.command(
            Path('/work/app'), STATE, 'dependency-upgrades', ('Bash(docker *)',)
        )
        self.assertEqual(command[:2], ['claude', '-p'])
        self.assertIn('unattended', command[2])
        self.assertIn('The project ($PROJECT) is /work/app', command[2])
        self.assertIn('its runs are kept under /state/o/app', command[2])
        self.assertIn('redirect output to a file', command[2])
        self.assertEqual(command[command.index('--add-dir') + 1], '/state/o/app')
        self.assertEqual(command[command.index('--permission-mode') + 1], 'dontAsk')
        allowed = command[command.index('--allowedTools') + 1 : command.index('--disallowedTools')]
        self.assertIn('Bash(make *)', allowed)
        self.assertIn('Bash(docker *)', allowed)
        denied = command[command.index('--disallowedTools') + 1 :]
        self.assertIn('Bash(gh pr merge *)', denied)

    def test_skills_kept_for_mason_are_added(self) -> None:
        command = runner.command(Path('/work/app'), STATE, 'dependency-report', skills=SKILLS)
        added = [command[i + 1] for i, arg in enumerate(command) if arg == '--add-dir']
        self.assertEqual(added, ['/state/o/app', '/data/mason'])

    def test_retrospective_can_run_masons_checks(self) -> None:
        command = runner.command(Path('/work/app'), STATE, 'dependency-retrospective')
        self.assertIn('Bash(uv run --locked python -m unittest *)', command)
        self.assertNotIn('Bash(npm *)', command)

    def test_only_the_installed_mason_runs_python(self) -> None:
        command = runner.command(Path('/work/app'), STATE, 'dependency-upgrades')
        self.assertIn('Bash(mason *)', command)
        self.assertFalse([tool for tool in command if tool.startswith('Bash(python')])

    def test_code_review_runs_nothing_from_the_pr(self) -> None:
        command = runner.command(Path('/work/app'), STATE, 'code-review', pr=7)
        for tool in ('Bash(make *)', 'Bash(npm *)', 'Bash(uv run --locked python -m unittest *)'):
            self.assertNotIn(tool, command)

    def test_doctor_command_checks_the_workflow(self) -> None:
        self.assertEqual(
            runner.doctor_command('sentry-errors'),
            ['mason', 'doctor', '--workflow', 'sentry-errors', '--unattended'],
        )

    def test_sentry_workflows_declare_their_token(self) -> None:
        for name in ('sentry-errors', 'sentry-retrospective'):
            self.assertIn('SENTRY_AUTH_TOKEN', skills.workflows()[name].env)
        self.assertEqual(skills.workflows()['code-review'].env, ())


class InteractiveCommandTests(unittest.TestCase):
    def test_starts_the_agent_with_the_request_typed_in(self) -> None:
        command = runner.interactive_command(
            Path('/work/app'), STATE, 'dependency-report', SKILLS, request='for October 2026'
        )
        self.assertEqual(command[0], 'claude')
        self.assertTrue(command[1].startswith('Run the dependency report for this project'))
        self.assertIn('(for October 2026)', command[1])
        self.assertNotIn('unattended', command[1])
        self.assertNotIn('-p', command)
        self.assertNotIn('--permission-mode', command)  # the person's own settings apply
        self.assertEqual(command[2:], ['--add-dir', '/state/o/app', '--add-dir', '/data/mason'])

    def test_without_skills_kept_for_mason(self) -> None:
        command = runner.interactive_command(Path('/work/app'), STATE, 'dependency-report')
        self.assertEqual(command[2:], ['--add-dir', '/state/o/app'])


class EnvironmentTests(unittest.TestCase):
    def test_marked_unattended_without_pythonpath(self) -> None:
        env = runner.environment('timer', {'PATH': '/bin', 'PYTHONPATH': '/project'})
        self.assertEqual(env, {'PATH': '/bin', 'MASON_UNATTENDED': '1', 'MASON_TRIGGER': 'timer'})

    def test_drops_masons_own_virtualenv(self) -> None:
        env = runner.environment(
            'timer', {'PATH': '/uv/env/bin:/usr/bin', 'VIRTUAL_ENV': '/uv/env'}
        )
        self.assertNotIn('VIRTUAL_ENV', env)
        self.assertEqual(env['PATH'], '/usr/bin')

    def test_log_path(self) -> None:
        log = runner.log_path(
            Path('/state/o/app/logs'),
            'dependency-upgrades',
            datetime(2026, 11, 11, 9, 30, tzinfo=UTC),
        )
        self.assertEqual(log, Path('/state/o/app/logs/20261111-093000-dependency-upgrades.log'))

    def test_run_sets_the_environment_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as logs, tempfile.TemporaryDirectory() as project:
            code = 'import os; e = os.environ; print(e["MASON_UNATTENDED"], e["MASON_TRIGGER"])'
            status, log = runner.run(
                Path(logs),
                Path(project),
                'dependency-upgrades',
                [sys.executable, '-c', code],
                'systemd timer',
            )
            self.assertEqual(status, 0)
            self.assertEqual(log.read_text(encoding='utf-8'), '1 systemd timer\n')


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.calls: list[list[str]] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def fake(self, fail: str | None = None):
        def run(command, **kwargs):
            self.calls.append(command)
            if fail:
                raise subprocess.CalledProcessError(1, command, stderr=fail)
            if command[:3] == ['gh', 'repo', 'clone']:
                (Path(command[4]) / '.git').mkdir(parents=True)
            return subprocess.CompletedProcess(command, 0, '', '')

        return run

    def test_clones_then_fetches(self) -> None:
        target = self.tmp / 'repos' / 'o' / 'app'
        self.assertIn('cloned', workspace.ensure_clone('o/app', target, self.fake()))
        self.assertEqual(self.calls[0][:4], ['gh', 'repo', 'clone', 'o/app'])
        self.assertIn('fetched', workspace.ensure_clone('o/app', target, self.fake()))
        self.assertEqual(self.calls[1][3:], ['fetch', '--prune', '--quiet', 'origin'])

    def test_will_not_clone_over_something_else(self) -> None:
        target = self.tmp / 'busy'
        target.mkdir()
        (target / 'notes.txt').write_text('mine')
        with self.assertRaisesRegex(workspace.WorkspaceError, 'is not a clone'):
            workspace.ensure_clone('o/app', target, self.fake())
        self.assertEqual(self.calls, [])

    def test_failures_are_reported(self) -> None:
        with self.assertRaisesRegex(workspace.WorkspaceError, 'gh repo clone failed: not found'):
            workspace.ensure_clone('o/app', self.tmp / 'x', self.fake(fail='not found'))


class RunCommandTests(unittest.TestCase):
    """`mason run`, through the command line, without starting an agent (--dry-run)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        git = ['git', '-C', str(self.project)]
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        subprocess.run([*git, 'remote', 'add', 'origin', 'git@github.com:o/app.git'], check=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        shutil.rmtree(paths.data_home(), ignore_errors=True)

    def test_unattended_dry_run(self) -> None:
        result = mason(
            'run',
            'dependency-upgrades',
            '--project',
            str(self.project),
            '--unattended',
            '--dry-run',
            '--trigger',
            'timer',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('MASON_UNATTENDED=1 MASON_TRIGGER=timer claude -p', result.stdout)
        self.assertIn(str(paths.project_state('o/app')), result.stdout)

    def test_interactive_dry_run_in_the_current_directory(self) -> None:
        result = mason(
            'run', 'dependency-report', '--request', 'for 2026-10', '--dry-run', cwd=self.project
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            'and follow it. Run the dependency report for this project (for 2026-10).',
            result.stdout,
        )
        self.assertNotIn(str(paths.data_home()), result.stdout)

    def test_the_session_gets_its_own_skills_directory(self) -> None:
        (paths.data_home() / '.claude' / 'skills').mkdir(parents=True)
        result = mason('run', 'dependency-report', '--project', str(self.project), '--dry-run')
        sessions = paths.project_state('o/app') / 'sessions'
        self.assertIn(f'--add-dir {sessions}/', result.stdout)
        self.assertIn(f'Read {sessions}/', result.stdout)
        self.assertNotIn(f'--add-dir {paths.data_home()}', result.stdout)
        self.assertFalse(sessions.exists())  # a dry run stages nothing

    def test_by_repository_uses_masons_own_clone(self) -> None:
        result = mason('run', 'dependency-report', '--repo', 'o/app', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'cd {paths.clone("o/app")}', result.stdout)
        self.assertFalse(paths.clone('o/app').exists())  # a dry run clones nothing

    def test_refusals(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            result = mason('run', 'dependency-report', '--project', empty, '--dry-run')
            self.assertEqual(result.returncode, 1)
            self.assertIn('is not the root of a git repository', result.stderr)
        result = mason('run', 'code-review', '--project', str(self.project), '--dry-run')
        self.assertIn('give --pr', result.stderr)
        result = mason('run', 'dependency-report', '--repo', 'not-a-repo', '--dry-run')
        self.assertIn('owner/name', result.stderr)
        subprocess.run(['git', '-C', str(self.project), 'remote', 'remove', 'origin'], check=True)
        result = mason('run', 'dependency-report', '--project', str(self.project), '--dry-run')
        self.assertIn('no GitHub origin remote', result.stderr)


class SkillsCommandTests(unittest.TestCase):
    """`mason skills`, with a fake `npx` that records how it was called."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        bin_dir = Path(self._tmp.name)
        self.record = bin_dir / 'calls'
        npx = bin_dir / 'npx'
        npx.write_text(f'#!/bin/sh\necho "$(pwd)|$DISABLE_TELEMETRY|$*" >> {self.record}\n')
        npx.chmod(npx.stat().st_mode | stat.S_IEXEC)
        self.env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}', HOME=str(HOME))

    def tearDown(self) -> None:
        self._tmp.cleanup()
        shutil.rmtree(paths.data_home(), ignore_errors=True)

    def calls(self) -> list[list[str]]:
        return [line.split('|') for line in self.record.read_text().splitlines()]

    def test_install_for_mason_sessions_only(self) -> None:
        result = mason('skills', 'install', 'org/mason@v1', env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('mason run sessions only', result.stdout)
        ((cwd, telemetry, args),) = self.calls()
        self.assertEqual(Path(cwd), paths.data_home())
        self.assertEqual(telemetry, '1')
        self.assertIn('add org/mason@v1 -y -a claude-code --skill *', args)
        self.assertNotIn('-g', args.split())

    def test_install_globally(self) -> None:
        mason('skills', 'install', 'org/mason@v1', '--global', '--agent', 'codex', env=self.env)
        ((cwd, _, args),) = self.calls()
        self.assertEqual(Path(cwd), HOME)
        self.assertEqual(args.split()[-1], '-g')
        self.assertIn('-a codex', args)


if __name__ == '__main__':
    unittest.main()
