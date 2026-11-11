"""Parse a unified diff into commentable lines, and annotate it for a review agent.

GitHub accepts a review comment only on a line that appears in the PR's diff: `RIGHT` for added
and context lines (numbered in the new file), `LEFT` for removed and context lines (numbered in
the old file). The annotated form puts those coordinates on every line, so an agent copies a
location instead of counting one:

    [OLD:12] removed line
    [NEW:34] added line
    [OLD:12,NEW:34] context line

The idea comes from Warp's cloud-factory-demo (`review-pr/scripts/annotate_diff.py`).
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

HUNK_HEADER = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
GIT_HEADER = re.compile(r'^diff --git a/(.+) b/(.+)$')

Side = Literal['LEFT', 'RIGHT']


@dataclass
class FileDiff:
    path: str
    left: dict[int, str] = field(default_factory=dict)
    right: dict[int, str] = field(default_factory=dict)

    def side(self, name: Side) -> dict[int, str]:
        return self.left if name == 'LEFT' else self.right


@dataclass(frozen=True)
class Line:
    """One line of the diff. `kind` is '-', '+' or ' ' inside a hunk, else 'header'."""

    raw: str
    kind: Literal['-', '+', ' ', 'header']
    path: str | None = None
    old: int = 0
    new: int = 0

    @property
    def text(self) -> str:
        return self.raw[1:]


def parse(diff_text: str) -> dict[str, FileDiff]:
    """Map each file in the diff to its commentable lines on each side.

    A deleted file is keyed by its old path; every other file by its new path. A file with no
    hunks (binary, mode-only) is present with no lines, so "in the diff but not commentable" can
    be told apart from "not in the diff".
    """
    files: dict[str, FileDiff] = {}
    for line in scan(diff_text):
        if line.path is None:
            continue
        file_diff = files.setdefault(line.path, FileDiff(line.path))
        match line.kind:
            case '-':
                file_diff.left[line.old] = line.text
            case '+':
                file_diff.right[line.new] = line.text
            case ' ':
                file_diff.left[line.old] = line.text
                file_diff.right[line.new] = line.text
    return files


def annotate(diff_text: str) -> str:
    """Return the diff with every hunk line prefixed by its coordinates."""
    out: list[str] = []
    for line in scan(diff_text):
        match line.kind:
            case '-':
                out.append(f'[OLD:{line.old}] {line.text}')
            case '+':
                out.append(f'[NEW:{line.new}] {line.text}')
            case ' ':
                out.append(f'[OLD:{line.old},NEW:{line.new}] {line.text}')
            case 'header':
                out.append(line.raw)
    return '\n'.join(out) + ('\n' if diff_text.endswith('\n') else '')


def scan(diff_text: str) -> Iterator[Line]:
    """Yield every line of the diff, with its file path and line numbers.

    Headers are recognised only outside a hunk, because a removed line whose text starts with
    '-- ' is written '--- …' inside one. A hunk ends when the counts in its header are used up.
    """
    path: str | None = None
    old_path: str | None = None
    old = new = old_left = new_left = 0
    for raw in diff_text.splitlines():
        if old_left > 0 or new_left > 0:
            marker = raw[:1]
            if marker == '-' and old_left > 0:
                yield Line(raw, '-', path, old=old)
                old, old_left = old + 1, old_left - 1
                continue
            if marker == '+' and new_left > 0:
                yield Line(raw, '+', path, new=new)
                new, new_left = new + 1, new_left - 1
                continue
            if (marker == ' ' or raw == '') and old_left > 0 and new_left > 0:
                # An empty line is a context line whose leading space was stripped in transit.
                yield Line(raw or ' ', ' ', path, old=old, new=new)
                old, new = old + 1, new + 1
                old_left, new_left = old_left - 1, new_left - 1
                continue
            if marker == '\\':  # '\ No newline at end of file'
                yield Line(raw, 'header', path)
                continue
            old_left = new_left = 0  # malformed or truncated hunk; read this line as a header

        if (header := HUNK_HEADER.match(raw)) is not None:
            old_start, old_count, new_start, new_count = header.groups()
            old, new = int(old_start), int(new_start)
            old_left = 1 if old_count is None else int(old_count)
            new_left = 1 if new_count is None else int(new_count)
        elif (git := GIT_HEADER.match(raw)) is not None:
            old_path = None
            # Binary and mode-only changes have no ---/+++ lines; register them here.
            path = git.group(2) if git.group(1) == git.group(2) else None
        elif raw.startswith('--- '):
            old_path = _header_path(raw[4:], 'a/')
        elif raw.startswith('+++ '):
            new_path = _header_path(raw[4:], 'b/')
            path = old_path if new_path is None else new_path
        yield Line(raw, 'header', path)


def _header_path(value: str, prefix: str) -> str | None:
    value = value.split('\t', 1)[0].rstrip()
    if value == '/dev/null':
        return None
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return value.removeprefix(prefix)
