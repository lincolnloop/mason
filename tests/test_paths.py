import unittest
from pathlib import Path

from artifacts import paths

from . import HOME


class PathsTests(unittest.TestCase):
    def test_xdg_variables_are_followed(self) -> None:
        env = {
            'XDG_CONFIG_HOME': '/c',
            'XDG_STATE_HOME': '/s',
            'XDG_CACHE_HOME': '/k',
            'HOME': '/h',
        }
        self.assertEqual(paths.settings('org/app', env), Path('/c/mason/projects/org/app.toml'))
        self.assertEqual(paths.runs('org/app', env), Path('/s/mason/org/app/runs'))
        self.assertEqual(paths.logs('org/app', env), Path('/s/mason/org/app/logs'))
        self.assertEqual(paths.cache_home(env), Path('/k/mason'))

    def test_the_usual_defaults_under_home(self) -> None:
        env = {'HOME': '/h'}
        self.assertEqual(
            paths.settings('org/app', env), Path('/h/.config/mason/projects/org/app.toml')
        )
        self.assertEqual(paths.project_state('org/app', env), Path('/h/.local/state/mason/org/app'))
        self.assertEqual(paths.cache_home(env), Path('/h/.cache/mason'))

    def test_a_relative_xdg_path_is_ignored(self) -> None:
        env = {'XDG_CONFIG_HOME': 'relative/dir', 'HOME': '/h'}
        self.assertEqual(paths.config_home(env), Path('/h/.config/mason'))

    def test_a_repository_must_be_owner_and_name(self) -> None:
        for bad in ('app', 'org/app/extra', '../app', 'org/..', 'org/a b', ''):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                paths.settings(bad, {'HOME': '/h'})


class IsolationTests(unittest.TestCase):
    """Every directory mason writes to points into the test run's temporary home, never the
    developer's real one."""

    def test_every_base_directory_is_isolated(self) -> None:
        for base in (
            paths.config_home(),
            paths.state_home(),
            paths.cache_home(),
            paths.data_home(),
        ):
            with self.subTest(base=base):
                self.assertTrue(base.is_relative_to(HOME), base)


if __name__ == '__main__':
    unittest.main()
