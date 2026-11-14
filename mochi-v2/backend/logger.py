"""Timestamped logging (millisecond precision) to the console and a log file: evidence for R6/R7.

- log():     lifecycle events (start, JOINED, LEFT, CHAT, errors). Console + file.
- message(): every protocol message sent or received. File always, console only with --verbose.
"""
import threading
import time

import config

_file = None
_lock = threading.Lock()  # one line at a time, even when many client threads log at once


def setup():
    global _file
    if config.LOG_FILE:
        config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _file = open(config.LOG_FILE, "a", encoding="utf-8")


def _stamp():
    t = time.time()
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"


def _write(line, console):
    with _lock:
        if console:
            print(line, flush=True)
        if _file:
            _file.write(line + "\n")
            _file.flush()


def log(*parts):
    _write(" ".join([_stamp(), *map(str, parts)]), console=True)


def message(direction, who, payload):
    """direction is 'RECV' or 'SEND'; payload is the JSON bytes. Long payloads (avatars) are cut short."""
    if not (_file or config.VERBOSE):
        return
    preview = payload[:160].decode("utf-8", "replace") + ("..." if len(payload) > 160 else "")
    _write(f"{_stamp()} {direction} {who} {len(payload)}B {preview}", console=config.VERBOSE)
