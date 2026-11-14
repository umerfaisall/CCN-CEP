# How mochi works (file by file)

## Big picture
```
 browser (web/)  <--- one TCP connection, WebSocket framing, JSON text frames --->  server.py
 index.html + style.css + app.js (same port serves the page)
```
The browser downloads the page from `server.py` over plain HTTP, then opens `/ws`. After the HTTP "Upgrade" handshake the same TCP connection carries our own protocol: one JSON object per WebSocket frame (`{"type":"CHAT", ...}`). `protocol_spec.md` is that protocol written down (CEP requirement R1).

## server.py (stdlib only, raw sockets, ~300 lines)
- **WebSocket framing** (`frame`, `ws_read`, `read_exact`): builds/reads RFC 6455 frames by hand. Browser frames are masked, so `ws_read` XORs the payload with the 4-byte mask.
- **Threads**: `main()` accepts TCP connections; each gets a thread running `handle_connection`.
- **HTTP**: `handle_connection` reads the request line + headers. `GET /ws` with `Upgrade: websocket` → `serve_ws` (computes `Sec-WebSocket-Accept` = base64(SHA1(key + GUID)), sends `101`, then loops reading frames). Anything else → `serve_static`, which only serves .html/.css/.js from `web/` (or the same folder).
- **State** (all guarded by one `RLock`): `clients` (socket → profile), `groups`, `history` (last 500 chat packets), `seq` counter.
- **Handlers**: `login`, `on_chat`, `on_typing`, `on_profile`, `on_group_create`, `on_group_add`, `on_ping`, looked up in the `HANDLERS` dict by message `type`. `handle()` enforces "first message must be LOGIN".
- **Ordering**: `on_chat` increments `seq` and emits the packet while holding the lock, so every client sees the same order even if people send at the same instant.
- **Routing**: public → everyone; private → recipient + sender; group → online members. `push_users` / `push_groups` / `presence` broadcast who is online and who joined/left.
- **History**: every chat packet is appended to `history`; `send_history` replays the ones a user may see (lobby, their private chats, their groups) after login and when they are added to a group.
- **Cleanup** (`finally`): removes the client, broadcasts `PRESENCE LEFT` + new `USER_LIST`. `TCP_NODELAY` stops small frames waiting for Nagle; `SO_KEEPALIVE` detects dead peers.

## web/index.html
Static skeleton: sidebar (chat list, "+ group", your profile card), main area (empty state, header, message list, typing line, composer) and four `<dialog>`s (login, profile, new group, add member).

## web/style.css
Y2K / MSN-Messenger look: colour tokens in `:root`, each panel is a retro "window" (gradient title bar with _ □ × buttons), glossy pill buttons, inset text fields, speech-bubble messages (blue = them, pink = you), pixel font for titles. A media query turns it into a single-column phone layout.

## web/app.js
- **State `S`**: `people` (everyone ever seen), `online` (set), `groups`, `chats` (messages per conversation), `typing`, `active`.
- **Chat keys**: `public`, `u:<username>`, `g:<id>`. Every incoming message is stored under its key *whether or not that chat is open* (this fixed the lost-first-message bug), then unread badge / toast / sound / desktop notification fire if you are not looking at it.
- **Sidebar** (`entries`, `render`): one list sorted by latest message; online people with no chat yet come after.
- **Typing**: `oninput` sends `TYPING true` at most every 2.5 s and `false` after 2 s idle; receivers also expire it after 4 s.
- **Connection** (`connect`): login, ping every 4 s (shows RTT), auto-reconnect with back-off and re-login.
- **History** (`addHistory`): merges the replay, skipping messages already present (by `seq`) and sorting by `seq`.
- **Profile photo** (`square`): crops to a 128×128 JPEG in a canvas, sends it as a base64 data URL.

## bench.py
Raw-socket test client for requirement R6: measures RTT, new-connection cost, chat relay time and throughput (50 B vs 1000 B), repeated N times with mean/stdev, saved to `bench_results.csv`.

## Why history was missing, and what changed
The first version deliberately kept no history (the spec said so): the server forgot each message after forwarding it and a browser only knew messages received while it was connected. So a refresh or late join showed an empty lobby/group. Now the server keeps the last 500 messages in RAM and replays the relevant ones. It is still lost when the server restarts (no database, by design).
