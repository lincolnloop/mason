"""`review/1`: what `code-review` writes for a pull request. See schemas/review.md.

Locations are checked against the PR's diff, because GitHub rejects a review (HTTP 422) whose
comment sits on a line the diff doesn't show. There is no verdict and no counts: the renderer
derives both from the findings, so they can't disagree with them.
"""

import re
from typing import Any

from . import diff as diff_module
from ._check import ENVELOPE_FIELDS, SHA_PATTERN, SLUG_PATTERN, Checker, check_envelope
from .run import ID_PATTERN as RUN_ID_PATTERN

SCHEMA = 'review/1'

SEVERITIES = ('critical', 'important', 'suggestion', 'nit')
STATUSES = ('new', 'still_open', 'addressed', 'declined', 'withdrawn')
EVIDENCE_STATUSES = ('verified', 'inferred')
SIDES = ('LEFT', 'RIGHT')
MAX_INLINE = 50
INLINE_SETTINGS = ('none', 'suggestions', 'all')
MAX_RANGE_LINES = 10

FINDING_ID = re.compile(r'^F[1-9][0-9]*$')
FINGERPRINT_PATTERN = re.compile(r'^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)+$')
SUGGESTION_BLOCK = re.compile(r'```suggestion[^\n]*\n(.*?)\n?```', re.DOTALL)

FIELDS = ('run', 'pr', 'base_sha', 'head_sha', 'followup_of', 'summary', 'findings')
FINDING_FIELDS = (
    'id',
    'fingerprint',
    'severity',
    'category',
    'rule',
    'title',
    'body',
    'evidence',
    'location',
    'suggestion',
    'status',
)


def validate(artifact: Any, diff_text: str | None = None) -> list[str]:
    """Validate a review. With `diff_text`, also check every location against the diff."""
    checker = Checker()
    if not checker.object(artifact, '$', ENVELOPE_FIELDS + FIELDS, ('inline',)):
        return checker.errors
    check_envelope(checker, artifact, SCHEMA)
    if 'inline' in artifact:
        # Set by the renderer when it posts the review, never by the author.
        checker.choice(artifact['inline'], '$.inline', INLINE_SETTINGS)
    checker.string(artifact.get('run'), '$.run', pattern=RUN_ID_PATTERN, pattern_hint='a run id')
    checker.integer(artifact.get('pr'), '$.pr', minimum=1)
    for key in ('base_sha', 'head_sha'):
        checker.string(
            artifact.get(key),
            f'$.{key}',
            pattern=SHA_PATTERN,
            pattern_hint='a 40-character commit SHA',
        )
    followup_of = artifact.get('followup_of')
    checker.string(
        followup_of,
        '$.followup_of',
        nullable=True,
        pattern=SHA_PATTERN,
        pattern_hint='a 40-character commit SHA',
    )
    if followup_of is not None and followup_of == artifact.get('head_sha'):
        checker.error(
            '$.followup_of', 'is the same commit as head_sha; a follow-up reviews a later head'
        )
    checker.string(artifact.get('summary'), '$.summary')

    files = diff_module.parse(diff_text) if diff_text is not None else None
    findings = artifact.get('findings')
    checker.array(
        findings,
        '$.findings',
        lambda v, p: _finding(checker, v, p, files, followup_of),
    )
    if isinstance(findings, list):
        pairs = [(f'$.findings[{i}]', f) for i, f in enumerate(findings) if isinstance(f, dict)]
        checker.unique(((p, f.get('id')) for p, f in pairs), 'id')
        checker.unique(((p, f.get('fingerprint')) for p, f in pairs), 'fingerprint')
        inline = sum(
            1
            for _, f in pairs
            if isinstance(f.get('location'), dict) and f['location'].get('in_diff') is True
        )
        if inline > MAX_INLINE:
            checker.error(
                '$.findings',
                f'has {inline} findings in the diff; at most {MAX_INLINE} can be posted inline',
            )
    return checker.errors


