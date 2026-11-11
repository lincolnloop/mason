import unittest

from artifacts import embed

from .fixtures import CHANGE, FINDING, make


class EmbedTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        text = f'Some PR text.\n\n{embed.embed(CHANGE)}\n\n{embed.embed(FINDING, "finding x")}\n'
        self.assertEqual(embed.extract(text), [CHANGE, FINDING])
        self.assertEqual(embed.extract(text, 'finding/1'), [FINDING])

    def test_strings_with_fences_survive(self) -> None:
        change = make(CHANGE, effect='Has ```code``` and\n```\nfenced\n``` text')
        self.assertEqual(embed.extract(embed.embed(change)), [change])

    def test_github_line_endings(self) -> None:
        text = embed.embed(FINDING).replace('\n', '\r\n')
        self.assertEqual(embed.extract(text), [FINDING])

    def test_collapsed_with_a_label(self) -> None:
        block = embed.embed(CHANGE)
        self.assertIn('<details>\n<summary>change.json</summary>', block)

    def test_nothing_embedded(self) -> None:
        self.assertEqual(embed.extract('just a PR description'), [])

    def test_damaged_block_raises(self) -> None:
        block = embed.embed(FINDING).replace('"kind"', '"kind" oops')
        with self.assertRaisesRegex(ValueError, 'not valid JSON'):
            embed.extract(block)

    def test_marker_must_match_schema(self) -> None:
        block = embed.embed(FINDING).replace('artifact finding/1', 'artifact change/1')
        with self.assertRaisesRegex(ValueError, 'marked change/1'):
            embed.extract(block)


if __name__ == '__main__':
    unittest.main()
