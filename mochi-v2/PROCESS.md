# Mochi v2: modular backend + redesigned UI

**Date:** 2026-10-31 · **Scope:** a new copy of the chat system in `mochi-v2/`. The original files in the project root (`server.py`, `web/`, `bench.py`, the `.md` files) were not modified.

## 1. What was asked and what was done

| Request | What was done |
|---|---|
| Leave the current code as it is | Nothing outside `mochi-v2/` was edited. |
| New folder with `web/` and `backend/` | `mochi-v2/web/` (UI) and `mochi-v2/backend/` (server) |
| The current UI is too "girly"; make a normal, attractive one | New neutral design: white/grey panels, one blue accent, automatic dark mode, initials avatars, icons instead of emoji |
| Modularise the backend | The single 380-line `server.py` is split into 10 modules, each with one job |
| Document the whole process | This file |

**The protocol is unchanged.** Every message type, field, error code and the legal order are exactly as in `../protocol_spec.md`. Two things prove it: the new UI sends the same JSON as the old one, and the original `bench.py` runs unchanged against the new server (§7).

## 2. Folder layout

```
mochi-v2/
├── PROCESS.md          this file
├── backend/
│   ├── main.py         entry point: command-line options, listening socket, accept loop
│   ├── config.py       every setting and limit in one place
│   ├── logger.py       millisecond-timestamped log: console + logs/server.log
│   ├── ws_framing.py   WebSocket handshake key + frame encode/decode (RFC 6455)
│   ├── http_static.py  read an HTTP request, serve index.html / style.css / app.js
│   ├── connection.py   one connection's life: HTTP or WebSocket, then cleanup
│   ├── state.py        shared state (clients, groups, history, seq) + the lock
│   ├── outbox.py       send / emit / error: dict -> JSON -> frame -> socket
│   ├── broadcast.py    USER_LIST, GROUP_LIST, PRESENCE, HISTORY
│   └── handlers.py     protocol logic: what to do on receipt of each message type
├── web/
│   ├── index.html
│   ├── style.css
│   └── app.js
└── logs/               created on first run (server.log)
```

## 3. How to run

```powershell
cd E:\mochi-chaty2k\mochi-chaty2k\mochi-v2\backend
py main.py
```
Then open `http://localhost:5000` on this machine, or `http://<printed LAN IP>:5000` from the other hosts.

| Option | Meaning |
|---|---|
| `--port 6000` | listen on another port (no more editing code) |
| `--host 192.168.1.10` | listen on one interface only (default `0.0.0.0` = all) |
| `--log path\file.log` | log file location (default `mochi-v2/logs/server.log`) |
| `--no-log` | console only |
| `--verbose` | also print every SEND/RECV line on the console |

Benchmark (copy of the original tool, `mochi-v2/bench.py`), run from another host: `py bench.py <server-ip> 5000 5`

## 4. Backend: how the split was done

### 4.1 Principle

Each module answers one question, and the order the modules depend on each other follows the network layers, from the socket up to the protocol:

| Layer | Module | Question it answers |
|---|---|---|
| Settings | `config.py` | Which values (port, limits, paths)? |
| Instrumentation | `logger.py` | What happened, and when (to the ms)? |
| Framing | `ws_framing.py` | Where does one message end in the TCP byte stream? |
| HTTP | `http_static.py` | Which file did the browser ask for? |
| State | `state.py` | Who is online, in which groups, what was said? |
| Output | `outbox.py` | How does a dict become bytes on a socket? |
| Server events | `broadcast.py` | What does the server announce by itself? |
| Protocol | `handlers.py` | What do we do on receipt of each message? |
| Lifecycle | `connection.py` | What happens from accept() to close? |
| Entry | `main.py` | How does the server start? |

### 4.2 Dependency graph (arrow = "imports")

```
main ──▶ connection ──▶ handlers ──▶ broadcast ──▶ outbox ──▶ ws_framing ──▶ config
             │              │            │            │
             │              └──▶ state ◀─┴────────────┘
             ├──▶ http_static ──▶ config
             └──▶ logger ──▶ config
```
There are no cycles. This was deliberate: Python fails with "partially initialized module" errors when two modules import each other.

### 4.3 Where each original function went

