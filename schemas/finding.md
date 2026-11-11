# `finding/1`

One flagged package, with enough evidence for the person deciding the PR to act on it without
re-investigating. A pass writes one for every package that needs something done, whether or not
it goes into the PR.

```json
{
  "schema": "finding/1",
  "project": "example-org/example-app",
  "produced_by": {"skill": "dependency-upgrades", "automation_sha": "889bd9b0000000000000000000000000000000aa"},
  "id": "dependabot-2026-11-urllib3",
  "fingerprint": "deps/pip/urllib3",
  "source": {"process": "dependency-upgrades", "refs": ["dependabot#640"]},
  "kind": "dependency",
  "title": "urllib3 2.7.0 has a fixed vulnerability",
  "claim": "urllib3 2.8.0 fixes the advisory, and no other package pins it below that.",
  "severity": "important",
  "evidence": [
    {"ref": "requirements/base.txt", "note": "urllib3==2.7.0, via requests", "status": "verified"},
    {"ref": "dependabot#640", "note": "first patched version 2.8.0", "status": "verified"}
  ],
  "dependency": {
    "ecosystem": "pip",
    "package": "urllib3",
    "installed": "2.7.0",
    "fixed": "2.8.0",
    "manifests": ["requirements/base.txt", "requirements/dev.txt"],
    "classification": "easy"
  }
}
```

| Field | Meaning |
|---|---|
| `id` | Unique within the project. A lowercase slug. |
| `fingerprint` | `area/slug`, stable across passes (`deps/<ecosystem>/<package>`), so the same package flagged in two passes is recognised as one. |
| `source.process`, `source.refs` | What found it, and the alert numbers, as `dependabot#<N>`. |
| `kind` | What happens next. `dependency` goes into the upgrade PR. `investigate` needs a person or its own PR (an EOL line, a major bump, a blocked range). `external` is waiting on someone else, such as an upstream fix. `skill_change` is a change to this repository's skills, proposed by a retrospective. |
| `title`, `claim` | One line, then the specific claim the evidence supports. |
| `severity` | `critical`, `important` or `suggestion`. |
| `evidence[]` | Each item has a `ref` (file, alert, URL), a `note`, and a `status` of `verified` (checked against the files or the advisory) or `inferred`. |
| `dependency` | The package. Required when `kind` is `dependency`, and allowed on any kind. |

## `dependency`

An end-of-life release line, which is an `investigate` finding, not part of the PR:

```json
{
  "schema": "finding/1",
  "project": "example-org/example-app",
  "produced_by": {"skill": "dependency-upgrades", "automation_sha": "889bd9b0000000000000000000000000000000aa"},
  "id": "dependabot-2026-11-django",
  "fingerprint": "deps/pip/django",
  "source": {"process": "dependency-upgrades", "refs": ["dependabot#212", "dependabot#215"]},
  "kind": "investigate",
  "title": "Django 5.1 is end-of-life; its advisories have no 5.1 fix",
  "claim": "Neither advisory lists a range covering 5.1.x, so no point release will close them.",
  "severity": "important",
  "evidence": [
    {"ref": "requirements/base.txt", "note": "django==5.1.14 pinned in base.in", "status": "verified"}
  ],
  "dependency": {
    "ecosystem": "pip",
    "package": "django",
    "installed": "5.1.14",
    "fixed": null,
    "manifests": ["requirements/base.txt"],
    "classification": "eol",
    "tracked_by": "https://github.com/example-org/example-app/issues/120"
  }
}
```

`classification` follows the order the `dependency-upgrades` skill checks in: `already_fixed`,
`no_fix`, `eol`, `blocked`, `major`, `easy`. The validator enforces the consequences:

- `no_fix` has `fixed: null`.
- `easy`, `major` and `blocked` name the fixed version.
- `eol` and `major` must have `tracked_by`: a link to existing tracked work, or `null` once
  you've searched and found none. Without it, a stalled upgrade gets re-described as new work.

Only an `easy` classification can have `kind: "dependency"`, the kind that goes into the PR.
The validator rejects any other. `eol`, `major` and `blocked` findings are `investigate`, and
`no_fix` is `external`. `already_fixed` needs no finding.
