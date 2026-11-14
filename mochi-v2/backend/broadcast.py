"""Server-initiated messages: who is online, which groups you are in, presence events, history replay.
Callers hold state.lock."""
import time

import config
import state
from outbox import emit, send


def push_users():
    """USER_LIST to everyone. Resent on every join, leave and profile change (includes avatars)."""
    users = sorted(state.clients.values(), key=lambda p: p["username"].casefold())
    emit({"type": "USER_LIST", "users": users}, state.clients)


def push_groups(conns=None):
    """GROUP_LIST per client, containing only the groups that client belongs to."""
    for c in list(conns if conns is not None else state.clients):
        me = state.clients.get(c)
        if me:
            mine = [{"id": g["id"], "name": g["name"], "members": sorted(g["members"], key=str.casefold)}
                    for g in state.groups.values() if me["username"] in g["members"]]
            send(c, {"type": "GROUP_LIST", "groups": mine})


def presence(event, name):
    """PRESENCE JOINED/LEFT. Shares the seq counter with CHAT, so joins, leaves and chats form one order."""
    emit({"type": "PRESENCE", "event": event, "username": name, "seq": state.next_seq(),
          "timestamp": time.time()}, state.clients)


def send_history(conn, only_group=None):
    """Replay recent messages this user may see: lobby, their private chats, their groups.
    With only_group, just that group's messages (used when someone is added to it)."""
    me = state.clients[conn]["username"].casefold()

    def visible(m):
        if m["scope"] == "group":
            g = state.groups.get(m["group_id"])
            return bool(g) and any(u.casefold() == me for u in g["members"]) and only_group in (None, m["group_id"])
        if only_group:
            return False
        return m["scope"] == "public" or me in (m["username"].casefold(), m["recipient"].casefold())

    msgs = [m for m in state.history if visible(m)][-config.HISTORY_REPLAYED:]
    if msgs:
        send(conn, {"type": "HISTORY", "messages": msgs})