| Original `server.py` | New home | Notes |
|---|---|---|
| `HOST, PORT, WEB, STATIC`, limits scattered in code | `config.py` | Limits are named constants now, e.g. `MAX_TEXT = 1000` |
| `log` | `logger.log` | Adds the date and milliseconds, and writes to a file too |
| `frame`, `read_exact`, `ws_read` | `ws_framing.frame / read_exact / read_frame` | Logic unchanged |
| WebSocket accept computation (inline) | `ws_framing.accept_key` | |
| `lock, clients, out_locks, groups, history, seq` | `state.py` | `seq` is now changed only through `state.next_seq()` |
| `find`, `members_of` | `state.py` | |
| `write`, `send`, `err`, `emit` | `outbox.py` (`err` → `error`) | `emit` now encodes once (§5) |
| `push_users`, `push_groups`, `send_history`, `presence` | `broadcast.py` | |
| `login`, `on_*`, `HANDLERS`, `handle` | `handlers.py` | Routing shared by CHAT and TYPING (§5) |
| `http`, `serve_static` | `http_static.respond / serve_static` | Request parsing moved into `read_request` |
| `serve_ws`, `handle_connection`, cleanup in `finally` | `connection.py` | Cleanup is its own function, `cleanup()` |
| `lan_ip`, `main` | `main.py` | Adds command-line options |

### 4.4 One chat message through the modules

```
browser frame ─▶ connection.serve_ws ─▶ ws_framing.read_frame (unmask)
                 logger.message("RECV")  ─▶ json.loads ─▶ handlers.handle (logged in? type known?)
                 ─▶ handlers.on_chat: validate ─▶ take state.lock ─▶ route() picks targets
                 ─▶ state.next_seq() ─▶ state.history.append ─▶ outbox.emit (encode + frame once)
                 ─▶ logger.message("SEND") per target ─▶ write() under each socket's own lock ─▶ TCP
```

## 5. Deliberate changes (everything else behaves exactly like the original)

Each change was made for a requirement in `project.md`. None of them changes the protocol.

| # | Change | Why (project.md reference) | Effect on the wire |
|---|---|---|---|
| 1 | `--host` / `--port` options | Working agreement 3: never hardcode addresses or ports | none |
| 2 | Every message logged with ms timestamps to `logs/server.log` | Working agreement 4; evidence for R6/R7 | none |
| 3 | `emit()` encodes JSON and builds the frame **once** per broadcast, not once per recipient | Efficiency: a USER_LIST with avatars was re-encoded for every client | identical bytes |
| 4 | Explicit Content-Type for .html/.css/.js | Python's `mimetypes` reads the Windows registry, which can map `.css` to `text/plain`; browsers then refuse the stylesheet | correct header |
| 5 | One `route()` function picks recipients for both CHAT and TYPING | One place to read and explain scope rules (removes duplicated code) | identical |
| 6 | Log lines for "websocket open" and group create/add | Easier to follow a demo from the log | none |

**Tradeoff of change 2:** writing a log line takes some processing time while the global lock is held, so it adds a little processing delay to every message (Lecture 02 nodal delay). If an R6 run must exclude this, start with `--no-log` and report both numbers. That comparison could itself serve as a "measurement vs performance" observation.

**Not changed, on purpose:** the global lock design, RAM-only history, no passwords, no idle-client timeout. These are the design tensions the report discusses, so v2 keeps them identical to the original. Their known costs are explained in the earlier code walkthrough: a slow client can stall broadcasts, and a pulled cable is detected late.

## 6. UI redesign

### 6.1 Goals
A neutral, professional chat client of the kind people already know (Slack, Teams, WhatsApp Web) that looks good on a projector in the live demo.

### 6.2 Decisions

| Decision | Why |
|---|---|
| Neutral greys, a single blue accent (`#2563eb`) | Calm and readable; the accent only marks "you", unread counts and actions |
| Automatic dark mode (`prefers-color-scheme`) | Follows the OS; all colours are CSS variables in `:root`, so the palette changes in one place |
| **System fonts only**, no Google Fonts | Lab or demo machines may have no internet; the old UI downloaded fonts from Google |
| Initials avatars on a colour derived from the username | Recognisable without photos; the same person always gets the same colour |
| Inline SVG icons (plus, edit, send, back, close) | Sharp at any size, no extra files or HTTP requests |
| Messages grouped by sender (name and avatar only on the first of a run) | Less clutter in busy lobby or group chats |
| Phone layout under 760 px: list *or* chat, with a back button | Clients on phones count as hosts too |
| Respects `prefers-reduced-motion` | Accessibility |

