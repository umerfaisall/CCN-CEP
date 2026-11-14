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


def route(conn, sender, msg):
    """
    Decide WHO receives a CHAT or TYPING message, based on its "scope".
    Returns (recipients, extra_fields), or an error code string.
    """
    scope = msg.get("scope", "public")  # a message without "scope" counts as public
    if scope == "public":
        return list(state.clients), {}
    if scope == "private":
        recipient_name = msg.get("recipient")
        recipient_conn = state.find(recipient_name)  
        if not isinstance(recipient_name, str) or recipient_conn is conn: 
            return "INVALID_RECIPIENT"
        if recipient_conn is None:
            return "USER_OFFLINE"  # no offline delivery in this protocol
        return [conn, recipient_conn], {"recipient": state.clients[recipient_conn]["username"]}
    
    if scope == "group":
        group = state.groups.get(str(msg.get("group_id"))) 
        if not group or sender["username"] not in group["members"]:  # unknown group, or sender not in it
            return "NOT_GROUP_MEMBER"
        return state.members_of(group), {"group_id": group["id"]}  # offline members are skipped
    return "INVALID_SCOPE"


def login(conn, msg):
    """
    Expected message format:
      {"type": "LOGIN", "username": "ali", "display_name": "Ali"}  # display_name optional
    """
    username, display_name = msg.get("username"), msg.get("display_name")
    username = username.strip() if isinstance(username, str) else ""
    if (
        not username
        or len(username) > config.MAX_USERNAME
        or not username.isprintable()  # blocks control chars like \n, \r, \t, null bytes
    ):
        return error(
            conn,
            "INVALID_USERNAME",
            f"Username must be 1-{config.MAX_USERNAME} printable characters",
        )

    # 3. Normalize display_name (optional; falls back to username later)
    display_name = (
        display_name.strip()[:config.MAX_DISPLAY_NAME]
        if isinstance(display_name, str)
        else ""
    )

    # 4. Critical section: check uniqueness and register the session atomically
    with state.lock:
        # Case-insensitive check: "ALI" is rejected if "ali" is already online
        if state.find(username):
            return error(
                conn,
                "USERNAME_TAKEN",
                "That username is online right now, pick another",
            )

        # Transition point: presence of `conn` in `state.clients` means "logged in"
        state.clients[conn] = {
            "username": username,
            "display_name": display_name or username,
            "bio": "",
            "avatar": "",
        }

        # Confirm login to this client
        send(conn, {"type": "LOGIN_OK", "username": username, "server_time": time.time()})

        # Record event and notify everyone else
        logger.log("JOINED", username, "from", state.peers.get(conn, "?"))
        presence("JOINED", username)
        push_users()          # broadcast updated online user list to all clients
        push_groups([conn])   # send existing groups only to this client
        send_history(conn)    # replay recent message history to this client


def on_ping(conn, sender, msg):
    send(conn, {"type": "PONG", "id": msg.get("id"), "server_time": time.time()})


def on_profile(conn, sender, msg):
    display_name = msg.get("display_name", "")
    bio = msg.get("bio", "")
    avatar = msg.get("avatar", "")
    fields = (display_name, bio, avatar)

    if not all(isinstance(field, str) for field in fields):
        return error(conn, "INVALID_PROFILE", "All profile fields must be strings.")
    
    # 3. Enforce maximum length constraints
    exceeds_length_limits = (
        len(display_name.strip()) > config.MAX_DISPLAY_NAME or 
        len(bio) > config.MAX_BIO or 
        len(avatar) > config.MAX_AVATAR
    )
    if exceeds_length_limits:
        return error(
            conn, 
            "PROFILE_TOO_LARGE",
            f"Exceeded limits: Name max {config.MAX_DISPLAY_NAME}, "
            f"Bio max {config.MAX_BIO}, Avatar max {config.MAX_AVATAR} chars."
        )
    
 
    if avatar and not AVATAR_RE.fullmatch(avatar):
        return error(conn, "INVALID_PROFILE", "Avatar must be a base64 png/jpeg/webp/gif data URL.")
    
    # 5. Apply updates thread-safely and broadcast changes
    with state.lock:
        # Fall back to username if display name is left empty after stripping
        updated_name = display_name.strip() or sender["username"]
        
        sender.update(
            display_name=updated_name,
            bio=bio.strip(),
            avatar=avatar
        )
        
        # Acknowledge the user and update everyone's sidebar
        send(conn, {"type": "PROFILE_OK", "profile": sender})
        push_users()


