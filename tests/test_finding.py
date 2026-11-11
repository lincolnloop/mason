import unittest

from artifacts import finding

from .fixtures import DEPENDENCY, FINDING, make


def errors_for(artifact: dict) -> list[str]:
    return finding.validate(artifact)


class FindingTests(unittest.TestCase):
    def test_valid(self) -> None:
        self.assertEqual(errors_for(FINDING), [])

    def test_not_an_object(self) -> None:
        self.assertEqual(finding.validate([]), ['$: must be an object, got array'])

    def test_missing_field_is_reported_once(self) -> None:
        self.assertEqual(errors_for(make(FINDING, title=...)), ['$.title: is required'])
        self.assertEqual(errors_for(make(FINDING, source=...)), ['$.source: is required'])

    def test_missing_and_unknown_fields(self) -> None:
        errors = errors_for(make(FINDING, claim=..., summary='typo for claim'))
        self.assertIn('$.claim: is required', errors)
        self.assertTrue(any(e.startswith('$.summary: is not a known field') for e in errors))

    def test_wrong_schema_and_envelope(self) -> None:
        errors = errors_for(
            make(FINDING, schema='finding/2', project='no-slash', produced_by={'skill': 'x'})
        )
        self.assertIn("$.schema: must be 'finding/1', got 'finding/2'", errors)
        self.assertTrue(any(e.startswith('$.project: must match owner/repo') for e in errors))
        self.assertIn('$.produced_by.automation_sha: is required', errors)

    def test_dirty_automation_sha_is_allowed(self) -> None:
        artifact = make(
            FINDING,
            produced_by={'skill': 'dependency-upgrades', 'automation_sha': 'a' * 40 + '-dirty'},
        )
        self.assertEqual(errors_for(artifact), [])

    def test_fingerprint_needs_an_area(self) -> None:
        errors = errors_for(make(FINDING, fingerprint='no-area'))
        self.assertTrue(any(e.startswith('$.fingerprint: must match area/slug') for e in errors))

    def test_evidence_is_required_and_typed(self) -> None:
        self.assertIn(
            '$.evidence: must have at least 1 item(s)', errors_for(make(FINDING, evidence=[]))
        )
        errors = errors_for(
            make(FINDING, evidence=[{'ref': 'x', 'note': 'y', 'status': 'probably'}])
        )
        self.assertIn(
            "$.evidence[0].status: must be one of inferred, verified, got 'probably'", errors
        )

    def test_dependency_kind_needs_dependency(self) -> None:
        errors = errors_for(make(FINDING, dependency=...))
        self.assertIn('$.dependency: is required when kind is dependency', errors)

    def test_only_easy_upgrades_go_into_the_pr(self) -> None:
        dependency = {**DEPENDENCY, 'classification': 'major', 'tracked_by': None}
        errors = errors_for(make(FINDING, kind='dependency', dependency=dependency))
        self.assertTrue(
            any(
                e.startswith("$.kind: is dependency, but the classification is 'major'")
                for e in errors
            )
        )
        self.assertEqual(errors_for(make(FINDING, kind='investigate', dependency=dependency)), [])

    def test_eol_needs_tracked_work_searched(self) -> None:
        dependency = {**DEPENDENCY, 'classification': 'eol', 'fixed': None}
        errors = errors_for(make(FINDING, kind='investigate', dependency=dependency))
        self.assertTrue(any(e.startswith('$.dependency.tracked_by: is required') for e in errors))

    def test_fixed_version_consistency(self) -> None:
        no_fix = {**DEPENDENCY, 'classification': 'no_fix'}
        self.assertIn(
            '$.dependency.fixed: must be null when classification is no_fix',
            errors_for(make(FINDING, kind='investigate', dependency=no_fix)),
        )
        easy_without_fix = {**DEPENDENCY, 'fixed': None}
        self.assertIn(
            '$.dependency.fixed: must name the fixed version when classification is easy',
            errors_for(make(FINDING, kind='dependency', dependency=easy_without_fix)),
        )

    def test_reports_every_error(self) -> None:
        errors = errors_for(make(FINDING, kind='nope', severity='high', title=''))
        self.assertEqual(len(errors), 3)


if __name__ == '__main__':
    unittest.main()
