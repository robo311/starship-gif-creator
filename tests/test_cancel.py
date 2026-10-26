"""Cancelling a job stops its subprocess promptly and records it as cancelled."""

import sys
import threading
import time

import pytest

from server import media
from server.jobs import Cancelled, JobRegistry, current_cancel


def test_a_cancel_kills_the_running_command_quickly():
    flag = threading.Event()
    outcome = {}

    def work():
        current_cancel.set(flag)
        started = time.monotonic()
        try:
            media.run([sys.executable, "-c", "import time; time.sleep(30)"])
        except Cancelled:
            outcome["cancelled_after"] = time.monotonic() - started

    thread = threading.Thread(target=work)
    thread.start()
    time.sleep(0.4)
    flag.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert outcome["cancelled_after"] < 2


def test_without_a_job_commands_run_normally():
    assert media.run([sys.executable, "-c", "print('hi')"]).strip() == "hi"


def test_a_failing_command_still_raises_command_failed():
    with pytest.raises(media.CommandFailed):
        media.run([sys.executable, "-c", "import sys; sys.exit(3)"])


def test_a_cancelled_job_keeps_that_state_even_if_it_then_finishes():
    jobs = JobRegistry()
    job = jobs.create("render")
    jobs.update(job, state="running")
    assert jobs.cancel(job) is True
    jobs.finish(job, {"late": True})
    assert jobs.get(job)["state"] == "cancelled"
    assert jobs.cancel(job) is False
