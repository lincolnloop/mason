# `change/1`

What a workflow attaches to the PR it opens. It answers the questions a reader would otherwise
have to work out: what does merging this do, what does it touch, what was checked, and how will
we know it worked? The full example is under the derived fields, below.

| Field | Meaning |
|---|---|
| `run` | The id of the run that opened the PR. Its `run/1` record is embedded beside the change. |
| `findings` | The ids of the `dependency` findings this PR upgrades. |
| `branch` | The PR's head branch. |
| `head_sha` | The commit the PR was opened at. If the merged head differs, someone changed the PR before merging it, which a retrospective counts. |
| `summary` | One line; also the PR title. |
| `effect` | What changes, for people and for agents, in a sentence or two. |
| `blast_radius` | What the change touches, including the things it deliberately doesn't change. |
| `verified[]` | Each check, with its `result`: `pass` (ran here and passed), `ci` (not run here; CI runs it on the PR, and whoever merges checks it) or `skipped` (didn't run anywhere; needs a `note` saying why). A `fail` is rejected: a change with a failing check isn't ready for a PR. |
| `not_verified` | What wasn't checked. `verified` and `not_verified` can't both be empty. |
| `success_signal` | How we will know it worked, checked `check_after_days` after the merge. |
| `revert` | How to undo it. |

## Derived fields: `dependencies` and `alerts`

These are optional, and **never written by hand**. A dependency upgrade produces them from the
repository and the alert list:

```sh
artifact deps-diff --repo . --base origin/main --pip 'requirements/*.txt' --npm '**/package-lock.json' > deps.json
artifact alerts-closed --repo . --base origin/main --alerts alerts.json --pip … --npm … > alerts.json
```

- `deps-diff` reads every compiled requirements file and npm lockfile at both revisions. It
  lists every package whose version moved, transitive ones included, in one row per version
  change, with every manifest it changed in.
- `alerts-closed` decides each open alert from the versions installed before and after:
  - `closed`: vulnerable before, not after;
  - `already_fixed`: not vulnerable before, so this change can't take the credit;
  - `undecided`: a version or range it can't order, to check by hand.

  One vulnerable nested npm copy keeps an alert open.

The renderer builds the PR's package and alert tables from these fields. It marks major bumps
itself.

```json
{
  "schema": "change/1",
  "project": "example-org/example-app",
  "produced_by": {"skill": "dependency-upgrades", "automation_sha": "889bd9b0000000000000000000000000000000aa"},
  "run": "20261111-093000-dependency-upgrades",
  "findings": ["dependabot-2026-11-urllib3"],
  "branch": "dependency-upgrades-20261111-093000",
  "head_sha": "5f3a2c1b9e8d7f6a5b4c3d2e1f0a9b8c7d6e5f4a",
  "summary": "Security upgrades: easy Dependabot fixes (November 2026)",
  "effect": "Closes 2 of the 8 open alerts by moving urllib3, and the certifi it brings, to patched versions.",
  "blast_radius": ["requirements/base.txt and requirements/dev.txt only"],
  "verified": [{"check": "make test", "result": "pass"}],
  "not_verified": [],
  "success_signal": {
    "kind": "alerts_closed",
    "refs": ["dependabot#640", "dependabot#641"],
    "check_after_days": 1,
    "description": "The listed alerts are closed by Dependabot once the PR merges"
  },
  "revert": "Revert the PR; each package is its own commit.",
  "dependencies": [
    {"ecosystem": "pip", "package": "urllib3", "before": "2.7.0", "after": "2.8.0", "manifests": ["requirements/base.txt", "requirements/dev.txt"], "direct": true, "via": [], "copy": null},
    {"ecosystem": "pip", "package": "certifi", "before": "2025.1.31", "after": "2026.10.18", "manifests": ["requirements/base.txt", "requirements/dev.txt"], "direct": false, "via": ["requests"], "copy": null}
  ],
  "alerts": {
    "open_before": {"critical": 2, "high": 4, "medium": 2, "low": 0},
    "closed": [
      {"number": 640, "package": "urllib3", "severity": "high"},
      {"number": 641, "package": "certifi", "severity": "critical"}
    ],
    "already_fixed": [],
    "undecided": []
  }
}
```

## `success_signal.kind`

| Kind | Checked by | `refs` |
|---|---|---|
| `alerts_closed` | the Dependabot alerts are closed | alert numbers, as `dependabot#<N>` (required) |
| `sentry_quiet` | the Sentry issues get no new events after the fix is deployed, for `check_after_days`; the Sentry retrospective checks it | Sentry short ids (required) |
| `manual` | a person, as described | anything useful |

A signal a machine can check is worth more than `manual`. Use `manual` only when nothing else
fits, and make `description` specific enough to judge.
