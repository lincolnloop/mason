# `assessment/1`

Every open Dependabot alert, and what was decided about it. `dependency-assessment` writes one each
time it runs, with a `finding/1` per package, into the month's assessment issue
(`Dependency assessment: <Month YYYY>`). The issue's body is always the latest assessment, so it
can run as often as wanted; each run also comments on what changed.

It is the hand-off to `dependency-upgrades`, which applies the `easy` findings, and which refuses to
start from an assessment that doesn't cover every alert open now (`mason check-assessment`).

`mason build-assessment` builds it from the run's alerts and findings, and refuses unless every
open alert is accounted for exactly once: by one finding (its `source.refs`), or as already fixed by
what is installed.

```json
{
  "schema": "assessment/1",
  "project": "example/project",
  "produced_by": {
    "skill": "dependency-assessment",
    "automation_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "run": "20261119-090000-dependency-assessment",
  "assessed_at": "2026-11-19T09:00:00Z",
  "commit": "cccccccccccccccccccccccccccccccccccccccc",
  "alerts": [
    {
      "number": 1,
      "ecosystem": "pip",
      "package": "pillow",
      "manifest": "requirements/base.txt",
      "severity": "critical",
      "classification": "major",
      "finding": "dependabot-2026-11-pillow"
    },
    {
      "number": 2,
      "ecosystem": "pip",
      "package": "pillow",
      "manifest": "requirements/base.txt",
      "severity": "high",
      "classification": "major",
      "finding": "dependabot-2026-11-pillow"
    },
    {
      "number": 3,
      "ecosystem": "pip",
      "package": "urllib3",
      "manifest": "requirements/base.txt",
      "severity": "high",
      "classification": "easy",
      "finding": "dependabot-2026-11-urllib3"
    },
    {
      "number": 4,
      "ecosystem": "pip",
      "package": "certifi",
      "manifest": "requirements/base.txt",
      "severity": "high",
      "classification": "already_fixed",
      "finding": null
    }
  ],
  "findings": [
    "dependabot-2026-11-pillow",
    "dependabot-2026-11-urllib3"
  ]
}
```

| Field | Meaning |
|---|---|
| `assessed_at` | When the alerts were read. |
| `commit` | The commit of the main branch the installed versions were read from. |
| `alerts[]` | Every alert open when it was made, by number: its package, manifest and severity, the `classification` decided for it (as in `finding/1`), and the `finding` that covers it, or null when installed versions already fix it (`already_fixed`). |
| `findings` | The ids of the findings embedded beside it. |
