# SPDX-License-Identifier: GPL-3.0-or-later
"""Small Blender-independent worker used by threshold generation.

This module intentionally imports only the Python standard library.  Arrays,
Blender data and generation functions must be captured on the main thread and
passed explicitly to :meth:`GenerationJobManager.submit`.  In particular, a
worker must never reach into ``bpy`` data while it is running.

Cancellation is necessarily cooperative at the thread boundary: Python cannot
safely stop a function which has already started.  Cancelling a running job
marks it cancelled immediately and discards its eventual value or exception.
Submitting another job always performs that cancellation first.  With one
executor worker the replacement then starts after the old callable returns.
"""

from __future__ import annotations

from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
import threading
from typing import Any, Callable


class JobState(str, Enum):
    """Observable lifecycle of the manager's current job."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobNotReadyError(RuntimeError):
    """Raised when a result is requested before a job reaches a final state."""


@dataclass(slots=True)
class _Job:
    identifier: int
    state: JobState = JobState.PENDING
    future: Future[None] | None = None
    result: Any = None
    exception: BaseException | None = None
    cancel_requested: bool = False
    # The event is useful to clients which explicitly pass it to their own
    # callable.  It is never injected into the callable's arguments.
    cancel_event: threading.Event = field(default_factory=threading.Event)


class GenerationJobManager:
    """Run at most one current generation job on a single worker thread.

    ``poll`` and ``take_result`` never block.  ``take_result`` consumes a
    terminal job: it returns a successful value, re-raises the saved exception,
    or raises :class:`concurrent.futures.CancelledError` for a cancelled job.
    A function is invoked with exactly the positional and keyword arguments
    supplied to ``submit``; no cancellation token, NumPy object or Blender
    context is implicitly added.
    """

    def __init__(self, *, thread_name_prefix: str = "QuickSDFGenerate") -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=thread_name_prefix,
        )
        self._lock = threading.RLock()
        self._current: _Job | None = None
        self._next_identifier = 1
        self._shutdown = False

    @property
    def job_id(self) -> int | None:
        """Identifier of the current job, or ``None`` when it was consumed."""

        with self._lock:
            return None if self._current is None else self._current.identifier

    @property
    def exception(self) -> BaseException | None:
        """Saved failure for inspection without consuming it."""

        with self._lock:
            return None if self._current is None else self._current.exception

    @property
    def cancel_event(self) -> threading.Event | None:
        """Cancellation event for clients that opt into cooperative stopping.

        The event is exposed rather than injected into ``submit`` arguments so
        the worker has no hidden dependencies.  Callers that need cooperative
        cancellation can pass this event to a subsequent callable explicitly.
        Most generation code can simply rely on stale results being discarded.
        """

        with self._lock:
            return None if self._current is None else self._current.cancel_event

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> int:
        """Replace the current job and schedule ``fn(*args, **kwargs)``.

        Returns a monotonically increasing identifier.  The identifier is only
        informational; all public operations apply to the current job.
        """

        if not callable(fn):
            raise TypeError("fn must be callable")
        with self._lock:
            if self._shutdown:
                raise RuntimeError("generation job manager is shut down")
            self._discard_current_locked()
            job = _Job(identifier=self._next_identifier)
            self._next_identifier += 1
            self._current = job
            job.future = self._executor.submit(self._run, job, fn, args, kwargs)
            return job.identifier

    def _run(
        self,
        job: _Job,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        with self._lock:
            if job.cancel_requested:
                job.state = JobState.CANCELLED
                return
            job.state = JobState.RUNNING

        try:
            value = fn(*args, **kwargs)
        except BaseException as error:
            with self._lock:
                if job.cancel_requested:
                    job.state = JobState.CANCELLED
                    job.exception = None
                else:
                    job.exception = error
                    job.state = JobState.FAILED
            return

        with self._lock:
            if job.cancel_requested:
                job.state = JobState.CANCELLED
                job.result = None
            else:
                job.result = value
                job.state = JobState.SUCCEEDED

    def _cancel_job_locked(self, job: _Job) -> bool:
        if job.state in {
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELLED,
        }:
            return False
        job.cancel_requested = True
        job.cancel_event.set()
        job.state = JobState.CANCELLED
        if job.future is not None:
            job.future.cancel()
        return True

    def _discard_current_locked(self) -> None:
        old = self._current
        if old is None:
            return
        self._cancel_job_locked(old)
        # A replacement makes every previous outcome stale, including an
        # already completed but untaken one.
        old.result = None
        old.exception = None
        self._current = None

    def cancel(self) -> bool:
        """Cancel the current pending/running job and discard its outcome."""

        with self._lock:
            if self._current is None:
                return False
            cancelled = self._cancel_job_locked(self._current)
            if cancelled:
                self._current.result = None
                self._current.exception = None
            return cancelled

    def poll(self) -> JobState | None:
        """Return the current state without waiting, or ``None`` if idle."""

        with self._lock:
            return None if self._current is None else self._current.state

    def take_result(self) -> Any:
        """Consume and return the current terminal result without blocking."""

        with self._lock:
            job = self._current
            if job is None:
                raise JobNotReadyError("there is no current generation job")
            if job.state in {JobState.PENDING, JobState.RUNNING}:
                raise JobNotReadyError("the generation job has not finished")
            self._current = None
            result = job.result
            error = job.exception
            state = job.state
            job.result = None
            job.exception = None

        if state is JobState.CANCELLED:
            raise CancelledError("the generation job was cancelled")
        if state is JobState.FAILED:
            if error is None:  # Defensive: FAILED always stores an exception.
                raise RuntimeError("the generation job failed without an exception")
            raise error
        return result

    def shutdown(self, *, wait: bool = True) -> None:
        """Cancel outstanding work and release the executor (idempotently)."""

        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            if self._current is not None:
                self._cancel_job_locked(self._current)
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def __enter__(self) -> "GenerationJobManager":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.shutdown()


# Short alias for callers which do not need to distinguish generation jobs
# from other application-level work.
JobManager = GenerationJobManager


__all__ = [
    "GenerationJobManager",
    "JobManager",
    "JobNotReadyError",
    "JobState",
]
