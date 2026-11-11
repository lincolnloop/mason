import unittest

from artifacts import review

from .fixtures import DIFF, REVIEW, REVIEW_FINDING, SHA_A, SHA_C, make, review_with


def located(**location_changes) -> dict:
    location = {**REVIEW_FINDING['location']}
    for key, value in location_changes.items():
        if value is ...:
            location.pop(key, None)
        else:
            location[key] = value
    return location


class StructureTests(unittest.TestCase):
    def test_valid(self) -> None:
        self.assertEqual(review.validate(REVIEW, DIFF), [])

    def test_valid_without_findings(self) -> None:
        self.assertEqual(review.validate(make(REVIEW, findings=[]), DIFF), [])

    def test_no_verdict_or_counts(self) -> None:
        errors = review.validate(make(REVIEW, verdict='APPROVE'), DIFF)
        self.assertTrue(errors[0].startswith('$.verdict: is not a known field'))

    def test_shas_are_full_length(self) -> None:
        errors = review.validate(make(REVIEW, head_sha='abc123'), DIFF)
        self.assertTrue(errors[0].startswith('$.head_sha: must match a 40-character commit SHA'))

    def test_followup_must_be_a_different_head(self) -> None:
        errors = review.validate(make(REVIEW, followup_of=SHA_C), DIFF)
        self.assertIn(
            '$.followup_of: is the same commit as head_sha; a follow-up reviews a later head',
            errors,
        )

    def test_carried_status_needs_a_followup(self) -> None:
        errors = review.validate(review_with(status='addressed'), DIFF)
        self.assertTrue(
            errors[0].startswith("$.findings[0].status: is 'addressed', but $.followup_of is null")
        )
        artifact = make(review_with(status='addressed'), followup_of=SHA_A)
        self.assertEqual(review.validate(artifact, DIFF), [])

    def test_nit_needs_a_suggestion(self) -> None:
        errors = review.validate(review_with(severity='nit'), DIFF)
        self.assertTrue(errors[0].startswith('$.findings[0].suggestion: is required for a nit'))

    def test_duplicate_ids_and_fingerprints(self) -> None:
        artifact = make(REVIEW, findings=[REVIEW_FINDING, REVIEW_FINDING])
        errors = review.validate(artifact, DIFF)
        self.assertIn("$.findings[1].id: duplicates $.findings[0].id ('F1')", errors)
        self.assertIn(
            "$.findings[1].fingerprint: duplicates $.findings[0].fingerprint ('app/lookup-500')",
            errors,
        )

    def test_whole_pr_finding_has_no_location(self) -> None:
        self.assertEqual(review.validate(review_with(location=None), DIFF), [])

    def test_suggestion_needs_a_location(self) -> None:
        errors = review.validate(review_with(location=None, suggestion='x'), DIFF)
        self.assertIn('$.findings[0].suggestion: needs a location in the diff to apply to', errors)

    def test_suggestion_is_unfenced(self) -> None:
        errors = review.validate(review_with(suggestion='```suggestion\n    x = 1\n```'), DIFF)
        self.assertTrue(
            errors[0].startswith('$.findings[0].suggestion: must be the replacement text only')
        )

    def test_path_has_no_prefix(self) -> None:
        errors = review.validate(review_with(location=located(path='b/app/views.py')), DIFF)
        self.assertTrue(errors[0].startswith('$.findings[0].location.path: must be relative'))

    def test_too_many_inline_findings(self) -> None:
        findings = [
            make(REVIEW_FINDING, id=f'F{n}', fingerprint=f'app/f{n}')
            for n in range(1, review.MAX_INLINE + 2)
        ]
        errors = review.validate(make(REVIEW, findings=findings), DIFF)
        self.assertEqual(
            errors,
            [
                f'$.findings: has {review.MAX_INLINE + 1} findings in the diff; at most {review.MAX_INLINE} can be posted inline'
            ],
        )


