# Mochi Chat — Application-Layer Protocol

## Framing
- Transport: one TCP connection per client on port 5000, upgraded from HTTP to WebSocket (RFC 6455). The server implements the handshake and framing itself with Python's `socket` module.
- Messages: UTF-8 JSON objects, exactly one per WebSocket text frame. The message types below are this project's own protocol; WebSocket is only the browser-compatible framing.
- Limits: username 1–20 printable chars, unique case-insensitively among online users; display name ≤ 32; bio ≤ 160; avatar ≤ 200 000 chars; chat text 1–1000 chars.

## Messages
| Type | Dir | Fields | Meaning |
|---|---|---|---|
| LOGIN | C→S | username, display_name? | First message on every connection |
| LOGIN_OK | S→C | username, server_time | Accepted |
| USER_LIST | S→all | users[{username, display_name, bio, avatar}] | Who is online; resent on every join, leave, profile change |
| PRESENCE | S→all | event (`JOINED`/`LEFT`), username, seq, timestamp | Join/leave notification |
| PROFILE_UPDATE | C→S | display_name, bio, avatar | `avatar` is `""` or a `data:image/(png|jpeg|webp|gif);base64,…` URL (client sends a 128×128 JPEG) |
| PROFILE_OK | S→C | profile | Acknowledges the update |
| CHAT | C→S | text, scope (`public`/`private`/`group`), recipient? / group_id? | Send a message |
| CHAT | S→C | username, text, scope, seq, timestamp, recipient? / group_id? | Relay. Private goes to recipient and sender; group goes to online members; public to everyone |
| TYPING | C→S | scope, recipient? / group_id?, is_typing | Typing state, for any scope |
| TYPING | S→C | same + username | Relayed to the other participants only |
| HISTORY | S→C | messages[] (CHAT packets) | Replay of the last ≤200 messages this user may see (lobby, own private chats, own groups). Sent after login, and for one group when the user is added to it. Client dedupes by `seq` |
| GROUP_CREATE | C→S | name (1–40) | Creator becomes first member |
| GROUP_CREATED | S→C | id, name | Reply to creator |
| GROUP_ADD_MEMBER | C→S | group_id, username | Caller must be a member; target must be online |
| GROUP_LIST | S→C | groups[{id, name, members[]}] | Sent per client, **only the groups that client belongs to** |
| PING / PONG | C↔S | id (echoed), server_time | Application-level RTT and keep-alive (client pings every 4 s) |
| ERROR | S→C | code, message | Request rejected |

## Legal order
1. Connect, WebSocket handshake. 2. `LOGIN` (anything else → `LOGIN_REQUIRED`). 3. `LOGIN_OK`, then `PRESENCE`, `USER_LIST`, `GROUP_LIST`. 4. Any other client message in any order until the socket closes. Closing the socket is the logout; the server broadcasts `PRESENCE LEFT` and a fresh `USER_LIST`.

## Error codes
`LOGIN_REQUIRED`, `BAD_JSON`, `INVALID_MESSAGE`, `UNKNOWN_TYPE`, `INVALID_USERNAME`, `USERNAME_TAKEN`, `EMPTY_MESSAGE`, `MESSAGE_TOO_LONG`, `INVALID_SCOPE`, `INVALID_RECIPIENT`, `USER_OFFLINE`, `INVALID_PROFILE`, `PROFILE_TOO_LARGE`, `INVALID_GROUP_NAME`, `NOT_GROUP_MEMBER`. `TYPING` with a bad target is dropped silently (it is high-frequency and advisory).

## Ordering and delivery
`seq` is incremented for every `CHAT` and `PRESENCE` while the server holds one global lock, and the packet is emitted under that same lock. TCP keeps each connection in order, so every client sees events in the server's `seq` order even when several users send at the same instant. The server keeps the last 500 chat packets in RAM for `HISTORY`; delivery to offline users, durable storage, encryption and passwords are out of scope.

## Client behaviour worth stating in the report
- The server only replays a short in-memory window, so the client stores each conversation locally (keyed public / user / group) and counts unread messages per chat. A message for a chat you are not viewing is stored, badged and notified, never dropped.
- Typing: the client sends `is_typing:true` at most every 2.5 s while typing and `false` after 2 s idle or on send; the receiver also expires the indicator after 4 s in case the `false` is lost.
- Failure handling: on socket loss the client retries with back-off (1, 2, 4, 8 s) and re-sends `LOGIN` and its profile. If the old session is still registered, the server answers `USERNAME_TAKEN` and the client simply retries.
- Server sets `TCP_NODELAY` (small frames must not wait for Nagle) and `SO_KEEPALIVE`.

## Limitations
History (500 messages), profiles, groups and membership live in memory and vanish on restart. No TLS, passwords or persistence. A slow receiver can briefly stall the global send lock (ordering is chosen over isolation). Do not expose to the public Internet.
