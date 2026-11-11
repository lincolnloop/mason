import unittest

from artifacts import diff

from .fixtures import DIFF


class ParseTests(unittest.TestCase):
    def test_sides_and_line_numbers(self) -> None:
        files = diff.parse(DIFF)
        views = files['app/views.py']
        self.assertEqual(
            views.left,
            {
                10: '    first = 1',
                11: '    second = 2',
                12: '    old = get(slug)',
                13: '    last = 3',
            },
        )
        self.assertEqual(
            views.right,
            {
                10: '    first = 1',
                11: '    second = 2',
                12: '    new = get_or_404(slug)',
                13: '    extra = True',
                14: '    last = 3',
            },
        )

    def test_removed_line_that_looks_like_a_header(self) -> None:
        # A removed line whose text is '-- comment' is written '--- comment' inside a hunk.
        text = (
            'diff --git a/q.sql b/q.sql\n--- a/q.sql\n+++ b/q.sql\n'
            '@@ -1,2 +1,1 @@\n--- comment\n select 1;\n'
        )
        files = diff.parse(text)
        self.assertEqual(files['q.sql'].left, {1: '-- comment', 2: 'select 1;'})
        self.assertEqual(files['q.sql'].right, {1: 'select 1;'})
        self.assertEqual(set(files), {'q.sql'})

    def test_new_and_deleted_files(self) -> None:
        text = (
            'diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n'
            '@@ -0,0 +1,2 @@\n+a\n+b\n'
            'diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n'
            '@@ -1 +0,0 @@\n-x\n'
        )
        files = diff.parse(text)
        self.assertEqual(files['new.py'].right, {1: 'a', 2: 'b'})
        self.assertEqual(files['new.py'].left, {})
        self.assertEqual(files['gone.py'].left, {1: 'x'})
        self.assertEqual(files['gone.py'].right, {})

    def test_rename_is_keyed_by_new_path(self) -> None:
        text = (
            'diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n'
            '--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-a\n+b\n'
        )
        files = diff.parse(text)
        self.assertEqual(set(files), {'new.py'})
        self.assertEqual(files['new.py'].left, {1: 'a'})

    def test_binary_file_is_present_without_lines(self) -> None:
        text = 'diff --git a/logo.png b/logo.png\nindex 1..2 100644\nBinary files a/logo.png and b/logo.png differ\n'
        files = diff.parse(text)
        self.assertEqual(files['logo.png'].left, {})
        self.assertEqual(files['logo.png'].right, {})

    def test_no_newline_marker_and_stripped_context_line(self) -> None:
        text = 'diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n a\n\n-b\n\\ No newline at end of file\n+c\n'
        files = diff.parse(text)
        self.assertEqual(files['f'].left, {1: 'a', 2: '', 3: 'b'})
        self.assertEqual(files['f'].right, {1: 'a', 2: '', 3: 'c'})

    def test_several_hunks_and_files(self) -> None:
        text = (
            'diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-x\n+y\n@@ -10 +10,2 @@\n z\n+w\n'
            'diff --git a/b b/b\n--- a/b\n+++ b/b\n@@ -5 +5 @@\n-p\n+q\n'
        )
        files = diff.parse(text)
        self.assertEqual(files['a'].right, {1: 'y', 10: 'z', 11: 'w'})
        self.assertEqual(files['b'].left, {5: 'p'})

    def test_empty_diff(self) -> None:
        self.assertEqual(diff.parse(''), {})


class AnnotateTests(unittest.TestCase):
    def test_annotates_hunk_lines_and_keeps_headers(self) -> None:
        lines = diff.annotate(DIFF).splitlines()
        self.assertEqual(lines[:5], DIFF.splitlines()[:5])
        self.assertEqual(
            lines[5:],
            [
                '[OLD:10,NEW:10]     first = 1',
                '[OLD:11,NEW:11]     second = 2',
                '[OLD:12]     old = get(slug)',
                '[NEW:12]     new = get_or_404(slug)',
                '[NEW:13]     extra = True',
                '[OLD:13,NEW:14]     last = 3',
            ],
        )

    def test_keeps_trailing_newline(self) -> None:
        self.assertTrue(diff.annotate(DIFF).endswith('\n'))
        self.assertFalse(diff.annotate(DIFF.rstrip('\n')).endswith('\n'))


if __name__ == '__main__':
    unittest.main()
