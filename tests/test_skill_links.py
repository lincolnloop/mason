import tempfile
import unittest
from pathlib import Path

from lib.artifacts import skill_links


class SkillLinksTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.hub = Path(tmp.name) / 'hub'
        for name in ('alpha', 'beta'):
            (self.hub / 'skills' / name).mkdir(parents=True)
            (self.hub / 'skills' / name / 'SKILL.md').write_text('---\nname: x\n---\n')
        # A directory without a SKILL.md is not a skill.
        (self.hub / 'skills' / 'notes').mkdir()
        self.target = Path(tmp.name) / 'home' / '.agents' / 'skills'

    def statuses(self, results: list[skill_links.Result]) -> dict[str, str]:
        return {r.name: r.status for r in results}

    def test_links_each_skill_and_creates_the_directory(self) -> None:
        results = skill_links.link(self.hub, self.target)
        self.assertEqual(self.statuses(results), {'alpha': 'linked', 'beta': 'linked'})
        self.assertTrue((self.target / 'alpha' / 'SKILL.md').is_file())
        # Resolving the link reaches the hub, so a skill can find the repository from itself.
        self.assertEqual((self.target / 'alpha').resolve().parent.parent, self.hub.resolve())

    def test_linking_again_changes_nothing(self) -> None:
        skill_links.link(self.hub, self.target)
        results = skill_links.link(self.hub, self.target)
        self.assertEqual(self.statuses(results), {'alpha': 'already', 'beta': 'already'})

    def test_leaves_another_skill_of_the_same_name_alone(self) -> None:
        (self.target / 'alpha').mkdir(parents=True)
        (self.target / 'beta').symlink_to(self.hub)
        results = skill_links.link(self.hub, self.target)
        self.assertEqual(self.statuses(results), {'alpha': 'conflict', 'beta': 'conflict'})
        self.assertFalse((self.target / 'alpha').is_symlink())
        self.assertEqual((self.target / 'beta').readlink(), self.hub)

    def test_dry_run_changes_nothing(self) -> None:
        results = skill_links.link(self.hub, self.target, dry_run=True)
        self.assertEqual(self.statuses(results), {'alpha': 'linked', 'beta': 'linked'})
        self.assertFalse(self.target.exists())

    def test_remove_takes_only_this_repositorys_links(self) -> None:
        skill_links.link(self.hub, self.target)
        (self.target / 'beta').unlink()
        (self.target / 'beta').mkdir()
        results = skill_links.unlink(self.hub, self.target)
        self.assertEqual(self.statuses(results), {'alpha': 'removed', 'beta': 'conflict'})
        self.assertFalse((self.target / 'alpha').exists())
        self.assertTrue((self.target / 'beta').is_dir())

    def test_remove_when_nothing_is_linked(self) -> None:
        results = skill_links.unlink(self.hub, self.target)
        self.assertEqual(self.statuses(results), {'alpha': 'absent', 'beta': 'absent'})


if __name__ == '__main__':
    unittest.main()
