"""Every JSON example in schemas/*.md must validate, so the docs can't drift from the code."""

import json
import re
import unittest

from artifacts import VALIDATORS, validate

from . import ROOT

BLOCK = re.compile(r'^```json\n(.*?)^```$', re.DOTALL | re.MULTILINE)


class SchemaDocTests(unittest.TestCase):
    def test_examples_validate(self) -> None:
        pages = sorted((ROOT / 'schemas').glob('*.md'))
        self.assertTrue(pages)
        examples = 0
        for page in pages:
            for body in BLOCK.findall(page.read_text(encoding='utf-8')):
                examples += 1
                with self.subTest(page=page.name, example=body[:60]):
                    self.assertEqual(validate(json.loads(body)), [])
        self.assertGreaterEqual(examples, 2)

    def test_every_schema_has_a_page(self) -> None:
        for schema in VALIDATORS:
            name = schema.split('/')[0]
            self.assertTrue((ROOT / 'schemas' / f'{name}.md').exists(), name)


if __name__ == '__main__':
    unittest.main()
