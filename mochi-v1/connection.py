import socket
import urllib.parse
from server import serve_ws, serve_static
def handle_connection(conn, addr):
    """
    Main entry point for every incoming client connection.
    It manages the entire lifecycle of the connection: setup, reading request,
    routing (HTTP vs WebSocket), error handling, and safe cleanup.
    """
    try:
        # Step 1: Configure socket performance and timeout settings
        configure_socket(conn)
        
        # Step 2: Read the raw incoming HTTP request headers safely
        raw_request = read_http_headers(conn)
        if not raw_request:
            return  # Client disconnected before sending data
            
        # Step 3: Parse raw bytes into useful HTTP components (method, path, headers)
        method, path, headers = parse_http_request(raw_request)
        
        # Step 4: Route the request based on whether it's a WebSocket upgrade or a normal static file request
        if is_websocket_upgrade(method, path, headers):
            serve_ws(conn, headers)  # Handle real-time WebSocket chat connection
        else:
            serve_static(conn, method, path)  # Serve normal HTML/CSS/JS files
            
    except (OSError, ValueError) as e:
        # Handle normal, expected connection drops or invalid parsing cleanly
        log(f"{addr[0]}:{addr[1]} closed ({e})")
    except Exception as e:  
        # Catch unexpected errors so the server doesn't crash silently (leaving browser hanging)
        log(f"{addr[0]}:{addr[1]} ERROR {type(e).__name__}: {e}")
    finally:
        # Step 5: Guaranteed cleanup when the connection ends for any reason
        cleanup_client(conn)


# HELPER FUNCTIONS 
def configure_socket(conn):
    """Applies low-level TCP optimizations and safety timeouts to the socket."""
    # Disable Nagle's algorithm so small chat packets send instantly without delay
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    
    # Enable TCP keepalive probes to detect dead or disconnected clients automatically
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    
    # Set a 30-second timeout so the server doesn't hang indefinitely on inactive connections
    conn.settimeout(30)


def read_http_headers(conn):
    """Safely reads data chunks from the socket until HTTP headers end or size limit is hit."""
    raw = b""
    # HTTP headers always end with a double carriage return/newline (\r\n\r\n)
    # We cap it at 16KB (16384 bytes) to protect against memory exhaustion or Slowloris attacks
    while b"\r\n\r\n" not in raw and len(raw) < 16384:
        chunk = conn.recv(4096)  # Read in 4KB chunks
        if not chunk:
            return None         # Connection closed prematurely
        raw += chunk
    return raw


def parse_http_request(raw):
    """Decodes raw socket bytes and extracts the HTTP method, URL path, and headers."""
    # Split raw bytes at the header boundary, decode to text using latin1 (safe 1-to-1 byte mapping), and split lines
    lines = raw.split(b"\r\n\r\n")[0].decode("latin1").split("\r\n")
    
    # Extract HTTP method (e.g., GET) and target URL from the first line
    print(lines)
    method, target = (lines[0].split() + ["", ""])[:2]
    
    # Convert all header lines into a clean, lowercase key-value dictionary
    headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
    
    # Clean and decode the URL path, removing any query parameters (e.g., ?foo=bar)
    path = urllib.parse.unquote(target.split("?")[0])
    
    return method, path, headers


def is_websocket_upgrade(method, path, headers):
    """Checks if the client is requesting to switch from HTTP to a real-time WebSocket connection."""
    return (
        method == "GET" 
        and path == "/ws" 
        and headers.get("upgrade", "").lower() == "websocket"
    )


def cleanup_client(conn):
    """Safely removes the client from active server tracking dictionaries and closes the socket."""
    with lock:  # Thread-safe lock to prevent race conditions when modifying shared state
        out_locks.pop(conn, None)  # Remove socket output lock
        gone = clients.pop(conn, None)  # Remove client from active users list
        
        # If the user was logged in, notify everyone that they left
        if gone:
            log("LEFT", gone["username"])
            presence("LEFT", gone["username"])
            push_users()  # Update user list for remaining clients
            
    conn.close()  # Finally, close the physical network socket