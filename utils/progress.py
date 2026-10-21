"""Machine-readable progress lines for the desktop app.

Each event is one stdout line "@@{json}". They are printed only when the app
starts the run with JOBSCRAPER_PROGRESS=1, so the command line stays as it was.
"""

import json
import os
import sys
import threading

_lock = threading.Lock()


def enabled():
    return os.environ.get("JOBSCRAPER_PROGRESS") == "1"


def emit(event, **data):
    if not enabled():
        return
    line = "@@" + json.dumps({"event": event, **data}, ensure_ascii=False, default=str)
    with _lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def stage(name, state, **data):
    """state: start | done | error."""
    emit("stage", stage=name, state=state, **data)


def step(name, done, total, text="", **data):
    emit("step", stage=name, done=done, total=total, text=text, **data)
