import json
import logger
import state
from ws_framing import TEXT, frame


def encode(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()

def write(conn, data):
    """Write a framed message to a connection, thread-safe. Returns True if successful, False if the socket is closed."""
    lock = state.out_locks.get(conn)
    if lock is None:
        return False
        
    try:
        with lock:
            conn.sendall(data)  # sendall loops until every byte is successfully passed to TCP
        return True
    except OSError:
        return False

def send(conn, obj):
    """Encodes, frames, and sends a single JSON message to a specific connection."""
    body = encode(obj)
    logger.message("SEND", state.label(conn), body)
    return write(conn, frame(TEXT, body))

def emit(obj, conns):
    body = encode(obj)
    data = frame(TEXT, body)
    
    for recipient_conn in list(conns):
        logger.message("SEND", state.label(recipient_conn), body)
        write(recipient_conn, data)

def error(conn, code, message):
    send(conn, {
        "type": "ERROR", 
        "code": code, 
        "message": message
    })