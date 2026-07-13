from __future__ import annotations

from concurrent.futures import CancelledError
import threading
import time
import unittest

from quick_sdf_blender.jobs import (
    GenerationJobManager,
    JobNotReadyError,
    JobState,
)


def wait_for_state(
    manager: GenerationJobManager,
    states: set[JobState],
    *,
    timeout: float = 2.0,
) -> JobState:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = manager.poll()
        if state in states:
            return state
        time.sleep(0.002)
    raise AssertionError(f"job did not reach {states}; last state was {manager.poll()}")


class GenerationJobManagerTests(unittest.TestCase):
    def test_submit_passes_only_explicit_arguments_and_returns_result(self):
        manager = GenerationJobManager()
        try:
            identifier = manager.submit(
                lambda first, second, *, scale: (first + second) * scale,
                2,
                3,
                scale=4,
            )
            self.assertEqual(identifier, 1)
            self.assertEqual(manager.job_id, identifier)
            self.assertEqual(
                wait_for_state(manager, {JobState.SUCCEEDED}),
                JobState.SUCCEEDED,
            )
            self.assertEqual(manager.take_result(), 20)
            self.assertIsNone(manager.poll())
            self.assertIsNone(manager.job_id)
        finally:
            manager.shutdown()

    def test_poll_is_non_blocking_and_take_rejects_running_job(self):
        manager = GenerationJobManager()
        release = threading.Event()
        try:
            manager.submit(release.wait)
            wait_for_state(manager, {JobState.RUNNING})
            started = time.monotonic()
            self.assertEqual(manager.poll(), JobState.RUNNING)
            self.assertLess(time.monotonic() - started, 0.05)
            with self.assertRaises(JobNotReadyError):
                manager.take_result()
        finally:
            release.set()
            manager.shutdown()

    def test_exception_is_retained_and_reraised_by_take_result(self):
        manager = GenerationJobManager()
        failure = ValueError("bad mask stack")

        def fail():
            raise failure

        try:
            manager.submit(fail)
            wait_for_state(manager, {JobState.FAILED})
            self.assertIs(manager.exception, failure)
            with self.assertRaises(ValueError) as caught:
                manager.take_result()
            self.assertIs(caught.exception, failure)
            self.assertIsNone(manager.poll())
        finally:
            manager.shutdown()

    def test_cancel_marks_running_job_and_discards_late_value(self):
        manager = GenerationJobManager()
        started = threading.Event()
        release = threading.Event()

        def work():
            started.set()
            release.wait()
            return "stale"

        try:
            manager.submit(work)
            self.assertTrue(started.wait(1.0))
            self.assertTrue(manager.cancel())
            self.assertEqual(manager.poll(), JobState.CANCELLED)
            self.assertFalse(manager.cancel())
            with self.assertRaises(CancelledError):
                manager.take_result()
            self.assertIsNone(manager.poll())
        finally:
            release.set()
            manager.shutdown()

    def test_new_submit_cancels_old_job_and_single_worker_orders_execution(self):
        manager = GenerationJobManager()
        old_started = threading.Event()
        release_old = threading.Event()
        replacement_started = threading.Event()

        def old_work():
            old_started.set()
            release_old.wait()
            raise AssertionError("a cancelled exception must be discarded")

        def replacement():
            replacement_started.set()
            return "current"

        try:
            old_id = manager.submit(old_work)
            self.assertTrue(old_started.wait(1.0))
            new_id = manager.submit(replacement)
            self.assertGreater(new_id, old_id)
            self.assertEqual(manager.job_id, new_id)
            self.assertEqual(manager.poll(), JobState.PENDING)
            self.assertFalse(replacement_started.wait(0.03))
            release_old.set()
            self.assertTrue(replacement_started.wait(1.0))
            wait_for_state(manager, {JobState.SUCCEEDED})
            self.assertEqual(manager.take_result(), "current")
        finally:
            release_old.set()
            manager.shutdown()

    def test_optional_cancel_event_is_set_without_argument_injection(self):
        manager = GenerationJobManager()
        release = threading.Event()
        try:
            manager.submit(release.wait)
            wait_for_state(manager, {JobState.RUNNING})
            event = manager.cancel_event
            self.assertIsNotNone(event)
            self.assertFalse(event.is_set())
            manager.cancel()
            self.assertTrue(event.is_set())
        finally:
            release.set()
            manager.shutdown()

    def test_shutdown_cancels_job_is_idempotent_and_prevents_submit(self):
        manager = GenerationJobManager()
        started = threading.Event()
        release = threading.Event()

        def work():
            started.set()
            release.wait()

        manager.submit(work)
        self.assertTrue(started.wait(1.0))
        manager.shutdown(wait=False)
        manager.shutdown()
        self.assertEqual(manager.poll(), JobState.CANCELLED)
        with self.assertRaises(RuntimeError):
            manager.submit(lambda: 2)
        release.set()


if __name__ == "__main__":
    unittest.main()
