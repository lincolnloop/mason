import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from artifacts import version

SHA = 'a' * 40


class VersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_checkout_uses_its_commit(self) -> None:
        (self.tmp / '.git').mkdir()
        answers = {'rev-parse': SHA, 'status': ''}
        sha = version.automation_sha(self.tmp, git=lambda _c, cmd, *a: answers[cmd])
        self.assertEqual(sha, SHA)

    def test_a_modified_checkout_is_dirty(self) -> None:
        (self.tmp / '.git').mkdir()
        answers = {'rev-parse': SHA, 'status': ' M lib/x.py'}
        sha = version.automation_sha(self.tmp, git=lambda _c, cmd, *a: answers[cmd])
        self.assertEqual(sha, SHA + '-dirty')

    def test_git_failing_is_reported(self) -> None:
        (self.tmp / '.git').mkdir()

        def broken(*args: str) -> str:
            raise subprocess.CalledProcessError(128, 'git', stderr='not a repository')

        with self.assertRaisesRegex(version.VersionError, 'not a repository'):
            version.automation_sha(self.tmp, git=broken)

    def test_an_install_from_git_uses_the_recorded_commit(self) -> None:
        recorded = json.dumps(
            {'url': 'https://github.com/o/mason', 'vcs_info': {'vcs': 'git', 'commit_id': SHA}}
        )
        self.assertEqual(version.automation_sha(self.tmp, direct_url=recorded), SHA)

    def test_an_install_without_a_commit_is_refused(self) -> None:
        for recorded in ('', 'not json', json.dumps({'url': 'file:///x', 'dir_info': {}}), '[]'):
            with (
                self.subTest(recorded=recorded),
                self.assertRaisesRegex(version.VersionError, 'uv tool install'),
            ):
                version.automation_sha(self.tmp, direct_url=recorded)


class SourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_checkout_uses_its_origin(self) -> None:
        (self.tmp / '.git').mkdir()
        url = version.source_url(self.tmp, git=lambda *a: 'git@github.com:o/mason.git')
        self.assertEqual(url, 'git@github.com:o/mason.git')

    def test_a_checkout_without_origin_is_refused(self) -> None:
        (self.tmp / '.git').mkdir()

        def no_remote(*args: str) -> str:
            raise subprocess.CalledProcessError(2, 'git', stderr='No such remote')

        with self.assertRaisesRegex(version.VersionError, 'no origin remote'):
            version.source_url(self.tmp, git=no_remote)

    def test_an_install_from_git_uses_its_url(self) -> None:
        recorded = json.dumps(
            {'url': 'git+https://github.com/o/mason', 'vcs_info': {'vcs': 'git', 'commit_id': SHA}}
        )
        self.assertEqual(
            version.source_url(self.tmp, direct_url=recorded), 'https://github.com/o/mason'
        )

    def test_an_install_not_from_git_is_refused(self) -> None:
        for recorded in ('', '{', json.dumps({'url': 'file:///x', 'dir_info': {}})):
            with self.subTest(recorded=recorded), self.assertRaises(version.VersionError):
                version.source_url(self.tmp, direct_url=recorded)

    def test_clone(self) -> None:
        origin = self.tmp / 'origin'
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(origin)], check=True)
        identity = ['-c', 'user.name=t', '-c', 'user.email=t@example.com']
        subprocess.run(
            ['git', '-C', str(origin), *identity, 'commit', '-q', '--allow-empty', '-m', 'x'],
            check=True,
        )
        target = self.tmp / 'clone'
        version.clone(str(origin), target)
        self.assertTrue((target / '.git').is_dir())
        with self.assertRaisesRegex(version.VersionError, 'already exists'):
            version.clone(str(origin), target)
        with self.assertRaisesRegex(version.VersionError, 'git clone .* failed'):
            version.clone(str(self.tmp / 'missing'), self.tmp / 'other')


if __name__ == '__main__':
    unittest.main()
