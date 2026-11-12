import os
import subprocess
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path

from artifacts import doctor

from . import ROOT


def which_of(*present: str):
    return lambda name: f'/usr/bin/{name}' if name in present else None


def runner(outputs: dict[str, tuple[int, str]]):
    def run(command: Sequence[str]) -> tuple[int, str]:
        return outputs.get(' '.join(command), (0, ''))

    return run


class ParseVersionTests(unittest.TestCase):
    def test_versions(self) -> None:
        self.assertEqual(doctor.parse_version('git version 2.43.0'), (2, 43, 0))
        self.assertEqual(doctor.parse_version('v22.20.1'), (22, 20, 1))
        self.assertEqual(doctor.parse_version('uv 0.9.18 (abc 2026-01-01)'), (0, 9, 18))
        self.assertEqual(doctor.parse_version('gh version 2.60'), (2, 60))

    def test_no_version(self) -> None:
        self.assertIsNone(doctor.parse_version(''))
        self.assertIsNone(doctor.parse_version('command not found'))


class ToolTests(unittest.TestCase):
    git = doctor.TOOLS[0]
    node = next(tool for tool in doctor.TOOLS if tool.name == 'node')

    def test_present(self) -> None:
        check = doctor.check_tool(
            self.git, which_of('git'), runner({'git --version': (0, 'git version 2.43.0\n')})
        )
        self.assertEqual((check.status, check.detail), (doctor.OK, 'git version 2.43.0'))

    def test_missing_required_tool_fails(self) -> None:
        check = doctor.check_tool(self.git, which_of(), runner({}))
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn('not found on PATH', check.detail)

    def test_missing_optional_tool_warns(self) -> None:
        check = doctor.check_tool(self.node, which_of(), runner({}))
        self.assertEqual(check.status, doctor.WARN)

    def test_version_command_failing(self) -> None:
        check = doctor.check_tool(
            self.git, which_of('git'), runner({'git --version': (1, 'broken')})
        )
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn('failed', check.detail)

    def test_too_old(self) -> None:
        check = doctor.check_tool(
            self.node, which_of('node'), runner({'node --version': (0, 'v20.11.0')})
        )
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn('20.11.0 found; 22.20 or later', check.detail)

    def test_unreadable_version_against_a_minimum(self) -> None:
        check = doctor.check_tool(
            self.node, which_of('node'), runner({'node --version': (0, 'nightly')})
        )
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn('an unknown version', check.detail)

    def test_new_enough(self) -> None:
        check = doctor.check_tool(
            self.node, which_of('node'), runner({'node --version': (0, 'v24.12.0')})
        )
        self.assertEqual(check.status, doctor.OK)


class OtherCheckTests(unittest.TestCase):
    def test_python(self) -> None:
        self.assertEqual(doctor.check_python((3, 12, 1)).status, doctor.OK)
        self.assertEqual(doctor.check_python((3, 11, 9)).status, doctor.FAIL)

    def test_environment(self) -> None:
        hub = Path('/hub')
        self.assertEqual(doctor.check_environment(hub, Path('/hub/.venv')).status, doctor.OK)
        check = doctor.check_environment(hub, Path('/usr'))
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn('uv run --project /hub --locked', check.detail)

    def test_gh_auth(self) -> None:
        ok = doctor.check_gh_auth(which_of('gh'), runner({'gh auth status': (0, '')}))
        self.assertEqual(ok.status, doctor.OK)
        out = doctor.check_gh_auth(which_of('gh'), runner({'gh auth status': (1, 'no')}))
        self.assertEqual(out.status, doctor.FAIL)
        self.assertEqual(doctor.check_gh_auth(which_of(), runner({})).status, doctor.FAIL)

    def test_tokens_never_show_their_values(self) -> None:
        checks = doctor.check_tokens(
            ['SENTRY_AUTH_TOKEN', 'OTHER'], {'SENTRY_AUTH_TOKEN': 's3cr3t'}
        )
        self.assertEqual([c.status for c in checks], [doctor.OK, doctor.FAIL])
        self.assertNotIn('s3cr3t', doctor.render(checks))

    def test_empty_token_counts_as_unset(self) -> None:
        checks = doctor.check_tokens(['SENTRY_AUTH_TOKEN'], {'SENTRY_AUTH_TOKEN': ''})
        self.assertEqual(checks[0].status, doctor.FAIL)

    def test_unattended_wants_a_claude_token(self) -> None:
        missing = doctor.check_tokens([], {}, unattended=True)
        self.assertEqual([c.status for c in missing], [doctor.WARN])
        present = doctor.check_tokens([], {'CLAUDE_CODE_OAUTH_TOKEN': 'x'}, unattended=True)
        self.assertEqual([c.status for c in present], [doctor.OK])
        self.assertEqual(doctor.check_tokens([], {}), [])

    def test_render_and_failed(self) -> None:
        checks = [
            doctor.Check('git', doctor.OK, 'git version 2.43.0'),
            doctor.Check('node', doctor.WARN, 'not found'),
            doctor.Check('gh auth', doctor.FAIL, 'not logged in'),
        ]
        text = doctor.render(checks)
        self.assertIn('fail  gh auth  not logged in', text)
        self.assertTrue(text.endswith('1 failed, 1 warnings\n'))
        self.assertTrue(doctor.failed(checks))
        self.assertFalse(doctor.failed(checks[:2]))


class CliTests(unittest.TestCase):
    def test_missing_workflow_token_exits_1_and_reports_everything(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != 'SENTRY_AUTH_TOKEN'}
        result = subprocess.run(
            [
                sys.executable,
                '-I',
                str(ROOT / 'bin' / 'artifact'),
                'doctor',
                '--workflow',
                'sentry-errors',
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn('fail  SENTRY_AUTH_TOKEN', result.stderr)
        self.assertIn('git', result.stderr)
        self.assertIn('failed,', result.stderr)

    def test_unknown_workflow_is_refused(self) -> None:
        result = subprocess.run(
            [sys.executable, '-I', str(ROOT / 'bin' / 'artifact'), 'doctor', '--workflow', 'nope'],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
