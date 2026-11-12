import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from artifacts import unattended

from . import ROOT


class UnattendedTests(unittest.TestCase):
    def test_command(self) -> None:
        command = unattended.command(
            Path('/hub'), Path('/work/app'), 'dependency-upgrades', ('Bash(docker *)',)
        )
        self.assertEqual(command[:2], ['claude', '-p'])
        self.assertIn('unattended', command[2])
        self.assertIn('the project ($PROJECT) is /work/app', command[2])
        self.assertIn('redirect output to a file', command[2])
        self.assertEqual(command[command.index('--add-dir') + 1], '/hub')
        self.assertEqual(command[command.index('--permission-mode') + 1], 'dontAsk')
        allowed = command[command.index('--allowedTools') + 1 : command.index('--disallowedTools')]
        self.assertIn('Bash(make *)', allowed)
        self.assertIn('Bash(docker *)', allowed)
        denied = command[command.index('--disallowedTools') + 1 :]
        self.assertIn('Bash(gh pr merge *)', denied)

    def test_retrospective_can_run_this_repositorys_checks(self) -> None:
        command = unattended.command(Path('/hub'), Path('/work/app'), 'dependency-retrospective')
        self.assertIn('Bash(uv run --locked python -m unittest *)', command)
        self.assertNotIn('Bash(npm *)', command)

    def test_only_this_repositorys_command_line_runs_python(self) -> None:
        command = unattended.command(Path('/hub'), Path('/work/app'), 'dependency-upgrades')
        self.assertIn('Bash(uv run --project /hub --locked python -I /hub/bin/artifact *)', command)
        self.assertFalse([tool for tool in command if tool.startswith('Bash(python')])

    def test_code_review_needs_a_pr_and_runs_nothing_from_it(self) -> None:
        with self.assertRaisesRegex(ValueError, 'give --pr'):
            unattended.command(Path('/hub'), Path('/work/app'), 'code-review')
        command = unattended.command(Path('/hub'), Path('/work/app'), 'code-review', pr=7)
        self.assertIn('pull request #7', command[2])
        for tool in ('Bash(make *)', 'Bash(npm *)', 'Bash(uv run --locked python -m unittest *)'):
            self.assertNotIn(tool, command)

    def test_doctor_command_is_offline_and_checks_the_workflow(self) -> None:
        self.assertEqual(
            unattended.doctor_command(Path('/hub'), 'sentry-errors'),
            [
                'uv', 'run', '--offline', '--project', '/hub', '--locked', 'python', '-I',
                '/hub/bin/artifact', 'doctor', '--workflow', 'sentry-errors', '--unattended',
            ],
        )  # fmt: skip

    def test_sentry_workflows_declare_their_token(self) -> None:
        for name in ('sentry-errors', 'sentry-retrospective'):
            self.assertIn('SENTRY_AUTH_TOKEN', unattended.WORKFLOWS[name].env)
        self.assertEqual(unattended.WORKFLOWS['code-review'].env, ())

    def test_environment_is_offline_without_pythonpath(self) -> None:
        env = unattended.environment('timer', {'PATH': '/bin', 'PYTHONPATH': '/project'})
        self.assertEqual(
            env,
            {
                'PATH': '/bin',
                'MASON_UNATTENDED': '1',
                'MASON_TRIGGER': 'timer',
                'UV_OFFLINE': '1',
            },
        )

    def test_environment_drops_the_runners_own_virtualenv(self) -> None:
        env = unattended.environment(
            'timer', {'PATH': '/uv/env/bin:/usr/bin', 'VIRTUAL_ENV': '/uv/env'}
        )
        self.assertNotIn('VIRTUAL_ENV', env)
        self.assertEqual(env['PATH'], '/usr/bin')

    def test_log_path(self) -> None:
        log = unattended.log_path(
            Path('/hub'),
            Path('/work/example-app'),
            'dependency-upgrades',
            datetime(2026, 11, 11, 9, 30, tzinfo=UTC),
        )
        self.assertEqual(log, Path('/hub/runs/example-app/20261111-093000-dependency-upgrades.log'))

    def test_run_sets_the_environment_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as hub, tempfile.TemporaryDirectory() as project:
            code = (
                'import os; e = os.environ; '
                'print(e["MASON_UNATTENDED"], e["MASON_TRIGGER"], e["UV_OFFLINE"])'
            )
            args = [sys.executable, '-c', code]
            status, log = unattended.run(
                Path(hub), Path(project), 'dependency-upgrades', args, 'systemd timer'
            )
            self.assertEqual(status, 0)
            self.assertEqual(log.read_text(encoding='utf-8'), '1 systemd timer 1\n')

    def test_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            subprocess.run(['git', 'init', '-q', project], check=True)
            result = subprocess.run(
                [
                    sys.executable,
                    '-I',
                    str(ROOT / 'bin' / 'unattended'),
                    'dependency-upgrades',
                    project,
                    '--dry-run',
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                'MASON_UNATTENDED=1 MASON_TRIGGER=bin/unattended UV_OFFLINE=1 claude -p',
                result.stdout,
            )

    def test_refuses_a_directory_that_is_not_a_repository(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            result = subprocess.run(
                [
                    sys.executable,
                    '-I',
                    str(ROOT / 'bin' / 'unattended'),
                    'dependency-upgrades',
                    project,
                    '--dry-run',
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn('is not the root of a git repository', result.stderr)


if __name__ == '__main__':
    unittest.main()
