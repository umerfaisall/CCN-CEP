"""Settings shared by every module. main.py overwrites HOST, PORT, LOG_FILE and VERBOSE from the
command line, so the same code runs unchanged on every host (no hardcoded addresses)."""
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent

HOST = "0.0.0.0"  # every interface, so other hosts on the LAN can connect (127.0.0.1 would fail R3)
PORT = 5000
WEB_DIR = BACKEND_DIR.parent / "web"
LOG_FILE = BACKEND_DIR.parent / "logs" / "server.log"  # None = console only
VERBOSE = False  # True = also print every SEND/RECV line on the console

# Protocol limits (protocol_spec.md)
MAX_USERNAME = 20
MAX_DISPLAY_NAME = 32
MAX_BIO = 160
MAX_AVATAR = 200_000
MAX_TEXT = 1000
MAX_GROUP_NAME = 40
HISTORY_KEPT = 500      # chat packets kept in RAM
HISTORY_REPLAYED = 200  # at most this many sent in one HISTORY

# Transport / HTTP safety limits
MAX_FRAME = 1_000_000      # reject bigger WebSocket frames (protects memory)
HTTP_HEADER_LIMIT = 16384  # stop reading a request header after this many bytes
HTTP_TIMEOUT = 30          # seconds a new connection may stay silent before we drop it

# Only these files are ever served, with an explicit type (Windows' registry can map .css/.js wrongly)
STATIC_TYPES = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}
