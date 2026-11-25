"""Every JSON example in schemas/*.md must validate, so the docs can't drift from the code."""

import json
import re
import subprocess
import sys
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
            for kind, body in blocks:
                if kind != 'json':
                    continue
                examples += 1
                with self.subTest(page=page.name, example=body[:60]):
                    artifact = json.loads(body)
                    self.assertEqual(validate(artifact), [])
        self.assertGreaterEqual(examples, 8)

    def test_every_schema_has_a_page(self) -> None:
        from artifacts import VALIDATORS

        for schema in VALIDATORS:
            name = schema.split('/')[0]
            self.assertTrue((ROOT / 'schemas' / f'{name}.md').exists(), name)


class SchemaCommandTests(unittest.TestCase):
    """`mason schema` prints the pages, so an installed copy, without this checkout, has them."""

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, '-I', str(ROOT / 'bin' / 'artifact'), 'schema', *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_lists_and_prints_pages(self) -> None:
        listed = self.run_cli().stdout.split()
        self.assertIn('dependency-report', listed)
        self.assertNotIn('README', listed)
        page = self.run_cli('dependency-report/1')
        self.assertEqual(page.returncode, 0, page.stderr)
        self.assertTrue(page.stdout.startswith('# `dependency-report/1`'))

    def test_an_unknown_schema_is_refused(self) -> None:
        result = self.run_cli('nonsense')
        self.assertEqual(result.returncode, 1)
        self.assertIn('known:', result.stderr)


if __name__ == '__main__':
    unittest.main()