### 6.3 Removed vs added

| Removed (old Y2K look) | Added |
|---|---|
| Fake Windows title bars and the _ □ × buttons | Search box that filters chats by name |
| Emoji "faces" (`•ᴗ•`) as default avatars | Sign out button in the profile dialog |
| Pink/blue bubbles, pixel font, `.exe` dialog titles | Small avatars next to messages in lobby/group chats |
| Cute copy ("hop in ♡", "study besties") | Esc closes the open chat; empty-chat hint |

### 6.4 What stayed identical in `app.js`
Connection and auto-reconnect with back-off, LOGIN flow, PING every 4 s with the RTT shown under your name, per-chat message store and unread badges, HISTORY dedupe by `seq`, typing throttle (2.5 s) and expiry (4 s), notifications and sound, the 128×128 JPEG avatar crop. The protocol code paths were copied as they were; only the rendering functions (`avatar`, `meta`, `render`, `header`, `paint`) and the event wiring for the new layout changed.

The browser stores its data under a new key (`mochi2`), so v1 and v2 can be used side by side without mixing saved profiles.

## 7. Testing done

1. **Compile/syntax:** all Python modules compile (`py -m py_compile`), and `app.js` passes `node --check`.
2. **Protocol test, 32 checks, all passing.** A raw-socket test script exercised every message type and every error code, plus:
   - path-traversal requests (`/../backend/main.py`, `/%2e%2e/...`) → 404
   - correct Content-Types for the CSS and JS files
   - case-insensitive `USERNAME_TAKEN`
   - private messages echoed to the sender
   - HISTORY privacy: a new user sees the lobby, but not other users' private or group messages
   - group HISTORY on being added
   - `PRESENCE LEFT` on disconnect

   The two test failures seen along the way were both bugs in the test script itself, not the server.
3. **The original `bench.py`, unchanged, against the v2 server** (loopback, 5 runs). This only shows compatibility. **These numbers are not R6 results**, because 127.0.0.1 does not count as multi-host:

   | Measurement | Mean | Stdev |
   |---|---|---|
   | RTT PING→PONG | 0.28 ms | 0.02 |
   | New connection (TCP + upgrade + LOGIN_OK) | 1.61 ms | 0.21 |
   | Chat relay A→server→B, 100 B | 0.37 ms | 0.02 |
   | Throughput, 50 B messages | 178 KB/s | 20 |
   | Throughput, 1000 B messages | 2719 KB/s | 408 |

4. **Not tested:** the UI was not opened in a real browser during this work. Before the demo, check it on two machines: log in, use lobby/private/group chat, typing, profile photo, phone width, and dark mode.

## 8. Issue found in the original code (not fixed, as requested)

`server.py` and a new root-level `connection.py` were edited at 13:30–13:58 on 2026-10-31, before this work. `server.py` now does `from connection import handle_connection`, and `connection.py` does `from server import serve_ws, serve_static`. This is a **circular import**, and `py server.py` currently crashes on startup:

```
ImportError: cannot import name 'handle_connection' from 'connection'
```

It happens because `server.py` runs as `__main__`, so the import loads it a second time as a module named `server`. That second copy hits line 321 before `connection.py` has finished loading. `mochi-v2/backend` avoids this with the one-way dependency graph in §4.2. Options for the root copy: undo that edit, or use `mochi-v2` as the main version.

## 9. For the AI usage declaration (copy into `docs/ai-usage-log.md`)

| Date | Task | What the AI produced | What the humans must do / changed |
|---|---|---|---|
| 2026-10-31 | Modularise server, redesign UI | `mochi-v2/backend/*` (split of server.py + logging/CLI changes in §5), `mochi-v2/web/*` (new HTML/CSS, adapted app.js), this document, a protocol test script | Review and understand every module (viva!), test in a browser on the 3 hosts, decide whether v2 replaces v1 |

## 10. Suggested next steps

1. Open the v2 UI on 2–3 machines and run the checklist in §7.4.
2. Decide which version is the official submission, and update `project.md` and `protocol_spec.md` paths.
3. Use `logs/server.log` for R7 evidence (e.g. a client closing mid-chat → `LEFT` + `PRESENCE LEFT` lines).
4. Run `bench.py` across real hosts for R6, with and without `--no-log`.
