import collections
import threading

import config

lock = threading.RLock()  # reentrant: a function holding it may call another that takes it
clients = {}    # conn -> {"username", "display_name", "bio", "avatar"}; being a key = logged in
peers = {}      # conn -> "ip:port", so log lines can name a connection before it logs in
out_locks = {}  # conn -> write lock, so frames from different threads never interleave on one socket
groups = {}     # id -> {"id", "name", "members": set of usernames}
history = collections.deque(maxlen=config.HISTORY_KEPT)  # recent CHAT packets, RAM only
_seq = 0


def next_seq():
    """Next global sequence number for CHAT/PRESENCE. The caller must hold `lock` and emit the
    packet before releasing it, otherwise two senders could deliver out of seq order."""
    global _seq
    _seq += 1
    return _seq


def find(name):
    """Connection of an online user (case-insensitive), or None."""
    if isinstance(name, str):
        for c, p in clients.items():
            if p["username"].casefold() == name.casefold():
                return c


def members_of(g):
    """Connections of the group's members who are online right now."""
    return [c for c, p in clients.items() if p["username"] in g["members"]]


def label(conn):
    """Username if logged in, else ip:port; used in log lines."""
    p = clients.get(conn)
    return p["username"] if p else peers.get(conn, "?")
