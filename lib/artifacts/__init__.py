"""Artifact contracts.

- `finding`: one thing a workflow found.
- `change`: the pull request it opens.
- `review`: a review of a pull request.
- `run`: who started a run, where, and how it ended.
- `outcomes`, `review-outcomes`, `sentry-outcomes`: what happened afterwards, for a
  retrospective.

Each module has `SCHEMA` (the value of the artifact's `schema` field) and `validate(artifact)`,
which returns a list of errors, empty when the artifact is valid. A review is validated against
the PR's unified diff as well, to check its locations.
"""

from typing import Any

from . import change, finding, outcomes, review, review_outcomes, run, sentry_outcomes

VALIDATORS = {
    finding.SCHEMA: finding.validate,
    change.SCHEMA: change.validate,
    run.SCHEMA: run.validate,
    outcomes.SCHEMA: outcomes.validate,
    review_outcomes.SCHEMA: review_outcomes.validate,
    sentry_outcomes.SCHEMA: sentry_outcomes.validate,
}


def validate(artifact: Any, diff_text: str | None = None) -> list[str]:
    """Validate any artifact, choosing the validator from its `schema` field."""
    schema = artifact.get('schema') if isinstance(artifact, dict) else None
    if schema == review.SCHEMA:
        if diff_text is None:
            return ['$: a review must be validated against the PR diff (pass --diff)']
        return review.validate(artifact, diff_text)
    validator = VALIDATORS.get(schema)
    if validator is None:
        known = ', '.join(sorted([*VALIDATORS, review.SCHEMA]))
        return [f'$.schema: must be one of {known}, got {schema!r}']
    if diff_text is not None:
        return ['$: --diff only applies to a review']
    return validator(artifact)
