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
        command = unattended.command(Path('/hub'), 'dependency-upgrades', ('Bash(docker *)',))
        self.assertEqual(
            command[:3], ['claude', '-p', unattended.WORKFLOWS['dependency-upgrades'].prompt]
        )
        self.assertIn('unattended', command[2])
        self.assertEqual(command[command.index('--add-dir') + 1], '/hub')
        self.assertEqual(command[command.index('--permission-mode') + 1], 'dontAsk')
        allowed = command[command.index('--allowedTools') + 1 : command.index('--disallowedTools')]
        self.assertIn('Bash(make *)', allowed)
        self.assertIn('Bash(docker *)', allowed)
        denied = command[command.index('--disallowedTools') + 1 :]
        self.assertIn('Bash(gh pr merge *)', denied)

    def test_retrospective_can_run_this_repositorys_checks(self) -> None:
        command = unattended.command(Path('/hub'), 'dependency-retrospective')
        self.assertIn('Bash(python3 -m unittest *)', command)
        self.assertNotIn('Bash(npm *)', command)

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
            code = 'import os; print(os.environ["AUTOMATION_UNATTENDED"], os.environ["AUTOMATION_TRIGGER"])'
            args = [sys.executable, '-c', code]
            status, log = unattended.run(
                Path(hub), Path(project), 'dependency-upgrades', args, 'systemd timer'
            )
            self.assertEqual(status, 0)
            self.assertEqual(log.read_text(encoding='utf-8'), '1 systemd timer\n')

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
                'AUTOMATION_UNATTENDED=1 AUTOMATION_TRIGGER=bin/unattended claude -p', result.stdout
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
