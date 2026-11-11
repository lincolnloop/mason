import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from artifacts import lock

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


class LockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.lock = lock.path(
            Path(self._tmp.name), Path('/work/example-app'), 'dependency-upgrades'
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_path(self) -> None:
        self.assertEqual(self.lock.parts[-3:], ('runs', 'example-app', 'dependency-upgrades.lock'))

    def test_one_at_a_time(self) -> None:
        self.assertTrue(lock.acquire(self.lock, NOW)[0])
        taken, message = lock.acquire(self.lock, NOW + timedelta(minutes=5))
        self.assertFalse(taken)
        self.assertIn('another run is active', message)

    def test_release_then_acquire(self) -> None:
        lock.acquire(self.lock, NOW)
        lock.release(self.lock)
        self.assertTrue(lock.acquire(self.lock, NOW)[0])

    def test_releasing_an_absent_lock_is_fine(self) -> None:
        lock.release(self.lock)

    def test_stale_lock_is_replaced(self) -> None:
        lock.acquire(self.lock, NOW)
        taken, message = lock.acquire(self.lock, NOW + lock.STALE_AFTER + timedelta(minutes=1))
        self.assertTrue(taken)
        self.assertIn('replaced a stale lock taken at 2026-11-11T12:00:00Z', message)

    def test_lock_without_a_time_falls_back_to_the_directory_time(self) -> None:
        self.lock.mkdir(parents=True)  # as if another run had only just taken it
        self.assertFalse(lock.acquire(self.lock)[0])
        later = datetime.now(UTC) + lock.STALE_AFTER + timedelta(minutes=1)
        self.assertTrue(lock.acquire(self.lock, later)[0])


if __name__ == '__main__':
    unittest.main()
