"""In-memory progress registry so the UI can poll long-running work."""

from __future__ import annotations

import threading
import uuid
from typing import Any


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
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
        return job_id

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
        self.update(job_id, state="done", percent=100.0, result=result)

    def fail(self, job_id: str, error: str) -> None:
        self.update(job_id, state="error", error=error)


registry = JobRegistry()