def _finding(
    checker: Checker,
    finding: Any,
    path: str,
    files: dict[str, diff_module.FileDiff] | None,
    followup_of: str | None,
) -> None:
    if not checker.object(finding, path, FINDING_FIELDS):
        return
    checker.string(finding.get('id'), f'{path}.id', pattern=FINDING_ID, pattern_hint='F1, F2, …')
    checker.string(
        finding.get('fingerprint'),
        f'{path}.fingerprint',
        pattern=FINGERPRINT_PATTERN,
        pattern_hint='area/slug, stable across reviews of the same PR',
    )
    severity = finding.get('severity')
    checker.choice(severity, f'{path}.severity', SEVERITIES)
    checker.string(
        finding.get('category'),
        f'{path}.category',
        pattern=SLUG_PATTERN,
        pattern_hint='a lowercase slug',
    )
    checker.string(finding.get('rule'), f'{path}.rule', nullable=True)
    checker.string(finding.get('title'), f'{path}.title', max_length=200)
    checker.string(finding.get('body'), f'{path}.body')
    checker.choice(finding.get('evidence'), f'{path}.evidence', EVIDENCE_STATUSES)
    status = finding.get('status')
    checker.choice(status, f'{path}.status', STATUSES)
    if status not in (None, 'new') and followup_of is None:
        checker.error(
            f'{path}.status',
            f'is {status!r}, but $.followup_of is null: only a follow-up review carries earlier findings',
        )

    suggestion = finding.get('suggestion')
    checker.string(suggestion, f'{path}.suggestion', nullable=True)
    if isinstance(suggestion, str) and SUGGESTION_BLOCK.search(suggestion):
        checker.error(
            f'{path}.suggestion',
            'must be the replacement text only; the renderer adds the ```suggestion fence',
        )
    if severity == 'nit' and suggestion is None:
        checker.error(
            f'{path}.suggestion',
            'is required for a nit: a nit is only worth raising with a ready fix',
        )

    location = finding.get('location')
    if location is None:
        if suggestion is not None:
            checker.error(f'{path}.suggestion', 'needs a location in the diff to apply to')
        return
    if not _location(checker, location, f'{path}.location'):
        return
    if files is not None:
        _against_diff(checker, location, f'{path}.location', files)
        if isinstance(suggestion, str) and location.get('in_diff') is True:
            _suggestion_context(checker, suggestion, location, f'{path}.suggestion', files)
    if suggestion is not None and location.get('in_diff') is not True:
        checker.error(
            f'{path}.suggestion',
            'needs a location with in_diff true; GitHub can only apply a suggestion to a line in the diff',
        )


def _location(checker: Checker, location: Any, path: str) -> bool:
    if not checker.object(
        location, path, ('path', 'line', 'side', 'in_diff'), ('start_line', 'start_side')
    ):
        return False
    ok = checker.string(location.get('path'), f'{path}.path')
    if ok and location['path'].startswith(('/', 'a/', 'b/', './')):
        checker.error(
            f'{path}.path',
            f'must be relative to the repository root with no prefix, got {location["path"]!r}',
        )
        ok = False
    ok &= checker.integer(location.get('line'), f'{path}.line', minimum=1)
    ok &= checker.choice(location.get('side'), f'{path}.side', SIDES)
    ok &= checker.boolean(location.get('in_diff'), f'{path}.in_diff')
    has_start_line = 'start_line' in location
    if has_start_line != ('start_side' in location):
        checker.error(path, 'start_line and start_side must be given together')
        return False
    if has_start_line:
        ok &= checker.integer(location['start_line'], f'{path}.start_line', minimum=1)
        ok &= checker.choice(location['start_side'], f'{path}.start_side', SIDES)
        if ok:
            if location['start_side'] != location['side']:
                checker.error(
                    f'{path}.start_side',
                    'must equal side; a range cannot cross from removed to added lines',
                )
                ok = False
            elif location['start_line'] >= location['line']:
                checker.error(
                    f'{path}.start_line', 'must be before line; omit start_line for a single line'
                )
                ok = False
            elif location['line'] - location['start_line'] + 1 > MAX_RANGE_LINES:
                checker.error(
                    path, f'spans more than {MAX_RANGE_LINES} lines; point at the lines that matter'
                )
                ok = False
    return bool(ok)


def _against_diff(
    checker: Checker,
    location: dict[str, Any],
    path: str,
    files: dict[str, diff_module.FileDiff],
) -> None:
    file_path, line, side = location['path'], location['line'], location['side']
    file_diff = files.get(file_path)
    lines = file_diff.side(side) if file_diff is not None else {}
    commentable = line in lines and (
        'start_line' not in location
        or all(n in lines for n in range(location['start_line'], line + 1))
    )
    if location['in_diff']:
        if file_diff is None:
            checker.error(f'{path}.path', f'{file_path!r} is not in the PR diff; set in_diff false')
        elif not commentable:
            span = f'{location["start_line"]}-{line}' if 'start_line' in location else str(line)
            checker.error(
                f'{path}.line',
                f'{file_path}:{span} on {side} is not in the diff; take coordinates from the annotated '
                f'diff ([OLD:n] is LEFT n, [NEW:m] and [OLD:n,NEW:m] are RIGHT m), or set in_diff false',
            )
    elif commentable:
        checker.error(
            f'{path}.in_diff',
            f'is false, but {file_path}:{line} on {side} is in the diff; set it true',
        )


def _suggestion_context(
    checker: Checker,
    suggestion: str,
    location: dict[str, Any],
    path: str,
    files: dict[str, diff_module.FileDiff],
) -> None:
    """A suggestion replaces start_line..line exactly; repeating a neighbouring line duplicates it."""
    lines = files[location['path']].side(location['side'])
    replacement = suggestion.split('\n')
    start = location.get('start_line', location['line'])
    above = lines.get(start - 1)
    below = lines.get(location['line'] + 1)
    if above is not None and above.strip() and replacement[0] == above:
        checker.error(
            path,
            f'starts with line {start - 1}, which is outside the range and would be duplicated',
        )
    if below is not None and below.strip() and replacement[-1] == below:
        checker.error(
            path,
            f'ends with line {location["line"] + 1}, which is outside the range and would be duplicated',
        )
