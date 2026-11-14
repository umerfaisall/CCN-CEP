"""CS-327 CEP: multi-client chat server. Standard library only, raw sockets.

Serves the web UI over HTTP and speaks the custom JSON protocol (see protocol_spec.md)
inside WebSocket text frames, all on one TCP port.
"""
import base64, collections, hashlib, json, mimetypes, re, socket, struct, threading, time, urllib.parse
from pathlib import Path

HOST, PORT = "0.0.0.0", 5000
HERE = Path(__file__).resolve().parent
WEB = HERE / "web" if (HERE / "web" / "index.html").is_file() else HERE  # works with or without a web/ folder
STATIC = {".html", ".css", ".js"}  # only these are ever served, so a flat folder never exposes server.py
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
AVATAR_RE = re.compile(r"data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]*")

lock = threading.RLock()  # guards all state below; seq is assigned and emitted under it
clients = {}              # conn -> {"username", "display_name", "bio", "avatar"}
out_locks = {}            # conn -> write lock, so frames from different threads never interleave
groups = {}               # id -> {"id", "name", "members": set of usernames}
seq = 0
history = collections.deque(maxlen=500)  # recent CHAT packets, replayed on login / group join (RAM only)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------- WebSocket framing (RFC 6455) ----------
def frame(opcode, payload=b""):
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | opcode, n])
    elif n < 65536:
        head = bytes([0x80 | opcode, 126]) + struct.pack("!H", n)
    else:
        head = bytes([0x80 | opcode, 127]) + struct.pack("!Q", n)
    return head + payload


