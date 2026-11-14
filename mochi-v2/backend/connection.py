"""One TCP connection, from accept() to close: an HTTP request for a page, or a WebSocket chat session."""
import json
import socket
import threading

import config
import handlers
import logger
import state
from broadcast import presence, push_users
from http_static import read_request, respond, serve_static
from outbox import error, write
from ws_framing import CLOSE, PING, PONG, TEXT, accept_key, frame, read_frame


def handle_connection(conn, addr):
    """Runs in its own thread for every accepted connection."""
    peer = f"{addr[0]}:{addr[1]}"
    try:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # no Nagle delay for small chat frames
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)  # OS probes idle peers (slow backstop)
        conn.settimeout(config.HTTP_TIMEOUT)  # a client that sends nothing can't hold a thread forever
        req = read_request(conn)
        if req is None:
            return
        method, path, headers = req
        if method == "GET" and path == "/ws" and headers.get("upgrade", "").lower() == "websocket":
            serve_ws(conn, peer, headers)
        else:
            serve_static(conn, method, path)
    except (OSError, ValueError) as e:  # expected: closed tab, reset connection, oversized frame
        logger.log(f"{peer} closed ({e})")
    except Exception as e:  # a bug: log it instead of letting the thread die silently
        logger.log(f"{peer} ERROR {type(e).__name__}: {e}")
    finally:
        cleanup(conn)


def serve_ws(conn, peer, headers):
    key = headers.get("sec-websocket-key")
    if not key:
        return respond(conn, 400, "text/plain", b"Missing WebSocket key")
    conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n").encode())
    conn.settimeout(None)  # chat connections stay open for hours; idle is normal
    with state.lock:
        state.out_locks[conn] = threading.Lock()  # from now on write() may send to this socket
        state.peers[conn] = peer
    logger.log(f"{peer} websocket open")
    while True:
        op, data = read_frame(conn)
        if op == CLOSE:
            break
        if op == PING:  # WebSocket-level ping (not our PING message)
            write(conn, frame(PONG, data))
        elif op == TEXT:
            logger.message("RECV", state.label(conn), data)
            try:
                msg = json.loads(data.decode())
            except ValueError:
                error(conn, "BAD_JSON", "Send valid UTF-8 JSON in a text frame")
                continue
            handlers.handle(conn, msg)


def cleanup(conn):
    """Runs however the connection ended. Closing the socket is the logout."""
    with state.lock:
        state.out_locks.pop(conn, None)  # first, so no other thread writes to a closing socket
        state.peers.pop(conn, None)
        gone = state.clients.pop(conn, None)
        if gone:
            logger.log("LEFT", gone["username"])
            presence("LEFT", gone["username"])
            push_users()
    conn.close()
