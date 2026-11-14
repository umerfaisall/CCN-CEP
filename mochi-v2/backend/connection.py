"""
Manages individual TCP connections from accept() to close:
 - Configures socket options for real-time chat performance
 - Reads the initial HTTP request and routes to either static file server or WebSocket upgrade
 - Performs the WebSocket handshake and enters the main frame-listening loop
 - Cleans up connection state and resources when the socket closes    ]
 
 """
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
    peer = f"{addr[0]}:{addr[1]}"
    
    try:
        # Optimize socket for real-time chat (disable Nagle's algorithm and enable keepalive)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        
        # Apply a timeout to prevent inactive clients from holding server threads indefinitely
        conn.settimeout(config.HTTP_TIMEOUT)
        
        req = read_request(conn)
        if req is None:
            return
            
        method, path, headers = req
        is_websocket_upgrade = (
            method == "GET" and 
            path == "/ws" and 
            headers.get("upgrade", "").lower() == "websocket"
        )
        
        if is_websocket_upgrade:
            serve_ws(conn, peer, headers)
        else:
            serve_static(conn, method, path)
            
    except (OSError, ValueError) as e:
        logger.log(f"{peer} closed ({e})")
    except Exception as e:
        logger.log(f"{peer} ERROR {type(e).__name__}: {e}")
    finally:
        cleanup(conn)

def serve_ws(conn, peer, headers):
    """
    Performs the WebSocket handshake, registers the connection state, 
    and enters the main frame-listening loop for real-time chat.
    """
    key = headers.get("sec-websocket-key")
    if not key:
        return respond(conn, 400, "text/plain", b"Missing WebSocket key")
        
    handshake_response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n"
    )
    conn.sendall(handshake_response.encode())
    conn.settimeout(None)
    
    with state.lock:
        state.out_locks[conn] = threading.Lock() 
        state.peers[conn] = peer
        
    logger.log(f"{peer} websocket open")
    
    # Main listening loop for incoming frames from the client
    while True:
        op, data = read_frame(conn)
        
        if op == CLOSE:
            break
        elif op == PING:
            # Respond to protocol-level WebSocket pings to keep the connection  
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
    """
    Ensures safe resource teardown however a connection ends. 
    """
    with state.lock:
        # Remove output locks and peer references first to block other threads from writing to a dead socket
        state.out_locks.pop(conn, None)
        state.peers.pop(conn, None)
        
        gone_user = state.clients.pop(conn, None)
        if gone_user:
            logger.log("LEFT", gone_user["username"])
            presence("LEFT", gone_user["username"])
            push_users()
            
    conn.close()