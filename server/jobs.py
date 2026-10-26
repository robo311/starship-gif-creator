"""In-memory progress registry so the UI can poll long-running work."""

from __future__ import annotations

import threading
import uuid
from contextvars import ContextVar
from typing import Any


class Cancelled(RuntimeError):
    """The user stopped the job; not an error worth reporting."""


# The cancel flag of the job running on this thread, so code deep inside a job
# (a subprocess wait, a tracking loop) can notice a cancel without every
# function in between having to pass the flag along.
current_cancel: ContextVar[threading.Event | None] = ContextVar("current_cancel", default=None)


def check_cancelled() -> None:
    flag = current_cancel.get()
    if flag is not None and flag.is_set():
        raise Cancelled()


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._flags: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def create(self, kind: str) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "kind": kind,
                "state": "pending",
                "percent": 0.0,
                "message": "",
                "result": None,
                "error": None,
            }
            self._flags[job_id] = threading.Event()
        return job_id

    def cancel_flag(self, job_id: str) -> threading.Event | None:
        with self._lock:
            return self._flags.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Ask a job to stop. False if it is unknown or already over."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["state"] in ("done", "error", "cancelled"):
                return False
            self._flags[job_id].set()
            job.update(state="cancelled", message="Cancelled")
            return True

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update(fields)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def finish(self, job_id: str, result: Any) -> None:
        self._settle(job_id, state="done", percent=100.0, result=result)

    def fail(self, job_id: str, error: str) -> None:
        self._settle(job_id, state="error", error=error)

    def _settle(self, job_id: str, **fields: Any) -> None:
        """Record the outcome, unless the job was cancelled first: that stands."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job["state"] != "cancelled":
                job.update(fields)


registry = JobRegistry()
