"""Artifact contracts.

- `finding`: one thing a workflow found.
- `change`: the pull request it opens.
- `run`: who started a run, where, and how it ended.
- `outcomes`: what happened afterwards, for a retrospective.
- `assessment`: every open alert, and what was decided about it.
- `dependency-report`: a month's vulnerability figures, for the people the project answers to.

Each module has `SCHEMA` (the value of the artifact's `schema` field) and `validate(artifact)`,
which returns a list of errors, empty when the artifact is valid.
"""

from typing import Any

from . import (
    assessment,
    change,
    finding,
    outcomes,
    report,
    run,
)

VALIDATORS = {
    finding.SCHEMA: finding.validate,
    change.SCHEMA: change.validate,
    run.SCHEMA: run.validate,
    outcomes.SCHEMA: outcomes.validate,
    report.SCHEMA: report.validate,
    assessment.SCHEMA: assessment.validate,
}


def validate(artifact: Any) -> list[str]:
    """Validate any artifact, choosing the validator from its `schema` field."""
    schema = artifact.get('schema') if isinstance(artifact, dict) else None
    validator = VALIDATORS.get(schema)
    if validator is None:
        known = ', '.join(sorted(VALIDATORS))
        return [f'$.schema: must be one of {known}, got {schema!r}']
    return validator(artifact)