def read_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def ws_read(conn):
    """Read one frame -> (opcode, payload). Browser frames are always masked."""
    b1, b2 = read_exact(conn, 2)
    # 2. Extract the base payload length from the lower 7 bits of the second byte
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", read_exact(conn, 2))[0]
    elif n == 127:
        n = struct.unpack("!Q", read_exact(conn, 8))[0]

    # Security safeguard: Reject any frame over 1 MB to prevent memory overload / DoS attacks
    if n > 1_000_000:
        raise ValueError("frame too large")
    
    mask = read_exact(conn, 4) if b2 & 0x80 else None
    data = read_exact(conn, n) if n else b""
    if mask and n:
        key = (mask * (n // 4 + 1))[:n]
        data = (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")
    return b1 & 0x0F, data


def write(conn, data):
    lk = out_locks.get(conn)
    if lk is None:
        return False
    try:
        with lk:
            conn.sendall(data)
        return True
    except OSError:
        return False


def send(conn, obj):
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    return write(conn, frame(0x1, body))


def err(conn, code, message):
    send(conn, {"type": "ERROR", "code": code, "message": message})


def emit(obj, conns):
    for c in list(conns):
        send(c, obj)


# ---------- state helpers ----------
def find(name):
    """Connection of an online user (case-insensitive), or None."""
    if isinstance(name, str):
        for c, p in clients.items():
            if p["username"].casefold() == name.casefold():
                return c


def members_of(g):
    return [c for c, p in clients.items() if p["username"] in g["members"]]


def push_users():
    users = sorted(clients.values(), key=lambda p: p["username"].casefold())
    emit({"type": "USER_LIST", "users": users}, clients)


def push_groups(conns=None):
    """Each client is told only about the groups it belongs to."""
    for c in list(conns if conns is not None else clients):
        me = clients.get(c)
        if me:
            mine = [{"id": g["id"], "name": g["name"], "members": sorted(g["members"], key=str.casefold)}
                    for g in groups.values() if me["username"] in g["members"]]
            send(c, {"type": "GROUP_LIST", "groups": mine})


def send_history(conn, only_group=None):
    """Replay the recent messages this user is allowed to see: lobby, their private chats, their groups."""
    me = clients[conn]["username"].casefold()

    def visible(m):
        if m["scope"] == "group":
            g = groups.get(m["group_id"])
            return bool(g) and any(u.casefold() == me for u in g["members"]) and only_group in (None, m["group_id"])
        if only_group:
            return False
        return m["scope"] == "public" or me in (m["username"].casefold(), m["recipient"].casefold())
    msgs = [m for m in history if visible(m)][-200:]
    if msgs:
        send(conn, {"type": "HISTORY", "messages": msgs})


def presence(event, name):
    global seq
    seq += 1
    emit({"type": "PRESENCE", "event": event, "username": name, "seq": seq, "timestamp": time.time()}, clients)


# ---------- message handlers ----------
def login(conn, msg):
    name, disp = msg.get("username"), msg.get("display_name")
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > 20 or not name.isprintable():
        return err(conn, "INVALID_USERNAME", "Username must be 1-20 printable characters")
    disp = disp.strip()[:32] if isinstance(disp, str) else ""
    with lock:
        if find(name):
            return err(conn, "USERNAME_TAKEN", "That username is online right now, pick another")
        clients[conn] = {"username": name, "display_name": disp or name, "bio": "", "avatar": ""}
        send(conn, {"type": "LOGIN_OK", "username": name, "server_time": time.time()})
        log("JOINED", name)
        presence("JOINED", name)
        push_users()
        push_groups([conn])
        send_history(conn)


def on_ping(conn, me, msg):
    send(conn, {"type": "PONG", "id": msg.get("id"), "server_time": time.time()})


def on_profile(conn, me, msg):
    d, b, a = msg.get("display_name", ""), msg.get("bio", ""), msg.get("avatar", "")
    if not all(isinstance(x, str) for x in (d, b, a)):
        return err(conn, "INVALID_PROFILE", "Profile fields must be strings")
    if len(d.strip()) > 32 or len(b) > 160 or len(a) > 200_000:
        return err(conn, "PROFILE_TOO_LARGE", "Name max 32, bio max 160, avatar max 200000 chars")
    if a and not AVATAR_RE.fullmatch(a):
        return err(conn, "INVALID_PROFILE", "Avatar must be a base64 png/jpeg/webp/gif data URL")
    with lock:
        me.update(display_name=d.strip() or me["username"], bio=b.strip(), avatar=a)
        send(conn, {"type": "PROFILE_OK", "profile": me})
        push_users()


def on_chat(conn, me, msg):
    global seq
    text, scope = msg.get("text"), msg.get("scope", "public")
    if not isinstance(text, str) or not text.strip():
        return err(conn, "EMPTY_MESSAGE", "Message cannot be empty")
    if len(text) > 1000:
        return err(conn, "MESSAGE_TOO_LONG", "Maximum is 1000 characters")
    with lock:
        extra = {}
        if scope == "public":
            targets = list(clients)
        elif scope == "private":
            r = msg.get("recipient")
            to = find(r)
            if not isinstance(r, str) or to is conn:
                return err(conn, "INVALID_RECIPIENT", "Choose another user")
            if to is None:
                return err(conn, "USER_OFFLINE", "That user is not online")
            extra, targets = {"recipient": clients[to]["username"]}, [conn, to]
        elif scope == "group":
            g = groups.get(str(msg.get("group_id")))
            if not g or me["username"] not in g["members"]:
                return err(conn, "NOT_GROUP_MEMBER", "You are not a member of that group")
            extra, targets = {"group_id": g["id"]}, members_of(g)
        else:
            return err(conn, "INVALID_SCOPE", "Scope must be public, private or group")
        seq += 1
        pkt = {"type": "CHAT", "username": me["username"], "text": text.strip(), "scope": scope,
               "seq": seq, "timestamp": time.time(), **extra}
        history.append(pkt)
        emit(pkt, targets)
        log(f"CHAT seq={seq} {me['username']} -> {scope} ({len(text)} chars)")


def on_typing(conn, me, msg):
    """Relay a typing state to the other participants. Invalid targets are ignored silently."""
    scope, pkt = msg.get("scope"), {"type": "TYPING", "username": me["username"],
                                    "scope": msg.get("scope"), "is_typing": bool(msg.get("is_typing"))}
    with lock:
        if scope == "public":
            targets = list(clients)
        elif scope == "private":
            to = find(msg.get("recipient"))
            targets = [to] if to else []
            pkt["recipient"] = clients[to]["username"] if to else None
        elif scope == "group":
            g = groups.get(str(msg.get("group_id")))
            if not g or me["username"] not in g["members"]:
                return
            pkt["group_id"], targets = g["id"], members_of(g)
        else:
            return
        emit(pkt, [c for c in targets if c is not conn])


def on_group_create(conn, me, msg):
    name = msg.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
        return err(conn, "INVALID_GROUP_NAME", "Group name must be 1-40 characters")
    with lock:
        gid = str(len(groups) + 1)  # groups are never deleted, so ids stay unique
        groups[gid] = {"id": gid, "name": name.strip(), "members": {me["username"]}}
        send(conn, {"type": "GROUP_CREATED", "id": gid, "name": name.strip()})
        push_groups([conn])


def on_group_add(conn, me, msg):
    with lock:
        g = groups.get(str(msg.get("group_id")))
        if not g or me["username"] not in g["members"]:
            return err(conn, "NOT_GROUP_MEMBER", "Only group members can add people")
        new = find(msg.get("username"))
        if new is None:
            return err(conn, "USER_OFFLINE", "That user must be online to be added")
        g["members"].add(clients[new]["username"])
        push_groups(members_of(g))
        send_history(new, g["id"])


HANDLERS = {"PING": on_ping, "PROFILE_UPDATE": on_profile, "CHAT": on_chat, "TYPING": on_typing,
            "GROUP_CREATE": on_group_create, "GROUP_ADD_MEMBER": on_group_add}


def handle(conn, msg):
    if not isinstance(msg, dict):
        return err(conn, "INVALID_MESSAGE", "Message must be a JSON object")
    kind = msg.get("type")
    if conn not in clients:
        return login(conn, msg) if kind == "LOGIN" else err(conn, "LOGIN_REQUIRED", "First message must be LOGIN")
    fn = HANDLERS.get(kind)
    if fn:
        fn(conn, clients[conn], msg)
    else:
        err(conn, "UNKNOWN_TYPE", "Supported: " + ", ".join(HANDLERS))


# ---------- HTTP + connection lifecycle ----------
def http(conn, status, ctype, body):
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}[status]
    if ctype.startswith(("text/", "application/javascript")):
        ctype += "; charset=utf-8"
    head = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\nCache-Control: no-store\r\n\r\n")
    conn.sendall(head.encode() + body)


def serve_static(conn, method, path):
    f = (WEB / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
    if method != "GET" or f.parent != WEB.resolve() or f.suffix not in STATIC or not f.is_file():
        return http(conn, 404, "text/plain", b"Not found")
    http(conn, 200, mimetypes.guess_type(f.name)[0] or "application/octet-stream", f.read_bytes())


def serve_ws(conn, headers):
    key = headers.get("sec-websocket-key")
    if not key:
        return http(conn, 400, "text/plain", b"Missing WebSocket key")
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    conn.settimeout(None)
    out_locks[conn] = threading.Lock()
    while True:
        op, data = ws_read(conn)
        if op == 0x8:
            break
        if op == 0x9:
            write(conn, frame(0xA, data))
        elif op == 0x1:
            try:
                msg = json.loads(data.decode())
            except ValueError:
                err(conn, "BAD_JSON", "Send valid UTF-8 JSON in a text frame")
                continue
            handle(conn, msg)

from connection import handle_connection
def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # UDP connect sends nothing; it just picks the outbound interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main():
    if not (WEB / "index.html").is_file():
        raise SystemExit(f"index.html not found next to server.py or in {HERE / 'web'} - check your folder layout")
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(50)
    log(f"serving {WEB}")
    log(f"open http://localhost:{PORT} here, or http://{lan_ip()}:{PORT} from other hosts")
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle_connection, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        log("stopped")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