class LocationTests(unittest.TestCase):
    def test_added_line_on_right(self) -> None:
        self.assertEqual(review.validate(review_with(location=located(line=13)), DIFF), [])

    def test_removed_line_on_left(self) -> None:
        self.assertEqual(
            review.validate(review_with(location=located(line=12, side='LEFT')), DIFF), []
        )

    def test_line_outside_the_hunk(self) -> None:
        errors = review.validate(review_with(location=located(line=40)), DIFF)
        self.assertEqual(len(errors), 1)
        self.assertTrue(
            errors[0].startswith(
                '$.findings[0].location.line: app/views.py:40 on RIGHT is not in the diff'
            )
        )

    def test_new_file_line_number_on_left(self) -> None:
        # RIGHT 14 exists ('last = 3'); LEFT 14 does not, since the old file ends the hunk at 13.
        errors = review.validate(review_with(location=located(line=14, side='LEFT')), DIFF)
        self.assertTrue(
            errors[0].startswith('$.findings[0].location.line: app/views.py:14 on LEFT')
        )

    def test_file_not_in_diff(self) -> None:
        errors = review.validate(review_with(location=located(path='app/models.py')), DIFF)
        self.assertEqual(
            errors,
            [
                "$.findings[0].location.path: 'app/models.py' is not in the PR diff; set in_diff false"
            ],
        )

    def test_out_of_diff_finding_is_allowed_when_marked(self) -> None:
        location = located(path='app/models.py', line=171, in_diff=False)
        self.assertEqual(review.validate(review_with(location=location), DIFF), [])

    def test_in_diff_false_on_a_diff_line_is_a_lie(self) -> None:
        errors = review.validate(review_with(location=located(in_diff=False)), DIFF)
        self.assertEqual(
            errors,
            [
                '$.findings[0].location.in_diff: is false, but app/views.py:12 on RIGHT is in the diff; set it true'
            ],
        )

    def test_range(self) -> None:
        location = located(start_line=11, start_side='RIGHT', line=13)
        self.assertEqual(review.validate(review_with(location=location), DIFF), [])

    def test_range_rules(self) -> None:
        cases = {
            'start_line and start_side must be given together': located(start_line=11),
            'must equal side': located(start_line=11, start_side='LEFT'),
            'must be before line': located(start_line=12, start_side='RIGHT'),
            'spans more than 10 lines': located(start_line=1, start_side='RIGHT', line=12),
        }
        for message, location in cases.items():
            with self.subTest(message):
                errors = review.validate(review_with(location=location), DIFF)
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(message, errors[0])

    def test_range_must_stay_inside_the_diff(self) -> None:
        location = located(start_line=9, start_side='RIGHT', line=11)
        errors = review.validate(review_with(location=location), DIFF)
        self.assertTrue(
            errors[0].startswith('$.findings[0].location.line: app/views.py:9-11 on RIGHT')
        )

    def test_structure_only_without_a_diff(self) -> None:
        self.assertEqual(review.validate(review_with(location=located(line=40))), [])


class SuggestionTests(unittest.TestCase):
    def test_replacement_for_the_line(self) -> None:
        artifact = review_with(suggestion='    new = get_object_or_404(Model, slug=slug)')
        self.assertEqual(review.validate(artifact, DIFF), [])

    def test_repeating_the_line_above_duplicates_it(self) -> None:
        artifact = review_with(
            suggestion='    second = 2\n    new = get_object_or_404(Model, slug=slug)'
        )
        errors = review.validate(artifact, DIFF)
        self.assertEqual(
            errors,
            [
                '$.findings[0].suggestion: starts with line 11, which is outside the range and would be duplicated'
            ],
        )

    def test_repeating_the_line_below_duplicates_it(self) -> None:
        artifact = review_with(
            suggestion='    new = get_object_or_404(Model, slug=slug)\n    extra = True'
        )
        errors = review.validate(artifact, DIFF)
        self.assertEqual(
            errors,
            [
                '$.findings[0].suggestion: ends with line 13, which is outside the range and would be duplicated'
            ],
        )

    def test_suggestion_outside_the_diff(self) -> None:
        location = located(path='app/models.py', line=171, in_diff=False)
        errors = review.validate(review_with(location=location, suggestion='x'), DIFF)
        self.assertTrue(
            errors[0].startswith('$.findings[0].suggestion: needs a location with in_diff true')
        )


if __name__ == '__main__':
    unittest.main()
