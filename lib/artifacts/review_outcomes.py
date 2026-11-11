"""`review-outcomes/1`: what happened to each finding `code-review` posted, over a period.

The facts the code-review retrospective judges, collected by a script, as in Warp's factory
where a daily job reads people's reactions to automated reviews before improving the review
skill. For each review this skill posted, and each of its findings:

- whether a later follow-up review found it `addressed`, `declined`, `still_open` or `withdrawn`;
- whether the flagged lines changed in a later commit on the PR, which is the strongest sign a
  finding was acted on;
- for a finding posted inline, whether its conversation thread was resolved or is outdated;
- what people said on the PR afterwards, for the agent to read;
- what became of the PR: merged, closed or still open.

`build` is pure; `collect` fetches. See schemas/review-outcomes.md.
"""

import json
from datetime import UTC, datetime
from typing import Any

from . import collect as fetch
from . import render, review_context
from ._check import ENVELOPE_FIELDS, Checker, check_envelope
from .run import ID_PATTERN as RUN_ID_PATTERN

SCHEMA = 'review-outcomes/1'
PR_FIELDS = 'number,title,url,state,createdAt,mergedAt,closedAt,headRefOid,comments,reviews'
FIELDS = ('run', 'period', 'reviews')
# Each thread's state, and enough to match it to the finding that started it.
THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          isResolved isOutdated path originalLine diffSide
          comments(first: 1) { nodes { originalCommit { oid } body } }
        }
      }
    }
  }
}
"""
REVIEW_FIELDS = (
    'pr',
    'url',
    'pr_state',
    'posted_at',
    'head_sha',
    'followup_of',
    'run',
    'inline',
    'findings',
    'said_after',
)
FINDING_FIELDS = (
    'id',
    'fingerprint',
    'severity',
    'category',
    'rule',
    'title',
    'location',
    'later_status',
    'lines_changed',
    'posted_inline',
    'thread',
)


def validate(artifact: Any) -> list[str]:
    checker = Checker()
    if not checker.object(artifact, '$', ENVELOPE_FIELDS + FIELDS):
        return checker.errors
    check_envelope(checker, artifact, SCHEMA)
    checker.string(artifact.get('run'), '$.run', pattern=RUN_ID_PATTERN, pattern_hint='a run id')
    period = artifact.get('period')
    if checker.object(period, '$.period', ('since', 'until')):
        checker.timestamp(period.get('since'), '$.period.since')
        checker.timestamp(period.get('until'), '$.period.until')

    def finding(value: Any, path: str) -> None:
        if checker.object(value, path, FINDING_FIELDS):
            checker.choice(
                value.get('later_status'),
                f'{path}.later_status',
                ('still_open', 'addressed', 'declined', 'withdrawn', None),
            )

    def one(value: Any, path: str) -> None:
        if checker.object(value, path, REVIEW_FIELDS):
            checker.integer(value.get('pr'), f'{path}.pr', minimum=1)
            checker.choice(value.get('pr_state'), f'{path}.pr_state', ('open', 'merged', 'closed'))
            checker.timestamp(value.get('posted_at'), f'{path}.posted_at')
            checker.array(value.get('findings'), f'{path}.findings', finding)

    checker.array(artifact.get('reviews'), '$.reviews', one)
    return checker.errors


def build(
    *,
    repo: str,
    run: dict[str, Any],
    since: datetime,
    until: datetime,
    prs: list[dict[str, Any]],
    diffs: dict[tuple[str, str], str],
    threads: dict[int, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """The outcomes, from `gh pr view` data, the diffs between reviewed and final heads, and
    each PR's review threads.

    `diffs` maps (reviewed head, final head) to the diff between them; a missing entry means
    the head didn't move after that review. `threads` maps a PR number to its review threads,
    as the GraphQL API returns them. Pure.
    """
    threads = threads or {}
    reviews = []
    for pr in prs:
        said = review_context.said(pr)
        posted = review_context.previous_reviews(said)
        final_head = pr['headRefOid']
        for index, item in enumerate(posted):
            if not since <= _parse(item['posted_at']) < until:
                continue
            review = item['review']
            later = posted[index + 1]['review'] if index + 1 < len(posted) else None
            later_status = (
                {f['fingerprint']: f['status'] for f in later['findings']} if later else {}
            )
            delta = diffs.get((review['head_sha'], final_head))
            reviews.append(
                {
                    'pr': pr['number'],
                    'url': pr['url'],
                    'pr_state': pr['state'].lower(),
                    'posted_at': item['posted_at'],
                    'head_sha': review['head_sha'],
                    'followup_of': review['followup_of'],
                    'run': review['run'],
                    # None for a review posted before the setting was recorded.
                    'inline': review.get('inline'),
                    'findings': [
                        _finding(
                            f,
                            later_status.get(f['fingerprint']),
                            delta,
                            review,
                            threads.get(pr['number'], []),
                        )
                        for f in review['findings']
                    ],
                    'said_after': [
                        {k: s[k] for k in ('kind', 'author', 'at', 'body')}
                        for s in said
                        if not s['review'] and s['at'] > item['posted_at']
                    ],
                }
            )
    return {
        'schema': SCHEMA,
        'project': repo,
        'produced_by': {
            'skill': 'code-review-retrospective',
            'automation_sha': run['produced_by']['automation_sha'],
        },
        'run': run['id'],
        'period': {'since': _stamp(since), 'until': _stamp(until)},
        'reviews': sorted(reviews, key=lambda r: (r['pr'], r['posted_at'])),
    }


def _finding(
    finding: dict[str, Any],
    later_status: str | None,
    delta: str | None,
    review: dict[str, Any],
    threads: list[dict[str, Any]],
) -> dict[str, Any]:
    location = finding['location']
    if location is None or location['side'] != 'RIGHT':
        changed = None  # nothing to compare: no location, or a removed line
    elif delta is None:
        changed = False  # the PR's head didn't move after the review
    else:
        changed = review_context.lines_touched(delta, location['path'], location['line'])
    return {
        'id': finding['id'],
        'fingerprint': finding['fingerprint'],
        'severity': finding['severity'],
        'category': finding['category'],
        'rule': finding['rule'],
        'title': finding['title'],
        'location': None if location is None else f'{location["path"]}:{location["line"]}',
        'later_status': later_status,
        'lines_changed': changed,
        'posted_inline': (
            None if review.get('inline') is None else render.is_inline(finding, review['inline'])
        ),
        'thread': _thread(finding, review['head_sha'], threads),
    }


def _thread(
    finding: dict[str, Any], head_sha: str, threads: list[dict[str, Any]]
) -> dict[str, bool] | None:
    """The inline thread this finding started, if it was posted inline: the one on its line, on
    the reviewed commit, whose first comment names the finding's id."""
    location = finding['location']
    if location is None:
        return None
    for thread in threads:
        first = ((thread.get('comments') or {}).get('nodes') or [{}])[0]
        if (
            thread.get('path') == location['path']
            and thread.get('originalLine') == location['line']
            and thread.get('diffSide') == location['side']
            and (first.get('originalCommit') or {}).get('oid') == head_sha
            and f'({finding["id"]})' in (first.get('body') or '')
        ):
            return {
                'resolved': bool(thread.get('isResolved')),
                'outdated': bool(thread.get('isOutdated')),
            }
    return None


