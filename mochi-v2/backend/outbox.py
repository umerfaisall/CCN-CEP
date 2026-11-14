"""Sending: Python dict -> compact JSON -> WebSocket text frame -> socket."""
import json

import logger
import state
from ws_framing import TEXT, frame


def encode(obj):
    # no spaces and raw UTF-8 instead of \u escapes: fewer bytes on the wire
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()


def write(conn, data):
    """Send raw bytes. False if the socket is gone; a dead recipient must never crash the sender's thread."""
    lk = state.out_locks.get(conn)
    if lk is None:
        return False
    try:
        with lk:
            conn.sendall(data)  # sendall loops until every byte is handed to TCP
        return True
    except OSError:
        return False


def send(conn, obj):
    body = encode(obj)
    logger.message("SEND", state.label(conn), body)
    return write(conn, frame(TEXT, body))


def emit(obj, conns):
    """Send one message to many clients. Encoded and framed once, not once per recipient."""
    body = encode(obj)
    data = frame(TEXT, body)
    for c in list(conns):  # copy: the dict may change while we loop
        logger.message("SEND", state.label(c), body)
        write(c, data)


def error(conn, code, message):
    send(conn, {"type": "ERROR", "code": code, "message": message})
