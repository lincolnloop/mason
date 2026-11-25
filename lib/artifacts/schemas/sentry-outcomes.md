# `sentry-outcomes/1`

Whether the fixes `sentry-errors` shipped made their errors stop. It is collected by a script,
`artifact collect-sentry-outcomes`, from GitHub (the fix PRs and triage issues) and Sentry (each
fixed issue's last event), so the Sentry retrospective judges facts rather than impressions.

```json
{
  "schema": "sentry-outcomes/1",
  "project": "example-org/example-app",
  "produced_by": {
    "skill": "sentry-retrospective",
    "automation_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "run": "20261201-000000-sentry-retrospective",
  "period": {
    "since": "2026-11-01T00:00:00Z",
    "until": "2026-11-20T00:00:00Z"
  },
  "policy": {
    "quiet_after_days": 14,
    "deploy_grace_hours": 24,
    "stale_pr_after_days": 14
  },
  "prs": [
    {
      "number": 40,
      "url": "https://github.com/o/r/pull/40",
      "title": "Fix cancel",
      "state": "merged",
      "draft": false,
      "created_at": "2026-11-02T00:00:00Z",
      "decided_at": "2026-11-03T00:00:00Z",
      "days_open": 1.0,
      "stale": false,
      "run": {
        "id": "20261111-093000-dependency-upgrades",
        "trigger": "unattended",
        "started_by": "systemd timer",
        "started_at": "2026-11-11T09:30:00Z"
      },
      "edited": false,
      "ci": {
        "passed": [],
        "failed": [],
        "pending": []
      },
      "alerts_closed": [],
      "findings": {
        "in_pr": [
          "sentry-2026-11-example-1a2"
        ],
        "left_out": []
      },
      "comments": [],
      "signal": {
        "status": "met",
        "issues": [
          {
            "short_id": "EXAMPLE-1A2",
            "last_event": "2026-11-03T12:00:00Z",
            "after_fix": false
          }
        ]
      }
    }
  ],
  "triage_issues": [
    {
      "number": 9,
      "url": "https://github.com/o/r/issues/9",
      "state": "open",
      "created_at": "2026-11-05T00:00:00Z",
      "findings": [
        "shop/cancel-unknown-order-500"
      ]
    }
  ],
  "unresolved_now": {
    "production": 41
  }
}
```

| Field | Meaning |
|---|---|
| `policy` | `quiet_after_days` (from `[sentry-errors]`): how long errors must stay quiet after the merge for the fix to count. `deploy_grace_hours` (from `[sentry-retrospective]`): time allowed for the deploy, before an event counts against the fix. |
| `prs[]` | Every fix PR open in the period, or opened, merged or closed in it, with the same record as an upgrade PR's (`schemas/outcomes.md`): merged, edited before merge, CI, what people said. |
| `prs[].signal` | For each Sentry issue the change's `sentry_quiet` signal names: its last event in any configured environment, and whether that came after the merge plus the grace (`after_fix`). `status` is `not_met` as soon as one issue has, `met` once `quiet_after_days` have passed without, `pending` until then, and `not_applicable` for a PR that wasn't merged. |
| `triage_issues[]` | The triage issues the workflow opened in the period, their state, and the fingerprints of the findings in them. |
| `unresolved_now` | Unresolved issues in each environment at collection time, over `[sentry-errors] period_days`. |

A fix whose errors keep coming is the most useful thing a retrospective can find: the root cause
was something else. The last event is read across environments, so a fix deployed to staging
first is judged there too.
