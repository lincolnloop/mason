# `run/1`

Who or what started a workflow run, where, and when, and how it ended. Every run writes one,
so every run is accountable. The record is published where people already look, not left on the
machine that ran it:

- a run that opens a PR embeds it in the PR body, with a one-line summary at the end;
- a run that stops because an earlier upgrade PR is still open posts it in a comment on that PR,
  with the open alerts the PR doesn't cover. The PR itself is not changed, so a reviewer
  part-way through it isn't surprised by new commits.

`artifact run-start <project> <workflow>` writes it at the start of a run. The renderers set
`outcome` when they publish it.

```json
{
  "schema": "run/1",
  "project": "example-org/example-app",
  "produced_by": {"skill": "dependency-upgrades", "automation_sha": "889bd9b0000000000000000000000000000000aa"},
  "id": "20261111-093000-dependency-upgrades",
  "trigger": "unattended",
  "started_by": "systemd timer dependency-upgrades.timer",
  "host": "factory-1",
  "started_at": "2026-11-11T09:30:00Z",
  "outcome": "stopped_pr_open",
  "pr": 42,
  "alerts_not_covered": [651, 652]
}
```

| Field | Meaning |
|---|---|
| `id` | `<UTC stamp>-<workflow>`. Also names the run's directory and its branch. |
| `trigger` | `interactive` (a person in a session) or `unattended` (`AUTOMATION_UNATTENDED=1`). |
| `started_by` | `AUTOMATION_TRIGGER` if the launcher set it (`bin/unattended --trigger`), otherwise the GitHub login of the person running it. |
| `host` | The machine that ran it, so its log under `runs/` can be found. |
| `started_at` | When the run started, in UTC. |
| `outcome` | Set when the record is published: `pr_opened` (the PR carrying it is the run's), or `stopped_pr_open`. Absent while the run is in progress. |
| `pr`, `alerts_not_covered` | Only for `stopped_pr_open`: the open PR the run stopped for, and the open alerts that PR doesn't account for (not in its change, and not cited by its findings). |

A run that finds nothing to do, or stops for another reason (another run holds the lock, the
open-PR limit is reached), publishes no record: there is no PR for it to go on. Its report says
why, and the alert history still shows how long alerts stay open.
