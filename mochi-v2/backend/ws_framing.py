"""
WebSocket handshake and framing (RFC 6455), written by hand on top of a TCP socket.
"""
import base64
import hashlib
import struct
import config

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # Fixed by the RFC; identical for every server implementation
TEXT, CLOSE, PING, PONG = 0x1, 0x8, 0x9, 0xA  # Standard opcodes used by the chat server


def accept_key(client_key):
    """
    Computes the Sec-WebSocket-Accept header response value:
    base64(SHA1(client_key + GUID)); proves to the client that we support WebSocket.
    """
    combined_key = (client_key + GUID).encode()
    return base64.b64encode(hashlib.sha1(combined_key).digest()).decode()


def frame(opcode, payload=b""):

    payload_length = len(payload)
    if payload_length < 126:
        header = bytes([0x80 | opcode, payload_length])
    elif payload_length < 65536:
        header = bytes([0x80 | opcode, 126]) + struct.pack("!H", payload_length)  # ! = network byte order
    else:
        header = bytes([0x80 | opcode, 127]) + struct.pack("!Q", payload_length)
        
    return header + payload


def read_exact(conn, n):
    """
    Reads exactly n bytes from the socket connection. 
    Loops until all requested bytes arrive to handle TCP stream fragmentation.
    """
    buffer = b""
    while len(buffer) < n:
        chunk = conn.recv(n - len(buffer))
        if not chunk:
            raise ConnectionError("Peer closed connection unexpectedly")  # Empty read = FIN sent
        buffer += chunk
    return buffer


def read_frame(conn):
    """
    Reads and unmasks one WebSocket frame from the client.
    Returns a tuple of (opcode, payload). Browser client frames are always masked.
    """
    b1, b2 = read_exact(conn, 2)
    opcode = b1 & 0x0F
    
    payload_length = b2 & 0x7F
    if payload_length == 126:
        payload_length = struct.unpack("!H", read_exact(conn, 2))[0]
    elif payload_length == 127:
        payload_length = struct.unpack("!Q", read_exact(conn, 8))[0]
        
    if payload_length > config.MAX_FRAME:
        raise ValueError("WebSocket frame exceeds maximum allowed size")
        
    is_masked = bool(b2 & 0x80)
    mask = read_exact(conn, 4) if is_masked else None
    data = read_exact(conn, payload_length) if payload_length else b""
    
    # Unmask the payload data using an XOR operation against the repeating 4-byte masking key
    if mask and payload_length:
        mask_key = (mask * (payload_length // 4 + 1))[:payload_length]
        
        data_int = int.from_bytes(data, "big")
        mask_int = int.from_bytes(mask_key, "big")
        data = (data_int ^ mask_int).to_bytes(payload_length, "big")
        
    return opcode, data