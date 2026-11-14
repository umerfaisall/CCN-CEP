"""WebSocket handshake and framing (RFC 6455), written by hand on top of a TCP socket.

TCP delivers a byte stream with no message boundaries. WebSocket puts a small header with the
payload length in front of every message so the receiver knows where each one ends.
"""
import base64
import hashlib
import struct

import config

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # fixed by the RFC, the same for every server
TEXT, CLOSE, PING, PONG = 0x1, 0x8, 0x9, 0xA  # opcodes we use


def accept_key(client_key):
    """Sec-WebSocket-Accept = base64(SHA1(key + GUID)); proves we really speak WebSocket."""
    return base64.b64encode(hashlib.sha1((client_key + GUID).encode()).digest()).decode()


def frame(opcode, payload=b""):
    """Server -> client frame: FIN bit + opcode, then the length in 7 bits, 7+16 bits or 7+64 bits.
    Server frames are never masked."""
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | opcode, n])
    elif n < 65536:
        head = bytes([0x80 | opcode, 126]) + struct.pack("!H", n)  # ! = network byte order
    else:
        head = bytes([0x80 | opcode, 127]) + struct.pack("!Q", n)
    return head + payload


def read_exact(conn, n):
    """recv() may return fewer bytes than asked (TCP can split a message), so loop until we have n."""
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")  # empty read = the other side sent FIN
        buf += chunk
    return buf


def read_frame(conn):
    """Read one frame -> (opcode, payload). Browser frames are always masked, so unmask them."""
    b1, b2 = read_exact(conn, 2)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", read_exact(conn, 2))[0]
    elif n == 127:
        n = struct.unpack("!Q", read_exact(conn, 8))[0]
    if n > config.MAX_FRAME:
        raise ValueError("frame too large")
    mask = read_exact(conn, 4) if b2 & 0x80 else None
    data = read_exact(conn, n) if n else b""
    if mask and n:
        key = (mask * (n // 4 + 1))[:n]  # repeat the 4-byte key to the payload length, XOR in one go
        data = (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")
    return b1 & 0x0F, data
