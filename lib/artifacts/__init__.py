"""Artifact contracts: `finding` (one thing a workflow found), `change` (the pull request it
opens), `run` (who started the run, where, and how it ended) and `outcomes` (what happened to a
workflow's PRs and the project's alerts over a period).

Each module has `SCHEMA` (the value of the artifact's `schema` field) and `validate(artifact)`,
which returns a list of errors, empty when the artifact is valid.
"""

from typing import Any

from . import change, finding, outcomes, run

VALIDATORS = {
    finding.SCHEMA: finding.validate,
    change.SCHEMA: change.validate,
    run.SCHEMA: run.validate,
    outcomes.SCHEMA: outcomes.validate,
}


def validate(artifact: Any) -> list[str]:
    """Validate any artifact, choosing the validator from its `schema` field."""
    schema = artifact.get('schema') if isinstance(artifact, dict) else None
    validator = VALIDATORS.get(schema)
    if validator is None:
        return [f'$.schema: must be one of {", ".join(sorted(VALIDATORS))}, got {schema!r}']
    return validator(artifact)
