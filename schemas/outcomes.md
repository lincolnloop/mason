# `outcomes/1`

What happened to a workflow's PRs, and to the project's alerts, over a period. It is collected
by a script, `artifact collect-outcomes`, so a retrospective judges facts it didn't count or
date itself. It is the same split Warp's factory uses for its review feedback loop: collect
with a script, judge with an agent.

Sources: the `automation`-labelled PRs and their comments, CI checks and embedded artifacts; the
project's git history (reverts); the Dependabot alert history (every alert's own open, fix and
dismiss dates); and the PyPI and npm registries and GitHub for package health. Only package
names are sent to the registries.

```json
{
  "schema": "outcomes/1",
  "project": "example-org/example-app",
  "produced_by": {
    "skill": "dependency-retrospective",
    "automation_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "run": "20261101-000000-dependency-retrospective",
  "period": {
    "since": "2026-10-01T00:00:00Z",
    "until": "2026-11-01T00:00:00Z"
  },
  "policy": {
    "fix_within_days": {
      "critical": 3,
      "high": 7,
      "medium": 30,
      "low": 90
    },
    "stale_after_months": 24,
    "stale_pr_after_days": 14
  },
  "prs": [
    {
      "number": 40,
      "url": "https://github.com/example-org/example-app/pull/40",
      "title": "Security upgrades",
      "state": "merged",
      "draft": false,
      "created_at": "2026-10-10T09:00:00Z",
      "decided_at": "2026-10-12T09:00:00Z",
      "days_open": 2.0,
      "stale": false,
      "run": {
        "id": "20261111-093000-dependency-upgrades",
        "trigger": "unattended",
        "started_by": "systemd timer",
        "started_at": "2026-11-11T09:30:00Z"
      },
      "edited": true,
      "ci": {
        "passed": [
          "ci/circleci: lint",
          "tests"
        ],
        "failed": [],
        "pending": []
      },
      "alerts_closed": [
        7
      ],
      "findings": {
        "in_pr": [
          "dependabot-2026-11-pillow"
        ],
        "left_out": []
      },
      "comments": [
        {
          "kind": "comment",
          "author": "reviewer",
          "created_at": "2026-10-11T08:00:00Z",
          "body": "Pinned qs back; the 6.17 parser breaks the admin search."
        }
      ]
    }
  ],
  "stopped_runs": [],
  "reverts": [],
  "alerts": {
    "open_now": {
      "critical": 0,
      "high": 0,
      "medium": 1,
      "low": 0
    },
    "opened": {
      "critical": 0,
      "high": 1,
      "medium": 0,
      "low": 0
    },
    "fixed": {
      "critical": 0,
      "high": 1,
      "medium": 0,
      "low": 0
    },
    "dismissed": {
      "critical": 0,
      "high": 0,
      "medium": 0,
      "low": 0
    },
    "fixed_by_upgrade_prs": [
      7
    ],
    "days_to_fix": {
      "critical": {
        "count": 0,
        "median": null,
        "max": null
      },
      "high": {
        "count": 1,
        "median": 4.4,
        "max": 4.4
      },
      "medium": {
        "count": 0,
        "median": null,
        "max": null
      },
      "low": {
        "count": 0,
        "median": null,
        "max": null
      }
    },
    "open_past_target": [
      {
        "number": 12,
        "package": "pkg12",
        "severity": "medium",
        "age_days": 78.0
      }
    ]
  },
  "packages": [
    {
      "ecosystem": "npm",
      "package": "request",
      "latest": "2.88.2",
      "latest_released_at": "2020-02-11T16:35:36Z",
      "deprecated": "request has been deprecated",
      "repository": "git+https://github.com/request/request.git",
      "archived": true,
      "open_alerts": 1,
      "error": null,
      "flags": [
        "stale",
        "deprecated",
        "archived"
      ]
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `run` | The retrospective run that collected it. |
| `period` | `since` to `until`. `since` defaults to `period_days` before now; a retrospective passes the end of the previous one, so periods don't overlap or leave gaps. |
| `policy` | The targets it was judged against, from `[dependency-retrospective]` and `[limits]`. |
| `prs[]` | Every upgrade PR open during the period, or opened, merged or closed in it. |
| `prs[].edited` | `true` if the PR's head moved after it was opened (`change.head_sha`): someone pushed to it, and for a merged PR, changed it before merging. `null` if the PR has no embedded change. |
| `prs[].ci` | Check names by result: `passed`, `failed`, `pending`. |
| `prs[].comments` | What people said, as comments or reviews (`kind`), oldest first, up to 2,000 characters each. Run records posted as comments are not included here; they are `stopped_runs`. |
| `stopped_runs[]` | Runs that stopped because an upgrade PR was open, from the records they posted on it. |
| `reverts[]` | Commits on the main branch that revert an upgrade commit (`Revert "Upgrade <package> to <version>"`). |
| `alerts` | By severity: `open_now`, and `opened`, `fixed` and `dismissed` in the period. `days_to_fix` (count, median, max) for alerts fixed in the period. `fixed_by_upgrade_prs`: the fixed alerts an upgrade PR's change said it would close. `open_past_target`: open alerts older than their severity's target, most severe and oldest first. |
| `packages[]` | Every package with an open alert or a finding in an upgrade PR: its latest release and date, a deprecation notice, whether its GitHub repository is archived, and `flags` derived from those (`stale`: no release for `stale_after_months`). `error` says what couldn't be read. |
