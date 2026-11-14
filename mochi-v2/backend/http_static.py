"""Minimal hand-written HTTP/1.1: read one request, serve the web UI files. Only GET, only .html/.css/.js."""
import urllib.parse

import config

REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found"}


def read_request(conn):
    """Read the request line and headers. Returns (method, path, headers) or None if the client closed."""
    raw = b""
    while b"\r\n\r\n" not in raw and len(raw) < config.HTTP_HEADER_LIMIT:  # blank line ends the headers
        chunk = conn.recv(4096)
        if not chunk:
            return None
        raw += chunk
    lines = raw.split(b"\r\n\r\n")[0].decode("latin1").split("\r\n")
    method, target = (lines[0].split() + ["", ""])[:2]  # e.g. "GET /ws HTTP/1.1"
    headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}  # names are case-insensitive
    path = urllib.parse.unquote(target.split("?")[0])
    return method, path, headers


def respond(conn, status, ctype, body):
    """Non-persistent response (Connection: close): one file per TCP connection, never cached."""
    if ctype.startswith(("text/", "application/javascript")):
        ctype += "; charset=utf-8"
    head = (f"HTTP/1.1 {status} {REASONS[status]}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\nCache-Control: no-store\r\n\r\n")
    conn.sendall(head.encode() + body)


def serve_static(conn, method, path):
    web = config.WEB_DIR.resolve()
    f = (web / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
    # resolve() + parent check blocks path traversal like /../backend/main.py
    if method != "GET" or f.parent != web or f.suffix not in config.STATIC_TYPES or not f.is_file():
        return respond(conn, 404, "text/plain", b"Not found")
    respond(conn, 200, config.STATIC_TYPES[f.suffix], f.read_bytes())