def on_chat(conn, sender, msg):
    """
    Handles incoming chat messages (public, private, or group).
    """
    # 1. Extract and validate message text
    text = msg.get("text")
    is_invalid_text = not isinstance(text, str) or not text.strip()
    
    if is_invalid_text:
        return error(conn, "EMPTY_MESSAGE", "Message cannot be empty.")
        
    if len(text) > config.MAX_TEXT:
        return error(conn, "MESSAGE_TOO_LONG", f"Maximum is {config.MAX_TEXT} characters.")
    
    with state.lock:
        route_result = route(conn, sender, msg)
        if isinstance(route_result, str):
            return error(conn, route_result, ROUTE_ERRORS[route_result])
            
        recipients, extra_fields = route_result
        
        # Construct the official server-certified packet
        packet = {
            "type": "CHAT",
            "username": sender["username"],
            "text": text.strip(),
            "scope": msg.get("scope", "public"),
            "seq": state.next_seq(),
            "timestamp": time.time(),
            **extra_fields  # Dynamically adds "recipient" or "group_id"
        }
        
        # Save to the in-memory history buffer and transmit to all target recipients
        state.history.append(packet)  # Stores the last 500 messages for HISTORY replay
        emit(packet, recipients)
        
        # Log the successful chat delivery
        logger.log(
            f"CHAT seq={packet['seq']} {sender['username']} -> {packet['scope']} "
            f"({len(text)} chars, {len(recipients)} recipients)"
        )


def on_typing(conn, sender, msg):
    """
    Handles typing indicators
    """
    if msg.get("scope") not in ("public", "private", "group"):
        return
        
    with state.lock:
        route_result = route(conn, sender, msg)
        
        if isinstance(route_result, str):
            return
            
        recipients, extra_fields = route_result
        
        packet = {
            "type": "TYPING",
            "username": sender["username"],
            "scope": msg["scope"],
            "is_typing": bool(msg.get("is_typing")),  # Anything truthy becomes True
            **extra_fields
        }
        
        # Exclude the user who is currently typing from receiving their own typing indicator
        everyone_but_typer = [recipient for recipient in recipients if recipient is not conn]
        emit(packet, everyone_but_typer)


def on_group_create(conn, sender, msg):
    """
    Handles creating a new chat group. 
    """
    group_name = msg.get("name")
    
    is_invalid_name = not isinstance(group_name, str) or not 1 <= len(group_name.strip()) <= config.MAX_GROUP_NAME
    if is_invalid_name:
        return error(conn, "INVALID_GROUP_NAME", f"Group name must be 1-{config.MAX_GROUP_NAME} characters.")
        
    clean_group_name = group_name.strip()
    
    with state.lock:
        # Generate a unique sequential group ID safely inside the thread lock
        group_id = str(len(state.groups) + 1)
        
        state.groups[group_id] = {
            "id": group_id, 
            "name": clean_group_name, 
            "members": {sender["username"]}  # Stored as a set
        }
        
        send(conn, {"type": "GROUP_CREATED", "id": group_id, "name": clean_group_name})
        push_groups([conn])
        logger.log(f"GROUP {group_id} '{clean_group_name}' created by {sender['username']}")


def on_group_add(conn, sender, msg):
    """
    Handles adding a new member to an existing group.
    Validates that the requester is a member and the target user is online.
    """
    with state.lock:
        group = state.groups.get(str(msg.get("group_id")))
        
        # Verify group existence and ensure the caller has permission
        if not group or sender["username"] not in group["members"]:
            return error(conn, "NOT_GROUP_MEMBER", "Only group members can add people.")
            
        new_member_conn = state.find(msg.get("username"))
        if new_member_conn is None:
            return error(conn, "USER_OFFLINE", "That user must be online to be added.")
            
        # Add member using their official username registered at login
        new_member_name = state.clients[new_member_conn]["username"]
        group["members"].add(new_member_name)
        
        # Broadcast updated group list to all current group members and sync chat history
        push_groups(state.members_of(group))
        send_history(new_member_conn, group["id"])
        
        logger.log(f"GROUP {group['id']} {sender['username']} added {new_member_name}")

HANDLERS = {
    "PING": on_ping, 
    "PROFILE_UPDATE": on_profile, 
    "CHAT": on_chat, 
    "TYPING": on_typing,
    "GROUP_CREATE": on_group_create, 
    "GROUP_ADD_MEMBER": on_group_add
}

def handle(conn, msg):

    if not isinstance(msg, dict):
        return error(conn, "INVALID_MESSAGE", "Message must be a JSON object.")
        
    msg_type = msg.get("type")

    if conn not in state.clients:
        if msg_type == "LOGIN":
            return login(conn, msg)
        return error(conn, "LOGIN_REQUIRED", "First message must be LOGIN.")
    
    handler = HANDLERS.get(msg_type)
    if handler:
        sender = state.clients[conn]
        return handler(conn, sender, msg)
        
    supported_types = ", ".join(HANDLERS.keys())

    return error(conn, "UNKNOWN_TYPE", f"Supported message types: {supported_types}")