def collect(
    repo: str, run: dict[str, Any], since: datetime, until: datetime, limit: int = 300
) -> dict[str, Any]:
    """Fetch every PR updated in the period, and the diffs after each review on it."""
    listed = json.loads(
        fetch.gh(
            'pr',
            'list',
            '--repo',
            repo,
            '--state',
            'all',
            '--limit',
            str(limit),
            '--search',
            f'updated:>={since:%Y-%m-%d}',
            '--json',
            'number',
        )
    )
    prs, diffs, threads = [], {}, {}
    owner, name = repo.split('/')
    for entry in listed:
        pr = json.loads(
            fetch.gh('pr', 'view', str(entry['number']), '--repo', repo, '--json', PR_FIELDS)
        )
        posted = review_context.previous_reviews(review_context.said(pr))
        if not posted:
            continue
        prs.append(pr)
        data = json.loads(
            fetch.gh(
                'api',
                'graphql',
                '-f',
                f'query={THREADS_QUERY}',
                '-F',
                f'owner={owner}',
                '-F',
                f'name={name}',
                '-F',
                f'number={pr["number"]}',
            )
        )
        threads[pr['number']] = data['data']['repository']['pullRequest']['reviewThreads']['nodes']
        for item in posted:
            head = item['review']['head_sha']
            if head != pr['headRefOid'] and (head, pr['headRefOid']) not in diffs:
                diffs[(head, pr['headRefOid'])] = review_context.compare_diff(
                    repo, head, pr['headRefOid']
                )
    return build(
        repo=repo, run=run, since=since, until=until, prs=prs, diffs=diffs, threads=threads
    )


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _stamp(value: datetime) -> str:
    return f'{value.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}'
