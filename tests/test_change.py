import unittest

from artifacts import change

from .fixtures import CHANGE, make


class ChangeTests(unittest.TestCase):
    def test_valid(self) -> None:
        self.assertEqual(change.validate(CHANGE), [])

    def test_failing_check_is_rejected(self) -> None:
        errors = change.validate(make(CHANGE, verified=[{'check': 'make test', 'result': 'fail'}]))
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith('$.verified[0].result: is fail'))

    def test_skipped_check_needs_a_reason(self) -> None:
        errors = change.validate(
            make(CHANGE, verified=[{'check': 'npm run build', 'result': 'skipped'}])
        )
        self.assertTrue(
            any(
                e.startswith('$.verified[0].note: is required when result is skipped')
                for e in errors
            )
        )
        ok = make(
            CHANGE,
            verified=[{'check': 'npm run build', 'result': 'skipped', 'note': 'no node here'}],
        )
        self.assertEqual(change.validate(ok), [])

    def test_check_left_to_ci(self) -> None:
        verified = [{'check': 'make test', 'result': 'ci'}]
        self.assertEqual(change.validate(make(CHANGE, verified=verified)), [])

    def test_something_must_be_said_about_verification(self) -> None:
        errors = change.validate(make(CHANGE, verified=[], not_verified=[]))
        self.assertTrue(any('both empty' in e for e in errors))

    def test_machine_checked_signal_needs_refs(self) -> None:
        signal = {**CHANGE['success_signal'], 'refs': []}
        errors = change.validate(make(CHANGE, success_signal=signal))
        self.assertIn(
            '$.success_signal.refs: must name what to check when kind is alerts_closed', errors
        )

    def test_manual_signal_without_refs(self) -> None:
        signal = {
            'kind': 'manual',
            'refs': [],
            'check_after_days': 0,
            'description': 'Someone confirms the image upload still works',
        }
        self.assertEqual(change.validate(make(CHANGE, success_signal=signal)), [])

    def test_signal_window_is_bounded(self) -> None:
        signal = {**CHANGE['success_signal'], 'check_after_days': 365}
        self.assertIn(
            '$.success_signal.check_after_days: must be at most 90, got 365',
            change.validate(make(CHANGE, success_signal=signal)),
        )

    def test_findings_are_unique_and_present(self) -> None:
        self.assertIn(
            '$.findings: must have at least 1 item(s)', change.validate(make(CHANGE, findings=[]))
        )
        errors = change.validate(make(CHANGE, findings=['a', 'a']))
        self.assertTrue(any('duplicates' in e for e in errors))


if __name__ == '__main__':
    unittest.main()
