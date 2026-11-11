"""Embed artifacts in a PR body or comment, and extract them again.

GitHub is the archive: an artifact travels inside the markdown it belongs to, collapsed so it
doesn't get in a reader's way. Markers make extraction exact, and the JSON is in a fenced block
so it survives GitHub's markdown handling unchanged.
"""

import json
import re
from typing import Any

START = '<!-- automation:artifact {schema} -->'
END = '<!-- /automation:artifact -->'
BLOCK = re.compile(
    r'<!-- automation:artifact (?P<schema>[a-z][a-z-]*/[0-9]+) -->\s*'
    r'<details>.*?```json\n(?P<json>.*?)\n```\s*</details>\s*'
    r'<!-- /automation:artifact -->',
    re.DOTALL,
)


def embed(artifact: dict[str, Any], label: str | None = None) -> str:
    """Return a collapsed block holding `artifact`, to append to a PR body or comment."""
    schema = artifact['schema']
    summary = label or f'{schema.split("/")[0]}.json'
    # A fence inside a string can't close the block early: json.dumps escapes newlines, so no
    # line of the output starts with ```.
    body = json.dumps(artifact, indent=2, ensure_ascii=False)
    return '\n'.join(
        [
            START.format(schema=schema),
            '<details>',
            f'<summary>{summary}</summary>',
            '',
            '```json',
            body,
            '```',
            '',
            '</details>',
            END,
        ]
    )


def extract(markdown: str, schema: str | None = None) -> list[dict[str, Any]]:
    """Return every artifact embedded in `markdown`, optionally only those of one schema.

    A block whose JSON doesn't parse, or whose `schema` disagrees with its marker, raises
    ValueError: a damaged archive should be noticed, not skipped.
    """
    artifacts = []
    for match in BLOCK.finditer(markdown.replace('\r\n', '\n')):
        marker = match.group('schema')
        try:
            artifact = json.loads(match.group('json'))
        except json.JSONDecodeError as exc:
            raise ValueError(f'embedded {marker} artifact is not valid JSON: {exc}') from exc
        found = artifact.get('schema') if isinstance(artifact, dict) else type(artifact).__name__
        if found != marker:
            raise ValueError(f'embedded artifact marked {marker} has schema {found!r}')
        if schema is None or marker == schema:
            artifacts.append(artifact)
    return artifacts
