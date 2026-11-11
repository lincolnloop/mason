# `review-outcomes/1`

What happened to each finding `code-review` posted, over a period. It is collected by a script,
`artifact collect-review-outcomes`, so the code-review retrospective judges facts it didn't
gather itself. Warp's factory works the same way: a daily job collects people's reactions to
automated reviews, and an agent decides what the review skill should learn from them.

It reads every PR updated in the period, finds the reviews this skill posted from the artifacts
embedded in them, and for each review compares the reviewed head with the PR's final head.

```json
{
  "schema": "review-outcomes/1",
  "project": "example-org/example-app",
  "produced_by": {
    "skill": "code-review-retrospective",
    "automation_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "run": "20261201-000000-code-review-retrospective",
  "period": {
    "since": "2026-11-01T00:00:00Z",
    "until": "2026-12-01T00:00:00Z"
  },
  "reviews": [
    {
      "pr": 7,
      "url": "https://github.com/example-org/example-app/pull/7",
      "pr_state": "merged",
      "posted_at": "2026-11-03T09:00:00Z",
      "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "followup_of": null,
      "run": "20261111-093000-code-review",
      "inline": "all",
      "findings": [
        {
          "id": "F1",
          "fingerprint": "app/lookup-500",
          "severity": "important",
          "category": "correctness",
          "rule": null,
          "title": "Lookup",
          "location": "app/views.py:12",
          "later_status": null,
          "lines_changed": true,
          "posted_inline": true,
          "thread": {
            "resolved": true,
            "outdated": true
          }
        }
      ],
      "said_after": [
        {
          "kind": "comment",
          "author": "dev",
          "at": "2026-11-03T14:00:00Z",
          "body": "Good catch, fixed."
        }
      ]
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `reviews[]` | One per review the skill posted in the period: its PR, when, the commit it reviewed, the run that wrote it, and what became of the PR (`pr_state`). |
| `reviews[].inline` | The `[code-review] inline` setting the review was posted with: `none`, `suggestions` or `all`. `null` for a review posted before the setting was recorded. The report tallies findings by it, to compare settings. |
| `findings[].later_status` | What the next review of the same PR said about this finding, matched by `fingerprint`: `addressed`, `declined`, `still_open` or `withdrawn`. `null` if there was no later review. |
| `findings[].lines_changed` | Whether a later commit on the PR changed the flagged lines (within three lines), the strongest sign that a finding was acted on. `false` if the head didn't move after the review; `null` for a finding with no location, or on a removed line. |
| `findings[].posted_inline` | Whether the finding was posted as an inline comment under that setting (only `new` findings are). `null` when the setting wasn't recorded. |
| `findings[].thread` | For a finding posted inline, its conversation thread: `resolved` (someone closed it) and `outdated` (a later commit changed its lines). `null` if it had no thread. Read through GitHub's GraphQL API, and matched by file, line, side, the reviewed commit, and the finding's id in the comment. |
| `said_after` | What people said on the PR after the review, as comments or reviews (`kind`), up to 4,000 characters each. The retrospective reads it to tell a finding that was right but not worth fixing from one that was wrong. |

A finding counts as **acted on** when its lines changed or a follow-up found it addressed. The
report tallies that by category and severity. A category whose findings are rarely acted on is
where the review is noisy, or where the project disagrees with it.
