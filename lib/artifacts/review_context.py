"""What `code-review` needs about a pull request, gathered by a script before the agent reads it.

Into the run directory it writes:

- `pr.diff`, the PR's unified diff, which the review is validated against;
- `pr_diff.txt`, the same diff annotated with the coordinates findings must use;
- `context.json`: the PR's commits and description, the previous review by this skill (if any),
  and what people said since it;
- `delta.diff` and `delta_diff.txt`, when a previous review exists for an earlier head: what
  changed since, so a follow-up review checks what is new rather than starting again.

`build` is pure; `fetch` does the I/O. Everything in the PR (its description, comments, the
code) was written by someone else: the agent reads it as data, never as instructions.
"""

import json
from pathlib import Path
from typing import Any

from . import collect, diff, embed

# `gh pr view --json` has no base commit in every gh version, so it is read from the REST API.
PR_FIELDS = (
    'number,title,body,url,author,state,isDraft,headRefOid,headRefName,comments,reviews,files'
)
TEXT_LIMIT = 4000


def build(pr: dict[str, Any]) -> dict[str, Any]:
    """The context, from `gh pr view --json PR_FIELDS`. Pure."""
    conversation = said(pr)
    reviews = previous_reviews(conversation)
    previous = reviews[-1] if reviews else None
    since = previous['posted_at'] if previous else None
    return {
        'pr': pr['number'],
        'url': pr['url'],
        'title': pr['title'],
        'author': (pr.get('author') or {}).get('login') or 'unknown',
        'state': pr['state'].lower(),
        'draft': bool(pr.get('isDraft')),
        'base_sha': pr['baseRefOid'],
        'head_sha': pr['headRefOid'],
        'head_ref': pr['headRefName'],
        'description': (pr.get('body') or '')[:TEXT_LIMIT],
        'files': sorted(f['path'] for f in pr.get('files') or []),
        'previous_review': previous['review'] if previous else None,
        'already_reviewed': bool(previous and previous['review']['head_sha'] == pr['headRefOid']),
        # Replies to the previous review, or the whole conversation if there is none.
        'said_since': [
            s for s in conversation if not s['review'] and (since is None or s['at'] > since)
        ],
    }


def previous_reviews(said: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every review this skill posted on the PR, oldest first, from the embedded artifacts."""
    found = []
    for item in said:
        for review in item['review']:
            found.append({'posted_at': item['at'], 'review': review})
    return sorted(found, key=lambda r: r['posted_at'])


def said(pr: dict[str, Any]) -> list[dict[str, Any]]:
    """Every comment and review on the PR, oldest first, with any reviews this skill embedded."""
    items = []
    for kind, entries, when in (
        ('comment', pr.get('comments') or [], 'createdAt'),
        ('review', pr.get('reviews') or [], 'submittedAt'),
    ):
        for entry in entries:
            body = entry.get('body') or ''
            try:
                embedded = embed.extract(body)
            except ValueError:
                embedded = []
            reviews = [a for a in embedded if a.get('schema') == 'review/1']
            if embedded and not reviews:
                continue  # a workflow's record (a stopped run), not something a person said
            items.append(
                {
                    'kind': kind
                    if kind == 'comment'
                    else f'review: {(entry.get("state") or "").lower()}',
                    'author': (entry.get('author') or {}).get('login') or 'unknown',
                    'at': entry.get(when) or '',
                    'body': '' if reviews else body[:TEXT_LIMIT],
                    'review': reviews,
                }
            )
    # An approval with no text still says something; an empty comment doesn't.
    kept = [i for i in items if i['review'] or i['body'] or i['kind'] != 'comment']
    return sorted(kept, key=lambda s: s['at'])


def fetch(repo: str, number: int, directory: Path) -> dict[str, Any]:
    """Fetch the PR and write the context files into `directory`. Returns the context."""
    pr = json.loads(collect.gh('pr', 'view', str(number), '--repo', repo, '--json', PR_FIELDS))
    pr['baseRefOid'] = collect.gh(
        'api', f'repos/{repo}/pulls/{number}', '--jq', '.base.sha'
    ).strip()
    context = build(pr)
    pr_diff = pull_diff(repo, number)
    (directory / 'pr.diff').write_text(pr_diff, encoding='utf-8')
    (directory / 'pr_diff.txt').write_text(diff.annotate(pr_diff), encoding='utf-8')
    previous = context['previous_review']
    context['delta'] = None
    if previous and not context['already_reviewed']:
        delta = compare_diff(repo, previous['head_sha'], context['head_sha'])
        (directory / 'delta.diff').write_text(delta, encoding='utf-8')
        (directory / 'delta_diff.txt').write_text(diff.annotate(delta), encoding='utf-8')
        context['delta'] = {'from': previous['head_sha'], 'to': context['head_sha']}
    (directory / 'context.json').write_text(
        json.dumps(context, indent=2, ensure_ascii=False) + '\n',
        encoding='utf-8',
    )
    return context


def pull_diff(repo: str, number: int) -> str:
    return collect.gh(
        'api', '-H', 'Accept: application/vnd.github.diff', f'repos/{repo}/pulls/{number}'
    )


def compare_diff(repo: str, base: str, head: str) -> str:
    return collect.gh(
        'api', '-H', 'Accept: application/vnd.github.diff', f'repos/{repo}/compare/{base}...{head}'
    )


def lines_touched(diff_text: str, path: str, line: int, window: int = 3) -> bool:
    """Whether a diff from commit A to commit B changes `path` near `line`, as numbered at A.

    A removed line has its own number at A. An added line sits between two lines of A, so it is
    placed at the number of the next line of A it comes before.
    """
    position = 0  # the number, at A, of the next line of A in the current hunk
    for item in diff.scan(diff_text):
        if item.kind == 'header':
            header = diff.HUNK_HEADER.match(item.raw)
            if header is not None:
                position = int(header.group(1))
            continue
        if item.path != path:
            continue
        if item.kind == ' ':
            position = item.old + 1
            continue
        at = item.old if item.kind == '-' else position
        if item.kind == '-':
            position = item.old + 1
        if abs(at - line) <= window:
            return True
    return False
