import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from artifacts import credentials, inbox, paths, project

REPO = 'acme/app'


class Clean(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        shutil.rmtree(paths.config_home(), ignore_errors=True)

    def write(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def project_settings(self, repo: str, credentials_table: str) -> Path:
        return self.write(paths.settings(repo), f'version = 1\n[credentials]\n{credentials_table}')


class ReadTests(Clean):
    def test_env_file(self) -> None:
        path = self.write(self.tmp / 'env', '# tokens\nexport A=1\nB=\'two words\'\n\nC="3"\n')
        self.assertEqual(credentials.read_env_file(path), {'A': '1', 'B': 'two words', 'C': '3'})
        with self.assertRaisesRegex(credentials.CredentialError, ':2: not a KEY=value'):
            credentials.read_env_file(self.write(self.tmp / 'bad', 'A=1\nnot a line\n'))
        with self.assertRaisesRegex(credentials.CredentialError, 'no such env file'):
            credentials.read_env_file(self.tmp / 'missing')

    def test_check(self) -> None:
        self.assertEqual(credentials.check({'github': 'gh:bot', 'git_name': 'mason'}), [])
        self.assertEqual(
            credentials.check({'github': 'vault:x', 'token': 'y', 'host': 3}),
            [
                'token: is not a known field',
                'host: must be a string',
                "github: is 'env:<VARIABLE>' or 'gh:<account>'",
            ],
        )
        self.assertEqual(credentials.check('x'), ['must be a table'])
        self.assertIn(
            '$.credentials: token: is not a known field',
            project.check({'credentials': {'token': 'x'}}),
        )

    def test_the_project_over_its_owner(self) -> None:
        self.write(
            credentials.owner_settings('acme'),
            "[credentials]\ngit_name = 'acme bot'\nhost = 'gh.acme.example'\n",
        )
        self.project_settings(REPO, "git_name = 'app bot'\n")
        found = credentials.load(REPO)
        self.assertEqual((found.get('git_name'), found.get('host')), ('app bot', 'gh.acme.example'))
        self.assertIn('host = gh.acme.example  (', found.describe())
        self.assertIn('the shared credentials', credentials.load('other/app').describe())


class EnvironmentTests(Clean):
    def test_no_credentials_leaves_the_environment(self) -> None:
        self.assertEqual(credentials.environment(REPO, base={'GH_TOKEN': 'x'}), {'GH_TOKEN': 'x'})

    def test_only_this_projects_tokens(self) -> None:
        acme = self.write(
            self.tmp / 'env.acme', 'ACME_GH_TOKEN=acme-token\nSENTRY_AUTH_TOKEN=acme-sentry\n'
        )
        other = self.write(
            self.tmp / 'env.other', 'OTHER_GH_TOKEN=other-token\nSENTRY_AUTH_TOKEN=other-sentry\n'
        )
        self.write(paths.config_home() / 'env', 'SHARED_TOKEN=shared\n')
        self.project_settings(
            REPO,
            f"env_file = '{acme}'\ngithub = 'env:ACME_GH_TOKEN'\ngit_name = 'acme bot'\ngit_email = 'bot@acme.example'\n",
        )
        self.project_settings('other/app', f"env_file = '{other}'\n")
        base = {
            'PATH': '/bin',
            'GH_TOKEN': 'mine',
            'GITHUB_TOKEN': 'mine',
            'OTHER_GH_TOKEN': 'other-token',
            'SHARED_TOKEN': 'shared',
            'SENTRY_AUTH_TOKEN': 'other-sentry',
        }
        found = credentials.environment(REPO, base=base)
        self.assertEqual(
            found,
            {
                'PATH': '/bin',
                'GH_TOKEN': 'acme-token',
                'ACME_GH_TOKEN': 'acme-token',
                'SENTRY_AUTH_TOKEN': 'acme-sentry',
                'GIT_AUTHOR_NAME': 'acme bot',
                'GIT_COMMITTER_NAME': 'acme bot',
                'GIT_AUTHOR_EMAIL': 'bot@acme.example',
                'GIT_COMMITTER_EMAIL': 'bot@acme.example',
            },
        )

    def test_a_gh_account_and_a_host(self) -> None:
        self.project_settings(REPO, "github = 'gh:acme-bot'\nhost = 'gh.acme.example'\n")
        asked = []

        def token(account: str, host: str | None) -> str:
            asked.append((account, host))
            return 'bot-token'

        found = credentials.environment(REPO, base={}, gh_token=token)
        self.assertEqual(asked, [('acme-bot', 'gh.acme.example')])
        self.assertEqual(
            found,
            {
                'GH_TOKEN': 'bot-token',
                'GH_HOST': 'gh.acme.example',
                'GH_ENTERPRISE_TOKEN': 'bot-token',
            },
        )

    def test_a_missing_token_is_refused(self) -> None:
        self.project_settings(REPO, "github = 'env:ACME_GH_TOKEN'\n")
        with self.assertRaisesRegex(credentials.CredentialError, 'ACME_GH_TOKEN is not set'):
            credentials.environment(REPO, base={})

    def test_activate(self) -> None:
        self.project_settings(REPO, "git_name = 'acme bot'\n")
        saved = dict(os.environ)
        try:
            credentials.activate(REPO)
            self.assertEqual(os.environ['GIT_AUTHOR_NAME'], 'acme bot')
        finally:
            os.environ.clear()
            os.environ.update(saved)


class TokenTests(unittest.TestCase):
    def fake(self, headers: str, status: int = 0):
        def run(command, **kwargs):
            body = '{"login": "acme-bot"}'
            return subprocess.CompletedProcess(
                command, status, f'HTTP/2.0 200 OK\r\n{headers}\r\n\r\n{body}', 'boom'
            )

        return run

    def test_classic_scopes_and_expiry(self) -> None:
        soon = (datetime.now(UTC) + timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S UTC')
        found = credentials.token_checks(
            {},
            self.fake(
                f'X-Oauth-Scopes: read:org\r\nGithub-Authentication-Token-Expiration: {soon}'
            ),
        )
        self.assertEqual(found[0], ('ok', 'acts as acme-bot'))
        self.assertEqual(found[1][0], 'fail')
        self.assertIn('without the repo scope', found[1][1])
        self.assertEqual(found[2][0], 'warn')

    def test_fine_grained_and_failures(self) -> None:
        found = credentials.token_checks({}, self.fake('Content-Type: application/json'))
        self.assertIn('fine-grained', found[1][1])
        self.assertEqual(
            credentials.token_checks({}, self.fake('', status=1))[0], ('fail', 'gh api user: boom')
        )


class InboxTests(Clean):
    def test_each_project_is_read_with_its_own_token(self) -> None:
        bin_dir = self.tmp / 'bin'
        bin_dir.mkdir()
        record = self.tmp / 'tokens'
        fake = bin_dir / 'gh'
        fake.write_text(f'#!/bin/sh\necho "$GH_TOKEN" >> {record}\necho "[]"\n')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        acme = self.write(self.tmp / 'env.acme', 'ACME=acme-token\n')
        self.project_settings(REPO, f"env_file = '{acme}'\ngithub = 'env:ACME'\n")
        self.write(paths.settings('other/app'), 'version = 1\n')
        env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}', GH_TOKEN='mine')
        saved = os.environ['PATH']
        os.environ['PATH'] = env['PATH']
        try:
            inbox.collect([REPO, 'other/app'], env=env, now=datetime(2026, 11, 25, tzinfo=UTC))
        finally:
            os.environ['PATH'] = saved
        self.assertEqual(record.read_text().split(), ['acme-token', 'acme-token', 'mine', 'mine'])


if __name__ == '__main__':
    unittest.main()
