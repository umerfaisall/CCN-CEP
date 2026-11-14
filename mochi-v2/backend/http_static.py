"""
Minimal hand-written HTTP/1.1 implementation:
reads one request and serves the web UI files. Supports only GET for .html/.css/.js files.
"""
import urllib.parse
import config

REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found"}


def read_request(conn):
    raw = b""
    # Read until the double CRLF (blank line marking end of headers) or header size limit is reached
    while b"\r\n\r\n" not in raw and len(raw) < config.HTTP_HEADER_LIMIT:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        raw += chunk
        
    # Split headers from body and decode using latin1 to safely handle raw bytes
    header_section = raw.split(b"\r\n\r\n")[0].decode("latin1")
    lines = header_section.split("\r\n")
    
    # Parse the request line (e.g., "GET /ws HTTP/1.1")
    request_parts = lines[0].split() + ["", ""]
    method, target = request_parts[0], request_parts[1]
    
    headers = {
        key.strip().lower(): val.strip() 
        for line in lines[1:] 
        for key, _, val in [line.partition(":")] 
        if key
    }
    
    clean_path = target.split("?")[0]
    path = urllib.parse.unquote(clean_path)
    
    return method, path, headers


def respond(conn, status, ctype, body):
    """
    Sends a non-persistent HTTP response (Connection: close) with cache-control disabled.
    Automatically appends UTF-8 character encoding to text and javascript types.
    """
    if ctype.startswith(("text/", "application/javascript")):
        ctype += "; charset=utf-8"
        
    response_head = (
        f"HTTP/1.1 {status} {REASONS[status]}\r\n"
        f"Content-Type: {ctype}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "Cache-Control: no-store\r\n\r\n"
    )
    
    conn.sendall(response_head.encode() + body)


def serve_static(conn, method, path):
    """
    Serves static files (HTML, CSS, JS) from the configured web directory.
    Includes security checks to block unsupported methods and path traversal attacks.
    """
    web_dir = config.WEB_DIR.resolve()
    target_filename = "index.html" if path == "/" else path.lstrip("/")
    file_path = (web_dir / target_filename).resolve()
    
    is_invalid_request = (
        method != "GET" or 
        file_path.parent != web_dir or 
        file_path.suffix not in config.STATIC_TYPES or 
        not file_path.is_file()
    )
    
    if is_invalid_request:
        return respond(conn, 404, "text/plain", b"Not found")
        
    content_type = config.STATIC_TYPES[file_path.suffix]
    file_contents = file_path.read_bytes()
    
    respond(conn, 200, content_type, file_contents)