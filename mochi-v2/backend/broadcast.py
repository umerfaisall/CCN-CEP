"""Messages the SERVER sends on its own (no client asked for them directly).

There are four:
    USER_LIST   who is online right now                 -> to everyone
    GROUP_LIST  which groups you are a member of         -> to each client separately
    PRESENCE    "ali JOINED" / "ali LEFT"                -> to everyone
    HISTORY     old messages you missed                  -> to one client

Every function here reads the shared state (state.clients, state.groups, state.history), so the
CALLER must already hold state.lock. That way the state can't change halfway through building a message.
"""
import time
import config
import state
from outbox import emit, send

# USER_LIST
def push_users():
    """
    Send the list of online users to every client.
    """
    online_profiles = list(state.clients.values())
    online_profiles.sort(key=lambda profile: profile["username"].casefold())
    message = {"type": "USER_LIST", "users": online_profiles}
    everyone = state.clients 
    emit(message, everyone)  #emit sends the same message to many clients at once.

# GROUP_LIST
def push_groups(conns=None):
    """
    Send each client the list of groups THEY belong to (never other people's groups).
    Unlike USER_LIST, the content differs per client, so we build one message per client.
    """
    if conns is None:
        conns = state.clients
    targets = list(conns)  # copy, in case the dict changes while we loop

    for conn in targets:
        profile = state.clients.get(conn)
        if profile is None:
            continue  # this socket has not logged in (or already left): nothing to send

        my_groups = _groups_of(profile["username"])
        send(conn, {"type": "GROUP_LIST", "groups": my_groups})


def _groups_of(username):
    """All groups this username is a member of, in the shape the client expects."""
    result = []
    for group in state.groups.values():
        if username in group["members"]:
            result.append({
                "id": group["id"],
                "name": group["name"],
                # members is a Python set (no order, not JSON-able), so turn it into a sorted list
                "members": sorted(group["members"], key=str.casefold),
            })
    return result


# PRESENCE
def presence(event, name):
    """Announce that a user joined or left, to everyone."""
    message = {
        "type": "PRESENCE",
        "event": event,
        "username": name,
        "seq": state.next_seq(),
        "timestamp": time.time(),
    }
    emit(message, state.clients)

# HISTORY
def send_history(conn, only_group=None):
    
    """Replay recent chat messages to ONE client.

    Used in two situations:
      1. Right after login (only_group=None):
         lobby messages + their private chats + chats of groups they are in.

      2. When the user is added to a group (only_group="<group id>"):
         only that group's old messages, so they can read what was said before they joined.

    At most config.HISTORY_REPLAYED (200) messages are sent, the newest ones.
    Example:
        {"type":"HISTORY","messages":[ {CHAT packet}, {CHAT packet}, ... ]}
    """

    my_name = state.clients[conn]["username"]
    allowed = []
    for message in state.history:  # oldest first
        if _can_see(message, my_name, only_group):
            allowed.append(message)

    newest = allowed[-config.HISTORY_REPLAYED:]  # keep only the last 200
    if newest:  # don't send an empty HISTORY
        send(conn, {"type": "HISTORY", "messages": newest})


def _can_see(message, my_name, only_group):
    """Privacy rule: may the user `my_name` see this old CHAT message?"""
    me = my_name.casefold()  # compare names ignoring case: "Ali" == "ali"
    scope = message["scope"]

    if scope == "group":
        group = state.groups.get(message["group_id"])
        if group is None:
            return False
        is_member = any(member.casefold() == me for member in group["members"])
        right_group = only_group is None or only_group == message["group_id"]
        return is_member and right_group

    # From here on the message is public or private.
    if only_group is not None:
        return False  # we were asked for one group only

    if scope == "public":
        return True  # lobby: everyone may see it

    # private: only the two people in the conversation
    sender = message["username"].casefold()
    recipient = message["recipient"].casefold()
    return me == sender or me == recipient
