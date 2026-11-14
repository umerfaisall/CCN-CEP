import threading
import time
import config

_file = None
_lock = threading.Lock()

def setup():
    """Initializes the log file directory and opens the file in append mode if configured."""
    global _file
    if config.LOG_FILE:
        config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _file = open(config.LOG_FILE, "a", encoding="utf-8")


def _stamp():
    """Generates a high-precision timestamp string down to the millisecond."""
    current_time = time.time()
    local_time = time.localtime(current_time)
    milliseconds = int(current_time * 1000) % 1000
    
    formatted_time = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    return f"{formatted_time}.{milliseconds:03d}"


def _write(line, console):
    """Thread-safely writes a log line to the console and/or persistent log file."""
    with _lock:
        if console:
            print(line, flush=True)
        if _file:
            _file.write(line + "\n")
            _file.flush()


def log(*parts):
    """
    Logs general lifecycle events (startup, joins, leaves, chat events, errors)
    to both the console and the log file.
    """
    timestamped_message = " ".join([_stamp(), *map(str, parts)])
    _write(timestamped_message, console=True)


def message(direction, who, payload):
    """
    Logs raw protocol messages (SEND or RECV). 
    Always recorded to the file; printed to the console only if VERBOSE mode is active.
    Truncates large payloads (like image avatars) to keep logs readable.
    """
    if not (_file or config.VERBOSE):
        return
        
    # Safely decode and truncate long payloads (e.g., base64 data URLs)
    max_preview_length = 160
    payload_preview = payload[:max_preview_length].decode("utf-8", "replace")
    if len(payload) > max_preview_length:
        payload_preview += "..."
        
    log_line = f"{_stamp()} {direction} {who} {len(payload)}B {payload_preview}"
    _write(log_line, console=config.VERBOSE)