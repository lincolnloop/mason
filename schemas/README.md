# Artifact schemas

The contracts a workflow writes, as described in `docs/design.md` §3. The validators in
`lib/artifacts/` are the source of truth. These pages explain the fields and why they exist.
Every `json` example on these pages is validated by `tests/test_schema_docs.py`, so an example
that drifts from the code fails the tests.

| Schema | Written by | Read by |
|---|---|---|
| [`finding/1`](finding.md) | `dependency-upgrades`, one per flagged package | the renderer; the person deciding the PR |
| [`change/1`](change.md) | `dependency-upgrades`, one per PR | the renderer; the person deciding the PR |
| [`outcomes/1`](outcomes.md) | `artifact collect-outcomes`, for a retrospective | the `dependency-retrospective` skill; the report |
| [`run/1`](run.md) | every run, at its start | the renderer; anyone asking who started a PR, or why a run opened none |

## Shared fields

Every artifact has:

- `schema`: the name and major version, e.g. `"finding/1"`.
- `project`: the consuming repository, `owner/repo`.
- `produced_by`:
  - `skill`: the skill that wrote it.
  - `automation_sha`: the commit of this repository it ran from, with `-dirty` appended for
    uncommitted changes.
  - `model` (optional).

## Validating

```sh
python3 -I path/to/mason/bin/artifact validate finding.json
```

A validator reports every error, each naming the field (`$.dependency.fixed`) and what to
do about it. It never rewrites the artifact. Unknown fields are errors, so a misspelt key is
caught, not ignored.

## Versioning

A change that makes an existing valid artifact invalid, or that changes a field's meaning, is a
new major version (`finding/2`). The validator then accepts both versions until every skill
writes the new one. Adding an optional field is not a new version.
