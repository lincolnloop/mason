# `dependency-report/1`

One calendar month's vulnerability figures, published as a report for the people the project
answers to: a client, a manager, an auditor. `mason collect-report` builds it from the
Dependabot alert history, using the same counting rules as the retrospective's `outcomes/2`
(`outcomes.classify`), so the two never disagree. It is embedded in the published report, so the
figures stay as published even if an advisory's severity is revised later.

The example is shortened: `months` holds one row for every month of the year up to the report's.

```json
{
  "schema": "dependency-report/1",
  "project": "example/project",
  "produced_by": {
    "skill": "dependency-report",
    "automation_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "run": "20261201-090000-dependency-report",
  "period": {
    "month": "2026-11",
    "since": "2026-11-01T00:00:00Z",
    "until": "2026-12-01T00:00:00Z",
    "complete": true
  },
  "policy": {
    "fix_within_days": {
      "critical": 3,
      "high": 7,
      "medium": 30,
      "low": 90
    }
  },
  "month": {
    "opened": {
      "critical": 0,
      "high": 2,
      "medium": 1,
      "low": 0
    },
    "fixed": {
      "critical": 1,
      "high": 2,
      "medium": 0,
      "low": 0
    },
    "moved": {
      "critical": 0,
      "high": 0,
      "medium": 1,
      "low": 0
    },
    "dismissed": {
      "critical": 0,
      "high": 0,
      "medium": 0,
      "low": 1
    },
    "open_at_start": {
      "critical": 1,
      "high": 1,
      "medium": 1,
      "low": 1
    },
    "open_at_end": {
      "critical": 0,
      "high": 1,
      "medium": 1,
      "low": 0
    },
    "fixed_within_target": {
      "critical": 0,
      "high": 2,
      "medium": 0,
      "low": 0
    },
    "advisories": {
      "fixed": {
        "critical": 1,
        "high": 1,
        "medium": 0,
        "low": 0
      },
      "open_at_end": {
        "critical": 0,
        "high": 1,
        "medium": 1,
        "low": 0
      }
    },
    "days_to_fix": {
      "critical": {
        "count": 1,
        "median": 40.0,
        "max": 40.0
      },
      "high": {
        "count": 2,
        "median": 3.0,
        "max": 3.0
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
    }
  },
  "year_to_date": {
    "since": "2026-01-01T00:00:00Z",
    "opened": {
      "critical": 1,
      "high": 3,
      "medium": 2,
      "low": 1
    },
    "fixed": {
      "critical": 1,
      "high": 2,
      "medium": 0,
      "low": 0
    },
    "moved": {
      "critical": 0,
      "high": 0,
      "medium": 1,
      "low": 0
    },
    "dismissed": {
      "critical": 0,
      "high": 0,
      "medium": 0,
      "low": 1
    },
    "open_at_start": {
      "critical": 0,
      "high": 0,
      "medium": 0,
      "low": 0
    },
    "open_at_end": {
      "critical": 0,
      "high": 1,
      "medium": 1,
      "low": 0
    },
    "fixed_within_target": {
      "critical": 0,
      "high": 2,
      "medium": 0,
      "low": 0
    },
    "advisories": {
      "fixed": {
        "critical": 1,
        "high": 1,
        "medium": 0,
        "low": 0
      },
      "open_at_end": {
        "critical": 0,
        "high": 1,
        "medium": 1,
        "low": 0
      }
    },
    "days_to_fix": {
      "critical": {
        "count": 1,
        "median": 40.0,
        "max": 40.0
      },
      "high": {
        "count": 2,
        "median": 3.0,
        "max": 3.0
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
    }
  },
  "months": [
    {
      "month": "2026-10",
      "fixed": 0,
      "advisories_fixed": 0,
      "open_at_end": 4,
      "critical_open_at_end": 1
    },
    {
      "month": "2026-11",
      "fixed": 3,
      "advisories_fixed": 2,
      "open_at_end": 2,
      "critical_open_at_end": 0
    }
  ],
  "groups": {
    "source": "configured",
    "manifests": {
      "production": [
        "requirements/base.txt"
      ],
      "production_build": [
        "client/*/package*.json"
      ],
      "development": [
        "requirements/dev.txt"
      ],
      "not_deployed": []
    },
    "month": {
      "production": {
        "fixed": 2,
        "advisories_fixed": 2,
        "open_at_end": 1,
        "advisories_open_at_end": 1,
        "critical_open_at_end": 0
      },
      "production_build": {
        "fixed": 0,
        "advisories_fixed": 0,
        "open_at_end": 1,
        "advisories_open_at_end": 1,
        "critical_open_at_end": 0
      },
      "development": {
        "fixed": 1,
        "advisories_fixed": 1,
        "open_at_end": 0,
        "advisories_open_at_end": 0,
        "critical_open_at_end": 0
      },
      "not_deployed": {
        "fixed": 0,
        "advisories_fixed": 0,
        "open_at_end": 0,
        "advisories_open_at_end": 0,
        "critical_open_at_end": 0
      },
      "unclassified": {
        "fixed": 0,
        "advisories_fixed": 0,
        "open_at_end": 0,
        "advisories_open_at_end": 0,
        "critical_open_at_end": 0
      }
    },
    "year_to_date": {
      "production": {
        "fixed": 2,
        "advisories_fixed": 2,
        "open_at_end": 1,
        "advisories_open_at_end": 1,
        "critical_open_at_end": 0
      },
      "production_build": {
        "fixed": 0,
        "advisories_fixed": 0,
        "open_at_end": 1,
        "advisories_open_at_end": 1,
        "critical_open_at_end": 0
      },
      "development": {
        "fixed": 1,
        "advisories_fixed": 1,
        "open_at_end": 0,
        "advisories_open_at_end": 0,
        "critical_open_at_end": 0
      },
      "not_deployed": {
        "fixed": 0,
        "advisories_fixed": 0,
        "open_at_end": 0,
        "advisories_open_at_end": 0,
        "critical_open_at_end": 0
      },
      "unclassified": {
        "fixed": 0,
        "advisories_fixed": 0,
        "open_at_end": 0,
        "advisories_open_at_end": 0,
        "critical_open_at_end": 0
      }
    }
  },
  "open": [
    {
      "ecosystem": "pip",
      "package": "virtualenv",
      "severity": "high",
      "alerts": 1,
      "advisories": 1,
      "oldest_days": 183.0,
      "manifests": [
        "requirements/base.txt"
      ],
      "groups": [
        "production"
      ]
    },
    {
      "ecosystem": "npm",
      "package": "vite",
      "severity": "medium",
      "alerts": 1,
      "advisories": 1,
      "oldest_days": 28.0,
      "manifests": [
        "client/app/package-lock.json"
      ],
      "groups": [
        "production_build"
      ]
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `period` | The calendar month (`month`, `YYYY-MM`) in UTC, from `since` to `until`. `complete` is false for a month still in progress; it then ends when the data was collected. |
| `policy.fix_within_days` | The target time to fix each severity, from `[dependency-retrospective]`, so the report and the retrospective judge against the same targets. |
| `month` | The month's figures, all as of `period.until`, by severity. `opened`, `fixed`, `moved` and `dismissed` happened in the month. `open_at_start` and `open_at_end` are the alerts open at each end of it. `fixed_within_target`: fixed alerts that took no longer than their target. `advisories`: distinct advisories (GHSA ids) among the `fixed` and `open_at_end` alerts. `days_to_fix`: count, median and maximum, from the alert being raised to its fix. `moved` and `dismissed` are never fixes: see `outcomes.md`. |
| `year_to_date` | The same figures from 1 January to `period.until`, plus its `since`. Its `advisories.fixed` counts each advisory once, so it can be less than the sum of the months. |
| `months[]` | One row per month of the year so far, for the trend: alerts and advisories fixed, alerts open at the month's end, and how many of those were critical. |
| `groups` | Where the vulnerable packages are, by the manifest each alert is on: `production` (the code production runs), `production_build` (installed and run on production machines to build what is served), `development`, `not_deployed`, or `unclassified` (a manifest in no group). `source` is `configured` when the project lists its manifests for each group in `[dependency-report]` (`manifests` holds those globs; the first group that matches wins), or `dependabot` when it lists none and Dependabot's own `scope` (runtime or development) is used instead. `month` and `year_to_date` count, per group, alerts and advisories fixed and still open, and critical alerts open. An advisory on manifests in two groups counts in both. |
| `open[]` | What was open at `period.until`, one row per package: its most severe alert, how many advisories and alerts, the oldest alert's age in days, the manifests they are on, and the `groups` those manifests are in. Most severe first, then oldest. |
