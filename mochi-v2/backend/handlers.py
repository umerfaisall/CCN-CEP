"""What the server does on receipt of each client message (the protocol logic in protocol_spec.md).

Every handler after login is called as fn(conn, me, msg): the socket, the sender's profile dict and
the parsed JSON object.
"""
import re
import time

import config
import logger
import state
from broadcast import presence, push_groups, push_users, send_history
from outbox import emit, error, send

AVATAR_RE = re.compile(r"data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]*")

ROUTE_ERRORS = {
    "INVALID_RECIPIENT": "Choose another user",
    "USER_OFFLINE": "That user is not online",
    "NOT_GROUP_MEMBER": "You are not a member of that group",
    "INVALID_SCOPE": "Scope must be public, private or group",
}


def route(conn, me, msg):
    """Who receives a CHAT/TYPING with this scope. Returns (targets, extra_fields) or an error code.
    Caller holds state.lock."""
    scope = msg.get("scope", "public")
    if scope == "public":
        return list(state.clients), {}
    if scope == "private":
        r = msg.get("recipient")
        to = state.find(r)
        if not isinstance(r, str) or to is conn:
            return "INVALID_RECIPIENT"
        if to is None:
            return "USER_OFFLINE"
        return [conn, to], {"recipient": state.clients[to]["username"]}  # sender gets the official copy too
    if scope == "group":
        g = state.groups.get(str(msg.get("group_id")))
        if not g or me["username"] not in g["members"]:
            return "NOT_GROUP_MEMBER"
        return state.members_of(g), {"group_id": g["id"]}
    return "INVALID_SCOPE"


def login(conn, msg):
    name, disp = msg.get("username"), msg.get("display_name")
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > config.MAX_USERNAME or not name.isprintable():
        return error(conn, "INVALID_USERNAME", f"Username must be 1-{config.MAX_USERNAME} printable characters")
    disp = disp.strip()[:config.MAX_DISPLAY_NAME] if isinstance(disp, str) else ""
    with state.lock:  # check and insert in one step, so two clients can't both claim a name
        if state.find(name):
            return error(conn, "USERNAME_TAKEN", "That username is online right now, pick another")
        state.clients[conn] = {"username": name, "display_name": disp or name, "bio": "", "avatar": ""}
        send(conn, {"type": "LOGIN_OK", "username": name, "server_time": time.time()})
        logger.log("JOINED", name, "from", state.peers.get(conn, "?"))
        presence("JOINED", name)
        push_users()
        push_groups([conn])
        send_history(conn)


def on_ping(conn, me, msg):
    send(conn, {"type": "PONG", "id": msg.get("id"), "server_time": time.time()})


def on_profile(conn, me, msg):
    d, b, a = msg.get("display_name", ""), msg.get("bio", ""), msg.get("avatar", "")
    if not all(isinstance(x, str) for x in (d, b, a)):
        return error(conn, "INVALID_PROFILE", "Profile fields must be strings")
    if len(d.strip()) > config.MAX_DISPLAY_NAME or len(b) > config.MAX_BIO or len(a) > config.MAX_AVATAR:
        return error(conn, "PROFILE_TOO_LARGE",
                     f"Name max {config.MAX_DISPLAY_NAME}, bio max {config.MAX_BIO}, avatar max {config.MAX_AVATAR} chars")
    if a and not AVATAR_RE.fullmatch(a):
        return error(conn, "INVALID_PROFILE", "Avatar must be a base64 png/jpeg/webp/gif data URL")
    with state.lock:
        me.update(display_name=d.strip() or me["username"], bio=b.strip(), avatar=a)
        send(conn, {"type": "PROFILE_OK", "profile": me})
        push_users()


def on_chat(conn, me, msg):
    text = msg.get("text")
    if not isinstance(text, str) or not text.strip():
        return error(conn, "EMPTY_MESSAGE", "Message cannot be empty")
    if len(text) > config.MAX_TEXT:
        return error(conn, "MESSAGE_TOO_LONG", f"Maximum is {config.MAX_TEXT} characters")
    with state.lock:  # seq is assigned AND emitted under the lock: every client sees the same order
        r = route(conn, me, msg)
        if isinstance(r, str):
            return error(conn, r, ROUTE_ERRORS[r])
        targets, extra = r
        pkt = {"type": "CHAT", "username": me["username"], "text": text.strip(), "scope": msg.get("scope", "public"),
               "seq": state.next_seq(), "timestamp": time.time(), **extra}
        state.history.append(pkt)
        emit(pkt, targets)
        logger.log(f"CHAT seq={pkt['seq']} {me['username']} -> {pkt['scope']} "
                   f"({len(text)} chars, {len(targets)} recipients)")


def on_typing(conn, me, msg):
    """Relay typing state to the other participants. No seq, no history, and bad targets are dropped
    silently: typing is frequent and only advisory."""
    if msg.get("scope") not in ("public", "private", "group"):
        return
    with state.lock:
        r = route(conn, me, msg)
        if isinstance(r, str):
            return
        targets, extra = r
        pkt = {"type": "TYPING", "username": me["username"], "scope": msg["scope"],
               "is_typing": bool(msg.get("is_typing")), **extra}
        emit(pkt, [c for c in targets if c is not conn])


def on_group_create(conn, me, msg):
    name = msg.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= config.MAX_GROUP_NAME:
        return error(conn, "INVALID_GROUP_NAME", f"Group name must be 1-{config.MAX_GROUP_NAME} characters")
    with state.lock:
        gid = str(len(state.groups) + 1)  # groups are never deleted, so ids stay unique
        state.groups[gid] = {"id": gid, "name": name.strip(), "members": {me["username"]}}
        send(conn, {"type": "GROUP_CREATED", "id": gid, "name": name.strip()})
        push_groups([conn])
        logger.log(f"GROUP {gid} '{name.strip()}' created by {me['username']}")


def on_group_add(conn, me, msg):
    with state.lock:
        g = state.groups.get(str(msg.get("group_id")))
        if not g or me["username"] not in g["members"]:
            return error(conn, "NOT_GROUP_MEMBER", "Only group members can add people")
        new = state.find(msg.get("username"))
        if new is None:
            return error(conn, "USER_OFFLINE", "That user must be online to be added")
        g["members"].add(state.clients[new]["username"])
        push_groups(state.members_of(g))
        send_history(new, g["id"])
        logger.log(f"GROUP {g['id']} {me['username']} added {state.clients[new]['username']}")


# Dispatch table: message type -> handler. Also the whitelist of accepted types.
HANDLERS = {"PING": on_ping, "PROFILE_UPDATE": on_profile, "CHAT": on_chat, "TYPING": on_typing,
            "GROUP_CREATE": on_group_create, "GROUP_ADD_MEMBER": on_group_add}


def handle(conn, msg):
    """Protocol state machine: before LOGIN only LOGIN is legal; after it, any type in HANDLERS."""
    if not isinstance(msg, dict):
        return error(conn, "INVALID_MESSAGE", "Message must be a JSON object")
    kind = msg.get("type")
    if conn not in state.clients:
        return login(conn, msg) if kind == "LOGIN" else error(conn, "LOGIN_REQUIRED", "First message must be LOGIN")
    fn = HANDLERS.get(kind)
    if fn:
        fn(conn, state.clients[conn], msg)
    else:
        error(conn, "UNKNOWN_TYPE", "Supported: " + ", ".join(HANDLERS))
