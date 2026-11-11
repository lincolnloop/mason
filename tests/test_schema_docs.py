"""Every JSON example in schemas/*.md must validate, so the docs can't drift from the code.

On a page with a ```diff block, review examples are validated against that diff.
"""

import json
import re
import unittest

from artifacts import validate

from . import ROOT

BLOCK = re.compile(r'^```(json|diff)\n(.*?)^```$', re.DOTALL | re.MULTILINE)


class SchemaDocTests(unittest.TestCase):
    def test_examples_validate(self) -> None:
        pages = sorted((ROOT / 'schemas').glob('*.md'))
        self.assertTrue(pages)
        examples = 0
        for page in pages:
            blocks = BLOCK.findall(page.read_text(encoding='utf-8'))
            diffs = [body for kind, body in blocks if kind == 'diff']
            for kind, body in blocks:
                if kind != 'json':
                    continue
                examples += 1
                with self.subTest(page=page.name, example=body[:60]):
                    artifact = json.loads(body)
                    diff_text = diffs[0] if diffs and artifact.get('schema') == 'review/1' else None
                    self.assertEqual(validate(artifact, diff_text), [])
        self.assertGreaterEqual(examples, 9)

    def test_every_schema_has_a_page(self) -> None:
        from artifacts import VALIDATORS, review

        for schema in [*VALIDATORS, review.SCHEMA]:
            name = schema.split('/')[0]
            self.assertTrue((ROOT / 'schemas' / f'{name}.md').exists(), name)


if __name__ == '__main__':
    unittest.main()
